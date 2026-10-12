#!/usr/bin/env bash
# 服务器一条命令部署/升级（MySQL、Redis 均为宿主机系统服务，本脚本只管容器侧）：
# 注意：镜像内后端源码已 Cython 编译为 .so（无业务 .py），插件同样随镜像固化；
#       本脚本与 artisan.py（入口保留 .py）交互，用法完全不变。
#
#   PAY_IMAGE=registry.example.com/myns/pay-gateway:<tag> ./scripts/deploy.sh
#
# 做的事（可重复执行，幂等）：
#   1. .env 缺失时自动从 .env.example 生成并在本机写入密钥（提示先填 DB/Redis 再跑一次）
#   2. PAY_IMAGE 带仓库前缀时 docker compose pull 拉镜像
#   3. docker compose up -d（app×2 + worker）
#   4. 等待全部容器 healthy
#   5. 空库自动建表初始化；已有库自动 alembic 增量迁移
#
# 可选参数：
#   --no-pull   跳过 pull（离线/已手动加载镜像时）
#   --scale N   app 实例数（默认 compose 内置 2，N ≤ 3）
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE_FILE=${COMPOSE_FILE:-docker-compose.production.yml}

log() { printf '\033[1;36m[deploy]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[deploy]\033[0m %s\n' "$*" >&2; exit 1; }

PULL=1; SCALE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-pull) PULL=0; shift ;;
    --scale)   SCALE="${2:?--scale 需要数值}"; shift 2 ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $1（--help 查看用法）" >&2; exit 2 ;;
  esac
done

command -v docker >/dev/null || die "缺少 docker 命令"
docker info >/dev/null 2>&1 || die "docker 守护进程未运行"
[ -f "$COMPOSE_FILE" ] || die "缺少 $COMPOSE_FILE（请在仓库根目录运行）"
dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

# ---- 1. .env ----
if [ ! -f .env ]; then
  log "未发现 .env：从 .env.example 初始化，并本机生成密钥 ..."
  cp .env.example .env
  secret=$(openssl rand -hex 32)
  storage=$(openssl rand -hex 32)
  # 生产兜底：即使 .env.example 被改回 local，部署引导也强制 prod（local 会启用万能验证码 0000）
  sed -i.bak "s#^APP_SECRET_KEY=.*#APP_SECRET_KEY=${secret}#; s#^STORAGE_LOCAL_SECRET=.*#STORAGE_LOCAL_SECRET=${storage}#; s#^APP_ENV=.*#APP_ENV=prod#; s#^APP_DEBUG=.*#APP_DEBUG=false#" .env && rm -f .env.bak
  log "已生成 .env（密钥已写入，APP_ENV 已强制为 prod）。请编辑 .env 填写宿主机 MySQL（DB_DATABASE/"
  log "DB_USERNAME/DB_PASSWORD）与 Redis（REDIS_PASSWORD），确认 DB_HOST/REDIS_HOST"
  log "由 compose 覆盖为 host.docker.internal，然后重新运行本脚本。"
  exit 3
fi

# 生产防呆：存量 .env 若是 local（万能验证码 0000 放行登录），提醒但不阻断
if grep -qE '^APP_ENV=local' .env; then
  log "警告：.env 为 APP_ENV=local（万能验证码 0000 放行登录验证码），生产环境必须改为 prod"
fi

# ---- 2. 拉镜像 ----
IMAGE="${PAY_IMAGE:-pay-gateway:latest}"
if [ "$PULL" -eq 1 ] && [ "${IMAGE#*/}" != "$IMAGE" ]; then
  log "拉取镜像 ${IMAGE} ..."
  dc pull app worker
fi

# ---- 3. 起容器 ----
UP_ARGS=(-d)
[ -n "$SCALE" ] && UP_ARGS+=(--scale "app=${SCALE}")
log "启动容器（镜像 ${IMAGE}）..."
dc up "${UP_ARGS[@]}"

# ---- 4. 等全部 healthy ----
log "等待容器健康检查（最多 90s）..."
ok=0
for _ in $(seq 1 45); do
  out=$(dc ps --format '{{.Name}}|{{.Status}}' 2>/dev/null || true)
  if [ -n "$out" ] \
     && [ "$(printf '%s\n' "$out" | grep -c '|')" -ge 3 ] \
     && ! printf '%s\n' "$out" | grep -Eq 'starting|unhealthy'; then
    ok=1; break
  fi
  sleep 2
done
if [ "$ok" -ne 1 ]; then dc ps; dc logs --tail 30 app worker || true
  die "容器未在时限内全部 healthy（上面是最近日志）"; fi
log "容器已全部 healthy。"

# ---- 5. 建表 / 迁移 ----
log "检查数据库状态..."
db_state=$(dc exec -T app python - <<'PY' || die "无法连接数据库：请检查 .env 的 DB_* 配置与宿主机 MySQL 接入（见 DEPLOY.md）；Redis 报错则检查 REDIS_PASSWORD"
import asyncio
from sqlalchemy import text
from app.core.database import engine

async def main():
    async with engine.connect() as c:
        async def has(t):
            r = await c.execute(text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema=DATABASE() AND table_name=:t"), {"t": t}
            )
            return bool(r.scalar())
        orders, ver = await has("orders"), await has("alembic_version")
        print("versioned" if orders and ver
              else "unversioned" if orders else "fresh")
asyncio.run(main())
PY
)
case "$db_state" in
  fresh)
    log "空库：建表 + 初始化（管理员/支付方式/设置/权限），并纳管 alembic ..."
    dc exec -T app python artisan.py db init
    dc exec -T app python artisan.py migrate stamp head
    ;;
  unversioned)
    log "库已初始化但未纳管 alembic：标记当前版本后增量迁移 ..."
    log "（注意：若该库由旧版本代码建表，请改用 migrate stamp <对应版本> 后再 upgrade）"
    dc exec -T app python artisan.py migrate stamp head
    dc exec -T app python artisan.py migrate upgrade
    ;;
  versioned)
    log "执行增量迁移（无新版本则无操作）..."
    dc exec -T app python artisan.py migrate upgrade
    ;;
  *) die "数据库状态识别失败：$db_state" ;;
esac

# ---- 6. 汇总 ----
dc ps --format 'table {{.Name}}\t{{.Status}}'
log "完成 ✔  入口：宿主 nginx 反代 127.0.0.1:8000-8002（nginx-example.conf）；"
log "      管理后台 /admin（首次部署默认 admin/123456，登录后立即改密）；"
log "      日志：docker compose -f $COMPOSE_FILE logs -f app worker"
