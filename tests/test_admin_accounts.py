"""管理员账号管理：详情接口（编辑弹窗回填）与超管限定。

早前后端只有列表/新增/修改/删除，前端编辑走 GET /permission/accounts/{id}
落 404，点「编辑」直接报错，这里锁住该接口。
"""
import pytest
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import SystemAdmin


async def _headers(session, username: str, is_super_admin: bool = True) -> dict:
    session.add(SystemAdmin(
        name=username, username=username,
        password=hash_password("admin123"), is_super_admin=is_super_admin,
    ))
    await session.commit()
    admin = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == username)
    )).first()
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}


@pytest.mark.asyncio
async def test_account_detail_for_edit_dialog(client, session):
    """超管可读子账号详情：字段齐全且带已勾选权限，编辑弹窗据此回填。"""
    headers = await _headers(session, "root")

    created = await client.post("/admin-api/permission/accounts", headers=headers, json={
        "username": "sub_edit", "name": "子账号", "password": "sub123456",
        "is_forbid": False, "permissions": [],
    })
    assert created.status_code == 200, created.text
    item = created.json()["data"]

    resp = await client.get(f"/admin-api/permission/accounts/{item['id']}", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["id"] == item["id"]
    assert data["username"] == "sub_edit"
    assert data["name"] == "子账号"
    assert data["is_forbid"] is False
    assert data["permissions"] == []
    assert "permission_details" in data

    # 不存在 → body code 404（存量为 400 + code 404 的统一 err 口径）
    missing = await client.get("/admin-api/permission/accounts/999999", headers=headers)
    assert missing.json()["code"] == 404


@pytest.mark.asyncio
async def test_account_detail_requires_super(client, session):
    """账号管理仅超管可用：子账号即便持有 system.admin 权限也 403。"""
    headers = await _headers(session, "sub_admin", is_super_admin=False)
    resp = await client.get("/admin-api/permission/accounts/1", headers=headers)
    assert resp.status_code == 403, resp.text
    assert resp.json()["code"] == 403
