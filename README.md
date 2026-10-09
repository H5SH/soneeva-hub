# Payment Service

One FastAPI service that owns Stripe for every BigCommerce app. Each Next app redirects the merchant
here to pay, then polls `/entitlements` to know what that store is allowed to do.

```
Next app ──redirect ?token=──▶ /billing/{app_id} ──▶ Stripe Checkout
                                        ▲                        │
Next app ◀──redirect back──── /billing/{app_id}/return ◀─────────┘
Next app ──x-api-key──▶ /v1/apps/{app_id}/entitlements        Stripe ──▶ /webhooks/stripe/{app_id}
```

## Authentication

Every call carries a JWT signed with `API_SECRET` (HS256, `exp` required) whose claims are
`store_hash`, `access_token` and `plan`.

- JSON API: send it as the `x-api-key` header.
- Hosted pages: browsers cannot set headers on a redirect, so `/billing/...` accepts the same token
  as `?token=`. Issue those with a short TTL (~5 minutes).

`app_id` lives in the path, never in the token, so one token cannot be replayed across apps. The
`plan` claim is informational — this service always answers from Stripe and its own store.

Public endpoints: `/health`, `/docs`, `/openapi.json`, `/webhooks/*`, and the
`/billing/{app_id}/return` and `/cancel` landing pages (Stripe calls those; they carry no token).

## Endpoints

### Hosted flow (browser redirects)

| Method | Path | Params | Purpose |
| --- | --- | --- | --- |
| GET | `/billing/{app_id}/start` | `token`, `plan?`, `redirect?` | Plan picker. With `plan`, skips straight to Stripe. |
| GET | `/billing/{app_id}/checkout` | `token`, `plan`, `redirect?` | Creates the session, 303s to Stripe. |
| GET | `/billing/{app_id}/return` | `session_id`, `redirect?` | Syncs the subscription, then 303s to the app with `?billing=success&plan=…`. |
| GET | `/billing/{app_id}/cancel` | `redirect?` | Merchant backed out — 303s back with `?billing=cancelled`. |
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

Drop a JSON file in [apps/](apps/) named after the `app_id` — see
[apps/commerce-manager.json](apps/commerce-manager.json). Files are read on first use and cached.

```json
{
  "name": "My App",
  "return_url": "https://myapp.com/billing",
  "allowed_return_hosts": ["localhost"],
  "free_plan_code": "free",
  "plans": [
    { "code": "free", "name": "Free", "features": ["…"], "limits": { "products": 50 } },
    { "code": "pro", "name": "Pro", "price_id": "price_123", "trial_days": 14,
      "features": ["…"], "limits": { "products": 100000 } }
  ]
}
```

Prices, currency and interval always come from Stripe at read time, so they cannot drift. `features`
and `limits` are yours — they are what `/entitlements` hands back to the app.

## Calling it from a Next app

```ts
const token = jwt.sign(
  { store_hash: session.storeHash, access_token: session.accessToken, plan: session.plan },
  process.env.PAYMENT_API_SECRET!,
  { expiresIn: "5m" },
);

// Gate a feature
const res = await fetch(`${PAYMENT_URL}/v1/apps/commerce-manager/entitlements`, {
  headers: { "x-api-key": token },
});
const { active, limits } = await res.json();

// Send the merchant to pay
redirect(`${PAYMENT_URL}/billing/commerce-manager/start?token=${token}`);
```

## Structure

```
app/
  config.py          Settings (.env) and the app registry
  dependencies.py    DI: app config, auth context, services
  utils.py           get_path plus URL builders
  middleware/auth.py JWT verification for every non-public route
  models/            Pydantic types only
  routes/            HTTP layer, one file per concern
  services/
    billing.py       All billing rules
    webhook.py       Event dispatch + idempotency
    db/              base.py contract, firestore.py implementation, exported as DB
    payment/         base.py contract, stripe.py implementation, exported as Payment
apps/                One JSON file per BigCommerce app
```

`db/` and `payment/` each expose an abstract base plus one implementation, re-exported from
`__init__.py` as `DB` and `Payment`. Swapping Firestore or Stripe means adding a file and changing
that one import.

## Running

```bash
uv sync
cp .env.example .env      # fill in API_SECRET and the Stripe keys
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
  --set-env-vars SERVICE_BASE_URL=https://payment-service-xxx.run.app \
  --set-secrets API_SECRET=api-secret:latest,STRIPE_SECRET_KEY=stripe-key:latest,STRIPE_WEBHOOK_SECRET=stripe-webhook:latest
```

The container listens on `$PORT` (8080 by default) and runs as a non-root user. Give the service
account the `roles/datastore.user` role for Firestore, and point one Stripe webhook endpoint per app
at `/webhooks/stripe/{app_id}`.
