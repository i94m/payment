"""商户结算：申请（余额→冻结）→ 平台打款（冻结扣减）/ 拒绝（冻结→余额退回）。

余额变动一律走 services/balance.change_merchant_balance；frozen_balance 为
「结算在途」口径：申请时转入、打款时扣减、拒绝时退回余额。
状态：0 待处理 / 1 已打款 / 2 已拒绝。

并发纪律（P0-1/P0-2）——**可移植守卫优先，行锁只是附加保险**：
- 申请：可提余额判定写进 `UPDATE merchants ... WHERE 余额-预留-D+1当日 >= 金额`
  的 WHERE（balance.reserve_settlement_funds），靠影响行数判定谁预占成功。
  SQLite 上 `with_for_update` 是空操作，条件 UPDATE + rowcount 才是两侧都成立的
  唯一串行化点，门禁用例（tests/test_p0_concurrency.py）因此在测试库也能证明语义。
- 打款/驳回：`UPDATE settlements SET status=... WHERE id=? AND status=0` + rowcount，
  双击打款、打款/驳回并发时只有一方能改到行，另一方拿到 rowcount=0 直接失败。
- 站内信/邮件在**提交之后**发送：SMTP 最坏 20s 超时不再占着结算行与商户行的锁。
"""
import logging
import random
from datetime import datetime
from decimal import Decimal

from sqlalchemy import update as sa_update
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Merchant, Order, Settlement
from app.services.balance import (
    CENT,
    change_frozen_balance,
    change_merchant_balance,
    dmoney,
    reserve_settlement_funds,
)
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

    串行化：`reserve_settlement_funds` 的条件 UPDATE（余额-预留-D+1 ≥ 金额）+
    影响行数，锁外预检只做快速失败与友好报错。
    """
    amount = dmoney(amount)
    if amount <= 0:
        return None, "结算金额必须大于0"

    # 锁外预检：只是快速失败，真正的判定在条件 UPDATE 的 WHERE 里
    if merchant.status != 1:
        return None, "商户已被封禁，无法结算"
    if merchant.settle_status != 1:
        return None, "结算功能未开启，请联系管理员"

    min_amount = await get_money_setting(session, "settle_min_amount")
    if amount < min_amount:
        return None, f"结算金额不能低于 {min_amount}"

    if not merchant.settle_account or not merchant.settle_name:
        return None, "请先完善结算收款信息"

    merchant_id = merchant.id
    daily_limit = await get_int_setting(session, "settle_daily_limit")
    if daily_limit > 0 and await _today_apply_count(session, merchant_id) >= daily_limit:
        return None, f"今日结算申请已达上限（{daily_limit} 次/天）"

    # D+1：当日收款不计入可提余额（与 settle_fee_preview / auto_settle 同口径）
    hold = await _today_income(session, merchant_id) if await _d1_enabled(session) else Decimal("0.00")

    # ---- 关键段：条件 UPDATE 原子预占（余额 -amount / 冻结 +amount）----
    ok, reason, locked = await reserve_settlement_funds(
        session, merchant_id, amount, hold=hold,
    )
    if not ok:
        # 先取错误文案需要的值再回滚（回滚会让实例过期，async 下访问会抛 MissingGreenlet）
        message = "结算申请失败"
        if reason == "not_found":
            message = "商户不存在"
        elif reason == "disabled":
            message = "商户已被封禁，无法结算"
        elif reason == "settle_off":
            message = "结算功能未开启，请联系管理员"
        elif locked is not None:
            available = (dmoney(locked.balance) - dmoney(locked.remain_money) - hold).quantize(CENT)
            message = f"超出可提余额（当前可提 {max(available, Decimal('0.00'))} 元）"
        await session.rollback()
        return None, message

    fee_rate = await get_money_setting(session, "settle_fee_rate")
    fee_min = await get_money_setting(session, "settle_fee_min")
    fee_max = await get_money_setting(session, "settle_fee_max")
    fee = _calc_fee(amount, fee_rate, fee_min, fee_max)
    real_amount = (amount - fee).quantize(CENT)
    if real_amount <= 0:
        await session.rollback()
        return None, "结算金额不足以支付手续费，请提高结算金额"

    settle = Settlement(
        settle_no=generate_settle_no(),
        merchant_id=merchant_id,
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
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        _log.critical("结算申请提交失败 merchant_id=%s amount=%s", merchant_id, amount, exc_info=True)
        return None, "结算申请提交失败，请重试"
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


async def _claim_settlement(
    session: AsyncSession, settle_id: int, values: dict,
) -> bool:
    """结算单状态机的可移植互斥：条件 UPDATE + 影响行数。

    `WHERE id=? AND status=0` 把「状态还是待处理」和「本次状态迁移」压成同一条
    语句：MySQL 上它同时是行锁，SQLite 上它是写事务的临界区，两边的 rowcount
    都只会有一次命中。因此双击打款、打款+驳回并发时只有一方能通过，
    不再依赖 `SELECT ... FOR UPDATE`（SQLite 空操作）或进程内锁（跨实例无效）。
    """
    stmt = (
        sa_update(Settlement)
        .where(col(Settlement.id) == settle_id, col(Settlement.status) == 0)
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    return result.rowcount == 1


async def _settlement_claim_error(session: AsyncSession, settle_id: int) -> str:
    """占位失败时区分「单不存在」与「状态已变」，不向管理员暴露内部枚举。"""
    exists = (await session.exec(
        select(Settlement.id).where(Settlement.id == settle_id)
    )).first()
    return "结算单状态不允许该操作" if exists is not None else "结算单不存在"


async def _notify_settle(
    session: AsyncSession, merchant_id: int, title: str, content: str,
) -> None:
    """资金事务**提交之后**的结算通知（站内信 + 按订阅发邮件）。

    发信最坏占 20s（SMTP 超时）：放在 commit 之后，结算行/商户行的锁已经释放，
    邮件慢或失败都不会拖住资金事务；通知失败只记日志，不回滚已落账的打款结果。
    """
    from app.services.message import send_merchant_message

    try:
        merchant = await session.get(Merchant, merchant_id)
        if merchant is None:
            return
        await send_merchant_message(
            session, merchant,
            title=title, content=content,
            biz_type="settle", subscribe_flag="notify_settle",
        )
        await session.commit()
    except Exception:
        await session.rollback()
        _log.warning("结算通知发送失败 merchant_id=%s title=%s", merchant_id, title, exc_info=True)


async def mark_settlement_paid(session: AsyncSession, settle_id: int, remark: str = "") -> tuple[bool, str]:
    """平台打款标记（状态迁移 + 冻结扣减，提交后发通知）。"""
    values: dict = {"status": 1, "paid_at": datetime.now()}
    if remark:
        values["remark"] = remark[:255]

    if not await _claim_settlement(session, settle_id, values):
        error = await _settlement_claim_error(session, settle_id)
        await session.rollback()
        return False, error

    settle = (await session.exec(
        select(Settlement).where(Settlement.id == settle_id)
        .execution_options(populate_existing=True)
    )).first()
    if settle is None:  # 理论上不可能（UPDATE 命中过）
        await session.rollback()
        return False, "结算单不存在"

    try:
        await change_frozen_balance(session, settle.merchant_id, -dmoney(settle.amount))
    except ValueError as e:
        # 先取日志字段再回滚：rollback 会让实例过期，过期属性访问在 async 下会抛 MissingGreenlet
        settle_no, merchant_id, settle_amount = settle.settle_no, settle.merchant_id, settle.amount
        await session.rollback()
        _log.critical("结算打款冻结扣减异常 settle_no=%s merchant_id=%s amount=%s: %s",
                      settle_no, merchant_id, settle_amount, e)
        return False, "冻结余额异常，请先核对资金流水再重试"

    merchant_id, settle_no = settle.merchant_id, settle.settle_no
    real_amount, account = settle.real_amount, settle.account
    try:
        await session.commit()
    except Exception:
        # 提交失败（并发写冲突/连接中断）：整笔回滚，绝不能对管理员报"已打款"
        await session.rollback()
        _log.critical("结算打款提交失败 settle_no=%s", settle_no, exc_info=True)
        return False, "打款提交失败，请重试"

    await _notify_settle(
        session, merchant_id, "结算已打款",
        f"结算单 {settle_no} 已打款 {real_amount} 元至 {account}。",
    )
    return True, ""


async def reject_settlement(session: AsyncSession, settle_id: int, remark: str = "") -> tuple[bool, str]:
    """拒绝结算 → 冻结退回余额（状态迁移 + 资金退回，提交后发通知）。"""
    values: dict = {"status": 2}
    if remark:
        values["remark"] = remark[:255]

    if not await _claim_settlement(session, settle_id, values):
        error = await _settlement_claim_error(session, settle_id)
        await session.rollback()
        return False, error

    settle = (await session.exec(
        select(Settlement).where(Settlement.id == settle_id)
        .execution_options(populate_existing=True)
    )).first()
    if settle is None:  # 理论上不可能（UPDATE 命中过）
        await session.rollback()
        return False, "结算单不存在"

    try:
        await change_frozen_balance(session, settle.merchant_id, -dmoney(settle.amount))
    except ValueError as e:
        settle_no, merchant_id, settle_amount = settle.settle_no, settle.merchant_id, settle.amount
        await session.rollback()
        _log.critical("结算驳回冻结扣减异常 settle_no=%s merchant_id=%s amount=%s: %s",
                      settle_no, merchant_id, settle_amount, e)
        return False, "冻结余额异常，请先核对资金流水再重试"

    await change_merchant_balance(
        session, settle.merchant_id, settle.amount,
        biz_type="settle_reject", trade_no=settle.settle_no, remark="结算拒绝退回",
    )
    merchant_id, settle_no = settle.merchant_id, settle.settle_no
    amount = settle.amount
    try:
        await session.commit()
    except Exception:
        await session.rollback()
        _log.critical("结算驳回提交失败 settle_no=%s", settle_no, exc_info=True)
        return False, "驳回提交失败，请重试"

    await _notify_settle(
        session, merchant_id, "结算被驳回",
        f"结算单 {settle_no} 被驳回，{amount} 元已退回余额。原因：{remark or '无'}",
    )
    return True, ""
