"""开放 API 请求体"""

from pydantic import BaseModel


class ApiLoginRequest(BaseModel):
    """API 登录（手机号/用户名 + 密码）"""
    username: str
    password: str
    captcha_key: str | None = None
    captcha_code: str | None = None


class ProfileRequest(BaseModel):
    """更新个人资料"""
    nickname: str | None = None
    avatar: str | None = None
