from fastapi import APIRouter

from .admin import router as admin_router
from .admin.common import public_router as common_public_router
from .common import router as common_router, upload_file

routers = APIRouter()

# 管理后台（/admin-api）
routers.include_router(admin_router, prefix="/admin-api", tags=["admin"])
routers.include_router(common_router, prefix="/admin-api", tags=["common"])
# 公开端点多前缀挂载：admin/merchant/agent 三端 SPA 未登录时也可读取
routers.include_router(common_public_router, prefix="/admin-api", tags=["common"])
routers.include_router(common_public_router, prefix="/merchant-api", tags=["merchant"])
routers.include_router(common_public_router, prefix="/agent-api", tags=["agent"])

# 商户后台（/merchant-api）
from .merchant import router as merchant_router  # noqa: E402
routers.include_router(merchant_router, prefix="/merchant-api", tags=["merchant"])

# 代理面板（/agent-api）
from .agent import router as agent_router  # noqa: E402
routers.include_router(agent_router, prefix="/agent-api", tags=["agent"])

# 商户开放 API（/api 根路径资源风格）— pid+签名认证，协议差异由参数承载（sign_type 等）
from .openapi import router as openapi_router  # noqa: E402
routers.include_router(openapi_router, prefix="/api", tags=["openapi"])

# 上游回调网关（/callbacks/{plugin}/{trade_no}）— 插件验签，公开
from .callbacks import router as callbacks_router  # noqa: E402
routers.include_router(callbacks_router, prefix="/callbacks", tags=["callbacks"])

# 收银台（/cashier 页面 + 同资源 JSON）— 公开 + 限流
from .cashier import router as cashier_router  # noqa: E402
routers.include_router(cashier_router, prefix="/cashier", tags=["cashier"])

# 聚合收款页（/paypage 页面 + 建单 JSON）— 公开 + 限流
from .paypage import router as paypage_router  # noqa: E402
routers.include_router(paypage_router, prefix="/paypage", tags=["paypage"])

# 页面（/cashier 收银台 HTML）— 公开
from .web import router as web_router  # noqa: E402
routers.include_router(web_router, tags=["web"])

# 独立上传路由
upload_router = APIRouter()
upload_router.add_api_route("/upload", upload_file, methods=["POST"], tags=["upload"])
routers.include_router(upload_router)
