"""商户退款（开放 API 同步退款模型）。

规则：
- 订单须已支付（status=1）且非扣量单（扣量单对外"未支付"口径不变 → 拒退）
- 可退上限 = merchant_income（NULL 兜底 money）；已退 = SUM(refunds.amount WHERE status=1)
- 支持多次部分退款；累计退满自动置 order.status=2
- out_refund_no 同商户幂等：已有退款单重复提交返回原单
- 余额 < 退款额拒
- 上游失败 → 报错不落库零副作用；上游成功记账失败 → rollback + critical
- 记账 change_merchant_balance(-amount, biz_type="refund")
"""
import json
import logging
import random
from datetime import datetime
from decimal import Decimal

from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Merchant, Order, Refund
from app.services.balance import change_merchant_balance, dmoney
from app.services.order import CENT, ORDER_PAID, ORDER_REFUNDED, refundable_base

_log = logging.getLogger(__name__)


def generate_refund_no() -> str:
    """退款单号：R + yymmddHHMMSSffffff(18) + 3 位随机 = 22 位。"""
    return "R" + datetime.now().strftime("%y%m%d%H%M%S%f") + str(random.randint(100, 999))


async def refunded_amount(session: AsyncSession, trade_no: str) -> Decimal:
    """订单累计已退金额（成功退款单）。"""
    total = (await session.exec(
        select(func.sum(Refund.amount)).where(
            col(Refund.trade_no) == trade_no,
            col(Refund.status) == 1,  # noqa: E712
        )
    )).one()
    return dmoney(total or 0)


def _refund_ok(result: dict) -> bool:
    """归一化插件退款成功判定（各插件返回形态不一致）。

    epay：status==1；alipay/wxpay 系：code==0；douyinpay：status=="success"。
    """
    if not isinstance(result, dict):
        return False
    status = result.get("status")
    if status == 1 or status == "success":
        return True
    return result.get("code") == 0


async def create_refund(
    session: AsyncSession,
    merchant: Merchant,
    trade_no: str,
    refund_amount: Decimal | str | None = None,
    out_refund_no: str = "",
    reason: str = "",
) -> Refund:
    """发起退款（同步模型：插件返回即终态）。成功返回 Refund，失败抛 ValueError。

    余额预检 → 插件退款（事务外 IO）→ Refund 落库 + 扣余额 + 退满置 status=2 同一事务。
    """
    out_refund_no = (out_refund_no or "").strip()[:64] or None
    reason = (reason or "").strip()[:255] or None

    # 幂等：同商户同 out_refund_no 重复提交返回原单
    if out_refund_no:
        existing = (await session.exec(
            select(Refund).where(
                col(Refund.merchant_id) == merchant.id,
                col(Refund.out_refund_no) == out_refund_no,
            )
        )).first()
        if existing:
            return existing

    order = (await session.exec(
        select(Order).where(Order.trade_no == trade_no)
    )).first()
    if not order or order.merchant_id != merchant.id:
        raise ValueError("订单不存在")
    if order.is_deducted or order.status != ORDER_PAID:
        raise ValueError("订单未支付，无法退款")

    base = refundable_base(order)
    refunded = await refunded_amount(session, trade_no)
    refundable = (base - refunded).quantize(CENT)
    if refundable <= 0:
        raise ValueError("该订单已全额退款")

    if refund_amount is not None and str(refund_amount).strip() != "":
        try:
            amount = dmoney(refund_amount)
        except Exception:
            raise ValueError("退款金额格式错误")
    else:
        amount = refundable  # 缺省 = 剩余可退全额
    if amount <= 0:
        raise ValueError("退款金额必须大于0")
    if amount > refundable:
        raise ValueError(f"超出可退金额，最多可退 {refundable:f}")

    # 余额预检（正式扣减在插件成功后的事务内，行锁下再校验一次）
    if dmoney(merchant.balance) < amount:
        raise ValueError("商户余额不足，无法退款")

    # 解析通道与插件（插件调用是外部 IO，保持在事务外）
    from app.services.channel import get_channel_with_config
    from app.services.plugin import PluginRegistry, maybe_await

    channel, channel_config = await get_channel_with_config(session, order.channel_id)
    plugin_name = order.plugin_name or (channel.plugin_name if channel else "")
    plugin = PluginRegistry.get_payment(plugin_name, channel_config) if plugin_name else None
    if plugin is None or not plugin.is_refund_supported():
        raise ValueError("该订单支付通道不支持退款")

    refund_no = generate_refund_no()
    order_view = {
        "trade_no": order.trade_no,
        "out_trade_no": order.out_trade_no or "",
        "api_trade_no": order.api_trade_no or "",
        "money": str(order.money),
        "realmoney": str(order.realmoney) if order.realmoney is not None else str(order.money),
        "payment_type_code": order.payment_type_code or "",
    }
    try:
        result = await maybe_await(plugin.refund(order_view, refund_no, amount))
    except NotImplementedError:
        raise ValueError("该订单支付通道不支持退款")
    except Exception as e:
        _log.warning("退款上游请求异常 trade_no=%s: %s", trade_no, e)
        raise ValueError("上游通道退款请求失败，请稍后重试")

    result = result if isinstance(result, dict) else {}
    if not _refund_ok(result):
        # 上游失败：报错不落库，零副作用
        raise ValueError(str(result.get("msg") or "退款失败"))

    # 上游成功：Refund 落库 + 扣余额 + 退满置 status=2（同一事务）
    try:
        locked = (await session.exec(
            select(Order).where(Order.trade_no == trade_no).with_for_update()
        )).first()
        refunded_now = await refunded_amount(session, trade_no)
        cap = refundable_base(locked or order)
        if locked is None or locked.status != ORDER_PAID or locked.is_deducted \
                or (cap - refunded_now).quantize(CENT) < amount:
            raise RuntimeError(f"退款落库前状态校验失败 trade_no={trade_no}")

        refund = Refund(
            refund_no=refund_no,
            out_refund_no=out_refund_no,
            trade_no=trade_no,
            merchant_id=merchant.id,
            amount=amount,
            status=1,
            reason=reason,
            result=json.dumps(result, ensure_ascii=False)[:5000],
            finished_at=datetime.now(),
        )
        session.add(refund)
        await change_merchant_balance(
            session, merchant.id, -amount,
            biz_type="refund", trade_no=trade_no,
            remark=f"订单退款 {refund_no}",
        )
        if (refunded_now + amount).quantize(CENT) >= cap:
            locked.status = ORDER_REFUNDED
        session.add(locked)
        await session.commit()
        await session.refresh(refund)
        return refund
    except Exception:
        await session.rollback()
        _log.critical("退款上游已成功但记账失败（需人工核对）trade_no=%s refund_no=%s amount=%s",
                      trade_no, refund_no, amount, exc_info=True)
        raise ValueError("退款记账失败，请联系平台核对")


async def get_refund(
    session: AsyncSession,
    merchant: Merchant,
    refund_no: str = "",
    out_refund_no: str = "",
) -> Refund | None:
    """查询退款单：refund_no 精确查，未命中时 out_refund_no 兜底（限本商户）。"""
    refund_no = (refund_no or "").strip()
    out_refund_no = (out_refund_no or "").strip()
    if refund_no:
        row = (await session.exec(
            select(Refund).where(
                col(Refund.refund_no) == refund_no,
                col(Refund.merchant_id) == merchant.id,
            )
        )).first()
        if row:
            return row
    if out_refund_no:
        return (await session.exec(
            select(Refund).where(
                col(Refund.merchant_id) == merchant.id,
                col(Refund.out_refund_no) == out_refund_no,
            )
        )).first()
    return None


def refund_query_payload(refund: Refund) -> dict:
    """开放 API 退款查询响应 data。"""
    return {
        "refund_no": refund.refund_no,
        "out_refund_no": refund.out_refund_no or "",
        "trade_no": refund.trade_no,
        "amount": f"{dmoney(refund.amount):f}",
        "status": refund.status,
        "reason": refund.reason or "",
        "created_at": refund.created_at.strftime("%Y-%m-%d %H:%M:%S") if refund.created_at else "",
        "finished_at": refund.finished_at.strftime("%Y-%m-%d %H:%M:%S") if refund.finished_at else "",
    }
