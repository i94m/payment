"""admin 订单列表「汇总金额」笛卡尔积回归（FREE-86 P3-27 附带修复的门禁）。

原实现（main / PR #7 基线）：

    agg_query = _build_query(body)
    select(func.coalesce(func.sum(Order.money), 0)).select_from(agg_query.subquery())

`sum(Order.money)` 的列来自 orders 实体，SQLAlchemy 会把它一并放进 FROM，实际 SQL
是 `FROM orders, (SELECT orders…) AS anon` —— 笛卡尔积：筛选命中 N 行时每条订单被
计 N 次，汇总金额放大 N 倍。列表 total 与分页都正确，只有汇总错，肉眼几乎看不出来。

实测（3 笔命中，10+20+50=80）：旧实现返回 **240.00**，修复后 80.00。

修复在 `feat/api-presentation-layer`（PR #8）：`sum(subq.c.money)` 只引用子查询列。
本用例在该分支上是 **XPASS**；合并进 main 后删掉下面的 xfail 标记即为常驻门禁。
需要立刻看真实结果时用 `pytest tests/test_admin_order_summary.py --runxfail`。
"""
from decimal import Decimal

import pytest
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import Order, SystemAdmin

# 修复未合并进 main 前为预期失败（与 test_p0_concurrency.py 同一套 xfail 工作流）
xfail_p3_27 = pytest.mark.xfail(
    reason="P3-27 未修复：admin 订单列表汇总 FROM 里同时有 orders 与子查询，金额被放大 N 倍",
    strict=False,
)


async def _admin_headers(session, username: str = "admin") -> dict:
    admin = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == username)
    )).first()
    if not admin:
        admin = SystemAdmin(name=username, username=username,
                            password=hash_password("admin123"), is_super_admin=True)
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


async def _mk_order(session, merchant_id: int, trade_no: str,
                    money: str, status: int = 1) -> Order:
    order = Order(
        trade_no=trade_no, merchant_id=merchant_id, out_trade_no=trade_no,
        subject="汇总测试", money=Decimal(money), status=status,
    )
    session.add(order)
    await session.commit()
    return order


async def _summary(client, headers, body: dict) -> tuple[int, Decimal]:
    r = await client.post("/admin-api/order/list", headers=headers, json=body)
    assert r.status_code == 200, r.text[:300]
    data = r.json()["data"]
    return data["total"], Decimal(data["summary"]["money"])


@xfail_p3_27
@pytest.mark.asyncio
async def test_admin_order_summary_is_not_multiplied_by_cartesian_product(client, seeded, session):
    mid = seeded["merchant"].id
    await _mk_order(session, mid, "26111700000000000041", "10.00", status=1)
    await _mk_order(session, mid, "26111700000000000042", "20.00", status=1)
    await _mk_order(session, mid, "26111700000000000043", "50.00", status=0)
    h = await _admin_headers(session)

    # 全量：3 笔、金额 10+20+50=80；笛卡尔积下会变成 3×80=240
    total, money = await _summary(client, h, {})
    assert total == 3
    assert money == Decimal("80.00"), f"汇总金额被放大：{money}"

    # 状态筛选命中 2 笔：10+20=30（旧实现 2×30=60）
    total, money = await _summary(client, h, {"status": "1"})
    assert total == 2
    assert money == Decimal("30.00"), f"汇总金额被放大：{money}"

    # 汇总口径是「筛选全集」，不随分页收窄（每页 1 条仍是 30）
    total, money = await _summary(client, h, {"status": "1", "page": 1, "per_page": 1})
    assert total == 2
    assert money == Decimal("30.00"), f"汇总应按筛选全集求和，实际：{money}"

    # 单笔精确筛选：只有 1 行命中时笛卡尔积不可见，顺带确认筛选仍然生效
    total, money = await _summary(client, h, {"out_trade_no": "26111700000000000042"})
    assert total == 1
    assert money == Decimal("20.00")
