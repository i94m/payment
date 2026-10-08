"""前端静态托管：/docs 未构建时显式 404 提示，不被 SPA 兜底成后台页面。"""
import httpx
import pytest
from fastapi import FastAPI

from app.core import frontend
from app.core.frontend import register_docs, register_frontend

# 与 register_docs 同源的根目录推导（app/core/frontend.py → 上三级为项目根）
PROJECT_ROOT = frontend.os.path.dirname(frontend.os.path.dirname(frontend.os.path.dirname(
    frontend.os.path.abspath(frontend.__file__))))
DOCS_DIST = frontend.os.path.join(PROJECT_ROOT, "static", "docs")
FRONTEND_ENTRY = frontend.os.path.join(PROJECT_ROOT, "static", "frontend", "index.html")


def _build_app() -> FastAPI:
    # 与 main.py 一致：关闭内置 Swagger/Re-doc/OpenAPI，/docs 让给文档站
    return FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


def _registered(app: FastAPI) -> FastAPI:
    register_docs(app)
    register_frontend(app)
    return app


async def _get(app: FastAPI, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        return await c.get(path)


def _location_path(r: httpx.Response) -> str:
    """重定向 Location 的路径部分：Starlette redirect_slashes 生成绝对地址
    （http://host/docs/），自定义兜底重定向生成相对地址（/docs/），二者等价。"""
    from urllib.parse import urlparse

    return urlparse(r.headers["location"]).path


@pytest.mark.skipif(not frontend.os.path.exists(DOCS_DIST), reason="docs 未构建")
async def test_docs_served_when_built():
    """dist 存在时 /docs 由 mount 接管，文档首页统一跳转协议规则页（无独立首页）。

    裸 /docs 不被 Mount 匹配，由 SPA 兜底 307 到 /docs/；/docs/ 再由
    _DocsStaticFiles 307 到 /docs/guide/protocol.html。
    """
    app = _registered(_build_app())
    r = await _get(app, "/docs")
    assert r.status_code == 307, (r.status_code, r.text[:120])
    assert _location_path(r) == "/docs/"

    r = await _get(app, "/docs/")
    assert r.status_code == 307, (r.status_code, r.text[:120])
    assert _location_path(r) == "/docs/guide/protocol.html"

    r = await _get(app, "/docs/guide/protocol.html")
    assert r.status_code == 200, (r.status_code, r.text[:120])
    assert "text/html" in r.headers["content-type"]
    # 内置 Swagger 已关闭，/docs 不应出现 Swagger UI
    assert "swagger-ui" not in r.text


async def test_docs_404_hint_when_not_built(monkeypatch):
    """dist 缺失时 /docs/ 显式 404 并附构建提示，其余路径 SPA 兜底不受影响。"""
    if not frontend.os.path.exists(FRONTEND_ENTRY):
        pytest.skip("前端未构建（static/frontend 缺失）")
    # 指向一个必然不存在的目录，模拟"未构建"（请求时文件查找失败 → 构建提示）
    monkeypatch.setattr(frontend, "_docs_dist",
                        lambda: frontend.os.path.join(PROJECT_ROOT, "static", "docs_missing"))
    app = _registered(_build_app())

    # 裸 /docs 先 307 到 /docs/（绝对/相对地址均可）；/docs/ 无独立首页再 307 到协议页，
    # 未构建时最终在协议页路径上给出 404 提示
    r = await _get(app, "/docs")
    assert r.status_code == 307 and _location_path(r) == "/docs/", r.status_code
    r = await _get(app, "/docs/")
    assert r.status_code == 307 and _location_path(r) == "/docs/guide/protocol.html", r.status_code
    for path in ("/docs/guide/protocol.html", "/docs/guide/protocol.md"):
        r = await _get(app, path)
        assert r.status_code == 404, (path, r.status_code)
        assert "pnpm install" in r.text, (path, r.text[:200])

    # 前缀相近但非 /docs 的路径仍走 SPA 兜底
    r = await _get(app, "/docsq")
    assert r.status_code == 200
    # 双端入口不受影响：/ 商户中心、/admin 总后台
    for path in ("/", "/admin"):
        r = await _get(app, path)
        assert r.status_code == 200, path


async def test_builtin_docs_ui_disabled():
    """FastAPI 内置 Swagger/OpenAPI 已关闭，不与 /docs 抢路由、不对外暴露 Schema。"""
    from main import app

    for path in ("/docs", "/redoc", "/openapi.json"):
        r = await _get(app, path)
        assert "swagger-ui" not in r.text.lower(), path
        if path == "/openapi.json":
            assert r.status_code == 404, (r.status_code, r.text[:120])


@pytest.mark.skipif(not frontend.os.path.exists(FRONTEND_ENTRY), reason="前端未构建")
async def test_admin_prefix_configurable(monkeypatch):
    """ADMIN_PATH_PREFIX 自定义后：/{prefix} 服务总后台与资源深链，/admin 不再注册。

    非法/保留值（如 docs）静默回退 admin，且不遮蔽文档路由。
    """
    from app.core.config import config
    from starlette.routing import Mount

    monkeypatch.setattr(config, "ADMIN_PATH_PREFIX", "console-9x")
    app = _registered(_build_app())
    r = await _get(app, "/console-9x")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    r = await _get(app, "/console-9x/deep/link")
    assert r.status_code == 200
    mounted = {route.path for route in app.routes if isinstance(route, Mount)}
    assert "/console-9x/assets" in mounted
    assert "/admin" not in mounted

    monkeypatch.setattr(config, "ADMIN_PATH_PREFIX", "docs")  # 保留字 → 回退 admin
    app2 = _registered(_build_app())
    r = await _get(app2, "/admin")
    assert r.status_code == 200
    r = await _get(app2, "/docs")
    assert r.status_code == 307  # 文档路由未被遮蔽
