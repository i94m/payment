# 插件开发指南（AI 协作规范）

本目录是本项目的**支付插件库**（短信/邮件适配器在 `app/addons/{sms,mail}/`，不属插件体系）。**任何 AI 编码助手在本目录下新增/修改插件时，必须遵守本指南**；与上游支付平台的对接协议、历史踩坑也记录在各个插件文件的 docstring 里，动手前先读同类参考实现。

## 1. 目录结构与发现机制

```
plugins/
├── <plugin_name>/           支付插件
│   └── plugin.py            必需，插件实现 + 元信息（类属性 meta，不再使用 info.json）
├── _example/                ★ 示例模板（伪代码），新插件从这里复制开始
└── README.md                本文件
```

注册由 `app/services/plugin.py` 的 `PluginRegistry` 完成，规则：

- 只扫描 `plugins/` 下一级目录，目录内必须有 `plugin.py`
- **`_` 前缀目录一律跳过**（不注册、不在后台插件列表展示），保留给模板/示例/临时实验；
  新插件从 `plugins/_example/` 复制后**必须去掉下划线前缀**
- 通过 `importlib` 导入 `plugins.<name>.plugin`，模块内**任何**继承
  `BasePayment / BaseSms / BaseMail` 的类都会被注册（取第一个命中的）
- 注册键 = 目录名；**`meta.name` 必须与目录名一致**（上传安装会强制校验）
- 元信息（name/showname/types/inputs/version）统一定义在插件类的 `meta`（PluginMeta）上
- 加载失败只记 warning 不中断启动 —— 新插件写完后要主动验证注册成功（见 §8）
- 新增插件后需 `PluginRegistry.reload()` 或重启进程才生效（`_loaded` 缓存）；
  后台「同步插件」/上传安装会写跨进程刷新戳，其他 worker/副本随后自动跟上

### 插件分两档：内置 vs 三方

| 分类 | 位置 | 后台标记 | 新增/更新方式 |
|---|---|---|---|
| **内置** | 仓库 `plugins/`（随镜像编译成 .so） | 内置 | 重建镜像 |
| **三方** | 宿主机 `storage/plugins/`（容器内持久卷） | 三方 | 后台 zip 上传，或把编译产物目录拷进去（**无需重建镜像、无需重启**） |

- 同名时**三方覆盖内置**：已上线的内置插件要热修/换版本，不必重新打镜像
- 三方目录与后台上传是**同一个目录**（`storage/plugins`），同样只认编译后的 `plugin*.so`，不接受 `.py`
- 全局刷新是**跨进程**的：点后台「刷新插件」或上传安装会写刷新戳，
  后台插件列表刷新会**强制比对**（`discover(force=True)`，不看节流间隔），所以换个 worker 刷新页面
  看到的一定是最新；业务侧（下单取插件）按 `PluginRegistry.CHECK_INTERVAL`（默认 5 秒）收敛，不需要重启容器
- 手工拷目录/删目录同样会被发现（扫描时比对三方目录 mtime），后台列表里尚未落库的插件标记 `pending`，
  点一次刷新即写入 `plugins` 表

## 2. 接口契约

接口定义在 `app/services/plugin_interfaces.py`，写插件前先通读。支付插件继承 `BasePayment`：

| 方法 | 必须实现 | 说明 |
|---|---|---|
| `submit(ctx) -> PaymentResult` | 是（abstract） | 发起支付 |
| `query(ctx) -> QueryResult` | 否 | 主动查单（掉单补单），不支持返回 `unknown` |
| `notify_handler(ctx_params, body) -> NotifyVerdict` | 否 | 上游异步回调：验签 + 判断成功 |
| `return_handler(ctx_params) -> dict \| None` | 否 | 同步回跳，仅上游 return 带成功态时才需要 |
| `refund(order, refund_no, amount) -> dict` | 否 | 退款，`{"status": 1/0, "msg": ...}`（商户端退款入口已下线，插件层接口保留） |
| `is_refund_supported()` | 否 | 类方法，支持退款才覆写为 `True` |
| `meta: PluginMeta` | 是 | 元信息唯一来源（name/showname/types/inputs/version）+ admin 动态渲染凭证表单 |

数据类（字段含义见源码注释，勿随意加字段）：

- `PaymentContext`：`order`（订单快照 dict，**禁止修改**）/ `notify_url` / `return_url` / `site_url` / `is_mobile` / `client_ip`
- `PaymentResult`：`kind` 只能是 `jump / qrcode / html / scheme / error`
- `QueryResult`：`status` 只能是 `paid / unpaid / failed / unknown`
- `NotifyVerdict`：`ok`（验签通过且交易成功）/ `api_trade_no` / `money` / `ack`（回给上游的字面量）

## 3. 架构边界（红线）

**插件只做协议**（签名、HTTP、回调验签），不做业务：

1. **不碰数据库 / 不 import SQLAlchemy 模型 / 不做订单状态变更**。幂等记账、状态机、金额比对全部由 `app/services/order.py::process_paid_order` 统一执行
2. **不做金额业务比对**。插件只负责从回调里把 `money` 解析成 `Decimal` 放进 `NotifyVerdict`，中心层比对 `order.realmoney`，不符记 mismatch 拒绝记账
3. **金额解析失败要显式失败**：`Decimal` 解析抛异常时返回 `NotifyVerdict(ok=False, ack="fail")`，绝不能返回 `ok=True, money=None` 放行
4. **订单号以回调参数与 URL 中的 trade_no 为准**，不要自行从 body 里"猜"业务字段语义
5. **金额单位是元**（`Decimal`），对接分单位的上游时自行换算后再放进 verdict

## 4. 异步与 HTTP 约定

- 所有 IO 方法必须是 `async def`；HTTP 一律用共享客户端 `app/services/http_client.py::get_http_client()`，**禁止自建 `httpx.AsyncClient` / requests / aiohttp**
- 表单提交：`client.post(url, data=params)`；JSON：`client.post(url, json=params)`；超时由全局配置管理，插件不要覆盖
- 异常处理：上游连接失败捕获 `Exception` 后返回 `PaymentResult(kind="error", msg=...)` 或 `QueryResult(status="unknown")`，不要让异常炸到路由层（回调异常路由层会兜底回 fail，但日志会失真）
- 日志用模块级 `_log = logging.getLogger(__name__)`，**不要像老 PHP 插件那样写文件到 /tmp**；日志里禁止出现密钥（appkey/私钥）全文

## 5. 签名实现注意事项（对接上游文档时的易错点）

1. **MD5 一律小写**：Python `hexdigest()` 本身就是小写；比对时两侧统一（PHP 老代码用 `strcasecmp` 的，Python 侧 `.lower() == .lower()`）
2. **排序语义**：PHP `ksort($arr, SORT_STRING)` ≙ Python `sorted(keys)`（ASCII 字符串升序）
3. **空值排除的边界**：排除 `''` 和 `None`，但 **`"0"` 必须参与签名**（`'' == '0'` 在 PHP 是宽松比较的坑；参考 `plugins/epay/plugin.py` 的 `_sign_content_epay` 注释）
4. **拼接方式逐字核对上游文档**：`k=v&k=v` 后**直接拼 key** 还是拼 **`&key=密钥`**，逐字核对（epay 插件是前者），差一个 `&` 签名就错
5. **有文档签名示例向量的，先离线验证再写业务逻辑**（见 §8），验证不过说明拼接规则理解有误
6. URL 编码：签名用**原始值**编码前的串；发起跳转时才用 `urlencode()`

## 6. 回调（notify）流程与 ack 语义

回调统一入口是 `app/routers/callbacks.py`：

- 路由：`ANY /callbacks/{plugin_name}/{trade_no}`，插件名与订单通道不一致直接 400（防伪造探测），所以 `notify_url` 必须原样使用 `ctx.notify_url`，**不要自行拼改路径**
- `ctx_params` 已由路由层聚合：GET query + POST form + POST JSON body，插件无需重复解析原始 body（除非上游签名要求原始字节串）
- 插件职责：验签 → 判断成功状态 → 解析金额 → 返回 `NotifyVerdict`
- **ack 语义**（易错）：
  - 验签失败 → `ack="fail"`（让上游重发）
  - 验签通过但状态非成功（如"处理中"）→ `ok=False, ack="success"`（让上游停止重发）
  - 验签通过且成功 → `ok=True, ack="success"`
- 回调里的成功状态码要转成字符串比较：`str(ctx_params.get("orderStatus")) == "2"`，防止上游传 `"02"` / int 差异
- 同步回跳 `return_handler` 只在上游 return 请求**自带签名和成功态**时才实现；多数上游 return 无签名，保持默认返回 `None` 即可

## 7. meta 元信息与凭证表单

- `meta = PluginMeta(...)`：`name` 与目录名一致；`types` 只能取系统支付方式 code（目前内置 `alipay / wxpay`），对应 `ctx.order["payment_type_code"]`
- **`version` 必填，新插件一律 `1.0.0`**；此后协议/签名行为变更递增主版本（2.0.0），新增功能或凭证字段递增次版本（1.1.0），纯修复递增修订号（1.0.1）
- `inputs` 是 admin 渠道表单 schema（运维在后台配置支付账号/密钥等凭证时按它渲染），必须是 **list[dict]**，字段：`{"name", "title", "type"(input/textarea/select), "placeholder", "required", "options"(select 用 dict)}`
  - `input`：单行短凭证（网关地址、商户号、MD5 密钥）
  - `textarea`：需要整段粘贴的长凭证（平台公钥、商户私钥等 PEM/base64 密钥）——多行 PEM 可含 `BEGIN/END` 头尾，签名前由 `app/lib/sign.py` 的 `normalize_key()` 统一清洗空白/换行
  - `select`：枚举值，用 `options` 提供候选项
- **placeholder 是硬性要求**：
  - 每个支付账号/密钥类字段（商户号、AppID、密钥、网关地址、编码/编号等一切 `type: "input"` 或 `"textarea"` 的字段）**必须带 `placeholder`**，缺一个都算不合规
  - placeholder 要写成**可直接照抄的真实格式示例**，说明取值来源与格式约束，而不是"请输入xxx"这类空话：
    - 网关地址 → 完整默认地址，如 `http://137.220.128.173/gateway/pay/api`
    - 商户号/AppID → 上游账号的样例形态，如 `1127`、`2021001234567890`
    - 密钥 → 形态说明，如 `商户后台分配的32位密钥，用于MD5签名`
    - 公钥/私钥（textarea）→ `粘贴 PEM 公钥（多行可含 BEGIN/END 头尾）`
    - 特殊编码（如支付方式编号）→ 格式 + 含义，如 `数字编码如 123，不同支付方式不同，需向平台确认`
  - `type: "select"` 用 `options` 提供候选项，不需要 placeholder
- 凭证读取统一走 `self.channel`（已解密 dict），空值兜底用 `str(self.channel.get("k", "") or "").strip()`，且**必须与 inputs 里的 `name` 逐一对应**

## 8. 验收清单（写完插件必须逐项过）

```bash
cd <仓库根目录>
```

1. **可导入**：`python -c "import plugins.<name>.plugin"`
2. **注册成功**：`PluginRegistry.reload()` 后 `get_payment_class("<name>")` 非 None（写临时脚本或用 `python -c` 验证）
3. **签名有文档向量的必须离线比对**，用 `python -c` 算一遍
4. **meta 表单字段与实际读取的 channel 键一一对应**（input name ↔ `self.channel.get(...)`）
5. **每个 `type: "input"` / `"textarea"` 的 inputs 字段都有 placeholder**，且是可照抄的真实格式示例（见 §7）
6. **`meta.version` 必填**：新插件一律 `1.0.0`，迭代按语义化递增（见 §7）
7. **notify 分支齐全**：无 sign / 验签失败 / 非成功状态 / 成功 / 金额解析失败，五条路径都有明确返回
8. 不确定上游查单响应结构时，`query` 按防御式解析（`code`/`data` 判空），失败一律 `unknown`
9. 新插件接入真实通道前，用上游测试商户号跑一笔小额下单 + 回调闭环
10. 验收通过后，执行 `scripts/build_plugin.sh <插件名>` 生成客户上传包（见 §11，产物在 `dist/`，不入库）

## 9. 常见坑（历史踩过的）

- 把 `"0"` 当空值排除了 → 签名错，上游回验签失败
- 签名串末尾 `&key=` 拼接方式与上游文档不符
- 成功状态码用 int 比较，上游传来 `"2"`（字符串）或 `"02"`
- 回调里自行比对金额并拒绝 → 与中心层逻辑重复且口径不一致；插件只报数
- 在插件里 `import app.models` 查订单 → 越界；订单一律通过 `ctx.order` 快照拿
- 同步/返回代码里用同步 requests/httpx → 阻塞事件循环
- `meta.name` 与目录名不一致 → 注册键错位，admin 建通道后回调 404
- inputs 缺 placeholder 或写空话 → 运维建通道时不知道账号/密钥该填什么格式，填错后排查成本高
- inputs 写成 dict → 渲染异常，**必须 list[dict]**
- 老 PHP 插件的 `/tmp/*.log` 文件日志、`global $siteurl` 全局变量、`daddslashes` 转义在新架构里都没有对应物，从 PHP 插件改写时不要照搬

## 10. 参考实现

- `plugins/_example/` — **伪代码骨架模板，新插件的起点**；`_` 前缀目录不会被注册、不在后台展示，复制使用时去掉前缀并把 `example.py` 重命名为 `plugin.py`
- `plugins/epay/plugin.py` — **易支付 V1**（MD5 签名 + `submit.php`/`mapi.php`/`api.php` + 回调 + 查单 + 退款全量参考）
- `plugins/epayv2/plugin.py` — **易支付 V2**（RSA2 签名 + `api/pay/submit|create|query|refund` + 回调验签，含
  `timestamp` 300s 窗口与 `pay_type/pay_info` 应答映射；两版协议不同，不要互相套用）

短信/邮件适配器已迁至 `app/addons/{sms,mail}/`（复用 storage addon 的组织方式），由 `PluginRegistry` 按包名扫描，不适用本指南的插件验收流程。

## 11. 打包与分发（对接完成后，生成给客户的上传包）

插件开发 + §8 验收全部通过后，一条命令生成客户可直接在后台上传的安装包：

```bash
cd <仓库根目录>
scripts/build_plugin.sh <插件名>        # 需要 docker（与生产镜像同环境编译 .so）
```

产物（`dist/` 已在 `.gitignore` 中，**不会入库**，仅用于私下分发）：

```
dist/plugins/<插件名>/             解压后的插件目录（见下方「目录映射」）
dist/plugin_packages/<插件名>.zip  后台上传包（仅 .so，无源码 / 无 info.json）
```

**两种投放方式（都不需要重建镜像）：**

- **目录映射（推荐自用/托管部署）**：把 `dist/plugins/<插件名>/` 整个目录拷到服务器的 `storage/plugins/<插件名>/`，点一次后台「刷新插件」即全进程生效（无需重启/重建镜像；同名覆盖内置插件）
- **后台上传（推荐发给客户）**：把 `dist/plugin_packages/<插件名>.zip` 发给客户，客户在后台「上传插件」安装

zip 内结构（后台上传接口要求的目录层级）：

```
<插件名>/plugin.cpython-312-x86_64-linux-gnu.so
```

> 上传接口只接受编译后的 `.so`，包内含 `.py` 会被拒绝；`info.json` 已废除，残留文件会被忽略、不落盘。
> 版本号来自插件类 `meta.version`，发版前记得递增；客户上传后可点后台「刷新插件」
> 确认插件出现在通道表单的下拉里（来源标记为「三方」）。

**客户侧安装步骤：**

1. 管理后台 →「支付通道」→ 右上角「上传插件」，选择 `dist/plugin_packages/<插件名>.zip`
2. 新建通道：选支付方式 + 该插件 → 按 `inputs` 表单填凭证 → 保存（新通道默认关闭）
3. 通道列表点「测试」跑一笔测试单，确认下单 + 回调闭环
4. 打开通道开关启用
5. 不需要重启容器：上传接口写跨进程刷新戳，后台插件列表刷新即为最新；其他 worker 后续请求自动跟上

**服务器侧目录映射（替代后台上传）：** 把 `dist/plugins/<插件名>/` 整个目录拷到服务器的 `storage/plugins/<插件名>/`（`storage/` 已由 compose 挂载进容器，无需改编排），然后在后台点一次「刷新插件」即可（**无需重启容器**；不点也会在插件列表里以「未同步」状态出现）。同名插件会自动覆盖镜像内置版本，插件来源标记为「三方」。
