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
  category, condition, price, state and distance from a city, stable pagination, suggestions
  while typing. With an embedding model, hybrid search also finds what people mean ("algo para
  pedalar" finds bikes); see [Search quality](#search-quality).
- **Discovery**: similar items on every listing; saved searches that notify about new matches.
- **Listing assistant**: a price range from similar listings and tips for the seller's own
  listings (dark photo, vague title, price far above similar items); with a vision model, a
  draft of title, description, category and condition from the photos. See
  [Listing assistant](#listing-assistant).
- **Moderation**: listings that may be prohibited (weapons, drugs, counterfeits, wild animals,
  documents, e-cigarettes) wait for an admin instead of going live; reports reach the admin
  with a suggested priority; scam patterns in chat get a warning. See [Moderation](#moderation).
- **Seller dashboard**: views, saves, questions, chats, orders and sales per listing and per
  day for 7, 30 or 90 days, conversion, median time to answer a chat and what coupons
  brought in, with a CSV export. See [Seller dashboard](#seller-dashboard).
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
- **Notifications outside the app**: Web Push to the person's phones and computers and
  e-mail for sales, payments, shipments and disputes, chosen per kind of notice. See
  [Notifications outside the app](#notifications-outside-the-app).

**Operations**

- **Operations page for admins**: requests per minute, error rate, latency percentiles per route,
  grouped server errors with their traceback, background jobs, payments waiting, database size,
  AI usage and the AI on/off switch.
- **Alerts** (error rate, failing or stalled jobs, payment notifications backing up, payments
  stuck, database near the free limit) delivered to admins as in-app notifications, once per
  incident.
- A scheduled GitHub Actions check of the live demo; a failed run is the alert.

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
`questions`, `coupons`, `disputes`, `shipping`, `geo`, `notifications`, `ops`, `ai`. Each module owns its
tables and talks to the others through plain function calls. At this size, separate services
would add network failure modes without adding capacity; the boundaries are where a split would
happen later.

| Layer | Choice |
|---|---|
| API | Python 3.13, FastAPI, SQLAlchemy 2 (async), Alembic, Pydantic v2 |
| Database | PostgreSQL 18: full-text search, trigram matching, pgvector, `earthdistance`, advisory locks, `LISTEN/NOTIFY`, JSONB |
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

## Observability without outside services

Everything lives in PostgreSQL, so the free tier needs no monitoring account:

- A middleware counts requests per minute and route template (`/api/listings/{id}`), with a
  latency histogram of fixed buckets, so percentiles can be estimated after aggregating minutes
  (an average cannot give a p95). A job writes the counts every 30 seconds; a week is kept.
- Unhandled errors are grouped by fingerprint (exception type, route and the deepest frame in our
  code; never the message, which carries ids) with a count, first and last seen, the request id
  and the traceback. The client only ever sees a stable error code.
- Every background job records its last run, duration, items processed and consecutive failures.
- Alert rules read those tables; when one starts firing, admins get one in-app notification.
- Logs are JSON with the request id on every line, and never include query strings.

**AI module.** Every model call goes through one client: an on/off switch admins control, a daily
spending ceiling for paid models (a ceiling of zero allows no paid calls), a cache by hash of the
input, and a log of tokens, latency, cost and failures per call. The live demo runs without a
model (`TP_AI_PROVIDER=none`) and works fully without it; AI features are evaluated with an open
model on a local Ollama server.

## Search quality

Listings are embedded with [bge-m3](https://huggingface.co/BAAI/bge-m3) (multilingual) and stored
in PostgreSQL with pgvector; a background job re-embeds a listing when its text changes. A search
takes the 50 best full-text matches and the 50 nearest listings by meaning and merges them with
reciprocal rank fusion, which needs no calibration between the two kinds of score.

`backend/eval` holds a judged evaluation set: 164 generated listings in 18 product kinds and 95
queries written the way people type (synonyms, slang, English words, typos, vague needs).
Relevance is computed from each listing's attributes, so nothing is hand-labelled. Weights and the
distance cutoff were tuned on half of the queries; the table reports the other half.

| Search | nDCG@10 | Recall@10 | MRR | Queries with no result |
|---|---:|---:|---:|---:|
| Full-text only (live demo) | 0.515 | 0.558 | 0.683 | 32% |
| Hybrid, nomic-embed-text | 0.818 | 0.832 | 0.887 | 0% |
| Hybrid, bge-m3 | **0.893** | **0.939** | **0.978** | 0% |

A query embedding takes about 46 ms on a laptop GPU, and the vector scan about 18 ms. The limit:
distance alone cannot tell that the catalog does not sell something. A cutoff strict enough to
silence queries such as "ração para cachorro" also drops real matches, so the API flags pages
where no listing matched the words and the app shows them as *similar items* instead of results.
Full report: [`backend/eval/results/search.md`](backend/eval/results/search.md).

## Listing assistant

Selling starts with photos. With a vision model available, the form offers to fill in title,
description, category and condition from them; the seller reviews every field before anything
is published. One structured call (JSON schema) returns the draft, and the server checks it:
sentences claiming what a photo cannot show ("funcionando perfeitamente", "ótimo estado") are
removed, because only the seller knows them. A small model sometimes loops on a photo until the
server aborts the answer; the request is then retried with a growing repeat penalty.

The price range and the tips need no model, so they also run on the live demo. The range comes
from the asking prices of the most similar listings (by meaning with embeddings, by words
without them), trimmed of outliers, with the count it is based on. Tips check the seller's own
listing: few or dark photos, a vague or all-caps title, a short description, a price well above
similar listings.

`backend/eval` holds 84 freely licensed photos from Wikimedia Commons (sources and licenses in
`listing_photos.json`; the images are fetched, not committed), 3 to 5 per product kind, curated
by hand to show one item the way a seller would photograph it. With Qwen2.5-VL 7B on a laptop
GPU:

| Prompt | Category right | Title names the product | Raw unsupported claims |
|---|---:|---:|---:|
| Category slugs in English, with a guide to each category | 81% | 80% | 4% |
| Category names in Portuguese, with the guide (in use) | **86%** | 81% | 6% |
| Category names in Portuguese, no guide | 69% | 80% | 4% |

The first version filed every camera and headphone under `phones`: the model read *fone de
ouvido* as phones. Naming the categories in Portuguese fixed that. It was written after seeing
those mistakes on the same photos, so 86% is optimistic. The remaining mistakes are taxonomy
calls (an air fryer filed as a large appliance, a stroller under toys). The price range had
its median within the expected range for 90% of the drafts. A draft takes about 7 s (1,650
tokens in, mostly the image, 85 out). Full report:
[`backend/eval/results/assistant.md`](backend/eval/results/assistant.md).

## Moderation

Every check has two layers: rules (word lists and patterns) that always run, including on the
live demo, and a model that reads the text in context when one is available. Nothing is
punished automatically: a risky listing waits for an admin, a suspicious message gets a warning
next to it, and a report gets a suggested priority and reason; people decide.

`backend/eval/moderation_dataset.py` is a hand-labelled set written before the checks: 82
listings (42 prohibited; 39 hard cases such as a Nerf gun, a glue gun, a painting reproduction,
or "pist0la" and "erva" in disguise), 50 chat messages (25 scams; honest questions about paying
by Pix through the site among the hard cases) and 30 reports. With Qwen2.5-VL 7B:

| Check | Decision | Precision | Recall |
|---|---|---:|---:|
| Listings | Rules | 100% | 88% |
| Listings | Model, medium or high risk | 100% | 50% |
| Listings | **Rules or model** (in use) | **100%** | **98%** |
| Chat | Rules (payment outside the site) | 77% | 40% |
| Chat | **Model** (in use with a model) | **100%** | **84%** |

The model names the right category for almost every prohibited listing but often calls the
risk low, so alone it misses half; flagging any category it names would hold 19 innocent
look-alikes. Combined with the rules, it catches what the words miss without false alarms.
In chat the model wins outright, and it also clears the rules' warning from honest messages
about Pix. For reports, the rules' priority ranks 85% of the urgent ones first against 31% for
the model, which tends to call things low priority, so the queue is ordered by the rules and
the model adds what the content is and why. Full report:
[`backend/eval/results/moderation.md`](backend/eval/results/moderation.md).

## Seller dashboard

The dashboard reads saves, questions, conversations and orders from their own tables, so its
numbers cannot drift from what the seller sees in orders and chat; tests check them against
those rows. Only views are new. A listing page opened by someone other than the seller or an
admin counts once per visitor per day (the account, or a hash of address and browser for
visitors who are not signed in; link previews and crawlers are left out). Views are counted in
memory and a job adds them to `listing_daily_views`, one row per listing and day, like the
request metrics: a page view never waits on a write. Days are Brasília days; 400 days are kept.

Orders count when paid and stay counted after shipping and delivery; refunds are shown apart.
Shop totals count each order once and use what the buyer paid for the items after coupons;
the per-listing table counts an order in each listing it contains, at item prices. Response
time is the median from a buyer's first message to the seller's first answer after it. The
chart is a single series picked from the totals above it, grouped by week when a bar per day
would be too thin to read, with a table view and keyboard reading of each bar.

## Notifications outside the app

Every notice is created in the transaction of the event that causes it. In that same
transaction an outbox row is added for each channel the person wants for that kind of notice,
so a push or an e-mail exists exactly when the sale, message or dispute committed. A job sends
due rows every 30 seconds, retries failures with backoff (five attempts), and records why a row
was skipped: no device, read in the app first, more than a day old, or the channel not set up.

| Kind of notice | Push (default) | E-mail (default) |
|---|---|---|
| Messages and questions | on | none |
| Offers | on | none |
| Orders and disputes | on | on: sale, payment, shipment, refund and dispute decisions |
| Searches and saved items | on | none |
| Activity (followers, reviews, moderation) | off | none |

**Web Push without a paid service.** The browser's push service (Google, Mozilla, Apple) only
relays an encrypted message; the API encrypts it for the device (RFC 8291) and signs its
request with a VAPID key (RFC 8292), built on `cryptography`. A test checks the encryption
byte for byte against the RFC's example. Only a VAPID private key is configured; nothing is
signed up for. Two rules keep it safe: the API posts only to the browsers' push services (an
endpoint elsewhere would make it a proxy to any address), and a device receives only while the
session that registered it is signed in, so a shared computer stops showing someone's notices
when they sign out. Signing out also removes the browser's subscription, and another person
signing in later does not inherit it. On iPhone and iPad, web push needs the site added to the
home screen; the settings page says so.

**E-mail through any SMTP service.** Messages are written on the server in the account's
language, with the same sentences as the app, as plain text and HTML, and link to the order
and to the notification settings. Without a service configured (`TP_EMAIL_PROVIDER=none`)
nothing is sent; `log` writes them to the log for development.

## Security

- **Sessions**: random token in an `HttpOnly; Secure; SameSite=Lax` cookie, only its SHA-256
  stored; revocable instantly. Passwords hashed with Argon2id (OWASP parameters); unknown emails
  still run a hash so timing does not reveal accounts.
- **Password reset**: the request answers the same for every address, and the lookup and the
  e-mail happen after the response, so neither the answer nor its timing reveals accounts. The
  link carries a 256-bit token in the URL fragment (never sent to a server), stored only as its
  SHA-256; it works once, for 30 minutes, and a newer link or a password change voids it. The
  new password follows the sign-up rules; every session is signed out and the owner is told by
  e-mail. Demonstration accounts and accounts without a usable password get no link.
- **Two-step verification**: optional for every account, required for administration. An
  authenticator app (TOTP, RFC 6238) whose secret is stored encrypted (AES-GCM, `TP_MFA_KEY`);
  a code is accepted once, for its 30-second step or the previous one. Ten single-use recovery
  codes, stored as SHA-256. After the password, a five-minute pending session waits for the code
  and is not a sign-in; a correct code starts a new session. Wrong codes are limited per account
  like passwords. Turning it on or off asks for the password again and signs out other devices.
- **CSRF**: token bound to the session, sent in a header on unsafe requests, plus an `Origin`
  check.
- **One origin, closed API**: the browser only talks to the Pages domain; a Pages Function
  proxies `/api` and adds a shared secret. The API rejects requests without it, so it cannot be
  called around the proxy and the client-IP header it receives is trustworthy.
- **Rate limits** in PostgreSQL (atomic upsert per key and window, own transaction): sign-up,
  login per IP *and* per account, password reset per IP *and* per address, checkout, uploads,
  search, messages, offers, follows, saves, questions, reports. `429` with `Retry-After`.
  Per-account ceilings (follows, saves, blocks, coupons, photos) keep one account from filling
  the free database.
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
uv run pytest            # 161 tests on a real PostgreSQL (Testcontainers), migrated by Alembic
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
- **Discovery**: embeddings kept current after edits, hybrid search finding meaning the words
  miss, the similar-items flag, similar listings with and without a model, suggestions, and
  saved-search alerts sent once, never for the user's own listings. A deterministic fake model
  stands in for the real one.
- **Listing assistant**: drafts from photos (photos shrunk before the model, at most three,
  unsupported claims removed, not-a-product answers refused, a looping answer retried), price
  ranges that ignore outliers and fall back to the category, and every tip.
- **Moderation**: rules against look-alikes, listings held at publishing and after an edit,
  approve and reject with the seller's notification, report triage ordering the admin queue,
  chat warnings added and cleared by the model, and nothing done without a model where the
  rules already ran.
- **Seller dashboard**: a visitor counted once a day, the seller, admins and crawlers left
  out, and every total matched against the saves, questions, conversations, orders and
  coupons it comes from, per listing, per day and in the CSV export.
- **Notifications outside the app**: the RFC 8291 example, the VAPID token, texts in both
  languages, and end to end with a stand-in push service that decrypts what it receives:
  a chat message on the buyer's phone, sale e-mails in each side's language, preferences,
  retries, devices the push service dropped, notices read in the app first, sign-out, and
  endpoints outside the browsers' push services refused.
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

### Load test

`backend/scripts/loadtest.py` runs concurrent visitors through the public browsing paths (feed,
search, listing, questions) and reports throughput and latency percentiles, counting rate-limited
answers apart from errors.

On a laptop (i7-13620H), one API process with PostgreSQL in Docker and the rate limits raised:

| Visitors | Requests/s | p50 | p95 | p99 | Errors |
|---:|---:|---:|---:|---:|---:|
| 1 | 33 | 28 ms | 50 ms | 68 ms | 0 |
| 5 | 63 | 75 ms | 121 ms | 188 ms | 0 |
| 10 | 58 | 159 ms | 292 ms | 458 ms | 0 |
| 20 | 53 | 346 ms | 628 ms | 892 ms | 0 |

One Python process saturates at about 60 requests/s; past that, latency grows and throughput does
not, which is where a second worker process or instance would come in. Against the live demo, the
platform's own bot protection on `*.pages.dev` steps in first: a few hundred automated requests in
a minute from one address get a browser challenge before the API's rate limits are reached. That
protection is not configurable on the free `pages.dev` domain; a custom domain would allow tuning
it.

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

To try push notifications locally, generate a VAPID key into `backend/.env` and run the
production build (`npm run build && npx vite preview`), which installs the service worker;
`TP_EMAIL_PROVIDER=log` writes e-mails to the API log:

```bash
cd backend
echo "TP_VAPID_PRIVATE_KEY=$(uv run python -c 'from tradepost.notifications.webpush import generate_vapid_private_key as g; print(g())')" >> .env
echo "TP_EMAIL_PROVIDER=log" >> .env
```

To try semantic search, install [Ollama](https://ollama.com), pull the model and start the API
with it; `seed_catalog` loads the evaluation catalog, and the background job embeds it within a
minute:

```bash
ollama pull bge-m3
cd backend
uv run python -m scripts.seed_catalog
TP_AI_PROVIDER=ollama uv run uvicorn tradepost.main:create_app --factory --port 8000
uv run python -m eval.search_eval                 # rerun the evaluation (also: ollama pull nomic-embed-text)
```

The listing assistant's draft needs a vision model (about 6 GB):

```bash
ollama pull qwen2.5vl:7b
cd backend
uv run python -m eval.listing_photos              # fetch the evaluation photos from Wikimedia Commons
uv run python -m eval.assistant_eval --arm labels # one prompt at a time; a cut-short run resumes
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
| API | Render web service (`render.yaml`) | Docker; migrations run at start-up; health check `/healthz`; `TP_MFA_KEY` (32 random bytes, URL-safe base64) is required |
| Database | Neon | the non-pooled connection string; `sslmode` is adapted for asyncpg |
| Webhooks | `https://<pages-domain>/api/webhooks/stripe` (and `/mercadopago`) | through the proxy, bytes unchanged |
| Free-tier guard | `TP_MEDIA_QUOTA_BYTES`, `TP_MAX_PHOTOS_PER_USER` | uploads stop at 2 GB / 200 photos per account; photos cached at the edge |
| Commerce settings | `TP_MAX_INSTALLMENTS`, `TP_PAYOUT_HOLD_HOURS` | defaults: 12 installments, payouts 168 hours after delivery |
| AI | `TP_AI_PROVIDER` (`none`, `ollama`), `TP_AI_BASE_URL`, `TP_AI_CHAT_MODEL`, `TP_AI_EMBED_MODEL`, prices and `TP_AI_DAILY_BUDGET_USD` | `none` on the live demo |
| Notifications | `TP_VAPID_PRIVATE_KEY`, `TP_VAPID_SUBJECT`; `TP_EMAIL_PROVIDER` (`none`, `log`, `smtp`), `TP_EMAIL_FROM`, `TP_SMTP_HOST`, `TP_SMTP_PORT`, `TP_SMTP_USERNAME`, `TP_SMTP_PASSWORD` | without a key or a service, that channel is simply not offered |
| Uptime | `.github/workflows/uptime.yml` | every 6 hours; GitHub notifies the owner when it fails |

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
