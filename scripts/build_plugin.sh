#!/usr/bin/env bash
# 单独打包一个支付插件：编译为 linux/amd64 .so（与生产镜像同环境同指令，ABI 一致），
# 并生成可直接发给客户、在后台「上传插件」安装的 zip。
#
# 用法:
#   scripts/build_plugin.sh <插件名>
#
# 产物（dist/ 已在 .gitignore 中，不会入库）:
#   dist/plugins/<插件名>/             解压后的插件目录（服务器挂载/更新用）
#   dist/plugin_packages/<插件名>.zip  发给客户上传的安装包（仅 .so，无源码）
#
# 客户侧安装（推荐发给客户）:
#   后台「支付通道 → 上传插件」选择该 zip → 建通道填凭证 → 测试 → 启用
#   多 worker/多副本部署时，其他进程需重启后才加载新插件
#
# 自用/托管部署（目录映射，无需重建镜像）:
#   把 dist/plugins/<插件名>/ 整个目录拷到服务器的 storage/plugins/<插件名>/，
#   重启容器即可（与后台上传同一目录；同名覆盖镜像内置插件，后台标记为「三方」）
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

# 打成后台上传格式的 zip：条目为 <插件名>/plugin*.so（无 .py / 无 info.json）
PKG_DIR="$ROOT/dist/plugin_packages"
mkdir -p "$PKG_DIR"
PKG="$PKG_DIR/$PLUGIN.zip"
rm -f "$PKG"

if command -v python3 >/dev/null 2>&1; then
  (cd "$ROOT" && python3 - "$PKG" "$PLUGIN" <<'PY'
import pathlib, sys, zipfile
pkg, name = sys.argv[1], sys.argv[2]
src = pathlib.Path("dist/plugins") / name
with zipfile.ZipFile(pkg, "w", zipfile.ZIP_DEFLATED) as zf:
    for p in sorted(src.rglob("*")):
        if p.is_file():
            zf.write(p, p.relative_to(src.parent).as_posix())
PY
  )
elif command -v zip >/dev/null 2>&1; then
  (cd "$ROOT/dist/plugins" && zip -qr "$PKG" "$PLUGIN")
else
  echo "需要 python3 或 zip 命令来生成上传包"; exit 1
fi

echo "产物: $OUT_DIR"
echo "上传包: $PKG（发给客户，后台「上传插件」安装）"

