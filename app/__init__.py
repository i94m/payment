"""包入口 —— 构建期「模块文件名混淆」的导入钩子安装点。

镜像构建（scripts/compile_backend.py）会把 `app/` 下除引导模块外的每一条模块
编译成 `.so` 后**改成不透明文件名**（`_a1b2c3d4e5.cpython-312-*.so`），原始名称
只留在本文件的映射表里（同样随 Cython 编译，不以明文源码进镜像）。运行期由下面的
MetaPathFinder 把「真实模块名 → 不透明文件」翻译掉，因此：

- 代码里的 import 一行都不用改（静态/动态 import 都照旧）
- 磁盘上看不到 `license.so` / `order.so` 这类指向性文件名
- 源码运行（本地开发/测试）映射为空，等于什么都没装

映射注入位置：`_MAP_BLOB` / `_MAP_KEY`，构建脚本替换；两者为空即不启用。
"""
import base64
import importlib.machinery
import importlib.util
import json
import sys

# 构建期注入：base64url(XOR(compact JSON, key)) / 密钥 hex。
# JSON 形如 {"app.core.license": ["app/core/_a1b2c3d4e5.cpython-312-x86_64-linux-gnu.so", false]}
_MAP_BLOB = ""
_MAP_KEY = ""


def _decode_map() -> dict[str, list]:
    if not _MAP_BLOB or not _MAP_KEY:
        return {}
    key = bytes.fromhex(_MAP_KEY)
    raw = base64.urlsafe_b64decode(_MAP_BLOB + "=" * (-len(_MAP_BLOB) % 4))
    data = bytes(b ^ key[i % len(key)] for i, b in enumerate(raw))
    return json.loads(data.decode("utf-8"))


_MAP: dict[str, list] = _decode_map()
_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent


class _OpaqueFileFinder:
    """把真实模块名解析到不透明文件名的 .so / .py。"""

    @staticmethod
    def find_spec(fullname, path=None, target=None):
        entry = _MAP.get(fullname)
        if not entry:
            return None
        rel, is_package = entry
        file = _ROOT / rel
        if not file.is_file():
            return None
        if file.suffix == ".py":
            loader = importlib.machinery.SourceFileLoader(fullname, str(file))
        else:
            loader = importlib.machinery.ExtensionFileLoader(fullname, str(file))
        if is_package:
            return importlib.util.spec_from_file_location(
                fullname, str(file), loader=loader,
                submodule_search_locations=[str(file.parent)],
            )
        return importlib.util.spec_from_file_location(fullname, str(file), loader=loader)


if _MAP and not any(isinstance(f, _OpaqueFileFinder) for f in sys.meta_path):
    # 插到最前：真实文件名已不存在，只有这里能解析；未命中的模块仍交给后续 finder
    sys.meta_path.insert(0, _OpaqueFileFinder())
