"""商户结算：申请（余额→冻结）→ 平台打款（冻结扣减）/ 拒绝（冻结→余额退回）。

余额变动一律走 services/balance.change_merchant_balance；frozen_balance 为
「结算在途」口径：申请时转入、打款时扣减、拒绝时退回余额。
状态：0 待处理 / 1 已打款 / 2 已拒绝。

并发纪律（P0-1/P0-2）：
- 申请：先 `lock_merchant` 拿商户行锁，**锁内**重算可提余额 + 当日申请次数，
  再扣余额/转冻结 —— 可提余额校验不再发生在锁外旧快照上。
- 打款/驳回：先 `lock_settlement` 锁结算单行，状态机判断与冻结扣减在同一事务内，
  双击打款或打款+驳回并发时只有一个请求能通过 status==0 校验。
- 冻结扣减走 change_frozen_balance：不做 `max(...,0)` 截断，冻结不足显式报错。
"""
import logging
import random
from datetime import datetime
from decimal import Decimal

from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Merchant, Order, Settlement
from app.services.balance import (
    CENT,
    change_frozen_balance,
    change_merchant_balance,
    dmoney,
    lock_merchant,
)
from app.services.coop_lock import coop_lock
from app.services.settings import get_int_setting, get_money_setting

_log = logging.getLogger(__name__)

# 结算方式
SETTLE_TYPES = {1: "支付宝", 2: "微信", 3: "QQ", 4: "银行卡"}


def generate_settle_no() -> str:
    return "S" + datetime.now().strftime("%y%m%d%H%M%S") + str(random.randint(1000, 9999))


def _calc_fee(amount: Decimal, fee_rate: Decimal, fee_min: Decimal, fee_max: Decimal) -> Decimal:
    """手续费 = 金额×费率%，应用保底/封顶（0=不生效）；封顶不超过结算金额本身。"""
    fee = (amount * fee_rate / Decimal(100)).quantize(Decimal("0.01"))
    if fee_min > 0 and fee < fee_min:
        fee = fee_min
    if fee_max > 0 and fee > fee_max:
        fee = fee_max
    if fee > amount:  # 保底手续费高于结算金额时不能出现负到账
        fee = amount
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


async def _today_apply_count_locked(session: AsyncSession, merchant_id: int) -> int:
    """当日申请次数（行锁当前读：调用方已持有商户行锁，串行化后不再吃旧快照）。"""
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    rows = (await session.exec(
        select(Settlement.id).where(
            col(Settlement.merchant_id) == merchant_id,
            col(Settlement.created_at) >= today_start,
        ).with_for_update()
    )).all()
    return len(rows)


async def _d1_enabled(session: AsyncSession) -> bool:
    from app.services.settings import get_setting
    return bool(await get_setting(session, "settle_d1_enable", False))


async def _withdrawable(session: AsyncSession, merchant: Merchant) -> Decimal:
    """可提余额口径：余额 - 预留余额；D+1 开启时再扣除当日收款。"""
    withdrawable = dmoney(merchant.balance) - dmoney(merchant.remain_money)
    if await _d1_enabled(session):
        withdrawable -= await _today_income(session, merchant.id)
    return withdrawable.quantize(CENT)


async def apply_settlement(
    session: AsyncSession,
    merchant: Merchant,
    amount: Decimal | str,
) -> tuple[Settlement | None, str]:
    """商户发起结算申请（余额转入冻结）。返回 (结算单, 错误信息)。

    串行化：同一商户的申请走进程内协作锁 + 行锁，锁内重算可提余额。
    """
    merchant_id = merchant.id
    async with coop_lock(f"settle:apply:{merchant_id}"):
        return await _apply_settlement_impl(session, merchant, amount)


async def _apply_settlement_impl(
    session: AsyncSession,
    merchant: Merchant,
    amount: Decimal | str,
) -> tuple[Settlement | None, str]:
    amount = dmoney(amount)
    if amount <= 0:
        return None, "结算金额必须大于0"

    # 锁外预检：只是快速失败，真正的判断在商户行锁内重做
    if merchant.status != 1:
        return None, "商户已被封禁，无法结算"
    if merchant.settle_status != 1:
        return None, "结算功能未开启，请联系管理员"

    min_amount = await get_money_setting(session, "settle_min_amount")
    if amount < min_amount:
        return None, f"结算金额不能低于 {min_amount}"

    if not merchant.settle_account or not merchant.settle_name:
        return None, "请先完善结算收款信息"

    # ---- 关键段：商户行锁 → 锁内重算可提余额/当日次数 → 扣余额 + 转冻结 ----
    try:
        locked = await lock_merchant(session, merchant.id)
    except ValueError:
        return None, "商户不存在"
    if locked.status != 1:
        await session.rollback()
        return None, "商户已被封禁，无法结算"
    if locked.settle_status != 1:
        await session.rollback()
        return None, "结算功能未开启，请联系管理员"

    daily_limit = await get_int_setting(session, "settle_daily_limit")
    if daily_limit > 0 and await _today_apply_count_locked(session, locked.id) >= daily_limit:
        await session.rollback()
        return None, f"今日结算申请已达上限（{daily_limit} 次/天）"

    withdrawable = await _withdrawable(session, locked)
    if amount > withdrawable:
        await session.rollback()
        return None, f"超出可提余额（当前可提 {withdrawable.quantize(CENT)} 元）"

    fee_rate = await get_money_setting(session, "settle_fee_rate")
    fee_min = await get_money_setting(session, "settle_fee_min")
    fee_max = await get_money_setting(session, "settle_fee_max")
    fee = _calc_fee(amount, fee_rate, fee_min, fee_max)
    real_amount = (amount - fee).quantize(CENT)
    if real_amount <= 0:
        await session.rollback()
        return None, "结算金额不足以支付手续费，请提高结算金额"

    await change_merchant_balance(
        session, locked.id, -amount,
        biz_type="settle", remark="结算申请冻结",
    )
    await change_frozen_balance(session, locked.id, amount)

    settle = Settlement(
        settle_no=generate_settle_no(),
        merchant_id=locked.id,
        amount=amount,
        fee=fee,
        real_amount=real_amount,
        settle_type=locked.settle_type or 1,
        account=locked.settle_account,
        account_name=locked.settle_name,
        status=0,
        created_at=datetime.now(),  # 应用侧本地时间（DB 默认值在 SQLite 为 UTC，影响每日上限统计）
    )
    session.add(settle)
    await session.commit()
    await session.refresh(settle)
    return settle, ""


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
        "withdrawable": str(max(balance - remain - today_income, Decimal("0.00")).quantize(CENT)),
        "amount": str(amount),
        "fee": str(fee),
        "real_amount": str((amount - fee).quantize(CENT)) if amount > 0 else "0.00",
    }


async def auto_settle_merchants(session: AsyncSession) -> int:
    """自动结算：为 auto_settle=1 且可提余额达标的商户批量发起结算（cron 调用）。

    可提余额口径必须与商户手动申请一致（含 D+1 当日收款扣减），否则开了 D+1 的
    商户会被一直「超出可提余额」拒掉。单商户失败只记日志，不影响整批；
    返回成功发起的结算单数。
    """
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
        merchant_id = m.id
        try:
            withdrawable = await _withdrawable(session, m)
        except Exception:
            _log.warning("自动结算：可提余额计算失败 merchant_id=%s", merchant_id, exc_info=True)
            await session.rollback()
            continue
        if withdrawable <= 0 or withdrawable < min_amount:
            continue
        settle, error = await apply_settlement(session, m, withdrawable)
        if error:
            # 不再静默吞错：至少留一条可检索的日志（含拒绝原因）
            _log.info("自动结算未发起 merchant_id=%s amount=%s: %s",
                      merchant_id, withdrawable, error)
            continue
        count += 1
    return count


async def _lock_settlement(session: AsyncSession, settle_id: int) -> Settlement | None:
    """行锁读结算单（当前读：状态机判断不吃调用方事务的旧快照）。"""
    return (await session.exec(
        select(Settlement)
        .where(Settlement.id == settle_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).first()


async def mark_settlement_paid(session: AsyncSession, settle_id: int, remark: str = "") -> tuple[bool, str]:
    """平台打款标记（冻结扣减 + 站内消息 + 按订阅发邮件）。"""
    async with coop_lock(f"settle:op:{settle_id}"):
        return await _mark_settlement_paid_impl(session, settle_id, remark)


async def _mark_settlement_paid_impl(
    session: AsyncSession, settle_id: int, remark: str = "",
) -> tuple[bool, str]:
    from app.services.message import send_merchant_message

    settle = await _lock_settlement(session, settle_id)
    if not settle:
        return False, "结算单不存在"
    if settle.status != 0:
        await session.rollback()
        return False, "结算单状态不允许该操作"

    try:
        merchant = await change_frozen_balance(session, settle.merchant_id, -dmoney(settle.amount))
    except ValueError as e:
        # 先取日志字段再回滚：rollback 会让实例过期，过期属性访问在 async 下会抛 MissingGreenlet
        settle_no, merchant_id, settle_amount = settle.settle_no, settle.merchant_id, settle.amount
        await session.rollback()
        _log.critical("结算打款冻结扣减异常 settle_no=%s merchant_id=%s amount=%s: %s",
                      settle_no, merchant_id, settle_amount, e)
        return False, "冻结余额异常，请先核对资金流水再重试"

    settle.status = 1
    settle.paid_at = datetime.now()
    if remark:
        settle.remark = remark[:255]
    session.add(settle)
    await send_merchant_message(
        session, merchant,
        title="结算已打款",
        content=f"结算单 {settle.settle_no} 已打款 {settle.real_amount} 元至 {settle.account}。",
        biz_type="settle", subscribe_flag="notify_settle",
    )
    settle_no = settle.settle_no
    try:
        await session.commit()
    except Exception:
        # 提交失败（并发写冲突/连接中断）：整笔回滚，绝不能对管理员报"已打款"
        await session.rollback()
        _log.critical("结算打款提交失败 settle_no=%s", settle_no, exc_info=True)
        return False, "打款提交失败，请重试"
    return True, ""


async def reject_settlement(session: AsyncSession, settle_id: int, remark: str = "") -> tuple[bool, str]:
    """拒绝结算 → 冻结退回余额。"""
    async with coop_lock(f"settle:op:{settle_id}"):
        return await _reject_settlement_impl(session, settle_id, remark)


async def _reject_settlement_impl(
    session: AsyncSession, settle_id: int, remark: str = "",
) -> tuple[bool, str]:
    from app.services.message import send_merchant_message

    settle = await _lock_settlement(session, settle_id)
    if not settle:
        return False, "结算单不存在"
    if settle.status != 0:
        await session.rollback()
        return False, "结算单状态不允许该操作"

    try:
        await change_frozen_balance(session, settle.merchant_id, -dmoney(settle.amount))
    except ValueError as e:
        settle_no, merchant_id, settle_amount = settle.settle_no, settle.merchant_id, settle.amount
        await session.rollback()
        _log.critical("结算驳回冻结扣减异常 settle_no=%s merchant_id=%s amount=%s: %s",
                      settle_no, merchant_id, settle_amount, e)
        return False, "冻结余额异常，请先核对资金流水再重试"

    settle.status = 2
    if remark:
        settle.remark = remark[:255]
    session.add(settle)
    await change_merchant_balance(
        session, settle.merchant_id, settle.amount,
        biz_type="settle_reject", trade_no=settle.settle_no, remark="结算拒绝退回",
    )
    merchant = await session.get(Merchant, settle.merchant_id)
    settle_no = settle.settle_no
    if merchant:
        await send_merchant_message(
            session, merchant,
            title="结算被驳回",
            content=f"结算单 {settle.settle_no} 被驳回，{settle.amount} 元已退回余额。原因：{remark or '无'}",
            biz_type="settle", subscribe_flag="notify_settle",
        )
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        _log.critical("结算驳回提交失败 settle_no=%s", settle_no, exc_info=True)
        return False, "驳回提交失败，请重试"
    return True, ""
