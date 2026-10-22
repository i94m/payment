import html
import os
import re

from fastapi import FastAPI, HTTPException
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.staticfiles import StaticFiles

from app.core.config import config
from app.core.deps import SessionDep
from app.core.modules import MODULES

# 管理后台页面路径前缀保留字：页面路径改跑这些前缀会撞路由/静态挂载/文档
_RESERVED_PREFIXES = {
    "admin", "merchant", "agent", "docs", "api", "storage", "static",
    "assets", "images", "css", "audio", "paypage", "cashier",
    "orders", "callbacks", "upload", "health", "openapi",
    "admin-api", "merchant-api", "agent-api",
}


def _admin_prefix() -> str:
    """管理后台页面路径前缀（ADMIN_PATH_PREFIX，默认 admin，见 config.py）。

    仅约束页面路径：小写字母/数字/连字符，且不得与保留前缀冲突；
    非法或冲突时静默回退 admin（入口隐藏靠改值，不靠报错）。
    """
    raw = (config.ADMIN_PATH_PREFIX or "").strip().lower().strip("/")
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", raw) and raw not in _RESERVED_PREFIXES:
        return raw
    return "admin"

_DOCS_NOT_FOUND_TIP = (
    "对接文档未构建：请在 resources/docs 目录执行 pnpm install && pnpm build "
    "生成 static/docs（构建后无需重启即可访问）"
)


def _docs_dist() -> str:
    """VitePress 产物目录（独立函数便于测试 monkeypatch 模拟未构建）。"""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base_dir, "static", "docs")


class _DocsStaticFiles(StaticFiles):
    """文档静态托管：dist 缺失或路径未命中时返回构建提示（而非裸 404）。

    check_dir=False：挂载时不要求 dist 已存在，服务启动后再执行
    pnpm build 即可立即访问，无需重启。
    """

    def __init__(self, directory: str, tip: str):
        super().__init__(directory=directory, html=True, check_dir=False)
        self._tip = tip

    async def check_config(self) -> None:
        """跳过父类首次请求时的目录存在性校验（dist 允许构建前不存在）。"""
        self.config_checked = True

    async def get_response(self, path: str, scope):
        # 无独立文档首页：/docs/（StaticFiles 收到规范化后的 "."）与 /docs/index.html
        # 统一跳转协议规则页（商户对接流程起点）
        normalized = path.strip("/")
        if normalized in ("", ".", "index.html"):
            return RedirectResponse(url="/docs/guide/protocol.html", status_code=307)
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:  # starlette 抛的是父类，fastapi.HTTPException 捕不到
            if exc.status_code == 404:
                return JSONResponse({"code": 404, "message": self._tip}, status_code=404)
            raise


def register_docs(app: FastAPI) -> None:
    """公开托管 VitePress 对接文档（/docs，无需登录）。

    静态产物位于 static/docs（resources/docs 为独立文档模块）。
    必须在 register_frontend 之前注册：SPA 兜底路由是全量 path 通配，
    后注册的 mount 永远轮不到。挂载不校验 dist 是否已存在（check_dir=False），
    未构建时请求 /docs 返回构建提示（见 _DocsStaticFiles），
    构建完成后无需重启即可访问。
    """
    app.mount(
        "/docs",
        _DocsStaticFiles(_docs_dist(), _DOCS_NOT_FOUND_TIP),
        name="docs",
    )


def _console_dist() -> str:
    """console 产物目录（独立函数便于测试 monkeypatch 模拟未构建/桩目录）。"""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base_dir, "static", "console")


def register_frontend(app: FastAPI) -> None:
    """注册前端静态资源 + SPA fallback。static/console 不存在时自动跳过。"""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    frontend_dir = _console_dist()
    landing_path = os.path.join(base_dir, "static", "index.html")
    # 管理后台页面路径前缀（ADMIN_PATH_PREFIX 可配，默认 admin；调用期读取便于测试）
    admin_prefix = _admin_prefix()

    if not os.path.exists(frontend_dir):
        return

    def _entry(name: str) -> FileResponse | JSONResponse:
        path = os.path.join(frontend_dir, name)
        if os.path.exists(path):
            # 入口 HTML 必须每次回源校验（304 便宜）：不带 Cache-Control 时浏览器
            # 会启发式缓存旧页面，发布新版后用户仍看到旧入口；资源文件名带 hash 不受影响
            return FileResponse(path, headers={"Cache-Control": "no-cache"})
        return JSONResponse({"code": 404, "message": "Not Found"}, status_code=404)

    # 挂载静态资源子目录
    for subdir in ["assets", "css", "images", "audio"]:
        dir_path = os.path.join(frontend_dir, subdir)
        if os.path.exists(dir_path):
            app.mount(f"/{subdir}", StaticFiles(directory=dir_path), name=f"frontend-{subdir}")
            # /{admin_prefix}、/agent、/merchant 深链下相对引用 ./assets 解析到对应子路径，各双挂一份
            # （vite base="./"，SPA fallback 又支持 /merchant 旧前缀，漏挂会导致该路径下白屏）
            app.mount(f"/{admin_prefix}/{subdir}", StaticFiles(directory=dir_path), name=f"frontend-admin-{subdir}")
            app.mount(f"/agent/{subdir}", StaticFiles(directory=dir_path), name=f"frontend-agent-{subdir}")
            app.mount(f"/merchant/{subdir}", StaticFiles(directory=dir_path), name=f"frontend-merchant-{subdir}")

    # 入口页：/ 宣传首页（static/index.html，缺失时兜底商户中心；landing_enable 关闭时
    # 返回居中站点标题 + 商户登录链接的极简页，不跳转）、/{admin_prefix}/ 总后台（admin.html）、
    # /agent/ 代理面板（agent.html）、/merchant/ 商户中心（index.html）。
    # 无斜杠路径 307 规范化到带斜杠：URL 唯一（收藏/日志/Referer 一致），
    # 且 vite base="./" 的相对资源引用只按带斜杠目录解析一种口径。
    # 注意：SPA fallback 是全量通配，Starlette 自动的 redirect_slashes 永远轮不到，
    # 必须显式注册无斜杠跳转路由。
    def _landing() -> FileResponse | JSONResponse:
        if os.path.exists(landing_path):
            return FileResponse(landing_path, headers={"Cache-Control": "no-cache"})
        return _entry("index.html")

    def _site_title_page(site_name: str) -> HTMLResponse:
        """landing_enable 关闭时的极简占位页：站点标题（settings 的 site_name）居中，
        下方附商户登录入口。链接指向商户中心 /merchant/（未登录由前端守卫自动
        带到 #/login，已登录直达工作台），故只挂链接不做 307。"""
        name = html.escape(site_name)
        return HTMLResponse(
            f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}</title>
<style>
  html, body {{ margin: 0; height: 100%; }}
  body {{ display: flex; flex-direction: column; align-items: center; justify-content: center;
         gap: 20px; font: 500 28px/1.4 system-ui, "PingFang SC", "Microsoft YaHei", sans-serif;
         color: #1f2329; background: #fff; }}
  body a {{ font-size: 15px; color: #2563eb; text-decoration: none; }}
  body a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>{name}<a href="/merchant/">商户登录</a></body>
</html>""",
            headers={"Cache-Control": "no-cache"},
        )

    def _slash_pair(path: str, entry: str):
        """注册 /{path} → 307 /{path}/ 与 /{path}/ → 入口页 的成对路由。"""

        @app.get(path)
        async def entry_redirect():
            return RedirectResponse(url=f"{path}/", status_code=307)

        @app.get(f"{path}/")
        async def entry_page():
            return _entry(entry)

    @app.get("/")
    async def home(session: SessionDep):
        # 宣传首页开关（settings 表 landing_enable，默认开）。直接查库不走 60s 缓存
        # （对齐 admin 设置页读法）：/ 是高频公开入口，不挂 CacheDep，避免 Redis
        # 抖动把静态首页打成 500；读配置异常时按开启兜底，首页保持可用。
        from app.services.settings import get_setting, setting_enabled
        try:
            enabled = setting_enabled(await get_setting(session, "landing_enable"))
        except Exception:
            enabled = True
        if not enabled:
            # 关闭宣传首页：不跳转，改居中展示站点标题 + 商户登录入口的极简页
            try:
                site_name = str(await get_setting(session, "site_name") or "聚合支付")
            except Exception:
                site_name = "聚合支付"
            return _site_title_page(site_name)
        return _landing()

    _slash_pair("/merchant", "index.html")
    _slash_pair(f"/{admin_prefix}", "admin.html")
    _slash_pair("/agent", "agent.html")

    # SPA fallback
    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        # API/回调路径不做 SPA 兜底
        api_prefixes = tuple(
            m.api_prefix.strip("/") + "/" for m in MODULES.values()
        ) + ("api/", "openapi/", "callbacks/", "orders/", "upload", "health")
        if full_path.startswith(api_prefixes):
            raise HTTPException(status_code=404, detail="Not Found")
        # 对接文档：裸 /docs 不会被 Mount（"/docs"）匹配，补一个到 /docs/ 的重定向；
        # /docs/ 与子路径正常由 docs mount 接管（含未构建时的构建提示 404），
        # 此分支仅作为 mount 未注册时的兜底防御
        if full_path == "docs":
            return RedirectResponse(url="/docs/", status_code=307)
        if full_path.startswith("docs/"):
            raise HTTPException(status_code=404, detail=_DOCS_NOT_FOUND_TIP)
        # 带扩展名的路径（如 /foo.js）视为静态资源请求，不存在即 404，
        # 避免 SPA 兜底掩盖无效路径
        last_segment = full_path.rsplit("/", 1)[-1]
        if "." in last_segment:
            raise HTTPException(status_code=404, detail="Not Found")
        # 总后台/代理面板前端（/{admin_prefix}、/agent 深链刷新）；其余（含 /merchant 及其子路径，
        # 商户中心入口在 /merchant）兜底为商户中心
        if full_path == admin_prefix or full_path.startswith(admin_prefix + "/"):
            return _entry("admin.html")
        if full_path == "agent" or full_path.startswith("agent/"):
            return _entry("agent.html")
        return _entry("index.html")
