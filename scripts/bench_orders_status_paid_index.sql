-- ============================================================
-- P3-27 复现脚本：orders 增加 (status, paid_at) 复合索引的 EXPLAIN 前后数据
-- ============================================================
-- 用途：验收迁移 0004（ix_orders_status_paid）时，任何人都能在自己的 MySQL 上
--       复现 PR 描述里的「扫描行数 / 耗时」对比，不需要应用、Redis 或既有数据。
--
-- 要求：MySQL 8.0.18+（EXPLAIN ANALYZE）。5.7 可跑，但 FORMAT=JSON 部分需替换。
-- 运行：
--   mysql -h127.0.0.1 -P3306 -uroot -p < scripts/bench_orders_status_paid_index.sql
--
-- 数据集：独立临时库 payexplain_bench，先 DROP 再建，不影响任何业务库。
--         20 万单 / 180 天 / 70% 已支付（与 PR 描述同规模，保证数字可比）。
-- 口径：表结构与索引对齐 app/models.py 的 Order（除本迁移新增的
--       ix_orders_status_paid 外，其余索引与线上一致）。
-- 读法：每条 EXPLAIN ANALYZE 只看两处 ——
--       `rows=NNN`（实际扫描/命中行数）与 `actual time=a..b`（毫秒）。
--       同一行里出现 `ix_orders_status_paid` 说明吃上了新索引。
--
-- 参考结果（2026-11-17，MySQL 8.4 容器，warm cache，各跑 5 次取中位数）：
--   Q1 今日收入   140,000 行 / 47.1ms  →  777 行 / 0.41ms   （≈115×，正常吃到新索引）
--   Q2 30 日趋势  140,000 行 / 47.8ms  →  默认计划仍走旧索引 47.6ms；
--                 FORCE INDEX 后 23,330 行 / 10.3ms（≈4.6×）
--   Q3 累计已支付 140,000 行 / 44.4ms  →  44.8ms（无变化，本就需扫全部已支付行）
-- 结论：收益集中在「按 paid_at 窄区间过滤」的查询（今日/昨日收入）；
--       趋势图因 (status, paid_at) 不覆盖 money，优化器可能仍选旧索引。
-- ============================================================

CREATE DATABASE IF NOT EXISTS payexplain_bench DEFAULT CHARSET utf8mb4;
USE payexplain_bench;

DROP TABLE IF EXISTS orders;
CREATE TABLE orders (
    id              BIGINT       NOT NULL AUTO_INCREMENT,
    merchant_id     BIGINT       NOT NULL,
    status          TINYINT      NOT NULL DEFAULT 0,
    money           DECIMAL(12,2) NOT NULL,
    realmoney       DECIMAL(12,2) DEFAULT NULL,
    profit          DECIMAL(12,2) DEFAULT NULL,
    channel_id      INT          NOT NULL DEFAULT 0,
    payment_type_id INT          NOT NULL DEFAULT 0,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at         DATETIME     DEFAULT NULL,
    PRIMARY KEY (id),
    KEY ix_orders_merchant_created (merchant_id, status, created_at),
    KEY ix_orders_merchant_paid (merchant_id, paid_at),
    KEY ix_orders_created (created_at),
    KEY ix_orders_status_created (status, created_at)
) ENGINE = InnoDB;

-- ---- 造数：20 万单铺开 180 天，10% 未支付 / 70% 已支付 / 10% 已退款 / 10% 已过期 ----
SET SESSION cte_max_recursion_depth = 300000;
INSERT INTO orders (merchant_id, status, money, realmoney, profit,
                    channel_id, payment_type_id, created_at)
WITH RECURSIVE seq(n) AS (
    SELECT 1 UNION ALL SELECT n + 1 FROM seq WHERE n < 200000
)
SELECT 1 + (n % 500)                                     AS merchant_id,
       CASE WHEN n % 10 < 7 THEN 1
            WHEN n % 10 = 7 THEN 0
            WHEN n % 10 = 8 THEN 2
            ELSE 3 END                                   AS status,
       ROUND(10 + (n % 9900) / 100, 2)                   AS money,
       ROUND(10 + (n % 9900) / 100 * 0.98, 2)            AS realmoney,
       ROUND((n % 9900) / 100 * 0.01, 4)                 AS profit,
       1 + (n % 8)                                        AS channel_id,
       1 + (n % 4)                                        AS payment_type_id,
       CURDATE() - INTERVAL FLOOR(n / 1111) DAY + INTERVAL (n % 1440) MINUTE AS created_at
FROM seq;

-- 已支付订单的实付时间落在创建后 1 分钟（同一天），与线上「paid_at 按支付时间归属」一致
UPDATE orders SET paid_at = created_at + INTERVAL 1 MINUTE WHERE status = 1;

ANALYZE TABLE orders;

SELECT '========== 1) 无 ix_orders_status_paid（迁移前） ==========' AS section;

SELECT '---- Q1 今日收入（_today_stats）----' AS q;
EXPLAIN ANALYZE
SELECT payment_type_id, channel_id,
       COALESCE(SUM(realmoney), 0), COALESCE(SUM(profit), 0)
FROM orders
WHERE status = 1
  AND paid_at >= CURDATE()
  AND paid_at < CURDATE() + INTERVAL 1 DAY
GROUP BY payment_type_id, channel_id;

SELECT '---- Q2 近 30 日趋势（trend GROUP BY DATE(paid_at)）----' AS q;
EXPLAIN ANALYZE
SELECT DATE(paid_at) AS d, COUNT(*), COALESCE(SUM(money), 0)
FROM orders
WHERE status = 1
  AND paid_at >= CURDATE() - INTERVAL 29 DAY
  AND paid_at < CURDATE() + INTERVAL 1 DAY
GROUP BY d;

SELECT '---- Q3 累计已支付（count + sum）----' AS q;
EXPLAIN ANALYZE
SELECT COUNT(*), COALESCE(SUM(money), 0) FROM orders WHERE status = 1;

SELECT '========== 2) 建索引（迁移 0004 的 DDL） ==========' AS section;
CREATE INDEX ix_orders_status_paid ON orders (status, paid_at);
ANALYZE TABLE orders;

SELECT '---- Q1 今日收入（迁移后）----' AS q;
EXPLAIN ANALYZE
SELECT payment_type_id, channel_id,
       COALESCE(SUM(realmoney), 0), COALESCE(SUM(profit), 0)
FROM orders
WHERE status = 1
  AND paid_at >= CURDATE()
  AND paid_at < CURDATE() + INTERVAL 1 DAY
GROUP BY payment_type_id, channel_id;

SELECT '---- Q2 近 30 日趋势（迁移后，优化器自选）----' AS q;
EXPLAIN ANALYZE
SELECT DATE(paid_at) AS d, COUNT(*), COALESCE(SUM(money), 0)
FROM orders
WHERE status = 1
  AND paid_at >= CURDATE() - INTERVAL 29 DAY
  AND paid_at < CURDATE() + INTERVAL 1 DAY
GROUP BY d;

SELECT '---- Q2 近 30 日趋势（迁移后，FORCE INDEX 后的上半界）----' AS q;
EXPLAIN ANALYZE
SELECT DATE(paid_at) AS d, COUNT(*), COALESCE(SUM(money), 0)
FROM orders FORCE INDEX (ix_orders_status_paid)
WHERE status = 1
  AND paid_at >= CURDATE() - INTERVAL 29 DAY
  AND paid_at < CURDATE() + INTERVAL 1 DAY
GROUP BY d;

SELECT '---- Q3 累计已支付（迁移后）----' AS q;
EXPLAIN ANALYZE
SELECT COUNT(*), COALESCE(SUM(money), 0) FROM orders WHERE status = 1;

SELECT '========== 3) 索引清单确认 ==========' AS section;
SHOW INDEX FROM orders;

-- 验收完清理：DROP DATABASE payexplain_bench;
