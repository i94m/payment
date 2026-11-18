"""字符串混淆的运行时解码（仅构建期产物会调用；源码运行无副作用）。

`scripts/string_obfuscate.py` 在镜像构建时把源码里的字符串字面量换成
`_obf_s("密文")`，本模块负责解码：

- 密文 = base64url(XOR(utf-8(原文), 密钥))
- 密钥 `_KEY_HEX` 由构建脚本写入本文件（随 Cython 编译进 .so，不以明文源码存在）
- 源码运行/测试时 `_KEY_HEX` 为空、也没有任何调用点，因此不会触发解码

注意：`s()` 带 lru_cache（按密文缓存），同一常量只解码一次；
构建密钥变化时需 `s.cache_clear()`（只在构建流程/测试里发生）。
"""
import base64
import functools

_KEY_HEX = ""


def _key() -> bytes:
    if not _KEY_HEX:
        raise RuntimeError(
            "字符串混淆密钥未注入：本模块只在 SO_COMPILE 构建产物中使用；"
            "源码运行无需调用（请确认不是误跑了混淆后的源码）"
        )
    return bytes.fromhex(_KEY_HEX)


@functools.lru_cache(maxsize=8192)
def s(blob: str) -> str:
    """解密一个构建期加密的字符串常量。"""
    key = _key()
    raw = base64.urlsafe_b64decode(blob + "=" * (-len(blob) % 4))
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(raw)).decode("utf-8")
