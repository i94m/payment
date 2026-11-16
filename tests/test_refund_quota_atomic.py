"""退款配额原子计数列（orders.refunded_amount）回归用例。

背景：P0-3（并发部分退款超退）在 main 上的守卫是「锁订单行 + 锁内求和」。行锁在
SQLite（测试库）上是空操作，门禁证明的只是进程内行为；跨进程/跨实例真正成立的
是「单行条件 UPDATE + 影响行数」这种可移植形态：

    UPDATE orders SET refunded_amount = refunded_amount + :amt
     WHERE trade_no = ? AND status = 1 AND refunded_amount + :amt <= 可退上限

要点：
- 配额列语义 = **已退 + 在途预占**，占位成功即生效，不等上游返回
- 上游失败 / 孤儿预占 → 等额回退（只减不增，最低夹到 0），绝不永久锁死额度
- 对外报表口径不变：仍按**成功**退款单求和（`services/refund.py::refunded_amount`）
- 记账失败（钱已退出）**不回退**配额，避免同一订单再退一次

这些用例对旧实现（只按退款单求和）是红的：旧实现下配额只写在列上、流水里没有对应
退款单时，判定会算出「零已用」而放行超退（见 `test_quota_guard_reads_column_not_refund_rows`）。
"""
from decimal import Decimal

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Order, Refund
from app.services.balance import dmoney
from app.services.refund import refunded_amount
from tests.conftest import upstream_state
from tests.test_deduct_refill import _balance, _refetch
from tests.test_fund_lifecycle import _pay_order, _refund


def _fresh_maker(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.mark.asyncio
async def test_quota_guard_reads_column_not_refund_rows(client, seeded, session, engine):
    """配额判定只看订单行的累计列：流水里没有对应退款单也必须拦住超退。

    这正是「可移植原子更新」与旧实现的区别点 —— 旧实现按 `refunds` 求和，
    在途占位只存在于列上时它会算出 0 已用、放行 60 而造成超退。
    """
    order = await _pay_order(client, session, "QTA-1")

    # 可退上限 98；模拟「另一笔在途退款已占 50」——只有配额列，没有成功退款单
    await session.exec(update(Order).where(Order.trade_no == order.trade_no)
                       .values(refunded_amount=dmoney("50.00")))
    await session.commit()

    over = await _refund(client, order.trade_no, amount="60.00")
    assert over["code"] != 0
    assert "可退" in over["message"]
    assert await _balance(engine, seeded["merchant"].id) == dmoney("98.00")

    ok = await _refund(client, order.trade_no, amount="48.00", out_refund_no="QTA-OK")
    assert ok["code"] == 0, ok

    fresh = await _refetch(engine, order.trade_no)
    assert dmoney(fresh.refunded_amount) == dmoney("98.00")  # 50 在途 + 48 本次
    assert await _balance(engine, seeded["merchant"].id) == dmoney("50.00")


@pytest.mark.asyncio
async def test_quota_released_when_upstream_fails(client, seeded, session, engine):
    """上游失败 → 占位单删除 + 配额等额回退，下一次全额退款仍可成功（不永久漏额度）。"""
    order = await _pay_order(client, session, "QTA-2")

    upstream_state["refund_ok"] = False
    try:
        body = await _refund(client, order.trade_no, amount="60.00", out_refund_no="QTA-FAIL")
        assert body["code"] != 0
    finally:
        upstream_state["refund_ok"] = True

    fresh = await _refetch(engine, order.trade_no)
    assert dmoney(fresh.refunded_amount) == dmoney("0.00"), "上游失败未回退配额"
    rows = (await session.exec(select(Refund).where(Refund.trade_no == order.trade_no))).all()
    assert rows == []
    assert await _balance(engine, seeded["merchant"].id) == dmoney("98.00")

    full = await _refund(client, order.trade_no, amount="98.00", out_refund_no="QTA-AFTER")
    assert full["code"] == 0, full
    fresh = await _refetch(engine, order.trade_no)
    assert dmoney(fresh.refunded_amount) == dmoney("98.00")
    assert fresh.status == 2


@pytest.mark.asyncio
async def test_quota_release_is_clamped_at_zero(client, seeded, session, engine):
    """回退只减不增且夹到 0（孤儿占位/重复清理不会把配额减成负数）。"""
    from app.services.refund import _release_refund_quota

    order = await _pay_order(client, session, "QTA-3")

    await session.exec(update(Order).where(Order.trade_no == order.trade_no)
                       .values(refunded_amount=dmoney("10.00")))
    await session.commit()

    await _release_refund_quota(session, order.trade_no, dmoney("60.00"))
    await session.commit()

    got = (await session.exec(
        select(Order).where(Order.trade_no == order.trade_no)
        .execution_options(populate_existing=True))).one()
    assert dmoney(got.refunded_amount) == dmoney("0.00")

    # 已经是 0 再回退一次也不能变负
    await _release_refund_quota(session, order.trade_no, dmoney("5.00"))
    await session.commit()
    got = (await session.exec(
        select(Order).where(Order.trade_no == order.trade_no)
        .execution_options(populate_existing=True))).one()
    assert dmoney(got.refunded_amount) == dmoney("0.00")


@pytest.mark.asyncio
async def test_quota_matches_success_sum_after_partial_refunds(client, seeded, session, engine):
    """多笔部分退款：配额列 == 成功退款单求和，退满置全退。"""
    order = await _pay_order(client, session, "QTA-4")

    for amount, key in (("40.00", "QTA-A"), ("30.00", "QTA-B"), ("28.00", "QTA-C")):
        body = await _refund(client, order.trade_no, amount=amount, out_refund_no=key)
        assert body["code"] == 0, body

    async with _fresh_maker(engine)() as s:
        success_sum = await refunded_amount(s, order.trade_no)

    fresh = await _refetch(engine, order.trade_no)
    assert dmoney(fresh.refunded_amount) == dmoney("98.00")
    assert success_sum == dmoney("98.00")
    assert fresh.status == 2
    assert await _balance(engine, seeded["merchant"].id) == dmoney("0.00")
