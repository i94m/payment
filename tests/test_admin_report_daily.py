"""admin「总业绩统计」按天汇总（`GET /admin-api/report/daily`）。

口径必须与首页一致，否则同一笔钱在两个页面会出现两个数：
- 收款：已支付且非测试单，按 `paid_at` 归日，金额取 `realmoney`
- 扣量：`is_deducted=1`，按 `created_at` 归日，金额取 `money`
- 平台利润：`profit` + 扣量单未被支付的 `merchant_income`
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import Order, SystemAdmin


async def _admin_headers(session) -> dict:
    admin = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == "reportadmin")
    )).first()
    if not admin:
        admin = SystemAdmin(name="管理员", username="reportadmin",
                            password=hash_password("admin123"), is_super_admin=True)
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


def _mk_order(session, merchant_id: int, trade_no: str, *, money: str,
              realmoney: str | None = None, profit: str = "0.00",
              merchant_income: str | None = None, paid_at: datetime | None = None,
              created_at: datetime | None = None, is_deducted: bool = False,
              order_type: int = 0, status: int = 1) -> Order:
    o = Order(
        trade_no=trade_no, merchant_id=merchant_id, out_trade_no=trade_no,
        subject="业绩测试", money=Decimal(money),
        realmoney=Decimal(realmoney) if realmoney is not None else None,
        profit=Decimal(profit),
        merchant_income=Decimal(merchant_income) if merchant_income is not None else None,
        paid_at=paid_at, created_at=created_at, is_deducted=is_deducted,
        order_type=order_type, status=status,
    )
    session.add(o)
    return o


@pytest.mark.asyncio
async def test_daily_performance_aggregates_by_day(client, seeded, session):
    mid = seeded["merchant"].id
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday = today - timedelta(days=1)

    # 今天：一笔普通单（收款 100 / 利润 1）+ 一笔扣量单（申报 50 / 利润 0.5 + 回补 49）
    _mk_order(session, mid, "RPT-TODAY-1", money="100.00", realmoney="100.00",
              profit="1.00", merchant_income="98.00",
              paid_at=today + timedelta(hours=10), created_at=today + timedelta(hours=10))
    _mk_order(session, mid, "RPT-TODAY-2", money="50.00", realmoney="50.00",
              profit="0.50", merchant_income="49.00", is_deducted=True,
              paid_at=today + timedelta(hours=12), created_at=today + timedelta(hours=12))
    # 昨天：一笔普通单
    _mk_order(session, mid, "RPT-YESTERDAY", money="20.00", realmoney="20.00",
              profit="0.20", merchant_income="19.00",
              paid_at=yesterday + timedelta(hours=9), created_at=yesterday + timedelta(hours=9))
    # 测试单不计入
    _mk_order(session, mid, "RPT-TEST", money="999.00", realmoney="999.00",
              profit="9.00", order_type=2,
              paid_at=today + timedelta(hours=11), created_at=today + timedelta(hours=11))
    # 未支付不计入收款
    _mk_order(session, mid, "RPT-PENDING", money="70.00", status=0,
              created_at=today + timedelta(hours=13))
    await session.commit()

    h = await _admin_headers(session)
    r = await client.get("/admin-api/report/daily", headers=h, params={
        "start_date": yesterday.strftime("%Y-%m-%d"),
        "end_date": today.strftime("%Y-%m-%d"),
    })
    body = r.json()
    assert body["code"] == 0, body
    data = body["data"]
    # 区间内所有日期都补 0 行（这里 2 天，正序）
    assert [it["date"] for it in data["items"]] == [
        yesterday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"),
    ]

    y = data["items"][0]
    assert y["paid_count"] == 1
    assert y["paid_money"] == 20.0
    assert y["deduct_count"] == 0
    assert y["deduct_money"] == 0.0
    assert y["profit"] == 0.2

    t = data["items"][1]
    assert t["paid_count"] == 2
    assert t["paid_money"] == 150.0          # 100 + 50（测试单 999 不计）
    assert t["deduct_count"] == 1
    assert t["deduct_money"] == 50.0
    # 平台利润含扣量单回补的 merchant_income：1.00 + (0.50 + 49.00)
    assert t["profit"] == 50.5

    total = data["total"]
    assert total["paid_count"] == 3
    assert total["paid_money"] == 170.0
    assert total["deduct_count"] == 1
    assert total["deduct_money"] == 50.0
    assert total["profit"] == 50.7


@pytest.mark.asyncio
async def test_daily_performance_rejects_bad_ranges(client, seeded, session):
    h = await _admin_headers(session)
    today = date.today()

    r = await client.get("/admin-api/report/daily", headers=h,
                         params={"start_date": "2026-13-01", "end_date": "2026-13-02"})
    assert r.json()["code"] == -1
    assert "格式错误" in r.json()["message"]

    r = await client.get("/admin-api/report/daily", headers=h, params={
        "start_date": today.strftime("%Y-%m-%d"),
        "end_date": (today - timedelta(days=1)).strftime("%Y-%m-%d"),
    })
    assert r.json()["code"] == -1
    assert "不能早于" in r.json()["message"]

    r = await client.get("/admin-api/report/daily", headers=h, params={
        "start_date": (today - timedelta(days=91)).strftime("%Y-%m-%d"),
        "end_date": today.strftime("%Y-%m-%d"),
    })
    assert r.json()["code"] == -1
    assert "最多" in r.json()["message"]


@pytest.mark.asyncio
async def test_daily_performance_defaults_to_last_30_days(client, seeded, session):
    h = await _admin_headers(session)
    r = await client.get("/admin-api/report/daily", headers=h)
    body = r.json()
    assert body["code"] == 0, body
    assert len(body["data"]["items"]) == 30
    assert body["data"]["end_date"] == date.today().strftime("%Y-%m-%d")
