"""站点配置（运行期 KV，即时生效）。"""
import base64
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, Depends, Request
from pydantic import RootModel
from sqlmodel import select

from app.constants.settings import ADMIN_SETTING_KEYS
from app.core.deps import CacheDep, SessionDep
from app.core.security import AdminUser, PermissionChecker
from app.core.settings import merged_items
from app.models import Setting
from app.responses import err, suc
from app.services.settings import set_settings
from app.lib.sign import pem_to_flat

router = APIRouter()


@router.get("", dependencies=[Depends(PermissionChecker("pay_setting.view"))])
async def get_settings(session: SessionDep):
    """全量配置项（含每项最后修改时间）；直接查库,不走 60s 业务缓存。"""
    rows = (await session.exec(select(Setting))).all()
    return suc({"items": merged_items(rows)})


class UpdateSettingsRequest(RootModel[dict[str, Any]]):
    """{key: value} 部分更新；合法性（key 是否注册、类型）由 SETTINGS_REGISTRY 校验"""


@router.patch("", dependencies=[Depends(PermissionChecker("pay_setting.update"))])
async def update_settings(body: UpdateSettingsRequest, session: SessionDep, cache: CacheDep,
                          request: Request, auth: AdminUser):
    """更新站点配置。

    P1-9：**写入同样按 ADMIN_SETTING_KEYS 白名单过滤** —— 只隐藏不拦截等于没拦：
    此前该接口可改 platform_rsa_private_key / api_refund_enable
    等页面不展示的键（含签名私钥），越权面远超配置页语义。
    站点级开关仍可通过运行期注册表默认值 / 直接写库调整。
    """
    updates = body.root or {}
    rejected = sorted(k for k in updates if k not in ADMIN_SETTING_KEYS)
    if rejected:
        return err(422, f"不允许通过该接口修改的配置项：{', '.join(rejected)}", http_status=422)

    try:
        await set_settings(session, updates, cache)
    except ValueError as e:
        # 非法 key / 类型不符：整体拒绝,不产生部分写入
        # http_status 必须显式传 422,否则 err() 默认 400 与 body code 不一致
        return err(422, str(e), http_status=422)

    rows = (await session.exec(select(Setting))).all()
    return suc({"items": merged_items(rows)})


@router.post("/generate-rsa-keypair", dependencies=[Depends(PermissionChecker("pay_setting.update"))])
async def generate_rsa_keypair():
    """生成平台 RSA 密钥对（2048，对齐商户密钥 createRsaPair 的规格）。

    私钥返回 PKCS#8 DER 的 Base64 —— 即 platform_rsa_private_key 的存储格式
    （sign.base64_to_pem 直接包 PEM）。公钥返回单行 base64（对齐老系统观感），
    仅供现场确认，保存后商户详情「平台公钥」由私钥派生展示。密钥不落库，
    仍需点「保存设置」生效。

    注意：platform_rsa_private_key 不在 ADMIN_SETTING_KEYS 内，配置页 PATCH 改不了它
    （P1-9）；轮换平台签名密钥走 artisan 命令/直接写库，本接口只负责生成候选值。
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = private_key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("utf-8")
    return suc({"private_key": base64.b64encode(der).decode("utf-8"), "public_key": pem_to_flat(public_pem)})
