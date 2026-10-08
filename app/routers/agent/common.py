"""代理端公共接口。"""
import uuid

from fastapi import APIRouter

from app.core.limiter import rate_limit
from app.core.deps import CacheDep
from app.lib.captcha import generate_image_captcha
from app.responses import suc

router = APIRouter()


@router.get("/captcha")
async def captcha(cache: CacheDep, _rl=rate_limit(times=20)):
    """图片验证码。返回 {content: data URL, key}。"""
    content, code = generate_image_captcha()
    key = uuid.uuid4().hex[:26].lower()
    await cache.set(f"captcha:{key}", code, ex=300)
    return suc({"content": content, "key": key})
