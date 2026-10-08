"""admin 公共接口：用户信息 / 菜单 / 系统信息 / 改密。"""
from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.deps import CacheDep, SessionDep
from app.core.security import AdminUser, hash_password, verify_password
from app.models import Merchant, SystemAdmin
from app.responses import err, suc

router = APIRouter()

# 公开子路由（无需登录）：挂在 /admin-api 与 /merchant-api 下，
# 供 SPA 登录页/侧边栏在未认证时读取站点展示信息
public_router = APIRouter()


@public_router.get("/site-info")
async def get_site_info(session: SessionDep, cache: CacheDep):
    """站点公开信息：站点名 + 功能开关（商户端菜单显隐），登录页/侧边栏/菜单用。"""
    from app.services.settings import build_public_features, get_all_settings
    settings = await get_all_settings(session, cache=cache)
    return suc({
        "site_name": str(settings.get("site_name") or "聚合支付"),
        "features": build_public_features(settings),
    })


@router.get("/userInfo")
async def get_user_info(auth: AdminUser):
    user = auth.user
    return suc({
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "super": bool(user.is_super_admin),
    })


@router.get("/menuList")
async def get_menu_list(auth: AdminUser, session: SessionDep):
    from app.constants.permissions import MENU_TREE
    if auth.is_super_admin:
        return suc(MENU_TREE)
    keys = await auth.get_permission_keys(session)
    filtered = _filter_menu(MENU_TREE, keys)
    return suc(filtered)


@router.get("/systemInfo")
async def get_system_info(auth: AdminUser, session: SessionDep):
    from sqlmodel import func, select
    merchant_count = (await session.exec(select(func.count()).select_from(Merchant))).one()
    admin_count = (await session.exec(select(func.count()).select_from(SystemAdmin))).one()
    return suc({
        "version": "1.0.0",
        "framework": "FastAPI",
        "merchant_count": merchant_count,
        "admin_count": admin_count,
    })


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/changePassword")
async def change_password(body: ChangePasswordRequest, auth: AdminUser, session: SessionDep):
    user = auth.user
    if not verify_password(body.old_password, user.password):
        return err(-1, "原密码错误")
    user.password = hash_password(body.new_password)
    session.add(user)
    await session.commit()
    return suc("密码修改成功")


def _filter_menu(menus: list, keys: set) -> list:
    result = []
    for item in menus:
        if item.get("key") and item["key"] not in keys:
            continue
        children = item.get("children", [])
        if children:
            filtered_children = _filter_menu(children, keys)
            if filtered_children:
                result.append({**item, "children": filtered_children})
        else:
            result.append(item)
    return result
