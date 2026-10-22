"""密钥配置校验（app/core/config.py）：APP_SECRET_KEY 必填、
STORAGE_LOCAL_SECRET 空值回退到 APP_SECRET_KEY。
"""
import pytest

from app.core.config import Config


def test_storage_secret_falls_back_to_app_secret():
    cfg = Config(APP_SECRET_KEY="app-secret", STORAGE_LOCAL_SECRET="")
    assert cfg.STORAGE_LOCAL_SECRET == "app-secret"


def test_storage_secret_explicit_value_wins():
    cfg = Config(APP_SECRET_KEY="app-secret", STORAGE_LOCAL_SECRET="storage-secret")
    assert cfg.STORAGE_LOCAL_SECRET == "storage-secret"


def test_app_secret_still_required():
    with pytest.raises(ValueError, match="APP_SECRET_KEY"):
        Config(APP_SECRET_KEY="", STORAGE_LOCAL_SECRET="storage-secret")
