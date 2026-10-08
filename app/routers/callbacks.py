"""上游回调网关（/callbacks）— 上游支付插件异步通知与同步回跳的统一入口。

- ANY /callbacks/{plugin}/{trade_no}           上游异步通知：URL 中的插件验签 → 幂等记账 → 投递商户通知
- GET  /callbacks/{plugin}/{trade_no}/return   上游同步回跳：尝试记账 → 302 商户 return_url

应答给上游的是插件声明/约定的字面量（如 success/fail）。
URL 携带插件名作为第一路由依据，并与订单通道插件名做一致性校验（防跨插件伪造探测）。
"""
import logging
import traceback

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, Response

from app.core.deps import CacheDep, SessionDep
from app.services.channel import get_channel_with_config
from app.services.notify import requeue_notify
from app.services.order import (
    ORDER_PAID,
    ORDER_PENDING,
    get_order_by_trade_no,
    process_paid_order,
)
from app.services.pay_debug import write_pay_debug
from app.services.plugin import PluginRegistry, maybe_await
from app.worker import enqueue

_log = logging.getLogger(__name__)

router = APIRouter()

RESP_OK = "success"
RESP_FAIL = "fail"


async def _load_plugin(plugin_name: str, trade_no: str, session: SessionDep):
    """按 URL 插件名加载插件实例，并校验与订单通道一致。

    返回 (order, channel, plugin, err)：err 非 None 时为应答 Response。
    channel 一并带出（回调调试日志开关在通道上）。
    """
    order = await get_order_by_trade_no(session, trade_no)
    if not order:
        return None, None, None, Response(content="order not found", status_code=404)

    channel, config = await get_channel_with_config(session, order.channel_id)
    if not channel:
        return order, None, None, Response(content="channel not found", status_code=404)
    if channel.plugin_name != plugin_name:
        # URL 插件与订单通道不符：拒绝处理（防伪造探测）
        return order, None, None, Response(content="plugin mismatch", status_code=400)
    plugin = PluginRegistry.get_payment(plugin_name, config)
    if not plugin:
        return order, None, None, Response(content="plugin not found", status_code=404)
    return order, channel, plugin, None


async def _parse_params(request: Request) -> dict:
    params = dict(request.query_params)
    if request.method == "POST":
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                body_json = await request.json()
                if isinstance(body_json, dict):
                    params.update(body_json)
            except Exception:
                pass
        else:
            try:
                form = await request.form()
                for k, v in form.items():
                    params[k] = v
            except Exception:
                pass
    return params


async def _notify_merchant(trade_no: str, session: SessionDep) -> None:
    """投递商户通知任务；队列不可用兜底为重置重试队列标记。"""
    try:
        await enqueue("notify_merchant_job", trade_no)
    except Exception:
        try:
            await requeue_notify(session, trade_no)
        except Exception:
            _log.exception("投递商户通知任务失败 trade_no=%s", trade_no)


@router.api_route("/{plugin_name}/{trade_no}", methods=["GET", "POST"], include_in_schema=False)
async def upstream_notify(plugin_name: str, trade_no: str, request: Request,
                          session: SessionDep, cache: CacheDep):
    """上游异步通知。"""
    order, channel, plugin, err_resp = await _load_plugin(plugin_name, trade_no, session)
    if err_resp is not None:
        return err_resp
    assert order is not None and plugin is not None

    dbg = bool(getattr(channel, "debug_log", 0))

    # 已支付订单直接回 success（上游停止重发）
    if order.status == ORDER_PAID:
        if dbg:
            write_pay_debug(plugin_name, "NOTIFY", {
                "trade_no": trade_no, "stage": "already_paid",
                "params": dict(request.query_params),
            })
        return Response(content=RESP_OK, media_type="text/plain")

    params = await _parse_params(request)
    body = await request.body()

    try:
        verdict = await maybe_await(plugin.notify_handler(params, body))
    except Exception:
        _log.exception("插件回调验签异常 trade_no=%s plugin=%s", trade_no, plugin_name)
        if dbg:
            write_pay_debug(plugin_name, "NOTIFY", {
                "trade_no": trade_no, "stage": "exception",
                "params": params, "body": body,
                "error": traceback.format_exc(),
            })
        return Response(content=RESP_FAIL, media_type="text/plain")

    if not verdict.ok:
        if dbg:
            write_pay_debug(plugin_name, "NOTIFY", {
                "trade_no": trade_no, "stage": "verdict_reject",
                "params": params, "body": body,
                "verdict": {"ok": verdict.ok, "ack": verdict.ack},
                "upstream": getattr(plugin, "last_debug", None),
            })
        return Response(content=verdict.ack, media_type="text/plain")

    result = await process_paid_order(
        trade_no, verdict.api_trade_no, verdict.money, verdict.buyer_id,
        session, cache,
    )
    if dbg:
        write_pay_debug(plugin_name, "NOTIFY", {
            "trade_no": trade_no, "stage": "done",
            "params": params, "body": body,
            "verdict": {
                "ok": verdict.ok, "ack": verdict.ack,
                "api_trade_no": verdict.api_trade_no,
                "money": str(verdict.money) if verdict.money is not None else None,
                "buyer_id": verdict.buyer_id,
            },
            "paid_status": result.status,
            "should_notify": result.should_notify,
            "upstream": getattr(plugin, "last_debug", None),
        })
    if result.status in ("paid", "duplicate"):
        # 充值单（order_type=1）等无商户回调场景 should_notify=False；
        # duplicate（重复回调已支付单）保持重发通知行为
        if result.status == "duplicate" or result.should_notify:
            await _notify_merchant(trade_no, session)
        return Response(content=RESP_OK, media_type="text/plain")
    if result.status == "lock_failed":
        # 并发处理中，对上游回 success
        return Response(content=RESP_OK, media_type="text/plain")
    if result.status == "not_found":
        return Response(content=RESP_FAIL, media_type="text/plain")
    if result.status == "mismatch":
        _log.error("回调金额不符 trade_no=%s claimed=%s", trade_no, verdict.money)
        return Response(content=RESP_FAIL, media_type="text/plain")
    # invalid（已过期）：记录后回 fail，交人工处理
    _log.warning("回调订单状态不允许记账 trade_no=%s status=%s", trade_no, order.status)
    return Response(content=RESP_FAIL, media_type="text/plain")


@router.get("/{plugin_name}/{trade_no}/return", include_in_schema=False)
async def upstream_return(plugin_name: str, trade_no: str, request: Request,
                          session: SessionDep, cache: CacheDep):
    """上游同步回跳：验签并尝试记账（部分上游只在 return 带成功态），再 302 到商户。"""
    order, channel, plugin, err_resp = await _load_plugin(plugin_name, trade_no, session)
    if err_resp is not None:
        return err_resp
    assert order is not None and plugin is not None

    params = dict(request.query_params)
    try:
        result = await maybe_await(plugin.return_handler(params))
    except Exception:
        result = None

    if result and result.get("status") == "paid" and order.status == ORDER_PENDING:
        await process_paid_order(
            trade_no, result.get("api_trade_no", ""), None, "",
            session, cache,
        )
        await _notify_merchant(trade_no, session)

    await session.refresh(order)
    if order.return_url:
        sep = "&" if "?" in order.return_url else "?"
        return RedirectResponse(f"{order.return_url}{sep}trade_no={order.trade_no}", status_code=302)
    return Response(content=RESP_OK, media_type="text/plain")
