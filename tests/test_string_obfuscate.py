"""构建期混淆：字符串加密（语义保持）与模块文件名映射（导入钩子）。

镜像构建时才启用（SO_COMPILE + STRING_OBFUSCATE/MODULE_OBFUSCATE），这里用源码
形态覆盖算法与语义，不依赖 Cython：
- 字符串混淆后执行结果必须与原文完全一致（f-string/字典键/默认值/注解/match/docstring）
- 文件名映射能让「真实模块名 → 不透明 .py/.so」被正确解析
"""
import importlib.util
import pathlib
import sys

import pytest

import app as app_pkg
from app.core import _obf
from scripts import string_obfuscate as so

KEY = bytes.fromhex("0123456789abcdef0123456789abcdef")


# ============================================================
# 编解码
# ============================================================

@pytest.mark.parametrize("value", [
    "hello", "中文提示：无效的授权码", "emoji 🚀 ok", "line1\nline2", "a" * 500, "0",
])
def test_encode_decode_roundtrip(value):
    blob = so.encode(value, KEY)
    assert value not in blob or len(value) <= 1
    assert so.decode(blob, KEY) == value


# ============================================================
# 源码混淆：语义必须完全一致
# ============================================================

SOURCE = '''
"""模块文档"""
from __future__ import annotations

CONST = "hello"
NAME = "世界"
PATTERN = f"a-{CONST}-b"
DICT = {"k": "v", "中文": "值"}


def f(x: str = "def") -> str:
    """函数文档"""
    match x:
        case "def":
            return PATTERN + DICT["k"]
        case "中文":
            return NAME
        case _:
            return CONST


class C:
    """类文档"""
    attr = "cls"

    def m(self):
        return self.attr + "!"
'''

RAW_BYTES = 'RAW = b"binary\\x00"\n'


def _run(src: str) -> dict:
    ns: dict = {}
    exec(compile(src, "<test>", "exec"), ns)
    return ns


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setattr(_obf, "_KEY_HEX", KEY.hex())
    _obf.s.cache_clear()
    yield
    _obf.s.cache_clear()


def test_obfuscated_source_behaves_identically():
    original = _run(SOURCE + RAW_BYTES)
    obfuscated = so.obfuscate_source(SOURCE + RAW_BYTES, KEY, module="t")
    result = _run(obfuscated)

    assert result["f"]() == original["f"]() == "a-hello-bv"
    assert result["f"]("中文") == original["f"]("中文") == "世界"
    assert result["f"]("other") == original["f"]("other") == "hello"
    assert result["C"]().m() == "cls!"
    assert result["DICT"] == original["DICT"]

    # 文档字符串保留（不被混淆，help()/OpenAPI 描述可用）
    assert result["__doc__"] == "模块文档"
    assert result["f"].__doc__ == "函数文档"
    assert result["C"].__doc__ == "类文档"

    # 默认值/注解仍是原值
    assert result["f"].__defaults__ == ("def",)
    assert result["f"].__annotations__["return"] == "str"

    # bytes 不参与混淆
    assert result["RAW"] == b"binary\x00"

    # 明文常量不再出现在产物里
    assert "a-hello-b" not in obfuscated
    assert "_obf_s(" in obfuscated
    # 注入位置：在 __future__ 之后（否则 SyntaxError）
    lines = [ln for ln in obfuscated.splitlines() if ln.strip()]
    assert lines[0].startswith('"""')
    assert lines[1].startswith("from __future__")
    assert "import s as _obf_s" in lines[2]


def test_match_pattern_literals_not_transformed():
    """match 的 case 字面量必须是常量，替换成函数调用会直接 SyntaxError。"""
    src = 'def f(x):\n    match x:\n        case "a":\n            return 1\n        case _:\n            return 2\n'
    out = so.obfuscate_source(src, KEY, module="m")
    compile(out, "<m>", "exec")  # 能编译即说明语法合法
    assert _run(out)["f"]("a") == 1


def test_obfuscate_is_idempotent_in_effect():
    """二次混淆只是再包一层，语义不变（构建幂等性兜底）。"""
    once = so.obfuscate_source(SOURCE, KEY, module="t")
    twice = so.obfuscate_source(once, KEY, module="t")
    assert _run(twice)["f"]() == _run(SOURCE)["f"]()


def test_inject_key_requires_marker(tmp_path):
    helper = tmp_path / "_obf.py"
    helper.write_text('_KEY_HEX = ""\n', encoding="utf-8")
    so.inject_key(helper, KEY)
    assert f'_KEY_HEX = "{KEY.hex()}"' in helper.read_text(encoding="utf-8")

    bad = tmp_path / "bad.py"
    bad.write_text("nothing\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        so.inject_key(bad, KEY)


def test_docstring_can_be_obfuscated_for_internal_modules():
    """app/core/** 会连 docstring 一起混淆（__doc__ 变成 None，不泄露实现说明）。"""
    out = so.obfuscate_source(SOURCE, KEY, module="app.core.x", keep_docstrings=False)
    assert "模块文档" not in out
    result = _run(out)
    assert not result["__doc__"]          # 抹空，不泄露实现说明
    assert result["f"]() == "a-hello-bv"   # 行为不变


def test_should_skip_bootstrap_modules():
    assert so.should_skip("app/__init__.py")
    assert so.should_skip("app/core/_obf.py")
    assert not so.should_skip("app/core/license.py")


def test_opaque_stem_hides_module_name():
    stem = so.opaque_stem("app.core.license", KEY)
    assert stem.startswith("_") and len(stem) == 11
    assert "license" not in stem
    assert so.opaque_stem("app.core.license", KEY) == stem          # 同一构建稳定
    assert so.opaque_stem("app.core.license", b"x" * 16) != stem     # 换构建密钥变名


# ============================================================
# 文件名映射 / 导入钩子
# ============================================================

def test_finder_resolves_opaque_module(tmp_path, monkeypatch):
    d = tmp_path / "fake_mods"
    d.mkdir()
    (d / "_x1.py").write_text("VALUE = 42\n", encoding="utf-8")

    monkeypatch.setattr(app_pkg, "_ROOT", tmp_path)
    monkeypatch.setattr(app_pkg, "_MAP", {"fake_pkg.opaque": ["fake_mods/_x1.py", False]})

    finder = app_pkg._OpaqueFileFinder()
    assert finder.find_spec("not.mapped") is None

    spec = finder.find_spec("fake_pkg.opaque")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.VALUE == 42


def test_finder_marks_package_search_path(tmp_path, monkeypatch):
    pkg_dir = tmp_path / "fake_pkg"
    pkg_dir.mkdir()
    (pkg_dir / "_p.py").write_text("SUB = 'ok'\n", encoding="utf-8")

    monkeypatch.setattr(app_pkg, "_ROOT", tmp_path)
    monkeypatch.setattr(app_pkg, "_MAP", {"fake_pkg": ["fake_pkg/_p.py", True]})

    spec = app_pkg._OpaqueFileFinder().find_spec("fake_pkg")
    assert spec and spec.submodule_search_locations == [str(pkg_dir)]


def test_source_run_has_no_mapping():
    """源码运行/测试：映射为空 → 不安装钩子，行为与改动前一致。"""
    assert app_pkg._MAP == {}
    assert not any(isinstance(f, app_pkg._OpaqueFileFinder) for f in sys.meta_path)


def test_decode_map_from_injected_blob():
    """构建脚本注入的 blob 能被 app/__init__ 的解析逻辑还原。"""
    payload = {"app.core.x": ["app/core/_abc.so", False]}
    import json
    blob = so.encode(json.dumps(payload, separators=(",", ":")), KEY)

    path_mod = type(app_pkg)
    orig = (path_mod._MAP_BLOB, path_mod._MAP_KEY) if hasattr(path_mod, "_MAP_BLOB") else None
    # 直接在模块命名空间上临时改（构建期的真实形态）
    import app
    app._MAP_BLOB, app._MAP_KEY = blob, KEY.hex()
    try:
        assert app._decode_map() == payload
    finally:
        app._MAP_BLOB, app._MAP_KEY = "", ""
    assert orig is None or True  # 占位，避免未使用告警
