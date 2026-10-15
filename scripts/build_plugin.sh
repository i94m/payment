#!/usr/bin/env bash
# 单独打包一个支付插件：编译为 linux/amd64 .so（与生产镜像同环境同指令，ABI 一致）
#
# 用法:
#   scripts/build_plugin.sh <插件名>        # 产物输出 dist/plugins/<插件名>/
#
# 更新到容器（以 prod/ 部署为例，app 与 worker 两个服务都要挂载）:
#   1. 把 dist/plugins/<插件名>/ 拷到服务器，如 prod/plugins_ext/<插件名>/
#   2. compose 里两个服务的 volumes 各加一行:
#        - ./plugins_ext/<插件名>:/app/plugins/<插件名>:ro
#   3. docker compose up -d                 # 重建容器，启动时自动刷新插件注册表
#   4. 后台「已安装插件」核对版本号（更新前记得在 info.json 里递增 version）
#
# 注意: 产物仅适用于生产镜像（cp312 / linux-amd64）；挂载会覆盖镜像内同名插件目录
set -euo pipefail

PLUGIN="${1:?用法: scripts/build_plugin.sh <插件名>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

[ -f "$ROOT/plugins/$PLUGIN/plugin.py" ] || { echo "插件不存在: plugins/$PLUGIN/plugin.py"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "需要 docker"; exit 1; }

# 助手镜像与 Dockerfile builder 阶段同版本（python:3.12-slim + gcc + cython==3.3.*）
docker build -q -f "$ROOT/scripts/plugin-builder.Dockerfile" \
  -t pay-plugin-builder "$ROOT/scripts" >/dev/null

OUT_DIR="$ROOT/dist/plugins/$PLUGIN"
rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

docker run --rm --platform linux/amd64 \
  -e PLUGIN="$PLUGIN" \
  -v "$ROOT":/src:ro \
  -v "$OUT_DIR":/out \
  pay-plugin-builder \
  python /src/scripts/build_plugin_inner.py

echo "产物: $OUT_DIR"
