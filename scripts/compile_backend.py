#!/usr/bin/env python3
"""Cython 源码保护编译：app/ + plugins/ + main.py → 机器码 .so，并删除 .py 源码。

运行环境（见 Dockerfile builder 阶段）：
  - 解释器带 cython + setuptools（独立 venv /opt/cybuild）
  - cwd 必须是项目根（inplace 构建按包路径落盘 .so）
  - gcc 可用（python:slim 自带 CPython 头文件，无需 python3-dev）

不编译（保留原样）：
  - artisan.py：入口纯装配，须能 `python artisan.py` 直接执行（deploy.sh 依赖）
  - alembic/**：迁移脚本按文件路径动态加载（不走包导入），且 migrate init 会在
    容器内覆写 env.py；迁移脚本=表结构，敏感度低
  - plugins/_example/**：插件开发模板，保持可读（发现逻辑按 `_` 前缀天然跳过）
  - *.json（info.json、regions.json 等）等数据文件

编译指令三件套是 pydantic v2 / FastAPI / typer 兼容的命门：
  annotation_typing=False  注解按 Python 对象保留（pydantic 读 __annotations__ 建模型）
  binding=True             保留函数对象语义（__signature__/__annotations__）
  embedsignature=True      签名嵌入 docstring（inspect.signature 可解析）
"""
from __future__ import annotations

import os
import sys

# 安全守卫（必须先于 Cython/setuptools 导入，保证本地误执行时得到干净提示）：
# 本脚本会删除源码 .py，只允许在容器镜像 builder 阶段执行
#（对应 Dockerfile 的 RUN SO_COMPILE=1 ...）。本地开发/调试永远直接跑源码，
# 不需要也不应该编译 .so，防止误执行清空工作区。
if os.environ.get("SO_COMPILE") != "1":
    sys.exit(
        "拒绝执行：本脚本会删除 app/、plugins/ 下的 .py 源码，仅在容器构建"
        "（Dockerfile builder 阶段，SO_COMPILE=1）时运行。"
        "本地调试请直接运行 .py 源码，无需编译。"
    )

import importlib.machinery  # noqa: E402
import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

from Cython.Build import cythonize  # noqa: E402
from setuptools import Distribution, Extension  # noqa: E402
from setuptools.command.build_ext import build_ext  # noqa: E402

ROOT = Path.cwd()

# 顶层扫描包：plugins 不存在的仓库（如基础模板）自动跳过
TOP_DIRS = ("app", "plugins")
EXCLUDE_DIRS = {
    ROOT / "alembic",
    ROOT / "plugins" / "_example",
}
# 包目录之外的单文件：main.py 纳入编译（uvicorn main:app 字符串导入对 .so 同样生效；
# 镜像内不保留可读装配源码），artisan.py 除外（deploy.sh 按脚本直接执行）
EXTRA_FILES = (ROOT / "main.py",)


def collect_targets() -> list[Path]:
    targets = [p for p in EXTRA_FILES if p.is_file()]
    for top in TOP_DIRS:
        base = ROOT / top
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            if any(p.is_relative_to(d) for d in EXCLUDE_DIRS):
                continue
            targets.append(p)
    return targets


def module_name(p: Path) -> str:
    # app/services/order.py → "app.services.order"
    # plugins/epay/plugin.py → "plugins.epay.plugin"
    return ".".join(p.relative_to(ROOT).with_suffix("").parts)


def main() -> None:
    sources = collect_targets()
    if not sources:
        sys.exit("没有找到可编译的 .py 文件")

    exts = cythonize(
        [Extension(module_name(p), [str(p)]) for p in sources],
        compiler_directives={
            "language_level": "3",
            "annotation_typing": False,
            "binding": True,
            "embedsignature": True,
        },
        build_dir="/tmp/cython_c",  # 生成的 .c 不留在源码树
        nthreads=os.cpu_count() or 4,
        force=True,
        quiet=True,
    )

    dist = Distribution({"ext_modules": exts})
    cmd = build_ext(dist)
    cmd.inplace = True  # .so 落回源码树原位：app/services/order.cpython-312-*.so
    cmd.ensure_finalized()
    # 并行 gcc（distutils 线程池编译多个 Extension，子进程不吃 GIL）：模块上百个、
    # 每个一文件，串行编译占镜像构建大头；parallel=核数约 4 倍提速（GH runner 4 vCPU）
    cmd.parallel = os.cpu_count() or 4
    cmd.run()

    # 自检：逐模块验证 .so 已生成，缺失则中止（不删源码，安全失败）
    so_suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
    missing = [
        str(p)
        for p in sources
        if not any(p.with_suffix(s).exists() for s in so_suffixes)
    ]
    if missing:
        sys.exit(f"以下模块未生成 .so，中止（不删源码）：{missing}")

    for p in sources:
        p.unlink()
    for d in (
        *ROOT.glob("app/**/__pycache__"),
        *ROOT.glob("plugins/**/__pycache__"),
        ROOT / "build",
    ):
        shutil.rmtree(d, ignore_errors=True)

    leftover = [
        str(p)
        for top in TOP_DIRS
        if (ROOT / top).is_dir()
        for p in (ROOT / top).rglob("*.py")
        if "_example" not in p.parts
    ]
    leftover += [str(p) for p in EXTRA_FILES if p.is_file()]
    if leftover:
        sys.exit(f"残留 .py：{leftover}")
    print(f"OK：{len(sources)} 个模块已编译为扩展并删除源码")


if __name__ == "__main__":
    main()
