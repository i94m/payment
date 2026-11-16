"""本地存储驱动。

路径安全（P1-14）：驱动层自己做「落盘地址必须收在 base_path 之内」的校验，
不依赖路由层的字符串过滤（`'..' in key` 拦不住绝对路径、URL 编码、Windows
分隔符等变体）。越界一律抛 ValueError，调用方按业务错误处理。
"""

import os

from app.addons.storage.interface import StorageInterface
from app.core.config import config


class LocalStorage(StorageInterface):
    def __init__(self):
        self.base_path = config.STORAGE_PATH
        self.host = config.STORAGE_LOCAL_HOST

    def get_config(self) -> dict:
        return {
            "base_path": self.base_path,
            "host": self.host,
        }

    def get_url(self, key: str) -> str:
        return f"{self.host}/{key}"

    # ------------------------------------------------------------
    # 路径收敛
    # ------------------------------------------------------------

    def _safe_path(self, key: str) -> str:
        """key → base_path 内的绝对路径；绝对 key / `../` 越界直接拒绝。"""
        base = os.path.realpath(self.base_path)
        raw = str(key or "")
        if raw.startswith(("/", "\\")) or os.path.isabs(raw):
            raise ValueError("非法存储路径")
        full = os.path.realpath(os.path.join(base, raw))
        if full != base and not full.startswith(base + os.sep):
            raise ValueError("非法存储路径")
        return full

    async def get_list(self, options: dict | None = None) -> list[dict]:
        import time
        from datetime import datetime

        options = options or {}
        target_dir = self._safe_path(options.get("prefix", ""))
        if not target_dir or not os.path.exists(target_dir):
            return []

        files = []
        limit = options.get("limit", 100)
        for root, _, filenames in os.walk(target_dir):
            for filename in sorted(filenames):
                if filename.startswith("."):
                    continue
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, self.base_path)
                stat = os.stat(full_path)
                files.append({
                    "key": rel_path,
                    "size": stat.st_size,
                    "last_modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })
                if len(files) >= limit:
                    return files
        return files

    async def upload(self, key: str, data: bytes) -> dict:
        full_path = self._safe_path(key)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(data)
        return {"key": key, "url": self.get_url(key)}

    def copy(self, from_key: str, to_key: str) -> bool:
        import shutil
        from_path = self._safe_path(from_key)
        to_path = self._safe_path(to_key)
        if not os.path.exists(from_path):
            return False
        os.makedirs(os.path.dirname(to_path), exist_ok=True)
        shutil.copy2(from_path, to_path)
        return True

    def move(self, from_key: str, to_key: str) -> bool:
        from_path = self._safe_path(from_key)
        to_path = self._safe_path(to_key)
        if not os.path.exists(from_path):
            return False
        os.makedirs(os.path.dirname(to_path), exist_ok=True)
        os.rename(from_path, to_path)
        return True

    async def delete(self, keys: list[str]) -> None:
        for key in keys:
            full_path = self._safe_path(key)
            if os.path.exists(full_path):
                os.remove(full_path)
