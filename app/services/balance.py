"""商户余额变更 — 全站唯一记账入口。

必须通过 change_merchant_balance 变更余额（行锁 + 流水 + 余额原子更新）。
金额一律 Decimal，quantize(0.01) 后入库。
"""
from decimal import Decimal, ROUND_HALF_UP

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Merchant, MerchantRecord

CENT = Decimal("0.01")


def dmoney(v) -> Decimal:
    """入口金额规范化：Decimal(str(x)) + quantize(0.01)，严禁 float 直接运算。"""
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


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

    # 行锁锁定商户
    merchant = (await session.exec(
        select(Merchant).where(Merchant.id == merchant_id).with_for_update()
    )).first()
    if not merchant:
        raise ValueError(f"商户 {merchant_id} 不存在")

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
