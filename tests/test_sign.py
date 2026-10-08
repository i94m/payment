"""签名工具测试：升序拼串 + 空值排除 + RSA2 签名/验签/缓存。"""
from app.lib.sign import (
    get_sign_content,
)


def test_sign_content_excludes_sign_and_empty():
    data = {
        "money": "100.00",
        "name": "测试商品",
        "out_trade_no": "T123",
        "pid": "1001",
        "type": "alipay",
        "param": "",        # 空 → 排除
        "notify_url": "https://m.example/notify",
        "sign": "should-exclude",
        "sign_type": "RSA2",
    }
    content = get_sign_content(data)
    assert content == (
        "money=100.00&name=测试商品&notify_url=https://m.example/notify"
        "&out_trade_no=T123&pid=1001&type=alipay"
    )


def test_sign_content_keeps_zero_string():
    """"0"/0 参与签名（param=0 场景），仅 None/空串排除。"""
    data = {"pid": "0", "money": "5", "type": "alipay", "name": "", "count": 0}
    content = get_sign_content(data)
    assert "pid=0" in content
    assert "count=0" in content
    assert "name=" not in content


def test_sign_content_epay_variant_keeps_zero_string():
    from plugins.epay.plugin import _sign_content_epay
    data = {"pid": "0", "money": "5", "type": "alipay", "name": ""}
    # 规则：只排除空字符串，"0" 参与签名
    assert "pid=0" in _sign_content_epay(data)
    assert "name=" not in _sign_content_epay(data)


def test_rsa_key_parse_cached():
    """RSA 私钥解析结果进程内缓存（回归：逐次 PEM 解析 ~40ms 阻塞事件循环，
    压测把下单压到 ~25 rps/worker）—— 同一密钥二次签名不重复解析。"""
    import time

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    import app.lib.sign as sign_mod

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    raw = sign_mod.pem_to_flat(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode())
    data = {"pid": "1001", "money": "10.00", "type": "alipay"}

    sign_mod.make_rsa_sign(data, raw)          # 首次：解析 + 签名
    assert raw in sign_mod._private_key_cache  # 已缓存解析结果

    t0 = time.perf_counter()
    for _ in range(20):
        sign_mod.make_rsa_sign(data, raw)      # 全部命中缓存
    per_call = (time.perf_counter() - t0) / 20
    assert per_call < 0.005, f"缓存后单次签名应 <5ms（实测 {per_call*1000:.1f}ms）"

    # 公钥验签同样缓存
    pub = sign_mod.derive_public_key(raw)
    sig = sign_mod.make_rsa_sign(data, raw)
    assert sign_mod.verify_rsa_sign(data, pub, sig) is True
    assert pub in sign_mod._public_key_cache
