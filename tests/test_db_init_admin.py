"""db init 超管账号种子：固定 superadmin / 123456，不读环境变量，重复执行不重复创建。"""
import pytest
from sqlmodel import select

from app.commands.db import _adminer
from app.core.security import verify_password
from app.models import SystemAdmin


@pytest.mark.asyncio
async def test_seed_superadmin_fixed_password(session, monkeypatch):
    # 环境变量已废弃：即便设置也不应影响初始化密码
    monkeypatch.setenv("ADMIN_PASSWORD", "should-be-ignored")

    await _adminer(session)

    rows = (await session.exec(select(SystemAdmin))).all()
    assert len(rows) == 1
    admin = rows[0]
    assert admin.username == "superadmin"
    assert admin.name == "超级管理员"
    assert admin.is_super_admin is True
    assert verify_password("123456", admin.password)

    # 幂等：再次执行不应重复创建
    await _adminer(session)
    again = (await session.exec(select(SystemAdmin))).all()
    assert len(again) == 1
