# Payment Service

One FastAPI service that owns Stripe for all of our apps — web, mobile, desktop, a platform
extension, anything that can sign a JWT and make an HTTPS call. An app sends its customer
here to pay, then reads `/entitlements` to know what that customer is allowed to do.

```
Your app ──redirect ?token=──▶ /billing/{app_id} ──▶ Stripe Checkout
                                        ▲                        │
Your app ◀──redirect back──── /billing/{app_id}/return ◀─────────┘
Your app ──x-api-key──▶ /v1/apps/{app_id}/entitlements        Stripe ──▶ /webhooks/stripe/{app_id}
```

Nothing here is tied to one platform or one language. An app is a row in Firestore with a
public key and some plans; a **tenant** is whatever that app bills — a store, a workspace, an
organisation, a single user.

## Authentication

Every call carries a **per-application Ed25519 (EdDSA) JWT**. The app holds the private key;
this service keeps only the public half, in Firestore at `applications/{id}.keys[]`. There is
no shared secret, so one app's key cannot sign for another.

| | |
| --- | --- |
| header | `alg: EdDSA`, `kid` — the key id issued with the keypair |
| `iss` | the app id. **Must equal `{app_id}` in the path**, or the call is rejected |
| `sub` | the tenant — whatever your app bills: store, workspace, org, user id. From your session, never a request body |
| `aud` | `payment-service` for the JSON API, `payment-service-hosted` for `?token=` redirects |
| `jti` | fresh per request — a replayed id is refused |
| `iat` / `exp` | both required, ≤60s TTL, 30s leeway |

- JSON API: send it as the `x-api-key` header.
- Hosted pages: browsers cannot set headers on a redirect, so `/billing/...` takes the same
  token as `?token=` — with the hosted audience, so the two are not interchangeable.

Issue a keypair with `uv run python -m cli key:rotate <app_id>`, which prints the private half
once. See [CLI.md](CLI.md).

Public endpoints: `/health`, `/docs`, `/openapi.json`, `/webhooks/*`, and the
`/billing/{app_id}/return` and `/cancel` landing pages (Stripe calls those; they carry no token).

## Endpoints

### Hosted flow (browser redirects)

| Method | Path | Params | Purpose |
| --- | --- | --- | --- |
| GET | `/billing/{app_id}` | `token`, `plan?`, `redirect?` | Plan picker. With `plan`, skips straight to Stripe. |
| GET | `/billing/{app_id}/checkout` | `token`, `plan`, `redirect?` | Creates the session, 303s to Stripe. |
| GET | `/billing/{app_id}/return` | `session_id`, `redirect?` | Syncs the subscription, then 303s to the app with `?billing=success&plan=…`. |
| GET | `/billing/{app_id}/cancel` | `redirect?` | Customer backed out — 303s back with `?billing=cancelled`. |
| GET | `/billing/{app_id}/portal` | `token`, `redirect?` | 303s to the Stripe billing portal. |

`redirect` is only honoured for hosts listed in the app's config; anything else falls back to
`return_url`, so the endpoints cannot be used as an open redirect.

### JSON API

| Method | Path | Body / query | Purpose |
| --- | --- | --- | --- |
| GET | `/v1/apps/{app_id}/subscription` | — | Full billing state: status, plan, period, trial, cancellation. |
| GET | `/v1/apps/{app_id}/entitlements` | — | Small cacheable gate check — poll this. |
| GET | `/v1/apps/{app_id}/plans` | — | Configured plans merged with live Stripe prices. |
| POST | `/v1/apps/{app_id}/checkout/session` | `plan_code`, `success_url?`, `cancel_url?`, `promo_code?`, `quantity?`, `trial_days?` | Returns the Stripe URL to redirect to. |
| POST | `/v1/apps/{app_id}/billing/portal` | `return_url?` | Returns the portal URL. |
| POST | `/v1/apps/{app_id}/subscription/change` | `plan_code`, `proration_behavior?` | Upgrade/downgrade in place. |
| POST | `/v1/apps/{app_id}/subscription/cancel` | `at_period_end` | Cancel now or at period end. |
| POST | `/v1/apps/{app_id}/subscription/resume` | — | Undo a pending cancellation. |
| POST | `/v1/apps/{app_id}/subscription/sync` | — | Re-read Stripe. Recovery hatch for a missed webhook. |
| GET | `/v1/apps/{app_id}/invoices` | `limit`, `starting_after` | Billing history with hosted URL + PDF. |
| GET | `/v1/apps/{app_id}/payment/method` | — | Card on file (brand, last4, expiry). |
| POST | `/webhooks/stripe/{app_id}` | Stripe payload | Signature-verified, idempotent on event id. |
| GET | `/health` | — | Status plus the configured app ids. |

Docs are at `/docs`, schema at `/openapi.json`.

## Adding an app

One way only: the dashboard at `/admin`, or the CLI. There is no config file to edit and no
seed script — the registry reads Firestore.

```bash
uv run python -m cli app:create myapp --name "My App" \
    --return-url https://myapp.com/billing --hosts myapp.com,localhost
uv run python -m cli plan:save myapp free --name Free
uv run python -m cli plan:save myapp pro  --name Pro --amount 2900 --trial 14
uv run python -m cli app:update myapp --free-plan free
uv run python -m cli key:rotate myapp          # env block for the app, shown once
```

Prices, currency and interval always come from Stripe at read time, so they cannot drift.
`features` and `limits` are yours — they are what `/entitlements` hands back to the app.

Full reference: [CLI.md](CLI.md). Screens and roles: [docs/admin-dashboard.md](docs/admin-dashboard.md).

## Calling it

Any language with an Ed25519 JWT library works; the six claims above are the whole contract.
TypeScript, since that is what most of our apps are:

```ts
import { SignJWT } from "jose";

const key = await importPKCS8(process.env.PAYMENT_PRIVATE_KEY!, "EdDSA");
const token = await new SignJWT({})
  .setProtectedHeader({ alg: "EdDSA", kid: process.env.PAYMENT_KEY_ID! })
  .setIssuer(process.env.PAYMENT_APPLICATION_ID!)   // must match the app id in the path
  .setAudience("payment-service")
  .setSubject(session.storeHash)                    // from the session, never the request body
  .setJti(crypto.randomUUID())
  .setIssuedAt()
  .setExpirationTime("60s")
  .sign(key);

const app = process.env.PAYMENT_APPLICATION_ID;
const res = await fetch(`${PAYMENT_URL}/v1/apps/${app}/entitlements`, {
  headers: { "x-api-key": token },
});
const { plan_code, active, limits } = await res.json();   // snake_case — not planCode
```

The response is **snake_case**: `plan_code`, `current_period_end`, `in_grace_period`. Reading
the camelCase names gives `undefined` with no error, so the plan looks permanently missing.

A client with no browser — mobile, desktop, CLI — skips the hosted redirect pages and uses
the JSON API: `POST /checkout/session` returns a Stripe URL to open in a system browser or
web view, and `GET /entitlements` is the gate check afterwards.

A working implementation is in `snapback/lib/payment/`. Testing it end to end:
[docs/local-testing.md](docs/local-testing.md).

## Structure

```
app/
  config.py          Settings (.env) and the app registry
  dependencies.py    DI: wiring, scopes, admin sessions
  templating.py      One Jinja environment
  utils.py           get_path plus URL builders
  models/            Pydantic types only
  routes/
    admin/           Dashboard screens, one module per group
    billing.py       Hosted redirects
    subscription.py  JSON API
    webhook.py       Stripe events
  services/
    admin.py         App, plan, user and role operations
    billing.py       All billing rules
    coupons.py       100%-off promotion codes
    roles.py         Role to scopes, cached
    session.py       Shared HS256 dashboard session
    webhook.py       Event dispatch + idempotency
    auth/            People. base.py contract, firebase.py, exported as Auth
    tokens/          Machines. Ed25519 keys, verification, replay guard
    db/              base.py contract, firestore.py implementation, exported as DB
    payment/         base.py contract, stripe.py implementation, exported as Payment
cli/                 The dashboard's operations, from a terminal
```

`db/` and `payment/` each expose an abstract base plus one implementation, re-exported from
`__init__.py` as `DB` and `Payment`. Swapping Firestore or Stripe means adding a file and changing
that one import.

## Running

```bash
uv sync
cp .env.example .env      # Stripe keys, SESSION_SECRET, the Firebase pair
uv run python main.py          # or: uv run uvicorn main:app --reload --port 8080
```

Firestore uses application default credentials — set `GOOGLE_APPLICATION_CREDENTIALS` locally, or
nothing at all on GCP.

Stripe webhooks locally:

```bash
stripe listen --forward-to localhost:8080/webhooks/stripe/commerce-manager
```

## Type checking & linting

Type checking runs via [pyright](https://microsoft.github.io/pyright/) in `strict` mode
(see [pyrightconfig.json](pyrightconfig.json)):

```bash
uv run pyright
```

Linting runs via [ruff](https://docs.astral.sh/ruff/):

```bash
uv run ruff check .
```

### Pre-push hook

A pre-push hook runs `ruff check` before every `git push` and blocks the push if it fails. The hook
lives in [scripts/hooks/pre-push](scripts/hooks/pre-push) (tracked in git, unlike `.git/hooks/`) and
is wired up per-clone with:

```bash
git config core.hooksPath scripts/hooks
```

pyright is not part of the hook — the codebase isn't clean under strict mode yet, so gating pushes
on it today would block everything. Run `uv run pyright` manually, and once the existing findings
are cleared, add `uv run pyright` to `scripts/hooks/pre-push` to enforce it on every push too.

## Deploying to Cloud Run

```bash
gcloud run deploy payment-service \
  --source . --region us-central1 --allow-unauthenticated \
  --set-env-vars SERVICE_BASE_URL=https://payment-service-xxx.run.app,FIREBASE_API_KEY=…,FIREBASE_AUTH_DOMAIN=… \
  --set-secrets SESSION_SECRET=session-secret:latest,STRIPE_SECRET_KEY=stripe-key:latest,STRIPE_WEBHOOK_SECRET=stripe-webhook:latest
```

The container listens on `$PORT` (8080 by default) and runs as a non-root user. Give the service
account `roles/datastore.user` for Firestore and `roles/firebaseauth.admin` for the dashboard's
accounts, and point one Stripe webhook endpoint per app at `/webhooks/stripe/{app_id}`.

Caches are per-instance with a short TTL, so an edit on one container reaches the others within
a minute — see [docs/admin-dashboard.md](docs/admin-dashboard.md).
