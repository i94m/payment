"""后台管理请求体"""

from typing import Optional

from pydantic import BaseModel


class LoginRequest(BaseModel):
    """登录"""
    username: str
    password: str
    captcha_key: str | None = None
    captcha_code: str | None = None


class ChangePasswordRequest(BaseModel):
    """修改密码"""
    old_password: str
    password: str


class ResetPasswordRequest(BaseModel):
    """重置密码"""
    password: str


class CreateAdminRequest(BaseModel):
    """创建管理员"""
    name: str
    username: str
    password: str
    permissions: list[int]


class UpdateAdminRequest(BaseModel):
    """更新管理员"""
    name: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    is_forbid: Optional[bool] = None
    permissions: Optional[list[int]] = None
