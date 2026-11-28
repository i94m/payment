---
layout: home

hero:
  name: "对接文档"
  text: "聚合支付网关 · 商户接入"
  tagline: 签名算法 / API 下单 / 异步通知
  actions:
    - theme: brand
      text: 开始接入
      link: /guide/protocol.html
    - theme: alt
      text: API 下单
      link: /pay/api.html

features:
  - title: 协议规则
    details: 请求地址、统一响应结构、金额与时间格式、幂等与验签顺序。
    link: /guide/protocol.html
  - title: 签名算法
    details: MD5 拼串签名规则，含各语言签名示例与常见踩坑。
    link: /sign/index.html
  - title: API 下单
    details: 服务端发起下单，直接返回支付跳转链接或二维码地址。
    link: /pay/api.html
  - title: 回调通知
    details: 支付成功后平台异步 POST 通知，失败按阶梯间隔自动重试。
    link: /pay/notify.html
---
