"""商户异步通知（Webhook：POST JSON + 平台私钥签名）+ 阶梯重试 + notify_logs 全量留痕。

投递出口只有 notify_merchant_once：
- 成功（HTTP 2xx）→ notify_status=0
- 失败 → notify_count+1，按阶梯设 next_notify_at；超上限置 -1
调度见 app/worker/jobs/__init__.py（notify_merchant 任务 + scan_pending_notify cron）。
"""
import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.cache import CacheClient
from app.lib.url_guard import UnsafeUrlError, assert_public_http_url
from app.models import Merchant, NotifyLog, Order
from app.services.http_client import get_notify_client
from app.services.balance import dmoney
from app.services.order import (
    get_order_by_trade_no,
    ORDER_PAID,
    build_webhook_payload,
    fmt_money,
)
from app.services.settings import get_all_settings, get_notify_ladder

_log = logging.getLogger(__name__)


async def notify_merchant_once(
    session: AsyncSession,
    cache: CacheClient | None = None,
    trade_no: str = "",
) -> bool:
    """投递一次商户通知。返回是否成功。"""
    order = await get_order_by_trade_no(session, trade_no)
    if not order:
        return True  # 订单不存在，对调度器表现为已完成
    if order.status != ORDER_PAID or not order.notify_url:
        # 非已支付（待支付/已退款/已过期）或无回调地址（收款页/充值单等）：
        # 标记完成，否则 notify_status 恒为 1，scan_pending_notify 每分钟重复投递空任务
        order.notify_status = 0
        order.next_notify_at = None
        session.add(order)
        await session.commit()
        return True
    if order.notify_status == 0:
        return True  # 已成功过，跳过

    merchant = await session.get(Merchant, order.merchant_id)
    if not merchant:
        return True

    settings = await get_all_settings(session, cache)
    ladder = get_notify_ladder(settings)
    try:
        max_attempts = int(settings.get("notify_max_attempts", "6"))
    except ValueError:
        max_attempts = 6
    attempt = order.notify_count + 1

    payload = build_webhook_payload(order, merchant, platform_rsa_key=settings.get("platform_rsa_private_key") or "")
    url = order.notify_url
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request_line = f"POST {url}\n{body.decode('utf-8')}"

    success = False
    http_status = None
    response_text = ""
    # SSRF 防护（P1-12）：只投递公网 http(s) 地址，且禁止跟随 3xx 跳转
    # （否则「公网域名 302 到 127.0.0.1 / 169.254.169.254」就绕过了地址校验）。
    # 不安全地址按「投递失败」处理 → 走正常阶梯重试直至耗尽，notify_logs 里可见原因。
    try:
        await assert_public_http_url(url)
    except UnsafeUrlError as e:
        response_text = f"UNSAFE_URL: {e}"
        _log.warning("通知地址不安全，已拦截 trade_no=%s url=%s: %s", trade_no, url, e)
    else:
        try:
            # P3-30：共享连接池（同一商户域名只握手一次），不跟随 3xx
            resp = await get_notify_client().post(
                url, content=body,
                headers={"Content-Type": "application/json; charset=utf-8"},
            )
            http_status = resp.status_code
            response_text = (resp.text or "")[:2000]
            success = 200 <= resp.status_code < 300
        except Exception as e:
            response_text = f"EXCEPTION: {e}"
            _log.warning("通知商户失败 trade_no=%s attempt=%s: %s", trade_no, attempt, e)

    session.add(NotifyLog(
        kind="merchant",
        trade_no=trade_no,
        url=url[:500],
        request=request_line[:2000],
        response=response_text[:2000],
        http_status=http_status,
        attempt=attempt,
        success=success,
    ))

    if success:
        order.notify_status = 0
        order.next_notify_at = None
    else:
        order.notify_count = attempt
        if attempt >= max_attempts or attempt > len(ladder):
            order.notify_status = -1  # 重试耗尽
            order.next_notify_at = None
            # 风控联动：24h 内通知失败订单数达阈值 → 自动关闭支付权限 + 站内消息
            try:
                await _maybe_block_for_notify_failures(session, merchant, settings)
            except Exception:
                _log.warning("通知失败联动异常 merchant_id=%s", merchant.id, exc_info=True)
        else:
            order.notify_status = 1
            order.next_notify_at = datetime.now() + timedelta(minutes=ladder[min(attempt - 1, len(ladder) - 1)])
    session.add(order)
    await session.commit()
    return success


async def _maybe_block_for_notify_failures(
    session: AsyncSession, merchant: Merchant, settings: dict,
) -> None:
    """通知失败联动：开关开启且 24h 内耗尽订单达阈值时关闭商户支付权限。

    只拦截 pay_status=1 的商户（已被管理员关闭/待审核的不动）。
    """
    from sqlmodel import col, func

    from app.services.settings import setting_enabled

    if not setting_enabled(settings.get("notify_fail_block_enable", False)):
        return
    try:
        threshold = int(settings.get("notify_fail_block_threshold", 10) or 10)
    except (TypeError, ValueError):
        threshold = 10
    if threshold <= 0:
        return

    since = datetime.now() - timedelta(hours=24)
    failed = (await session.exec(
        select(func.count()).select_from(Order).where(
            col(Order.merchant_id) == merchant.id,
            col(Order.notify_status) == -1,  # noqa: E712
            col(Order.paid_at).isnot(None),
            col(Order.paid_at) >= since,
        )
    )).one()
    if int(failed) < threshold:
        return

    locked = (await session.exec(
        select(Merchant).where(Merchant.id == merchant.id).with_for_update()
    )).first()
    if not locked or locked.pay_status != 1:
        return
    locked.pay_status = 0
    session.add(locked)

    from app.services.message import send_merchant_message
    await send_merchant_message(
        session, locked,
        title="支付权限已被关闭",
        content=f"近 24 小时内已有 {int(failed)} 笔订单回调通知失败（重试耗尽），"
                f"系统已自动关闭你的支付权限，请检查回调地址后联系管理员恢复。",
        biz_type="system", subscribe_flag="notify_login",
    )
    _log.warning("通知失败联动：关闭商户支付权限 merchant_id=%s failed=%s", merchant.id, failed)


async def requeue_notify(session: AsyncSession, trade_no: str) -> bool:
    """手动重发通知（admin/merchant 按钮）：重置为待重试并立即投递。"""
    order = await get_order_by_trade_no(session, trade_no)
    if not order or order.status != ORDER_PAID:
        return False
    order.notify_status = 1
    order.notify_count = 0
    order.next_notify_at = datetime.now()
    session.add(order)
    await session.commit()
    return True
