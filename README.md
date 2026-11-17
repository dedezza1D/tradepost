# tradepost

A marketplace where anyone can list items with photos, buyers search and filter, pay by card or
Pix through **Stripe** or **Mercado Pago**, and sellers ship, get paid out and receive reviews.

Payments run in **test mode only** (provider sandboxes): no real money moves, and the API refuses
live credentials.

[![CI](https://github.com/dedezza1D/tradepost/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/dedezza1D/tradepost/actions/workflows/ci.yml)

## What it does

- **Accounts**: sign up, sign in, server-side sessions, password change that signs out other devices.
- **Listings**: up to 8 photos each (validated and re-encoded), draft → active → paused / sold out.
- **Search**: Portuguese full-text search that ignores accents, typo tolerance, filters by
  category, condition, price and state, stable pagination.
- **Cart and checkout**: items from several sellers in one payment; stock is reserved for 15
  minutes while the buyer pays.
- **Payments**: Stripe (card and Pix through the Payment Element) and Mercado Pago (Pix QR code
  and card through the Card Payment Brick), confirmed by signed webhooks.
- **Orders**: one order per seller, `paid → shipped → delivered`, seller cancellation with refund,
  full audit trail.
- **Seller money**: commission per sale, funds held until the buyer confirms delivery, payout
  to the seller's Stripe Connect account, balance dashboard.
- **Reviews**: one per delivered order, seller rating on listings and profile.

## Architecture

```
 Browser ──▶ Cloudflare Pages ─────────────────────────────────────────────┐
             │ static React app                                           │
             │ Functions: /api/*  → proxy (adds shared secret, client IP)  │
             │            /media/* → R2 bucket (listing photos)            │
             └──────────────┬──────────────────────────────────────────────┘
                            ▼
              FastAPI service (Docker, Render) ── in-process jobs ──┐
               │        │            │                              │
               ▼        ▼            ▼                              ▼
        PostgreSQL   Cloudflare   Stripe / Mercado Pago      webhook inbox, reconciliation,
          (Neon)       R2         sandboxes                  expiry, payouts
```

One modular service: `identity`, `catalog`, `orders`, `payments`, `reviews`. Each module owns
its tables and talks to the others through plain function calls. At this size, separate
services would add network failure modes without adding capacity; the boundaries are where a
split would happen later.

| Layer | Choice |
|---|---|
| API | Python 3.13, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2 |
| Database | PostgreSQL 17: full-text search, trigram matching, advisory locks, JSONB |
| Web app | React 19, TypeScript, Vite, TanStack Query, Tailwind CSS |
| Payments | Stripe PaymentIntents + Connect (Accounts v2), Mercado Pago Orders API |
| Hosting | Cloudflare Pages + R2, Render (Docker), Neon, all on free tiers |

## How a payment works

```
buyer                    API                              provider
  │ POST /checkouts       │                                  │
  │ (Idempotency-Key) ───▶│ reserve stock, create orders     │
  │                       │ (one transaction)                │
  │ POST /payments ──────▶│ create PaymentIntent / order ───▶│
  │◀── client secret / Pix QR code                           │
  │ pays in the provider's widget or bank app ──────────────▶│
  │                       │◀──────────── signed webhook ─────│
  │                       │ store event (unique id), ask the │
  │                       │ provider for the real status ───▶│
  │                       │ mark paid: orders, ledger, cart  │
  │ GET /checkouts/{id} ─▶│ (polling also asks the provider) │
```

The rest of the system only sees a `PaymentProvider` port (`start`, `get_status`, `cancel`,
`refund`, `verify_webhook`). Each adapter maps its provider's statuses to five states. An
in-memory provider implements the same port for tests.

## Consistency guarantees

These are the properties the tests pin down, and how they are kept:

- **Never oversell.** Reserving is one statement per listing,
  `UPDATE … SET stock = stock - q WHERE stock >= q`, inside the checkout's transaction, in
  listing-id order so concurrent checkouts cannot deadlock. A test fires 25 buyers at the last
  unit: exactly one checkout is created.
- **A checkout is paid at most once.** Payment results are applied under a row lock on the
  checkout. A second successful attempt (the buyer paid the abandoned Pix code *and* the card) or
  a payment that lands after the reservation expired is **refunded automatically**.
- **Every path is idempotent.** Webhooks, reconciliation, expiry and the buyer's own polling can
  report the same payment at the same time; they all end in one function that ignores repeated
  or backward transitions. Ledger rows are unique per order and kind, webhook events per
  provider and event id.
- **Webhooks are not trusted for state.** A verified notification only says *which* payment
  changed; its status is read from the provider's API with our credentials.
- **Nothing waits forever.** A background task asks the provider about payments pending for more
  than a minute (lost webhooks, the API asleep on the free tier). Unpaid checkouts expire after
  15 minutes: the provider is checked first (a payment found then wins), open payments are
  cancelled, and the stock goes back on sale. A payment that can no longer be cancelled (under
  review) gets a 10-minute grace period.
- **Retries are safe.** `POST /checkouts` requires an `Idempotency-Key`; the same key returns
  the same checkout, the same key with a different body is rejected. Calls to providers carry
  their own idempotency keys (one PaymentIntent per checkout, one payout per order).
- **Money is integer cents** with an append-only ledger (`sale`, `platform_fee`, `payout`,
  `refund`, `fee_reversal`); balances are sums, never stored.
- **Edits do not overwrite purchases.** Listings use optimistic locking (`version`); a sale bumps
  the version, so a seller editing a stale page gets `409` instead of resurrecting sold stock.
  Orders keep a snapshot of title and price.

## Security

- **Sessions**: random token in an `HttpOnly; Secure; SameSite=Lax` cookie, only its SHA-256
  stored; revocable instantly. Passwords hashed with Argon2id (OWASP parameters); unknown emails
  still run a hash so timing does not reveal accounts.
- **CSRF**: token bound to the session, sent in a header on unsafe requests, plus an `Origin`
  check.
- **One origin, closed API**: the browser only talks to the Pages domain; a Pages Function
  proxies `/api` and adds a shared secret. The API rejects requests without it, so it cannot be
  called around the proxy and the client-IP header it receives is trustworthy.
- **Rate limits** in PostgreSQL (atomic upsert per key and window, own transaction): sign-up,
  login per IP *and* per account, checkout, uploads, search. `429` with `Retry-After`.
- **Authorization per resource**: sellers only see their orders and drafts; another user's
  listing, order or checkout answers `404`. Tests cover these paths.
- **Uploads**: type from magic bytes (not name or `Content-Type`), pixel limit before decoding,
  re-encoded to WebP, which drops EXIF (including GPS) and anything appended to the file. Photos
  are served from R2 only under keys the API generates.
- **Payments**: card data never reaches the API (Stripe Elements, Mercado Pago Bricks). The API
  refuses Stripe live keys and Mercado Pago credentials that do not belong to a test user.
- **Headers**: CSP limited to the payment providers' domains, HSTS, `nosniff`, `frame-ancestors
  'none'`. Errors return stable codes without internals; logs carry a request id and no query
  strings.
- **Supply chain**: `pip-audit`, `npm audit` and a Trivy scan of the image in CI; the runtime
  image has no pip and runs as a non-root user.

## Testing

```bash
cd backend
uv run pytest            # 53 tests on a real PostgreSQL (Testcontainers), migrated by Alembic
uv run ruff check . && uv run mypy
uv run python scripts/sandbox_check.py   # adapters against the Stripe and Mercado Pago sandboxes
```

- Domain tests drive the HTTP API with the in-memory provider: reservation, idempotency,
  concurrency, webhook replay, lost webhooks, expiry races, double and late payments, the order
  lifecycle, payouts, refunds, reviews and access control.
- Adapter tests verify webhook signatures with locally signed payloads (no network).
- `sandbox_check.py` runs the real adapters against the sandboxes. It found two bugs the mocks
  could not: Stripe rejects `transfer_group` on transfers tied to a charge, and a Mercado Pago Pix
  under review cannot be cancelled.

CI (GitHub Actions) runs lint, strict type checking, migrations up → down → up on an empty
database, a drift check between models and migrations, the tests, dependency audits, the web
build, the Functions type check and an image scan. Render deploys a commit only after CI passed.

## Run locally

Requirements: Docker, [uv](https://docs.astral.sh/uv/), Node.js 22.

```bash
docker compose -p tradepost up -d --wait          # PostgreSQL on localhost:5433
cd backend
cp .env.example .env                              # add your sandbox keys (optional)
uv sync
uv run alembic upgrade head
uv run uvicorn tradepost.main:create_app --factory --port 8000
```

```bash
cd frontend
npm install
npm run dev                                       # http://localhost:5180
```

Without keys the checkout has no providers. With Stripe test keys, forward webhooks with the
[Stripe CLI](https://docs.stripe.com/stripe-cli):

```bash
cd backend
uv run python scripts/stripe_listen.py
```

### Test payments

| Provider | Method | How |
|---|---|---|
| Stripe | Card | `4242 4242 4242 4242`, any future date, any CVC |
| Stripe | Pix | choose Pix in the Payment Element; the test QR page lets you simulate the payment |
| Mercado Pago | Pix | approved automatically by the sandbox after a few seconds |
| Mercado Pago | Card | a Mercado Pago test card, holder name `APRO`, and the sandbox test buyer's email |

## Deployment

| Piece | Where | Notes |
|---|---|---|
| Web app + proxy | Cloudflare Pages (`frontend/`, `wrangler.toml`) | env: `API_ORIGIN`, `PROXY_SECRET`; R2 binding `MEDIA` |
| Photos | Cloudflare R2 bucket `tradepost-media` | API writes through the S3 API, Pages reads through the binding |
| API | Render web service (`render.yaml`) | Docker; migrations run at start-up; health check `/healthz` |
| Database | Neon | the non-pooled connection string; `sslmode` is adapted for asyncpg |
| Webhooks | `https://<pages-domain>/api/webhooks/stripe` and `/mercadopago` | through the proxy, bytes unchanged |

## Decisions and trade-offs

- **Separate charges and transfers (Stripe) instead of destination charges.** One payment can
  cover several sellers; each seller's share is transferred after delivery, which gives buyers an
  escrow-like guarantee.
- **Mercado Pago settles through the ledger.** Its native marketplace split needs every seller to
  connect a Mercado Pago account by OAuth and one payment per seller, and does not cover Pix. Here
  the platform account receives the payment and the ledger records what each seller is owed.
  Supporting both providers shows the port and adapters doing real work: the order logic does not
  know which provider was used.
- **Background work in the API process, coordinated by PostgreSQL.** The free tier has no
  worker. Tasks run every 30 seconds under advisory locks, so a second instance or a dedicated
  worker later would not double-process anything.
- **PostgreSQL does the jobs of three services**: search engine, queue and rate limiter. Fewer
  moving parts on free hosting; each would move out (OpenSearch, a queue, Redis) only when load
  asks for it.
- **Migrations at container start**, because the free tier has no pre-deploy step. They are
  written expand/contract so the running release keeps working while they apply.

### Free-tier limits

- The API sleeps after 15 minutes without traffic; the first request then takes about a minute.
  Webhooks are retried by the providers and missed ones are caught by reconciliation.
- Neon's free plan has 1 GB of storage and scales to zero; R2 has 10 GB.
- With a budget: an always-on instance and a separate worker, a pooled database connection,
  alerting on webhook lag and failed payments, and a CDN-backed image pipeline.

## License

MIT
