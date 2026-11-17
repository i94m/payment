from pathlib import Path
from typing import Literal

from pydantic import (
    HttpUrl,
    computed_field,
    model_validator, )
from pydantic_core import MultiHostUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self

from app.core.license import enforce


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        # Use top level .env file (one level above ./backend/)
        env_file="./.env",
        env_ignore_empty=True,
        extra="ignore",
        env_file_encoding="utf-8"
    )
    APP_SECRET_KEY: str = ""
    # 授权码（作者离线签发的 Ed25519 签名授权码）。仅编译镜像（.so 部署）启动时
    # 强制校验，本地源码运行/pytest 自动跳过（见 app/core/license.py）；空=未授权
    AUTH_CODE: str = ""
    JWT_ALGORITHM: str = "HS256"
    # 60 minutes * 24 hours * 8 days = 8 days
    # API端Token有效期[分钟]
    API_ACCESS_TOKEN_TTL: int = 11520
    # 收银台不透明链接有效期[分钟]（0=不限制；链接含签发时间戳，逐次校验）
    CASHIER_LINK_TTL_MINUTES: int = 1440
    # local 启用万能验证码 0000（仅供本地开发/测试）；缺省必须 fail-secure 落到 prod，
    # .env 漏配/留空时生产环境不得退回 local
    APP_ENV: Literal["local", "dev", "prod"] = "prod"
    SYSTEM_SETTING_ID: int | None = 1

    # 仅用作 OpenAPI 文档标题；给缺省值避免 worker 等进程因 .env 漏配而启动失败
    APP_NAME: str = "聚合支付"
    # 预留字段，当前代码未实际读取；同上
    APP_URL: str = "http://localhost:8000"
    # 管理后台页面路径前缀（默认 admin）：改掉可隐藏后台入口，如 "console-x9"。
    # 仅约束页面路径；管理 API 固定为 /admin-api，不受影响。
    ADMIN_PATH_PREFIX: str = "admin"
    APP_CORS_ORIGINS: str = "*"
    SENTRY_DSN: HttpUrl | None = None
    APP_DEBUG: bool = False
    # 应用日志是否同时输出到 stdout（容器环境推荐开启）
    LOG_TO_CONSOLE: bool = False

    # Web 进程数（uvicorn --workers）：compose env_file 会把它导出为容器环境变量，
    # 镜像 CMD 以 ${WEB_WORKERS:-2} 读取，未配置时默认 2。
    # 有效并发 = WEB_WORKERS × 容器副本数（--scale）；单 worker 的 DB 连接
    # = DB_POOL_SIZE + DB_MAX_OVERFLOW，调大 worker 数按需同步调大连接池。
    WEB_WORKERS: int = 2

    # DB
    DB_SCHEME: str = "mysql"
    DB_HOST: str
    DB_PORT: int = 3306
    DB_DATABASE: str
    DB_USERNAME: str
    DB_PASSWORD: str = ""
    # 连接池（高并发按机器规格调整；单 worker 进程的有效连接 = POOL_SIZE + MAX_OVERFLOW）
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 50
    DB_POOL_RECYCLE: int = 1800

    # 缓存
    CACHE_PREFIX: str = ""

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""

    # 任务调度
    SCHEDULER_ENABLED: bool = False

    # Worker（SAQ 队列）
    WORKER_ENABLED: bool = False
    WORKER_CONCURRENCY: int = 10
    WORKER_JOB_TIMEOUT: int = 300

    # Storage
    STORAGE_PROVIDER: str = "local"
    STORAGE_STS_EXPIRE_MINUTES: int = 600
    # Storage[Local]
    STORAGE_PATH: str = "storage"
    STORAGE_LOCAL_SECRET: str = ""
    STORAGE_LOCAL_HOST: str = "/storage"

    # Storage[腾讯云COS]
    STORAGE_COS_SECRET_ID: str = ""
    STORAGE_COS_SECRET_KEY: str = ""
    STORAGE_COS_BUCKET: str = ""
    STORAGE_COS_REGION: str = ""
    STORAGE_COS_HOST: str = ""
    # Storage[阿里云OSS]
    STORAGE_OSS_ACCESS_KEY_ID: str = ""
    STORAGE_OSS_ACCESS_KEY_SECRET: str = ""
    STORAGE_OSS_ACCOUNT_ID: str = ""
    STORAGE_OSS_BUCKET: str = ""
    STORAGE_OSS_ENDPOINT: str = ""
    STORAGE_OSS_REGION: str = ""
    STORAGE_OSS_HOST: str = ""

    # 上游 HTTP 调用超时（秒）
    HTTP_CONNECT_TIMEOUT: int = 5
    HTTP_READ_TIMEOUT: int = 15

    @computed_field(return_type=MultiHostUrl)  # type: ignore[prop-decorator]
    @property
    def db_connect(self) -> MultiHostUrl:
        if self.DB_SCHEME == "pgsql":
            scheme = "postgresql+asyncpg"
        elif self.DB_SCHEME == "sqlite":
            scheme = "sqlite+aiosqlite"
        else:
            scheme = "mysql+asyncmy"
        return MultiHostUrl.build(
            scheme=scheme,
            host=self.DB_HOST,
            port=self.DB_PORT,
            username=self.DB_USERNAME,
            password=self.DB_PASSWORD,
            path=self.DB_DATABASE,
        )

    @computed_field(return_type=dict)
    @property
    def get_redis_config(self) -> dict:
        return {
            "host": self.REDIS_HOST,
            "port": self.REDIS_PORT,
            "password": self.REDIS_PASSWORD,
            "encoding": "utf-8",
            "decode_responses": True,
            "db": 0
        }

    SMTP_TLS: bool = True
    SMTP_SSL: bool = False
    SMTP_PORT: int = 587
    SMTP_HOST: str | None = None
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    # TODO: update type to EmailStr when sqlmodel supports it
    EMAILS_FROM_EMAIL: str | None = None
    EMAILS_FROM_NAME: str | None = None

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if not value:
            raise ValueError(
                f"{var_name} 未配置：不会自动生成，请显式填入 .env"
                "（openssl rand -hex 32 生成，或 uv run python artisan.py key generate）"
            )

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("APP_SECRET_KEY", self.APP_SECRET_KEY)
        # STORAGE_LOCAL_SECRET 可不配：回退到 APP_SECRET_KEY。它只用于本地存储
        # 上传策略的 HMAC 签发/验签，签与验同在本服务内，回退不影响互操作。
        if not self.STORAGE_LOCAL_SECRET:
            self.STORAGE_LOCAL_SECRET = self.APP_SECRET_KEY
        # self._check_default_secret("DB_PASSWORD", self.DB_PASSWORD)

        return self

    # 上传策略
    UPLOAD_POLICIES: dict = {
        "image": {
            "guest": False,
            "disk": "local",
            "prefix": "image/",
            "frequency": "daily",
            "max_file_size": 2 * 1048576,
            "extensions": [".jpg", ".jpeg", ".png", ".webp", ".avif"],
        },
        "images": {
            "guest": False,
            "disk": "local",
            "prefix": "images/",
            "frequency": "daily",
            "max_file_size": 524288,
            "extensions": [".jpg", ".jpeg", ".png", ".webp", ".avif"],
            "maximum": 6,
        },
        "media": {
            "guest": False,
            "disk": "local",
            "prefix": "medias/",
            "frequency": "daily",
            "max_file_size": 20 * 1048576,
            "extensions": [".jpg", ".jpeg", ".png", ".mp4"],
            "maximum": 9,
        },
        "video": {
            "guest": False,
            "disk": "local",
            "prefix": "video/",
            "frequency": "daily",
            "max_file_size": 50 * 1048576,
            "extensions": [".jpg", ".mp4"],
        },
        "file": {
            "guest": False,
            "disk": "local",
            "prefix": "files/",
            "frequency": "daily",
            "max_file_size": 50 * 1048576,
            "extensions": [
                ".mp4", ".pdf", ".docx", ".doc", ".xlsx", ".xls",
                ".pptx", ".ppt", ".csv", ".txt", ".zip", ".rar",
            ],
        },
        "avatar": {
            "guest": True,
            "disk": "local",
            "prefix": "avatars/",
            "frequency": "daily",
            "max_file_size": 2 * 1048576,
            "extensions": [".jpg", ".jpeg", ".png", ".webp", ".avif"],
        },
        "cert": {
            "guest": False,
            "disk": "local",
            "prefix": "certs/",
            "frequency": "daily",
            "max_file_size": 5 * 1048576,
            "extensions": [".pem", ".key", ".crt", ".cer", ".der", ".p7b", ".p7c", ".p12"],
        },
        "banner": {
            "guest": False,
            "disk": "local",
            "prefix": "banners/",
            "frequency": "daily",
            "max_file_size": 5 * 1048576,
            "extensions": [".jpg", ".jpeg", ".png", ".webp", ".avif"],
            "maximum": 5,
        },
        "desc": {
            "guest": False,
            "disk": "local",
            "prefix": "desc/",
            "frequency": "daily",
            "max_file_size": 2 * 1048576,
            "extensions": [".jpg", ".jpeg", ".png", ".webp", ".avif"],
        },
        "root": {
            "guest": False,
            "disk": "local",
            "prefix": "",
            "max_file_size": 5 * 1048576,
            "extensions": [".txt"],
        },
    }


config = Config()  # type: ignore

# 启动授权门禁：uvicorn / SAQ worker / 容器内 artisan、alembic 每个入口进程都会
# 导入本模块，在导入期统一拦截；仅编译形态强制。此刻日志系统尚未初始化，
# 失败信息由 license 模块直接写 stderr；码为空/无效不退出进程，转未授权软锁
# （运行期由 main.py 中间件拦截业务请求，Web 页面展示「无效的授权码」）
enforce(config.AUTH_CODE)

