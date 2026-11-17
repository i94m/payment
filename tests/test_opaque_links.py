"""不透明链接（/c/{token} 收银台、/p/{code} 收款页短码）测试。

链接不再暴露 trade_no（含下单时间）与商户 pid；项目全新起步，
仅保留 /c/{token}、/p/{code} 唯一形态（旧路由已移除）。
"""
import re

import pytest
from sqlmodel import select

from app.lib.linktoken import (
    cashier_url,
    decode_link_token,
    encode_link_token,
)
from app.models import Merchant
from app.services import paytoken
from tests.conftest import pay_code, signed_params


async def _create_order(client, out_trade_no="OPQ1"):
    params = {
        "pid": "1001", "type": "alipay", "out_trade_no": out_trade_no,
        "notify_url": "https://merchant.example/notify",
        "return_url": "https://merchant.example/return",
        "name": "不透明链接测试", "money": "10.00",
    }
    params = signed_params(params)
    resp = await client.post("/api/orders", json=params)
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]


def test_token_roundtrip_and_tamper():
    token = encode_link_token("2610061200000000000001")
    assert decode_link_token(token) == "2610061200000000000001"
    # 外观随机、不含业务号
    assert "261006" not in token and len(token) > 80
    # 篡改/非法输入
    assert decode_link_token(token[:-4] + "AAAA") is None
    assert decode_link_token("not-a-token") is None
    assert decode_link_token("") is None


@pytest.mark.asyncio
async def test_pay_code_short_and_stable(session, seeded):
    """收款页短码：10 字符随机外观、不含 pid、同商户稳定复用、代理独立成码。"""
    m = seeded["merchant"]
    code = await paytoken.get_or_create(session, m.id)
    assert re.fullmatch(r"[0-9a-z]{10}", code)
    assert str(m.pid) not in code
    # 同 (merchant, agent) 幂等：不换码
    assert await paytoken.get_or_create(session, m.id) == code
    # 代理专属码独立，且解析回正确归属
    agent_code = await paytoken.get_or_create(session, m.id, 55)
    assert agent_code != code
    pt = await paytoken.resolve(session, agent_code)
    assert (pt.merchant_id, pt.agent_id) == (m.id, 55)
    # 非法输入
    assert await paytoken.resolve(session, "") is None
    assert await paytoken.resolve(session, "no-such-code") is None
    assert await paytoken.resolve(session, "x" * 40) is None


@pytest.mark.asyncio
async def test_pay_code_lowercase_and_case_insensitive(session, seeded):
    """短码只生成小写 base36；解析对大小写不敏感（不依赖库排序规则）。"""
    m = seeded["merchant"]
    codes = [await paytoken.get_or_create(session, m.id, aid) for aid in range(100, 120)]
    assert len(set(codes)) == len(codes)
    assert all(re.fullmatch(r"[0-9a-z]{10}", c) for c in codes)

    code = codes[0]
    # 用户手打/链接被转大写也能打开
    assert (await paytoken.resolve(session, code.upper())).token == code
    # 混合大小写输入同理
    mixed = "".join(ch.upper() if i % 2 else ch for i, ch in enumerate(code))
    assert (await paytoken.resolve(session, mixed)).token == code


@pytest.mark.asyncio
async def test_legacy_mixed_case_code_still_resolves(session, seeded):
    """历史 base62 混合大小写短码：旧链接不因改字母表而失效。"""
    from app.models import PayToken

    m = seeded["merchant"]
    session.add(PayToken(token="AbCdEf1234", merchant_id=m.id, agent_id=0))
    await session.commit()

    assert (await paytoken.resolve(session, "AbCdEf1234")).token == "AbCdEf1234"
    # 小写输入仍能命中旧码（SQLite 为大小写敏感比较，靠 resolve 的兜底查询）
    assert (await paytoken.resolve(session, "abcdef1234")).token == "AbCdEf1234"


@pytest.mark.asyncio
async def test_api_cashier_url_is_opaque(client, seeded):
    data = await _create_order(client)
    url = data["cashier_url"]
    # 默认下发即不透明形态：/c/{长随机串}，不含 trade_no
    assert re.search(r"/cashier/[A-Za-z0-9_-]{80,}", url), url
    assert data["trade_no"] not in url

    # 不透明链接可打开收银台（普通商户自选页，trade_no 已注入页面）
    path = url.replace("http://testserver", "")
    resp = await client.get(path)
    assert resp.status_code == 200
    assert "收银台" in resp.text
    assert data["trade_no"] in resp.text  # 页面内注入（URL 不暴露即可）

    # 旧路由已移除：/cashier?trade_no=、/paypage/{pid} 不再是业务入口
    # （无匹配路由，落入 SPA 兜底返回前端壳，不含收银台/收款页内容）
    resp2 = await client.get("/cashier", params={"trade_no": data["trade_no"]})
    assert "收银台" not in resp2.text
    resp3 = await client.get("/paypage/1001")
    assert "向商户付款" not in resp3.text


@pytest.mark.asyncio
async def test_opaque_cashier_random_merchant_direct_launch(client, seeded, session):
    """随机金额商户的不透明收银台链接：同 302 直跳上游。"""
    m = (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()
    m.random_enable = True
    m.random_min = 5
    m.random_max = 10
    session.add(m)
    await session.commit()

    data = await _create_order(client, "OPQ-R")
    resp = await client.get(data["cashier_url"].replace("http://testserver", ""),
                            follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("https://upstream.example/pay/")


@pytest.mark.asyncio
async def test_opaque_cashier_invalid_token(client, seeded):
    resp = await client.get("/cashier/invalid-token-value-here")
    assert resp.status_code == 200
    assert "链接无效" in resp.text


@pytest.mark.asyncio
async def test_paypage_opaque_link_flow(client, seeded, session):
    """/p/{code} 收款页全流程：info 按短码解析商户，order 按短码建单。"""
    token = await pay_code(session)

    # 页面本体
    page = await client.get(f"/p/{token}")
    assert page.status_code == 200 and "向商户付款" in page.text

    # info：短码模式（不带 pid）
    info = await client.get("/paypage/info", params={"token": token})
    body = info.json()
    assert body["code"] == 0, body
    assert body["data"]["pid"] == 1001

    # order：短码建单，返回的收银台链接同样是不透明形态
    order = await client.post("/paypage/order", json={"token": token, "money": "6.00"})
    obody = order.json()
    assert obody["code"] == 0, obody
    assert re.search(r"/cashier/[A-Za-z0-9_-]{80,}", obody["data"]["cashier_url"])

    # 非法短码
    bad = await client.get("/paypage/info", params={"token": "broken"})
    assert bad.json()["code"] != 0


@pytest.mark.asyncio
async def test_paypage_agent_token(client, seeded, session, engine):
    """代理专属 token：建单归属正确。"""
    from app.models import Agent

    m = (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()
    agent = Agent(merchant_id=m.id, name="ag-opq", password="x", status=1)
    session.add(agent)
    await session.commit()

    token = await pay_code(session, 1001, agent.id)
    order = await client.post("/paypage/order", json={"token": token, "money": "6.00"})
    data = order.json()["data"]
    from tests.test_deduct_refill import _refetch
    o = await _refetch(engine, data["trade_no"])
    assert o.agent_id == agent.id


@pytest.mark.asyncio
async def test_paypage_agent_disabled_or_deleted_falls_back(client, seeded, session, engine):
    """停用/软删代理后，旧链接仍可下单，归属静默归 0（订单照常入账商户，业绩不再计入）。"""
    from datetime import datetime

    from app.models import Agent
    from tests.test_deduct_refill import _refetch

    m = (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()

    async def _new_order_agent_id(a: Agent) -> int:
        token = await pay_code(session, 1001, a.id)
        r = await client.post("/paypage/order", json={"token": token, "money": "6.00"})
        assert r.json()["code"] == 0, r.json()
        o = await _refetch(engine, r.json()["data"]["trade_no"])
        return o.agent_id

    # 停用：下单不被拦截，归属从代理 id 回退为 0
    off = Agent(merchant_id=m.id, name="ag-opq-off", password="x", status=1)
    session.add(off)
    await session.commit()
    assert await _new_order_agent_id(off) == off.id
    off.status = 0
    session.add(off)
    await session.commit()
    assert await _new_order_agent_id(off) == 0

    # 软删（status 置 0 + deleted_at）：走同一回退分支
    dele = Agent(merchant_id=m.id, name="ag-opq-del", password="x", status=1)
    session.add(dele)
    await session.commit()
    dele.status = 0
    dele.deleted_at = datetime.now()
    session.add(dele)
    await session.commit()
    assert await _new_order_agent_id(dele) == 0


@pytest.mark.asyncio
async def test_merchant_paypage_settings_link(client, seeded, session):
    """商户端收款链接下发 /p/{code} 短码形态，不含 pid。"""
    from app.core.security import issue_token

    m = (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()
    token = await issue_token(guard="merchant", user_id=m.id, session=session)
    resp = await client.get("/merchant-api/paypage/settings",
                            headers={"Authorization": f"Bearer {token.access_token}"})
    link = resp.json()["data"]["link"]
    assert re.fullmatch(r"/p/[0-9a-z]{10}", link), link
    assert "1001" not in link
