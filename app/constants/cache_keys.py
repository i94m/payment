"""缓存 Key 定义 — 集中管理，避免硬编码"""

from enum import StrEnum

from app.core.config import config


class CacheKeys(StrEnum):
    """缓存键枚举

    使用 .format() 填充动态部分，例如:
        CacheKeys.LOGIN_ERROR_TIMES.format(username="admin")
    """

    # 系统设置缓存
    SYSTEM_SETTINGS = f"{config.CACHE_PREFIX}system_settings"

    # 登录错误次数
    LOGIN_ERROR_TIMES = f"{config.CACHE_PREFIX}login_error_{{username}}"

    # 验证码
    CAPTCHA = f"{config.CACHE_PREFIX}captcha_{{key}}"

    # 上传 STS 缓存
    UPLOAD_STS = f"{config.CACHE_PREFIX}upload_sts_{{hash}}"

    # 扣量计数器（Redis INCR 原子计数，替代 counters 行锁）
    # 按商户独立计数：每个商户一根刻度，互不影响
    DEDUCT_ORDER_COUNTER = f"{config.CACHE_PREFIX}deduct_order_counter_{{merchant_id}}"
