from typing import Annotated

from fastapi import Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.cache import CacheClient, get_cache
from app.core.database import get_db

# 缓存依赖
CacheDep = Annotated[CacheClient, Depends(get_cache)]

# 数据库依赖（异步）
SessionDep = Annotated[AsyncSession, Depends(get_db)]
