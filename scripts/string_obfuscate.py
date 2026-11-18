#!/usr/bin/env python3
"""构建期字符串混淆（配合 scripts/compile_backend.py，仅 SO_COMPILE 镜像构建使用）。

为什么：Cython 只做编译，不做混淆 —— `.so` 里的字符串常量仍可被 `strings`/反编译
直接看到（`Invalid License Code`、密钥格式、SQL、内部路径等）。这里在编译前把源码里
的字符串字面量替换成运行时解码调用，密文与密钥都只存在于构建产物里。

做法：
- 每个字符串字面量 `"x"` → `_obf_s("密文")`，并在模块头注入
  `from app.core._obf import s as _obf_s`
- 密文 = base64url(XOR(utf-8(x), 本次构建的随机密钥))，密钥写进 `app/core/_obf.py`
  （同样随 Cython 编译，不以明文源码出现在镜像里）
- 跳过：文档字符串（`app/core/**` 例外：连 docstring 一起混淆，避免内部说明泄露；
  其余模块保留 __doc__/help/OpenAPI 描述）、f-string 的字面量段（语法要求常量，
  只混淆 `{}` 里的表达式）、match 模式里的字面量（同上）、bytes、空串，
  以及 `app/core/_obf.py` 自身与 `app/__init__.py`（引导模块）

代价：每次求值多一次函数调用（`app.core._obf.s` 带 lru_cache，模块级常量只解码一次）。
"""
from __future__ import annotations

import ast
import base64
import hashlib
import os
import secrets
from pathlib import Path

HELPER_MODULE = "app.core._obf"
HELPER_ALIAS = "_obf_s"  # 单下划线：类体内不会被 name mangling 改写
KEY_MARKER = '_KEY_HEX = ""'
_OBF_SELF = "app/core/_obf.py"
_BOOT_STRAP = "app/__init__.py"


# 确定性密钥种子：同一份源码两次构建产出完全相同的密文/文件名，
# 这样 gcc 侧 ccache 才能命中（随机密钥会让 121 个模块每次全量重编，构建从 6s 涨到 230s）。
# 混淆只提高逆向门槛，密钥最终存在于 .so 里，随机与否对安全性没有实质差别。
KEY_SEED_ENV = "OBFUSCATE_KEY_SEED"
DEFAULT_KEY_SEED = "payment-obfuscate-v1"


def build_key(random_key: bool = False) -> bytes:
    """构建密钥（16 字节）。

    默认确定性派生（sha256(seed)[:16]）：源码不变 → 密文不变 → ccache 命中。
    需要彻底重洗（让所有密文一次性变样）时：random_key=True 或用
    OBFUSCATE_KEY_SEED 换一个种子重建。
    """
    if random_key:
        return secrets.token_bytes(16)
    seed = os.getenv(KEY_SEED_ENV) or DEFAULT_KEY_SEED
    return hashlib.sha256(seed.encode("utf-8")).digest()[:16]


def encode(value: str, key: bytes) -> str:
    data = value.encode("utf-8")
    xored = bytes(b ^ key[i % len(key)] for i, b in enumerate(data))
    return base64.urlsafe_b64encode(xored).rstrip(b"=").decode("ascii")


def decode(blob: str, key: bytes) -> str:
    raw = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode("utf-8")


class _StringSpans(ast.NodeVisitor):
    """收集「可替换为解码调用」的字符串字面量在源码中的字符区间。

    只做「字面量 → 调用」的文本级替换（不做 ast.unparse 重排）：
    原代码的书写形式（换行、括号、条件表达式形状）完全保留，避免触发
    Cython 编译器对改写后语法形状的崩溃/误判。
    """

    def __init__(self, key: bytes, source: str, keep_docstrings: bool = True):
        self.key = key
        self.source = source
        self.keep_docstrings = keep_docstrings
        self._lines = source.splitlines(keepends=True)
        starts, acc = [], 0
        for ln in self._lines:
            starts.append(acc)
            acc += len(ln)
        self._line_starts = starts
        self._docstrings: set[int] = set()
        self.spans: list[tuple[int, int, str]] = []

    # ---- 字符偏移：AST 的 col_offset 是 UTF-8 字节偏移，需按行换算 ----
    def _offset(self, lineno: int, col: int) -> int:
        line = self._lines[lineno - 1]
        if not line.isascii():
            col = len(line.encode("utf-8")[:col].decode("utf-8", "ignore"))
        return self._line_starts[lineno - 1] + col

    def _mark_docstring(self, node: ast.AST) -> None:
        body = getattr(node, "body", None)
        if not (body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            return
        doc = body[0].value
        self._docstrings.add(id(doc))  # 统一登记，避免又被 visit_Constant 加密成调用
        if self.keep_docstrings:
            return  # 保留原样
        # 内部模块：docstring 抹成空串（仍留一个字符串表达式 —— 后面可能紧跟
        # `from __future__`，直接删物会变成语法错误；抹成 "" 则 __doc__ 为空、无泄露）
        start = self._offset(doc.lineno, doc.col_offset)
        end = self._offset(doc.end_lineno, doc.end_col_offset)
        self.spans.append((start, end, '""'))

    def visit_Module(self, node: ast.Module):
        self._mark_docstring(node)
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef):
        self._mark_docstring(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._mark_docstring(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._mark_docstring(node)
        self.generic_visit(node)

    # ---- f-string 的字面量段必须是常量（语法要求），只处理 {} 里的表达式 ----
    def visit_JoinedStr(self, node: ast.JoinedStr):
        for part in node.values:
            if isinstance(part, ast.FormattedValue):
                self.visit(part)

    # ---- match 模式里的字面量必须是常量 ----
    def visit_MatchValue(self, node: ast.MatchValue):
        return

    def visit_MatchMapping(self, node: ast.MatchMapping):
        return

    def visit_Constant(self, node: ast.Constant):
        if not (isinstance(node.value, str) and node.value and id(node) not in self._docstrings):
            return
        start = self._offset(node.lineno, node.col_offset)
        end = self._offset(node.end_lineno, node.end_col_offset)
        blob = encode(node.value, self.key)
        self.spans.append((start, end, f'{HELPER_ALIAS}("{blob}")'))


def _import_offset(source: str, module: ast.Module) -> int:
    """解码函数导入的插入位置（原文偏移）：模块文档字符串与 __future__ 导入之后。

    返回原文中的字符偏移；-1 表示追加到文件末尾。注意必须用**原文**偏移：
    混淆会改写/缩短部分 span，若按行号在改写后的文本上再算会错位。
    """
    body = module.body
    idx = 0
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        idx = 1
    while idx < len(body) and isinstance(body[idx], ast.ImportFrom) \
            and body[idx].module == "__future__":
        idx += 1
    if idx >= len(body):
        return -1
    lines = source.splitlines(keepends=True)
    return sum(len(x) for x in lines[: body[idx].lineno - 1])


def obfuscate_source(source: str, key: bytes, module: str = "<src>",
                     keep_docstrings: bool = True) -> str:
    """把字符串字面量替换成运行时解码调用（保留原有代码形状），返回新源码。"""
    tree = ast.parse(source, filename=module)
    collector = _StringSpans(key, source, keep_docstrings=keep_docstrings)
    collector.visit(tree)

    # 导入插入与各字面量替换统一成「原文区间 → 新文本」，从后往前应用
    spans = list(collector.spans)
    insert_at = _import_offset(source, tree)
    injected = f"from {HELPER_MODULE} import s as {HELPER_ALIAS}\n"
    if insert_at == -1:
        out = source if source.endswith("\n") else source + "\n"
        return out + injected
    spans.append((insert_at, insert_at, injected))
    spans.sort(key=lambda x: (x[0], x[1]), reverse=True)

    out = source
    for start, end, text in spans:
        out = out[:start] + text + out[end:]
    return out


def inject_key(module_file: Path, key: bytes) -> None:
    """把本次构建的密钥写进 app/core/_obf.py（替换占位常量）。"""
    src = module_file.read_text(encoding="utf-8")
    if KEY_MARKER not in src:
        raise RuntimeError(f"{module_file} 缺少密钥占位 {KEY_MARKER}")
    module_file.write_text(
        src.replace(KEY_MARKER, f'_KEY_HEX = "{key.hex()}"', 1), encoding="utf-8")


def should_skip(rel_path: str) -> bool:
    """引导模块自身不混淆（它要最先可用，且不含业务字符串）。"""
    return rel_path in (_OBF_SELF, _BOOT_STRAP)


def opaque_stem(module: str, key: bytes) -> str:
    """模块名 → 不透明文件名（去掉原始名称的线索）。"""
    digest = hashlib.sha256(key + b"|module|" + module.encode()).hexdigest()
    return "_" + digest[:10]


# 这些目录的模块连 docstring 一起混淆：内部实现说明本身就是线索，
# 且它们的 __doc__ 不参与 OpenAPI / CLI 帮助（routers、commands 保持可读）
DROP_DOCSTRING_PREFIXES = ("app/core/",)


def obfuscate_tree(root: Path, key: bytes, targets: list[Path], module_name) -> int:
    """就地混淆给定源码文件；返回处理的模块数。供 compile_backend 调用。"""
    count = 0
    for p in targets:
        rel = p.relative_to(root).as_posix()
        if should_skip(rel):
            continue
        src = p.read_text(encoding="utf-8")
        keep_doc = not rel.startswith(DROP_DOCSTRING_PREFIXES)
        p.write_text(
            obfuscate_source(src, key, module=module_name(p), keep_docstrings=keep_doc),
            encoding="utf-8")
        count += 1
    inject_key(root / _OBF_SELF, key)
    return count


if __name__ == "__main__":  # 手工验证：混淆单个文件并回显
    import sys

    if len(sys.argv) < 2:
        raise SystemExit("用法: python scripts/string_obfuscate.py <file.py> [key_hex]")
    path = Path(sys.argv[1])
    key = bytes.fromhex(sys.argv[2]) if len(sys.argv) > 2 else build_key()
    print(obfuscate_source(path.read_text(encoding="utf-8"), key, module=str(path)))
