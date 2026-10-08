# 部署指南

生产部署：仅 app/worker 容器化 + 宿主机 nginx 反代；**MySQL 与 Redis 均用宿主机系统服务**（非容器）。

| 组件 | 运行方式 | 参考 |
|---|---|---|
| app × N | 容器（`--scale app=N`，端口 `127.0.0.1:8000-8002`） | `docker-compose-example.yml` |
| worker | 容器（SAQ：通知重试/补单/过期关单，可 `--scale worker=N`） | 同上 |
| MySQL 8.0 | **宿主机系统服务（非容器）**；容器化仅为备选 | 本文·一 |
| Redis | **宿主机系统服务（非容器）**（队列/限流/锁/扣量计数） | 本文·一之 3 |
| nginx | **宿主机**（非容器），轮询上游 8000-8002 | `nginx-example.conf` |

## 零、打包与部署（各一条命令）

打包机（需 docker + pnpm，产出推仓库）：

```shell
scripts/build-image.sh --registry registry.example.com/myns --push
# 默认标签 日期-git短sha，另打 latest；--tag v1.2.3 可指定版本
# 前端与对接文档产物（static/frontend、static/docs）不入库，脚本会先现场构建再进镜像，产物缺失构建直接失败
```

服务器（只需 docker + compose + 宿主机 MySQL/Redis，不装 node/pnpm/python）：

```shell
git pull && PAY_IMAGE=registry.example.com/myns/pay-gateway:<tag> ./scripts/deploy.sh
```

一条命令完成：`.env` 缺失时自动生成并写入密钥（首次会提示填好 DB/Redis 再跑一次）→
拉镜像 → `up -d`（app×2 + worker）→ 等全部 healthy → 空库自动建表初始化 /
已有库自动 alembic 增量迁移。可重复执行（升级=换镜像标签再跑一次）；
`--scale N` 调 app 实例数，`--no-pull` 跳过拉取。

`PAY_IMAGE` 环境变量决定 compose 用的镜像名（默认 `pay-gateway:latest`，本机
`scripts/build-image.sh` 产出的就是它）。服务器上请固定带版本标签拉取，避免 latest 漂移。

## 一、MySQL 高并发配置

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

### 2. my.cnf 示例（MySQL 8.0，4C8G 起步）

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
# redo 容量加大，减少检查点刷盘抖动（8.0.30+ 用该参数；旧版用 innodb_log_file_size = 512M）
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
collation-server        = utf8mb4_0900_ai_ci
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
    image: mysql:8.0
    container_name: pay-gateway-mysql
    command:
      - --max_connections=800
      - --innodb_buffer_pool_size=4G
      - --innodb_redo_log_capacity=1G
      - --character-set-server=utf8mb4
      - --collation-server=utf8mb4_0900_ai_ci
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
    networks:
      - app-net

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
- `.env`：`DB_POOL_SIZE / DB_MAX_OVERFLOW` 按“连接数预算”收敛；`WORKER_CONCURRENCY` 按 CPU 调
- 页面未暴露的运行期配置（`api_order_rate_limit`、`merchant_reg_open` / `merchant_reg_review`、
  `site_name`、`settle_d1_enable` 等）：站点配置页仅展示扣量 + 结算组（自动结算 / D+1 除外），
  其余设置键一律直接写 `settings` 表或调 `PATCH /admin-api/setting` 调整
  （如 `api_order_rate_limit` 下单限流，次/分钟/来源IP，0=不限，默认 60）
- 扩 app 实例：`./scripts/deploy.sh --scale 3`（上限 = 端口范围大小，见 compose 注释）
- nginx：`sudo cp nginx-example.conf /etc/nginx/conf.d/pay-gateway.conf && sudo nginx -t && sudo systemctl reload nginx`

## 三、容量速查（架构口径）

单实例（2 进程）约 1-2k QPS 下单、几百回调/s；水平扩展近似线性；
真正的天花板是 MySQL 单机写入 fsync（数千 TPS 量级）—— 到那一步再考虑读写分离/分库。
