# epay-next — 聚合支付网关（FastAPI 重写版）

基于 FastAPI + SQLModel(异步) + SAQ + React 19(shadcn/ui) 的聚合支付网关。
业务模型参考彩虹易支付（EPay），接口全部为全新 REST 设计。
由 epay-python（PHP 彩虹易支付的 Python 移植版）精简重构而来：数据模型精简为 9 类核心表，
全链路异步（asyncmy + httpx.AsyncClient），金额全链路 Decimal。

## 功能范围（v1）

- **商户开放 API（全新 REST 设计）**：`POST /orders`（下单，幂等）、
  `GET /orders/{trade_no}`（查单）、`POST /orders/{trade_no}/refund`（退款，
  支持多次部分退款）、`GET /refunds/{refund_no}`（退款查询）；
  pid + RSA2 签名认证（签名在 JSON body 参数内），统一 `{code,message,data}` 包络
- **收银台**：HTML 页（`/cashier?trade_no=`，自包含单文件）+ 同资源 JSON
  `/cashier/types|pay|status`（公开 + 限流）
- **订单**：建单幂等（`UniqueIndex(out_trade_no, merchant_id)`）、金额四线记账
  （申报/实付/商户到账/利润）、多条件查询、手动补单、重发通知、CSV 导出
- **回调幂等记账三道防线**：唯一索引 / 状态机行锁（FOR UPDATE，只允许 0→1）/ Redis 锁
- **掉单补回调**：商户通知失败按 1/2/5/10/30/60/120 分钟阶梯重试（`notify_logs` 全量留痕）；
  每 2 分钟主动查上游补记账补通知；每 5 分钟过期关单
- **上游回调网关**：`/callbacks/{plugin}/{trade_no}`（异步通知）与 `.../return`（同步回跳），
  URL 携带插件名并做一致性校验
- **上游插件框架**：异步 `BasePayment`（submit/query/notify_handler）+ info.json 自动发现；
  v1 内置 **EPay 上游通道插件**（`plugins/payment/epay/`，对接同类协议的上游网关）
- **通道管理**：凭证 Fernet 加密（密钥派生自 APP_KEY）、商户费率/成本费率、
  随机/顺序/权重三种路由、金额区间与当日限额（Redis 计数）
- **商户端**：登录（验证码）+ 管理员 SSO + 自助注册（开关/审核/邀请码绑定/
  找回密码）、订单（筛选/统计汇总/CSV 导出/重发通知/notify_status）、
  结算（冻结口径：申请冻结 → 打款扣冻结/拒绝退回；费率保底封顶/起结/预留余额/
  每日次数上限/D+1 可提口径/结算方式结构化/费率预览/自动结算开关）、
  余额充值（收银台闭环）、
  资金流水（类型筛选）、代理体系（CRUD + 专属收款链接 + 业绩归属）、聚合收款页
  （/paypage 免登录收款 + 随机金额 + 建单限流）、
  站内消息 + 邮件订阅、邀请返现、支付域名白名单、RSA 密钥对生成/会话管理
- **管理后台**：RBAC 权限树、数据看板、订单、商户（余额/随机金额/
  重置密码）、通道、插件、支付方式、结算、公告管理、回调日志、
  支付设置（运行期 KV 即时生效）

扣量（is_deducted，默认关闭，频次轮询 + 商户侧遮蔽 + 管理端回补）与通知失败
自动关停支付权限联动已实现；仍不含：分账/进件/投诉、代理独立登录面板
（归属/业绩/商户侧管理已实现）、风控/黑名单体系。

> 对外接口全部为全新设计；`.php` 风格路径仅存在于 `plugins/payment/epay/` 内部
> ——那是该插件作为客户端去调用**上游**网关的接口，与自身开放 API 无关。

## 快速开始

```shell
uv sync                                   # 安装依赖
cp .env.example .env
uv run python artisan.py key generate     # 生成密钥写入 .env
# 编辑 .env：MySQL / Redis 连接

uv run python artisan.py db init          # 建表 + 超管 + 支付方式 + 设置种子 + 权限同步
uv run python artisan.py db sync-columns  # 存量库升级：对比 ORM 自动补缺失列（幂等）
uv run alembic upgrade head               # 或使用 alembic 迁移建表（二选一）

uv run uvicorn main:app --reload          # API（默认 8000）
uv run python -m saq app.worker.settings  # Worker：通知重试/补单/过期
uv run pytest                             # 测试（134 个用例，sqlite 本地文件库）
```

Docker 全链路：`cp docker-compose-example.yml docker-compose.yml && docker compose up -d`
（默认起 2 个 app 实例，端口仅绑 `127.0.0.1:8000-8002`；`--scale app=3` 扩到 3 个。
主机 nginx 反代负载均衡见 `nginx-example.conf`：上游轮询这组端口，`nginx -t` 后 reload 生效。
生产部署与 MySQL 高并发配置见 `DEPLOY.md`。）

初始化后：admin 登录 `/admin-api/auth/login`（admin / 123456，首次后改密）。
配置流程：插件管理→同步插件 → 支付方式（内置 alipay/wxpay/qqpay/bank）→
创建通道（选 epay 插件，填上游 appurl/appid/appkey，凭证自动加密）→ 创建商户拿到 pid，
商户面板「安全设置」一键生成 RSA 密钥对后即可调用开放 API。

## 开放 API 要点

- 认证：`pid + RSA2 签名`（SHA256WithRSA；签名放 JSON body 参数内，`sign/sign_type`
  与空值不参与拼串、`"0"` 参与签名；`timestamp` 必填且 ±300s 防重放；密钥为
  RSA-2048/PKCS#8，商户面板可一键生成，公钥单行 base64/PEM 均可）
- 响应包络：`{"code": 0, "message": "ok", "data": {...}}`；HTTP 状态码语义化（400/401/404）
- 下单成功 data：`trade_no / pay_type(jump|qrcode|scheme|html) / payurl|qrcode|urlscheme / cashier_url`
- 商户通知：POST JSON（签名在 body 内，平台私钥 RSA2 签名，商户用平台公钥验签）；
  `trade_status=TRADE_SUCCESS`；**HTTP 2xx 即送达**；money 去尾零（`5` 而非 `5.00`），
  验签用收到的原始值拼串
- 退款：同步模型（上游成功即落库扣余额），可退上限=商户到账金额，`out_refund_no`
  商户侧幂等，退满订单置已退款；平台开关 `api_refund_enable`
- 下单超时≠失败：订单保持待支付，交由补单 cron 主动查上游

## 架构

```
app/
  models.py                 # 9 类核心表 + RBAC 框架表（MySQL8 规范设计，全 Decimal/Numeric(12,2)）
  core/                     # config(.env) / database(异步引擎+连接池) / security(JWT+RBAC) / cache(Redis 锁)
  services/
    order.py                # 建单幂等 / 拉起支付 / process_paid_order 幂等记账漏斗
    channel.py              # 通道路由（随机/顺序/权重 + 限额）
    balance.py              # 余额变更唯一入口（行锁+流水）
    notify.py               # 商户通知 + 阶梯重试 + notify_logs
    settle.py  settings.py  crypto.py  plugin.py  plugin_interfaces.py
  routers/
    openapi.py              # /orders 商户开放 API（签名认证）
    cashier.py              # /cashier/types|pay|status 收银台 JSON（限流）
    web.py                  # /cashier 收银台 HTML（自包含单文件）
    callbacks.py            # /callbacks/{plugin}/{trade_no} 上游回调网关
    admin/ merchant/        # 管理后台 / 商户端 REST API（v1 子集）
  worker/jobs/              # notify_merchant / query_upstream / 三个扫描 cron
plugins/payment/epay/       # EPay 上游通道插件（v1 唯一新增插件）
alembic/versions/0001_*.py  # 初始迁移
tests/                      # 签名 / 下单 / 回调幂等 / 阶梯重试 / 补单 / 收银台
```

老系统（PHP 版）仅作功能与业务逻辑参考；内部 API（admin-api/merchant-api）为全新 REST 设计，
统一响应包络 `{"code":0,"message":"ok","data":...}`。

## 对接文档（docs/ 独立模块）

商户对接文档为独立 VitePress 站点，**公开访问（无需登录）**，按商户对接流程组织（无 SDK）：

- 源码：`docs/` 子目录（`docs/.vitepress/config.mts` + Markdown 页面）
- 本地开发：`cd docs && pnpm install && pnpm dev` → http://localhost:5173/docs/
- 构建：`cd docs && pnpm build` → 产物 `static/docs`（已 gitignore）
- 托管：`app/core/frontend.py::register_docs` 将构建产物挂载到 `/docs`，
  后端启动即生效；商户后台「接入文档」菜单跳转 `/docs/`

```
docs/
  .vitepress/config.mts     # 站点配置（base=/docs/，按对接流程组织目录）
  index.md                  # 首页
  guide/protocol.md         # 协议规则
  pay/                      # 页面跳转支付 / API接口支付 / 支付结果通知
  sign/                     # RSA2 签名算法
  api/                      # 查单 / 退款 / 退款查询
  appendix/                 # 支付方式列表 / 补单机制
```

## 数据库设计规范（MySQL 8）

- 引擎/字符集：InnoDB + utf8mb4（utf8mb4_0900_ai_ci），建表由 ORM 统一声明
- **代理主键**：全部 BIGINT AUTO_INCREMENT 顺序主键（对聚簇索引友好，避免随机字符串主键导致页分裂）；
  业务键 trade_no/refund_no/settle_no 及 (out_trade_no, merchant_id) 幂等键用唯一索引承载
- 金额一律 `DECIMAL(12,2)`，应用层全链路 Decimal，禁止 float
- 时间字段：created_at/updated_at 带默认值；扫描类查询全部命中组合索引
  （通知扫描 `notify_status+next_notify_at`、过期扫描 `status+expire_at`、商户列表 `merchant_id+status+created_at`）
- 高频写表（orders/merchant_records/notify_logs）历史行只增不改，数据量大后可按 created_at 分区
- 不建物理外键（高并发互联网惯例），一致性由服务层 + 索引保证

## 高并发设计

- **全异步链路**：asyncmy 异步驱动 + AsyncSession + 共享 httpx.AsyncClient，单事件循环高吞吐；
  `WEB_CONCURRENCY` 控制 uvicorn 多进程，`DB_POOL_SIZE/DB_MAX_OVERFLOW` 控制每进程连接池
- **下单**：唯一索引幂等（并发重复下单返回同一订单），建单事务内零外部 IO，插件 HTTP 在事务外
- **回调记账**：三道防线——Redis 锁（挡并发重复回调）→ `SELECT ... FOR UPDATE` 行锁（状态机 0→1）→
  唯一索引/流水约束；上游重发任意次都不会重复入账
- **余额热点**：商户余额走行锁串行记账（强一致优先），流水表异步可查；后续量级上来可引入合并记账/异步入账
- **通知与补单**：SAQ 队列削峰，扫描 cron 以 Redis 锁防多 worker 重复扫描，单轮 LIMIT 分批

## 后台前端（fastapi-starter 模板重写）

- 管理后台：`/admin`（登录验证码、数据看板、订单[补单/重发通知/退款/导出]、商户[密钥重置/余额调整/流水]、
  通道[按插件 inputs 动态凭证表单，Fernet 加密]、插件、支付方式、结算、回调日志、支付设置、RBAC 账号）
- 商户中心：`/`（概览、订单+重发通知、结算申请、资金流水、密钥/RSA/结算资料、接入文档）
- 宣传首页：`/landing`（存在 landing.html 时；`/` 直接进商户中心登录页）
- 本地体验：`uv run python scripts/demo.py 10.00` 生成订单 → 收银台支付 → 模拟上游完成闭环
  （`scripts/mock_upstream.py`；本地环境万能验证码 `0000` 仅供自动化测试，`APP_ENV != local` 时自动关闭）
- 端到端：`uv run python scripts/e2e_smoke.py`（Playwright，23 项：双后台登录/页面/权限、浏览器完整支付闭环）

## 已知边界（后续迭代）

- 5 个直连插件（wxpayn/alipayd 等）保留旧同步实现，未适配异步接口
  （`services/plugin.maybe_await` 兼容调用，v1 验收不依赖）
- 微信 V3 回调验签 TODO 随直连插件一并后置
- 商户找回密码依赖邮件通道：支付设置 `mail_channel`/`mail_config` 未配置时不可自助重置
  （管理员端有「重置登录密码」兜底）；订阅邮件同理
- 代理独立登录面板后置：归属（paypage 路径式 `/{pid}/{agent_id}`）、业绩统计、商户侧管理已实现
