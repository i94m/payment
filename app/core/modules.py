"""多模块配置 — 支持多个独立后台（admin / merchant）

每个模块有独立的：
- API 前缀（/admin-api, /merchant-api）
- JWT guard 名（admin, merchant）
- 认证模型（SystemAdmin 或自定义模型）
- Token 有效期

新增模块步骤：
1. 在 MODULES 中添加配置
2. 创建 app/routers/{module}/ 目录
3. 在 app/routers/__init__.py 中注册路由
4. 创建前端入口 HTML + resources/backend/apps/{module}/ 目录
5. security.py 中注册 guard 依赖
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModuleConfig:
    """后台模块配置"""
    name: str                           # 模块名，如 "admin"
    display_name: str                   # 显示名，如 "管理后台"
    path: str = ""                      # URL 路径前缀，如 "admin"（前端 hash 路由用）
    api_prefix: str = "/admin-api"      # API 路由前缀
    guard: str = "admin"                # JWT guard 标识
    model: type | None = None           # 认证模型类（延迟导入）
    token_ttl_minutes: int = 60 * 24 * 8  # Token 有效期（分钟）
    # 前端自定义配置，会注入到 window.GLOBAL
    extra_global: dict[str, Any] = field(default_factory=dict)


# ============================================================
# 模块注册表 — 在此添加新模块
# ============================================================

MODULES: dict[str, ModuleConfig] = {
    "admin": ModuleConfig(
        name="admin",
        display_name="管理后台",
        path="admin",
        api_prefix="/admin-api",
        guard="admin",
    ),
    "merchant": ModuleConfig(
        name="merchant",
        display_name="商户后台",
        path="",
        api_prefix="/merchant-api",
        guard="merchant",
    ),
    "agent": ModuleConfig(
        name="agent",
        display_name="代理面板",
        path="agent",
        api_prefix="/agent-api",
        guard="agent",
    ),
}


def get_module(name: str) -> ModuleConfig | None:
    return MODULES.get(name)


def get_module_by_guard(guard: str) -> ModuleConfig | None:
    for m in MODULES.values():
        if m.guard == guard:
            return m
    return None
