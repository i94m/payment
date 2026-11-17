"""管理端回归：订单「已退款」状态 / 支付方式中文名 / 回调日志时间与 403 语义（B02/B08/B10/B11/B14）。

对应缺陷：
- B02 订单状态 2 在列表、筛选、CSV 导出中映射为「已退款」
- B08 支付方式列表返回 icon 字段（空则前端不渲染，杜绝 4 个 404）
- B10 回调日志 created_at 统一 YYYY-MM-DD HH:MM:SS
- B11 回调日志无权限返回 HTTP 403（而非 400 + body code 403）
- B14 订单列表/详情返回支付方式显示名 type_name
"""
import re
from decimal import Decimal

import pytest
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import Merchant, NotifyLog, Order, SystemAdmin


async def _admin_headers(session, *, super_admin: bool = True, username: str = "admin"):
    session.add(SystemAdmin(
        name="管理员", username=username,
        password=hash_password("admin123"), is_super_admin=super_admin,
    ))
    await session.commit()
    admin = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == username)
    )).first()
    token = (await issue_token(guard="admin", user_id=admin.id, session=session)).access_token
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_admin_order_refund_status_and_pay_type_name(session, client, seeded):
    headers = await _admin_headers(session)
    m = seeded["merchant"]
    pt = seeded["pay_type"]

    session.add_all([
        Order(trade_no="T20261117000001", merchant_id=m.id, out_trade_no="OUT1",
              payment_type_id=pt.id, payment_type_code="alipay", subject="商品A",
              money=Decimal("10.00"), realmoney=Decimal("10.00"), status=1),
        Order(trade_no="T20261117000002", merchant_id=m.id, out_trade_no="OUT2",
              payment_type_id=pt.id, payment_type_code="alipay", subject="商品B",
              money=Decimal("10.00"), realmoney=Decimal("10.00"), status=2),
    ])
    await session.commit()

    # 列表：type_name 为支付方式显示名；状态 2 原样返回供前端映射
    resp = await client.post("/admin-api/order/list", headers=headers,
                             json={"page": 1, "per_page": 20})
    assert resp.status_code == 200, resp.json()
    items = resp.json()["data"]["items"]
    refunded = next(it for it in items if it["trade_no"] == "T20261117000002")
    assert refunded["status"] == 2
    assert refunded["type"] == "alipay"
    assert refunded["type_name"] == "支付宝"

    # 筛选已退款
    resp = await client.post("/admin-api/order/list", headers=headers,
                             json={"status": 2})
    data = resp.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["trade_no"] == "T20261117000002"

    # 详情同样带 type_name
    resp = await client.get("/admin-api/order/T20261117000002", headers=headers)
    detail = resp.json()["data"]
    assert detail["status"] == 2 and detail["type_name"] == "支付宝"

    # CSV 导出：状态列输出中文「已退款」
    resp = await client.get("/admin-api/order/export?status=2", headers=headers)
    assert resp.status_code == 200
    body = resp.content.decode("utf-8-sig")
    assert "已退款" in body and ",2," not in body


@pytest.mark.asyncio
async def test_admin_notifylog_time_format_and_403(session, client, seeded):
    headers = await _admin_headers(session)
    session.add(NotifyLog(
        kind="merchant", trade_no="T20261117000001", url="https://example.com/notify",
        request="{}", response="ok", http_status=200, attempt=1, success=True,
    ))
    await session.commit()

    resp = await client.post("/admin-api/notifylog/list", headers=headers, json={})
    assert resp.status_code == 200, resp.json()
    item = resp.json()["data"]["items"][0]
    # 与其它列表页一致：YYYY-MM-DD HH:MM:SS（不是带 T 的 ISO 串）
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", item["created_at"])

    # 无 notify_log.view 的子账号：HTTP 403（不再落成 400 + body code 403）
    limited = await _admin_headers(session, super_admin=False, username="limited")
    resp = await client.post("/admin-api/notifylog/list", headers=limited, json={})
    assert resp.status_code == 403
    assert resp.json()["code"] == 403


@pytest.mark.asyncio
async def test_admin_pay_types_expose_icon(session, client, seeded):
    headers = await _admin_headers(session)
    resp = await client.get("/admin-api/pay/types", headers=headers)
    assert resp.status_code == 200
    item = resp.json()["data"][0]
    # 未配置图标时返回空串：前端据此不渲染，避免 /assets/icon/{code}.ico 的 404
    assert item["icon"] == ""
