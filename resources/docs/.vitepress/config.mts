import { defineConfig } from "vitepress"

// 对接文档站点 — 按商户对接流程组织（无 SDK 章节）。
// 部署形态：由 FastAPI 于 /docs 路径直接托管 static/docs 静态产物，
// 因此 base 固定为 /docs/；本地开发 `pnpm dev` 后访问 http://localhost:5173/docs/。
// 注意：静态托管（StaticFiles）无 cleanUrls 重写，链接需带 .html 后缀。
export default defineConfig({
  lang: "zh-CN",
  title: "对接文档",
  description: "聚合支付网关商户对接文档",
  base: "/docs/",
  // 产物统一输出到仓库根 static/docs（outDir 相对 resources/docs/ 解析，即项目根上两级）；
  // outDir 在项目根之外时 VitePress 默认不清空，必须显式开启
  outDir: "../../static/docs",
  emptyOutDir: true,
  ignoreDeadLinks: true,

  head: [
    ["meta", { name: "robots", content: "all" }],
    ["link", { rel: "icon", type: "image/svg+xml", href: "/docs/logo.svg" }],
  ],

  themeConfig: {
    nav: [
      { text: "首页", link: "/" },
      { text: "API列表", link: "/pay/api.html" },
      { text: "签名算法", link: "/sign/index.html" },
    ],

    sidebar: [
      {
        text: "开始接入",
        items: [
          { text: "协议规则", link: "/guide/protocol.html" },
          { text: "签名算法", link: "/sign/index.html" },
        ],
      },
      {
        text: "API列表",
        items: [
          { text: "支付下单", link: "/pay/api.html" },
          { text: "回调通知", link: "/pay/notify.html" },
          { text: "查询订单", link: "/api/order-query.html" },
          { text: "申请退款", link: "/api/refund.html" },
          { text: "查询退款", link: "/api/refund.html#查询退款" },
        ],
      },
      {
        text: "附录",
        items: [
          { text: "支付方式列表", link: "/appendix/paytype.html" },
          { text: "补单机制", link: "/appendix/replenish.html" },
        ],
      },
    ],

    // 不展示右侧"本页目录"
    outline: false,

    docFooter: {
      prev: "上一页",
      next: "下一页",
    },

    returnToTopLabel: "回到顶部",
    sidebarMenuLabel: "菜单",
    darkModeSwitchLabel: "外观",
    lightModeSwitchTitle: "切换到浅色模式",
    darkModeSwitchTitle: "切换到深色模式",

    footer: {
      message: "对接文档如有疑问请联系平台运营",
      copyright: "聚合支付网关",
    },
  },
})
