"""腾讯云 COS 存储驱动

依赖: pip install cos-python-sdk-v5
"""

import logging
from datetime import datetime

from app.addons.storage.interface import StorageInterface
from app.core.config import config

logger = logging.getLogger(__name__)


class COSStorage(StorageInterface):
    def __init__(self):
        self.secret_id = config.STORAGE_COS_SECRET_ID
        self.secret_key = config.STORAGE_COS_SECRET_KEY
        self.bucket = config.STORAGE_COS_BUCKET
        self.region = config.STORAGE_COS_REGION
        self.host = config.STORAGE_COS_HOST
        self._client = None

    def _get_client(self):
        if self._client is None:
            from qcloud_cos import CosConfig, CosS3Client
            self._client = CosS3Client(CosConfig(
                Region=self.region,
                SecretId=self.secret_id,
                SecretKey=self.secret_key,
                Scheme="https",
            ))
        return self._client

    def get_config(self) -> dict:
        return {
            "bucket": self.bucket,
            "region": self.region,
            "host": self.host,
        }

    def get_url(self, key: str) -> str:
        return f"{self.host}/{key}"

    async def get_list(self, options: dict | None = None) -> list[dict]:
        options = options or {}
        client = self._get_client()
        params = {}
        if options.get("limit"):
            params["MaxKeys"] = options["limit"]
        if options.get("prefix"):
            params["Prefix"] = options["prefix"]
        if options.get("start_key"):
            params["Marker"] = options["start_key"]

        files = []
        response = client.list_objects(Bucket=self.bucket, **params)
        for obj in response.get("Contents", []):
            files.append({
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": datetime.fromisoformat(obj["LastModified"]).strftime("%Y-%m-%d %H:%M:%S"),
            })
        return files

    async def upload(self, key: str, data: bytes) -> dict:
        self._get_client().put_object(Bucket=self.bucket, Key=key, Body=data)
        return {"key": key, "url": self.get_url(key)}

    def copy(self, from_key: str, to_key: str) -> bool:
        client = self._get_client()
        client.copy(Bucket=self.bucket, Key=to_key, CopySource={"Bucket": self.bucket, "Key": from_key})
        return True

    def move(self, from_key: str, to_key: str) -> bool:
        client = self._get_client()
        client.copy(Bucket=self.bucket, Key=to_key, CopySource={"Bucket": self.bucket, "Key": from_key})
        client.delete_object(Bucket=self.bucket, Key=from_key)
        return True

    async def delete(self, keys: list[str]) -> None:
        client = self._get_client()
        client.delete_objects(Bucket=self.bucket, Delete={"Objects": [{"Key": k} for k in keys]})
