"""代理面板与商户端代理接口：登录 / SSO / 守卫隔离 / 列表与业绩口径。"""

import itertools
from datetime import datetime, timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest

from app.services import paytoken

from main import app

from app.core.cache import get_cache
from app.core.security import hash_password, issue_token
from app.models import Agent, Merchant, Order

_trade_seq = itertools.count()


def _trade_no() -> str:
    # 1 + 10 + 6 = 17 位，计数器保证同一用例内唯一
    return f"T{datetime.now().strftime('%y%m%d%H%M')}{next(_trade_seq):06d}"


def _money(v) -> Decimal:
    """聚合金额断言统一按分位比较（sqlite 聚合可能返回 30.0/30.00 等形态）。"""
    return Decimal(str(v)).quantize(Decimal("0.01"))


async def _mk_agent(session, merchant_id: int, name: str, *, password: str = "agent123",
                    status: int = 1, remark: str | None = None) -> Agent:
    a = Agent(merchant_id=merchant_id, name=name, password=hash_password(password),
              status=status, remark=remark)
    session.add(a)
    await session.commit()
    await session.refresh(a)
    return a


async def _mk_order(session, merchant_id: int, *, agent_id: int = 0, status: int = 1,
                    order_type: int = 0, deducted: bool = False, money: str = "10.00",
                    paid: timedelta | None = timedelta(minutes=1)) -> Order:
    now = datetime.now()
    o = Order(
        trade_no=_trade_no(),
        merchant_id=merchant_id,
        out_trade_no=f"O{datetime.now().strftime('%y%m%d%H%M')}{next(_trade_seq):06d}",
        payment_type_code="alipay",
        subject="测试",
        money=Decimal(money),
        realmoney=Decimal(money),
        status=status,
        order_type=order_type,
        is_deducted=deducted,
        created_at=now,
        expire_at=now + timedelta(minutes=30),
        paid_at=(now - paid) if (paid is not None and status == 1) else None,
        agent_id=agent_id,
    )
    session.add(o)
    await session.commit()
    return o


def _mch_headers(token) -> dict:
    return {"Authorization": f"Bearer {token.access_token}"}


# ============================================================
# 代理端登录 / 资料
# ============================================================

@pytest.mark.asyncio
async def test_agent_login_and_profile(client, seeded, session):
    m = seeded["merchant"]
    a = await _mk_agent(session, m.id, "agent01", remark="一级代理")

    r = await client.post("/agent-api/auth/login", json={
        "username": "agent01", "password": "agent123", "captcha_code": "0000"})
    body = r.json()
    assert body["code"] == 0, body
    token = body["data"]["access_token"]
    assert token

    # 登录成功后更新 last_login_at，并可在 profile 中取到归属商户与专属链接
    prof = (await client.get("/agent-api/auth/profile",
                             headers={"Authorization": f"Bearer {token}"})).json()
    assert prof["code"] == 0, prof
    assert prof["data"]["name"] == "agent01"
    assert prof["data"]["merchant_name"] == m.username
    pt = await paytoken.resolve(session, prof["data"]["link"][3:])
    assert (pt.merchant_id, pt.agent_id) == (m.id, a.id)
    assert prof["data"]["last_login_at"]

    # 密码错误 / 被禁用 均拒绝
    r2 = await client.post("/agent-api/auth/login", json={
        "username": "agent01", "password": "wrong-pwd", "captcha_code": "0000"})
    assert r2.json()["code"] != 0

    a2 = await _mk_agent(session, m.id, "agent-off", status=0)
    r3 = await client.post("/agent-api/auth/login", json={
        "username": "agent-off", "password": "agent123", "captcha_code": "0000"})
    assert r3.json()["code"] != 0


@pytest.mark.asyncio
async def test_agent_name_global_unique(client, seeded, session):
    """代理名称全局唯一：跨商户不可重名，登录按名称精确匹配唯一账号。"""
    m1 = seeded["merchant"]
    m2 = Merchant(pid=1002, username="testmch2", password="x", status=1)
    session.add(m2)
    await session.commit()
    await session.refresh(m2)

    a1 = await _mk_agent(session, m1.id, "same-name", password="pass-111")

    # 他户建同名代理：拒绝（全局唯一）
    token2 = await issue_token(guard="merchant", user_id=m2.id, session=session)
    r = (await client.post("/merchant-api/agent", headers=_mch_headers(token2), json={
        "name": "same-name", "password": "pass-222"})).json()
    assert r["code"] != 0

    # 登录精确命中唯一代理
    r1 = (await client.post("/agent-api/auth/login", json={
        "username": "same-name", "password": "pass-111", "captcha_code": "0000"})).json()
    assert r1["code"] == 0, r1
    prof = (await client.get("/agent-api/auth/profile",
                             headers={"Authorization": f"Bearer {r1['data']['access_token']}"})).json()
    assert prof["data"]["id"] == a1.id
    assert prof["data"]["merchant_name"] == m1.username


# ============================================================
# 守卫隔离：merchant token ↔ agent-api / agent token ↔ merchant-api
# ============================================================

@pytest.mark.asyncio
async def test_guard_isolation(client, seeded, session):
    m = seeded["merchant"]
    a = await _mk_agent(session, m.id, "iso-agent")

    mch_token = await issue_token(guard="merchant", user_id=m.id, session=session)
    agent_token = await issue_token(guard="agent", user_id=a.id, session=session)

    r1 = await client.get("/agent-api/order/list", headers=_mch_headers(mch_token))
    assert r1.status_code == 401
    r2 = await client.get("/agent-api/dashboard", headers=_mch_headers(mch_token))
    assert r2.status_code == 401

    r3 = await client.get("/merchant-api/auth/profile", headers=_mch_headers(agent_token))
    assert r3.status_code == 401

    # 正确 guard 正常通过
    r4 = await client.get("/agent-api/dashboard", headers=_mch_headers(agent_token))
    assert r4.status_code == 200
    assert r4.json()["code"] == 0


# ============================================================
# 商户 SSO 代登录代理面板
# ============================================================

class MemoryCache:
    """跨 HTTP 请求共享的最小测试缓存。"""

    def __init__(self):
        self.items = {}

    async def get(self, key: str):
        return self.items.get(key)

    async def set(self, key: str, value, **kwargs):
        self.items[key] = value

    async def delete(self, key: str):
        self.items.pop(key, None)


@pytest.mark.asyncio
async def test_merchant_sso_into_agent_panel(client, seeded, session):
    m = seeded["merchant"]
    a = await _mk_agent(session, m.id, "sso-agent")
    mch_token = await issue_token(guard="merchant", user_id=m.id, session=session)

    cache = MemoryCache()

    async def _shared_cache():
        yield cache

    async def _disabled_cache():
        yield MemoryCache()

    app.dependency_overrides[get_cache] = _shared_cache
    try:
        r = await client.post(f"/merchant-api/agent/{a.id}/sso", headers=_mch_headers(mch_token))
        body = r.json()
        assert body["code"] == 0, body
        # 落地地址 /agent/#/sso-login?token=...（hash fragment 携参）
        fragment_query = urlparse(body["data"]["url"]).fragment.split("?", 1)[1]
        sso_token = parse_qs(fragment_query)["token"][0]
        assert body["data"]["url"].startswith("/agent/")

        sso1 = (await client.post("/agent-api/auth/sso", json={"token": sso_token})).json()
        assert sso1["code"] == 0, sso1
        prof = (await client.get("/agent-api/auth/profile",
                                 headers={"Authorization": f"Bearer {sso1['data']['access_token']}"})).json()
        assert prof["data"]["id"] == a.id

        # 一次性凭证：重复使用失效
        sso2 = (await client.post("/agent-api/auth/sso", json={"token": sso_token})).json()
        assert sso2["code"] != 0
    finally:
        app.dependency_overrides[get_cache] = _disabled_cache


@pytest.mark.asyncio
async def test_sso_ownership_and_disabled_agent(client, seeded, session):
    m = seeded["merchant"]
    other = Merchant(pid=1003, username="testmch3", password="x", status=1)
    session.add(other)
    await session.commit()
    await session.refresh(other)

    foreign = await _mk_agent(session, other.id, "foreign-agent")
    disabled = await _mk_agent(session, m.id, "disabled-agent", status=0)
    mch_token = await issue_token(guard="merchant", user_id=m.id, session=session)

    # 他人代理不可 SSO
    r1 = (await client.post(f"/merchant-api/agent/{foreign.id}/sso",
                            headers=_mch_headers(mch_token))).json()
    assert r1["code"] == 404
    # 被禁用的自家代理不可 SSO
    r2 = (await client.post(f"/merchant-api/agent/{disabled.id}/sso",
                            headers=_mch_headers(mch_token))).json()
    assert r2["code"] != 0


# ============================================================
# 商户端代理列表：分页 / 搜索 / 隔离 / CRUD
# ============================================================

@pytest.mark.asyncio
async def test_agent_list_pagination_kw_and_isolation(client, seeded, session):
    m = seeded["merchant"]
    other = Merchant(pid=1004, username="testmch4", password="x", status=1)
    session.add(other)
    await session.commit()
    await session.refresh(other)

    await _mk_agent(session, m.id, "alpha", remark="华东")
    await _mk_agent(session, m.id, "beta", remark="华南")
    await _mk_agent(session, m.id, "gamma")
    await _mk_agent(session, other.id, "alpha-2")  # 他户代理，不应出现

    mch_token = await issue_token(guard="merchant", user_id=m.id, session=session)
    h = _mch_headers(mch_token)

    r1 = (await client.get("/merchant-api/agent/list", headers=h)).json()
    assert r1["code"] == 0, r1
    assert r1["data"]["total"] == 3
    assert {i["name"] for i in r1["data"]["items"]} == {"alpha", "beta", "gamma"}
    for i in r1["data"]["items"]:
        pt = await paytoken.resolve(session, i["link"][3:])
        assert (pt.merchant_id, pt.agent_id) == (m.id, i["id"])

    # kw：命中名称 / 命中备注
    r2 = (await client.get("/merchant-api/agent/list", params={"kw": "alp"}, headers=h)).json()
    assert r2["data"]["total"] == 1 and r2["data"]["items"][0]["name"] == "alpha"
    r3 = (await client.get("/merchant-api/agent/list", params={"kw": "华东"}, headers=h)).json()
    assert r3["data"]["total"] == 1 and r3["data"]["items"][0]["name"] == "alpha"

    # 分页
    r4 = (await client.get("/merchant-api/agent/list",
                           params={"page": 2, "per_page": 2}, headers=h)).json()
    assert r4["data"]["total"] == 3 and len(r4["data"]["items"]) == 1

    # 商户隔离：他户只见自己的 alpha-2
    other_token = await issue_token(guard="merchant", user_id=other.id, session=session)
    r5 = (await client.get("/merchant-api/agent/list", headers=_mch_headers(other_token))).json()
    assert r5["data"]["total"] == 1 and r5["data"]["items"][0]["name"] == "alpha-2"


@pytest.mark.asyncio
async def test_agent_crud_rename_and_password(client, seeded, session):
    m = seeded["merchant"]
    mch_token = await issue_token(guard="merchant", user_id=m.id, session=session)
    h = _mch_headers(mch_token)

    r1 = (await client.post("/merchant-api/agent", headers=h, json={
        "name": "crud-agent", "password": "123456", "remark": "初始"})).json()
    assert r1["code"] == 0, r1
    aid = r1["data"]["id"]

    # 同名拒绝
    r2 = (await client.post("/merchant-api/agent", headers=h, json={
        "name": "crud-agent", "password": "123456"})).json()
    assert r2["code"] != 0

    # 改名 + 改备注
    await _mk_agent(session, m.id, "other-agent")
    r3 = (await client.put(f"/merchant-api/agent/{aid}", headers=h, json={
        "name": "crud-renamed", "remark": "新备注"})).json()
    assert r3["code"] == 0, r3
    assert r3["data"]["name"] == "crud-renamed"

    # 改成他人已占用的名称 → 拒绝
    r4 = (await client.put(f"/merchant-api/agent/{aid}", headers=h, json={
        "name": "other-agent"})).json()
    assert r4["code"] != 0

    # 改密码后用新密码登录
    r5 = (await client.put(f"/merchant-api/agent/{aid}", headers=h, json={
        "password": "newpass99"})).json()
    assert r5["code"] == 0, r5
    r6 = (await client.post("/agent-api/auth/login", json={
        "username": "crud-renamed", "password": "newpass99", "captcha_code": "0000"})).json()
    assert r6["code"] == 0, r6

    # 删除后不可再操作
    r7 = (await client.delete(f"/merchant-api/agent/{aid}", headers=h)).json()
    assert r7["code"] == 0, r7
    r8 = (await client.delete(f"/merchant-api/agent/{aid}", headers=h)).json()
    assert r8["code"] == 404

    # 软删除：列表不再出现、原账号不可登录、原名称可复用
    r9 = (await client.get("/merchant-api/agent/list",
                           params={"kw": "crud-renamed"}, headers=h)).json()
    assert r9["data"]["total"] == 0
    r10 = (await client.post("/agent-api/auth/login", json={
        "username": "crud-renamed", "password": "newpass99", "captcha_code": "0000"})).json()
    assert r10["code"] != 0
    r11 = (await client.post("/merchant-api/agent", headers=h, json={
        "name": "crud-renamed", "password": "123456"})).json()
    assert r11["code"] == 0, r11


# ============================================================
# 代理业绩明细：口径（排除扣量/充值）、汇总、排序
# ============================================================

@pytest.mark.asyncio
async def test_performance_list_aggregation(client, seeded, session):
    m = seeded["merchant"]
    a1 = await _mk_agent(session, m.id, "top-agent")
    a2 = await _mk_agent(session, m.id, "low-agent")
    a3 = await _mk_agent(session, m.id, "zero-agent")  # 无单代理：0 行仍展示

    # a1：今日两笔（10+20），一笔被扣量（5），一笔充值单（100）
    await _mk_order(session, m.id, agent_id=a1.id, money="10.00")
    await _mk_order(session, m.id, agent_id=a1.id, money="20.00")
    await _mk_order(session, m.id, agent_id=a1.id, money="5.00", deducted=True)
    await _mk_order(session, m.id, agent_id=a1.id, money="100.00", order_type=1)
    # a2：昨日一笔（7），今日一笔未支付（3）
    await _mk_order(session, m.id, agent_id=a2.id, money="7.00", paid=timedelta(days=1))
    await _mk_order(session, m.id, agent_id=a2.id, money="3.00", status=0, paid=None)
    # 无代理归属订单：今日 8（计入商户汇总、不进任何明细行）
    await _mk_order(session, m.id, agent_id=0, money="8.00")

    mch_token = await issue_token(guard="merchant", user_id=m.id, session=session)
    r = (await client.get("/merchant-api/agent/performance/list",
                          headers=_mch_headers(mch_token))).json()
    assert r["code"] == 0, r
    data = r["data"]

    rows = {row["name"]: row for row in data["items"]}
    assert _money(rows["top-agent"]["today_money"]) == Decimal("30.00")
    assert _money(rows["top-agent"]["yesterday_money"]) == Decimal("0.00")
    assert _money(rows["top-agent"]["total_money"]) == Decimal("30.00")
    assert rows["top-agent"]["total_count"] == 2

    assert _money(rows["low-agent"]["today_money"]) == Decimal("0.00")
    assert _money(rows["low-agent"]["yesterday_money"]) == Decimal("7.00")
    assert rows["low-agent"]["total_count"] == 1

    assert _money(rows["zero-agent"]["total_money"]) == Decimal("0.00")
    assert rows["zero-agent"]["total_count"] == 0

    # 按总金额倒序
    assert [row["name"] for row in data["items"]] == ["top-agent", "low-agent", "zero-agent"]

    # summary：商户整体口径（含无代理订单，排除扣量/充值/未支付）
    s = data["summary"]
    assert _money(s["today_money"]) == Decimal("38.00")  # 30 + 8
    assert _money(s["yesterday_money"]) == Decimal("7.00")
    assert _money(s["total_money"]) == Decimal("45.00")
    assert s["total_count"] == 4

    # kw 过滤明细行
    r2 = (await client.get("/merchant-api/agent/performance/list",
                           params={"kw": "top"}, headers=_mch_headers(mch_token))).json()
    assert r2["data"]["total"] == 1
    assert r2["data"]["items"][0]["name"] == "top-agent"
    # summary 不随 kw 收窄（商户整体口径）
    assert _money(r2["data"]["summary"]["total_money"]) == Decimal("45.00")


# ============================================================
# 代理端：首页业绩 + 订单列表（隔离 / 筛选）
# ============================================================

@pytest.mark.asyncio
async def test_agent_dashboard_and_order_list(client, seeded, session):
    m = seeded["merchant"]
    me = await _mk_agent(session, m.id, "panel-agent")
    other = await _mk_agent(session, m.id, "other-agent2")

    await _mk_order(session, m.id, agent_id=me.id, money="12.50")
    await _mk_order(session, m.id, agent_id=me.id, money="4.00", paid=timedelta(days=1))
    await _mk_order(session, m.id, agent_id=me.id, money="9.00", deducted=True)  # 不计业绩
    # 他人的订单与无代理订单均不可见
    await _mk_order(session, m.id, agent_id=other.id, money="99.00")
    await _mk_order(session, m.id, agent_id=0, money="77.00")
    # 历史订单（前天创建，用于日期筛选）
    old = await _mk_order(session, m.id, agent_id=me.id, money="6.00",
                          paid=timedelta(days=2))
    old.created_at = datetime.now() - timedelta(days=2)
    session.add(old)
    await session.commit()

    token = await issue_token(guard="agent", user_id=me.id, session=session)
    h = {"Authorization": f"Bearer {token.access_token}"}

    # 首页业绩：今日 12.50 / 昨日 4.00 / 累计（12.50+4.00+6.00）
    d = (await client.get("/agent-api/dashboard", headers=h)).json()
    assert d["code"] == 0, d
    assert _money(d["data"]["today"]["money"]) == Decimal("12.50")
    assert d["data"]["today"]["count"] == 1
    assert _money(d["data"]["yesterday"]["money"]) == Decimal("4.00")
    assert _money(d["data"]["total"]["money"]) == Decimal("22.50")
    assert d["data"]["total"]["count"] == 3

    # 订单列表：只见自己的单（含被扣量单——代理侧按商户口径遮蔽为「未支付」），倒序
    o = (await client.get("/agent-api/order/list", headers=h)).json()
    assert o["code"] == 0, o
    assert o["data"]["total"] == 4
    assert all(row["trade_no"] for row in o["data"]["items"])
    # 扣量单不可从列表反推：状态/实收/支付时间全部按未支付返回
    sent = next(row for row in o["data"]["items"] if _money(row["money"]) == Decimal("9.00"))
    assert sent["status"] == 0 and sent["realmoney"] == "" and sent["paid_at"] == ""

    # 状态筛选（代理视角）：待支付含扣量单；已支付排除扣量单
    o2 = (await client.get("/agent-api/order/list", params={"status": 0}, headers=h)).json()
    assert o2["data"]["total"] == 1
    assert _money(o2["data"]["items"][0]["money"]) == Decimal("9.00")
    o3 = (await client.get("/agent-api/order/list", params={"status": 1}, headers=h)).json()
    assert o3["data"]["total"] == 3
    assert all(row["status"] == 1 for row in o3["data"]["items"])

    # 日期筛选：仅前天那笔
    day = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    o4 = (await client.get("/agent-api/order/list",
                           params={"starttime": day, "endtime": day}, headers=h)).json()
    assert o4["data"]["total"] == 1
    assert _money(o4["data"]["items"][0]["money"]) == Decimal("6.00")


@pytest.mark.asyncio
async def test_agent_link_e2e_order_paid_updates_dashboard(client, seeded, session, engine):
    """端到端串接：代理专属链接下单 → 上游签名回调支付 → 代理业绩/订单实时反映。"""
    from tests.conftest import pay_code
    from tests.test_deduct_refill import _refetch
    from tests.test_pay_flow import _notify_params

    m = seeded["merchant"]
    a = await _mk_agent(session, m.id, "agent-link-e2e")

    # 走 /p/{code} 专属链接建单，归属代理
    token = await pay_code(session, 1001, a.id)
    r = await client.post("/paypage/order", json={"token": token, "money": "10.00"})
    body = r.json()
    assert body["code"] == 0, body
    trade_no = body["data"]["trade_no"]

    order = await _refetch(engine, trade_no)
    assert order.agent_id == a.id

    # 上游验签回调 → 支付成功
    resp = await client.post(f"/callbacks/epay/{trade_no}",
                             params=_notify_params(order, money="10.00", trade_no="UPLINK1"))
    assert resp.text == "success", resp.text

    # 代理登录面板：今日业绩与订单列表实时反映该单（口径：已支付收款单）
    login = await client.post("/agent-api/auth/login", json={
        "username": "agent-link-e2e", "password": "agent123", "captcha_code": "0000"})
    assert login.json()["code"] == 0, login.json()
    h = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    d = (await client.get("/agent-api/dashboard", headers=h)).json()
    assert d["code"] == 0, d
    assert _money(d["data"]["today"]["money"]) == Decimal("10.00")
    assert d["data"]["today"]["count"] == 1
    assert _money(d["data"]["total"]["money"]) == Decimal("10.00")

    o = (await client.get("/agent-api/order/list", headers=h)).json()
    assert o["code"] == 0, o
    assert o["data"]["total"] == 1
    assert o["data"]["items"][0]["trade_no"] == trade_no
    assert _money(o["data"]["items"][0]["money"]) == Decimal("10.00")
