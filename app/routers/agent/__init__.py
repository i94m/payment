from fastapi import APIRouter

from . import auth, common, dashboard, order

router = APIRouter()
router.include_router(auth.router, prefix="/auth", tags=["agent-auth"])
router.include_router(common.router, tags=["agent-common"])
router.include_router(dashboard.router, prefix="/dashboard", tags=["agent-dashboard"])
router.include_router(order.router, prefix="/order", tags=["agent-order"])
