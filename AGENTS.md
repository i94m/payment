# AGENTS.md

FastAPI + SQLModel(async) + SAQ + React 聚合支付网关. Python 3.12, `uv`, MySQL8/asyncmy, Redis. Docs/comments are in Chinese; keep new code consistent with surrounding style.

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
uv run pytest                            # tests (sqlite at /tmp/pay_pytest.db)
uv run pytest tests/test_pay_flow.py::test_create_order_and_notify_paid   # single test
```

Other `artisan.py` groups: `permission sync`, `cache clear`, `admin`, `migrate` (`migrate create-tables` = create-all without alembic).

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
- **Frontend is React 19 + Vite + shadcn/ui + Tailwind 4** (react-router-dom 7, HashRouter), with three SPAs under `resources/console/src/apps/{admin,agent,merchant}`: merchant (`index.html`, served at `/merchant`), admin (`admin.html`, served at `/{ADMIN_PATH_PREFIX}`, default `admin`, configurable in `.env` — page path only, `/admin-api` is fixed), agent panel (`agent.html`, served at `/agent`, API at `/agent-api`); marketing page is the site root `/` (also at `/landing`), served from `static/index.html` (committed source, alongside the gitignored build outputs in `static/console|docs`). Docker copies `static/` only.
- **`.env` is mandatory.** `app/core/config.py` raises at import if `APP_SECRET_KEY` or `STORAGE_LOCAL_SECRET` is empty — run `key generate` first.
- **Tests bootstrap their own env** in `tests/conftest.py` (sqlite, `APP_ENV`, etc.) *before* importing `app`. Redis/cache/limiter are disabled and the upstream is mocked with `httpx.MockTransport`. `asyncio_mode = "auto"`, so async test functions run without markers.
- **Money is `Decimal` end to end** (`Numeric(12,2)`); use `app/services/balance.py` `dmoney()` / the balance service, never `float`, when touching amounts.
- **Adding a payment plugin needs no registry edit.** Create `plugins/<name>/plugin.py` (subclass `BasePayment`) + optional `info.json`; discovery is by directory. The registry key is `info.json`'s `id` (fallback: dir name). Prefer the class `meta: PluginMeta`; old sync plugins are tolerated via `maybe_await`.
- **Response envelope.** All internal admin/merchant + open API responses use `app/responses.suc()` → `{code:0,message,data}` and `err()`.
- **Runtime settings are a DB-backed KV registry.** `app/constants/settings.py` `SETTINGS_REGISTRY` is the single source of truth; add a key there and seed it via `db init`, read through `app/services/settings.py`. Values are typed (bool/int/decimal-as-string/list_int) — don't store raw strings.
- **Everything DB is async** (`AsyncSession`, asyncmy). Pass the session into service functions; don't introduce sync SQLAlchemy calls.
- **`scripts/e2e_smoke.py` is stale** after the React rewrite: it still uses Arco selectors (`.arco-modal`) and old Vue-style routes, and its docstring says `tests/e2e_smoke.py` though the file is in `scripts/`. Verify selectors before trusting it.
- **No Python linter/formatter/typecheck is configured** (no ruff/mypy/config). Only the frontend has eslint + `tsc` (via `pnpm build`). Don't assume a `make lint` or `ruff` step exists.
- **Source protection is Docker-only.** The Dockerfile builder stage Cython-compiles `app/` + `plugins/` to `.so` (`scripts/compile_backend.py`, gated by `SO_COMPILE=1`) plus build-time import smoke tests and a no-residual-`.py` guard — the final image ships no business `.py`. Local dev never compiles; run source directly.
- **Deployment is documented in `DEPLOY.md`** (trust it over this summary for details): single `docker-compose.production.yml` runs app (scalable) + SAQ worker containers only; MySQL, Redis and nginx run as host services. Image comes from ghcr.io via `PAY_IMAGE` (default `ghcr.io/i94m/payment:latest`); `scripts/deploy.sh` on the server (auto `.env` bootstrap, pulls image, auto create-all or `alembic upgrade`), `scripts/build-image.sh` for local/offline builds.

## Architecture map

- `main.py` — app wiring, CORS, rate limiter, JSON exception handlers, SPA registration, `/health`.
- `app/models.py` — all SQLModel tables (single file).
- `app/core/` — `config`, `database` (async engine/session), `security` (JWT + RBAC), `cache`, `settings`, `modules` (module registry for admin/merchant), `frontend` (static + SPA fallback), `logger` (named loggers `app`/`plugins`/`plugin` + request_id tracing + QueueHandler async file output; NOT root — third-party logs don't land in app.log).
- `app/routers/` — `admin/` (`/admin-api`), `merchant/` (`/merchant-api`), `agent/` (`/agent-api`, 代理面板), plus public `openapi.py` (`/orders`), `callbacks.py` (`/callbacks/{plugin}/{trade_no}`), `cashier.py` + `web.py` (`/cashier`), `paypage.py` (免登录聚合收款页 `/p/{code}`).
- `app/addons/` — non-payment addons: `sms/`, `mail/` (providers like aliyun/qcloud/smtp), `storage/` (local/oss/cos). Payment plugins live only in `plugins/`.
- `app/services/` — `order.py` (idempotent create + `process_paid_order` accounting funnel), `channel.py` (routing/limits), `balance.py` (only balance mutation entrypoint, row-locked), `notify.py` (ladder retry), `plugin.py`/`plugin_interfaces.py`, `settings.py`, `crypto.py`.
- `app/worker/jobs/__init__.py` — SAQ jobs and cron list; register new jobs in `functions` and `cron_jobs`.
- `app/commands/` — artisan command modules, auto-registered by `artisan.py`.
- `app/logic/api_verify.py` + `app/lib/sign.py` — downstream merchant signature verification (MD5/RSA, 300s replay window).
- `plugins/epay/` — the outbound 易支付 protocol adapter used as the default upstream channel.

Default local seeds: admin `admin`/`123456`, local captcha `0000` (only when `APP_ENV=local`), demo merchant `demo`/`demo123456` (pid 1001).
