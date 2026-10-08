"""admin 资金明细：多条件查询 / 商户号过滤 / 汇总 / 导出。"""
from decimal import Decimal

import pytest

from app.core.security import hash_password
from app.models import Merchant, MerchantRecord, SystemAdmin


@pytest.mark.asyncio
async def test_admin_records_list_summary_export(session, client, seeded):
    session.add(SystemAdmin(
        name="管理员", username="admin",
        password=hash_password("admin123"), is_super_admin=True,
    ))
    m2 = Merchant(pid=1002, username="mch2", password=hash_password("test123456"),
                  status=1)
    session.add(m2)
    await session.commit()

    from sqlmodel import select
    from app.core.security import issue_token

    admin = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == "admin")
    )).first()
    token = (await issue_token(guard="admin", user_id=admin.id, session=session)).access_token
    headers = {"Authorization": f"Bearer {token}"}

    m1 = seeded["merchant"]
    rows = [
        MerchantRecord(merchant_id=m1.id, biz_type="order_income", amount=Decimal("100.00"),
                       balance_before=Decimal("0"), balance_after=Decimal("100.00"),
                       order_trade_no="T2026093001"),
        MerchantRecord(merchant_id=m1.id, biz_type="settle", amount=Decimal("-30.00"),
                       balance_before=Decimal("100.00"), balance_after=Decimal("70.00")),
        MerchantRecord(merchant_id=m2.id, biz_type="recharge", amount=Decimal("50.00"),
                       balance_before=Decimal("0"), balance_after=Decimal("50.00")),
    ]
    for r in rows:
        session.add(r)
    await session.commit()

    # 未登录拒绝
    resp = await client.post("/admin-api/record/list", json={})
    assert resp.status_code == 401

    # 全量：带商户号/用户名/类型中文名 + 汇总（增加/减少/总计）
    resp = await client.post("/admin-api/record/list", headers=headers,
                             json={"page": 1, "per_page": 20})
    assert resp.status_code == 200, resp.json()
    data = resp.json()["data"]
    assert data["total"] == 3
    assert data["summary"] == {"inc": "150.00", "dec": "-30.00", "total": "120.00"}
    first = next(it for it in data["items"] if it["biz_type"] == "order_income")
    assert first["merchant_pid"] == m1.pid
    assert first["merchant_username"] == "testmch"
    assert first["biz_type_name"] == "订单收入"

    # 商户号过滤（商户列表「资金明细」快捷跳转的默认条件）
    resp = await client.post("/admin-api/record/list", headers=headers,
                             json={"merchant_id": m1.id})
    data = resp.json()["data"]
    assert data["total"] == 2
    assert data["summary"] == {"inc": "100.00", "dec": "-30.00", "total": "70.00"}
    assert all(it["merchant_id"] == m1.id for it in data["items"])

    # 类型过滤 + 订单号搜索
    resp = await client.post("/admin-api/record/list", headers=headers,
                             json={"biz_type": "settle"})
    assert resp.json()["data"]["total"] == 1
    resp = await client.post("/admin-api/record/list", headers=headers,
                             json={"search_field": "order_trade_no", "keyword": "20260930"})
    data = resp.json()["data"]
    assert data["total"] == 1 and data["items"][0]["order_trade_no"] == "T2026093001"

    # 变更金额精确搜索
    resp = await client.post("/admin-api/record/list", headers=headers,
                             json={"search_field": "amount", "keyword": "-30.00"})
    assert resp.json()["data"]["total"] == 1

    # 时间范围：远古区间应查不到
    resp = await client.post("/admin-api/record/list", headers=headers,
                             json={"addtime": "2000-01-01,2000-01-02"})
    assert resp.json()["data"]["total"] == 0

    # 导出：与列表同条件（merchant_id 过滤）
    resp = await client.get("/admin-api/record/export", headers=headers,
                            params={"merchant_id": m1.id})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    body = resp.text
    assert "商户号" in body and "操作类型" in body
    assert "100.00" in body and "-30.00" in body
    assert "50.00" not in body  # m2 的流水不应出现在导出里
