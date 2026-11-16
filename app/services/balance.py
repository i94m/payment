"""商户资金变更 — 全站唯一记账入口。

余额：必须通过 change_merchant_balance（行锁 + 流水 + 余额原子更新）。
冻结：必须通过 change_frozen_balance（结算在途口径，行锁 + 非负校验）。
金额一律 Decimal，quantize(0.01) 后入库。

两个函数都先 `lock_merchant`：MySQL RR 下普通 SELECT 命中调用方事务的旧快照，
而金额判断（可提余额、冻结扣减）必须基于最新已提交值 —— 行锁当前读才是唯一的
串行化点，锁外预检只能当快速失败用。
"""
from decimal import Decimal, ROUND_HALF_UP

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Merchant, MerchantRecord

CENT = Decimal("0.01")


def dmoney(v) -> Decimal:
    """入口金额规范化：Decimal(str(x)) + quantize(0.01)，严禁 float 直接运算。"""
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


async def lock_merchant(session: AsyncSession, merchant_id: int) -> Merchant:
    """行锁读商户（当前读 + populate_existing：绕过 RR 快照拿最新已提交值）。

    资金写路径的第一把锁。并发资金操作（记账/退款/结算）由此串行化；
    调用方负责事务边界（提交或回滚），锁随事务释放。
    """
    merchant = (await session.exec(
        select(Merchant)
        .where(Merchant.id == merchant_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )).first()
    if not merchant:
        raise ValueError(f"商户 {merchant_id} 不存在")
    return merchant


async def change_merchant_balance(
    session: AsyncSession,
    merchant_id: int,
    amount: Decimal | str | int,
    biz_type: str,
    trade_no: str | None = None,
    remark: str | None = None,
) -> MerchantRecord:
    """变更商户余额并写流水（调用方负责外层事务/提交）。

    amount: 正=增加，负=减少
    biz_type: order_income/settle/settle_reject/adjust/recharge...
    """
    amount = dmoney(amount)
    if amount == 0:
        raise ValueError("余额变更金额不能为 0")

    # 行锁锁定商户（当前读，余额判断基于最新值）
    merchant = await lock_merchant(session, merchant_id)

    balance_before = dmoney(merchant.balance)
    balance_after = (balance_before + amount).quantize(CENT, rounding=ROUND_HALF_UP)
    if balance_after < 0:
        raise ValueError("余额不足")

    merchant.balance = balance_after
    record = MerchantRecord(
        merchant_id=merchant_id,
        biz_type=biz_type,
        amount=amount,
        balance_before=balance_before,
        balance_after=balance_after,
        order_trade_no=trade_no,
        remark=remark,
    )
    session.add(record)
    return record


async def change_frozen_balance(
    session: AsyncSession,
    merchant_id: int,
    amount: Decimal | str | int,
    allow_negative: bool = False,
) -> Merchant:
    """变更「结算在途」冻结余额（行锁 + 非负兜底），返回锁定的商户实例。

    frozen_balance 是结算申请（转入）→ 打款（扣减）/ 驳回（扣减）的中间口径，
    任何写入都必须走这里：原来散落在 settle.py 的 `max(frozen-amount, 0)` 截断
    会把「冻结不足」的错账静默抹平，这里改为显式报错（allow_negative=False）。
    调用方负责提交/回滚。
    """
    amount = dmoney(amount)
    if amount == 0:
        raise ValueError("冻结余额变更金额不能为 0")

    merchant = await lock_merchant(session, merchant_id)
    before = dmoney(merchant.frozen_balance)
    after = (before + amount).quantize(CENT, rounding=ROUND_HALF_UP)
    if after < 0 and not allow_negative:
        raise ValueError(f"冻结余额不足（当前 {before:f}，需要 {-amount:f}）")
    merchant.frozen_balance = after
    session.add(merchant)
    return merchant
