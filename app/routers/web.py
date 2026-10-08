"""面向浏览器的页面路由：收银台 / 聚合收款页 HTML（自包含单文件，无构建依赖）。

随机金额商户的收银台链接不展示收银台页面：服务端直接拉起支付（已拉起过则
复用落库的支付信息），按返回形态 302 跳转 / 渲染上游 HTML / 展示二维码 /
scheme 拉起页；失败或订单异常渲染统一结果页。随机金额商户的收款页 /p/{code}
同样不渲染表单：首访渲染倒计时页自动跳转，go=1 由服务端建单后 302 到收银台
链接完成真实拉起（对齐旧版 paypage/index.php 行为）。
"""
import html
import json
from decimal import Decimal
from urllib.parse import urlencode

import qrcode
import qrcode.image.svg
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.deps import CacheDep, SessionDep
from app.core.limiter import rate_limit
from app.models import Merchant, Order
from app.routers.paypage import (
    PaypageOrderRequest,
    create_paypage_order_core,
    load_paypage_merchant,
)
from app.services.balance import dmoney
from app.services.order import (
    ORDER_PAID,
    ORDER_PENDING,
    build_signed_return_url,
    get_order_by_trade_no,
    process_payment,
)
from app.services.settings import get_all_settings

router = APIRouter()


def _is_mobile(request: Request) -> bool:
    ua = request.headers.get("user-agent", "").lower()
    return any(k in ua for k in ("mobile", "android", "iphone", "ipad", "windows phone"))


async def _site_url(request: Request, session: SessionDep, cache: CacheDep) -> str:
    settings = await get_all_settings(session, cache)
    return (settings.get("site_url") or str(request.base_url)).rstrip("/")


async def _signed_return_url(order: Order, merchant: Merchant,
                             session: SessionDep, cache: CacheDep) -> str:
    if not order.return_url:
        return ""
    settings = await get_all_settings(session, cache)
    return build_signed_return_url(
        order, merchant, platform_rsa_key=settings.get("platform_rsa_private_key") or "",
    )


def _pay_display_money(order: Order) -> str:
    """付款人应支付金额：随机金额单展示实付（realmoney），否则申报金额。"""
    v = order.realmoney if order.realmoney is not None else order.money
    return f"{dmoney(v).quantize(Decimal('0.01')):f}"


# ============================================================
# 收银台入口：随机金额商户直接拉起支付，其余渲染自选支付方式页面
# ============================================================

@router.get("/cashier/{token}", include_in_schema=False)
async def cashier_opaque_page(request: Request, token: str,
                              session: SessionDep = None, cache: CacheDep = None,
                              _rl=rate_limit(times=30)):
    """收银台不透明链接（对外下发的默认形态）：链接不暴露 trade_no/下单时间。

    与 /cashier?trade_no= 同一处理逻辑；令牌无效渲染错误结果页。
    """
    from app.lib.linktoken import decode_link_token

    trade_no = decode_link_token(token)
    if not trade_no:
        return HTMLResponse(_render_result(
            "err", "链接无效", "支付链接无效或已过期，请从商户处重新发起付款",
            "", "", "",
        ))
    order = await get_order_by_trade_no(session, trade_no)
    if not order:
        return HTMLResponse(_render_result(
            "err", "订单不存在", "订单不存在或已失效", "", "", "",
        ))
    merchant = await session.get(Merchant, order.merchant_id)
    if merchant and merchant.random_enable:
        return await _direct_launch(request, order, merchant, session, cache)
    # 普通商户收银台页面：trade_no 由服务端注入（URL 不再携带业务号）
    return HTMLResponse(_cashier_page_for(order.trade_no))


def _cashier_page_for(trade_no: str) -> str:
    """收银台自选页注入已知 trade_no（供 /cashier/{token} 不透明链接使用）。"""
    return _CASHIER_PAGE.replace(
        "var TRADE_NO = ''; /* 由服务端注入（_cashier_page_for） */",
        f"var TRADE_NO = '{html.escape(trade_no, quote=True)}';", 1)


async def _direct_launch(request: Request, order: Order, merchant: Merchant,
                         session: SessionDep, cache: CacheDep):
    """随机金额商户收银台链接：不展示页面，直接拉起/复用支付并跳转。

    - 已支付 → 成功页（有回跳地址则自动跳转）
    - 非待支付 → 错误结果页
    - 已拉起过（extra 落库）→ 复用支付信息，避免重复请求上游
    - 未拉起过 → 现场拉起；失败订单保持待支付，结果页可刷新重试
    """
    site = await _site_url(request, session, cache)

    if order.status == ORDER_PAID:
        back = await _signed_return_url(order, merchant, session, cache)
        return HTMLResponse(_render_result(
            "ok", "支付成功", "订单已完成支付，感谢您的惠顾",
            _pay_display_money(order), order.trade_no, back,
        ))
    if order.status != ORDER_PENDING:
        return HTMLResponse(_render_result(
            "err", "订单无法支付", "订单已过期或状态异常，请重新发起付款",
            _pay_display_money(order), order.trade_no, "",
        ))

    extra = order.extra or {}
    pay_type = str(extra.get("pay_type") or "")
    pay_info = str(extra.get("pay_info") or "")
    if pay_type not in ("jump", "scheme", "qrcode", "html") or not pay_info:
        flow = await process_payment(
            order, merchant, session, cache,
            is_mobile=_is_mobile(request),
            notify_base=site, return_base=site, site_url=site,
        )
        if not flow.ok:
            return HTMLResponse(_render_result(
                "err", "拉起支付失败",
                flow.error or "上游通道暂不可用，请稍后刷新重试",
                _pay_display_money(order), order.trade_no, "",
            ))
        pay_type, pay_info = flow.pay_type, flow.pay_info or ""

    if pay_type == "html":
        return HTMLResponse(pay_info)
    if pay_type == "qrcode":
        return HTMLResponse(_render_qrcode_page(
            _pay_display_money(order), order.subject or "", order.trade_no, pay_info,
        ))
    if pay_type == "scheme":
        return HTMLResponse(_render_scheme_page(
            _pay_display_money(order), order.trade_no, pay_info,
        ))
    return RedirectResponse(pay_info, status_code=302)


# ============================================================
# 聚合收款页（随机金额商户服务端建单直跳，其余渲染输金额表单）
# ============================================================

def _go1_url(request: Request) -> str:
    """go=1 跳转地址：相对路径（避免反代场景协议/域名误判），保留其余查询参数。"""
    params = dict(request.query_params)
    params.pop("go", None)
    params["go"] = "1"
    return request.url.path + "?" + urlencode(params)


@router.get("/p/{token}", include_in_schema=False)
async def paypage_opaque_page(request: Request, token: str, go: str = "",
                              session: SessionDep = None, cache: CacheDep = None,
                              _rl=rate_limit(times=30)):
    """收款页短码链接：URL 不携带商户 pid（默认下发形态）。

    随机金额商户不渲染输金额表单（无“生成付款订单”按钮）：
    - 首访渲染倒计时页，JS（800ms）+ meta refresh（1s 兜底）自动跳转
      携带 go=1 重入本路由（两步式可挡住不执行 JS 的预取/爬虫批量建单）
    - go=1：服务端建随机金额收款单并 302 到收银台链接，由其直接
      拉起真实上游支付（jump 302 / qrcode / scheme / html）
    普通商户渲染输金额表单页（页面 JS 调 /paypage/info 与 /paypage/order）。
    """
    m, gate_err = await load_paypage_merchant(session, cache, token)
    if gate_err:
        return HTMLResponse(_render_paypage_error(gate_err))
    if m:
        if go != "1":
            return HTMLResponse(_render_paypage_go_page(_go1_url(request)))
        code, message, data = await create_paypage_order_core(
            PaypageOrderRequest(token=token), request, session, cache)
        if code != 0 or not (data and data.get("cashier_url")):
            return HTMLResponse(_render_paypage_error(message or "创建订单失败，请稍后重试"))
        return RedirectResponse(data["cashier_url"], status_code=302)
    return HTMLResponse(_PAYPAGE_PAGE)


# ============================================================
# 页面模板
# ============================================================

_BASE_CSS = """*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;background:#f5f6f7;min-height:100vh}
.wrap{max-width:480px;margin:0 auto;padding:16px}
.card{background:#fff;border-radius:12px;padding:24px;margin-bottom:16px;box-shadow:0 1px 4px rgba(0,0,0,.05)}
.tips{color:#86909c;font-size:12px;text-align:center;margin-top:20px}"""


def _tpl(src: str, *, raw: frozenset[str] = frozenset()) -> callable:
    """极简 {key} 占位模板：raw 集合外的值一律 HTML 转义（JS 串/内联 SVG 须列入 raw）。

    仅替换与 key 完全同名的 {key}，CSS 花括号不受影响。
    """
    def render(**kw) -> str:
        out = src
        for k, v in kw.items():
            s = str(v if v is not None else "")
            if k not in raw:
                s = html.escape(s, quote=True)
            out = out.replace("{" + k + "}", s)
        return out
    return render


# ---- 收银台自选支付方式页（非随机金额商户） ----
_CASHIER_PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>收银台</title>
<style>
""" + _BASE_CSS + """
.amount{text-align:center;padding:32px 24px}
.amount .label{color:#86909c;font-size:14px;margin-bottom:8px}
.amount .money{color:#1d2129;font-size:36px;font-weight:700}
.amount .subject{color:#4e5969;font-size:14px;margin-top:8px;word-break:break-all}
.badge{display:inline-block;padding:2px 10px;border-radius:10px;font-size:12px;margin-top:10px}
.badge.ok{background:#e8ffea;color:#00b42a}
.badge.wait{background:#fff7e8;color:#ff7d00}
.type-item{display:flex;align-items:center;justify-content:space-between;padding:16px;border:1px solid #e5e6eb;border-radius:10px;margin-bottom:12px;cursor:pointer;transition:all .15s;font-size:16px;color:#1d2129}
.type-item:active{background:#f2f3f5}
.type-item.active{border-color:#165dff;background:#f0f6ff}
.type-item .icon{width:36px;height:36px;border-radius:8px;display:inline-flex;align-items:center;justify-content:center;margin-right:12px;color:#fff;font-weight:700;font-size:14px}
.type-item .left{display:flex;align-items:center}
.btn{width:100%;padding:14px;border:none;border-radius:10px;background:#165dff;color:#fff;font-size:16px;font-weight:600;cursor:pointer}
.btn:disabled{background:#94bfff}
.err-title{color:#1d2129;font-size:18px;font-weight:600;text-align:center;margin-bottom:8px}
.err-msg{color:#86909c;font-size:14px;text-align:center;word-break:break-all}
</style>
</head>
<body>
<div class="wrap">
  <div class="card amount">
    <div class="label" id="amount-label">支付金额（元）</div>
    <div class="money" id="money">--</div>
    <div class="subject" id="subject"></div>
    <div id="status-badge"></div>
  </div>
  <div class="card" id="types-card">
    <div id="types"></div>
    <button class="btn" id="pay-btn" disabled>立即支付</button>
  </div>
  <div class="tips">支付完成后请勿关闭页面，将自动跳转</div>
</div>
<script>
var TRADE_NO = ''; /* 由服务端注入（_cashier_page_for） */
var API = '/cashier';
var types = [], chosen = '', timer = null;
function fmt(n){var d=parseFloat(n);return isNaN(d)?'--':d.toFixed(2)}
function showError(msg){
  document.getElementById('amount-label').textContent = '';
  document.getElementById('money').style.display = 'none';
  document.getElementById('status-badge').innerHTML = '';
  document.getElementById('types-card').style.display = 'none';
  var s = document.getElementById('subject');
  s.innerHTML = '';
  var t = document.createElement('div'); t.className = 'err-title'; t.textContent = '订单无法支付';
  var m = document.createElement('div'); m.className = 'err-msg';
  m.textContent = msg || '订单不存在或已失效';
  s.appendChild(t); s.appendChild(m);
  if(timer) clearInterval(timer);
}
function renderStatus(st, backurl){
  var badge = document.getElementById('status-badge');
  if(st === 1){
    badge.innerHTML = '<span class="badge ok">支付成功</span>';
    document.getElementById('types-card').style.display = 'none';
    if(backurl){ setTimeout(function(){ location.href = backurl; }, 1200); }
    if(timer) clearInterval(timer);
  } else {
    badge.innerHTML = '<span class="badge wait">待支付</span>';
  }
}
async function loadStatus(){
  try{
    var r = await fetch(API + '/status?trade_no=' + encodeURIComponent(TRADE_NO));
    var j = await r.json();
    if(j.code === 0 && j.data && j.data.status === 1){ renderStatus(1, j.data.return_url); }
  }catch(e){}
}
async function loadInfo(){
  try{
    var r = await fetch(API + '/types?trade_no=' + encodeURIComponent(TRADE_NO));
    var j = await r.json();
    if(j.code !== 0){ showError(j.message); return; }
    document.getElementById('money').textContent = fmt(j.data.money);
    document.getElementById('subject').textContent = j.data.subject || '';
    renderStatus(j.data.status, j.data.return_url);
    // 已拉起过 jump/scheme 支付：自动跳转上游（同一订单每会话只跳一次，
    // 用户返回收银台后可改选其它支付方式，避免来回跳死循环）
    var pt = j.data.pay_type, pi = j.data.pay_info;
    if(j.data.status === 0 && (pt === 'jump' || pt === 'scheme') && pi){
      var KEY = 'cashier_jump_' + TRADE_NO;
      var jumped = false;
      try{ jumped = !!sessionStorage.getItem(KEY); sessionStorage.setItem(KEY, '1'); }catch(e){}
      if(!jumped){ location.href = pi; return; }
    }
    types = j.data.types || [];
    var box = document.getElementById('types');
    var colors = {alipay:'#1677ff', wxpay:'#00c250', qqpay:'#12b7f5', bank:'#f5a623'};
    box.innerHTML = types.map(function(t,i){
      return '<div class="type-item" data-i="'+i+'"><span class="left"><span class="icon" style="background:'+(colors[t.code]||'#86909c')+'">'+(t.name||t.code).slice(0,2)+'</span>'+(t.name||t.code)+'</span><span>›</span></div>';
    }).join('');
    box.querySelectorAll('.type-item').forEach(function(el){
      el.addEventListener('click', function(){
        box.querySelectorAll('.type-item').forEach(function(x){x.classList.remove('active')});
        el.classList.add('active');
        chosen = types[+el.dataset.i].code;
        document.getElementById('pay-btn').disabled = false;
      });
    });
    if(types.length === 1){ box.querySelector('.type-item').click(); }
  }catch(e){ showError('网络错误，请刷新重试'); }
}
document.getElementById('pay-btn').addEventListener('click', async function(){
  if(!chosen) return;
  var btn = this; btn.disabled = true; btn.textContent = '正在发起支付...';
  try{
    var r = await fetch(API + '/pay', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({trade_no: TRADE_NO, type: chosen})});
    var j = await r.json();
    if(j.code === 0){
      var p = j.data.pay_type, info = j.data.pay_info;
      if(p === 'jump' || p === 'scheme'){ location.href = info; return; }
      if(p === 'html'){ document.open(); document.write(info); document.close(); return; }
      if(p === 'qrcode'){ alert('请使用手机扫码/付款：' + info); }
    } else {
      alert(j.message || '发起支付失败');
    }
  }catch(e){ alert('网络错误，请重试'); }
  btn.disabled = false; btn.textContent = '立即支付';
});
loadInfo(); timer = setInterval(loadStatus, 3000);
</script>
</body>
</html>"""


# ---- 结果页（成功 / 错误统一骨架） ----
_RESULT_TPL = _tpl("""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>{title}</title>
<style>
""" + _BASE_CSS + """
.result{text-align:center;padding:40px 24px}
.result .mark{width:64px;height:64px;border-radius:50%;margin:0 auto 16px;display:flex;align-items:center;justify-content:center;font-size:34px;color:#fff;font-weight:700}
.result.ok .mark{background:#00b42a}
.result.err .mark{background:#ff7d00}
.result h1{color:#1d2129;font-size:20px;font-weight:600;margin-bottom:8px}
.result .msg{color:#86909c;font-size:14px;word-break:break-all}
.result .money{color:#1d2129;font-size:30px;font-weight:700;margin-top:16px}
.result .money .rmb{font-size:16px;font-weight:400;margin-right:2px}
.result .no{color:#86909c;font-size:12px;margin-top:8px;word-break:break-all}
.btn{display:inline-block;margin-top:24px;padding:12px 36px;border:none;border-radius:10px;background:#165dff;color:#fff;font-size:15px;font-weight:600;cursor:pointer;text-decoration:none}
</style>
</head>
<body>
<div class="wrap">
  <div class="card result {kind}">
    <div class="mark">{mark}</div>
    <h1>{title}</h1>
    <div class="msg">{message}</div>
    <div class="money"><span class="rmb">￥</span>{money}</div>
    <div class="no">订单号 {trade_no}</div>
    <a class="btn" id="act-btn" href="javascript:void(0)">{btn_label}</a>
  </div>
  <div class="tips">支付完成后请勿关闭页面，将自动跳转</div>
</div>
<script>
var BACK = {back_js};
var btn = document.getElementById('act-btn');
if(BACK){ btn.href = BACK; }
else if({is_err_js}){ btn.addEventListener('click', function(){ location.reload(); }); }
else { btn.style.display = 'none'; }
setInterval(async function(){
  try{
    var r = await fetch('/cashier/status?trade_no=' + encodeURIComponent({trade_no_js}));
    var j = await r.json();
    if(j.code === 0 && j.data && j.data.status === 1 && (j.data.return_url || BACK)){
      location.href = j.data.return_url || BACK;
    }
  }catch(e){}
}, 3000);
</script>
</body>
</html>""", raw=frozenset({"back_js", "is_err_js", "trade_no_js"}))


def _render_result(kind: str, title: str, message: str, money: str,
                   trade_no: str, back: str) -> str:
    """结果页：ok=支付成功（有回跳则按钮直达）；err=错误（按钮刷新重试）。"""
    return _RESULT_TPL(
        kind=kind, mark="✓" if kind == "ok" else "!", title=title, message=message,
        money=money, trade_no=trade_no, btn_label="完成" if kind == "ok" else "刷新重试",
        back_js=json.dumps(back or ""), trade_no_js=json.dumps(trade_no),
        is_err_js="true" if kind == "err" else "false",
    )


# ---- scheme 拉起页：自动 location.href 拉起 APP，未拉起展示手动按钮 ----
_SCHEME_TPL = _tpl("""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>正在拉起支付</title>
<style>
""" + _BASE_CSS + """
.launch{text-align:center;padding:40px 24px}
.launch .spin{width:40px;height:40px;margin:0 auto 16px;border-radius:50%;border:3px solid #e5e6eb;border-top-color:#165dff;animation:spin 1s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.launch h1{color:#1d2129;font-size:18px;font-weight:600;margin-bottom:8px}
.launch .msg{color:#86909c;font-size:13px}
.launch .money{color:#1d2129;font-size:30px;font-weight:700;margin-top:16px}
.launch .money .rmb{font-size:16px;font-weight:400;margin-right:2px}
.launch .no{color:#86909c;font-size:12px;margin-top:8px}
.btn{display:none;margin:24px auto 0;padding:12px 36px;border:none;border-radius:10px;background:#165dff;color:#fff;font-size:15px;font-weight:600;cursor:pointer;text-decoration:none}
.fallback{display:none;margin-top:16px}
</style>
</head>
<body>
<div class="wrap">
  <div class="card launch">
    <div class="spin"></div>
    <h1>正在拉起支付</h1>
    <div class="msg">请在手机上打开本链接完成付款</div>
    <div class="money"><span class="rmb">￥</span>{money}</div>
    <div class="no">订单号 {trade_no}</div>
    <div class="fallback" id="fallback">
      <div class="msg">未自动拉起？请点击下方按钮</div>
      <a class="btn" id="retry" href="{scheme_url}">立即支付</a>
    </div>
  </div>
  <div class="tips">支付完成后请勿关闭页面，将自动跳转</div>
</div>
<script>
location.href = {scheme_js};
setTimeout(function(){
  document.getElementById('fallback').style.display = 'block';
  document.getElementById('retry').style.display = 'inline-block';
}, 2500);
setInterval(async function(){
  try{
    var r = await fetch('/cashier/status?trade_no=' + encodeURIComponent({trade_no_js}));
    var j = await r.json();
    if(j.code === 0 && j.data && j.data.status === 1){
      location.href = j.data.return_url || '/';
    }
  }catch(e){}
}, 3000);
</script>
</body>
</html>""", raw=frozenset({"scheme_js", "trade_no_js"}))


def _render_scheme_page(money: str, trade_no: str, scheme_url: str) -> str:
    return _SCHEME_TPL(
        money=money, trade_no=trade_no, scheme_url=scheme_url,
        scheme_js=json.dumps(scheme_url), trade_no_js=json.dumps(trade_no),
    )


# ---- 二维码支付页：服务端渲染二维码 SVG + 状态轮询 ----
_QRCODE_TPL = _tpl("""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>扫码支付</title>
<style>
""" + _BASE_CSS + """
.qr-card{text-align:center;padding:32px 24px}
.qr-card h1{color:#1d2129;font-size:18px;font-weight:600;margin-bottom:4px}
.qr-card .subject{color:#4e5969;font-size:13px;margin-bottom:12px;word-break:break-all}
.qr-card .money{color:#1d2129;font-size:30px;font-weight:700;margin-bottom:16px}
.qr-card .money .rmb{font-size:16px;font-weight:400;margin-right:2px}
.qr-box{display:inline-block;padding:12px;border:1px solid #e5e6eb;border-radius:12px;background:#fff}
.qr-box svg{width:220px;height:220px;display:block}
.qr-raw{color:#86909c;font-size:12px;margin-top:12px;word-break:break-all}
.qr-card .no{color:#86909c;font-size:12px;margin-top:8px}
.badge{display:inline-block;padding:2px 10px;border-radius:10px;font-size:12px;margin-top:12px;background:#fff7e8;color:#ff7d00}
</style>
</head>
<body>
<div class="wrap">
  <div class="card qr-card">
    <h1>扫码支付</h1>
    <div class="subject">{subject}</div>
    <div class="money"><span class="rmb">￥</span>{money}</div>
    <div class="qr-box">{qr_html}</div>
    <div class="badge">待支付</div>
    <div class="no">订单号 {trade_no}</div>
  </div>
  <div class="tips">请使用对应 APP 扫码完成付款，支付后自动跳转</div>
</div>
<script>
setInterval(async function(){
  try{
    var r = await fetch('/cashier/status?trade_no=' + encodeURIComponent({trade_no_js}));
    var j = await r.json();
    if(j.code === 0 && j.data && j.data.status === 1){
      location.href = j.data.return_url || '/';
    }
  }catch(e){}
}, 3000);
</script>
</body>
</html>""", raw=frozenset({"qr_html", "trade_no_js"}))


def _qr_svg(content: str) -> str:
    """二维码内容 → 内联 SVG（SvgPathImage 单 path，体积小）。失败返回空串由调用方降级。"""
    try:
        import io

        img = qrcode.make(content, image_factory=qrcode.image.svg.SvgPathImage, border=2)
        buf = io.BytesIO()
        img.save(buf)
        return buf.getvalue().decode("utf-8")
    except Exception:
        return ""


def _render_qrcode_page(money: str, subject: str, trade_no: str, content: str) -> str:
    svg = _qr_svg(content)
    qr_html = svg if svg else (
        '<div style="width:220px;height:220px;display:flex;align-items:center;'
        'justify-content:center;color:#86909c;font-size:13px;padding:12px;'
        'word-break:break-all;overflow:auto">' + html.escape(content) + "</div>"
    )
    return _QRCODE_TPL(
        money=money, subject=subject, trade_no=trade_no,
        qr_html=qr_html, trade_no_js=json.dumps(trade_no),
    )


# ---- 随机金额收款页自动跳转页（对齐旧版 paypage/index.php：JS 800ms + meta refresh 1s 兜底） ----
_PAYPAGE_GO_TPL = _tpl("""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>正在创建支付订单</title>
<meta http-equiv="refresh" content="1;url={go_url}">
<style>
body{display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f5f6f7;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;color:#1d2129}
.box{text-align:center}
.spinner{width:32px;height:32px;margin:0 auto 16px;border:3px solid #e5e6eb;border-top-color:#165dff;border-radius:50%;animation:spin .8s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.box p{font-size:15px}
</style>
</head>
<body><div class="box"><div class="spinner"></div><p>正在为您创建支付订单，请稍候…</p></div>
<script>
var goUrl = {go_js};
var jumped = false;
function goPay(){ if(jumped) return; jumped = true; location.replace(goUrl); }
setTimeout(goPay, 800);
</script>
</body>
</html>""", raw=frozenset({"go_js"}))


def _render_paypage_go_page(go_url: str) -> str:
    """倒计时页：meta refresh 用转义后的相对地址，JS 用 JSON 串（略早于 meta 触发）。"""
    return _PAYPAGE_GO_TPL(go_url=go_url, go_js=json.dumps(go_url))


# ---- 收款页门禁错误页（短码无效 / 商户不可收款 / 建单失败等） ----
_PAYPAGE_ERR_TPL = _tpl("""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>无法付款</title>
<style>
""" + _BASE_CSS + """
.result{text-align:center;padding:40px 24px}
.result .mark{width:64px;height:64px;border-radius:50%;margin:0 auto 16px;display:flex;align-items:center;justify-content:center;font-size:34px;color:#fff;font-weight:700;background:#ff7d00}
.result h1{color:#1d2129;font-size:20px;font-weight:600;margin-bottom:8px}
.result .msg{color:#86909c;font-size:14px;word-break:break-all}
</style>
</head>
<body>
<div class="wrap">
  <div class="card result">
    <div class="mark">!</div>
    <h1>无法付款</h1>
    <div class="msg">{message}</div>
  </div>
  <div class="tips">请联系商户重新发起付款</div>
</div>
</body>
</html>""")


def _render_paypage_error(message: str) -> str:
    return _PAYPAGE_ERR_TPL(message=message)


# 原始字符串：内含 JS 正则 \/ \d 转义，非 raw 会触发 SyntaxWarning
_PAYPAGE_PAGE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1">
<title>向商户付款</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;background:#f5f6f7;min-height:100vh}
.wrap{max-width:420px;margin:0 auto;padding:24px 16px}
.card{background:#fff;border-radius:14px;padding:24px;margin-bottom:14px;box-shadow:0 1px 4px rgba(0,0,0,.05)}
.mch{display:flex;align-items:center;gap:10px}
.mch .avatar{width:44px;height:44px;border-radius:10px;background:#165dff;color:#fff;display:flex;align-items:center;justify-content:center;font-size:18px;font-weight:700}
.mch .name{font-size:16px;font-weight:600;color:#1d2129}
.mch .sub{font-size:12px;color:#86909c;margin-top:2px}
.money-input{display:flex;align-items:baseline;gap:6px;justify-content:center;padding:20px 0 8px}
.money-input .rmb{font-size:26px;color:#1d2129;font-weight:600}
#money{border:none;outline:none;font-size:40px;font-weight:700;color:#1d2129;width:200px;text-align:center;background:transparent}
.remark{width:100%;border:1px solid #e5e6eb;border-radius:8px;padding:10px 12px;font-size:14px;outline:none;margin-top:8px}
.remark:focus{border-color:#165dff}
.keypad{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:14px}
.keypad button{padding:16px 0;font-size:20px;border:none;border-radius:10px;background:#f2f3f5;color:#1d2129;cursor:pointer}
.keypad button:active{background:#e5e6eb}
.btn{width:100%;padding:14px;border:none;border-radius:10px;background:#165dff;color:#fff;font-size:16px;font-weight:600;cursor:pointer;margin-top:14px}
.btn:disabled{background:#94bfff}
.tips{color:#86909c;font-size:12px;text-align:center;margin-top:16px}
.hidden{display:none}
#msg{color:#86909c;text-align:center;padding:40px 0;font-size:14px}
</style>
</head>
<body>
<div class="wrap">
  <div class="card">
    <div class="mch">
      <div class="avatar" id="avatar">?</div>
      <div>
        <div class="name" id="mch-name">加载中...</div>
        <div class="sub">向商户付款</div>
      </div>
    </div>
    <div id="err" class="hidden"><div id="msg"></div></div>
    <div id="form">
      <div class="money-input"><span class="rmb">￥</span><input id="money" inputmode="decimal" placeholder="0.00" readonly></div>
      <input class="remark" id="remark" maxlength="30" placeholder="备注（选填）">
      <div class="keypad" id="keypad">
        <button data-k="1">1</button><button data-k="2">2</button><button data-k="3">3</button>
        <button data-k="4">4</button><button data-k="5">5</button><button data-k="6">6</button>
        <button data-k="7">7</button><button data-k="8">8</button><button data-k="9">9</button>
        <button data-k=".">.</button><button data-k="0">0</button><button data-k="del">删除</button>
      </div>
      <button class="btn" id="pay-btn">付款</button>
    </div>
  </div>
  <div class="tips">付款完成后将自动跳转，请勿关闭页面</div>
</div>
<script>
var mt = location.pathname.match(/^\/p\/([A-Za-z0-9_-]{6,32})\/?$/);
var TOKEN = (mt && mt[1]) || '';
var submitting = false;
function showErr(msg){
  document.getElementById('form').classList.add('hidden');
  document.getElementById('err').classList.remove('hidden');
  document.getElementById('msg').textContent = msg || '页面不可用';
}
var PID = '';
async function loadInfo(){
  if(!TOKEN){ showErr('链接无效'); return; }
  try{
    var j = await (await fetch('/paypage/info?token=' + encodeURIComponent(TOKEN))).json();
    if(j.code !== 0){ showErr(j.message); return; }
    PID = String(j.data.pid);
    document.getElementById('mch-name').textContent = j.data.name;
    document.getElementById('avatar').textContent = (j.data.name || '?').slice(0,1);
  }catch(e){ showErr('网络错误，请重试'); }
}
document.getElementById('keypad').addEventListener('click', function(e){
  var k = e.target && e.target.dataset && e.target.dataset.k;
  if(!k) return;
  var el = document.getElementById('money');
  if(k === 'del'){ el.value = el.value.slice(0, -1); return; }
  if(k === '.' && el.value.indexOf('.') >= 0) return;
  var next = el.value + k;
  var parts = next.split('.');
  if(parts[1] && parts[1].length > 2) return;
  if(!isNaN(parseFloat(next))) el.value = next;
});
document.getElementById('pay-btn').addEventListener('click', async function(){
  if(submitting) return;
  var money = document.getElementById('money').value;
  if(!money || parseFloat(money) <= 0){ alert('请输入付款金额'); return; }
  submitting = true;
  var btn = this; btn.disabled = true; btn.textContent = '正在发起...';
  try{
    var r = await fetch('/paypage/order', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({token: TOKEN, money: money,
                            remark: document.getElementById('remark').value})});
    var j = await r.json();
    if(j.code === 0 && j.data && j.data.cashier_url){
      location.href = j.data.cashier_url; return;
    }
    alert((j && j.message) || '发起失败，请重试');
  }catch(e){ alert('网络错误，请重试'); }
  submitting = false; btn.disabled = false; btn.textContent = '付款';
});
loadInfo();
</script>
</body>
</html>"""
