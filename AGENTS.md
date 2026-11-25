# AGENTS.md

FastAPI + SQLModel(async) + SAQ + React 聚合支付网关. Python 3.12, `uv`, MySQL 8.0 优先（兼容 5.7）/asyncmy, Redis. Docs/comments are in Chinese; keep new code consistent with surrounding style.

The README describes business behavior but is partly stale (it says Vue3/Arco and lists migration files that are gitignored). Trust config and code over README prose.

## Commands

All backend commands must run from the **repo root** — `artisan.py` scans `app/commands/` and `app/core/config.py` loads `./.env` relative to cwd.

```shell
uv sync
cp .env.example .env
uv run python artisan.py key generate    # writes APP_SECRET_KEY + STORAGE_LOCAL_SECRET into .env
# edit .env for DB/Redis, then:
uv run python artisan.py db init         # create tables + admin + pay types + settings + permission tree
uv run uvicorn main:app --reload         # API on :8000
uv run python -m saq app.worker.settings # SAQ worker (needs Redis): notify retries / query-fill / expire
scripts/build_plugin.sh <name>           # compile a plugin to .so + upload zip (needs docker)
uv run python scripts/make_release.py    # interactive customer bundle (.env/compose/deploy.sh + zip) -> dist/releases/
uv run pytest                            # tests (sqlite at /tmp/pay_pytest.db)
uv run pytest tests/test_pay_flow.py::test_create_order_and_notify_paid   # single test
```

Other `artisan.py` groups: `permission sync`（改权限树后用；超管也可在「系统 → 账号管理」点「同步权限」，先预览再执行）, `cache clear`, `admin`, `migrate` (`migrate create-tables` = create-all without alembic).

Frontend lives in `resources/console` (pnpm, not part of `uv`):

```shell
cd resources/console
pnpm install
pnpm dev      # vite :5173, proxies /admin-api, /merchant-api, /agent-api, /storage, /upload to :8000
pnpm lint
pnpm build    # tsc -b && vite build -> ../../static/console

cd ../docs
pnpm build    # VitePress 对接文档 -> ../../static/docs
```

Build outputs are **not committed** (gitignored): vite → `static/console/`, VitePress docs → `static/docs/`. CI (`.github/workflows/docker-image.yml`) and `scripts/build-image.sh` build them in place before `docker build`; a missing/incomplete output fails the image build via entry-file guards (`console/{index,admin,agent}.html`, `docs/guide/protocol.html`). CI pushes to ghcr.io **amd64-only** (arm64 was dropped — QEMU-emulated Cython builds were 5–20× slower); Apple Silicon runs the amd64 image via Rosetta (`docker-compose.production.yml` pins `platform: linux/amd64`).

Local demo: `scripts/seed_demo.py` (demo/demo123456, pid=1001), `scripts/mock_upstream.py` (:9000), `scripts/demo.py [amount]`.

## Gotchas

- **Migrations are committed** (`alembic/versions/*.py` tracked; the old "gitignored" note is stale). Schema changes need a new revision chained to the current head (`uv run python -m alembic history` — the `python -m` form is reliable; bare `uv run alembic` can fail to spawn if the local venv's script shebangs are stale), and `app/models.py` must stay in sync — fresh installs use `create_all` (`artisan.py db init`), existing DBs use `alembic upgrade head`. Keep the chain single-head (no two revisions sharing the same `down_revision`).
- **Frontend is React 19 + Vite + shadcn/ui + Tailwind 4** (react-router-dom 7, HashRouter), with three SPAs under `resources/console/src/apps/{admin,agent,merchant}`: merchant (`index.html`, served at `/merchant`), admin (`admin.html`, served at `/{ADMIN_PATH_PREFIX}`, default `admin`, configurable in `.env` — page path only, `/admin-api` is fixed), agent panel (`agent.html`, served at `/agent`, API at `/agent-api`); marketing page is the site root `/` (DB setting `landing_enable`, off → `/` serves a minimal page showing the `site_name` setting centered, no redirect; the old `/landing` alias is removed), served from `static/index.html` (committed source, alongside the gitignored build outputs in `static/console|docs`). Docker copies `static/` only.
- **`.env` is mandatory.** `app/core/config.py` raises at import if `APP_SECRET_KEY` is empty — run `key generate` first. `STORAGE_LOCAL_SECRET` falls back to `APP_SECRET_KEY` when unset.
- **Tests bootstrap their own env** in `tests/conftest.py` (sqlite, `APP_ENV`, etc.) *before* importing `app`. Redis/cache/limiter are disabled and the upstream is mocked with `httpx.MockTransport`. `asyncio_mode = "auto"`, so async test functions run without markers.
- **Money is `Decimal` end to end** (`Numeric(12,2)`); use `app/services/balance.py` `dmoney()` / the balance service, never `float`, when touching amounts.
- **Adding a payment plugin needs no registry edit.** Create `plugins/<name>/plugin.py` (subclass `BasePayment`); discovery is by directory and the registry key is the directory name. All plugin metadata (`showname/types/inputs/version`) lives in the class `meta: PluginMeta` — there is no `info.json`. `meta.name` must equal the directory name. Old sync plugins are tolerated via `maybe_await`. Two tiers: **builtin** (`plugins/`, compiled into the image) and **third-party** (`storage/plugins/`, persistent volume — `.so` only, installed via admin zip upload or by dropping a compiled plugin dir in place; same name overrides builtin, no image rebuild needed; admin list tags them 内置/三方). Third-party packages must be Ed25519-signed: `signature.json` next to the `.so` (payload `PLUGIN1:` + base64url of `{name,sha256,version,iat}`, private key `license_private.pem`, public key baked in `app/core/plugin_signing.py`). Enforcement happens **only on the admin upload path** (the upload endpoint rejects unsigned packages); plugins copied straight into `storage/plugins/` are trusted and loaded without a signature check — operating server files is already root-equivalent. Source runs/pytest skip it too (`PLUGIN_SIGN_REQUIRED=1` opts in). `scripts/build_plugin.sh` signs automatically; `scripts/plugin_sign.py sign|verify|pubkey` are the manual tools. Upload requires the `channel.update` permission (same as 刷新插件) and the button is gated the same way. Package for customer upload with `scripts/build_plugin.sh <name>` → `dist/plugin_packages/<name>.zip` (whole `dist/` is gitignored).
- **Response envelope.** All internal admin/merchant + open API responses use `app/responses.suc()` → `{code:0,message,data}` and `err()`.
- **Runtime settings are a DB-backed KV registry.** `app/constants/settings.py` `SETTINGS_REGISTRY` is the single source of truth; add a key there and seed it via `db init`, read through `app/services/settings.py`. Values are typed (bool/int/decimal-as-string/list_int) — don't store raw strings.
- **扣量（deduct）是商户级配置 + 每商户独立计数，不要改回全局参数或全平台共用计数器。** 配置只存在于 `merchants.deduct_enable / deduct_frequency / deduct_minmoney`（NOT NULL，默认关）；没有全局 `deduct_*` settings key（迁移 `0012` 已删除并把旧全局值全量回填到每个商户，无「继承全局」语义）。判定在 `app/services/order.py` `_hit_deduct`：`enable && frequency > 0 && 实付金额 >= minmoney`（`>=`：正好等于起扣金额也扣；`minmoney = 0` = 不限金额；比较的是买家实付金额 `realmoney`，为 NULL 时回退商户申报额 `money`——随机金额单的 `realmoney` 是绝对金额，拿申报额比会让起扣金额形同虚设）。
- **扣量计数按商户独立**：Redis `deduct_order_counter:{merchant_id}` `INCR` 后 `n % frequency == 0`，Redis 不可用时回退 `counters` 行 `deduct_order:{merchant_id}`（`FOR UPDATE`，命中清零）。因此 A 配每 5 笔、B 配每 8 笔，各 40 笔合格单时 A 恰好扣 8 笔、B 恰好扣 5 笔，互不影响——这是用户拍板的验收口径。旧的全平台 key `deduct_order_counter` 与 `counters.deduct_order` 行已弃用（不迁移、不读取），每商户从 0 起算、第 N 笔首次命中；改频次或关后再开**不清零**，接着原位置继续。运维注意：配了 `CACHE_PREFIX` 时 `artisan cache clear` 会连计数器一起删，Redis 重启若丢增量也会让刻度回退，两者都只会造成每户至多一次「比 N 更短」的间隔。
- **扣量命中单必须对所有商户侧/代理侧视图隐藏**：命中即 `status=PAID` + `is_deducted=1`，不入账、不返佣、不通知；`merchant/dashboard.py` 的 `_PAID` 必须带 `is_deducted == False`，`_dump_order` 继续遮蔽状态/实收/支付时间/通知状态，`is_deducted` 与「扣量」字样不得出现在商户端或开放 API 的响应里；后台利润取平台留存口径（`profit + 扣量单 merchant_income`）。后台入口在商户列表「扣量」列（单商户弹窗）与「批量扣量」（只作用于未锁定商户 `fixed == False`，开启必须带频次、可选起扣金额；关闭只写开关位、保留频次与金额，即紧急停止——急停同样不覆盖锁定商户，锁定的要先解锁再单独处理）。手动补单 `undo_deduct` 的流水备注固定为中性的「订单补入账」。
- **Everything DB is async** (`AsyncSession`, asyncmy). Pass the session into service functions; don't introduce sync SQLAlchemy calls.
- **`scripts/e2e_smoke.py` is an opt-in browser E2E** (23 checks: admin/merchant login, permission-visible menus, a full browser payment loop). Playwright lives in the `e2e` dependency group, so a plain `uv sync` never installs it (145MB package + ~586MB browser cache); run it with `uv sync --group e2e && uv run playwright install chromium` then `uv run --group e2e python scripts/e2e_smoke.py`. It is already ported to the React+shadcn UI (React selectors, `#/...` hash routes) and is **not** wired into pytest or CI — treat its assertions as a manual smoke, and re-verify selectors after UI refactors.
- **No Python linter/formatter/typecheck is configured** (no ruff/mypy/config). Only the frontend has eslint + `tsc` (via `pnpm build`). Don't assume a `make lint` or `ruff` step exists.
- **Source protection is Docker-only.** The Dockerfile builder stage Cython-compiles `app/` + `plugins/` + `main.py` to `.so` (`scripts/compile_backend.py`, gated by `SO_COMPILE=1`) plus build-time import smoke tests and a no-residual-`.py` guard — the final image ships no business `.py` (only `artisan.py`, which deploy.sh runs as a script, `alembic/`, and `plugins/_example/`). Local dev never compiles; run source directly. The builder also runs two opt-in obfuscation passes (both on in the Dockerfile): `STRING_OBFUSCATE=1` turns every string literal into a runtime-decoded call (`scripts/string_obfuscate.py` + `app/core/_obf.py`, key injected per build; docstrings, f-string literal segments and `match` patterns are left alone because they must stay constants), and `MODULE_OBFUSCATE=1` renames every compiled `app/` module to an opaque stem (`_a1b2c3d4e5.cpython-*.so`) with a XOR-encrypted name→file map injected into `app/__init__.py`, where a `MetaPathFinder` resolves real module names — so imports stay untouched while on-disk filenames leak nothing. Both are skipped for `app/__init__.py`, `main.py`, `app/commands/**` (artisan discovers commands by filename) and `plugins/**` (registry key = directory name). The obfuscation key (and therefore every blob and opaque filename) is **deterministic** (`sha256(OBFUSCATE_KEY_SEED)`, default seed in `string_obfuscate.py`) so unchanged sources compile to identical C and ccache keeps hitting — a random per-build key would force a full 123-module recompile (~230s vs ~6s). Use `OBFUSCATE_RANDOM_KEY=1` or a new `OBFUSCATE_KEY_SEED` to re-shuffle everything once. CI accelerates the compile step with a ccache dance: the workflow restores `.ci-ccache` (workspace = build context), the builder `COPY`s it to `/opt/ccache` and compiles with `CC="ccache gcc"`, and the `ccache-export` stage copies the updated cache back out for `actions/cache` to save — unchanged modules compile in seconds; failed/cancelled runs save nothing (actions/cache post runs on success only).
- **License gate (`AUTH_CODE`).** `app/core/license.py` verifies an Ed25519-signed code from `.env` at config-import time, but **only when running compiled** (detection = module `__file__` suffix ≠ `.py`); source runs and pytest always skip it, so tests need no `AUTH_CODE`. Codes are bare two-segment `<payload>.<sig>` strings (legacy `PAY1.`-prefixed codes still verify). The public key has a single source: the baked-in `PUBLIC_KEY_B64`, compiled into the image's `.so` (no env/config override — deliberately, so deployers cannot swap in their own key and self-sign; re-keying = edit + rebuild + redistribute the image); empty/invalid code → no exit, soft-lock mode instead (process stays up; browser page requests get plain text `Invalid License Code`, every other request 503s with the same generic message — no reason/config details leak to the public, those land in `docker logs` stderr at startup; `/health` and `/license/expire` exempt — the latter returns the expiry date as plain text `YYYY-MM-DD`, or 「未授权」 when nothing is loaded); LicenseKeyError (baked key broken = author-side build bug) still fail-secure exits. Vendor workflow: `uv run python scripts/license_gen.py keygen` → paste the printed public key into `license.py`'s `PUBLIC_KEY_B64` → `issue --until YYYY-MM-DD` hands out one-line codes. `license_private.pem` is gitignored and never leaves the author's machine. The builder smoke test stubs the license module in-process (build has no code to verify) after asserting the public key is baked in. A per-request soft-lock middleware in `main.py` (`lock_reason()` = startup failure OR loaded `exp` passed; pure in-memory compare) 503s every non-`/health` / non-`/license/expire` request — `Accept: text/html` gets plain text `Invalid License Code`, everything else JSON (`source runs never lock`, dev unaffected). Soft-lock also pauses the background worker: `app/worker/__init__.py` wraps every job + `CronJob` (and `enqueue`) with `license_guard`, so notify retries / query-fill / expire / auto-settle stop while unlicensed; `/health` stays 200 but reports `licensed: false` for monitoring. Expiry compares against `max(system clock, highest clock ever seen)` — the high-water mark is persisted to `storage/private/.license_clock` in compiled images (`LICENSE_CLOCK_FILE` overrides), so rolling the host clock back cannot extend a license.
- **Deployment is documented in `DEPLOY.md`** (trust it over this summary for details): single `docker-compose.production.yml` runs app (scalable) + SAQ worker containers only; MySQL, Redis and nginx run as host services. Image comes from ghcr.io via `PAY_IMAGE` (default `ghcr.io/i94m/payment:latest`); `scripts/deploy.sh` on the server (auto `.env` bootstrap, pulls image, auto create-all or `alembic upgrade`), `scripts/build-image.sh` for local/offline builds.

## 前端列表展示约定（`resources/console`）

列表对齐按**内容类型**统一，表头跟随单元格：`headClassName` 与 `cellClassName` 写同一套对齐 class（`components/ui/table.tsx` 的 `TableHead` 默认左对齐）：

| 单元格内容类型 | 对齐 | 写法 |
|---|---|---|
| 数字：金额 / 数量 / 比率 / 次数 / 状态码 | 靠右 | `headClassName` 加 `text-right`，`cellClassName: "text-right"` |
| 字符串：名称 / 备注 / 时间 / URL | 靠左 | 默认，不写对齐 class |
| 编号类标识：`id`、`pid`、订单号、结算单号 | 靠左 | 默认（按字符串处理，不按数字靠右） |
| 状态徽标 / 标签 / 开关 / 操作 | 居中 | `headClassName` 加 `text-center`，`cellClassName: "text-center"`；单元格内容是 flex 容器时另加 `justify-center` |

- 订单状态色板只允许有一份：`src/lib/order-status.ts`（固定色值、不用主题色 `primary`、无边框，深色模式沿用同色相），三端共用 `components/order-status.tsx` 的 `OrderStatusBadge`；通知状态复用 `NOTIFY_STATUS_CLASS`，不要在页面里另写状态颜色。
- 详情弹窗的键值表用 `components/detail-table.tsx`（`DetailTable`：桌面一行两组、窄屏一行一组），不要再手写 `flex justify-between` 的行。

## Architecture map

- `main.py` — app wiring, CORS, rate limiter, JSON exception handlers, SPA registration, `/health`.
- `app/models.py` — all SQLModel tables (single file).
- `app/core/` — `config`, `database` (async engine/session), `security` (JWT + RBAC), `cache`, `settings`, `modules` (module registry for admin/merchant), `frontend` (static + SPA fallback), `logger` (named loggers `app`/`plugins`/`plugin` + request_id tracing + QueueHandler async file output; NOT root — third-party logs don't land in app.log).
- `app/routers/` — `admin/` (`/admin-api`), `merchant/` (`/merchant-api`), `agent/` (`/agent-api`, 代理面板), plus public `openapi.py` (`/orders`), `callbacks.py` (`/callbacks/{plugin}/{trade_no}`), `cashier.py` + `web.py` (`/cashier`), `paypage.py` (免登录聚合收款页 `/p/{code}`).
- `app/addons/` — non-payment addons: `sms/`, `mail/` (providers like aliyun/qcloud/smtp), `storage/` (local/oss/cos). Payment plugins live only in `plugins/`.
- `app/services/` — `order.py` (idempotent create + `process_paid_order` accounting funnel), `channel.py` (routing/limits), `balance.py` (only balance mutation entrypoint, row-locked), `notify.py` (ladder retry), `plugin.py`/`plugin_interfaces.py`, `settings.py`, `crypto.py`, `upgrade.py` (镜像版本发现 + 面板升级标记协议).
- `app/upgrader.py` — 面板一键升级执行器（compose profile `upgrade` 的 upgrader 服务）：httpx 直连 docker.sock 轮询 `storage/upgrade/` 请求，拉镜像→迁移→逐台重建容器→自更新；命令行升级走 `scripts/deploy.sh upgrade`（经 `artisan.py upgrade` 查版本）。
- `app/worker/jobs/__init__.py` — SAQ jobs and cron list; register new jobs in `functions` and `cron_jobs`.
- `app/commands/` — artisan command modules, auto-registered by `artisan.py`.
- `app/logic/api_verify.py` + `app/lib/sign.py` — downstream merchant signature verification (MD5/RSA, 300s replay window).
- `plugins/epay/` — 易支付 V1 outbound adapter (MD5, `submit.php`/`mapi.php`/`api.php`), the default upstream channel; `plugins/epayv2/` — 易支付 V2 adapter (RSA2, `api/pay/*`), a separate protocol, not a drop-in replacement.

Default local seeds: super admin `superadmin`/`123456` (fixed, no env var), local captcha `0000` (only when `APP_ENV=local`), demo merchant `demo`/`demo123456` (pid 1001).
