#!/usr/bin/env bash
# 一键打包部署镜像：前端构建 → 文档构建 → docker build → 打标签 →（可选）推送仓库
#
# 用法：
#   scripts/build-image.sh                        # 本地镜像 epay-next:<版本>+latest
#   scripts/build-image.sh --push                 # 构建并推送到默认仓库（需先 docker login）
#   scripts/build-image.sh --registry registry.cn-hangzhou.aliyuncs.com/myns --push
#   scripts/build-image.sh --tag v1.2.3           # 指定版本标签（默认 日期-git短sha）
#   scripts/build-image.sh --skip-web             # 跳过前端/文档构建（dist 已是最新时）
#
# 服务器侧拉取部署（MySQL 用宿主机实例，见 docker-compose-example.yml / DEPLOY.md）：
#   EPAY_IMAGE=<仓库>/epay-next:<版本> docker compose pull app worker
#   EPAY_IMAGE=<仓库>/epay-next:<版本> docker compose up -d
set -euo pipefail

cd "$(dirname "$0")/.."

REGISTRY=""          # 镜像仓库前缀（空=不打仓库前缀，仅本地）
TAG=""               # 版本标签（空=自动 日期-git短sha）
PUSH=0
SKIP_WEB=0

while [ $# -gt 0 ]; do
  case "$1" in
    --registry) REGISTRY="${2:?--registry 需要值}"; shift 2 ;;
    --tag)      TAG="${2:?--tag 需要值}"; shift 2 ;;
    --push)     PUSH=1; shift ;;
    --skip-web) SKIP_WEB=1; shift ;;
    -h|--help)  grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数: $1（--help 查看用法）" >&2; exit 2 ;;
  esac
done

log() { printf '\033[1;32m[build-image]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[build-image]\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null || die "缺少 docker 命令"
docker info >/dev/null 2>&1 || die "docker 守护进程未运行"

# ---- 1. 前端构建（dist 不入库，打包前必须现场构建） ----
if [ "$SKIP_WEB" -eq 0 ]; then
  command -v pnpm >/dev/null || die "缺少 pnpm 命令（npm i -g pnpm）"
  log "构建前端 (resources/backend)..."
  ( cd resources/backend
    [ -d node_modules ] || pnpm install --frozen-lockfile
    pnpm build )
  [ -f static/frontend/index.html ] || die "前端构建产物缺少 index.html（构建失败？）"
  [ -f static/frontend/admin.html ] || die "前端构建产物缺少 admin.html（构建失败？）"

  log "构建对接文档 (resources/docs)..."
  ( cd resources/docs
    [ -d node_modules ] || pnpm install --frozen-lockfile
    pnpm build )
  [ -f static/docs/guide/protocol.html ] || die "文档构建产物缺失（构建失败？）"
fi

# ---- 2. 版本标签 ----
if [ -z "$TAG" ]; then
  GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo nogit)
  TAG="$(date +%Y%m%d-%H%M)-${GIT_SHA}"
fi
IMAGE=epay-next
[ -n "$REGISTRY" ] && IMAGE="${REGISTRY%/}/epay-next"

# ---- 3. 构建镜像（Dockerfile 内有入口完整性守护，缺产物直接失败） ----
log "构建镜像 ${IMAGE}:${TAG} ..."
docker build -t "${IMAGE}:${TAG}" -t "${IMAGE}:latest" .

# ---- 4. 推送 ----
if [ "$PUSH" -eq 1 ]; then
  if [ -z "$REGISTRY" ]; then
    die "未指定 --registry，拒绝推送本地名空间镜像；用法：--registry <仓库地址>/<命名空间> --push"
  fi
  log "推送 ${IMAGE}:${TAG} 与 ${IMAGE}:latest ..."
  docker push "${IMAGE}:${TAG}"
  docker push "${IMAGE}:latest"
fi

log "完成 ✔  ${IMAGE}:${TAG}"
[ "$PUSH" -eq 1 ] && log "服务器拉取：EPAY_IMAGE=${IMAGE}:${TAG} docker compose pull app worker && docker compose up -d"
exit 0
