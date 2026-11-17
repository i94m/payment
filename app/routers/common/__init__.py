import json
import uuid
import hmac
import hashlib
import base64
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, Query

from app.core.limiter import rate_limit
from fastapi.responses import JSONResponse

from app.core.deps import CacheDep
from app.core.security import PermissionChecker
from app.core.config import config
from app.addons.storage import get_storage
from app.responses import suc, err

router = APIRouter()


# ============================================================
# 图片验证码 GET /admin-api/captcha
# ============================================================

@router.get("/captcha")
async def captcha(cache: CacheDep, _rl=rate_limit(times=20)):
    """生成图片验证码。返回 {content: data URL, key}，key 5 分钟内有效且一次性。"""
    import uuid

    from app.lib.captcha import generate_image_captcha

    content, code = generate_image_captcha()
    key = uuid.uuid4().hex[:26].lower()
    await cache.set(f"captcha:{key}", code, ex=300)
    return suc({"content": content, "key": key})


# ============================================================
# 获取上传 STS GET /admin-api/upload/sts
# ============================================================

@router.get("/upload/sts", dependencies=[Depends(PermissionChecker("system.admin"))])
async def upload_sts(
    request: Request,
    policy: str = Query(..., description="上传策略名"),
    cache: CacheDep = ...,
):
    """
    获取上传临时凭证

    对齐 Laravel StorageController::sts()
    Body: policy=xxx

    仅超管/持 system.admin 的管理员可用：STS 是写入存储的凭据，
    登录即可获取等于人人可往存储盘写文件（B04）。
    """
    upload_policies = config.UPLOAD_POLICIES

    if policy not in upload_policies:
        return err(400, "上传策略不存在")

    policy_config = upload_policies[policy]

    disk = config.STORAGE_PROVIDER if config.STORAGE_PROVIDER in ('cos', 'oss') else policy_config.get('disk', 'local')

    # 子目录
    frequency = policy_config.get('frequency', '')
    now = datetime.now()
    sub_dir = ''
    if frequency == 'yearly':
        sub_dir = f"{now.year}/"
    elif frequency == 'monthly':
        sub_dir = f"{now.strftime('%Y%m')}/"
    elif frequency == 'daily':
        sub_dir = f"{now.strftime('%Y%m%d')}/"

    prefix = policy_config.get('prefix', '') + sub_dir

    # 缓存避免频繁生成。key 里带上策略前缀：同一 Redis 可能被多套系统共用，
    # 仅用 disk + policy 会命中别的系统写入的 STS（前缀/存储密钥都不同，直传验签必然失败）。
    cache_key = f"upload.sts.{disk}.{policy}.{prefix}.{request.client.host}"
    cached = await cache.get(cache_key)
    if cached:
        return suc(json.loads(cached) if isinstance(cached, str) else cached)

    # STS 有效期
    sts_expires = config.STORAGE_STS_EXPIRE_MINUTES * 60
    expire_seconds = min(sts_expires, policy_config.get('expire_seconds', sts_expires))

    # 公共参数
    options = {
        'prefix': prefix,
        'expire_seconds': expire_seconds,
        'max_file_size': policy_config.get('max_file_size', 0),
        'extensions': policy_config.get('extensions', []),
        'success_action_status': 201,
    }

    # 根据 disk 类型生成 STS
    if disk in ('cos', 'oss'):
        sts_data = _generate_cloud_sts(disk, options)
    else:
        sts_data = _generate_local_sts(disk, options)

    # 组装返回结果（对齐 Laravel: options + sts_data 合并）
    result = {
        'provider': disk,
        'prefix': prefix,
        'expire_seconds': expire_seconds,
        'max_file_size': options['max_file_size'],
        'extensions': options['extensions'],
        'maximum': policy_config.get('maximum', 0),
        'success_action_status': options['success_action_status'],
        **sts_data,
    }

    # 缓存
    await cache.set(cache_key, json.dumps(result), ex=max(expire_seconds - 30, 60))

    return suc(result)


def _generate_local_sts(disk: str, options: dict) -> dict:
    """生成本地存储 STS（对齐 Laravel generateLocalStorageSts）"""
    storage_secret = config.STORAGE_LOCAL_SECRET

    sts_policy = {
        'disk': disk,
        'prefix': options['prefix'],
        'max_file_size': options['max_file_size'],
        'extensions': options['extensions'],
        'expiration': int(time.time()) + options['expire_seconds'],
    }
    sorted_policy = dict(sorted(sts_policy.items()))
    policy_b64 = base64.b64encode(json.dumps(sorted_policy, separators=(',', ':')).encode()).decode()
    signature = hmac.new(
        storage_secret.encode(),
        policy_b64.encode(),
        hashlib.sha1
    ).hexdigest()

    return {
        'url': config.STORAGE_LOCAL_HOST,
        'upload_url': '/upload',
        'payload': {
            'success_action_status': options['success_action_status'],
            'policy': policy_b64,
            'signature': signature,
        }
    }


def _generate_cloud_sts(disk: str, options: dict) -> dict:
    """生成云存储 STS（COS/OSS，对齐 Laravel CosAdapter::sts / OssAdapter::sts）"""
    if disk == 'cos':
        return _generate_cos_sts(options)
    elif disk == 'oss':
        return _generate_oss_sts(options)
    else:
        return _generate_local_sts(disk, options)


def _generate_cos_sts(options: dict) -> dict:
    """生成腾讯云 COS STS（对齐 Laravel CosAdapter::sts）"""
    secret_id = config.STORAGE_COS_SECRET_ID
    secret_key = config.STORAGE_COS_SECRET_KEY
    bucket = config.STORAGE_COS_BUCKET
    region = config.STORAGE_COS_REGION
    cos_host = config.STORAGE_COS_HOST

    max_file_size = options['max_file_size'] or 1024 * 1024 * 1024
    expire_seconds = options['expire_seconds']
    prefix = options['prefix']

    key_time = f"{int(time.time())};{int(time.time()) + expire_seconds}"

    # COS policy（JSON，base64 编码）
    cos_policy = json.dumps({
        'expiration': datetime.fromtimestamp(
            int(time.time()) + expire_seconds, tz=timezone.utc
        ).strftime('%Y-%m-%dT%H:%M:%S.000Z'),
        'conditions': [
            {'bucket': bucket},
            ['starts-with', '$key', prefix],
            ['content-length-range', 0, max_file_size],
            {'q-sign-algorithm': 'sha1'},
            {'q-ak': secret_id},
            {'q-sign-time': key_time},
        ],
    }, ensure_ascii=False)

    # 签名
    sign_key = hmac.new(
        secret_key.encode(), key_time.encode(), hashlib.sha1
    ).hexdigest()
    string_to_sign = hashlib.sha1(cos_policy.encode()).hexdigest()
    signature = hmac.new(
        sign_key.encode(), string_to_sign.encode(), hashlib.sha1
    ).hexdigest()

    # 上传地址
    upload_url = cos_host or f"https://{bucket}.cos.{region}.myqcloud.com"
    url = cos_host or f"https://{bucket}.cos.{region}.myqcloud.com"

    return {
        'url': url,
        'upload_url': upload_url,
        'payload': {
            'q-ak': secret_id,
            'q-sign-algorithm': 'sha1',
            'q-key-time': key_time,
            'q-signature': signature,
            'policy': base64.b64encode(cos_policy.encode()).decode(),
            'success_action_status': options['success_action_status'],
        }
    }


def _generate_oss_sts(options: dict) -> dict:
    """生成阿里云 OSS STS（对齐阿里云 PostObject 签名）"""
    access_key_id = config.STORAGE_OSS_ACCESS_KEY_ID
    access_key_secret = config.STORAGE_OSS_ACCESS_KEY_SECRET
    bucket = config.STORAGE_OSS_BUCKET
    endpoint = config.STORAGE_OSS_ENDPOINT
    region = config.STORAGE_OSS_REGION
    oss_host = config.STORAGE_OSS_HOST

    expire_seconds = options['expire_seconds']
    expiration = datetime.fromtimestamp(
        int(time.time()) + expire_seconds, tz=timezone.utc
    ).strftime('%Y-%m-%dT%H:%M:%SZ')

    # OSS policy（JSON，base64 编码）
    oss_policy_dict = {
        'expiration': expiration,
        'conditions': [
            ['starts-with', '$key', options['prefix']],
            ['content-length-range', 0, options['max_file_size'] or 1024 * 1024 * 1024],
        ],
    }
    oss_policy_b64 = base64.b64encode(
        json.dumps(oss_policy_dict).encode()
    ).decode()

    # 签名（HMAC-SHA1，对齐阿里云规范：base64 编码）
    signature = base64.b64encode(hmac.new(
        access_key_secret.encode(),
        oss_policy_b64.encode(),
        hashlib.sha1
    ).digest()).decode()

    # 上传地址
    upload_url = oss_host or f"https://{bucket}.{endpoint}"
    url = oss_host or f"https://{bucket}.{endpoint}"

    return {
        'url': url,
        'upload_url': upload_url,
        'payload': {
            'OSSAccessKeyId': access_key_id,
            'policy': oss_policy_b64,
            'signature': signature,
            'success_action_status': options['success_action_status'],
        }
    }


# ============================================================
# 上传文件 POST /upload (独立路由，非 admin-api 下)
# ============================================================

@router.post("/upload")
async def upload_file(
    request: Request,
    file: UploadFile = File(..., alias="file"),
    key: str = Form(default=""),
    policy: str = Form(default=""),
    signature: str = Form(default=""),
    name: str = Form(default="file"),
    success_action_status: int = Form(default=201),
):
    """
    上传文件（STS 模式，通过 policy + signature 鉴权）

    对齐 Laravel StorageController::upload()
    """
    # 验证 policy 签名
    policy_data = _verify_policy(policy, signature)
    if not policy_data:
        return JSONResponse(status_code=403, content={"code": -1, "message": "无效的上传策略"})

    # 读取文件内容
    content = await file.read()

    # 文件大小限制
    max_size = policy_data.get('max_file_size', 10 * 1024 * 1024)
    if max_size and len(content) > max_size:
        return JSONResponse(status_code=403, content={"code": -1, "message": "文件大小超出限制"})

    # 文件名
    if not key:
        ext = file.filename.rsplit('.', 1)[-1] if file.filename and '.' in file.filename else 'bin'
        key = uuid.uuid4().hex + '.' + ext
        prefix = policy_data.get('prefix', '')
        key = prefix + key

    # 路径遍历防护
    if '..' in key or key.startswith('/'):
        return JSONResponse(status_code=403, content={"code": -1, "message": "文件key不合法"})

    # 检查 prefix
    policy_prefix = policy_data.get('prefix', '')
    if policy_prefix and not key.startswith(policy_prefix):
        return JSONResponse(status_code=403, content={"code": -1, "message": "文件key不合法"})

    # 文件扩展名白名单验证
    extensions = policy_data.get('extensions', [])
    if extensions and '*' not in extensions:
        ext = key.rsplit('.', 1)[-1].lower() if '.' in key else ''
        if ext and f".{ext}" not in extensions and ext not in extensions:
            return JSONResponse(status_code=403, content={"code": -1, "message": "文件类型不支持"})

    # 通过存储驱动保存文件
    storage = get_storage(policy_data.get('disk'))
    result = await storage.upload(key, content)

    return JSONResponse(
        status_code=success_action_status,
        content={"key": result["key"], "url": result["url"]}
    )


def _verify_policy(policy_b64: str, signature: str) -> dict | None:
    """验证 STS policy 签名，返回 policy 数据或 None"""
    if not policy_b64 or not signature:
        return None

    storage_secret = config.STORAGE_LOCAL_SECRET

    # 对齐 Laravel: sha1 签名
    expected = hmac.new(
        storage_secret.encode(),
        policy_b64.encode(),
        hashlib.sha1
    ).hexdigest()

    if not hmac.compare_digest(signature, expected):
        return None

    try:
        payload = json.loads(base64.b64decode(policy_b64))
    except Exception:
        return None

    # 检查过期
    expiration = payload.get('expiration', 0)
    if expiration and int(expiration) < time.time():
        return None

    return payload


# ============================================================
# 文件列表 GET /admin-api/storage/files
# ============================================================

@router.get("/storage/files", dependencies=[Depends(PermissionChecker("system.admin"))])
async def storage_files(
    request: Request,
    disk: str = Query(default="local"),
    dir: str = Query(default=""),
    prefix: str = Query(default=""),
    marker: str = Query(default=""),
    limit: int = Query(default=100),
):
    """
    获取文件列表

    对齐 Laravel StorageController::files()

    仅超管/持 system.admin 的管理员可用：存储盘里含日志与上传件，
    登录即可枚举属于越权读取（B04）。
    """
    # 路径遍历防护
    if dir and ".." in dir:
        return err(400, "非法的目录参数")

    storage = get_storage(disk)
    files = await storage.get_list({
        'prefix': dir or prefix or '',
        'limit': limit,
    })
    return suc(files)


# ============================================================
# 广播认证 POST /admin-api/broadcasting/auth
# ============================================================

@router.post("/broadcasting/auth", dependencies=[Depends(PermissionChecker("system.admin"))])
async def broadcasting_auth():
    """
    广播认证

    对齐 Laravel BroadcastController::authenticate()
    直接返回成功（Laravel Broadcasting 认证通常用于 WebSocket，这里 mock）
    """
    return suc([])
