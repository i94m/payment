"""商户退款（开放 API 同步退款模型）。

规则：
- 订单须已支付（status=1）且非扣量单（扣量单对外"未支付"口径不变 → 拒退）
- 可退上限 = merchant_income（NULL 兜底 money）
- 支持多次部分退款；累计退满自动置 order.status=2
- out_refund_no 同商户幂等：已有退款单重复提交返回原单
- 余额 < 退款额拒
- 上游失败 → 报错、撤销预占（不落退款单、不扣余额）
- 上游成功记账失败 → rollback + critical
- 记账 change_merchant_balance(-amount, biz_type="refund")
- 邀请返现按退款额等比例回冲（clawback_invite_rebate）

并发口径（P0-3，配额占位 + 两阶段）：
1) **占位段**：可退上限不再靠「锁订单行再求和」（SQLite 上 FOR UPDATE 是空操作，
   门禁证明不了），而是订单行上的条件 UPDATE：
   `UPDATE orders SET refunded_amount = refunded_amount + :amt
     WHERE trade_no=? AND status=1 AND refunded_amount + :amt <= 可退上限`
   影响行数=1 才算占位成功 —— 原子、跨进程、MySQL/SQLite 同语义。
   `orders.refunded_amount` 是「已退 + 在途预占」配额，失败/孤儿占位会等额回退；
   对外报表仍按成功退款单求和（refunded_amount()）。
2) **幂等段**：out_refund_no 的占位单在上游 IO **之前**落库并提交，
   (merchant_id, out_refund_no) 唯一键兜住同键并发，同键上游只退一次。
3) **上游段**（事务外）：调用插件退款。
4) **记账段**（事务内）：锁退款单 + 订单行，置 status=1、扣余额、退满置单。
   上游失败时删除占位单并回退配额 → 对外行为与「零副作用」一致。

占位单若因进程崩溃残留（status=0 超过 REFUND_RESERVE_TTL），下次同键/同单退款
会先清理它并回退配额，不会永久锁死可退金额。
"""
import json
import logging
import random
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import case as sa_case
from sqlalchemy import update as sa_update
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Merchant, Order, Refund
from app.services.balance import EPS, change_merchant_balance, dmoney, lock_merchant
from app.services.order import (
    CENT,
    ORDER_PAID,
    ORDER_REFUNDED,
    clawback_invite_rebate,
    refundable_base,
)

_log = logging.getLogger(__name__)

# 处理中的退款预占最长存活时间（秒）：超过视为进程中断留下的孤儿预占
REFUND_RESERVE_TTL = 120


def generate_refund_no() -> str:
    """退款单号：R + yymmddHHMMSSffffff(18) + 3 位随机 = 22 位。"""
    return "R" + datetime.now().strftime("%y%m%d%H%M%S%f") + str(random.randint(100, 999))


async def refunded_amount(session: AsyncSession, trade_no: str) -> Decimal:
    """订单累计已退金额（**成功**退款单，对外报表口径）。"""
    total = (await session.exec(
        select(func.sum(Refund.amount)).where(
            col(Refund.trade_no) == trade_no,
            col(Refund.status) == 1,  # noqa: E712
        )
    )).one()
    return dmoney(total or 0)


async def _claim_refund_quota(session: AsyncSession, order: Order, amount: Decimal) -> bool:
    """退款配额占位：订单行上的条件 UPDATE + 影响行数（可移植的可退上限守卫）。

    `refunded_amount + amount <= 可退上限` 写在 WHERE 里，rowcount=1 才表示这次
    占住了额度：并发部分退款在同一行上串行化，后到者的 WHERE 看到前者已提交的
    配额，因此不可能超退。不依赖 `SELECT ... FOR UPDATE`（SQLite 空操作）。
    """
    cap = refundable_base(order)
    stmt = (
        sa_update(Order)
        .where(
            col(Order.trade_no) == order.trade_no,
            col(Order.status) == ORDER_PAID,
            col(Order.is_deducted) == False,  # noqa: E712
            col(Order.refunded_amount) + amount <= cap + EPS,
        )
        .values(refunded_amount=col(Order.refunded_amount) + amount)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    if result.rowcount != 1:
        return False
    # 精确复核（消掉 SQLite 浮点噪声带来的 EPS 放行）：失败由调用方回滚整笔
    fresh = await _fresh_order(session, order.trade_no)
    return fresh is not None and dmoney(fresh.refunded_amount) <= cap


async def _release_refund_quota(session: AsyncSession, trade_no: str, amount: Decimal) -> None:
    """回退配额（上游失败/孤儿占位）：只减不增，最低夹到 0。"""
    stmt = (
        sa_update(Order)
        .where(
            col(Order.trade_no) == trade_no,
            col(Order.refunded_amount) > 0,
        )
        .values(refunded_amount=sa_case(
            (col(Order.refunded_amount) >= amount, col(Order.refunded_amount) - amount),
            else_=Decimal("0.00"),
        ))
        .execution_options(synchronize_session=False)
    )
    await session.execute(stmt)


async def _fresh_order(session: AsyncSession, trade_no: str) -> Order | None:
    """当前读订单（populate_existing：不用会话里的旧快照）。"""
    return (await session.exec(
        select(Order).where(Order.trade_no == trade_no)
        .execution_options(populate_existing=True)
    )).first()


def _refund_ok(result: dict) -> bool:
    """归一化插件退款成功判定（epay 返回 status==1；code==0 形态兼容保留）。"""
    if not isinstance(result, dict):
        return False
    status = result.get("status")
    if status == 1 or status == "success":
        return True
    return result.get("code") == 0


async def _find_by_out_refund_no(
    session: AsyncSession, merchant_id: int, out_refund_no: str, *, lock: bool = False,
) -> Refund | None:
    stmt = select(Refund).where(
        col(Refund.merchant_id) == merchant_id,
        col(Refund.out_refund_no) == out_refund_no,
    )
    if lock:
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.exec(stmt)).first()


def _reserve_stale(refund: Refund) -> bool:
    """预占单是否已超时（孤儿预占）。"""
    if refund.status != 0:
        return False
    created = refund.created_at or datetime.now()
    return datetime.now() - created > timedelta(seconds=REFUND_RESERVE_TTL)


async def _discard_reservation(session: AsyncSession, refund_id: int) -> None:
    """删除占位单并回退配额（上游失败/孤儿占位）：只删 status=0 的行，绝不碰已成功单。"""
    row = (await session.exec(
        select(Refund).where(Refund.id == refund_id)
        .with_for_update().execution_options(populate_existing=True)
    )).first()
    if row is None or row.status != 0:
        return
    trade_no, amount = row.trade_no, dmoney(row.amount)
    await session.delete(row)
    await _release_refund_quota(session, trade_no, amount)
    await session.commit()


async def _rollback_and_resume(session: AsyncSession, refund: Refund) -> Refund:
    """回滚当前事务（释放订单行锁）后刷新退款单实例再返回。

    回滚会让会话内所有实例过期，直接返回会在路由序列化时触发同步惰性加载
    （async 下 MissingGreenlet），因此必须显式 refresh。
    """
    await session.rollback()
    await session.refresh(refund)
    return refund


async def create_refund(
    session: AsyncSession,
    merchant: Merchant,
    trade_no: str,
    refund_amount: Decimal | str | None = None,
    out_refund_no: str = "",
    reason: str = "",
) -> Refund:
    """发起退款（同步模型：插件返回即终态）。成功返回 Refund，失败抛 ValueError。

    可退配额原子占位 → 占位单落库（status=0，幂等键在上游 IO 之前提交）→ 插件退款
    （事务外）→ 记账（status=1 + 扣余额 + 退满置 status=2）同一事务。

    串行化由订单行上的条件 UPDATE（refunded_amount 配额）保证：跨进程/跨实例、
    MySQL 与 SQLite 同语义，不依赖 FOR UPDATE 或进程内锁。
    """
    trade_no = (trade_no or "").strip()[:32]
    return await _create_refund_impl(
        session, merchant, trade_no, refund_amount, out_refund_no, reason,
    )


async def _create_refund_impl(
    session: AsyncSession,
    merchant: Merchant,
    trade_no: str,
    refund_amount: Decimal | str | None = None,
    out_refund_no: str = "",
    reason: str = "",
) -> Refund:
    out_refund_no = (out_refund_no or "").strip()[:64] or None
    reason = (reason or "").strip()[:255] or None
    merchant_id = merchant.id

    # ---- 幂等快查（锁外）：已成功/在途的同键请求直接返回原单 ----
    if out_refund_no:
        existing = await _find_by_out_refund_no(session, merchant_id, out_refund_no)
        if existing:
            if _reserve_stale(existing):
                await _discard_reservation(session, existing.id)
            else:
                return existing

    # ---- 占位段：校验 → 条件 UPDATE 占配额 → 落 status=0 退款单 → 提交 ----
    order = await _fresh_order(session, trade_no)
    if not order or order.merchant_id != merchant_id:
        await session.rollback()
        raise ValueError("订单不存在")

    # 二次幂等：并发同 out_refund_no 请求已有一方落库
    if out_refund_no:
        existing = await _find_by_out_refund_no(session, merchant_id, out_refund_no, lock=True)
        if existing:
            return await _rollback_and_resume(session, existing)

    # 清理本单孤儿占位（进程中断残留的 status=0，超过 TTL 才清理）并回退其配额
    stale = (await session.exec(
        select(Refund).where(
            col(Refund.trade_no) == trade_no,
            col(Refund.status) == 0,  # noqa: E712
        ).with_for_update().execution_options(populate_existing=True)
    )).all()
    for row in stale:
        if _reserve_stale(row):
            await session.delete(row)
            await _release_refund_quota(session, trade_no, dmoney(row.amount))

    if order.is_deducted or order.status != ORDER_PAID:
        await session.rollback()
        raise ValueError("订单未支付，无法退款")

    base = refundable_base(order)
    refundable = (base - dmoney(order.refunded_amount)).quantize(CENT)
    if refundable <= 0:
        await session.rollback()
        raise ValueError("该订单已全额退款")

    if refund_amount is not None and str(refund_amount).strip() != "":
        try:
            amount = dmoney(refund_amount)
        except Exception:
            await session.rollback()
            raise ValueError("退款金额格式错误")
    else:
        amount = refundable  # 缺省 = 剩余可退全额
    if amount <= 0:
        await session.rollback()
        raise ValueError("退款金额必须大于0")
    if amount > refundable:
        await session.rollback()
        raise ValueError(f"超出可退金额，最多可退 {refundable:f}")

    # 通道与插件（插件调用是外部 IO，保持在事务外；这里提前解析做快速失败）
    from app.services.channel import get_channel_with_config
    from app.services.plugin import PluginRegistry, maybe_await

    channel, channel_config = await get_channel_with_config(session, order.channel_id)
    plugin_name = order.plugin_name or (channel.plugin_name if channel else "")
    plugin = PluginRegistry.get_payment(plugin_name, channel_config) if plugin_name else None
    if plugin is None or not plugin.is_refund_supported():
        await session.rollback()
        raise ValueError("该订单支付通道不支持退款")

    # 余额预检（行锁当前读；正式扣减在记账段再校验一次）
    locked_merchant = await lock_merchant(session, merchant_id)
    if dmoney(locked_merchant.balance) < amount:
        await session.rollback()
        raise ValueError("商户余额不足，无法退款")

    # 配额占位（原子；锁外预检只用于拼人话，真正的上限判定在 WHERE 里）
    if not await _claim_refund_quota(session, order, amount):
        fresh = await _fresh_order(session, trade_no)
        state_error = ""
        available = Decimal("0.00")
        if fresh is not None:
            available = (refundable_base(fresh) - dmoney(fresh.refunded_amount)).quantize(CENT)
            if fresh.status != ORDER_PAID or fresh.is_deducted:
                state_error = "订单未支付，无法退款"
        await session.rollback()
        if state_error:
            raise ValueError(state_error)
        if available <= 0:
            raise ValueError("该订单已全额退款")
        raise ValueError(f"超出可退金额，最多可退 {available:f}")

    refund_no = generate_refund_no()
    refund = Refund(
        refund_no=refund_no,
        out_refund_no=out_refund_no,
        trade_no=trade_no,
        merchant_id=merchant_id,
        amount=amount,
        status=0,  # 预占：可退额度已被占用，上游结果落定后置 1 / 删除
        reason=reason,
    )
    session.add(refund)
    order_view = {
        "trade_no": order.trade_no,
        "out_trade_no": order.out_trade_no or "",
        "api_trade_no": order.api_trade_no or "",
        "money": str(order.money),
        "realmoney": str(order.realmoney) if order.realmoney is not None else str(order.money),
        "payment_type_code": order.payment_type_code or "",
    }
    try:
        await session.commit()
    except Exception as e:
        await session.rollback()
        # 唯一键兜底：同键并发（订单行锁未覆盖到的极端情况）
        if out_refund_no:
            existing = await _find_by_out_refund_no(session, merchant_id, out_refund_no)
            if existing:
                return existing
        _log.warning("退款预占落库失败 trade_no=%s: %s", trade_no, type(e).__name__)
        raise ValueError("退款提交冲突，请稍后查询退款结果")
    refund_id = refund.id

    # ---- 上游段：外部 IO（事务外）----
    try:
        result = await maybe_await(plugin.refund(order_view, refund_no, amount))
    except NotImplementedError:
        await _discard_reservation(session, refund_id)
        raise ValueError("该订单支付通道不支持退款")
    except Exception as e:
        _log.warning("退款上游请求异常 trade_no=%s: %s", trade_no, e)
        await _discard_reservation(session, refund_id)
        raise ValueError("上游通道退款请求失败，请稍后重试")

    result = result if isinstance(result, dict) else {}
    if not _refund_ok(result):
        # 上游失败：撤销预占 → 不落退款单、不扣余额（零资金副作用）
        await _discard_reservation(session, refund_id)
        raise ValueError(str(result.get("msg") or "退款失败"))

    # ---- 记账段：Refund 置成功 + 扣余额 + 退满置 status=2（同一事务）----
    try:
        locked = (await session.exec(
            select(Order).where(Order.trade_no == trade_no)
            .with_for_update().execution_options(populate_existing=True)
        )).first()
        row = (await session.exec(
            select(Refund).where(Refund.id == refund_id)
            .with_for_update().execution_options(populate_existing=True)
        )).first()
        if row is None:
            raise RuntimeError(f"退款预占单已丢失 trade_no={trade_no} refund_no={refund_no}")
        cap = refundable_base(locked or order)
        refunded_now = await refunded_amount(session, trade_no)
        if locked is None or locked.status != ORDER_PAID or locked.is_deducted \
                or (cap - refunded_now).quantize(CENT) < amount:
            raise RuntimeError(f"退款落库前状态校验失败 trade_no={trade_no}")

        row.status = 1
        row.result = json.dumps(result, ensure_ascii=False)[:5000]
        row.finished_at = datetime.now()
        session.add(row)
        await change_merchant_balance(
            session, merchant_id, -amount,
            biz_type="refund", trade_no=trade_no,
            remark=f"订单退款 {refund_no}",
        )
        # 邀请返现按退款额等比例回冲（失败不阻断退款主流程）
        await clawback_invite_rebate(session, trade_no, merchant_id, amount)
        if (refunded_now + amount).quantize(CENT) >= cap:
            locked.status = ORDER_REFUNDED
        session.add(locked)
        await session.commit()
        await session.refresh(row)
        return row
    except Exception:
        await session.rollback()
        _log.critical("退款上游已成功但记账失败（需人工核对）trade_no=%s refund_no=%s amount=%s",
                      trade_no, refund_no, amount, exc_info=True)
        # 钱已经退出去了：占位单保留为「失败待核对」（status=2），**配额不回退**。
        # 若在这里回退配额，TTL 清理后同一订单还能再退一次 → 真金白银重复退款。
        try:
            row = (await session.exec(
                select(Refund).where(Refund.id == refund_id)
            )).first()
            if row is not None and row.status == 0:
                row.status = 2
                row.result = json.dumps(
                    {"error": "记账失败，需人工核对", "upstream": result},
                    ensure_ascii=False,
                )[:5000]
                row.finished_at = datetime.now()
                session.add(row)
                await session.commit()
        except Exception:
            await session.rollback()
            _log.critical("退款占位单标记失败 refund_no=%s（需人工核对）", refund_no, exc_info=True)
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
