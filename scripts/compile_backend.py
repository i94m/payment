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
  - *.json（regions.json 等）等数据文件

可选构建期混淆（Dockerfile builder 已默认打开，环境变量控制）：
  STRING_OBFUSCATE=1  字符串字面量加密（scripts/string_obfuscate.py）
  MODULE_OBFUSCATE=1  .so 改为不透明文件名 + 映射表注入 app/__init__.py
  不变量：app/__init__.py（引导）、main.py（uvicorn 入口）、app/commands/**（artisan
  按文件名发现）、plugins/**（注册键=目录名、主模块固定 plugin）保持原名

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
import json  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

# Cython / setuptools 延迟到 main() 里导入：构建机之外（如只调用本模块的
# 混淆辅助函数做验证/单测）不要求装编译工具链。

ROOT = Path.cwd()

# 构建期混淆（默认关闭；Dockerfile builder 显式打开）
#   STRING_OBFUSCATE=1  字符串字面量加密（scripts/string_obfuscate.py）
#   MODULE_OBFUSCATE=1  编译后把 .so 改成不透明文件名 + 注入映射表（app/__init__.py）
STRING_OBFUSCATE = os.environ.get("STRING_OBFUSCATE") == "1"
MODULE_OBFUSCATE = os.environ.get("MODULE_OBFUSCATE") == "1"

sys.path.insert(0, str(ROOT / "scripts"))
import string_obfuscate as obf  # noqa: E402

# 文件名混淆的排除项：
#   app/__init__.py — 引导模块（映射表在它里面，必须用标准名）
#   main.py         — uvicorn 入口（Dockerfile CMD 用 main:app）
#   app/commands/** — artisan.py 按文件名发现子命令（pkgutil），改名会丢命令
#   plugins/**      — 注册键 = 目录名、主模块固定叫 plugin（见 PluginRegistry）
RENAME_EXCLUDE = {"app/__init__.py", "main.py"}
RENAME_EXCLUDE_DIRS = (ROOT / "app" / "commands",)

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


def mapping_name(p: Path) -> str:
    """映射表用的模块名：包的 __init__.py 取包名。

    Cython 对 `Extension("app.worker.jobs.__init__", ...)` 会生成 package init 符号
    （PyInit_jobs），所以运行期必须以「包名」作为模块名加载改名后的 .so。
    """
    parts = p.relative_to(ROOT).with_suffix("").parts
    if p.name == "__init__.py":
        parts = parts[:-1]
    return ".".join(parts)


def plan_renames(sources: list[Path], key: bytes) -> dict[str, tuple[str, bool, Path]]:
    """真实模块名 → (不透明相对路径, 是否包, 原路径)。"""
    suffix = importlib.machinery.EXTENSION_SUFFIXES[0]
    plan: dict[str, tuple[str, bool, Path]] = {}
    for p in sources:
        rel = p.relative_to(ROOT).as_posix()
        if rel in RENAME_EXCLUDE:
            continue
        if any(p.is_relative_to(d) for d in RENAME_EXCLUDE_DIRS):
            continue
        mod = mapping_name(p)
        is_pkg = p.name == "__init__.py"
        target = p.parent.relative_to(ROOT) / (obf.opaque_stem(mod, key) + suffix)
        plan[mod] = (target.as_posix(), is_pkg, p)
    return plan


def inject_map(init_file: Path, plan: dict[str, tuple[str, bool, Path]], key: bytes) -> None:
    """把「真实模块名 → 不透明文件」映射（XOR+base64）写进 app/__init__.py。"""
    payload = {mod: [rel, is_pkg] for mod, (rel, is_pkg, _p) in plan.items()}
    blob = obf.encode(json.dumps(payload, separators=(",", ":")), key)
    src = init_file.read_text(encoding="utf-8")
    for marker, value in (('_MAP_BLOB = ""', f'_MAP_BLOB = "{blob}"'),
                          ('_MAP_KEY = ""', f'_MAP_KEY = "{key.hex()}"')):
        if marker not in src:
            sys.exit(f"app/__init__.py 缺少注入占位：{marker}")
        src = src.replace(marker, value, 1)
    init_file.write_text(src, encoding="utf-8")


def apply_obfuscation(sources: list[Path],
                      originals: dict[Path, str]) -> dict[str, tuple[str, bool, Path]]:
    """字符串混淆 + 生成/注入文件名映射；返回重命名计划（空=不改名）。"""
    random_key = os.environ.get("OBFUSCATE_RANDOM_KEY") == "1"
    key = obf.build_key(random_key=random_key)
    if STRING_OBFUSCATE:
        print(f"[混淆] 密钥：{'随机（每次构建不同，ccache 全失效）' if random_key else '确定性（同源码同产物，ccache 可命中）'}")
        changed = obf.obfuscate_tree(ROOT, key, sources, module_name)
        print(f"[混淆] 字符串加密完成：{changed} 个模块")
    plan: dict[str, tuple[str, bool, Path]] = {}
    if MODULE_OBFUSCATE:
        plan = plan_renames(sources, key)
        inject_map(ROOT / "app" / "__init__.py", plan, key)
        print(f"[混淆] 文件名映射已注入：{len(plan)} 个模块将改为不透明文件名")
    return plan


def main() -> None:
    from Cython.Build import cythonize
    from setuptools import Distribution, Extension
    from setuptools.command.build_ext import build_ext

    sources = collect_targets()
    if not sources:
        sys.exit("没有找到可编译的 .py 文件")

    originals = {p: p.read_text(encoding="utf-8") for p in sources}
    rename_plan = apply_obfuscation(sources, originals)

    def _cythonize_all():
        return cythonize(
            # -Wno-unused-function：Cython 注入的 __Pyx_* 工具函数按模块特性条件引用，
            # 未用到的报 unused 告警，属标准噪音（官方推荐做法）；其余告警保持可见
            [Extension(module_name(p), [str(p)], extra_compile_args=["-Wno-unused-function"])
             for p in sources],
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

    # 字符串混淆会把字面量换成函数调用，个别语法形状可能踩到 Cython 编译器的
    # 类型推断崩溃（如「字典推导 + 条件表达式 + 调用」）。这里做兜底：
    # 定位崩溃模块 → 恢复它的原始源码（该模块字符串不混淆）→ 重编，
    # 其余模块的混淆照常生效；回退数量超限则中止构建（不发布可疑产物）。
    reverted: list[Path] = []
    while True:
        try:
            exts = _cythonize_all()
            break
        except Exception as exc:  # noqa: BLE001 - 仅用于兜底定位
            if not STRING_OBFUSCATE:
                raise
            msg = str(exc)
            bad = next((p for p in sources if p not in reverted and str(p) in msg), None)
            if bad is None or len(reverted) >= 15:
                raise
            bad.write_text(originals[bad], encoding="utf-8")
            reverted.append(bad)
            print(f"[混淆] 回退字符串混淆：{bad.relative_to(ROOT)}（Cython 编译器崩溃），"
                  f"该模块字符串保持明文")
    if reverted:
        print(f"[混淆] 共 {len(reverted)} 个模块回退（其余 {len(sources) - len(reverted)} 个已混淆）")

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

    # 文件名混淆：把 .so 改成不透明名字（映射已写进 app/__init__.py）
    for mod, (rel, _is_pkg, p) in rename_plan.items():
        so = next((p.with_suffix(s) for s in so_suffixes if p.with_suffix(s).exists()), None)
        if so is None:  # pragma: no cover - 上面的 missing 自检已拦住
            sys.exit(f"重命名失败，找不到编译产物：{mod}")
        target = ROOT / rel
        if target.suffix != so.suffix:
            sys.exit(f"扩展名与映射表不一致（{target.name} vs {so.name}），中止")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(so, target)
    if rename_plan:
        print(f"[混淆] {len(rename_plan)} 个模块已改为不透明文件名")

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
    # ccache 命中统计（CI 观测缓存效果；本地无 ccache 时静默跳过）
    if shutil.which("ccache"):
        subprocess.run(["ccache", "-s"], check=False)


if __name__ == "__main__":
    main()
