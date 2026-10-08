"""商户结算：申请（余额→冻结）→ 平台打款（冻结扣减）/ 拒绝（冻结→余额退回）。

余额变动一律走 services/balance.change_merchant_balance；frozen_balance 为
「结算在途」口径：申请时转入、打款时扣减、拒绝时退回余额。
状态：0 待处理 / 1 已打款 / 2 已拒绝。
"""
import random
from datetime import datetime, timedelta
from decimal import Decimal

from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Merchant, Order, Settlement
from app.services.balance import change_merchant_balance, dmoney
from app.services.settings import get_int_setting, get_money_setting

# 结算方式
SETTLE_TYPES = {1: "支付宝", 2: "微信", 3: "QQ", 4: "银行卡"}


def generate_settle_no() -> str:
    return "S" + datetime.now().strftime("%y%m%d%H%M%S") + str(random.randint(1000, 9999))


def _calc_fee(amount: Decimal, fee_rate: Decimal, fee_min: Decimal, fee_max: Decimal) -> Decimal:
    """手续费 = 金额×费率%，应用保底/封顶（0=不生效）。"""
    fee = (amount * fee_rate / Decimal(100)).quantize(Decimal("0.01"))
    if fee_min > 0 and fee < fee_min:
        fee = fee_min
    if fee_max > 0 and fee > fee_max:
        fee = fee_max
    return fee.quantize(Decimal("0.01"))


async def _today_income(session: AsyncSession, merchant_id: int) -> Decimal:
    """当日收款到账（D+1 口径用；不含充值单与扣量单）。"""
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    income = (await session.exec(
        select(func.coalesce(func.sum(Order.merchant_income), 0)).where(
            col(Order.merchant_id) == merchant_id,
            col(Order.status) == 1,
            col(Order.order_type) == 0,
            col(Order.is_deducted) == False,  # noqa: E712
            col(Order.paid_at) >= today_start,
        )
    )).one()
    return dmoney(income)


async def _today_apply_count(session: AsyncSession, merchant_id: int) -> int:
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return int((await session.exec(
        select(func.count()).select_from(Settlement).where(
            col(Settlement.merchant_id) == merchant_id,
            col(Settlement.created_at) >= today_start,
        )
    )).one())


async def apply_settlement(
    session: AsyncSession,
    merchant: Merchant,
    amount: Decimal | str,
) -> tuple[Settlement | None, str]:
    """商户发起结算申请（余额转入冻结）。返回 (结算单, 错误信息)。"""
    amount = dmoney(amount)
    if amount <= 0:
        return None, "结算金额必须大于0"

    if merchant.status != 1:
        return None, "商户已被封禁，无法结算"
    if merchant.settle_status != 1:
        return None, "结算功能未开启，请联系管理员"

    min_amount = await get_money_setting(session, "settle_min_amount")
    if amount < min_amount:
        return None, f"结算金额不能低于 {min_amount}"

    # 每日申请次数上限（0=不限）
    daily_limit = await get_int_setting(session, "settle_daily_limit")
    if daily_limit > 0:
        used = await _today_apply_count(session, merchant.id)
        if used >= daily_limit:
            return None, f"今日结算申请已达上限（{daily_limit} 次/天）"

    # 可提余额口径：余额 - 预留余额；D+1 开启时再扣除当日收款
    withdrawable = dmoney(merchant.balance) - dmoney(merchant.remain_money)
    if await _d1_enabled(session):
        today_income = await _today_income(session, merchant.id)
        withdrawable -= today_income
    if amount > withdrawable:
        return None, f"超出可提余额（当前可提 {withdrawable.quantize(Decimal('0.01'))} 元）"

    if not merchant.settle_account or not merchant.settle_name:
        return None, "请先完善结算收款信息"

    # 行锁校验余额并扣款（防并发多次申请透支），随后转入冻结口径
    await change_merchant_balance(
        session, merchant.id, -amount,
        biz_type="settle", remark="结算申请冻结",
    )
    locked_m = await session.get(Merchant, merchant.id)
    locked_m.frozen_balance = (dmoney(locked_m.frozen_balance) + amount).quantize(Decimal("0.01"))

    fee_rate = await get_money_setting(session, "settle_fee_rate")
    fee_min = await get_money_setting(session, "settle_fee_min")
    fee_max = await get_money_setting(session, "settle_fee_max")
    fee = _calc_fee(amount, fee_rate, fee_min, fee_max)
    real_amount = (amount - fee).quantize(Decimal("0.01"))

    settle = Settlement(
        settle_no=generate_settle_no(),
        merchant_id=merchant.id,
        amount=amount,
        fee=fee,
        real_amount=real_amount,
        settle_type=merchant.settle_type or 1,
        account=merchant.settle_account,
        account_name=merchant.settle_name,
        status=0,
        created_at=datetime.now(),  # 应用侧本地时间（DB 默认值在 SQLite 为 UTC，影响每日上限统计）
    )
    session.add(settle)
    await session.commit()
    await session.refresh(settle)
    return settle, ""


async def _d1_enabled(session: AsyncSession) -> bool:
    from app.services.settings import get_setting
    return bool(await get_setting(session, "settle_d1_enable", False))


async def settle_fee_preview(session: AsyncSession, merchant: Merchant, amount: Decimal | str = "0") -> dict:
    """结算申请页预览数据：费率/起结/预留/可提（含 D+1 口径）+ 手续费与到账。"""
    from app.services.settings import get_setting

    amount = dmoney(amount)
    fee_rate = await get_money_setting(session, "settle_fee_rate")
    fee_min = await get_money_setting(session, "settle_fee_min")
    fee_max = await get_money_setting(session, "settle_fee_max")
    min_amount = await get_money_setting(session, "settle_min_amount")
    remain = dmoney(merchant.remain_money)
    balance = dmoney(merchant.balance)
    d1 = bool(await get_setting(session, "settle_d1_enable", False))
    today_income = await _today_income(session, merchant.id) if d1 else Decimal("0.00")
    daily_limit = await get_int_setting(session, "settle_daily_limit")
    daily_used = await _today_apply_count(session, merchant.id) if daily_limit > 0 else 0
    fee = _calc_fee(amount, fee_rate, fee_min, fee_max) if amount > 0 else Decimal("0.00")
    return {
        "fee_rate": str(fee_rate),
        "fee_min": str(fee_min),
        "fee_max": str(fee_max),
        "min_amount": str(min_amount),
        "remain_money": str(remain),
        "d1_enable": d1,
        "today_income": str(today_income),
        "daily_limit": daily_limit,
        "daily_used": daily_used,
        "withdrawable": str(max(balance - remain - today_income, Decimal("0.00")).quantize(Decimal("0.01"))),
        "amount": str(amount),
        "fee": str(fee),
        "real_amount": str((amount - fee).quantize(Decimal("0.01"))) if amount > 0 else "0.00",
    }


async def auto_settle_merchants(session: AsyncSession) -> int:
    """自动结算：为 auto_settle=1 且可提余额达标的商户批量发起结算（cron 调用）。

    单商户失败不中断整批；返回成功发起的结算单数。
    """
    from sqlmodel import col

    from app.services.settings import get_money_setting

    merchants = (await session.exec(
        select(Merchant).where(
            col(Merchant.auto_settle) == True,  # noqa: E712
            col(Merchant.settle_status) == 1,
            col(Merchant.status) == 1,
        )
    )).all()
    if not merchants:
        return 0

    min_amount = await get_money_setting(session, "settle_min_amount")
    count = 0
    for m in merchants:
        withdrawable = dmoney(m.balance) - dmoney(m.remain_money)
        if withdrawable < min_amount:
            continue
        settle, error = await apply_settlement(session, m, withdrawable)
        if error:
            continue
        count += 1
    return count


async def mark_settlement_paid(session: AsyncSession, settle_id: int, remark: str = "") -> tuple[bool, str]:
    """平台打款标记（冻结扣减 + 站内消息 + 按订阅发邮件）。"""
    from app.services.message import send_merchant_message

    settle = await session.get(Settlement, settle_id)
    if not settle:
        return False, "结算单不存在"
    if settle.status != 0:
        return False, "结算单状态不允许该操作"
    settle.status = 1
    settle.paid_at = datetime.now()
    if remark:
        settle.remark = remark[:255]
    session.add(settle)
    merchant = await session.get(Merchant, settle.merchant_id)
    if merchant:
        # 冻结口径扣减（申请时已从余额转入）
        merchant.frozen_balance = max(
            dmoney(merchant.frozen_balance) - dmoney(settle.amount), Decimal("0.00")
        ).quantize(Decimal("0.01"))
        session.add(merchant)
        await send_merchant_message(
            session, merchant,
            title="结算已打款",
            content=f"结算单 {settle.settle_no} 已打款 {settle.real_amount} 元至 {settle.account}。",
            biz_type="settle", subscribe_flag="notify_settle",
        )
    await session.commit()
    return True, ""


async def reject_settlement(session: AsyncSession, settle_id: int, remark: str = "") -> tuple[bool, str]:
    """拒绝结算 → 冻结退回余额。"""
    from app.services.message import send_merchant_message

    settle = await session.get(Settlement, settle_id)
    if not settle:
        return False, "结算单不存在"
    if settle.status != 0:
        return False, "结算单状态不允许该操作"

    settle.status = 2
    if remark:
        settle.remark = remark[:255]
    session.add(settle)
    merchant = await session.get(Merchant, settle.merchant_id)
    if merchant:
        merchant.frozen_balance = max(
            dmoney(merchant.frozen_balance) - dmoney(settle.amount), Decimal("0.00")
        ).quantize(Decimal("0.01"))
        session.add(merchant)
    await change_merchant_balance(
        session, settle.merchant_id, settle.amount,
        biz_type="settle_reject", trade_no=settle.settle_no, remark="结算拒绝退回",
    )
    merchant = await session.get(Merchant, settle.merchant_id)
    if merchant:
        await send_merchant_message(
            session, merchant,
            title="结算被驳回",
            content=f"结算单 {settle.settle_no} 被驳回，{settle.amount} 元已退回余额。原因：{remark or '无'}",
            biz_type="settle", subscribe_flag="notify_settle",
        )
    await session.commit()
    return True, ""
