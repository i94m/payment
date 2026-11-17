"""B04 / B05 / B11 回归：权限缺口、插件表初始化同步、403 语义统一。

- B04：存储/上传/系统信息/通知中心/广播认证等接口不再「登录即可」，
  统一要求 system.admin（无权限 → HTTP 403）
- B05：插件表不再是空表 —— db init / migrate 和后台按钮走同一个同步函数，
  全新库同步后「已安装插件」与新增通道下拉立刻可用，且清理失效插件行
- B11：/admin-api/permission/*、/admin-api/queue/stats 无权限返回 HTTP 403
  （不再是 err(403) 的 HTTP 400 + body code 403）
"""
import pytest
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import Plugin, SystemAdmin

# (method, path) —— 只读子管理员（无任何权限）应当全部 403
GUARDED_ENDPOINTS = [
    ("get", "/admin-api/storage/files"),
    ("get", "/admin-api/upload/sts?policy=image"),
    ("get", "/admin-api/common/systemInfo"),
    ("get", "/admin-api/message/list"),
    ("get", "/admin-api/message/unread-count"),
    ("post", "/admin-api/broadcasting/auth"),
    ("get", "/admin-api/permission/accounts"),
    ("get", "/admin-api/permission/permissions"),
    ("post", "/admin-api/queue/stats"),
]


async def _admin_headers(session, username: str, is_super_admin: bool) -> dict:
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
@pytest.mark.parametrize("method,path", GUARDED_ENDPOINTS)
async def test_b04_b11_sub_admin_forbidden(client, session, method, path):
    """无权限子管理员：HTTP 403（真实状态码），未登录 401。"""
    anon = await getattr(client, method)(path)
    assert anon.status_code == 401

    headers = await _admin_headers(session, "readonly", is_super_admin=False)
    resp = await getattr(client, method)(path, headers=headers)
    assert resp.status_code == 403, (path, resp.status_code, resp.text)
    assert resp.json()["code"] == 403


@pytest.mark.asyncio
async def test_b04_super_admin_still_passes(client, session):
    """超管不受影响：这些接口照旧可用（不能修成谁都不能用）。"""
    headers = await _admin_headers(session, "root", is_super_admin=True)

    resp = await client.get("/admin-api/storage/files", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["code"] == 0

    resp = await client.get("/admin-api/upload/sts", params={"policy": "image"}, headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["provider"] == "local"

    resp = await client.get("/admin-api/common/systemInfo", headers=headers)
    assert resp.status_code == 200, resp.text
    assert "merchant_count" in resp.json()["data"]

    resp = await client.get("/admin-api/message/unread-count", headers=headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["count"] == 0

    resp = await client.get("/admin-api/permission/accounts", headers=headers)
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_b05_sync_plugins_seeds_plugins_table(session):
    """全新库（plugins 空表）同步一次即可用：写入内置插件，且幂等。"""
    from app.services.plugin_sync import sync_plugins

    assert (await session.exec(select(Plugin))).first() is None

    first = await sync_plugins(session)
    rows = (await session.exec(select(Plugin))).all()
    names = {r.name for r in rows}
    assert first["synced"] == len(rows) >= 1
    # 镜像内置的 epay 必须在表里（通道默认渠道），新增通道下拉才有选项
    assert "epay" in names
    epay = next(r for r in rows if r.name == "epay")
    assert epay.showname
    assert epay.supported_types
    assert first["message"].startswith("已同步")

    # 幂等：再同步不产生重复行，也不改动既有插件
    second = await sync_plugins(session)
    assert len((await session.exec(select(Plugin))).all()) == len(rows)
    assert second["removed"] == [] and second["referenced"] == []


@pytest.mark.asyncio
async def test_b05_sync_plugins_cleans_stale_rows(session, seeded):
    """盘上已不存在的插件行清理；仍被通道引用的跳过并回报。"""
    from app.services.plugin_sync import sync_plugins

    # seeded 里的通道用 epay；再造一个引用失效插件的通道 + 一个无人引用的失效插件
    session.add(Plugin(name="ghost", showname="无人引用"))
    session.add(Plugin(name="legacy", showname="仍被通道引用"))
    channel = seeded["channel"]
    channel.plugin_name = "legacy"
    session.add(channel)
    await session.commit()

    result = await sync_plugins(session)
    names = {p.name for p in (await session.exec(select(Plugin))).all()}
    assert "ghost" not in names          # 无人引用 → 删除
    assert "legacy" in names             # 通道还在用 → 保留，避免通道静默失效
    assert result["removed"] == ["ghost"]
    assert result["referenced"] == ["legacy"]
    assert "legacy" in result["message"]
