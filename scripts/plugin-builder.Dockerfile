# 单插件编译助手镜像（scripts/build_plugin.sh 使用）
# 与 Dockerfile builder 阶段同版本：python:3.12-slim + gcc + cython==3.3.*，
# 保证单独编译的插件 .so 与镜像内产物 ABI 一致（cp312 / linux-amd64）
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "cython==3.3.*" setuptools

WORKDIR /work
