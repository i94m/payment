"""下游开放 API 认证依赖（pid + RSA2 签名）。

请求形态：POST/PUT 一律 JSON body（签名在 body 参数内）；GET 用 query 参数。
"""
import json
import logging
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request
from sqlmodel import select

from app.core.deps import CacheDep, SessionDep
from app.logic.api_verify import ensure_not_replayed, verify_api_signature
from app.models import Merchant

_log = logging.getLogger(__name__)


@dataclass
class ApiAuthResult:
    merchant: Merchant
    params: dict


async def _extract_params(request: Request) -> dict[str, Any]:
    """POST/PUT 取 JSON body（request.body 只能读一次，先取原始字节再解析）；
    GET 取 query 参数。"""
    if request.method in ("POST", "PUT"):
        raw = await request.body()
        try:
            body = json.loads(raw or b"")
        except Exception:
            raise HTTPException(status_code=400, detail={"code": -2, "message": "请求体必须为合法JSON"})
        if not isinstance(body, dict) or not body:
            raise HTTPException(status_code=400, detail={"code": -4, "message": "未传入任何参数"})
        return body
    params: dict[str, Any] = dict(request.query_params)
    return params


async def get_api_user(request: Request, session: SessionDep,
                       cache: CacheDep = None) -> ApiAuthResult:
    """pid + RSA2 签名认证。失败抛 HTTPException(400, {"code": -x, "message": ...})。"""
    params = await _extract_params(request)

    pid = params.get("pid", "")
    if not pid:
        raise HTTPException(status_code=400, detail={"code": -1, "message": "商户ID不能为空"})
    try:
        pid_int = int(str(pid))
    except ValueError:
        raise HTTPException(status_code=400, detail={"code": -1, "message": "商户ID格式错误"})

    merchant = (await session.exec(
        select(Merchant).where(Merchant.pid == pid_int)
    )).first()
    # P1-15：不存在与封禁统一文案，避免用错误信息差异枚举有效 pid
    if not merchant or merchant.status != 1:
        raise HTTPException(status_code=400,
                            detail={"code": -1, "message": "商户不存在或已停用"})

    try:
        verify_api_signature(merchant.public_key, params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"code": -3, "message": str(e)})

    # 签名一次性校验：同一签名 300s 内只受理一次（防截获重放；验签通过后才消耗签名）
    try:
        await ensure_not_replayed(cache, merchant.id, params)
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"code": -3, "message": str(e)})

    # 下游 API 支付接口总开关（验签后判定：合法调用方得到明确的「未开放」，
    # 而非验签失败；平台关闭 API 接入时的统一拦截点）
    from app.services.settings import get_setting, setting_enabled
    api_enable = setting_enabled(await get_setting(session, "api_pay_enable", True, cache=cache))
    if not api_enable:
        raise HTTPException(status_code=400,
                            detail={"code": -1, "message": "API支付接口未开放"})

    return ApiAuthResult(merchant=merchant, params=params)


# 依赖别名
ApiUser = Annotated[ApiAuthResult, Depends(get_api_user)]
