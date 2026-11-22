"""商户面板「对接信息」：apikey 接口带出复制用的真实配置。

面板把这段数据拼成「字段：值」纯文本给接入方，所以接口至少要保证：
名称/商户号真实、业务编码只给「测试编码」那一条（`is_test` 显式标记，没有就是 null，
不回退正式编码、也不按名称猜）、不再下发站点地址（下单/查询/文档域名由前端取
window.location.origin）。
"""
import pytest
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import ChannelCode, Merchant, Setting, SystemAdmin


async def _merchant_headers(session):
    m = (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()
    tk = await issue_token(guard="merchant", user_id=m.id, session=session)
    return {"Authorization": f"Bearer {tk.access_token}"}


async def _admin_headers(session):
    admin = SystemAdmin(name="管理员", username="testcodeadmin",
                        password=hash_password("admin123456"), is_super_admin=True)
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    tk = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {tk.access_token}"}


@pytest.mark.asyncio
async def test_apikey_exposes_only_marked_test_code(client, seeded, session):
    """有 is_test 编码 → 正常输出；名称含「测试」但没标记的不算。"""
    session.add(Setting(key="site_url", value="https://p.example.com/"))
    session.add_all([
        ChannelCode(code="1001", name="正式通道", status=1),
        # 名字含「测试」但没打标记：按显式标记走，不能被启发式选中
        ChannelCode(code="1002", name="测试通道", status=1),
        ChannelCode(code="1003", name="联调编码", status=1, is_test=True),
        ChannelCode(code="9999", name="停用测试", status=0, is_test=True),
    ])
    await session.commit()

    resp = await client.get("/merchant-api/account/apikey",
                            headers=await _merchant_headers(session))
    body = resp.json()
    assert body["code"] == 0, body
    data = body["data"]

    assert data["username"] == "testmch"
    assert data["pid"] == 1001
    assert data["channel_code"] == {"code": "1003", "name": "联调编码", "is_test": True}
    # 域名交给前端 window.location.origin，后端不再下发站点地址
    assert "site_url" not in data
    assert "channel_codes" not in data


@pytest.mark.asyncio
async def test_apikey_channel_code_null_without_test_code(client, seeded, session):
    """没有测试编码 → null（前端整行不展示），不回退到第一个启用编码。"""
    session.add_all([
        ChannelCode(code="a001", name="通道A", status=1),
        ChannelCode(code="b001", name="通道B", status=1),
    ])
    await session.commit()

    resp = await client.get("/merchant-api/account/apikey",
                            headers=await _merchant_headers(session))
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["channel_code"] is None


@pytest.mark.asyncio
async def test_apikey_channel_code_empty_when_none_at_all(client, seeded, session):
    resp = await client.get("/merchant-api/account/apikey",
                            headers=await _merchant_headers(session))
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["channel_code"] is None


@pytest.mark.asyncio
async def test_apikey_multiple_test_codes_pick_smallest_id(client, seeded, session):
    """允许多条测试编码：按 id 升序取第一条启用中的，行为可预期。"""
    session.add_all([
        ChannelCode(code="t100", name="先建的测试", status=1, is_test=True),
        ChannelCode(code="t900", name="后建的测试", status=1, is_test=True),
    ])
    await session.commit()
    # 停用的测试编码不能因为标记了就被选中
    session.add(ChannelCode(code="t050", name="停用的测试", status=0, is_test=True))
    await session.commit()

    resp = await client.get("/merchant-api/account/apikey",
                            headers=await _merchant_headers(session))
    data = resp.json()["data"]
    assert data["channel_code"]["name"] == "先建的测试"
    assert data["channel_code"]["is_test"] is True


@pytest.mark.asyncio
async def test_admin_channel_code_is_test_roundtrip(client, session, seeded):
    """后台新建/编辑能设置并回显 is_test，列表也带上标记。"""
    headers = await _admin_headers(session)
    from app.models import Channel, PaymentType

    pt = (await session.exec(select(PaymentType))).first()
    ch = Channel(name="测试上游", plugin_name="epay", payment_type_id=pt.id, status=1)
    session.add(ch)
    await session.commit()
    await session.refresh(ch)

    body = {"code": "t001", "name": "联调", "remark": "", "mode": 0,
            "status": 1, "is_test": True, "items": [{"channel_id": ch.id, "weight": 1}]}
    r = await client.post("/admin-api/pay/channel-codes", json=body, headers=headers)
    assert r.json()["code"] == 0, r.text
    code_id = r.json()["data"]["id"]

    r = await client.get(f"/admin-api/pay/channel-codes/{code_id}", headers=headers)
    assert r.json()["data"]["is_test"] is True
    r = await client.get("/admin-api/pay/channel-codes", headers=headers)
    row = next(it for it in r.json()["data"]["items"] if it["id"] == code_id)
    assert row["is_test"] is True

    # 编辑关掉标记：回到正式编码，接口不再展示
    r = await client.put(f"/admin-api/pay/channel-codes/{code_id}",
                         json={**body, "is_test": False}, headers=headers)
    assert r.json()["code"] == 0, r.text
    r = await client.get(f"/admin-api/pay/channel-codes/{code_id}", headers=headers)
    assert r.json()["data"]["is_test"] is False
    resp = await client.get("/merchant-api/account/apikey",
                            headers=await _merchant_headers(session))
    assert resp.json()["data"]["channel_code"] is None


@pytest.mark.asyncio
async def test_channel_codes_lists_enabled_codes_with_pay_types(client, seeded, session):
    """「支付通道」卡片：只列启用编码，支付方式取编码内启用中通道的类型。

    - 停用编码（status=0）不出现：下单会直接报错
    - 停用通道不贡献支付方式：它不参与分流，列出来等于虚假可用
    - 停用的支付方式（payment_types.status=0）也不下发
    - 没有任何可用通道的编码照常列，但 pay_types 为空数组（前端提示「暂不可用」）
    """
    from app.models import Channel, ChannelCodeItem, PaymentType
    from app.services.crypto import encrypt_config

    wx = PaymentType(code="wxpay", name="微信支付", status=1, sort=2)
    bank = PaymentType(code="bank", name="银行卡", status=0, sort=3)
    session.add_all([wx, bank])
    await session.commit()
    await session.refresh(wx)
    await session.refresh(bank)

    cfg = encrypt_config({"appurl": "https://upstream.example/", "appid": "2001",
                          "appkey": "k", "appswitch": "1"})
    ch_wx = Channel(name="微信上游", plugin_name="epay", payment_type_id=wx.id,
                    status=1, mode=0, config=cfg)
    ch_bank = Channel(name="银行上游", plugin_name="epay", payment_type_id=bank.id,
                      status=1, mode=0, config=cfg)
    ch_alipay_off = Channel(name="支付宝停用上游", plugin_name="epay",
                            payment_type_id=seeded["pay_type"].id, status=0, mode=0, config=cfg)
    session.add_all([ch_wx, ch_bank, ch_alipay_off])
    await session.commit()
    for ch in (ch_wx, ch_bank, ch_alipay_off):
        await session.refresh(ch)

    codes = {
        "a001": ChannelCode(code="a001", name="全支付", status=1),
        "b001": ChannelCode(code="b001", name="停用编码", status=0),
        "c001": ChannelCode(code="c001", name="只有停用通道", status=1, is_test=True,
                            remark="联调用"),
        "d001": ChannelCode(code="d001", name="未绑定", status=1),
    }
    for c in codes.values():
        session.add(c)
    await session.commit()
    for c in codes.values():
        await session.refresh(c)
    for code, channel_ids in {
        codes["a001"].id: [seeded["channel"].id, ch_wx.id, ch_bank.id],
        codes["c001"].id: [ch_alipay_off.id],
    }.items():
        for cid in channel_ids:
            session.add(ChannelCodeItem(code_id=code, channel_id=cid, weight=1, sort=0))
    await session.commit()

    resp = await client.get("/merchant-api/account/channel-codes",
                            headers=await _merchant_headers(session))
    body = resp.json()
    assert body["code"] == 0, body
    data = body["data"]
    # 停用编码不出现；启用编码按 id 升序
    assert [c["code"] for c in data] == ["a001", "c001", "d001"]

    a = data[0]
    assert a["name"] == "全支付"
    # 支付宝（sort=1）+ 微信（sort=2）；银行卡的支付方式停用、被过滤
    assert [t["code"] for t in a["pay_types"]] == ["alipay", "wxpay"]
    assert a["pay_types"][1] == {"code": "wxpay", "name": "微信支付", "icon": ""}

    c = data[1]
    assert c["is_test"] is True
    assert c["remark"] == "联调用"
    assert c["pay_types"] == []
    assert data[2]["pay_types"] == []
