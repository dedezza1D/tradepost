# tradepost

A marketplace where anyone can list items with photos, buyers search and filter, pay by card or
Pix, and sellers manage orders and receive reviews. Payments run in **test mode only** (Stripe and
Mercado Pago sandboxes): no real money moves.

> Work in progress. Done: accounts, listings with photos, search. Next: cart, checkout with stock
> reservations, payments (Stripe Connect and Mercado Pago), orders, seller dashboard, reviews.

## Stack

| Layer | Choice |
|---|---|
| API | Python 3.13, FastAPI, SQLAlchemy 2 (async), Alembic |
| Database | PostgreSQL 17 (full-text search, trigram matching) |
| Web app | React 19, TypeScript, Vite, TanStack Query, Tailwind CSS |
| Storage | S3-compatible object storage (Cloudflare R2 in production, local disk in development) |
| Tests | pytest against a real PostgreSQL (Testcontainers), migrated with Alembic |

## Run locally

Requirements: Docker, [uv](https://docs.astral.sh/uv/), Node.js 22.

```bash
docker compose -p tradepost up -d --wait      # PostgreSQL on localhost:5433
cd backend
uv sync
uv run alembic upgrade head
uv run uvicorn tradepost.main:create_app --factory --port 8000
```

In another terminal:

```bash
cd frontend
npm install
npm run dev                                   # http://localhost:5180
```

The web app proxies `/api` and `/media` to the API, so both are served from one origin, as in
production.

## Tests

```bash
cd backend
uv run pytest                 # starts a disposable PostgreSQL container
uv run ruff check . && uv run mypy
```

To reuse a running database instead of a container, set
`TP_TEST_DATABASE_URL=postgresql+asyncpg://tradepost:tradepost@localhost:5433/tradepost_test`.

## Design notes

These grow with the project; the full write-up comes with the first deployment.

- **One modular service.** Modules (`identity`, `catalog`, and later `checkout`, `orders`,
  `payments`) have clear boundaries inside one deployable API. At this scale, separate services
  would add network failure modes without adding capacity.
- **Server-side sessions, not JWTs.** The session cookie is `HttpOnly`, `Secure`, `SameSite=Lax`
  and holds a random token whose SHA-256 is stored. Sessions can be revoked instantly (logout,
  password change signs out other devices). Unsafe requests also need a CSRF token and an allowed
  `Origin`.
- **Same origin in every environment.** The web app proxies the API (Vite in development, a
  Cloudflare Pages Function in production), so cookies are first-party and CORS stays closed. In
  production the API only accepts requests carrying the proxy's shared secret.
- **Rate limits in PostgreSQL.** One small instance does not need Redis: an atomic upsert per
  (key, window) counts requests, written in its own transaction so a failed request still counts.
  Logins are limited per IP and per account.
- **Uploads are untrusted.** The file type comes from magic bytes; dimensions are checked before
  decoding; every photo is re-encoded to WebP, which drops EXIF metadata (including GPS position)
  and anything appended to the file.
- **Search without a search engine.** PostgreSQL full-text search with the Portuguese dictionary
  over unaccented text (`câmera` matches `camera`), plus trigram word similarity for typos. Newest
  and price orders use keyset pagination, which stays fast at any depth.
- **Optimistic locking on listings.** Edits carry the version they were based on; a stale edit
  gets `409` instead of silently overwriting a newer price.
- **Money is integer cents.** Never floating point.

## License

MIT
