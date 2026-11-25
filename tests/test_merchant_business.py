"""商户管理业务端到端测试：

1. pay_status：0=关闭 / 2=待审核 阻断下单
2. random_ratelimit：同一付款人 IP 每分钟建单上限
3. 随机金额：min==max 固定实付；min<max 实付落在区间内（申报金额不变）
4. settle_status：关闭时阻断结算申请
5. 管理端：删除保护（有余额拒绝）、批量随机金额（跳过 fixed）、
   批量随机金额（跳过 fixed）、商户状态开关切换
"""
import pytest
from decimal import Decimal
from sqlmodel import col, select

from app.models import Merchant, MerchantRecord, SystemAdmin
from app.services.balance import dmoney
from app.services.order import get_order_by_trade_no
from app.services.settle import apply_settlement
from tests.test_pay_flow import _create_order  # noqa: F401


async def _get_merchant(session) -> Merchant:
    return (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()


async def _admin_headers(session) -> dict:
    admin = SystemAdmin(username="rootadmin", name="root", password="x", is_super_admin=True)
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    from app.core.security import issue_token
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


# ---- pay_status 支付开关 ----

@pytest.mark.asyncio
async def test_pay_status_closed_blocks_order(client, seeded, session):
    m = await _get_merchant(session)
    m.pay_status = 0
    session.add(m)
    await session.commit()

    data, _ = await _create_order(client, out_trade_no="PAYOFF1")
    assert data["code"] == -1
    assert "支付权限已关闭" in data["message"]


@pytest.mark.asyncio
async def test_pay_status_pending_blocks_order(client, seeded, session):
    m = await _get_merchant(session)
    m.pay_status = 2
    session.add(m)
    await session.commit()

    data, _ = await _create_order(client, out_trade_no="PAYREV1")
    assert data["code"] == -1
    assert "待审核" in data["message"]


# ---- 限制拉单 ----

@pytest.mark.asyncio
async def test_ratelimit_blocks_same_ip(client, seeded, session):
    m = await _get_merchant(session)
    m.random_ratelimit = 2
    session.add(m)
    await session.commit()

    data1, _ = await _create_order(client, out_trade_no="RL1", client_ip="1.2.3.4")
    data2, _ = await _create_order(client, out_trade_no="RL2", client_ip="1.2.3.4")
    assert data1["code"] == 0 and data2["code"] == 0

    data3, _ = await _create_order(client, out_trade_no="RL3", client_ip="1.2.3.4")
    assert data3["code"] == -1
    assert "频繁" in data3["message"]

    # 其他 IP 不受影响
    data4, _ = await _create_order(client, out_trade_no="RL4", client_ip="5.6.7.8")
    assert data4["code"] == 0


# ---- 随机金额 ----

@pytest.mark.asyncio
async def test_random_fixed_amount(client, seeded, session):
    m = await _get_merchant(session)
    m.random_enable = True
    m.random_min = dmoney("5")
    m.random_max = dmoney("5")
    session.add(m)
    await session.commit()

    data, order = await _create_order(client, out_trade_no="RAND1", money="100.00")
    assert data["code"] == 0
    assert order.realmoney == dmoney("5")
    assert order.money == dmoney("100")  # 申报金额不变
    # 到账按实付计算：5 * (100-2)% = 4.90
    assert order.merchant_income == dmoney("4.90")


@pytest.mark.asyncio
async def test_random_range_amount(client, seeded, session):
    m = await _get_merchant(session)
    m.random_enable = True
    m.random_min = dmoney("3")
    m.random_max = dmoney("10")
    session.add(m)
    await session.commit()

    data, order = await _create_order(client, out_trade_no="RAND2", money="100.00")
    assert data["code"] == 0
    assert dmoney("3") <= order.realmoney <= dmoney("10")


# ---- settle_status 结算开关 ----

@pytest.mark.asyncio
async def test_settle_status_blocks_apply(client, seeded, session):
    m = await _get_merchant(session)
    m.settle_status = 0
    session.add(m)
    await session.commit()

    settle, error = await apply_settlement(session, m, "10")
    assert settle is None
    assert "结算功能未开启" in error


# ---- 管理端接口 ----

@pytest.mark.asyncio
async def test_admin_delete_protection_and_success(client, seeded, session):
    headers = await _admin_headers(session)
    m = await _get_merchant(session)

    # 有余额：拒绝
    m.balance = dmoney("9.99")
    session.add(m)
    await session.commit()
    resp = await client.delete(f"/admin-api/merchant/{m.id}", headers=headers)
    assert resp.json()["code"] == -1
    assert "余额" in resp.json()["message"]

    # 清零后：可删除
    m.balance = dmoney("0")
    session.add(m)
    await session.commit()
    merchant_id = m.id  # expire 前取出主键，避免 expire 后触发同步刷新
    resp = await client.delete(f"/admin-api/merchant/{m.id}", headers=headers)
    assert resp.json()["code"] == 0
    session.expire_all()  # 清除 identity map，强制读库
    gone = (await session.exec(select(Merchant).where(col(Merchant.id) == merchant_id))).first()
    assert gone is None


@pytest.mark.asyncio
async def test_admin_batch_random_skips_fixed(client, seeded, session):
    headers = await _admin_headers(session)
    m = await _get_merchant(session)
    m.fixed = True
    session.add(m)
    await session.commit()

    resp = await client.post(
        "/admin-api/merchant/random/set-all",
        json={"random_enable": True, "random_min": "2", "random_max": "8"}, headers=headers)
    assert resp.json()["code"] == 0
    await session.refresh(m)
    assert m.random_enable is False  # fixed 跳过

    # 单个设置不受 fixed 限制
    resp = await client.post(
        f"/admin-api/merchant/{m.id}/random",
        json={"random_enable": True, "random_min": "2", "random_max": "8"}, headers=headers)
    assert resp.json()["code"] == 0
    await session.refresh(m)
    assert m.random_enable is True
    assert m.random_min == dmoney("2")


@pytest.mark.asyncio
async def test_admin_update_amount_fields_reject_bad_input(client, seeded, session):
    """非法金额应返回业务错误而非 500。"""
    headers = await _admin_headers(session)
    m = await _get_merchant(session)
    resp = await client.put(
        f"/admin-api/merchant/{m.id}",
        json={"rate": "abc"}, headers=headers)
    assert resp.json()["code"] == -1
    assert "金额格式错误" in resp.json()["message"]


@pytest.mark.asyncio
async def test_admin_set_status_and_ratelimit(client, seeded, session):
    headers = await _admin_headers(session)
    m = await _get_merchant(session)

    # 状态开关切换：pay → 待审核
    resp = await client.post(
        f"/admin-api/merchant/{m.id}/status", json={"type": "pay", "status": 2}, headers=headers)
    assert resp.json()["code"] == 0
    await session.refresh(m)
    assert m.pay_status == 2

    # fixed 开关
    resp = await client.post(
        f"/admin-api/merchant/{m.id}/status", json={"type": "fixed", "status": 1}, headers=headers)
    assert resp.json()["code"] == 0
    await session.refresh(m)
    assert m.fixed is True

    # 限制拉单
    resp = await client.post(
        f"/admin-api/merchant/{m.id}/ratelimit", json={"random_ratelimit": 5}, headers=headers)
    assert resp.json()["code"] == 0
    await session.refresh(m)
    assert m.random_ratelimit == 5


# ---- 仅整数金额开关（random_integer） ----

@pytest.mark.asyncio
async def test_random_money_integer_flag(seeded, session):
    """random_integer 开启只随机整数；关闭时按分随机（支持两位小数）。"""
    from app.services.merchant import random_money

    m = await _get_merchant(session)
    m.random_enable = True
    m.random_min = dmoney("2")
    m.random_max = dmoney("2.50")
    m.random_integer = False
    seen = {random_money(m) for _ in range(200)}
    assert all(dmoney("2") <= v <= dmoney("2.50") for v in seen)
    assert all(v == v.quantize(Decimal("0.01")) for v in seen)
    assert any(v != v.to_integral_value() for v in seen)  # 关闭时能出现两位小数

    m.random_integer = True
    m.random_min = dmoney("2")
    m.random_max = dmoney("5")
    values = {random_money(m) for _ in range(200)}
    assert values <= {dmoney(n) for n in range(2, 6)}
    assert len(values) > 1  # 区间内应随机到多个整数


@pytest.mark.asyncio
async def test_admin_random_integer_validation(client, seeded, session):
    headers = await _admin_headers(session)
    m = await _get_merchant(session)

    # 开启仅整数金额后，小数区间被拒绝
    resp = await client.post(
        f"/admin-api/merchant/{m.id}/random",
        json={"random_enable": True, "random_min": "1.5", "random_max": "8",
              "random_integer": True}, headers=headers)
    assert resp.json()["code"] == -1
    assert "整数" in resp.json()["message"]

    # 整数区间 + 开关落库
    resp = await client.post(
        f"/admin-api/merchant/{m.id}/random",
        json={"random_enable": True, "random_min": "2", "random_max": "8",
              "random_integer": True}, headers=headers)
    assert resp.json()["code"] == 0
    await session.refresh(m)
    assert m.random_integer is True
    assert m.random_min == dmoney("2")

    # 关闭开关后允许两位小数
    resp = await client.post(
        f"/admin-api/merchant/{m.id}/random",
        json={"random_enable": True, "random_min": "1.55", "random_max": "8.20",
              "random_integer": False}, headers=headers)
    assert resp.json()["code"] == 0
    await session.refresh(m)
    assert m.random_integer is False
    assert m.random_min == dmoney("1.55")


@pytest.mark.asyncio
async def test_order_realmoney_integer_only(client, seeded, session):
    m = await _get_merchant(session)
    m.random_enable = True
    m.random_min = dmoney("2")
    m.random_max = dmoney("6")
    m.random_integer = True
    session.add(m)
    await session.commit()

    data, order = await _create_order(client, out_trade_no="RANDINT", money="100.00")
    assert data["code"] == 0
    assert dmoney("2") <= order.realmoney <= dmoney("6")
    assert order.realmoney == order.realmoney.to_integral_value()


@pytest.mark.asyncio
async def test_admin_reset_password_with_plain_input(client, seeded, session):
    """重置密码：管理员直接输入新密码（不再只能生成随机密码）。"""
    from app.core.security import verify_password

    headers = await _admin_headers(session)
    m = await _get_merchant(session)

    # 过短拒绝
    resp = await client.post(
        f"/admin-api/merchant/{m.id}/resetpwd", json={"password": "123"},
        headers=headers)
    assert resp.json()["code"] == -1

    resp = await client.post(
        f"/admin-api/merchant/{m.id}/resetpwd", json={"password": "newpass123"},
        headers=headers)
    assert resp.json()["code"] == 0
    await session.refresh(m)
    assert verify_password("newpass123", m.password)

    # 不传密码仍兼容：生成随机密码
    resp = await client.post(
        f"/admin-api/merchant/{m.id}/resetpwd", json={}, headers=headers)
    assert resp.json()["code"] == 0
    assert len(resp.json()["data"]["password"]) == 10


@pytest.mark.asyncio
async def test_admin_merchant_rate_field(client, seeded, session):
    """商户专属费率：可设置、可清除（空串回落编码/通道）、省略字段不改动、越界拒绝。"""
    headers = await _admin_headers(session)

    # 新增商户直接带费率
    resp = await client.post("/admin-api/merchant", headers=headers, json={
        "username": "rate_merchant", "password": "123456", "rate": "1.50"})
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["rate"] == "1.50"
    mid = body["data"]["id"]

    # 改费率
    resp = await client.put(f"/admin-api/merchant/{mid}", json={"rate": "2.35"}, headers=headers)
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["rate"] == "2.35"

    # 其他弹窗只传状态时不能把费率冲掉（不传 rate = 不改）
    resp = await client.put(f"/admin-api/merchant/{mid}", json={"status": 1}, headers=headers)
    assert resp.json()["data"]["rate"] == "2.35"

    # 空串 = 清除专属费率：回落编码费率 / 通道费率，而不是 0%
    resp = await client.put(f"/admin-api/merchant/{mid}", json={"rate": ""}, headers=headers)
    assert resp.json()["code"] == 0
    assert resp.json()["data"]["rate"] == ""
    m = await session.get(Merchant, mid)
    await session.refresh(m)
    assert m.rate is None

    # 越界 / 非法值拒绝
    for bad in ("101", "-0.01", "abc"):
        resp = await client.put(f"/admin-api/merchant/{mid}", json={"rate": bad}, headers=headers)
        assert resp.json()["code"] == -1, bad
    await session.refresh(m)
    assert m.rate is None  # 被拒的写入不落库

    # 新增时越界同样拒绝（不能被 pid 冲突重试吞成「创建冲突」）
    resp = await client.post("/admin-api/merchant", headers=headers, json={
        "username": "rate_bad", "password": "123456", "rate": "150"})
    assert resp.json()["code"] == -1
    assert "0~100" in resp.json()["message"]
