"""插件示例模板（伪代码）—— AI 写新插件从这里复制开始。

⚠️ 本目录以下划线开头（_example）：PluginRegistry 跳过 `_` 前缀目录，
   本模板【不会】被注册、不会出现在 admin 后台插件列表。

使用方法：
1. 复制整个 _example 目录 → plugins/<你的插件名>/
2. example.py 重命名为 plugin.py，info.example.json 重命名为 info.json
   （id 改成与目录名一致，version 新插件一律 1.0.0）
3. 按下方注释逐段替换伪代码；硬性规则见 plugins/README.md，
   完整可运行的参考实现见 README.md §10（epay / shuangzi 最贴近本骨架）

接口定义：app/services/plugin_interfaces.py（写之前先通读一遍）
"""
import hashlib
import logging
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.services.http_client import get_http_client
from app.services.plugin_interfaces import (
    BasePayment,
    NotifyVerdict,
    PaymentContext,
    PaymentResult,
    PluginMeta,
    QueryResult,
)

_log = logging.getLogger(__name__)


# ============================================================
# 一、签名（按上游文档实现，以下是最常见的 MD5 协议范式）
# ============================================================
def _make_sign(params: dict, key: str) -> str:
    """伪代码：MD5 签名范式（真实实现参考 plugins/epay/plugin.py）。

    1. 排除 sign / sign_type 字段
    2. 排除空值：'' 和 None 不参与，但 "0" 必须参与（历史大坑！）
    3. 键名 ASCII 升序（≙ PHP ksort SORT_STRING）
    4. 拼 "k=v"，连接符按上游文档（"&"）
    5. 末尾拼密钥：有的协议直接拼 key，有的拼 "&key=密钥" —— 逐字核对文档！
    6. md5 小写 hexdigest
    7. 【验收】上游文档有签名示例向量的，必须先离线比对通过再往下写
    """
    keys = sorted(k for k, v in params.items()
                  if k not in ("sign", "sign_type")
                  and v is not None and v != ""
                  and not isinstance(v, (list, dict)))
    content = "&".join(f"{k}={params[k]}" for k in keys)
    return hashlib.md5((content + key).encode("utf-8")).hexdigest()  # 末尾拼接方式按文档调整


def _to_money(value) -> Decimal | None:
    """回调金额解析：失败返回 None（绝不能吞异常后放行）。"""
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None


# ============================================================
# 二、插件类
# ============================================================
class ExamplePayment(BasePayment):
    """<上游名称>协议上游。"""

    # ---- 元信息：admin 后台按 inputs 动态渲染"渠道凭证"表单 ----
    meta = PluginMeta(
        name="example",                      # ★ 必须与目录名、info.json.id 三者一致
        showname="示例支付",
        types=["alipay", "wxpay"],           # 只能取系统支付方式 code
        inputs=[
            # ★ 硬性规则：每个 input 型字段必须带 placeholder，
            #   且是"可直接照抄的真实格式示例"，不是"请输入xxx"空话
            {"name": "appurl", "title": "网关地址", "type": "input",
             "placeholder": "https://pay.example.com/gateway/api", "required": True},
            {"name": "appid", "title": "商户号", "type": "input",
             "placeholder": "如 10001（上游商户后台的数字ID）", "required": True},
            {"name": "appkey", "title": "商户密钥", "type": "input",
             "placeholder": "上游商户后台分配的32位密钥，用于MD5签名", "required": True},
            # select 型用 options，不需要 placeholder
            {"name": "paymode", "title": "支付模式", "type": "select",
             "options": {"1": "跳转", "2": "扫码"}},
        ],
    )

    @classmethod
    def is_refund_supported(cls) -> bool:
        return False  # 上游有退款接口才改 True 并实现 refund()

    # ---- 凭证读取：统一走 self.channel（已解密 dict），键名 = inputs 里的 name ----
    def _cfg(self, name: str) -> str:
        return str(self.channel.get(name, "") or "").strip()

    # ========================================================
    # 三、发起支付（唯一必须实现的方法）
    # ========================================================
    async def submit(self, ctx: PaymentContext) -> PaymentResult:
        # ctx.order 是订单快照（trade_no/realmoney/payment_type_code/subject...），
        # 只读，禁止修改；ctx.notify_url/return_url 原样使用，禁止自行拼改
        params = {
            "merchantId": self._cfg("appid"),
            "orderId": ctx.order["trade_no"],
            "amount": f"{Decimal(str(ctx.order['realmoney'])):.2f}",  # 金额单位元、两位小数
            "notifyUrl": ctx.notify_url,
            "returnUrl": ctx.return_url,
            # ... 其余业务参数按上游文档
        }
        params["sign"] = _make_sign(params, self._cfg("appkey"))

        # HTTP 一律用共享异步客户端，禁止自建 httpx.AsyncClient / requests
        try:
            resp = await get_http_client().post(self._cfg("appurl"), data=params)  # 表单用 data=，JSON 用 json=
            body = resp.json()
        except Exception as e:
            _log.warning("example 下单失败 trade_no=%s: %s", ctx.order["trade_no"], e)
            return PaymentResult(kind="error", msg="上游通道连接失败")

        # 按上游响应判断成功，把支付链接交给前端：
        #   jump=跳转 / qrcode=二维码内容 / scheme=小程序 scheme / html=整页HTML
        if body.get("code") == 0 and body.get("payUrl"):
            return PaymentResult(kind="jump", url=str(body["payUrl"]))
        return PaymentResult(kind="error", msg=str(body.get("msg") or "上游下单失败"))

    # ========================================================
    # 四、主动查单（掉单补单；上游没有查单接口就不实现本方法）
    # ========================================================
    async def query(self, ctx: PaymentContext) -> QueryResult:
        # 伪代码步骤：
        # 1. 组装查单参数（商户订单号 = ctx.order["trade_no"]）+ 签名
        # 2. 请求上游；任何异常 → return QueryResult(status="unknown")，不要抛出
        # 3. 响应判空判错（code/data 防御式解析），失败也是 unknown
        # 4. 成功状态映射到 paid/unpaid/failed；金额解析用 _to_money
        ...
        return QueryResult(status="unknown")

    # ========================================================
    # 五、异步回调（核心：验签 + 判成功 + 报数；不做金额业务比对！）
    # ========================================================
    async def notify_handler(self, ctx_params: dict, body: bytes = b"") -> NotifyVerdict:
        # ctx_params 已由路由层聚合好 GET + form + JSON，直接用
        # 金额比对（回调金额 vs 订单实付）由 services/order.process_paid_order 统一做，
        # 插件只负责把上游声称的金额解析成 Decimal 放进 verdict
        sign = str(ctx_params.get("sign", ""))
        if not sign or _make_sign(ctx_params, self._cfg("appkey")) != sign.lower():
            return NotifyVerdict(ok=False, ack="fail")          # 验签失败 → fail（上游会重发）

        # 状态码一律转字符串比较，防 "02" / int 差异
        if str(ctx_params.get("tradeStatus")) != "SUCCESS":
            return NotifyVerdict(ok=False, ack="success")       # 非成功态 → success（上游停止重发）

        money = _to_money(ctx_params.get("amount"))
        if money is None:
            return NotifyVerdict(ok=False, ack="fail")          # 金额解析失败 → 显式 fail，绝不放行

        return NotifyVerdict(
            ok=True,
            api_trade_no=str(ctx_params.get("apiOrderId") or ""),  # 上游流水号，留对账用
            money=money,
            ack="success",
        )

    # ========================================================
    # 六、同步回跳（仅当上游 return 带签名+成功态才需要；否则整段删掉）
    # ========================================================
    async def return_handler(self, ctx_params: dict) -> dict | None:
        # 验签通过且成功时返回 {"status": "paid", "api_trade_no": "..."}，否则 None
        return None

    # ========================================================
    # 七、退款（is_refund_supported=False 时整段删掉）
    # ========================================================
    # async def refund(self, order: dict, refund_no: str, amount: Decimal) -> dict:
    #     # 成功返回 {"status": 1, "msg": "..."}，失败 {"status": 0, "msg": "..."}
    #     ...
