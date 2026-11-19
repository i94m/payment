from fastapi import APIRouter, Depends

from app.core.deps import require_agent_enabled

from . import auth, common, dashboard, order

router = APIRouter()
# 登录/SSO 在 auth 路由内单独判定 agent_enable（返回业务错误文案）；业务路由统一 403
router.include_router(auth.router, prefix="/auth", tags=["agent-auth"])
router.include_router(common.router, tags=["agent-common"])
router.include_router(dashboard.router, prefix="/dashboard", tags=["agent-dashboard"],
                      dependencies=[Depends(require_agent_enabled)])
router.include_router(order.router, prefix="/order", tags=["agent-order"],
                      dependencies=[Depends(require_agent_enabled)])
