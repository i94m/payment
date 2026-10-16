# ---------- Stage 1: builder —— 依赖安装 + Cython 编译（源码保护） ----------
# app/ 与 plugins/ 的 .py 全部编译为机器码 .so 并删除源码，最终镜像内无业务 .py。
# gcc 只用于编译本项目代码；第三方依赖（asyncmy/cryptography/pycryptodome 等
# C/Rust 扩展）全部走 wheel 安装无需重编；python:slim 自带 CPython 头文件。
FROM python:3.12-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# 1) 运行时依赖（缓存层；--no-dev：dev 组的 pytest/playwright/aiosqlite 仅测试用，不进生产镜像）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 2) 独立编译 venv：cython + setuptools（不污染运行时 .venv，也不进 pyproject/uv.lock；
#    cython 钉到实测过的次版本保证构建可复现 —— .so 运行时不依赖 cython）
RUN uv venv /opt/cybuild \
    && uv pip install --python /opt/cybuild/bin/python "cython==3.3.*" setuptools

# 3) 源码 + 编译脚本（前端/文档产物在编译之后再 COPY：产物变更不触发 Cython 重编）
COPY scripts/compile_backend.py ./compile_backend.py
COPY app/ ./app/
COPY main.py ./artisan.py ./
COPY plugins/ ./plugins/
COPY alembic/ ./alembic/
# alembic.ini 必须随镜像：既有库升级（artisan.py migrate upgrade）在容器内执行
COPY alembic.ini ./alembic.ini

# .env.example 烤作运行时 .env（实际值经 docker-compose env_file 注入覆盖）；
# 同时供下方冒烟导入 Config：DB_HOST/DB_DATABASE/DB_USERNAME 为必填字段，
# 缺 .env 时 pydantic 直接 ValidationError（两个密钥由冒烟环境变量注入）
COPY .env.example .env

# 4) Cython 编译 app/ + plugins/ → .so，并删除 .py（脚本内含自检：失败不删源码；
#    SO_COMPILE=1 是脚本安全守卫 —— 仅容器构建编译，本地开发永远直接跑源码）
RUN SO_COMPILE=1 /opt/cybuild/bin/python compile_backend.py

# 5) 编译后构建期冒烟（坏镜像出不了 builder）：
#    先真实 import app.core.license —— 校验授权门禁 .so 本身可加载、公钥已内置
#    （构建期没有可校验的授权码，随后用进程内桩模块顶替授权门禁再 import main；
#    桩只存在于本条 RUN 的进程内，不落盘、无环境开关，运行时无法触达）
#    import main —— FastAPI include_router 即解析全部端点签名，内省损坏当场失败
#    PluginRegistry.discover() —— 插件 .so 全量可导入（find_spec 探测）
#    settings() —— SAQ worker 配置可加载（任务函数与定时任务全量）
#    必填密钥用环境变量注入（pydantic-settings 环境变量优先于 env_file）
RUN APP_SECRET_KEY=build-smoke STORAGE_LOCAL_SECRET=build-smoke \
    /app/.venv/bin/python -c "import app.core.license; \
    assert app.core.license.PUBLIC_KEY_B64, '镜像未内置授权公钥：请先在 app/core/license.py 填入 PUBLIC_KEY_B64'; \
    import sys, types; _stub = types.ModuleType('app.core.license'); \
    _stub.enforce = lambda code: None; _stub.current = None; _stub.describe = lambda: 'build-smoke'; \
    _stub.expired = lambda: False; \
    sys.modules['app.core.license'] = _stub; \
    import main, artisan; \
    from app.services.plugin import PluginRegistry; PluginRegistry.discover(); \
    from app.worker import settings; s = settings(); \
    print('routes:', len(main.app.routes), '| payment:', len(PluginRegistry._payment), \
          '| worker functions:', len(s['functions']), '| cron_jobs:', len(s['cron_jobs']))"

# 6) 守护：app/ 与 plugins/ 下不允许残留 .py（_example 插件开发模板除外），
#    main.py 已随编译清单纳入（uvicorn 字符串导入 .so），镜像内不得有明文装配源码
RUN test -z "$(find app plugins -name '*.py' -not -path 'plugins/_example/*')" \
        && test ! -f main.py \
    || { echo '残留 .py 源码，编译不完整'; exit 1; }

# 7) 前端/文档产物（置于编译与冒烟之后：其变更只重做 COPY，层缓存跳过整个编译链）
# 复制前端构建产物（vite 输出到 static/console，由 CI/build-image.sh 现场构建，不入库）
COPY static/console/ ./static/console/
# 守护：入口 HTML 缺失（前端构建不完整）直接让镜像构建失败，
# 避免 SPA 白屏/404 问题被静默打进镜像（曾实测发生过：dist 只剩 admin/agent.html）
RUN test -f static/console/index.html \
    && test -f static/console/admin.html \
    && test -f static/console/agent.html
# 对接文档 VitePress 产物（resources/docs 内 pnpm build 生成，输出到 static/docs）
COPY static/docs/ ./static/docs/
# 宣传首页（/ 由 frontend.py 直接返回该文件，settings landing_enable 关闭时 / 跳商户登录页；
# 镜像内缺失时会兜底成商户中心 SPA 跳登录）
COPY static/index.html ./static/index.html

# 创建 storage 目录结构
RUN mkdir -p storage/logs storage/public storage/exports storage/private

# ---------- Stage 2: runtime —— 只含编译产物，无业务源码 ----------
FROM python:3.12-slim AS runtime

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# venv 与编译产物分开拷：runtime 只可能有这里列出的内容，
# builder 中间层里的 .py 永远进不了最终镜像（多阶段 COPY 拷的是末态文件系统）
# venv 同基础镜像同绝对路径传递（shebang/pyvenv.cfg 有效），runtime 不需要 uv
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/app/ ./app/
COPY --from=builder /app/plugins/ ./plugins/
# main.py 已随 app/ 编译为 .so（uvicorn main:app 字符串导入照常解析）；
# 入口仅保留 artisan.py（deploy.sh 以脚本方式直接执行，无法用 .so）
COPY --from=builder /app/artisan.py ./
COPY --from=builder /app/alembic/ ./alembic/
COPY --from=builder /app/alembic.ini ./alembic.ini
COPY --from=builder /app/static/ ./static/
COPY --from=builder /app/.env ./.env

# 创建 storage 目录结构
RUN mkdir -p storage/logs storage/public storage/exports storage/private

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

# WEB_WORKERS 经 compose env_file / docker run -e 导出为容器环境变量；未配置默认 2
CMD ["/bin/sh", "-c", "mkdir -p storage/logs storage/public storage/exports storage/private && uvicorn main:app --host 0.0.0.0 --port 8000 --workers \"${WEB_WORKERS:-2}\" --access-log"]
