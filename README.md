# tradepost

A marketplace with a social layer. Sellers list items with photos, options and shipping; buyers
follow sellers, save listings, ask public questions, chat and make offers, and pay by card (in
installments) or Pix through **Stripe** or **Mercado Pago**. Orders are shipped, delivered,
reviewed and, when something goes wrong, disputed and refunded; sellers are paid out after the
dispute window.

Payments run in **test mode only** (provider sandboxes): no real money moves, and the API refuses
live credentials.

**Live demo: https://tradepost-2ay.pages.dev** — runs on free tiers, so the first request after
a quiet period can take about a minute while the API wakes up. See [test payments](#test-payments).

[![CI](https://github.com/dedezza1D/tradepost/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/dedezza1D/tradepost/actions/workflows/ci.yml)

## What it does

**Buying and selling**

- **Listings**: up to 8 photos (validated and re-encoded, reorderable), options such as size or
  color with their own stock, draft → active → paused / sold out.
- **Search**: Portuguese full-text search that ignores accents, typo tolerance, filters by
  category, condition, price, state and distance from a city, stable pagination.
- **Shipping**: a fixed price set by the seller, or calculated from distance and package weight
  (standard or express), quoted on the listing and at checkout.
- **Cart and checkout**: items from several sellers in one payment; stock is reserved for 15
  minutes while the buyer pays; seller coupons (percentage or fixed, with minimum, use limit and
  expiry).
- **Payments**: Stripe (card and Pix through the Payment Element) and Mercado Pago (Pix QR code
  and card with installments through the Card Payment Brick), confirmed by signed webhooks or by
  asking the provider.
- **Orders**: one order per seller, `paid → shipped → delivered`, service, delivery time and
  destination on the order, seller cancellation with refund, full audit trail.
- **Disputes and returns**: the buyer opens a dispute after shipping or up to 7 days after
  delivery; buyer, seller and platform talk in a thread; the seller refunds (all or part) or
  declines; declined or unanswered disputes go to the platform to decide.
- **Seller money**: commission per sale, payout to the seller's Stripe Connect account 7 days
  after delivery (never during an open dispute), balance dashboard.

**Social**

- **Home feed** with *Trending*, *Near you* and *Following* tabs.
- **Follow sellers** and **save listings**, with alerts when a saved listing drops in price or has
  one unit left.
- **Public profiles** as storefronts: photo, bio, follower counts, reputation, listings, reviews.
- **Public questions** on listings, answered by the seller.
- **Chat per listing** with quick replies, unread counts, "seen" marks and live delivery.
- **Offers**: the buyer proposes a price, the seller accepts, declines or counters; an accepted
  offer is paid through the normal checkout at the agreed price.
- **Link previews**: a shared listing shows its photo, title and price in messaging apps.

**Trust and safety**

- **Reviews** after delivery and a **reputation level** computed from the last 90 days (sales,
  cancellations, late shipments, ratings), with a public page explaining the rules.
- **Block** a user (hides each other's listings, profiles, chats and purchases in both directions),
  **report** listings and profiles, moderation queue for admins.
- Messages with contact details or requests to pay outside the platform get a **warning** in the
  chat and are **refused** in public questions and answers.

**Interface**

- Phone-first layout with a bottom tab bar, light and dark themes, Portuguese and English
  (including notifications and errors), installable as an app (PWA).
- **Live notifications** over server-sent events, with polling as a fallback.

## Architecture

```
 Browser ──▶ Cloudflare Pages ──────────────────────────────────────────────────┐
             │ static React app (PWA)                                           │
             │ Functions: /api/*          → proxy (adds shared secret, client IP)│
             │            /media/*        → R2 bucket (photos, avatars)          │
             │            /listings/:id   → page with link-preview tags          │
             │            /og/:id.jpg     → preview image, cached at the edge    │
             └──────────────┬───────────────────────────────────────────────────┘
                            ▼
              FastAPI service (Docker, Render) ── in-process jobs ──┐
               │        │            │                              │
               ▼        ▼            ▼                              ▼
        PostgreSQL   Cloudflare   Stripe / Mercado Pago      webhook inbox, reconciliation,
          (Neon)       R2         sandboxes                  expiry, payouts, offer expiry,
                                                             dispute escalation
```

One modular service: `identity`, `catalog`, `orders`, `payments`, `reviews`, `social`, `chat`,
`questions`, `coupons`, `disputes`, `shipping`, `geo`, `notifications`. Each module owns its
tables and talks to the others through plain function calls. At this size, separate services
would add network failure modes without adding capacity; the boundaries are where a split would
happen later.

| Layer | Choice |
|---|---|
| API | Python 3.13, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2 |
| Database | PostgreSQL 18: full-text search, trigram matching, `earthdistance`, advisory locks, `LISTEN/NOTIFY`, JSONB |
| Web app | React 19, TypeScript, Vite, TanStack Query, Tailwind CSS, i18next, vite-plugin-pwa |
| Payments | Stripe PaymentIntents + Connect (Accounts v2), Mercado Pago Orders API |
| Tests | pytest on a real PostgreSQL, Playwright + axe, Node's test runner for edge code |
| Hosting | Cloudflare Pages + R2, Render (Docker), Neon, all on free tiers |

## How a payment works

```
buyer                    API                              provider
  │ POST /checkouts       │                                  │
  │ (Idempotency-Key) ───▶│ reserve stock and coupon,        │
  │                       │ price shipping, create orders    │
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
in-memory provider implements the same port for tests. `POST /checkouts/quote` runs the same
pricing without reserving anything, so the checkout page shows exactly what will be charged.

## Consistency guarantees

These are the properties the tests pin down, and how they are kept:

- **Never oversell.** Reserving is one statement per listing (and per option),
  `UPDATE … SET stock = stock - q WHERE stock >= q`, inside the checkout's transaction, always in
  the same order (listing id, then option) so concurrent checkouts cannot deadlock. A test fires
  25 buyers at the last unit: exactly one checkout is created.
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
  cancelled, and the stock and coupon uses go back. A payment that can no longer be cancelled
  (under review) gets a 10-minute grace period. Offers expire after 24 hours, unanswered
  disputes go to the platform after 72.
- **Contested writes have one winner.** Accepting an offer, using the last coupon, refunding a
  dispute: each is a conditional UPDATE or a row lock, so concurrent requests cannot both win.
- **Retries are safe.** `POST /checkouts` requires an `Idempotency-Key`; the same key returns
  the same checkout, the same key with a different body is rejected. Calls to providers carry
  their own idempotency keys (one PaymentIntent per checkout, one payout per order, one refund
  per dispute).
- **Money is integer cents** with an append-only ledger (`sale`, `platform_fee`, `payout`,
  `refund`, `fee_reversal`); balances are sums, never stored, and a payout is the order's balance.
- **Responses report committed data.** The database session commits before the response is sent,
  so a client that reads right after a write sees it.
- **Edits do not overwrite purchases.** Listings use optimistic locking (`version`); a sale bumps
  the version, so a seller editing a stale page gets `409` instead of resurrecting sold stock.
  Orders keep a snapshot of title, option and price.

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
  login per IP *and* per account, checkout, uploads, search, messages, offers, follows, saves,
  questions, reports. `429` with `Retry-After`. Per-account ceilings (follows, saves, blocks,
  coupons, photos) keep one account from filling the free database.
- **Authorization per resource**: sellers only see their orders and drafts; only the two people
  in a conversation or dispute can read it; another user's listing, order, checkout or
  conversation answers `404`. Tests cover these paths.
- **Uploads**: type from magic bytes (not name or `Content-Type`), pixel limit before decoding,
  re-encoded to WebP, which drops EXIF (including GPS) and anything appended to the file. Photos
  and avatars are served from R2 only under keys the API generates.
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
uv run pytest            # 132 tests on a real PostgreSQL (Testcontainers), migrated by Alembic
uv run ruff check . && uv run mypy
uv run python scripts/sandbox_check.py   # adapters against the Stripe and Mercado Pago sandboxes
```

- **Orders and payments**: reservation, idempotency, concurrency, webhook replay, lost webhooks,
  expiry races, double and late payments, the order lifecycle, payouts, refunds, reviews and
  access control.
- **Social**: counters, idempotent follow and save, price-drop and last-unit alerts, feed ordering
  and cursor pagination, trending pages that stay stable while new saves arrive, blocks hiding
  content in both directions, report deduplication and moderation.
- **Chat and offers**: access by the two participants only, unread and "seen" state, pagination,
  the off-platform detector, blocking, the offer state machine, a single acceptance under
  concurrent requests, expiry, and checkouts that charge the agreed price and nothing else.
- **Commerce**: questions and their visibility, reputation levels and inputs, option stock and the
  race on the last unit of an option, shipping quotes and checkout totals, installments, coupon
  rules and the use limit under concurrency, every dispute path, a single refund under concurrent
  requests, payout holds and the dispute window.
- **Adapters**: webhook signatures with locally signed payloads (no network).
  `sandbox_check.py` runs the real adapters against the sandboxes. It found two bugs the mocks
  could not: Stripe rejects `transfer_group` on transfers tied to a charge, and a Mercado Pago Pix
  under review cannot be cancelled.

```bash
cd frontend
npm run e2e              # Playwright: production build + API + PostgreSQL, phone and desktop
npm run test:edge        # link-preview tags built at the edge
```

The end-to-end suite runs five scenarios on a phone and a desktop viewport:

1. A seller lists an item in Portuguese; a buyer in English saves it, follows the seller, finds it
   in the Following tab, pays with the test card, gets it shipped and reviews it.
2. Buyer and seller chat: the reply must reach the buyer's open screen through the stream (faster
   than the fallback poll), then an offer is countered, accepted and paid at the agreed price.
3. A public question is asked and answered; a visitor sees it and follows the seller's
   reputation page.
4. An item with options and calculated shipping is bought with a coupon in 3 installments with
   express delivery, then disputed and refunded.
5. axe checks the main pages, the listing page and the conversation screen for accessibility
   violations.

CI (GitHub Actions) runs lint, strict type checking, migrations up → down → up on an empty
database, a drift check between models and migrations, the backend tests, the end-to-end suite,
dependency audits, the web build, the Functions type check and an image scan. Render deploys a
commit only after CI passed.

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

Without keys the checkout has no providers; `TP_PAYMENT_PROVIDERS='["fake"]'` enables the
in-memory test provider. With Stripe test keys, forward webhooks with the
[Stripe CLI](https://docs.stripe.com/stripe-cli):

```bash
cd backend
uv run python scripts/stripe_listen.py
```

### Test payments

| Provider | Method | How |
|---|---|---|
| Stripe | Card | `4242 4242 4242 4242`, any future date, any CVC (single payment) |
| Stripe | Pix | choose Pix in the Payment Element; the test QR page lets you simulate the payment |
| Mercado Pago | Pix | approved automatically by the sandbox after a few seconds |
| Mercado Pago | Card | a Mercado Pago test card, holder name `APRO`, the sandbox test buyer's email; installments up to 12 |

## Deployment

| Piece | Where | Notes |
|---|---|---|
| Web app + Functions | Cloudflare Pages (`frontend/`, `wrangler.toml`) | env: `API_ORIGIN`, `PROXY_SECRET`; R2 binding `MEDIA` |
| Photos and avatars | Cloudflare R2 bucket `tradepost-media` | API writes through the S3 API, Pages reads through the binding |
| API | Render web service (`render.yaml`) | Docker; migrations run at start-up; health check `/healthz` |
| Database | Neon | the non-pooled connection string; `sslmode` is adapted for asyncpg |
| Webhooks | `https://<pages-domain>/api/webhooks/stripe` (and `/mercadopago`) | through the proxy, bytes unchanged |
| Free-tier guard | `TP_MEDIA_QUOTA_BYTES`, `TP_MAX_PHOTOS_PER_USER` | uploads stop at 2 GB / 200 photos per account; photos cached at the edge |
| Commerce settings | `TP_MAX_INSTALLMENTS`, `TP_PAYOUT_HOLD_HOURS` | defaults: 12 installments, payouts 168 hours after delivery |

## Decisions and trade-offs

- **Separate charges and transfers (Stripe) instead of destination charges.** One payment can
  cover several sellers; each seller's share is transferred after the dispute window, which gives
  buyers an escrow-like guarantee.
- **Mercado Pago settles through the ledger.** Its native marketplace split needs every seller to
  connect a Mercado Pago account by OAuth and one payment per seller, and does not cover Pix. Here
  the platform account receives the payment and the ledger records what each seller is owed.
  Supporting both providers shows the port and adapters doing real work: the order logic does not
  know which provider was used.
- **Installments only where the provider supports them for Brazilian cards.** Mercado Pago's card
  form offers them; Stripe charges Brazilian cards in one payment, so its checkout is single
  payment.
- **Background work in the API process, coordinated by PostgreSQL.** The free tier has no
  worker. Tasks run every 30 seconds under advisory locks, so a second instance or a dedicated
  worker later would not double-process anything.
- **PostgreSQL does the jobs of four services**: search engine, queue, rate limiter and pub/sub
  (`LISTEN/NOTIFY` wakes the notification streams). Fewer moving parts on free hosting; each
  would move out (OpenSearch, a queue, Redis) only when load asks for it.
- **Mercado Pago without webhooks on the live demo.** Stripe notifications are signed and
  delivered to the API; Mercado Pago payments are confirmed by asking its API (while the buyer
  waits on the checkout page, and every 30 seconds in the background). The webhook handler and
  its signature check are implemented and tested; turning it on is one secret in the API's
  environment.
- **Feed computed on read ("pull"), not fanned out on write.** The Following tab joins
  `follows` with the sellers' newest listings through the `(seller_id, created_at)` index;
  Trending scores saves and sales in the last two weeks with exponential decay. At this scale
  one query per page is cheaper than writing every new listing into each follower's timeline.
  Fan-out on write starts paying off when sellers have thousands of followers and reads far
  outnumber writes; the switch would add a `timeline` table filled by a background job.
- **Chat over server-sent events plus plain POSTs, not WebSockets.** Messages are sent with
  POST; the existing notification stream sends a "wake" event and the open screens fetch what
  is new. One real-time channel instead of two, no sticky connections, and it works through the
  Pages proxy. If the stream fails, the app falls back to polling every 30 seconds. WebSockets
  (for typing indicators, say) would need a connection that stays on one instance.
- **Offers as a small state machine** (`pending → accepted | declined | countered | cancelled |
  expired`). Every transition is one conditional UPDATE on `status = 'pending'`, so concurrent
  accepts cannot both win, and a partial unique index allows one open offer per conversation.
  The checkout for an offer takes only the payment provider: listing, option, quantity and price
  come from the offer.
- **Shipping from a price table behind a port.** Quotes come from the distance between the two
  cities (IBGE coordinates), billable weight (the larger of real weight and
  length * width * height / 6000) and the service. Prices are simulated; a carrier aggregator such
  as Melhor Envio would be a second adapter, with the table as its fallback.
- **Options keep the listing's stock as their sum.** A reservation takes the option first and
  then the listing, always in the same order, so concurrent checkouts neither oversell an option
  nor deadlock. Retired options stay for the orders that point at them.
- **Payouts wait for the dispute window.** A seller is paid 7 days after delivery, never while a
  dispute is open, and the amount is the order's ledger balance, so a partial refund lowers it
  and a full refund cancels it. Refunds therefore never have to claw back a transfer.
- **Coupon uses are reserved, not counted after payment.** Opening a checkout takes a use with a
  conditional UPDATE under the limit, and an expired or cancelled checkout gives it back, so
  concurrent buyers cannot exceed the limit. The platform fee is charged on the discounted price;
  shipping is never discounted.
- **Reputation computed on read** from facts the platform records itself, so a seller cannot
  inflate it, and the published rules are the code's rules.
- **Counters updated in the same transaction** as the follow or save they count (atomic
  `x = x + 1`), so they never drift and need no reconciliation job.
- **Link previews rendered at the edge.** A Pages Function adds Open Graph tags to
  `/listings/:id` for messaging-app crawlers; the preview image is a JPEG the API renders on
  demand (WebP is not shown everywhere) and the edge caches for a day.
- **Migrations at container start**, because the free tier has no pre-deploy step. They are
  written expand/contract so the running release keeps working while they apply.

### Free-tier limits

- The API sleeps after 15 minutes without traffic; the first request then takes about a minute.
  Webhooks are retried by the providers and missed ones are caught by reconciliation; chat and
  notifications reach the app once it is awake.
- Neon's free plan has 1 GB of storage and scales to zero; R2 has 10 GB.
- With a budget: an always-on instance and a separate worker, a pooled database connection,
  alerting on webhook lag and failed payments, and a CDN-backed image pipeline.

### Known gaps

- Mercado Pago card payments (and so installments) need the sandbox test buyer's email and have
  not been run end to end yet; installments are covered by the tests with the in-memory provider.
  Mercado Pago Pix and both Stripe methods are checked on the live demo.
- Shipping prices are simulated and labels are not generated.
- Notifications reach the app while it is open; there is no Web Push.

## License

MIT
