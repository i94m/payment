"""商户资金变更 — 全站唯一记账入口。

余额：必须通过 change_merchant_balance（行锁 + 流水 + 余额原子更新）。
冻结：必须通过 change_frozen_balance（结算在途口径，行锁 + 非负校验）。
金额一律 Decimal，quantize(0.01) 后入库。

两个函数都先 `lock_merchant`：MySQL RR 下普通 SELECT 命中调用方事务的旧快照，
而金额判断（可提余额、冻结扣减）必须基于最新已提交值 —— 行锁当前读才是唯一的
串行化点，锁外预检只能当快速失败用。
"""
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import update as sa_update
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Merchant, MerchantRecord

CENT = Decimal("0.01")

# 条件 UPDATE 比较用的容差：SQLite（测试库）把 Numeric 落成 float，金额相减可能带
# ~1e-13 的浮点噪声，恰好落在边界上会被误判；MySQL DECIMAL 运算无此问题。
# 真正的精确判定在「更新成功后按 Decimal 复核」里做，这里只避免误杀合法请求。
EPS = Decimal("0.000001")


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


async def reserve_settlement_funds(
    session: AsyncSession,
    merchant_id: int,
    amount: Decimal | str,
    *,
    hold: Decimal | str = "0.00",
) -> tuple[bool, str, Merchant | None]:
    """结算申请的资金预占（余额 -amount / 冻结 +amount），原子且可移植。

    「可提余额够不够」不能只靠 `with_for_update`：SQLite（测试库/门禁用例）上它是
    空操作，行锁也只在单进程内可见。这里把判定条件直接写进 UPDATE 的 WHERE，
    用影响行数（rowcount）决定谁预占成功 —— MySQL / SQLite 语义一致，跨进程、
    跨实例都成立（这才是唯一正确的串行化点；行锁只是 MySQL 上的附加保险）。

    hold：可提余额口径里要额外预留的金额（D+1 开启时=当日收款）。

    返回 (是否成功, 失败原因码, 预占后的商户)。原因码供调用方拼人话：
    not_found / disabled / settle_off / insufficient。
    """
    amount = dmoney(amount)
    hold = dmoney(hold)
    if amount <= 0:
        raise ValueError("结算金额必须大于0")

    stmt = (
        sa_update(Merchant)
        .where(
            col(Merchant.id) == merchant_id,
            col(Merchant.status) == 1,
            col(Merchant.settle_status) == 1,
            # 可提余额 = 余额 - 预留 - D+1 当日收款，必须覆盖本次申请
            col(Merchant.balance) - col(Merchant.remain_money) - hold >= amount - EPS,
        )
        .values(
            balance=col(Merchant.balance) - amount,
            frozen_balance=col(Merchant.frozen_balance) + amount,
        )
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    if result.rowcount != 1:
        # 行存在但被状态/可提余额拦下：读出当前值给调用方拼错误文案
        current = (await session.exec(
            select(Merchant).where(Merchant.id == merchant_id)
            .execution_options(populate_existing=True)
        )).first()
        if current is None:
            return False, "not_found", None
        if current.status != 1:
            return False, "disabled", current
        if current.settle_status != 1:
            return False, "settle_off", current
        return False, "insufficient", current

    merchant = (await session.exec(
        select(Merchant).where(Merchant.id == merchant_id)
        .execution_options(populate_existing=True)
    )).first()
    if merchant is None:  # 理论上不可能（UPDATE 命中过）
        raise ValueError(f"商户 {merchant_id} 不存在")

    balance_after = dmoney(merchant.balance)
    balance_before = (balance_after + amount).quantize(CENT, rounding=ROUND_HALF_UP)
    if balance_before - dmoney(merchant.remain_money) - hold < amount:
        # 精确复核（消掉 SQLite 浮点噪声带来的 EPS 放行）：失败则整笔回滚，
        # 由调用方按「超出可提余额」返回，账上不会留下半截预占。
        return False, "insufficient", merchant

    session.add(MerchantRecord(
        merchant_id=merchant_id,
        biz_type="settle",
        amount=-amount,
        balance_before=balance_before,
        balance_after=balance_after,
        remark="结算申请冻结",
    ))
    return True, "", merchant

