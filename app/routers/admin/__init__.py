from fastapi import APIRouter
from . import (
    auth, common, order, merchant, channel, settle,
    settings, dashboard, notifylog, permission, announce, record, queue, message,
    audit,
)

router = APIRouter()
router.include_router(auth.router, prefix="/auth", tags=["admin-auth"])
router.include_router(common.router, prefix="/common", tags=["admin-common"])
router.include_router(order.router, prefix="/order", tags=["admin-order"])
router.include_router(merchant.router, prefix="/merchant", tags=["admin-merchant"])
router.include_router(record.router, prefix="/record", tags=["admin-record"])
router.include_router(channel.router, prefix="/pay", tags=["admin-pay"])
router.include_router(settle.router, prefix="/settle", tags=["admin-settle"])
router.include_router(announce.router, prefix="/announce", tags=["admin-announce"])
router.include_router(settings.router, prefix="/setting", tags=["admin-setting"])
router.include_router(dashboard.router, prefix="/dashboard", tags=["admin-dashboard"])
router.include_router(dashboard.router, prefix="/index", include_in_schema=False)
router.include_router(notifylog.router, prefix="/notifylog", tags=["admin-notifylog"])
router.include_router(permission.router, prefix="/permission", tags=["admin-permission"])
router.include_router(queue.router, prefix="/queue", tags=["admin-queue"])
router.include_router(message.router, prefix="/message", tags=["admin-message"])
router.include_router(audit.router, prefix="/audit", tags=["admin-audit"])
