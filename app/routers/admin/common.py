"""admin 公共接口：用户信息 / 菜单 / 系统信息 / 改密。"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.core.config import config
from app.core.deps import CacheDep, SessionDep
from app.core.limiter import rate_limit
from app.core.security import AdminUser, PermissionChecker, hash_password, verify_password
from app.addons.storage import get_storage
from app.models import Merchant, SystemAdmin
from app.responses import err, suc

router = APIRouter()

# 公开子路由（无需登录）：挂在 /admin-api 与 /merchant-api 下，
# 供 SPA 登录页/侧边栏在未认证时读取站点展示信息
public_router = APIRouter()


@public_router.get("/site-info")
async def get_site_info(session: SessionDep, cache: CacheDep, _rl=rate_limit(times=60)):
    """站点公开信息：站点名 + 功能开关（商户端菜单显隐），登录页/侧边栏/菜单/landing 用。

    version/revision 为镜像构建期元信息（发版标签），后台侧边栏站点名下展示版本用。
    """
    from app.services.settings import build_public_features, get_all_settings
    settings = await get_all_settings(session, cache=cache)
    return suc({
        "site_name": str(settings.get("site_name") or "聚合支付"),
        "version": config.APP_VERSION,
        "revision": config.APP_REVISION,
        "features": build_public_features(settings),
    })


# ============================================================
# 存储配置 GET /storage/info（三端前缀均可访问）
# ============================================================

@public_router.get("/storage/info")
async def storage_info():
    """
    当前存储配置（前端拼文件访问地址用）

    返回 provider 与访问域名：local 时 host 为 STORAGE_LOCAL_HOST（同域相对路径），
    cos/oss 时 host 为 bucket 的访问域名。前端据此决定 key → URL 的拼装方式，
    切换 STORAGE_PROVIDER 后无需改前端代码。

    不校验登录：admin / merchant / agent 三个控制台登录后都会调用，而内容仅为
    「存储驱动 + 文件访问域名」这类公开信息（文件本身即可公开访问）。
    """
    disk = config.STORAGE_PROVIDER if config.STORAGE_PROVIDER in ('cos', 'oss') else 'local'
    info = get_storage(disk).get_config()
    return suc({
        "provider": disk,
        "host": (info.get("host") or "").rstrip("/"),
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


@router.get("/systemInfo", dependencies=[Depends(PermissionChecker("system.admin"))])
async def get_system_info(session: SessionDep):
    """系统信息（版本 + 商户/管理员数量）。

    仅超管/持 system.admin 的管理员可用：商户/管理员规模属平台运营数据（B04）。
    """
    from sqlmodel import func, select
    merchant_count = (await session.exec(select(func.count()).select_from(Merchant))).one()
    admin_count = (await session.exec(select(func.count()).select_from(SystemAdmin))).one()
    return suc({
        # 版本/修订号来自镜像构建期（Dockerfile build-arg → ENV），源码运行为 dev
        "version": config.APP_VERSION,
        "revision": config.APP_REVISION,
        "build_date": config.APP_BUILD_DATE,
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
