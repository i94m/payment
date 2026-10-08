from fastapi import APIRouter
from . import (
    auth, common, dashboard, order, account, record, settle,
    agent, message, paypage,
)

router = APIRouter()
router.include_router(auth.router, prefix="/auth", tags=["merchant-auth"])
router.include_router(common.router, tags=["merchant-common"])
router.include_router(dashboard.router, prefix="/dashboard", tags=["merchant-dashboard"])
router.include_router(order.router, prefix="/order", tags=["merchant-order"])
router.include_router(account.router, prefix="/account", tags=["merchant-account"])
router.include_router(record.router, prefix="/record", tags=["merchant-record"])
router.include_router(settle.router, prefix="/settle", tags=["merchant-settle"])
router.include_router(agent.router, prefix="/agent", tags=["merchant-agent"])
router.include_router(paypage.router, prefix="/paypage", tags=["merchant-paypage"])
router.include_router(message.router, prefix="/message", tags=["merchant-message"])

# 对接文档已迁移为独立 VitePress 站点（resources/docs），
# 由 app.core.frontend.register_docs 公开托管在 /docs，支持未登录查看。
