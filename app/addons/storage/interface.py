"""存储抽象接口 — 定义统一存储操作"""


class StorageInterface:
    """存储驱动基类，所有存储实现必须继承此类"""

    def get_config(self) -> dict:
        """获取当前配置"""
        raise NotImplementedError

    def get_url(self, key: str) -> str:
        """获取文件访问 URL"""
        raise NotImplementedError

    async def get_list(self, options: dict | None = None) -> list[dict]:
        """获取文件列表"""
        raise NotImplementedError

    async def upload(self, key: str, data: bytes) -> dict:
        """上传文件，返回 {key, url}"""
        raise NotImplementedError

    def copy(self, from_key: str, to_key: str) -> bool:
        """复制文件"""
        raise NotImplementedError

    def move(self, from_key: str, to_key: str) -> bool:
        """移动文件"""
        raise NotImplementedError

    async def delete(self, keys: list[str]) -> None:
        """删除文件"""
        raise NotImplementedError
