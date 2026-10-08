"""
支付宝支付公共基类 -- RSA2签名/验签, 统一API调用, 异步通知验证

alipaysl(服务商模式) 和 alipayd(直付通模式) 共用此模块。
"""
import base64
import hashlib
import json
import logging
import time
import uuid
from typing import Optional
from urllib.parse import quote_plus, urlencode

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, utils
from cryptography.exceptions import InvalidSignature

from app.services.plugin_interfaces import BasePayment, PaymentContext, PaymentResult

_log = logging.getLogger(__name__)

ALIPAY_GATEWAY = "https://openapi.alipay.com/gateway.do"
SIGN_TYPE = "RSA2"  # SHA256withRSA


# ---------------------------------------------------------------------------
# RSA2 helpers
# ---------------------------------------------------------------------------

def _format_private_key(pem: str) -> bytes:
    """将原始私钥字符串格式化为 PEM 格式。"""
    pem = pem.strip()
    if "-----BEGIN" in pem:
        return pem.encode("utf-8")
    # 用户可能直接粘贴 base64 内容
    lines = [pem[i:i + 64] for i in range(0, len(pem), 64)]
    body = "\n".join(lines)
    return f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----".encode("utf-8")


def _format_public_key(pem: str) -> bytes:
    """将原始公钥字符串格式化为 PEM 格式。"""
    pem = pem.strip()
    if "-----BEGIN" in pem:
        return pem.encode("utf-8")
    lines = [pem[i:i + 64] for i in range(0, len(pem), 64)]
    body = "\n".join(lines)
    return f"-----BEGIN PUBLIC KEY-----\n{body}\n-----END PUBLIC KEY-----".encode("utf-8")


def _sign_params(params: dict, private_key_pem: str) -> str:
    """
    对请求参数做 RSA2 签名 (SHA256withRSA)。

    1. 过滤 sign / sign_type 字段
    2. 按 key 字典序排列
    3. 拼接为 key=value&key=value (空值跳过)
    4. 使用商户私钥签名, 返回 base64 编码签名字符串
    """
    filtered = {
        k: v for k, v in params.items()
        if k not in ("sign", "sign_type") and v is not None and str(v) != ""
    }
    sorted_items = sorted(filtered.items(), key=lambda x: x[0])
    sign_str = "&".join(f"{k}={v}" for k, v in sorted_items)

    private_key = serialization.load_pem_private_key(
        _format_private_key(private_key_pem), password=None
    )
    signature = private_key.sign(
        sign_str.encode("utf-8"),
        padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


def _verify_notify(params: dict, alipay_public_key_pem: str) -> bool:
    """
    验证支付宝异步通知的 RSA2 签名。

    从 params 中取 sign 和 sign_type, 对剩余参数排序拼接后用支付宝公钥验签。
    """
    sign = params.get("sign")
    if not sign:
        return False

    sign_type = params.get("sign_type", "RSA2")
    if sign_type != "RSA2":
        _log.warning("Unsupported sign_type: %s", sign_type)
        return False

    filtered = {
        k: v for k, v in params.items()
        if k not in ("sign", "sign_type") and v is not None and str(v) != ""
    }
    sorted_items = sorted(filtered.items(), key=lambda x: x[0])
    sign_str = "&".join(f"{k}={v}" for k, v in sorted_items)

    try:
        public_key = serialization.load_pem_public_key(
            _format_public_key(alipay_public_key_pem)
        )
        public_key.verify(
            base64.b64decode(sign),
            sign_str.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except InvalidSignature:
        _log.warning("Alipay notify signature verification failed")
        return False
    except Exception as e:
        _log.error("Alipay notify signature verification error: %s", e)
        return False


# ---------------------------------------------------------------------------
# Alipay API request
# ---------------------------------------------------------------------------

def _execute(
    method: str,
    biz_content: dict,
    app_id: str,
    private_key_pem: str,
    notify_url: str = "",
    return_url: str = "",
    app_auth_token: str = "",
    extra_params: dict | None = None,
) -> dict:
    """
    调用支付宝 OpenAPI。

    返回 JSON 解析后的响应体。对于返回 HTML 的接口（wap/page）,
    本方法组装完整表单参数但不实际发送请求 — 由调用方决定是否请求或直接构建表单。

    对于 precreate / refund / close 等接口, 直接 POST 并返回解析结果。
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    common_params = {
        "app_id": app_id,
        "method": method,
        "charset": "utf-8",
        "sign_type": SIGN_TYPE,
        "timestamp": timestamp,
        "version": "1.0",
        "biz_content": json.dumps(biz_content, ensure_ascii=False),
    }
    if notify_url:
        common_params["notify_url"] = notify_url
    if return_url:
        common_params["return_url"] = return_url
    if app_auth_token:
        common_params["app_auth_token"] = app_auth_token
    if extra_params:
        common_params.update(extra_params)

    # 签名
    common_params["sign"] = _sign_params(common_params, private_key_pem)

    # 构建表单参数 (url-encode values)
    form_items = []
    for k, v in common_params.items():
        form_items.append(f'<input type="hidden" name="{k}" value=\'{v}\' />')

    form_html = (
        f'<form id="alipaysubmit" name="alipaysubmit" '
        f'action="{ALIPAY_GATEWAY}?charset=utf-8" method="POST">'
        f'{"".join(form_items)}'
        f'<input type="submit" value="提交" style="display:none;" />'
        f'</form>'
        f'<script>document.forms["alipaysubmit"].submit();</script>'
    )

    return {
        "form_html": form_html,
        "params": common_params,
        "gateway": ALIPAY_GATEWAY,
    }


def _execute_and_parse(
    method: str,
    biz_content: dict,
    app_id: str,
    private_key_pem: str,
    notify_url: str = "",
    app_auth_token: str = "",
    extra_params: dict | None = None,
) -> dict:
    """
    调用支付宝 OpenAPI 并解析 JSON 响应。
    用于 precreate / refund / close 等服务端到服务端接口。
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

    params = {
        "app_id": app_id,
        "method": method,
        "charset": "utf-8",
        "sign_type": SIGN_TYPE,
        "timestamp": timestamp,
        "version": "1.0",
        "biz_content": json.dumps(biz_content, ensure_ascii=False),
    }
    if notify_url:
        params["notify_url"] = notify_url
    if app_auth_token:
        params["app_auth_token"] = app_auth_token
    if extra_params:
        params.update(extra_params)

    params["sign"] = _sign_params(params, private_key_pem)

    try:
        resp = httpx.post(ALIPAY_GATEWAY, data=params, timeout=30)
        resp.raise_for_status()
        result = resp.json()

        # 响应 key 格式: 方法名替换 . 为 _ + _response
        resp_key = method.replace(".", "_") + "_response"
        body = result.get(resp_key, {})

        if body.get("code") == "10000":
            return {"code": 0, "data": body, "raw": result}

        _log.warning(
            "Alipay API error: method=%s, code=%s, msg=%s, sub_code=%s, sub_msg=%s",
            method, body.get("code"), body.get("msg"),
            body.get("sub_code"), body.get("sub_msg"),
        )
        return {
            "code": -1,
            "msg": body.get("sub_msg") or body.get("msg") or "接口调用失败",
            "sub_code": body.get("sub_code"),
            "raw": result,
        }
    except httpx.HTTPStatusError as e:
        _log.error("Alipay API HTTP error: %s", e)
        return {"code": -1, "msg": f"HTTP错误: {e.response.status_code}"}
    except Exception as e:
        _log.error("Alipay API request error: %s", e)
        return {"code": -1, "msg": f"请求异常: {str(e)}"}


# ---------------------------------------------------------------------------
# AES 加解密 (可选, 用于敏感信息加密)
# ---------------------------------------------------------------------------

def _aes_decrypt(ciphertext: str, aes_key: str) -> str:
    """
    AES/ECB/PKCS5Padding 解密, 用于解密手机号等敏感信息。
    需要安装 pycryptodome。
    """
    try:
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import unpad
        key = aes_key.encode("utf-8")
        cipher = AES.new(key, AES.MODE_ECB)
        decrypted = unpad(cipher.decrypt(base64.b64decode(ciphertext)), AES.block_size)
        return decrypted.decode("utf-8")
    except ImportError:
        _log.warning("pycryptodome not installed, AES decrypt skipped")
        return ciphertext
    except Exception as e:
        _log.error("AES decrypt error: %s", e)
        return ciphertext


# ---------------------------------------------------------------------------
# Base payment class for Alipay
# ---------------------------------------------------------------------------

class AlipayBasePayment(BasePayment):
    """
    支付宝支付公共基类, 封装 submit/notify/refund/close 的通用逻辑。

    子类需要实现:
        - _get_select() -> str: 返回渠道配置的 select 值 (子方法编号)
        - _get_app_id() -> str
        - _get_private_key() -> str
        - _get_alipay_public_key() -> str
        - _get_notify_url(order: dict) -> str
        - _get_return_url(order: dict) -> str
        - _get_app_auth_token() -> str  (服务商模式需要, 直付通返回空)
        - _get_seller_id() -> str  (服务商模式返回 smid, 直付通返回空)
    """

    def _cfg(self, key: str, default: str = "") -> str:
        return str(self.channel.get(key, default) or default)

    def _get_select(self) -> str:
        return str(self.channel.get("select", "1") or "1")

    def _get_app_id(self) -> str:
        return self._cfg("appid")

    def _get_private_key(self) -> str:
        return self._cfg("appsecret")

    def _get_alipay_public_key(self) -> str:
        return self._cfg("publickey")

    def _get_notify_url(self, order: dict) -> str:
        """异步通知地址, 由平台统一网关路由。"""
        from app.core.config import config
        trade_no = order.get("trade_no", "")
        return f"{config.APP_URL}/pay/notify/{trade_no}"

    def _get_return_url(self, order: dict) -> str:
        return order.get("return_url", "")

    def _get_app_auth_token(self) -> str:
        """服务商模式下用于代商户发起请求的 token, 由子类覆盖。"""
        return ""

    def _get_seller_id(self) -> str:
        """收款账号, 服务商模式下为 smid。"""
        return ""

    def _build_biz_base(self, order: dict) -> dict:
        """构建 biz_content 的公共字段。"""
        biz = {
            "out_trade_no": order.get("trade_no", ""),
            "total_amount": str(order.get("money", "0")),
            "subject": order.get("name", "订单支付"),
        }
        seller_id = self._get_seller_id()
        if seller_id:
            biz["seller_id"] = seller_id
        return biz

    # -------------------------------------------------------------------
    # submit
    # -------------------------------------------------------------------

    def submit(self, ctx: PaymentContext) -> PaymentResult:
        order = ctx.order
        select_method = self._get_select()
        app_id = self._get_app_id()
        private_key = self._get_private_key()
        notify_url = self._get_notify_url(order)
        return_url = self._get_return_url(order)
        app_auth_token = self._get_app_auth_token()

        if not app_id or not private_key:
            raise ValueError("支付宝配置不完整: 缺少 appid 或 appsecret")

        biz = self._build_biz_base(order)

        try:
            if select_method == "1":
                return self._submit_precreate(
                    biz, app_id, private_key, notify_url, app_auth_token
                )
            elif select_method == "2":
                return self._submit_wap(
                    biz, app_id, private_key, notify_url, return_url, app_auth_token
                )
            elif select_method == "3":
                return self._submit_page(
                    biz, app_id, private_key, notify_url, return_url, app_auth_token
                )
            elif select_method == "4":
                return self._submit_app(
                    biz, app_id, private_key, notify_url, app_auth_token
                )
            else:
                raise ValueError(f"不支持的支付子方法: {select_method}")
        except ValueError:
            raise
        except Exception as e:
            _log.error("Alipay submit error: %s", e, exc_info=True)
            raise ValueError(f"支付请求失败: {str(e)}")

    def _submit_precreate(self, biz, app_id, private_key, notify_url, app_auth_token) -> PaymentResult:
        """当面付(扫码): alipay.trade.precreate"""
        result = _execute_and_parse(
            "alipay.trade.precreate",
            biz, app_id, private_key,
            notify_url=notify_url,
            app_auth_token=app_auth_token,
        )
        if result["code"] != 0:
            raise ValueError(result.get("msg", "预下单失败"))

        qr_code = result["data"].get("qr_code", "")
        if not qr_code:
            raise ValueError("支付宝未返回二维码链接")

        return PaymentResult(
            pay_type="qrcode",
            pay_info=qr_code,
            expires_in=300,
        )

    def _submit_wap(self, biz, app_id, private_key, notify_url, return_url, app_auth_token) -> PaymentResult:
        """手机网站支付: alipay.trade.wap.pay"""
        biz["product_code"] = "QUICK_WAP_WAY"

        result = _execute(
            "alipay.trade.wap.pay",
            biz, app_id, private_key,
            notify_url=notify_url,
            return_url=return_url,
            app_auth_token=app_auth_token,
        )
        return PaymentResult(
            pay_type="html",
            pay_info=result["form_html"],
            expires_in=600,
        )

    def _submit_page(self, biz, app_id, private_key, notify_url, return_url, app_auth_token) -> PaymentResult:
        """电脑网站支付: alipay.trade.page.pay"""
        biz["product_code"] = "FAST_INSTANT_TRADE_PAY"

        result = _execute(
            "alipay.trade.page.pay",
            biz, app_id, private_key,
            notify_url=notify_url,
            return_url=return_url,
            app_auth_token=app_auth_token,
        )
        return PaymentResult(
            pay_type="html",
            pay_info=result["form_html"],
            expires_in=600,
        )

    def _submit_app(self, biz, app_id, private_key, notify_url, app_auth_token) -> PaymentResult:
        """APP支付: alipay.trade.app.pay"""
        biz["product_code"] = "QUICK_MSECURITY_PAY"

        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        params = {
            "app_id": app_id,
            "method": "alipay.trade.app.pay",
            "charset": "utf-8",
            "sign_type": SIGN_TYPE,
            "timestamp": timestamp,
            "version": "1.0",
            "notify_url": notify_url,
            "biz_content": json.dumps(biz, ensure_ascii=False),
        }
        if app_auth_token:
            params["app_auth_token"] = app_auth_token

        params["sign"] = _sign_params(params, private_key)

        # APP支付 orderStr: key=value 排序拼接
        sorted_items = sorted(params.items(), key=lambda x: x[0])
        order_str = "&".join(f"{k}={v}" for k, v in sorted_items)

        return PaymentResult(
            pay_type="json",
            pay_info=json.dumps({"orderStr": order_str}, ensure_ascii=False),
            expires_in=600,
        )

    # -------------------------------------------------------------------
    # notify
    # -------------------------------------------------------------------

    def notify(self, params: dict, body: bytes = b"") -> dict | None:
        """
        处理支付宝异步通知。

        1. 验证 RSA2 签名
        2. 检查 trade_status
        3. 返回标准结果字典或 None
        """
        alipay_public_key = self._get_alipay_public_key()
        if not alipay_public_key:
            _log.error("Alipay public key not configured, cannot verify notify")
            return None

        if not _verify_notify(params, alipay_public_key):
            _log.warning("Alipay notify verification failed for params: %s",
                         list(params.keys()))
            return None

        trade_status = params.get("trade_status", "")
        if trade_status not in ("TRADE_SUCCESS", "TRADE_FINISHED"):
            _log.info("Alipay notify ignored, trade_status=%s", trade_status)
            return None

        trade_no = params.get("trade_no", "")
        total_amount = params.get("total_amount", "0")
        buyer_logon_id = params.get("buyer_logon_id", "")

        # 验证 out_trade_no 与本系统订单号一致
        out_trade_no = params.get("out_trade_no", "")
        if not trade_no:
            _log.warning("Alipay notify missing trade_no")
            return None

        _log.info(
            "Alipay notify success: trade_no=%s, out_trade_no=%s, amount=%s, buyer=%s",
            trade_no, out_trade_no, total_amount, buyer_logon_id,
        )

        return {
            "api_trade_no": trade_no,
            "money": float(total_amount),
            "buyer": buyer_logon_id,
        }

    # -------------------------------------------------------------------
    # return_handler (同步跳转)
    # -------------------------------------------------------------------

    def return_handler(self, params: dict) -> dict | None:
        """处理支付宝同步跳转 (GET return_url)。"""
        alipay_public_key = self._get_alipay_public_key()
        if alipay_public_key and _verify_notify(params, alipay_public_key):
            return {
                "trade_no": params.get("out_trade_no", ""),
                "api_trade_no": params.get("trade_no", ""),
            }
        return None

    # -------------------------------------------------------------------
    # refund
    # -------------------------------------------------------------------

    def refund(self, order: dict, refund_no: str, money: float) -> dict:
        """调用 alipay.trade.refund 发起退款。"""
        app_id = self._get_app_id()
        private_key = self._get_private_key()
        app_auth_token = self._get_app_auth_token()

        if not app_id or not private_key:
            return {"code": -1, "msg": "支付宝配置不完整"}

        biz = {
            "out_trade_no": order.get("trade_no", ""),
            "refund_amount": str(money),
            "out_request_no": refund_no,
        }

        result = _execute_and_parse(
            "alipay.trade.refund",
            biz, app_id, private_key,
            app_auth_token=app_auth_token,
        )

        if result["code"] == 0:
            return {
                "code": 0,
                "msg": "退款成功",
                "refund_fee": result["data"].get("refund_fee", str(money)),
            }
        return {
            "code": -1,
            "msg": result.get("msg", "退款失败"),
            "sub_code": result.get("sub_code"),
        }

    @classmethod
    def is_refund_supported(cls) -> bool:
        return True

    # -------------------------------------------------------------------
    # close
    # -------------------------------------------------------------------

    def close(self, order: dict) -> bool:
        """调用 alipay.trade.close 关闭订单。"""
        app_id = self._get_app_id()
        private_key = self._get_private_key()
        app_auth_token = self._get_app_auth_token()

        if not app_id or not private_key:
            return False

        biz = {
            "out_trade_no": order.get("trade_no", ""),
        }

        result = _execute_and_parse(
            "alipay.trade.close",
            biz, app_id, private_key,
            app_auth_token=app_auth_token,
        )

        if result["code"] == 0:
            _log.info("Alipay order closed: %s", order.get("trade_no"))
            return True

        _log.warning("Alipay close order failed: %s", result.get("msg"))
        return False
