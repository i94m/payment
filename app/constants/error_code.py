"""错误码定义 — 统一业务错误码"""


class ErrorCode:
    """业务错误码常量"""

    UNBOUND_PHONE = (40001, "请先绑定手机号")
    INCOMPLETE_PROFILE = (40002, "请先完善资料")
    CAPTCHA_EXPIRED = (40003, "验证码已过期")
    CAPTCHA_INVALID = (40004, "验证码错误")
    ACCOUNT_LOCKED = (40005, "账号已被锁定")
    ACCOUNT_DISABLED = (40006, "账号已被禁用")
    PASSWORD_ERROR = (40007, "用户名或密码错误")
    TOKEN_EXPIRED = (40008, "登录已过期，请重新登录")
    PERMISSION_DENIED = (40301, "没有操作权限")
    UPLOAD_FAILED = (41001, "文件上传失败")
    UPLOAD_TYPE_INVALID = (41002, "文件类型不支持")
    UPLOAD_SIZE_EXCEEDED = (41003, "文件大小超出限制")
