"""admin 路由装配。

鉴权口径（fail-closed）：
- 只有 /admin-api/auth/*（登录）是公开的；其余端点一律先过 admin 身份依赖
  （`get_current_admin`）——新增端点即使忘记挂 `PermissionChecker`，也不会
  变成匿名可访问（原来完全靠每个端点自觉，新增即 fail-open）。
- 细粒度权限仍由各端点的 `PermissionChecker("xxx.view")` 判定（超管放行）。
"""
from fastapi import APIRouter, Depends

from app.core.security import PermissionChecker, get_current_admin

from . import (
    auth, common, order, merchant, channel, settle,
    settings, dashboard, notifylog, permission, announce, record, queue, message,
    audit,
)

router = APIRouter()

# 登录/登出等认证端点自带 AdminUser 依赖，登录必须保持公开
router.include_router(auth.router, prefix="/auth", tags=["admin-auth"])

# 其余管理端点：统一先验身份（FastAPI 按请求缓存依赖，不会重复查库）
protected = APIRouter(dependencies=[Depends(get_current_admin)])
protected.include_router(common.router, prefix="/common", tags=["admin-common"])
protected.include_router(order.router, prefix="/order", tags=["admin-order"])
protected.include_router(merchant.router, prefix="/merchant", tags=["admin-merchant"])
protected.include_router(record.router, prefix="/record", tags=["admin-record"])
protected.include_router(channel.router, prefix="/pay", tags=["admin-pay"])
protected.include_router(settle.router, prefix="/settle", tags=["admin-settle"])
protected.include_router(announce.router, prefix="/announce", tags=["admin-announce"])
protected.include_router(settings.router, prefix="/setting", tags=["admin-setting"])
protected.include_router(dashboard.router, prefix="/dashboard", tags=["admin-dashboard"])
protected.include_router(notifylog.router, prefix="/notifylog", tags=["admin-notifylog"])
protected.include_router(permission.router, prefix="/permission", tags=["admin-permission"])
protected.include_router(queue.router, prefix="/queue", tags=["admin-queue"])
protected.include_router(message.router, prefix="/message", tags=["admin-message"])
# 操作审计（P1-7）：与其余管理端点同口径，先验身份再按 system.audit 判权
protected.include_router(audit.router, prefix="/audit", tags=["admin-audit"])

# 老后台首页入口 /admin-api/index 兼容别名：只保留「首页总览」一个端点，
# 不再把整个 dashboard 路由组重复挂一份（原来 /index/payroute、/index/config
# 等写接口都被复制了一套，路由表与审计口径都翻倍）。
router.add_api_route(
    "/index", dashboard.index, methods=["GET"], include_in_schema=False,
    dependencies=[Depends(PermissionChecker("dashboard.view"))],
)

router.include_router(protected)
