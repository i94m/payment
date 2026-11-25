#!/usr/bin/env bash
# 服务器一条命令部署/升级（MySQL、Redis 均为宿主机系统服务，本脚本只管容器侧）：
# 注意：镜像内后端源码已 Cython 编译为 .so（无业务 .py），插件同样随镜像固化；
#       本脚本与 artisan.py（入口保留 .py）交互，用法完全不变。
#
#   PAY_IMAGE=registry.example.com/myns/pay-gateway:<tag> ./scripts/deploy.sh
#
# 做的事（可重复执行，幂等）：
#   1. .env 缺失时从 .env.example 复制并退出，列出必填项（密钥不自动生成，缺配直接报错）
#   2. PAY_IMAGE 带仓库前缀时 docker compose pull 拉镜像
#   3. docker compose up -d（app×N + worker，N 取 .env 的 APP_REPLICAS，默认 3）
#   4. 等待全部容器 healthy（期望容器数按「app 实例数 + worker」计算）
#   5. 空库自动建表初始化；已有库自动 alembic 增量迁移
#
# 版本升级（在线查询镜像仓库可用版本，自动改 .env 的 PAY_IMAGE 后走同一套流程）：
#   ./scripts/deploy.sh upgrade             # 升到最新版本（交互确认）
#   ./scripts/deploy.sh upgrade 1.2.3       # 升级/回滚到指定版本
#   ./scripts/deploy.sh upgrade --check     # 只看当前版本与可用版本
#   ./scripts/deploy.sh upgrade --yes       # 免确认（脚本/定时用）
#
# 可选参数（非 upgrade 模式）：
#   --no-pull   跳过 pull（离线/已手动加载镜像时）
#   --scale N   app 实例数（覆盖 .env 的 APP_REPLICAS；N ≤ 端口范围大小，默认 3 个端口）
set -euo pipefail

cd "$(dirname "$0")/.."
# 编排文件：源码仓库用 docker-compose.production.yml；客户部署包内命名为 docker-compose.yml
if [ -z "${COMPOSE_FILE:-}" ]; then
  if [ -f docker-compose.production.yml ]; then
    COMPOSE_FILE=docker-compose.production.yml
  else
    COMPOSE_FILE=docker-compose.yml
  fi
fi

log() { printf '\033[1;36m[deploy]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[deploy]\033[0m %s\n' "$*" >&2; exit 1; }

# ---- 参数解析：upgrade 子命令 或 普通部署参数 ----
PULL=1; SCALE=""
UPGRADE_MODE=0; UPGRADE_CHECK=0; UPGRADE_YES=0; UPGRADE_VERSION=""
if [ "${1:-}" = "upgrade" ]; then
  UPGRADE_MODE=1; shift
  while [ $# -gt 0 ]; do
    case "$1" in
      --check)    UPGRADE_CHECK=1; shift ;;
      --yes|-y)   UPGRADE_YES=1; shift ;;
      -h|--help)  grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
      -*)         die "未知参数: $1（--check / --yes / 版本号）" ;;
      *)  [ -n "$UPGRADE_VERSION" ] && die "只能指定一个版本号"
          UPGRADE_VERSION="$1"; shift ;;
    esac
  done
else
  while [ $# -gt 0 ]; do
    case "$1" in
      --no-pull) PULL=0; shift ;;
      --scale)   SCALE="${2:?--scale 需要数值}"; shift 2 ;;
      -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
      *) echo "未知参数: $1（--help 查看用法）" >&2; exit 2 ;;
    esac
  done
fi

command -v docker >/dev/null || die "缺少 docker 命令"
docker info >/dev/null 2>&1 || die "docker 守护进程未运行"
[ -f "$COMPOSE_FILE" ] || die "缺少 $COMPOSE_FILE（请在仓库根目录运行）"
dc() { docker compose -f "$COMPOSE_FILE" "$@"; }

# ---- 1. .env ----
if [ ! -f .env ]; then
  log "未发现 .env：已从 .env.example 复制，请填写以下必填项后重新运行本脚本 ..."
  cp .env.example .env
  log "必填项（密钥一律手动生成，脚本不自动写入）："
  log "  APP_SECRET_KEY：openssl rand -hex 32 生成（或 uv run python artisan.py key generate）"
  log "  STORAGE_LOCAL_SECRET：可选，不填默认取 APP_SECRET_KEY；要独立密钥则 openssl rand -hex 32 另生成"
  log "  AUTH_CODE：向作者获取授权码填入"
  log "  DB_DATABASE / DB_USERNAME / DB_PASSWORD：宿主机 MySQL 接入"
  log "  REDIS_PASSWORD：宿主机 Redis 密码"
  log "另确认 APP_ENV=prod（local 会启用万能验证码 0000）。"
  exit 3
fi

# 取 .env 中某键的值（去引号/空白；多行取最后一条）
env_val() { sed -n "s/^$1=//p" .env | tail -1 | tr -d '"'"'"' '; }

# 密钥预检：不自动生成，缺配直接报错（APP_SECRET_KEY 兼作通道凭证加密根密钥，
# 自动生成会让运维误以为已正确配置，换钥/漏配事故难以察觉；STORAGE_LOCAL_SECRET
# 不填由应用侧回退到 APP_SECRET_KEY，不在此强制）
[ -n "$(env_val "APP_SECRET_KEY")" ] || die "APP_SECRET_KEY 未配置：请生成后填入 .env（openssl rand -hex 32），再重新运行本脚本"

# 授权码预检：缺码不阻断部署（服务会以未授权模式运行：页面返回纯文本「Invalid License Code」，
# 业务接口 503，/health 正常），仅提醒，拿到授权码填入重启即可解除
if ! grep -qE '^AUTH_CODE=..' .env; then
  log "警告：未配置授权码，服务将以未授权模式运行（页面返回纯文本「Invalid License Code」，业务接口不可用）"
  log "      向作者获取授权码填入 .env 的 AUTH_CODE= 后重新运行本脚本即可解除"
fi

# 生产防呆：存量 .env 若是 local（万能验证码 0000 放行登录），提醒但不阻断
if grep -qE '^APP_ENV=local' .env; then
  log "警告：.env 为 APP_ENV=local（万能验证码 0000 放行登录验证码），生产环境必须改为 prod"
fi

# ---- 1b. upgrade 子命令：在线查版本 → 改 .env 的 PAY_IMAGE → 落入下方同一套部署流程 ----
if [ "$UPGRADE_MODE" -eq 1 ]; then
  # 当前版本：优先运行中 app 容器的镜像标签（最真实），回落到 .env 的 PAY_IMAGE 标签
  CUR_VERSION=""
  cid=$(dc ps -q app 2>/dev/null | head -1 || true)
  if [ -n "$cid" ]; then
    CUR_VERSION=$(docker inspect -f '{{index .Config.Labels "org.opencontainers.image.version"}}' "$cid" 2>/dev/null || true)
  fi
  if [ -z "$CUR_VERSION" ] || [ "$CUR_VERSION" = "dev" ]; then
    base_ref=$(env_val "PAY_IMAGE")
    case "$base_ref" in
      *:*) CUR_VERSION="${base_ref##*:}" ;;
      *)   CUR_VERSION="unknown" ;;
    esac
  fi

  # 可用版本列表优先走应用内查询（容器里有 python + httpx，服务器无需另装）。
  # 老版本镜像里还没有 artisan upgrade 命令时，退回纯 shell 查询 registry API。
  BASE_IMAGE=$(env_val "PAY_IMAGE"); [ -n "$BASE_IMAGE" ] || BASE_IMAGE="ghcr.io/i94m/payment:latest"
  REPO_REF="${BASE_IMAGE%:*}"
  VERSIONS_OUT=$(dc exec -T --index 1 app python artisan.py upgrade versions 2>/dev/null) || VERSIONS_OUT=""
  if [ -z "$VERSIONS_OUT" ]; then
    log "容器内版本查询不可用（老版本镜像？），改用 registry API 直查 ..."
    command -v curl >/dev/null || die "缺少 curl 命令，无法查询版本"
    reg_host="${REPO_REF%%/*}"
    case "$reg_host" in
      *.*|localhost) repo_path="${REPO_REF#*/}" ;;
      *)             reg_host=""; repo_path="$REPO_REF" ;;
    esac
    [ -n "$reg_host" ] || die "PAY_IMAGE 不含镜像仓库前缀，无法在线查询版本"
    _token=$(curl -sf "https://${reg_host}/token?scope=repository:${repo_path}:pull&service=${reg_host}" \
      | sed -n 's/.*"token"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    [ -n "$_token" ] || die "获取 ${reg_host} 匿名 token 失败（镜像未公开或网络不通）"
    VERSIONS_OUT=$(curl -sf -H "Authorization: Bearer ${_token}" \
      "https://${reg_host}/v2/${repo_path}/tags/list" \
      | tr ',' '\n' | sed -n 's/.*"\([0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*\)".*/\1/p' \
      | sort -rV) || true
  fi
  mapfile -t VERSIONS <<< "$VERSIONS_OUT"
  [ "${#VERSIONS[@]}" -gt 0 ] || die "镜像仓库没有可用版本（或查询失败）"
  LATEST="${VERSIONS[0]}"

  if [ "$UPGRADE_CHECK" -eq 1 ]; then
    log "当前版本: ${CUR_VERSION}    最新版本: ${LATEST}"
    log "可用版本: ${VERSIONS[*]}"
    exit 0
  fi

  TARGET="$UPGRADE_VERSION"; [ -n "$TARGET" ] || TARGET="$LATEST"
  printf '%s\n' "${VERSIONS[@]}" | grep -qx "$TARGET" \
    || die "版本 ${TARGET} 不在可用列表（可用：${VERSIONS[*]}）"
  if [ "$TARGET" = "$CUR_VERSION" ] && [ -z "$UPGRADE_VERSION" ]; then
    log "当前已是最新版本 ${CUR_VERSION}，无需升级"; exit 0
  fi
  if [ "$UPGRADE_YES" -ne 1 ]; then
    read -r -p "升级 ${CUR_VERSION} → ${TARGET} ？(y/N) " _ans
    case "$_ans" in y|Y|yes|YES) ;; *) die "已取消" ;; esac
  fi

  # 仓库前缀沿用现有 PAY_IMAGE（仅替换标签；BASE_IMAGE 已在版本查询处解析）
  NEW_IMAGE="${REPO_REF}:${TARGET}"
  # 原地改写 .env 的 PAY_IMAGE（经临时文件 cat 回写，保留原 inode 属主/权限）
  awk -v kv="PAY_IMAGE=${NEW_IMAGE}" 'BEGIN{done=0}
    /^PAY_IMAGE=/{if(!done){print kv;done=1}next}
    {print}
    END{if(!done)print kv}' .env > .env.payup.tmp
  cat .env.payup.tmp > .env && rm .env.payup.tmp
  export PAY_IMAGE="$NEW_IMAGE"
  log "PAY_IMAGE 已更新为 ${NEW_IMAGE}，开始升级 ..."
fi

# ---- 2. 拉镜像 ----
IMAGE="${PAY_IMAGE:-pay-gateway:latest}"
if [ "$PULL" -eq 1 ] && [ "${IMAGE#*/}" != "$IMAGE" ]; then
  log "拉取镜像 ${IMAGE} ..."
  dc pull app worker
fi

# 三方插件目录：由部署用户预创建，避免 docker 挂载时用 root 建目录导致
# 部署方没有写权限（直接拷 .so 插件目录进 storage/plugins/<插件名>/ 即可，
# 无需重建镜像；与后台上传安装同一目录，同名覆盖镜像内置插件。
# 拷完在后台点一次「刷新插件」即全进程生效，无需重启容器）
mkdir -p storage/plugins

# ---- 3. 起容器 ----
UP_ARGS=(-d)
[ -n "$SCALE" ] && UP_ARGS+=(--scale "app=${SCALE}")
log "启动容器（镜像 ${IMAGE}）..."
dc up "${UP_ARGS[@]}"

# ---- 4. 等全部 healthy ----
# 期望容器数 = app 实例数 + worker（1 个）：不能写死，--scale 1 时只有 2 个容器，写死会假失败退出
# （upgrader 等附加服务不计数，判定用 -ge 不受影响）
APPS="${SCALE:-$(env_val APP_REPLICAS)}"   # app 实例数：--scale 优先，其次 .env 的 APP_REPLICAS（与 compose 插值同源）
case "$APPS" in
  ''|*[!0-9]*) APPS=3 ;;   # 未配置 / 非数字：回落到 compose 默认值 3
esac
EXPECTED=$((APPS + 1))     # 判定用 -ge：worker 被额外扩容时不误报
log "等待容器健康检查（期望 ${EXPECTED} 个：app×${APPS} + worker×1，最多 90s）..."
ok=0
for _ in $(seq 1 45); do
  out=$(dc ps --format '{{.Name}}|{{.Status}}' 2>/dev/null || true)
  if [ -n "$out" ] \
     && [ "$(printf '%s\n' "$out" | grep -c '|')" -ge "$EXPECTED" ] \
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
log "完成 ✔  入口：宿主 nginx 反代 127.0.0.1:8000-8002（nginx-conf.conf，宝塔可直接粘贴站点配置）；"
log "      管理后台 /admin（首次部署默认 superadmin/123456，登录后立即改密）；"
log "      日志：docker compose -f $COMPOSE_FILE logs -f app worker"
