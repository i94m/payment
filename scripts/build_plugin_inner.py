#!/usr/bin/env python3
"""单插件编译（由 scripts/build_plugin.sh 在 pay-plugin-builder 容器内执行）。

使用与 Dockerfile builder 阶段相同的编译指令集（annotation_typing/binding/
embedsignature 三件套，pydantic v2 兼容的命门，缺一不可），产物为
cp312 / linux-amd64 的 .so，与镜像内 .so ABI 一致。

挂载约定：仓库根只读挂 /src（取插件源码），/out 输出编译产物（.so + info.json
等非 .py 文件），插件名经环境变量 PLUGIN 传入。
"""
import importlib.machinery
import os
import pathlib
import shutil

from Cython.Build import cythonize
from setuptools import Distribution, Extension
from setuptools.command.build_ext import build_ext

PLUGIN = os.environ["PLUGIN"]
SRC = pathlib.Path("/src/plugins") / PLUGIN
WORK = pathlib.Path("/work/plugins") / PLUGIN
OUT = pathlib.Path("/out")

shutil.rmtree(WORK, ignore_errors=True)
shutil.copytree(SRC, WORK, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

# 编译目录下全部 .py（与 compile_backend 同策略，支持多模块插件）
sources = sorted(WORK.rglob("*.py"))
assert sources, f"{PLUGIN} 下没有 .py 可编译"

exts = cythonize(
    [
        Extension(".".join(p.relative_to(WORK.parent).with_suffix("").parts), [str(p)])
        for p in sources
    ],
    compiler_directives={
        "language_level": "3",
        "annotation_typing": False,
        "binding": True,
        "embedsignature": True,
    },
    build_dir="/tmp/cython_c",  # 生成的 .c 不留在产物里
    force=True,
    quiet=True,
)
cmd = build_ext(Distribution({"ext_modules": exts}))
cmd.inplace = True  # .so 落回 /work/plugins/<name>/ 原位
cmd.ensure_finalized()
cmd.run()

# 自检：每个 .py 都要有对应 .so，缺失则中止（不输出半成品）
suffixes = tuple(importlib.machinery.EXTENSION_SUFFIXES)
missing = [str(p) for p in sources if not any(p.with_suffix(s).exists() for s in suffixes)]
assert not missing, f"以下模块未生成 .so，中止：{missing}"

for p in sources:
    p.unlink()
shutil.rmtree(WORK / "__pycache__", ignore_errors=True)

OUT.mkdir(parents=True, exist_ok=True)
for p in WORK.iterdir():
    shutil.copy2(p, OUT / p.name)
print("OK:", ", ".join(sorted(x.name for x in OUT.iterdir())))
