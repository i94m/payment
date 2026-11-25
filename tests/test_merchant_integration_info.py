"""商户面板「对接信息」：apikey 接口带出复制用的真实配置。

面板把这段数据拼成「字段：值」纯文本给接入方，所以接口至少要保证：
名称/商户号真实、默认通道取后台指定且启用中的那条 `channels.is_default`
（全平台唯一，没有就是 null，不回退到随便一条通道）、不再下发站点地址
（下单/查询/文档域名由前端取 window.location.origin）。
"""
import pytest
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import Channel, ChannelCode, Merchant, PaymentType, Setting, SystemAdmin


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


def _mk_channel(pt_id: int, name: str, status: int = 1, is_default: bool = False) -> Channel:
    return Channel(name=name, plugin_name="epay", payment_type_id=pt_id,
                   status=status, mode=0, is_default=is_default)


@pytest.mark.asyncio
async def test_apikey_exposes_default_channel(client, seeded, session):
    """后台指定了默认通道 → 下发「通道名称 + 支付方式」。"""
    session.add(Setting(key="site_url", value="https://p.example.com/"))
    pt = (await session.exec(
        select(PaymentType).where(PaymentType.code == "alipay")
    )).first()
    session.add_all([
        _mk_channel(pt.id, "普通通道"),
        _mk_channel(pt.id, "主用通道", is_default=True),
    ])
    await session.commit()

    resp = await client.get("/merchant-api/account/apikey",
                            headers=await _merchant_headers(session))
    body = resp.json()
    assert body["code"] == 0, body
    data = body["data"]

    assert data["username"] == "testmch"
    assert data["pid"] == 1001
    assert data["default_channel"] == {"name": "主用通道", "payment_type": pt.name}
    # 域名交给前端 window.location.origin，后端不再下发站点地址
    assert "site_url" not in data
    assert "channel_codes" not in data


@pytest.mark.asyncio
async def test_apikey_default_channel_null_without_default(client, seeded, session):
    """没有默认通道 → null（前端整行不展示），不回退到第一个启用通道。"""
    pt = (await session.exec(
        select(PaymentType).where(PaymentType.code == "alipay")
    )).first()
    session.add_all([
        _mk_channel(pt.id, "通道A"),
        _mk_channel(pt.id, "通道B"),
    ])
    await session.commit()

    resp = await client.get("/merchant-api/account/apikey",
                            headers=await _merchant_headers(session))
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["default_channel"] is None


@pytest.mark.asyncio
async def test_apikey_default_channel_empty_when_none_at_all(client, seeded, session):
    resp = await client.get("/merchant-api/account/apikey",
                            headers=await _merchant_headers(session))
    body = resp.json()
    assert body["code"] == 0, body
    assert body["data"]["default_channel"] is None
    # 旧字段（测试编码）已下线，不再下发
    assert "channel_code" not in body["data"]


@pytest.mark.asyncio
async def test_apikey_skips_disabled_default_channel(client, seeded, session):
    """标记了但被停用的通道不算（后台停用会清标记，这里兜底再判一次）。"""
    pt = (await session.exec(
        select(PaymentType).where(PaymentType.code == "alipay")
    )).first()
    session.add(_mk_channel(pt.id, "停用的默认通道", status=0, is_default=True))
    await session.commit()

    resp = await client.get("/merchant-api/account/apikey",
                            headers=await _merchant_headers(session))
    assert resp.json()["data"]["default_channel"] is None


@pytest.mark.asyncio
async def test_admin_set_default_channel_is_global_unique(client, session, seeded):
    """默认通道全平台唯一：设新的会清掉旧的；停用通道不能设为默认。"""
    headers = await _admin_headers(session)
    pt = (await session.exec(
        select(PaymentType).where(PaymentType.code == "alipay")
    )).first()
    a = _mk_channel(pt.id, "通道A")
    b = _mk_channel(pt.id, "通道B")
    off = _mk_channel(pt.id, "停用通道", status=0)
    session.add_all([a, b, off])
    await session.commit()
    for c in (a, b, off):
        await session.refresh(c)

    r = await client.put(f"/admin-api/pay/channels/{a.id}/default",
                         json={"enabled": True}, headers=headers)
    assert r.json()["code"] == 0, r.text
    assert r.json()["data"]["is_default"] is True

    # 换到 B：A 的标记必须被清掉（商户端只展示一条）
    r = await client.put(f"/admin-api/pay/channels/{b.id}/default",
                         json={"enabled": True}, headers=headers)
    assert r.json()["code"] == 0, r.text
    detail_a = (await client.get(f"/admin-api/pay/channels/{a.id}", headers=headers)).json()["data"]
    detail_b = (await client.get(f"/admin-api/pay/channels/{b.id}", headers=headers)).json()["data"]
    assert detail_a["is_default"] is False
    assert detail_b["is_default"] is True

    # 停用通道不能设为默认
    r = await client.put(f"/admin-api/pay/channels/{off.id}/default",
                         json={"enabled": True}, headers=headers)
    assert r.json()["code"] == -1
    assert "先启用" in r.json()["message"]

    # 停用当前默认通道时，标记一并清掉
    r = await client.put(f"/admin-api/pay/channels/{b.id}",
                         json={"name": b.name, "plugin_name": "epay",
                               "payment_type_id": pt.id, "status": 0,
                               "merchant_rate": "0.00", "cost_rate": "0.00"},
                         headers=headers)
    assert r.json()["code"] == 0, r.text
    assert r.json()["data"]["is_default"] is False


@pytest.mark.asyncio
async def test_channel_codes_lists_enabled_codes_with_pay_types(client, seeded, session):
    """「支付通道」卡片：只列启用编码，支付方式取编码内启用中通道的类型。

    - 停用编码（status=0）不出现：下单会直接报错
    - 停用通道不贡献支付方式：它不参与分流，列出来等于虚假可用
    - 停用的支付方式（payment_types.status=0）也不下发
    - 没有任何可用通道的编码照常列，但 pay_types 为空数组（前端提示「暂不可用」）
    """
    from app.models import ChannelCodeItem
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
        "c001": ChannelCode(code="c001", name="只有停用通道", status=1,
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
    # 测试标记已下线
    assert "is_test" not in a

    c = data[1]
    assert c["remark"] == "联调用"
    assert c["pay_types"] == []
    assert data[2]["pay_types"] == []
