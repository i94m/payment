from typing import Annotated

from fastapi import Depends, HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.cache import CacheClient, get_cache
from app.core.database import get_db

# 缓存依赖
CacheDep = Annotated[CacheClient, Depends(get_cache)]

# 数据库依赖（异步）
SessionDep = Annotated[AsyncSession, Depends(get_db)]


async def require_agent_enabled(session: SessionDep, cache: CacheDep) -> None:
    """下级代理总开关（agent_enable）依赖 —— 关闭后代理相关接口一律 403。

    菜单显隐只是展示层：商户端「下级代理」整组隐藏后，直访 URL / 直接调接口
    仍能读写代理数据，所以真正的关闭点放在接口层（商户端 agent 路由 + 代理端
    业务路由共用本依赖）。代理面板登录/SSO 在 routers/agent/auth.py 内单独
    返回业务错误文案，便于旧客户端提示。
    """
    from app.services.settings import get_setting, setting_enabled

    if not setting_enabled(await get_setting(session, "agent_enable", True, cache=cache)):
        raise HTTPException(status_code=403, detail={"code": 403, "message": "下级代理功能已关闭"})
