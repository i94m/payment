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
uv run pytest                            # tests (sqlite at /tmp/epay_pytest.db)
uv run pytest tests/test_pay_flow.py::test_create_order_and_notify_paid   # single test
```

Other `artisan.py` groups: `permission sync`, `cache clear`, `admin`, `migrate` (`migrate create-tables` = create-all without alembic).

Frontend lives in `resources/backend` (pnpm, not part of `uv`):

```shell
cd resources/backend
pnpm install
pnpm dev      # vite :5173, proxies /admin-api, /merchant-api, /storage, /upload to :8000
pnpm lint
pnpm build    # tsc -b && vite build -> ../../static/frontend
```

Build outputs are **not committed** (gitignored): vite → `static/frontend/`, VitePress docs → `static/docs/`. CI (`.github/workflows/docker-image.yml`) and `scripts/build-image.sh` build them in place before `docker build`; a missing/incomplete output fails the image build via entry-file guards.

Local demo: `scripts/seed_demo.py` (demo/demo123456, pid=1001), `scripts/mock_upstream.py` (:9000), `scripts/demo.py [amount]`.

## Gotchas

- **Migrations are committed** (`alembic/versions/*.py` tracked; the old "gitignored" note is stale). Schema changes need a new revision chained to the current head (`uv run alembic history`), and `app/models.py` must stay in sync — fresh installs use `create_all` (`artisan.py db init`), existing DBs use `alembic upgrade head`. Keep the chain single-head (no two revisions sharing the same `down_revision`).
- **Frontend is React 19 + Vite + shadcn/ui + Tailwind 4** (react-router-dom 7, HashRouter), with two SPAs under `resources/backend/src/apps/{admin,merchant}` built from `index.html` (merchant, served at `/merchant`) / `admin.html` (served at `/admin`); marketing page is the site root `/` (also at `/landing`), served from `static/index.html` (committed source, alongside the gitignored build outputs in `static/frontend|docs`). Docker copies `static/` only.
- **`.env` is mandatory.** `app/core/config.py` raises at import if `APP_SECRET_KEY` or `STORAGE_LOCAL_SECRET` is empty — run `key generate` first.
- **Tests bootstrap their own env** in `tests/conftest.py` (sqlite, `APP_ENV`, etc.) *before* importing `app`. Redis/cache/limiter are disabled and the upstream is mocked with `httpx.MockTransport`. `asyncio_mode = "auto"`, so async test functions run without markers.
- **Money is `Decimal` end to end** (`Numeric(12,2)`); use `app/services/balance.py` `dmoney()` / the balance service, never `float`, when touching amounts.
- **Adding a payment plugin needs no registry edit.** Create `plugins/payment/<name>/plugin.py` (subclass `BasePayment`) + optional `info.json`; discovery is by directory. The registry key is `info.json`'s `id` (fallback: dir name). Prefer the class `meta: PluginMeta`; old sync plugins are tolerated via `maybe_await`.
- **Response envelope.** All internal admin/merchant + open API responses use `app/responses.suc()` → `{code:0,message,data}` and `err()`.
- **Runtime settings are a DB-backed KV registry.** `app/constants/settings.py` `SETTINGS_REGISTRY` is the single source of truth; add a key there and seed it via `db init`, read through `app/services/settings.py`. Values are typed (bool/int/decimal-as-string/list_int) — don't store raw strings.
- **Everything DB is async** (`AsyncSession`, asyncmy). Pass the session into service functions; don't introduce sync SQLAlchemy calls.
- **`scripts/e2e_smoke.py` is stale** after the React rewrite: it still uses Arco selectors (`.arco-modal`) and old Vue-style routes, and its docstring says `tests/e2e_smoke.py` though the file is in `scripts/`. Verify selectors before trusting it.
- **No Python linter/formatter/typecheck is configured** (no ruff/mypy/config). Only the frontend has eslint + `tsc` (via `pnpm build`). Don't assume a `make lint` or `ruff` step exists.

## Architecture map

- `main.py` — app wiring, CORS, rate limiter, JSON exception handlers, SPA registration, `/health`.
- `app/models.py` — all SQLModel tables (single file).
- `app/core/` — `config`, `database` (async engine/session), `security` (JWT + RBAC), `cache`, `settings`, `modules` (module registry for admin/merchant), `frontend` (static + SPA fallback).
- `app/routers/` — `admin/` (`/admin-api`), `merchant/` (`/merchant-api`), plus public `openapi.py` (`/orders`), `callbacks.py` (`/callbacks/{plugin}/{trade_no}`), `cashier.py` + `web.py` (`/cashier`).
- `app/services/` — `order.py` (idempotent create + `process_paid_order` accounting funnel), `channel.py` (routing/limits), `balance.py` (only balance mutation entrypoint, row-locked), `notify.py` (ladder retry), `plugin.py`/`plugin_interfaces.py`, `settings.py`, `crypto.py`.
- `app/worker/jobs/__init__.py` — SAQ jobs and cron list; register new jobs in `functions` and `cron_jobs`.
- `app/commands/` — artisan command modules, auto-registered by `artisan.py`.
- `app/logic/api_verify.py` + `app/lib/sign.py` — downstream merchant signature verification (MD5/RSA, 300s replay window).
- `plugins/payment/epay/` — the outbound EPay protocol adapter used as the default upstream channel.

Default local seeds: admin `admin`/`123456`, local captcha `0000` (only when `APP_ENV=local`), demo merchant `demo`/`demo123456` (pid 1001).
