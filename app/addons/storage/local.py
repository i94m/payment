"""本地存储驱动"""

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

    async def get_list(self, options: dict | None = None) -> list[dict]:
        import time
        from datetime import datetime

        options = options or {}
        target_dir = os.path.join(self.base_path, options.get("prefix", ""))
        if not os.path.exists(target_dir):
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
        full_path = os.path.join(self.base_path, key)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(data)
        return {"key": key, "url": self.get_url(key)}

    def copy(self, from_key: str, to_key: str) -> bool:
        import shutil
        from_path = os.path.join(self.base_path, from_key)
        to_path = os.path.join(self.base_path, to_key)
        if not os.path.exists(from_path):
            return False
        os.makedirs(os.path.dirname(to_path), exist_ok=True)
        shutil.copy2(from_path, to_path)
        return True

    def move(self, from_key: str, to_key: str) -> bool:
        from_path = os.path.join(self.base_path, from_key)
        to_path = os.path.join(self.base_path, to_key)
        if not os.path.exists(from_path):
            return False
        os.makedirs(os.path.dirname(to_path), exist_ok=True)
        os.rename(from_path, to_path)
        return True

    async def delete(self, keys: list[str]) -> None:
        for key in keys:
            full_path = os.path.join(self.base_path, key)
            if os.path.exists(full_path):
                os.remove(full_path)
