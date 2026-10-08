"""支付渠道配置构建器"""
import json
import os


def get_alipay_config(channel: dict, session=None) -> dict:
    config = {
        "app_id": channel.get("appid", ""),
        "alipay_public_key": channel.get("alipay_public_key", ""),
        "app_private_key": channel.get("app_private_key", ""),
        "sign_type": "RSA2",
        "charset": "utf-8",
    }
    if channel.get("cert_mode") == "1":
        config["app_cert_public_key"] = _cert_path(channel.get("app_cert_public_key", ""))
        config["alipay_root_cert"] = _cert_path(channel.get("alipay_root_cert", ""))
    plugin = channel.get("plugin", "")
    if plugin in ("alipaysl", "alipaycode"):
        config["app_auth_token"] = channel.get("app_auth_token", "")
    if plugin in ("alipayd", "alipayrp"):
        config["smid"] = channel.get("smid", "")
    return config


def get_wxpay_config(channel: dict) -> dict:
    config = {
        "appid": channel.get("appid", ""),
        "mchid": channel.get("mchid", ""),
        "apikey": channel.get("apikey", ""),
        "ssl_cert": _cert_path(channel.get("client_cert", "")),
        "ssl_key": _cert_path(channel.get("client_key", "")),
    }
    if channel.get("plugin") == "wxpaysl":
        config["sub_mchid"] = channel.get("sub_mchid", "")
        config["sub_appid"] = channel.get("sub_appid", "")
    return config


def get_wxpay_v3_config(channel: dict) -> dict:
    config = {
        "appid": channel.get("appid", ""),
        "mchid": channel.get("mchid", ""),
        "apikey": channel.get("apikey_v3", channel.get("apikey", "")),
        "private_key_path": _cert_path(channel.get("private_key_path", "")),
        "cert_serial": channel.get("cert_serial", ""),
        "platform_public_key_path": _cert_path(channel.get("platform_public_key", "")),
    }
    plugin = channel.get("plugin", "")
    if plugin == "wxpaynp":
        config["sub_mchid"] = channel.get("sub_mchid", "")
        config["ecommerce"] = True
    if plugin == "wxpayng":
        config["isGlobal"] = True
    return config


def get_qqpay_config(channel: dict) -> dict:
    return {
        "mchid": channel.get("mchid", ""),
        "apikey": channel.get("apikey", ""),
        "op_userid": channel.get("op_userid", ""),
        "op_userpwd": channel.get("op_userpwd", ""),
        "ssl_cert": _cert_path(channel.get("client_cert", "")),
        "ssl_key": _cert_path(channel.get("client_key", "")),
    }


def get_douyinpay_config(channel: dict) -> dict:
    return {
        "appid": channel.get("appid", ""),
        "mchid": channel.get("mchid", ""),
        "apikey": channel.get("apikey", ""),
        "private_key_path": _cert_path(channel.get("private_key", "")),
        "cert_serial": channel.get("cert_serial", ""),
        "platform_cert_path": _cert_path(channel.get("platform_cert", "")),
    }


def _cert_path(filename: str) -> str:
    if not filename:
        return ""
    if os.path.isabs(filename):
        return filename
    return os.path.join("file", "cert", filename)
