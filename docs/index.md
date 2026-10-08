---
layout: home

hero:
  name: "对接文档"
  text: "聚合支付网关 · 商户接入"
  tagline: 页面跳转支付 / API接口支付 / 异步通知 / 订单查询 / 退款
  actions:
    - theme: brand
      text: 开始接入
      link: /guide/protocol
    - theme: alt
      text: API接口支付
      link: /pay/api

features:
  - title: 页面跳转支付
    details: 通过收银台链接引导用户完成支付，用户可在收银台内更换支付方式。
    link: /pay/cashier
  - title: API接口支付
    details: 服务端发起下单（RSA2 签名在 JSON body 内），直接返回支付跳转链接、二维码或小程序拉起地址。
    link: /pay/api
  - title: 异步通知
    details: 支付成功后 POST JSON Webhook（平台私钥签名，HTTP 2xx 即送达），失败阶梯重试，全量留痕。
    link: /pay/notify
  - title: 订单查询与退款
    details: 查询订单支付/退款状态；支持多次部分退款与退款单查询。
    link: /api/order-query
---
