"""存储驱动工厂 — 根据 config.STORAGE_PROVIDER 返回对应驱动实例"""

from app.addons.storage.interface import StorageInterface
from app.core.config import config


def get_storage(disk: str | None = None) -> StorageInterface:
    """
    获取存储驱动实例

    Args:
        disk: 存储磁盘名，默认使用 config.STORAGE_PROVIDER
    """
    provider = disk or config.STORAGE_PROVIDER

    if provider == "cos":
        from app.addons.storage.cos import COSStorage
        return COSStorage()
    elif provider == "oss":
        from app.addons.storage.oss import OSSStorage
        return OSSStorage()
    else:
        from app.addons.storage.local import LocalStorage
        return LocalStorage()
