"""FREE-86 接口与后台表现层回归（P2-17/18/19/21/26、P3-27/30/31）。

覆盖点：
- 列表筛选参数非法值 → 400（不再是 500）
- 公开收款页 / 开放 API 的 NaN/Infinity 金额 → 业务错误（不再是 500）
- 收银台重复拉起复用已落库支付信息（切换方式/refresh 才重新拉起）
- 订单 CSV 导出补齐「已退款」状态文案
- admin 首页:查询条数收敛 + /index 兼容别名不重复挂载
- admin 路由 fail-closed：匿名 401；通知中心新增 message.view 权限键
"""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import event
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import AdminMessage, Order, SystemAdmin
from tests.conftest import pay_code, signed_params


async def _admin_headers(session, username: str = "admin", super_admin: bool = True) -> dict:
    admin = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == username)
    )).first()
    if not admin:
        admin = SystemAdmin(name=username, username=username,
                            password=hash_password("admin123"),
                            is_super_admin=super_admin)
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


def _merchant_headers(token) -> dict:
    return {"Authorization": f"Bearer {token.access_token}"}


async def _merchant_header(session, merchant_id: int) -> dict:
    token = await issue_token(guard="merchant", user_id=merchant_id, session=session)
    return _merchant_headers(token)


async def _mk_order(session, merchant_id: int, trade_no: str, **kw) -> Order:
    now = datetime.now()
    money = kw.pop("money", Decimal("10.00"))
    order = Order(
        trade_no=trade_no, merchant_id=merchant_id, out_trade_no=trade_no,
        subject="测试", money=money, status=kw.pop("status", 1),
        paid_at=kw.pop("paid_at", now), created_at=kw.pop("created_at", now),
        **kw,
    )
    session.add(order)
    await session.commit()
    return order


# ------------------------------------------------------------
# P2-19 列表筛选参数非法值 → 400
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_bad_list_filters_return_400(client, seeded, session):
    """`?merchant_id=abc` 之类不再抛 500，而是 400 + 具体字段。"""
    h = await _admin_headers(session)

    r = await client.post("/admin-api/order/list", headers=h, json={"merchant_id": "abc"})
    assert r.status_code == 400, r.text[:200]
    assert r.json()["code"] == 400
    assert "merchant_id" in r.json()["message"]

    r = await client.post("/admin-api/order/list", headers=h, json={"status": "1.5"})
    assert r.status_code == 400
    assert "status" in r.json()["message"]

    r = await client.post("/admin-api/record/list", headers=h, json={"merchant_id": "x"})
    assert r.status_code == 400
    assert "merchant_id" in r.json()["message"]

    r = await client.post("/admin-api/notifylog/list", headers=h, json={"success": "maybe"})
    assert r.status_code == 400
    assert "success" in r.json()["message"]

    # 合法值仍然正常
    r = await client.post("/admin-api/order/list", headers=h, json={"merchant_id": "1001"})
    assert r.status_code == 200 and r.json()["code"] == 0

    # 列表汇总金额按筛选结果求和（回归：曾因 FROM 里同时出现 orders 与子查询而翻倍）
    mid = seeded["merchant"].id
    await _mk_order(session, mid, "26111700000000000031", money=Decimal("10.00"))
    await _mk_order(session, mid, "26111700000000000032", money=Decimal("20.00"))
    r = await client.post("/admin-api/order/list", headers=h, json={"merchant_id": str(mid)})
    body = r.json()["data"]
    assert body["total"] == 2
    assert Decimal(body["summary"]["money"]) == Decimal("30.00")

    # 商户端同样收敛
    mh = await _merchant_header(session, seeded["merchant"].id)
    r = await client.post("/merchant-api/order/list", headers=mh, json={"status": "abc"})
    assert r.status_code == 400
    assert "status" in r.json()["message"]


# ------------------------------------------------------------
# P2-18 NaN / Infinity 金额
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_paypage_rejects_nan_and_infinity(client, session):
    token = await pay_code(session)
    for money in ("NaN", "Infinity", "-Infinity"):
        r = await client.post("/paypage/order", json={"token": token, "money": money})
        assert r.status_code in (200, 400), r.text[:200]  # 业务错误包络，不是 500
        body = r.json()
        assert body["code"] != 0
        assert body["message"] == "金额格式错误"


@pytest.mark.asyncio
async def test_openapi_rejects_nan_money(client, seeded):
    params = signed_params({
        "pid": "1001", "type": "alipay", "out_trade_no": "NAN-1",
        "notify_url": "https://merchant.example/notify",
        "name": "NaN", "money": "NaN",
    })
    r = await client.post("/api/orders", json=params)
    assert r.status_code == 400, r.text[:200]
    assert r.json()["code"] == -1
    assert "金额" in r.json()["message"]


@pytest.mark.asyncio
async def test_openapi_refund_rejects_infinity_amount(client, seeded, session):
    order = await _mk_order(session, seeded["merchant"].id, "26111700000000000001",
                            merchant_income=Decimal("10.00"), realmoney=Decimal("10.00"))
    params = signed_params({
        "pid": "1001", "refund_amount": "Infinity", "out_refund_no": "R-INF",
    })
    r = await client.post(f"/api/orders/{order.trade_no}/refund", json=params)
    assert r.status_code == 400, r.text[:200]
    assert "退款金额" in r.json()["message"]


# ------------------------------------------------------------
# P2-26 收银台重复拉起复用落库支付信息
# ------------------------------------------------------------

async def _pull_via_api(client, out_trade_no: str) -> Order:
    params = signed_params({
        "pid": "1001", "type": "alipay", "out_trade_no": out_trade_no,
        "notify_url": "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "收银台复用", "money": "10.00",
    })
    data = (await client.post("/api/orders", json=params)).json()
    assert data["code"] == 0, data
    trade_no = data["data"]["trade_no"]

    from app.core.database import get_db
    from main import app as _app
    from app.services.order import get_order_by_trade_no

    override = _app.dependency_overrides[get_db]
    async for s in override():
        return await get_order_by_trade_no(s, trade_no)
    raise AssertionError("order not found")


async def _set_extra(session, order: Order, extra: dict) -> None:
    fresh = await session.get(Order, order.id)
    fresh.extra = extra
    session.add(fresh)
    await session.commit()


@pytest.mark.asyncio
async def test_cashier_pay_reuses_stored_pay_info(client, seeded, session):
    order = await _pull_via_api(client, "CASH-REUSE")
    marked = "https://upstream.example/pay/MARKED"
    await _set_extra(session, order, {"pay_type": "jump", "pay_info": marked})

    # 同一支付方式：直接复用落库信息（重新拉起会拿到 mock 上游的 abc123 地址）
    r = await client.post("/cashier/pay", json={"trade_no": order.trade_no, "type": "alipay"})
    body = r.json()
    assert body["code"] == 0, body
    assert body["data"]["pay_info"] == marked

    # refresh=true：强制重新拉起上游
    r = await client.post("/cashier/pay",
                          json={"trade_no": order.trade_no, "type": "alipay", "refresh": True})
    body = r.json()
    assert body["code"] == 0, body
    assert body["data"]["pay_info"] != marked


# ------------------------------------------------------------
# P2-21 CSV 导出状态文案
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_order_export_status_text_includes_refunded(client, seeded, session):
    await _mk_order(session, seeded["merchant"].id, "26111700000000000011", status=2)
    await _mk_order(session, seeded["merchant"].id, "26111700000000000012", status=3)
    h = await _admin_headers(session)
    r = await client.get("/admin-api/order/export", headers=h)
    assert r.status_code == 200
    text = r.content.decode("utf-8-sig")
    assert "已退款" in text
    assert "已过期" in text


# ------------------------------------------------------------
# P3-27 首页查询收敛
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_dashboard_query_budget(client, seeded, session, engine):
    """首页总览固定查询数（趋势图单条 GROUP BY，不再按天 ×2 往返）。"""
    h = await _admin_headers(session)
    await _mk_order(session, seeded["merchant"].id, "26111700000000000021")
    await _mk_order(session, seeded["merchant"].id, "26111700000000000022",
                    status=0, paid_at=None, created_at=datetime.now() - timedelta(minutes=5))

    counter = {"n": 0}

    def _before(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            counter["n"] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", _before)
    try:
        r = await client.get("/admin-api/dashboard", headers=h, params={"days": 30})
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _before)
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data["trend"]) == 30
    assert data["count1"] >= 2
    # 30 天趋势若按旧实现会是 ~60 条，这里连同鉴权/映射表总共也不超过 15 条
    assert counter["n"] <= 15, f"dashboard 查询数 {counter['n']} 超预算"


# ------------------------------------------------------------
# P3-30 / P3-31 /index 别名与 admin fail-closed
# ------------------------------------------------------------

@pytest.mark.asyncio
async def test_index_alias_single_mount(client, seeded, session):
    h = await _admin_headers(session)
    r = await client.get("/admin-api/index", headers=h)
    assert r.status_code == 200
    assert "count1" in r.json()["data"]

    # /dashboard/index 与 /index/* 整组重复挂载已移除
    assert (await client.get("/admin-api/dashboard/index", headers=h)).status_code == 404
    assert (await client.post("/admin-api/index/payroute", headers=h,
                              json={"type_id": 1})).status_code == 404


@pytest.mark.asyncio
async def test_admin_routes_fail_closed(client, seeded, session):
    # 公开端点保持公开
    assert (await client.get("/admin-api/captcha")).status_code == 200
    assert (await client.get("/admin-api/site-info")).status_code == 200

    # 其余 admin 端点匿名一律 401（新增端点漏挂权限也不会匿名可访问）
    for path in ("/admin-api/common/userInfo", "/admin-api/storage/files",
                 "/admin-api/broadcasting/auth", "/admin-api/message/unread-count"):
        method = client.post if path.endswith("broadcasting/auth") else client.get
        r = await method(path)
        assert r.status_code == 401, (path, r.status_code)


@pytest.mark.asyncio
async def test_message_center_requires_permission(client, seeded, session):
    session.add(AdminMessage(title="t", content="c", biz_type="alert"))
    await session.commit()

    sub = await _admin_headers(session, username="sub-no-perm", super_admin=False)
    r = await client.get("/admin-api/message/unread-count", headers=sub)
    assert r.status_code == 403
    assert r.json()["code"] == 403

    # 超管放行
    h = await _admin_headers(session)
    r = await client.get("/admin-api/message/unread-count", headers=h)
    assert r.status_code == 200 and r.json()["data"]["count"] == 1
