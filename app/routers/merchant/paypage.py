"""商户端聚合收款：设置查看/调整 + 收款链接。

收款页本体（公开）见 routers/paypage.py；random_ratelimit 为同一付款 IP
每分钟对该商户的建单上限（0=不限），即限制拉单口径。
"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.core.deps import SessionDep
from app.core.security import MerchantUser
from app.responses import err, suc
from app.services.balance import dmoney
from app.lib.linktoken import paypage_url
from app.services import paytoken
from app.services.settings import get_setting

router = APIRouter()


@router.get("/settings")
async def paypage_settings(session: SessionDep, auth: MerchantUser):
    m = auth.user
    enable = await get_setting(session, "paypage_enable", True)
    return suc({
        "enable": bool(enable),  # 平台全局开关（只读）
        "random_enable": bool(m.random_enable),
        "random_min": str(m.random_min),
        "random_max": str(m.random_max),
        "random_ratelimit": m.random_ratelimit,
        # 短码链接（/p/{code}）：不暴露 pid，按需惰性生成终身复用
        "link": paypage_url(await paytoken.get_or_create(session, m.id)),
    })


class PaypageSettingsRequest(BaseModel):
    random_enable: bool
    random_min: str = "0"
    random_max: str = "0"
    random_ratelimit: int = 0


@router.put("/settings")
async def update_paypage_settings(body: PaypageSettingsRequest, session: SessionDep,
                                  auth: MerchantUser):
    """商户自助调整随机金额与建单限流（平台全局开关只读）。"""
    m = auth.user
    try:
        rmin = dmoney(body.random_min) if body.random_min else dmoney(0)
        rmax = dmoney(body.random_max) if body.random_max else dmoney(0)
    except Exception:
        return err(-1, "金额格式错误")
    if body.random_enable:
        if rmin <= 0 or rmax <= 0:
            return err(-1, "请填写最小和最大金额")
        if rmin != rmin.to_integral_value() or rmax != rmax.to_integral_value():
            return err(-1, "随机金额仅支持整数")
        if rmin > rmax:
            rmin, rmax = rmax, rmin
    m.random_enable = body.random_enable
    m.random_min = rmin
    m.random_max = rmax
    m.random_ratelimit = max(int(body.random_ratelimit or 0), 0)
    session.add(m)
    await session.commit()
    return suc("success")
