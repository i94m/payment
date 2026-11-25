"""后台「API密钥」弹窗的对接信息：详情接口下发明文密钥 + 默认通道。

口径要求（需求方拍板）：
- 明文 `api_secret` **只**从详情接口 `GET /admin-api/merchant/{id}` 出；
  列表 `POST /admin-api/merchant/list` 仍只有掩码，绝不批量下发。
- 默认通道取 `channels.is_default=1` 且启用的那条（全平台唯一），没有就是 null，
  不回退随便一条通道 —— 与商户端 `/merchant-api/account/apikey` 同一口径。
- 每下发一次明文就留一条审计日志（谁 / 哪个商户 / 来源 IP）。
"""
import logging

import pytest
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import (
    Channel, ChannelCode, ChannelCodeItem, Merchant, PaymentType, SystemAdmin,
)


async def _admin_headers(session):
    admin = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == "auditadmin")
    )).first()
    if not admin:
        admin = SystemAdmin(name="管理员", username="auditadmin",
                            password=hash_password("admin123456"), is_super_admin=True)
        session.add(admin)
        await session.commit()
        await session.refresh(admin)
    tk = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {tk.access_token}"}


async def _merchant(session) -> Merchant:
    return (await session.exec(select(Merchant).where(Merchant.pid == 1001))).one()


async def _alipay(session) -> PaymentType:
    return (await session.exec(
        select(PaymentType).where(PaymentType.code == "alipay")
    )).first()


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.mark.asyncio
async def test_admin_detail_returns_plaintext_and_default_channel(client, seeded, session):
    """详情带明文密钥与默认通道；列表接口不下发明文。"""
    pt = await _alipay(session)
    main = Channel(name="主用通道", plugin_name="epay", payment_type_id=pt.id, status=1,
                   mode=0, is_default=True)
    session.add_all([
        Channel(name="普通通道", plugin_name="epay", payment_type_id=pt.id, status=1, mode=0),
        main,
        # 停用的默认通道不参与挑选
        Channel(name="停用的主用通道", plugin_name="epay", payment_type_id=pt.id,
                status=0, mode=0, is_default=True),
    ])
    await session.commit()
    await session.refresh(main)
    # 对接信息展示的是「通道编码」（下游能直接当 channel_code 用），不是通道名称：
    # 只认启用中的编码，停用编码不展示
    on = ChannelCode(code="8088", name="主用编码", status=1)
    off = ChannelCode(code="9999", name="停用编码", status=0)
    session.add_all([on, off])
    await session.commit()
    await session.refresh(on)
    await session.refresh(off)
    session.add_all([
        ChannelCodeItem(code_id=on.id, channel_id=main.id),
        ChannelCodeItem(code_id=off.id, channel_id=main.id),
    ])
    await session.commit()
    m = await _merchant(session)
    headers = await _admin_headers(session)

    r = await client.get(f"/admin-api/merchant/{m.id}", headers=headers)
    body = r.json()
    assert body["code"] == 0, body
    data = body["data"]
    # 明文（不是掩码），且详情仍保留掩码字段供旧前端/其它用途
    assert data["api_secret"] and "*" not in data["api_secret"]
    assert data["api_secret_masked"].count("*") >= 4
    assert data["default_channel"] == {
        "name": "主用通道", "payment_type": pt.name, "codes": ["8088"],
    }
    # 旧的测试编码字段已下线
    assert "channel_code" not in data

    # 列表：只有掩码，没有明文
    r = await client.post("/admin-api/merchant/list", headers=headers, json={"page": 1, "per_page": 50})
    items = r.json()["data"]["items"]
    row = next(x for x in items if x["pid"] == 1001)
    assert "api_secret" not in row
    assert "api_secret_masked" in row


@pytest.mark.asyncio
async def test_admin_detail_default_channel_null_without_marker(client, seeded, session):
    """没有启用中的默认通道 → null（前端整行不展示，不回退正式通道）。"""
    pt = await _alipay(session)
    session.add_all([
        Channel(name="普通通道", plugin_name="epay", payment_type_id=pt.id, status=1, mode=0),
        Channel(name="停用的主用通道", plugin_name="epay", payment_type_id=pt.id,
                status=0, mode=0, is_default=True),
    ])
    await session.commit()
    m = await _merchant(session)
    headers = await _admin_headers(session)

    r = await client.get(f"/admin-api/merchant/{m.id}", headers=headers)
    data = r.json()["data"]
    assert data["default_channel"] is None


@pytest.mark.asyncio
async def test_admin_secret_view_is_audited(client, seeded, session):
    """查看明文密钥留痕：谁、哪个商户、来源 IP，且日志里不出现密钥本身。"""
    m = await _merchant(session)
    headers = await _admin_headers(session)

    capture = _Capture()
    audit_logger = logging.getLogger("app.audit")
    audit_logger.addHandler(capture)
    try:
        r = await client.get(f"/admin-api/merchant/{m.id}", headers=headers)
        assert r.json()["code"] == 0
    finally:
        audit_logger.removeHandler(capture)

    lines = [x for x in capture.messages if "action=view_merchant_api_secret" in x]
    assert len(lines) == 1, capture.messages
    line = lines[0]
    assert f"merchant_id={m.id}" in line
    assert "pid=1001" in line
    assert "admin=auditadmin" in line
    # 审计写的是事件，不是密钥内容
    secret = r.json()["data"]["api_secret"]
    assert secret and secret not in line
