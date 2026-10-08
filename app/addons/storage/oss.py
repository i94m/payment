"""阿里云 OSS 存储驱动

依赖: pip install oss2
"""

import logging
from datetime import datetime

from app.addons.storage.interface import StorageInterface
from app.core.config import config

logger = logging.getLogger(__name__)


class OSSStorage(StorageInterface):
    def __init__(self):
        self.access_key_id = config.STORAGE_OSS_ACCESS_KEY_ID
        self.access_key_secret = config.STORAGE_OSS_ACCESS_KEY_SECRET
        self.bucket_name = config.STORAGE_OSS_BUCKET
        self.endpoint = config.STORAGE_OSS_ENDPOINT
        self.region = config.STORAGE_OSS_REGION
        self.host = config.STORAGE_OSS_HOST
        self._bucket = None

    def _get_bucket(self):
        if self._bucket is None:
            import oss2
            auth = oss2.Auth(self.access_key_id, self.access_key_secret)
            self._bucket = oss2.Bucket(auth, self.endpoint, self.bucket_name)
        return self._bucket

    def get_config(self) -> dict:
        return {
            "bucket": self.bucket_name,
            "endpoint": self.endpoint,
            "region": self.region,
            "host": self.host,
        }

    def get_url(self, key: str) -> str:
        return f"{self.host}/{key}"

    async def get_list(self, options: dict | None = None) -> list[dict]:
        options = options or {}
        import oss2  # 与 _get_bucket 一致的懒加载（oss2 为可选依赖）
        bucket = self._get_bucket()
        params = {}
        if options.get("limit"):
            params["max_keys"] = options["limit"]
        if options.get("prefix"):
            params["prefix"] = options["prefix"]
        if options.get("start_key"):
            params["start_after"] = options["start_key"]

        files = []
        for obj in oss2.ObjectIterator(bucket, **params):
            files.append({
                "key": obj.key,
                "size": obj.size,
                "last_modified": datetime.fromtimestamp(obj.last_modified).strftime("%Y-%m-%d %H:%M:%S"),
            })
        return files

    async def upload(self, key: str, data: bytes) -> dict:
        self._get_bucket().put_object(key, data)
        return {"key": key, "url": self.get_url(key)}

    def copy(self, from_key: str, to_key: str) -> bool:
        self._get_bucket().copy_object(from_key, to_key)
        return True

    def move(self, from_key: str, to_key: str) -> bool:
        bucket = self._get_bucket()
        bucket.copy_object(from_key, to_key)
        bucket.delete_object(from_key)
        return True

    async def delete(self, keys: list[str]) -> None:
        self._get_bucket().batch_delete_objects(keys)
