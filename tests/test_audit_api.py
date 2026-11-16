"""审计查询接口的权限边界与「失败不落痕」回归（FREE-86 P1-7 / PR #10 门禁）。

审计表把资金/凭据操作的「谁、何时、前后值」落了库，读口子的权限口径因此
和资金数据同级，必须有门禁兜住：

- `system.audit` 是唯一读入口：非超管无该权限 → 真 403（不是静默空表）
- 匿名 → 401（admin 全局身份依赖 fail-closed，新增端点漏挂也不会裸奔）
- 有 `system.audit` 的非超管可读（运营/审计角色无需超管）
- **被拒绝的操作不写审计**：403/422 的请求没有产生效果，审计只记成功留痕

最后一条是审计可信度的前提——否则「有记录」不再等于「做过」。

**这 3 条是常驻门禁（`system.audit` 已随 PR #10 合入主线，xfail 标记已摘除）**：
断言对象是最终实现的读口口径——403/401 走的是 `app/routers/admin/audit.py` 的
`require_permission("system.audit")`，而不是「表里没数据所以为空」；`action` 参数是
子串筛选；「失败不落痕」靠 `record_audit` 只在业务成功后调用。验收时核对过
「修复前的树（`9334aa8`）上 `--runxfail` 全部失败，在最终树上全部通过」。
"""
import pytest
from sqlmodel import select

from app.core.security import hash_password, issue_token
from app.models import SystemAdmin, SystemAdminPermission, SystemAuditLog, SystemPermission


async def _make_admin(session, username: str, *, super_admin: bool = True,
                      perms: tuple[str, ...] = ()):
    admin = SystemAdmin(name="管理员", username=username,
                        password=hash_password("admin123456"),
                        is_super_admin=super_admin)
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    for key in perms:
        perm = (await session.exec(
            select(SystemPermission).where(SystemPermission.key == key)
        )).first()
        if perm is None:
            perm = SystemPermission(name=key, key=key)
            session.add(perm)
            await session.commit()
            await session.refresh(perm)
        session.add(SystemAdminPermission(admin_id=admin.id, permission_id=perm.id))
    await session.commit()
    token = await issue_token(guard="admin", user_id=admin.id, session=session)
    return {"Authorization": f"Bearer {token.access_token}"}, admin


@pytest.mark.asyncio
async def test_audit_query_requires_system_audit(session, client, seeded):
    """没有 system.audit 的账号读不到审计；匿名连身份都没有。"""
    headers, _ = await _make_admin(session, "auditno", super_admin=False,
                                   perms=("merchant.view",))

    for path in ("/admin-api/audit/list", "/admin-api/audit/actions"):
        denied = await client.get(path, headers=headers)
        assert denied.status_code == 403, denied.text[:200]
        assert denied.json()["code"] == 403

        anonymous = await client.get(path)
        assert anonymous.status_code == 401, anonymous.text[:200]


@pytest.mark.asyncio
async def test_audit_query_readable_with_system_audit(session, client, seeded):
    """有 system.audit 的非超管可读（运营/审计角色不必给超管）。"""
    super_headers, _ = await _make_admin(session, "auditsuper")
    merchant = seeded["merchant"]
    adjusted = await client.post(
        f"/admin-api/merchant/{merchant.id}/balance",
        json={"amount": "1.23", "remark": "审计读口用例"}, headers=super_headers,
    )
    assert adjusted.status_code == 200, adjusted.text[:300]

    headers, _ = await _make_admin(session, "auditop", super_admin=False,
                                   perms=("system.audit",))
    listed = await client.get("/admin-api/audit/list", headers=headers)
    assert listed.status_code == 200, listed.text[:200]
    items = listed.json()["data"]["items"]
    assert any(i["action"] == "merchant.balance_adjust" for i in items)

    actions = await client.get("/admin-api/audit/actions", headers=headers)
    assert actions.status_code == 200
    assert "merchant.balance_adjust" in actions.json()["data"]

    # 动作筛选确实生效（列表按 action 收敛）
    filtered = await client.get("/admin-api/audit/list",
                                params={"action": "merchant.balance_adjust"},
                                headers=headers)
    assert filtered.status_code == 200
    assert all(i["action"].startswith("merchant.balance_adjust")
               for i in filtered.json()["data"]["items"])


@pytest.mark.asyncio
async def test_rejected_operation_writes_no_audit(session, client, seeded):
    """PATCH 被白名单拒绝（422）不产生 setting.update 审计行。"""
    headers, _ = await _make_admin(session, "auditfail")
    before = (await session.exec(
        select(SystemAuditLog).where(SystemAuditLog.action == "setting.update")
    )).all()

    denied = await client.patch("/admin-api/setting",
                                json={"platform_rsa_private_key": "x"},
                                headers=headers)
    assert denied.status_code == 422

    after = (await session.exec(
        select(SystemAuditLog).where(SystemAuditLog.action == "setting.update")
    )).all()
    assert len(after) == len(before)
