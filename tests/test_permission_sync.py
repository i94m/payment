"""权限同步：预览只读 / 执行幂等 / 超管限定 / 树外节点清理 / 并发锁。

后台「系统 → 账号管理 → 同步权限」对应接口，与 CLI 共用
app/services/permission.py 的对账逻辑（本项目口径：树里没有的权限连授权一起删）。
"""
import pytest
from sqlmodel import select

from app.constants.permissions import PERMISSION_TREE
from app.core.security import hash_password, issue_token
from app.models import SystemAdmin, SystemAdminPermission, SystemPermission

# 权限树完整节点数（组 + 子项）；用例不写死具体 key，树改了也不用跟着改
TOTAL = sum(1 + len(children) for _, _, children in PERMISSION_TREE)
GROUP_NAME, GROUP_KEY, CHILDREN = PERMISSION_TREE[0]
CHILD_NAME, CHILD_KEY = CHILDREN[0]

PREVIEW = "/admin-api/permission/sync/preview"
SYNC = "/admin-api/permission/sync"


async def _headers(session, username: str = "root", is_super_admin: bool = True) -> dict:
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
async def test_preview_is_read_only(client, session):
    headers = await _headers(session)

    resp = await client.get(PREVIEW, headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["created_count"] == TOTAL
    assert data["updated_count"] == 0
    assert data["removed_count"] == 0

    # 预览不落库
    assert (await session.exec(select(SystemPermission))).all() == []


@pytest.mark.asyncio
async def test_sync_creates_then_is_idempotent(client, session):
    headers = await _headers(session)

    first = await client.post(SYNC, headers=headers)
    assert first.status_code == 200, first.text
    assert first.json()["data"] == {"created": TOTAL, "updated": 0, "removed": 0}

    rows = (await session.exec(select(SystemPermission))).all()
    assert len(rows) == TOTAL
    group = next(r for r in rows if r.key == GROUP_KEY)
    child = next(r for r in rows if r.key == CHILD_KEY)
    assert group.pid == 0
    assert child.pid == group.id

    second = await client.post(SYNC, headers=headers)
    assert second.json()["data"] == {"created": 0, "updated": 0, "removed": 0}


@pytest.mark.asyncio
async def test_sync_updates_renamed_permission(client, session):
    headers = await _headers(session)
    await client.post(SYNC, headers=headers)

    perm = (await session.exec(
        select(SystemPermission).where(SystemPermission.key == CHILD_KEY)
    )).first()
    perm.name = "旧名称"
    session.add(perm)
    await session.commit()

    preview = (await client.get(PREVIEW, headers=headers)).json()["data"]
    assert preview["created_count"] == 0
    assert preview["updated_count"] == 1
    assert preview["updated"][0]["before_name"] == "旧名称"

    assert (await client.post(SYNC, headers=headers)).json()["data"]["updated"] == 1
    session.expire_all()
    assert (await session.exec(
        select(SystemPermission).where(SystemPermission.key == CHILD_KEY)
    )).first().name == CHILD_NAME


@pytest.mark.asyncio
async def test_sync_requires_super_admin(client, session):
    headers = await _headers(session, username="sub", is_super_admin=False)

    resp = await client.get(PREVIEW, headers=headers)
    assert resp.status_code == 403
    assert resp.json()["code"] == 403

    resp = await client.post(SYNC, headers=headers)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_sync_removes_permissions_outside_tree(client, session):
    """树里没有的权限（含授权）会被清掉——本项目以代码树为准。"""
    headers = await _headers(session)

    stale = SystemPermission(pid=0, name="旧权限", key="legacy.old")
    session.add(stale)
    await session.commit()
    await session.refresh(stale)
    admin = (await session.exec(select(SystemAdmin).where(SystemAdmin.username == "root"))).first()
    session.add(SystemAdminPermission(admin_id=admin.id, permission_id=stale.id))
    await session.commit()

    preview = (await client.get(PREVIEW, headers=headers)).json()["data"]
    assert preview["removed_count"] == 1
    assert preview["removed"][0]["key"] == "legacy.old"

    stale_id = stale.id
    result = (await client.post(SYNC, headers=headers)).json()["data"]
    assert result["removed"] == 1

    session.expire_all()
    assert (await session.exec(
        select(SystemPermission).where(SystemPermission.key == "legacy.old")
    )).first() is None
    assert (await session.exec(
        select(SystemAdminPermission).where(SystemAdminPermission.permission_id == stale_id)
    )).all() == []
