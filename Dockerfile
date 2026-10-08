FROM python:3.12-slim

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH"

# 先复制依赖文件，命中缓存（--no-dev：dev 组的 pytest/playwright/aiosqlite 仅测试用，不进生产镜像）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 复制后端代码
COPY app/ ./app/
COPY main.py ./artisan.py ./
COPY plugins/ ./plugins/
COPY alembic/ ./alembic/
# alembic.ini 必须随镜像：既有库升级（artisan.py migrate upgrade）在容器内执行
COPY alembic.ini ./alembic.ini

# 复制前端构建产物（vite 输出到 static/frontend，由 CI/build-image.sh 现场构建，不入库）
COPY static/frontend/ ./static/frontend/
# 守护：入口 HTML 缺失（前端构建不完整）直接让镜像构建失败，
# 避免 SPA 白屏/404 问题被静默打进镜像（曾实测发生过：dist 只剩 admin/agent.html）
RUN test -f static/frontend/index.html \
    && test -f static/frontend/admin.html \
    && test -f static/frontend/agent.html
# 对接文档 VitePress 产物（resources/docs 内 pnpm build 生成，输出到 static/docs；compose 另有 ro 挂载可免重建镜像热更新）
COPY static/docs/ ./static/docs/
# 宣传首页（/landing 由 frontend.py 直接返回该文件，镜像内缺失时会兜底成商户中心 SPA 跳登录）
COPY static/index.html ./static/index.html

# 复制 .env.example 作为运行时 .env（实际值通过 docker-compose env_file 注入）
COPY .env.example .env

# 创建 storage 目录结构
RUN mkdir -p storage/logs storage/public storage/exports storage/private

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["/bin/sh", "-c", "mkdir -p storage/logs storage/public storage/exports storage/private && uvicorn main:app --host 0.0.0.0 --port 8000 --workers 2 --access-log"]
