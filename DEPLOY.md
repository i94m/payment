# 部署指南

生产部署：仅 app/worker 容器化 + 宿主机 nginx 反代；**MySQL 与 Redis 均用宿主机系统服务**（非容器）。

| 组件 | 运行方式 | 参考 |
|---|---|---|
| app × N | 容器（`--scale app=N`，端口 `127.0.0.1:8000-8002`） | `docker-compose.production.yml` |
| worker | 容器（SAQ：通知重试/补单/过期关单，可 `--scale worker=N`） | 同上 |
| MySQL 8.0（兼容 5.7） | **宿主机系统服务（非容器）**；容器化仅为备选 | 本文·一 |
| Redis | **宿主机系统服务（非容器）**（队列/限流/锁/扣量计数） | 本文·一之 3 |
| nginx | **宿主机**（非容器），轮询上游 8000-8002 | `nginx-conf.conf` |

## 零、打包与部署（各一条命令）

打包机（需 docker + pnpm，产出推仓库）：

```shell
scripts/build-image.sh --registry registry.example.com/myns --push
# 默认标签 日期-git短sha，另打 latest；--tag v1.2.3 可指定版本
# 前端与对接文档产物（static/console、static/docs）不入库，脚本会先现场构建再进镜像，产物缺失构建直接失败
```

服务器（只需 docker + compose + 宿主机 MySQL/Redis，不装 node/pnpm/python）：

```shell
git pull && PAY_IMAGE=registry.example.com/myns/pay-gateway:<tag> ./scripts/deploy.sh
```

一条命令完成：`.env` 缺失时从 `.env.example` 复制并列出必填项后退出（密钥
**不自动生成**，`APP_SECRET_KEY` 缺配直接报错；`STORAGE_LOCAL_SECRET` 可选，
留空默认取 `APP_SECRET_KEY`）→
拉镜像 → `up -d`（app×N + worker，N 取 `.env` 的 `APP_REPLICAS`，默认 3）→ 等全部 healthy（期望容器数按「app 实例数 + worker」计算，`--scale 1` 也成立）→ 空库自动建表初始化 /
已有库自动 alembic 增量迁移。可重复执行（升级=换镜像标签再跑一次）；
`--scale N` 调 app 实例数，`--no-pull` 跳过拉取。

`PAY_IMAGE` 环境变量决定 compose 用的镜像名（默认 `ghcr.io/i94m/payment:latest`，
本机 `scripts/build-image.sh` 产出 `pay-gateway:<tag>`，用 `PAY_IMAGE=pay-gateway:<tag>`
指定）。服务器上请固定带版本标签拉取，避免 latest 漂移。

GitHub Actions **只在发版（打版本标签）时构建**，普通提交（含 push 到 main）不产出镜像：

```shell
git tag -a v1.2.3 -m "1.2.3"     # 在要发布的提交上打标签
git push origin v1.2.3           # 只推这一个标签，触发构建
# 产物标签：1.2.3 / 1.2 / 1 / latest / sha-xxxxxxx（ghcr.io/i94m/payment）
```

也可以在 GitHub 网页创建：仓库 → **Releases → Draft a new release** → 「Choose a tag」里
输入 `v1.2.3` → 选择目标分支/提交 → Publish；或用
`gh release create v1.2.3 --target main --generate-notes`。两种方式最终都会推一个
`v1.2.3` 标签，触发同一个 workflow（**`v` 前缀不能省**，工作流只匹配 `v*` 标签）。

规则：

- 标签必须是 `v<major>.<minor>.<patch>` 形式，否则 CI 直接失败（防止只产出 sha 标签的镜像）。
- **版本号必须递增**：仓库里已有 `v1.0.2` 时再推 `v1.0.1`，workflow 会在第一步就报错退出，
  不会构建。原因：`1.0`、`1`、`latest` 是浮动标签，回退版本会把线上 `latest` 拉回旧镜像。
  确需补发旧版本，用 **Actions → docker-image → Run workflow** 手动填版本号（此时跳过顺序校验，
  且默认不动 `latest`；要让 `latest` 指过去需勾 `update_latest`）。
- 同一版本标签重推会被拒（GitHub 上标签不可覆盖，需先 `git tag -d` / 远端删除）；只是重跑
  构建则用手动 dispatch，版本号相同是允许的。
- 别用 `git push --tags` 批量推：历史标签会逐个触发构建，其中低于最高版本的会被上述校验拦下报错。

老版本标签长期保留，客户可直接固定在 `PAY_IMAGE=ghcr.io/i94m/payment:1.2.3`。

**版本写进了镜像本身**（不只是标签）：构建时把版本号 / 提交 / 构建时间作为 build-arg
写进 ENV 和 OCI 标签，随时可查线上跑的到底是哪版：

```shell
docker inspect --format '{{index .Config.Labels "org.opencontainers.image.version"}}' ghcr.io/i94m/payment:1.2.3
curl -s localhost:8000/health     # {"message":"ok","version":"1.2.3","revision":"<sha>","build_date":"..."}
```

后台接口 `/admin-api/systemInfo`（需 `system.admin` 权限）同样返回 `version` / `revision` /
`build_date`；源码直接运行时为 `dev`。管理后台侧边栏的**站点名称下方**直接显示版本号
（`v1.2.3`，hover 可见构建提交号；商户端/代理端不展示）。

### 生成客户部署包（一条命令交付）

```shell
uv run python scripts/make_release.py
# 交互询问「文件夹名称」和「授权有效日期」，自动签发授权码并打包
```

也可以非交互（CI/批量交付）：

```shell
uv run python scripts/make_release.py --name client-a --until 2027-12-31 \
    --image ghcr.io/i94m/payment:1.2.3
```

产物（`dist/` 已 gitignore，不会入库）：

```
dist/releases/<文件夹名>/            解压后的部署目录
dist/releases/<文件夹名>.zip         发给客户的交付包
```

包内结构：`.env`（已生成 `APP_SECRET_KEY` / `STORAGE_LOCAL_SECRET` / `AUTH_CODE` /
`PAY_IMAGE`）、`docker-compose.yml`、`scripts/deploy.sh`、`nginx-conf.conf`、
`安装说明.md`。客户解压后只需改 `.env` 里的数据库/Redis 配置，执行 `bash scripts/deploy.sh`
即可完成拉镜像、建表/迁移、健康检查。

> 需要作者本机的 `license_private.pem`（gitignored）；缺失时先跑
> `uv run python scripts/license_gen.py keygen`。脚本会校验签发私钥与镜像内置公钥是否一致。

## 一、MySQL 高并发配置

> **优先 MySQL 8.0，兼容 5.7**：建表/迁移/种子在 5.7.44 实测通过，代码无 CTE/窗口函数等
> 8.0 专属特性，排序规则统一 `utf8mb4_unicode_ci`（`utf8mb4_0900_ai_ci` 为 8.0 专属，
> 配置勿用）。5.7 已过官方 EOL（2023-10），条件允许优先 8.0。

### 1. 连接数预算（先算这个，扩容前必看）

每个进程（app 的每个 uvicorn worker、每个 SAQ worker）各持有一个连接池：

```
总连接 ≈ (app 实例数 × WEB_CONCURRENCY + worker 实例数) × (DB_POOL_SIZE + DB_MAX_OVERFLOW)
```

默认 `.env`（20 + 50）× 7 进程（3 实例 × 2 进程 + 1 worker）= **490 连接**，
而 MySQL `max_connections` 默认只有 **151** —— 不调参扩实例就会直接报连接错误。

两边任选其一（推荐同时做）：

- **调大 MySQL**：`max_connections = 800`（每连接内存开销 ~256KB-1MB，8G 机器可承受）
- **收敛应用侧**：`.env` 改 `DB_POOL_SIZE=10`、`DB_MAX_OVERFLOW=10` → 7 进程 × 20 = 140

### 2. my.cnf 示例（MySQL 8.0，兼容 5.7；4C8G 起步）

```ini
[mysqld]
# ---- 连接 ----
max_connections        = 800        # 按上面公式算，留 shell/监控余量
thread_cache_size      = 64
table_open_cache       = 4096
max_allowed_packet     = 64M

# ---- InnoDB 内存（最重要的一项）----
# 缓冲池 = 索引+热数据，物理内存的 50-70%；8G 机器给 4-5G
innodb_buffer_pool_size = 4G

# ---- 写入吞吐（支付库的事务 fsync 是物理天花板）----
# redo 容量加大，减少检查点刷盘抖动（8.0.30+ 用该参数；5.7 改用 innodb_log_file_size = 512M）
innodb_redo_log_capacity = 1G
# 1 = 每事务落盘不丢（默认，支付场景保持）；2 = 每秒刷，最多丢 1s 事务，换约 2-5 倍写吞吐
innodb_flush_log_at_trx_commit = 1
# 开 binlog（主从/点恢复必须）时保持 1，与上一条同理
sync_binlog             = 1
innodb_flush_method     = O_DIRECT   # 跳过 OS 缓存，避免双缓冲

# ---- 观测 ----
slow_query_log          = 1
long_query_time         = 0.1        # 支付接口 P99 应 <100ms，超了就该看
slow_query_log_file     = /var/log/mysql/slow.log

# ---- 字符集 ----
character-set-server    = utf8mb4
collation-server        = utf8mb4_unicode_ci   # 与建表排序规则一致（0900 为 8.0 专属，勿用）
```

### 3. 宿主机 MySQL 供容器访问（默认形态）

MySQL 以系统服务方式跑在宿主机（`systemctl enable --now mysql`），容器经
`host.docker.internal` 访问 —— compose 已配 `extra_hosts: host-gateway`，
Linux 上该域名自动映射到宿主机网关。三件事必须确认：

```ini
# /etc/mysql/mysql.conf.d/mysqld.cnf —— 监听不能只绑 127.0.0.1
bind-address = 0.0.0.0          # 或精确绑 docker 网桥 IP（如 172.17.0.1）更收敛
```

```sql
-- 账号允许容器网段连接（compose 自定义网桥一般是 172.x.0.0/16，docker network inspect 查）
CREATE USER 'pay'@'172.%' IDENTIFIED BY '强密码';
GRANT ALL PRIVILEGES ON pay.* TO 'pay'@'172.%';
FLUSH PRIVILEGES;
```

- 防火墙放行容器网段到 3306（`ufw allow from 172.16.0.0/12 to any port 3306`）
- `.env`：`DB_HOST` 留给 compose 覆盖（`host.docker.internal`），`DB_USERNAME/DB_PASSWORD` 用上面建的账号

### 3b. 宿主机 Redis 供容器访问（默认形态）

Redis 同样以系统服务跑在宿主机（`systemctl enable --now redis`），容器经
`host.docker.internal:6379` 访问。Redis 监听非回环地址时 protected-mode 会直接拒绝
外部连接，必须设密码：

```conf
# /etc/redis/redis.conf
bind 0.0.0.0            # 或精确绑 docker 网桥 IP（如 172.17.0.1）更收敛
requirepass 强密码       # 监听非回环必设；对应 .env 的 REDIS_PASSWORD
```

- 防火墙放行容器网段到 6379（`ufw allow from 172.16.0.0/12 to any port 6379`）
- `.env`：`REDIS_HOST` 留给 compose 覆盖（`host.docker.internal`），`REDIS_PASSWORD` 填上面密码
- 验证：容器内 `python -c "import redis; r=redis.Redis(host='host.docker.internal', port=6379, password='...'); print(r.ping())"`

### 4. 容器化 MySQL（备选，不推荐）

不想装宿主机 MySQL 时，把下面追加进 `docker-compose.yml`（app 侧 `DB_HOST` 改成 `mysql`）：

```yaml
  mysql:
    image: mysql:8.0              # 5.7 亦可（下方参数已兼容）
    container_name: pay-gateway-mysql
    command:
      - --max_connections=800
      - --innodb_buffer_pool_size=4G
      - --innodb_log_file_size=512M
      - --character-set-server=utf8mb4
      - --collation-server=utf8mb4_unicode_ci
    environment:
      MYSQL_ROOT_PASSWORD: 改成强密码
      MYSQL_DATABASE: pay
    volumes:
      - mysql_data:/var/lib/mysql
    healthcheck:
      test: ["CMD", "mysqladmin", "ping", "-h", "127.0.0.1", "-p改成交际密码"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  mysql_data:
```

### 5. 验证

```sql
SHOW VARIABLES WHERE Variable_name IN
  ('max_connections','innodb_buffer_pool_size','innodb_redo_log_capacity',
   'innodb_flush_log_at_trx_commit','sync_binlog');
SHOW STATUS LIKE 'Threads_connected';      -- 当前连接，应远小于 max_connections
SHOW STATUS LIKE 'Max_used_connections';   -- 历史峰值，扩容后盯这个
```

## 二、应用侧配套

- 部署/升级统一走 `scripts/deploy.sh`（建表/迁移已内置，无需手工 db init）
- **三方插件目录**：`storage/plugins/<插件名>/`（compose 已挂载整个 `storage/`，无需改编排）。
  把编译好的插件目录（`plugin*.so` + `signature.json`）拷进去后，在后台「支付通道 → 刷新插件」点一次即可，
  **不用重启容器、不用重建镜像**（上传/同步会写跨进程刷新戳；后台插件列表刷新时强制比对，
  换个 worker 看到的也一定是最新。手工拷入的目录同样会被自动发现，列表先以「未同步」出现，点刷新落库）。
  与后台上传安装是同一目录，同名时覆盖镜像内置插件，后台来源标记为「三方」。
  `deploy.sh` 会自动预创建该目录（部署用户属主，便于 scp 投放）。
- **插件包必须签名**：编译镜像对三方插件**双向验签**（上传接口 + 加载扫描），
  包内缺 `signature.json` 或摘要/插件名不符一律拒绝/跳过。签名由 `scripts/build_plugin.sh`
  自动完成（私钥 `license_private.pem` 只在作者本机），公钥内置在镜像
  （`app/core/plugin_signing.py`，无环境变量可关）。后台「上传插件」按钮仅超级管理员可见。
- `.env`：`DB_POOL_SIZE / DB_MAX_OVERFLOW` 按“连接数预算”收敛；`WORKER_CONCURRENCY` 按 CPU 调
- 页面未暴露的运行期配置（`api_order_rate_limit`、`merchant_reg_open` / `merchant_reg_review`、
  `site_name`、`settle_d1_enable` 等）：站点配置页仅展示扣量 + 结算组（自动结算 / D+1 除外），
  其余设置键一律直接写 `settings` 表或调 `PATCH /admin-api/setting` 调整
  （如 `api_order_rate_limit` 下单限流，次/分钟/来源IP，0=不限，默认 60）
- 扩 app 实例：`./scripts/deploy.sh --scale 3`（上限 = 端口范围大小，见 compose 注释）
- nginx：宝塔用户把 `nginx-conf.conf` 粘进站点「配置文件」（替换 `pay.example.com` 为实际域名）；
  非宝塔：`sudo cp nginx-conf.conf /etc/nginx/conf.d/pay-gateway.conf && sudo nginx -t && sudo systemctl reload nginx`

### 授权码（AUTH_CODE）

编译镜像启动时校验 `.env` 中的 `AUTH_CODE`（校验完全离线，不访问网络；本地源码运行不校验）。
缺失或无效时容器**不退出**，以未授权模式运行：Web 页面请求直接返回纯文本「Invalid License Code」（不区分原因、不返回 HTML 页面），
业务接口（后台 API / 下单 / 回调）一律 503，后台任务（通知重试/查单补单/过期置位/自动结算）同时暂停；
`/health` 保持 200 供容器监控，但响应里 `licensed` 为 `false` —— 建议监控盯这个字段做告警。
过期判断取 `max(系统时间, 历史最晚时间)`（后者落盘 `storage/private/.license_clock`），**回拨主机时钟不能续命**；
`/license/expire` 公开输出授权到期日期（纯文本 `YYYY-MM-DD`，未授权时输出「未授权」）；
具体原因见 `docker logs`，常见对照：

| 报错（原因：…） | 处理 |
| --- | --- |
| 授权码为空 | `.env` 未填 `AUTH_CODE=`，向作者索取后填入 |
| 格式不正确 / 编码不正确 / 载荷… | 码被截断或改坏（一行完整复制，勿加引号外字符） |
| 签名无效 | 码不是本系统签发或被篡改，重新索取 |
| 授权已于 … 过期 | 向作者续期（换码） |
| 未内置授权公钥 | 镜像构建遗漏公钥（作者侧构建问题，此情况启动即退出） |

- **解除**：有效码填入 `.env` 后 `docker compose up -d` 重建（或 `docker compose restart app worker`，`.env` 为 bind 挂载，重启即重读）
- **运行中过期**：API 逐请求比对有效期（内存比较，过期即刻 503 断流，回调同步断流；`/health` 与 `/license/expire` 保留供监控）；SAQ worker 与容器本身需重启后由启动校验拦截。续期 = 换码 + 重启

## 三、容量速查（架构口径）

单实例（2 进程）约 1-2k QPS 下单、几百回调/s；水平扩展近似线性；
真正的天花板是 MySQL 单机写入 fsync（数千 TPS 量级）—— 到那一步再考虑读写分离/分库。
