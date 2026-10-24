# CLI

Everything `/v1/payment` does, plus the one-time platform setup, from a terminal. Run from the repo root — it imports `app` and
reads `.env` from the working directory.

```bash
uv run python -m cli --help
uv run python -m cli <command> --help
```

`--json` is a **root** flag, so it goes before the command. Every payment command takes it and
prints exactly the body the matching `/v1/payment` route returns — real values, never display
strings — so `| jq` works:

```bash
uv run python -m cli --json app:show snapback | jq .plans
uv run python -m cli --json apps --q acme | jq '.apps[].id'
```

## Commands

### Apps

```bash
uv run python -m cli apps                       # id, name, tags, plan count, key count, webhook
uv run python -m cli apps --q acme              # matches the id, the name or a tag
uv run python -m cli app:show snapback          # plans, keys, return hosts, tags, webhook secret set?
uv run python -m cli app:create snapback --name Snapback \
    --return-url https://x.ngrok-free.app/plans \
    --hosts x.ngrok-free.app,localhost --tags acme,eu
uv run python -m cli app:update snapback --free-plan free
uv run python -m cli app:update snapback --hosts new.ngrok-free.app,localhost
uv run python -m cli app:update snapback --tags acme,staging
uv run python -m cli app:update snapback --webhook-secret whsec_...   # write-only, never printed
uv run python -m cli app:delete snapback        # refused while anyone is subscribed
```

| | |
|---|---|
| `app_id` | becomes the consuming app's `PAYMENT_APPLICATION_ID`. **Cannot be changed.** |
| `--hosts` | comma-separated. A success URL on an unlisted host is **silently** replaced by `--return-url` |
| `--return-url` | where a checkout lands when the caller supplies nothing usable. A client with no browser can leave it blank |
| `--tags` | comma-separated labels. Normalised in the service (lowercase, `EU West` → `eu-west`, de-duplicated), so karbon-admin and the CLI write the same tag |
| `--webhook-secret` | this app's own Stripe signing secret. Write-only: stored, shown only as "set" |

### Stripe webhook

```bash
uv run python -m cli webhook:create snapback
```

Creates the Stripe endpoint `{SERVICE_BASE_URL}/webhooks/stripe/snapback`, subscribed to exactly
the events the hub handles (`WEBHOOK_EVENTS` in `src/services/webhook.py`), and stores its signing
secret on `apps/snapback`. Stripe returns the secret only at creation; it is never printed
or returned (`POST /v1/payment/apps/{id}/webhook` does the same and answers `hasWebhookSecret`).
Needed once a second app exists: each endpoint has its own secret, and the optional
`STRIPE_WEBHOOK_SECRET` fallback can match only one. Refused when `SERVICE_BASE_URL` is local or
not https — locally use `stripe listen` and put its `whsec_` in `STRIPE_WEBHOOK_SECRET`.

### Plans

```bash
uv run python -m cli plans snapback
uv run python -m cli plan:save snapback free --name Free
uv run python -m cli plan:save snapback --name "Pro Plus" --amount 7500   # code: pro-plus
uv run python -m cli plan:save snapback pro --name Pro --amount 7500 --trial 7 \
    --features "Unlimited restores|Priority support"
uv run python -m cli plan:save snapback pro --amount 8500                  # a new Stripe price
uv run python -m cli plan:status snapback pro soon
uv run python -m cli plan:icon snapback pro ./pro.png                     # the Stripe product's image
uv run python -m cli plan:save soneeva per-minute --unit minute --amount 10    # $0.10 a minute
uv run python -m cli plan:save soneeva base --unit hour --amount 1000 --status soon
uv run python -m cli plan:save soneeva enterprise --contact
uv run python -m cli plan:delete snapback pro
```

- The code is derived from `--name` when omitted ("Pro Plus" → `pro-plus`), in the service, so
  the API and karbon-admin derive the same one. An explicit code wins. Editing keeps the plan's
  place in the list.
- **`--amount` is minor units per `--unit`** — `7500` is $75.00 a month; `--unit minute
  --amount 10` is $0.10 a minute. `0` (or nothing, on a new plan) is the free tier; omitted on an
  existing plan it keeps the saved amount.
- A plan is a Stripe product (metadata `app`, `code`, `status`, `unit`, `contact`) with its
  default price; nothing is stored in Firestore.
- `--unit minute|hour` is metered: the plan's Stripe billing meter (summing call seconds, one per
  plan) and a metered price that bills per started unit. Units are `UNIT_SECONDS` in `src/models/app.py`.
- Prices are immutable: a different amount, currency, unit or interval makes a new price, sets it as
  the product's default and archives the old one. Existing subscribers keep billing on theirs.
- `--status active|soon|hidden`: `active` is sold, `soon` is listed (site shows "Coming soon"),
  `hidden` is neither. Unset keeps the saved status. `--contact` marks it "on request" (no price).
- `--features` is split on `|` (the product's marketing features); unset keeps them.
- `plan:icon` uploads a png, jpeg or webp to Stripe and makes it the product's image (`icon`).
- `plan:delete` archives the product and its price. Existing subscribers keep billing on it.

### Platform

```bash
uv run python -m cli seed:soneeva                # app soneeva, tools karbon-admin and soneeva-voice; scopes, plans
uv run python -m cli db:prune [--apply]          # unused top-level collections of (default); --apply deletes them for good
uv run python -m cli scopes:sync soneeva ../console/scopes.json   # scopes/soneeva from the console
uv run python -m cli person:save <uid> --role serviceperson --phone +316...   # a serviceperson is a person
uv run python -m cli businesses
uv run python -m cli business:secret <id>        # its voice env into Secret Manager (voice-{id}), if missing
uv run python -m cli business:provision <id>     # finish or retry provisioning; adds new checklist items, keeps progress (idempotent; waits for its indexes)
uv run python -m cli business:delete <id>        # soft-delete the row; database and owner account stay
uv run python -m cli app:update soneeva --scopes hub.business.view,hub.business.create
```

`seed:soneeva` is idempotent. It creates (or resets the `hub.*` scopes of) one app and two internal tools:

| Caller | Is | `hub.*` scopes |
| --- | --- | --- |
| app `soneeva` | the product: plans, Stripe, `scopes/soneeva`; the console signs as it (`soneeva_staging` for staging) | `hub.business.*`, `hub.serviceperson.view`, `hub.voice.update`, `hub.number.*`, `hub.account.view`/`.create`/`.update`/`.delete`/`.recover`, `hub.billing.*`, `hub.handoff.redeem`, `hub.media.view`/`.update` |
| tool `soneeva-voice` | voice services (a key per business, issued into its `voice-{id}` secret; sandbox twins rotate theirs in karbon-admin) | `hub.usage.write`, `hub.plan.view` |
| tool `karbon-admin` | the internal app's server | `hub.people.*`, `hub.payment.*`, `hub.handoff.create`, `hub.account.recover`, `hub.platform.view`, `hub.business.view`/`.update`, `hub.serviceperson.view`, `hub.media.view`/`.update` |

An `apps/karbon-admin` or `apps/soneeva-voice` from before moves to `tools/` with its keys;
`apps/soneeva-console` and `apps/karbon-hub` are deleted, so `apps` holds only apps. It writes `scopes/soneeva`, saves `per-minute` ($0.10/min, active) plus
`base`, `pro`, `premium` and `enterprise` (coming soon) in Stripe live, and in test mode too when
`STRIPE_TEST_SECRET_KEY` is set, and creates each missing platform role with its `platformRoles`
from settings.json (console ids, and `platform.docs.view` for `superadmin`, `Profile.Write` for all).
An existing role is never added to, only cleared of `hub.*` ids, so edits made in Roles stick. Then issue the
UI keys with `key:rotate soneeva` (console) and `tool:key:rotate karbon-admin`.

### Internal tools

```bash
uv run python -m cli tools                           # id, name, status, keys, rotation
uv run python -m cli tool:show karbon-admin
uv run python -m cli tool:create ops-bot --name "Ops bot" --scopes hub.media.view
uv run python -m cli tool:update ops-bot --scopes hub.media.view,hub.media.update
uv run python -m cli tool:delete ops-bot
uv run python -m cli tool:key:rotate karbon-admin    # HUB_APP_ID / HUB_KEY_ID / HUB_PRIVATE_KEY, once
```

A tool (`tools/{id}`) holds hub keys and scopes and is never in Stripe: no plans, webhooks,
checkout or staging twin. Apps and tools share one id space. `soneeva-voice` has a key per business
(`subjectKeys`), so `tool:key:rotate` refuses it: a business's key is issued into its `voice-{id}` secret.

### Staging (Stripe test mode)

Every app `{appId}` also answers as `{appId}_staging` — no document of its own, the same keys. Commands
that take an app id for plans, coupons, `subscription` and `webhook:create` accept it and work in the
Stripe test account (`STRIPE_TEST_SECRET_KEY`): `plan:save soneeva_staging trial --name Trial`,
`webhook:create soneeva_staging` (endpoint `/webhooks/stripe/soneeva_staging`, secret stored as
`stagingWebhookSecret`). `app:show soneeva_staging` shows the test plans; `app:update`, `app:delete`
and `key:rotate` take the plain id. `app:delete` is refused while the app has any subscriber, live or test.
`scopes:sync` writes `scopes/{app}` from the console's `scopes.json` business scopes, the one source of ids.

### Keys

```bash
uv run python -m cli key:rotate snapback
```

Prints the env block once: `PAYMENT_APPLICATION_ID`, `PAYMENT_KEY_ID`, `PAYMENT_PRIVATE_KEY`
with newlines escaped, ready to paste. The console and karbon-admin read the same three values as
`HUB_APP_ID`, `HUB_KEY_ID`, `HUB_PRIVATE_KEY`. `POST /v1/payment/apps/{id}/keys` does the same and
returns `{appId, kid, privateKey}`. **The private half is never stored and never shown
again.** Rotating moves the previous key to `retiring` rather than deleting it, so tokens in
flight keep verifying while you redeploy.

### Coupons

```bash
uv run python -m cli coupons snapback
uv run python -m cli coupon:create snapback                  # 100% off forever, generated 10-char code
uv run python -m cli coupon:create snapback --code LAUNCH99 --max 50
uv run python -m cli coupon:create snapback --code HALFOFF3 --percent 50 --duration repeating --months 3
uv run python -m cli coupon:create snapback --code TENOFF26 --amount 1000 --currency eur \
    --duration once --expires 2026-12-31
uv run python -m cli coupon:delete snapback LAUNCH99
```

| | |
|---|---|
| `--percent` / `--amount` | percent off (default 100), or a fixed amount in **minor units** (`1000` is 10.00) in `--currency` |
| `--duration` | `once`, `repeating` (needs `--months`) or `forever` (default) |
| `--max` / `--expires` | redemption cap and expiry (ISO date) — they ride on the code, not the discount |

Terms that do not fit together (over 100%, months without `repeating`, a fractional amount) are
refused before anything reaches Stripe. At the default — 100% off forever — the customer checks
out normally and ends up with a **real** subscription at zero; nothing downstream knows a coupon
was involved. Needs at least one paid plan first; the products it is scoped to are what stop the
code working on another app's checkout. If Stripe refuses the code, the coupon it had just
created is deleted again rather than left orphaned. Coupons live only in Stripe (coupon +
promotion code, metadata `app`); `coupon:delete` deactivates the code and deletes its coupon.

### Inspection

```bash
uv run python -m cli subscription snapback <tenant>
uv run python -m cli customers                       # businesses with their subscriptions
uv run python -m cli customer:show <business_id>
uv run python -m cli customer:invoices <business_id>
uv run python -m cli customer:usage <business_id>     # seconds per day from the Stripe meter
```

Entitlements exactly as the consuming app receives them from
`GET /v1/apps/{id}/entitlements`. The first thing to run when an app shows the wrong plan —
it says whether the service is wrong or the app's own cache is stale.

### People and roles

```bash
uv run python -m cli people
uv run python -m cli person:create dev@soneeva.com --role sales --apps soneeva   # prints a password-set link
uv run python -m cli person:save <uid> --role serviceperson --apps snapback
uv run python -m cli person:save <uid> --role sales --disabled    # locks them out immediately
uv run python -m cli person:save <uid> --role sales --apps soneeva --businesses abc123,def456
uv run python -m cli person:delete <uid>

uv run python -m cli roles
uv run python -m cli role:show superadmin
uv run python -m cli role:save auditor --scopes Users.View,payment.app.view,payment.plan.view
uv run python -m cli role:save owner --scopes all       # * plus every payment and platform scope
uv run python -m cli role:delete auditor                # refused while anyone holds it
uv run python -m cli scopes                             # what a role can hold
```

The same records as karbon-admin › People and Roles (`/v1/people`, `/v1/roles`).
`person:create` never sets a password — the link is the credential. `--disabled` takes effect on
the next request, because the user row is read every time.

## Agents

An agent working inside this repo uses these commands directly (karbon-admin › Let your agent do
it has the prompt). One with only HTTP gets an operator token from that page instead and calls
`/v1/agent/*` — the same service methods as these commands and `/v1/payment`, with the person's
current `payment.*` scopes re-checked on every call; its schema is `/v1/agent/openapi.json`.

## Two things to understand

**It is not a second way to configure anything.** Every command calls the same service method
the matching `/v1/payment` route calls. That is why `PlanService.define` exists instead of
the Stripe price logic living in the route handler — a command whose behaviour is not in a
service is a fork waiting to diverge. Put the behaviour in a service first, then expose it.

**No scope checks, deliberately.** Running this requires `GOOGLE_APPLICATION_CREDENTIALS`,
which already grants full Firestore access, so a scope check here would be theatre. Scopes
constrain people acting through the API — a different threat.

## Exit codes

`0` on success. A refusal you can act on ("An app called 'snapback' already exists.") prints
one line and exits non-zero with no traceback, so it is safe in a script:

```bash
uv run python -m cli app:create snapback || echo "already there, moving on"
```
