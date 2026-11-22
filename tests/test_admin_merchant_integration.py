"""后台「API密钥」弹窗的对接信息：详情接口下发明文密钥 + 测试业务编码。

口径要求（需求方拍板）：
- 明文 `api_secret` **只**从详情接口 `GET /admin-api/merchant/{id}` 出；
  列表 `POST /admin-api/merchant/list` 仍只有掩码，绝不批量下发。
- 业务编码取全局 `is_test=1` 且启用的那条（多条取 id 最小），没有就是 null，
  不回退正式编码 —— 与商户端 `/merchant-api/account/apikey` 同一口径。
- 每下发一次明文就留一条审计日志（谁 / 哪个商户 / 来源 IP）。
"""
import logging

import pytest
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import ChannelCode, Merchant, SystemAdmin


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


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@pytest.mark.asyncio
async def test_admin_detail_returns_plaintext_and_test_code(client, seeded, session):
    """详情带明文密钥与测试编码；列表接口不下发明文。"""
    session.add_all([
        ChannelCode(code="1001", name="正式通道", status=1),
        ChannelCode(code="1002", name="测试通道", status=1),
        ChannelCode(code="1003", name="联调编码", status=1, is_test=True),
        # 停用的测试编码不参与挑选
        ChannelCode(code="9999", name="停用测试", status=0, is_test=True),
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
    assert data["channel_code"] == {"code": "1003", "name": "联调编码"}

    # 列表：只有掩码，没有明文
    r = await client.post("/admin-api/merchant/list", headers=headers, json={"page": 1, "per_page": 50})
    items = r.json()["data"]["items"]
    row = next(x for x in items if x["pid"] == 1001)
    assert "api_secret" not in row
    assert "api_secret_masked" in row


@pytest.mark.asyncio
async def test_admin_detail_channel_code_null_without_test_code(client, seeded, session):
    """没有测试编码 → channel_code 为 null（前端整行不展示，不回退正式编码）。"""
    session.add_all([
        ChannelCode(code="1001", name="正式通道", status=1),
        ChannelCode(code="9001", name="停用测试", status=0, is_test=True),
    ])
    await session.commit()
    m = await _merchant(session)
    headers = await _admin_headers(session)

    r = await client.get(f"/admin-api/merchant/{m.id}", headers=headers)
    data = r.json()["data"]
    assert data["channel_code"] is None


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
