"""admin 管理员账号管理（RBAC）。仅超管可用。

无权限统一抛 HTTPException(403)（B11）：早前 `return err(403, ...)` 落成
HTTP 400 + body code 403，前端 403 分支与监控统计都会漏掉。
"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete as sa_delete
from sqlmodel import col, func, select

from app.core.deps import SessionDep
from app.core.security import AdminUser, hash_password
from app.models import (
    SystemAdmin,
    SystemAdminPermission,
    SystemPermission,
)
from app.responses import err, paginate, suc

router = APIRouter()


async def require_super_admin(auth: AdminUser) -> None:
    """仅超管可用的依赖（403 真实状态码，不能落成 400）。"""
    if not auth.is_super_admin:
        raise HTTPException(status_code=403, detail={"code": 403, "message": "forbidden"})


_require_super = [Depends(require_super_admin)]


class AccountRequest(BaseModel):
    username: str
    name: str = ""
    password: str = ""
    is_forbid: bool | None = None
    permissions: list[int] | None = None


def _dump_admin(admin: SystemAdmin, permissions: list | None = None) -> dict:
    data = {
        "id": admin.id,
        "username": admin.username,
        "name": admin.name,
        "email": admin.email or "",
        "is_forbid": bool(admin.is_forbid),
        "is_super_admin": bool(admin.is_super_admin),
        "created_at": admin.created_at.strftime("%Y-%m-%d %H:%M:%S") if admin.created_at else "",
    }
    if permissions is not None:
        data["permissions"] = [p.id for p in permissions]
        data["permission_details"] = [{"id": p.id, "name": p.name, "key": p.key} for p in permissions]
    return data


async def _load_perms(session, admin_id: int) -> list[SystemPermission]:
    return list((await session.exec(
        select(SystemPermission)
        .join(SystemAdminPermission, SystemAdminPermission.permission_id == SystemPermission.id)
        .where(SystemAdminPermission.admin_id == admin_id)
    )).all())


async def _sync_permissions(session, admin_id: int, permission_ids: list[int]):
    await session.exec(sa_delete(SystemAdminPermission).where(
        SystemAdminPermission.admin_id == admin_id))
    if permission_ids:
        session.add_all([
            SystemAdminPermission(admin_id=admin_id, permission_id=pid)
            for pid in permission_ids
        ])
    await session.commit()


@router.get("/accounts", dependencies=_require_super)
async def accounts_index(session: SessionDep,
                         status: Optional[str] = Query(None),
                         keyword: Optional[str] = Query(None),
                         page: int = Query(1, ge=1), per_page: int = Query(20, ge=1, le=100)):
    query = select(SystemAdmin).where(SystemAdmin.is_super_admin == False)  # noqa: E712
    if status == "forbid":
        query = query.where(SystemAdmin.is_forbid == True)  # noqa: E712
    elif status == "normal":
        query = query.where(SystemAdmin.is_forbid == False)
    if keyword:
        query = query.where(
            (col(SystemAdmin.name).contains(keyword)) | (col(SystemAdmin.username).contains(keyword))
        )
    query = query.order_by(col(SystemAdmin.id).desc())

    total = (await session.exec(select(func.count()).select_from(query.subquery()))).one()
    items = (await session.exec(query.offset((page - 1) * per_page).limit(per_page))).all()
    result = []
    for it in items:
        perms = await _load_perms(session, it.id)
        result.append(_dump_admin(it, permissions=perms))
    return suc(paginate(items=result, total=total, page=page, per_page=per_page,
                        has_more_pages=page * per_page < total, has_pages=total > per_page))


@router.post("/accounts", dependencies=_require_super)
async def accounts_store(body: AccountRequest, session: SessionDep):
    if not body.password:
        return err(-1, "密码不能为空")
    existing = (await session.exec(
        select(SystemAdmin).where(SystemAdmin.username == body.username)
    )).first()
    if existing:
        return err(-1, "用户名已存在")
    admin = SystemAdmin(
        username=body.username,
        name=body.name or body.username,
        password=hash_password(body.password),
        is_super_admin=False,
    )
    session.add(admin)
    await session.commit()
    await session.refresh(admin)
    if body.permissions is not None:
        await _sync_permissions(session, admin.id, body.permissions)
    return suc(_dump_admin(admin, permissions=await _load_perms(session, admin.id)))


@router.put("/accounts/{item_id}", dependencies=_require_super)
async def accounts_update(item_id: int, body: AccountRequest, session: SessionDep):
    admin = await session.get(SystemAdmin, item_id)
    if not admin:
        return err(404, "记录不存在")
    if admin.is_super_admin:
        return err(-1, "不允许修改超级管理员")
    if body.name:
        admin.name = body.name
    if body.password:
        admin.password = hash_password(body.password)
    if body.is_forbid is not None:
        admin.is_forbid = body.is_forbid
    session.add(admin)
    await session.commit()
    if body.permissions is not None:
        await _sync_permissions(session, admin.id, body.permissions)
    return suc(_dump_admin(admin, permissions=await _load_perms(session, admin.id)))


@router.delete("/accounts/{item_id}", dependencies=_require_super)
async def accounts_destroy(item_id: int, session: SessionDep):
    admin = await session.get(SystemAdmin, item_id)
    if not admin:
        return err(404, "记录不存在")
    if admin.is_super_admin:
        return err(-1, "不允许删除超级管理员")
    await session.exec(sa_delete(SystemAdminPermission).where(
        SystemAdminPermission.admin_id == admin.id))
    await session.delete(admin)
    await session.commit()
    return suc("success")


@router.get("/permissions", dependencies=_require_super)
async def permissions_tree(session: SessionDep):
    all_perms = (await session.exec(
        select(SystemPermission).order_by(col(SystemPermission.id).desc())
    )).all()
    perm_children: dict[int, list[dict]] = {}
    for perm in all_perms:
        perm_children.setdefault(perm.pid, []).append({
            "id": perm.id, "pid": perm.pid, "name": perm.name, "key": perm.key,
        })
    result = [
        {**pd, "children": perm_children.get(pd["id"], [])}
        for pd in perm_children.get(0, [])
    ]
    return suc(result)
