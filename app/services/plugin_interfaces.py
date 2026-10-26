"""支付插件接口定义（异步版）。

设计边界：
- 插件只做协议（签名、HTTP、回调验签），不碰订单表、不做金额业务判断
- 回调金额 vs order.realmoney 的比对由 services/order.py 统一执行
- 所有 IO 方法都是 async def，插件内部必须使用 httpx.AsyncClient
  （共享 client 见 app/services/http_client.py）
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


@dataclass
class PluginMeta:
    """插件元信息（admin 动态渲染凭证表单用）。"""
    name: str                    # 必须与目录名一致
    showname: str
    types: list[str]             # 支持的 payment_type.code，如 ["alipay","wxpay"]
    inputs: list[dict] = field(default_factory=list)
    version: str = ""            # 插件版本（新插件一律 "1.0.0"，admin 列表展示/核对用）
    # inputs 元素示例：
    # {"name": "appurl", "title": "接口地址", "type": "input",
    #  "placeholder": "https://xxx/", "required": True}


@dataclass
class PaymentContext:
    """下单上下文（订单快照，插件禁止修改）。"""
    order: dict                  # trade_no/out_trade_no/subject/money/realmoney/payment_type_code...
    notify_url: str              # 上游回调我们的完整地址 /callbacks/{plugin}/{trade_no}
    return_url: str              # 上游支付完回跳我们的地址 /callbacks/{plugin}/{trade_no}/return
    site_url: str = ""
    is_mobile: bool = False
    client_ip: str = ""


@dataclass
class PaymentResult:
    kind: str                    # jump/qrcode/html/scheme/error
    url: str = ""                # jump/qrcode/scheme 时的跳转地址或二维码内容
    html: str = ""               # kind=html 时的页面内容
    msg: str = ""                # kind=error 时的错误信息
    expires_in: int = 300
    extra: dict = field(default_factory=dict)


@dataclass
class QueryResult:
    """主动查单结果（掉单补单用）。"""
    status: str                  # paid/unpaid/failed/unknown
    api_trade_no: str = ""
    money: Optional[Decimal] = None
    buyer_id: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class NotifyVerdict:
    """回调验签结论。金额业务比对由 services 执行。"""
    ok: bool                     # True=验签通过且交易成功
    api_trade_no: str = ""
    money: Optional[Decimal] = None
    buyer_id: str = ""
    ack: str = "success"         # 回给上游的应答字面量


class BasePayment(ABC):
    """支付插件基类（异步）。"""

    meta: PluginMeta

    def __init__(self, channel: dict):
        """channel: 通道解密后的凭证 dict + 通道参数。"""
        self.channel = channel
        # 最近一次上游交互的调试快照（url/params/耗时/响应原文），供管理端排障展示；
        # 插件自愿填充，无则视为不支持
        self.last_debug: dict | None = None

    @abstractmethod
    async def submit(self, ctx: PaymentContext) -> PaymentResult:
        """发起支付。"""
        ...

    async def query(self, ctx: PaymentContext) -> QueryResult:
        """主动查单（掉单补单）。默认不支持，返回 unknown。"""
        return QueryResult(status="unknown")

    async def notify_handler(self, ctx_params: dict, body: bytes = b"") -> NotifyVerdict:
        """处理上游异步回调：验签 + 判断成功。返回 NotifyVerdict。"""
        return NotifyVerdict(ok=False)

    async def return_handler(self, ctx_params: dict) -> dict | None:
        """同步回跳处理。返回 None 或 {"status": "paid"} 之类的信息。"""
        return None

    async def refund(self, order: dict, refund_no: str, amount: Decimal) -> dict:
        """发起退款。返回 {"status": 0/1, "msg": ...}。

        商户端退款入口（菜单/页面/开放API）已下线，本插件层接口保留备用。
        """
        raise NotImplementedError("该插件不支持退款")

    @classmethod
    def is_refund_supported(cls) -> bool:
        return False


class BaseSms(ABC):
    """短信插件基类（同步接口，站内通知用）。"""
    @abstractmethod
    def send(self, phone: str, tpl_code: str, tpl_param: dict) -> bool: ...


class BaseMail(ABC):
    """邮件插件基类（同步接口，站内通知用）。"""
    @abstractmethod
    def send(self, to: str, subject: str, body: str) -> bool: ...
