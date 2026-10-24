# karbon-hub

The central service for Soneeva and every app it bills (`soneeva`, `honeybook`, …): auth admin, scopes,
plans and payments (Stripe), businesses and their databases, private files (avatars, icons, logos),
servicepersons, voice deployments and Twilio numbers. Its four resources are **auth** (Firebase Auth
admin), **db** (Firestore), **storage** (the Firebase Storage bucket) and **payment** (Stripe).
The contract with the other services is [`../admin/content/platform/`](../admin/content/platform/) (karbon-admin `/platform`); this repo implements the karbon-hub side of it.

It grew out of the payment service, so the billing API below is unchanged: an app sends its
customer here to pay, then reads `/entitlements` to know what that customer may do.

It is a REST API only: no pages, sessions, cookies or CORS. Its callers are **apps** — products that
sell, with Stripe plans (`soneeva`; the console signs as it) — and **internal tools**, which hold hub
keys but never touch Stripe (`karbon-admin`, and `soneeva-voice` with a key per business), plus
Stripe. Each UI calls it from its own Next server only.

Apps live in `apps/{id}`, tools in `tools/{id}` (one id space; [src/services/tools.py](src/services/tools.py)),
so a tool never shows up in payment listings, public plans, webhooks or checkout.

**Staging.** Every app also answers as `{appId}_staging`: no document of its own, the same keys. The
hub strips `_staging` from the caller's `iss` and from app ids in paths and webhooks and runs that
request in Stripe **test mode** (`STRIPE_TEST_SECRET_KEY`; off → 503). Every Stripe-backed service
(gateway, plans and tenant caches, billing, coupons, webhooks) exists once per mode
([src/services/modes.py](src/services/modes.py)), so live and test never mix. A business signed up by
a staging caller is stored `test: true` and everything about it runs in test mode whoever calls (the
console, its voice, karbon-admin); a live business never becomes test, and a staging caller gets 403
on one. Tools have no twin.

## API

Every `/v1` call (except `/v1/public/plans`) carries `Authorization: Bearer <jwt>` — EdDSA,
`iss` = app id, `sub` = businessId or `-`, `aud` = `karbon-hub`, `kid`, `jti`, `exp - iat` ≤ 60 s,
and optionally `uid`, the person acting, whose role and scopes are re-read on every call. The full
table (method, path, app scope, person scope, body → response) and the serviceperson handoff
(one-time codes) are in [`../admin/content/platform/api.ts`](../admin/content/platform/api.ts) (karbon-admin `/platform#api`); example JSON for every endpoint
is [tests/contract/hub.json](tests/contract/hub.json).

| Area | Paths | Caller |
| --- | --- | --- |
| Public | `/health`, `/v1/public/plans?app=`, `/v1/agent/openapi.json`, `GET /recover` (the sign-in form), `GET /` (a Soneeva page) | anyone (`tests/test_exposure.py` keeps the list) |
| Stripe | `POST /webhooks/stripe/{app_id}` | Stripe (signature) |
| Businesses, branches, numbers, voice, servicepersons | `/v1/businesses…` (`?assigned=me`, `/{id}/branches`), `/v1/servicepersons` (people whose role is `serviceperson`), `/v1/platform/home` | console, karbon-admin |
| Billing | `/v1/businesses/{id}/plans`, `/checkout`, `/portal` | console, business sub, `billing.view` |
| Business members and own account | `/v1/accounts…` (business token only: create, read, edit, delete its members; a member: their own name and password) | console |
| Platform people and roles | `/v1/people…` (list, get, create, `PATCH`, `DELETE`, `/password-reset`, `/tokens`, `/servicepersons`), `/v1/roles…` (list with catalogue and holders, create, `PUT`, `DELETE`), `/v1/me` | karbon-admin, `hub.people.view`/`hub.people.update` + the person's `Users.*`/`Roles.*` |
| Advertisements | `/v1/advertisements…` (list, get, create, `PATCH`, `DELETE`, `/{id}/publish`, `DELETE /{id}/publish/{businessId}`): master in `(default)`, a copy in each business it is published to | karbon-admin, `hub.advertisement.view|update` + the person's `Advertise.View|Write` |
| Demo calls and scope ids | `/v1/platform/calls…` (`/totals`, `/daily`), `/v1/scopes/{appId}` | karbon-admin, console |
| Password reset request | `POST /v1/accounts/password-reset-request` (always 202) | console, karbon-admin |
| Caller names, tags and blocks | `GET /v1/businesses/{id}/callers?q=&tag=&blocked=&limit=&before=`, `PATCH …/callers/{e164}` (`name`, `tags`, `blocked`, `blockedReason`), `POST …/callers` (block a number that never called) (store: [src/services/callers.py](src/services/callers.py)) | karbon-admin (the console writes `b-{id}` directly) |
| Call menus and recorded messages | `GET /v1/businesses/{id}/ivr/numbers`, `PUT …/ivr/numbers/{e164}`, `GET`/`PUT`/`DELETE …/audios/{name}`, `DELETE …/audios/{name}/versions/{n}` | console (karbon-admin reads) |
| Media | `GET`/`PUT`/`DELETE /v1/media/{path}` | console, karbon-admin |
| Usage and plan status | `POST /v1/usage`, `GET /v1/businesses/{id}/plan` (`{active, status, plan, periodEnd, test}`; the voice re-checks on its own timer, the hub never calls a voice) | soneeva-voice |
| Payment admin | `/v1/payment/apps…`, `/v1/payment/businesses…` | karbon-admin, `payment.*` |
| Operator tokens | `POST`/`DELETE /v1/agent-tokens` (`{minutes}` 15–120 → `{token, expiresAt}` once) | karbon-admin, the person's role holds `Agent.Use` |
| Agent API | `/v1/agent/whoami`, `/v1/agent/apps…` (create, update, plans, webhook, keys `?format=env`, coupons, `tenants/{tenant}`); schema at `/v1/agent/openapi.json` (public) | an operator's agent, `Authorization: Bearer kat_…` |
| Handoff | `POST /v1/handoff` / `POST /v1/handoff/redeem` | karbon-admin (`Businesses.Access` + a running grant) / the console |
| Generic billing | `/v1/apps/{app_id}/…` (`x-api-key`, aud `payment-service`) | other apps (below) |

`/docs` and `/openapi.json` are served only with `ENVIRONMENT=development`.

`/` is a one-screen Soneeva page: a link to soneeva.com, or "Open key recovery" for someone signed in to `/recover`.

Recovery: `/recover` is the main HTML page. When karbon-admin is down, a platform person whose role
holds `Tools.Write` (`*` has it) signs in with email + password and rotates a hand-rotated internal
tool's key (karbon-admin; `soneeva-voice` rotates on redeploy; apps rotate in karbon-admin), audited
as `key.rotate`; the key is shown once. A 15-minute cookie
signed with `AUTH_SECRET` (karbon-admin's value), CSRF on every form, sign-in rate limited. Off
until `AUTH_SECRET` and `FIREBASE_API_KEY` are set. 2FA comes later. Someone signed in to karbon-admin
skips the sign-in: `src/auth/admin_session.py` reads its Auth.js cookie with the same `AUTH_SECRET`.

Files live at fixed paths in the private bucket `{project}.firebasestorage.app`, without an
extension (the type is metadata); no URL is stored anywhere, the path is the address:
`users/{uid}/avatar`, `apps/{appId}/avatar`, `businesses/{id}/avatar`,
`businesses/{id}/branches/{branchId}/avatar`, and everything else of a business under
`businesses/{id}/…`. `/v1/media/{path}` serves them (ETag, 304) after the checks in
[src/services/media.py](src/services/media.py); [storage.rules](storage.rules) keeps every client out.

A platform person is checked on their role's console ids (`Businesses.View|Write|Assign|Access|Delete`,
`Servicepersons.View`, `Users.*`, `Roles.*`, `Chats.View`; `*` covers them all), never on a `hub.*` id:
`hub.*` is only what the calling app may do (the table is `PERSON_SCOPES` in
[src/services/tokens/caller.py](src/services/tokens/caller.py)).
`Businesses.View` (or `.Assign`) reaches every business; `Businesses.Access` alone reaches only the
businesses assigned to the person, and opens their details (branches, checklist, callers, call menus;
`reads_business` in [src/services/people/rules.py](src/services/people/rules.py)). Changes still need
their write scopes.

**Pause and delete** are business statuses set with `PATCH /v1/businesses/{id}`: `onhold` (pause;
back to `running`/`pending` to resume) needs `Businesses.Write`, `deleted` (and restoring from it)
needs `Businesses.Delete` (superadmin via `*`) and is audited as `business.delete`; a business's own
token never sets or leaves `deleted`. `GET /v1/businesses` hides `deleted` unless `?status=deleted`,
and the home page leaves them out (payment customers still list them while billed). Nothing else changes: the database, voice and numbers stay.

An app's `hub.*` scopes live on `apps/{id}.scopes`, a tool's on `tools/{id}.scopes`
(`cli app:update --scopes`, `tool:update --scopes`; `seed:soneeva` sets them for Soneeva). Internal tools are managed only
by the CLI (`tool:*`) and `/recover`; there is no HTTP API for them.

**People.** One record per platform person, `users/{uid}`: `role`, `apps` (payment apps they act
on), `businesses` (empty = every business), `tokens`, `disabled`, plus who looks after them
(`assignments`). One role (`roles/{id}`) holds any mix of console ids (`Users.View`, `*` = every one)
and `payment.*`/`platform.*` scopes; never `hub.*` (an old `hub.*` id on a stored role is ignored and
drops off on the next save). `GET /v1/roles` returns the catalogue a role picks from. Nobody gives a
role, scope or app they don't hold, changes their own role, apps, businesses or disabled flag, or
deletes a role someone holds (409). Nothing here deploys soneeva-voice: `checkout.session.completed`
for `soneeva` writes that business's voice env to Secret Manager as `voice-{businessId}` (its own
`soneeva-voice` key, named on `businesses/{id}.voiceKeyId`; only the public half stays here), and its
first number adds the Twilio subaccount ([src/services/voice.py](src/services/voice.py)). A developer
deploys the voice by hand with that secret mounted (plus `MCP_URL`, `WHATSAPP_FROM` of their own) and a
platform person holding `Voice.Url.Write` saves its URL (`PATCH /v1/businesses/{id}` `voiceUrl`: it must
answer `/health`; every number is pointed at it). The voice follows its settings, menus and recordings
in the business's data itself. `Voice.Sandbox` opens a business's sandbox twin (`POST …/sandbox`; a test
business of its own, never listed) and rotates its key (`POST …/sandbox/key`: a local `.env`, shown once).
Every onboarding checklist starts with `assigned`, `meeting`, `subscribed`, `mcp`, `deployment`; the hub
keeps `assigned` and `subscribed` in step, the serviceperson ticks `meeting` and `deployment`
(`POST …/checklist/{item}`, [src/services/checklist.py](src/services/checklist.py)).
Decisions: [settings.json](settings.json), explained in [SETTINGS.md](SETTINGS.md). Rollout:
[ROLLOUT.md](ROLLOUT.md). Local end to end: [scripts/e2e.md](scripts/e2e.md).

## Generic billing API (`/v1/apps/{app_id}`)

```
Your server ──x-api-key──▶ POST /v1/apps/{app_id}/checkout/session ──▶ {url} ──▶ Stripe Checkout
Your server ──x-api-key──▶ GET  /v1/apps/{app_id}/entitlements          Stripe ──▶ /webhooks/stripe/{app_id}
```

Nothing here is tied to one platform or one language. An app is a row in Firestore with a
public key and some plans; a **tenant** is whatever that app bills — a store, a workspace, an
organisation, a single user.

### Authentication

Every call carries a **per-application Ed25519 (EdDSA) JWT**. The app holds the private key;
this service keeps only the public half, in Firestore at `apps/{id}.keys[]`. There is
no shared secret, so one app's key cannot sign for another.

| | |
| --- | --- |
| header | `alg: EdDSA`, `kid` — the key id issued with the keypair |
| `iss` | the app id. **Must equal `{app_id}` in the path**, or the call is rejected |
| `sub` | the tenant — whatever your app bills: store, workspace, org, user id. From your session, never a request body |
| `aud` | `payment-service` |
| `jti` | fresh per request — a replayed id is refused |
| `iat` / `exp` | both required, ≤60s TTL, 30s leeway |

Send it as the `x-api-key` header.

Issue a keypair with `uv run python -m cli key:rotate <app_id>`, which prints the private half
once. See [CLI.md](CLI.md).

### Endpoints

| Method | Path | Body / query | Purpose |
| --- | --- | --- | --- |
| GET | `/v1/apps/{app_id}/subscription` | — | Full billing state: status, plan, period, trial, cancellation. |
| GET | `/v1/apps/{app_id}/entitlements` | — | Small cacheable gate check — poll this. |
| GET | `/v1/apps/{app_id}/plans` | — | The app's plans: Stripe products with their default prices. |
| POST | `/v1/apps/{app_id}/checkout/session` | `plan_code`, `success_url?`, `cancel_url?`, `promo_code?`, `quantity?`, `trial_days?` | Returns the Stripe URL. Stripe returns to `success_url`/`cancel_url` (allowed hosts only, else `return_url`). |
| POST | `/v1/apps/{app_id}/billing/portal` | `return_url?` | Returns the portal URL. |
| POST | `/v1/apps/{app_id}/subscription/change` | `plan_code`, `proration_behavior?` | Upgrade/downgrade in place. |
| POST | `/v1/apps/{app_id}/subscription/cancel` | `at_period_end` | Cancel now or at period end. |
| POST | `/v1/apps/{app_id}/subscription/resume` | — | Undo a pending cancellation. |
| POST | `/v1/apps/{app_id}/subscription/sync` | — | Re-read Stripe now instead of after the short cache. |
| GET | `/v1/apps/{app_id}/invoices` | `limit`, `starting_after` | Billing history with hosted URL + PDF. |
| GET | `/v1/apps/{app_id}/payment/method` | — | Card on file (brand, last4, expiry). |
| POST | `/webhooks/stripe/{app_id}` | Stripe payload | Signature-verified; drops cached plans or the tenant's subscription; once per event id. |
| GET | `/health` | — | `{"status":"ok"}` and nothing else: it is public, so it names no apps. |

### Adding an app

Through karbon-admin (`/v1/payment/apps`) or the CLI. There is no config file to edit — the
registry reads Firestore.

```bash
uv run python -m cli app:create myapp --name "My App" \
    --return-url https://myapp.com/billing --hosts myapp.com,localhost
uv run python -m cli plan:save myapp free --name Free
uv run python -m cli plan:save myapp pro  --name Pro --amount 2900 --trial 14
uv run python -m cli webhook:create myapp     # its own Stripe endpoint; the secret is stored, never shown
uv run python -m cli app:update myapp --free-plan free
uv run python -m cli key:rotate myapp          # env block for the app, shown once
```

A plan is a Stripe product and its default price; nothing of it is stored here, so it cannot drift.
A different amount later is a new price (the old one is archived; subscribers keep it). `features`
are the product's marketing features, which `/entitlements` hands back to the app.

Full reference: [CLI.md](CLI.md).

### Calling it

Any language with an Ed25519 JWT library works; the six claims above are the whole contract.
TypeScript, since that is what most of our apps are:

```ts
import { SignJWT } from "jose";

const key = await importPKCS8(process.env.PAYMENT_PRIVATE_KEY!, "EdDSA");
const token = await new SignJWT({})
  .setProtectedHeader({ alg: "EdDSA", kid: process.env.PAYMENT_KEY_ID! })
  .setIssuer(process.env.PAYMENT_APPLICATION_ID!)   // must match the app id in the path
  .setAudience("payment-service")
  .setSubject(session.tenantId)                     // from the session, never the request body
  .setJti(crypto.randomUUID())
  .setIssuedAt()
  .setExpirationTime("60s")
  .sign(key);

const app = process.env.PAYMENT_APPLICATION_ID;
const res = await fetch(`${PAYMENT_URL}/v1/apps/${app}/entitlements`, {
  headers: { "x-api-key": token },
});
const { plan_code, active, features } = await res.json();   // snake_case — not planCode
```

The response is **snake_case**: `plan_code`, `current_period_end`, `in_grace_period`. Reading
the camelCase names gives `undefined` with no error, so the plan looks permanently missing.

`POST /checkout/session` returns a Stripe URL to open in a browser or web view, and
`GET /entitlements` is the gate check afterwards.

A working implementation is in `snapback/lib/payment/`. Testing it end to end:
[docs/local-testing.md](docs/local-testing.md).

## Structure

```
settings.json        Decisions and tunables (SETTINGS.md)
scopes.json          What karbon-hub's scopes mean, en/nl/de
src/
  config.py          Settings (.env) and the app registry
  tunables.py        settings.json reader
  wiring.py          Builds every service; shared by main.py and the CLI
  dependencies.py    DI: service getters
  utils.py           get_path
  models/            Pydantic types only (staff.py: /v1/payment bodies and camelCase views)
    db.py            THE registry: Default and BusinessDb collections, doc ids, camelCase (to_doc, STORED)
    paths.py         THE registry of storage paths, and the patterns media.py checks
  routes/
    payment/         /v1/me and /v1/payment: karbon-admin
    platform/        /v1 for the console and voice: businesses, billing, accounts, data, usage, handoff
    subscription.py  Generic billing API (/v1/apps/{app_id})
    checkout.py      Its checkout and portal URLs
    webhook.py       Stripe events
    recover.py       /recover: break-glass key rotation (templates/recover.html)
  db/                db — base.py: the one Database contract (documents, queries, rows, admin ops);
                     indexes.py: the indexes business data needs (the one list, provider-neutral);
                     place.py: where a business's data is, from its dbId (nested or its own database);
                     firestore.py: the only Firestore code; fake.py; exported as DB
  auth/              auth — Firebase Auth admin: base.py, firebase.py, password.py, fake.py, exported as Accounts
  storage/           storage — the private bucket: base.py, firebase.py, fake.py, exported as Storage
  payment/           payment — base.py, fake.py, stripe/ (gateway.py, catalog.py, parse.py), exported as Payment
  vault/             Secret Manager (voice env per business): gcp.py, fake.py
  numbers/           Phone numbers: twilio.py (REST via httpx), fake.py
  remote/            Unsigned readiness check of a voice URL before it is saved: restapi.py, fake.py
  services/          Business logic over a Database; unchanged when a provider changes
    errors.py        AdminError (operator refusals) and PlatformError (/v1 refusals)
    admin.py         Apps, keys, payment people and roles
    tools.py         Internal tools: keys and scopes, no Stripe
    modes.py         ByMode: one instance per Stripe mode (live, test)
    billing.py       All billing rules; tenant → Stripe customer and subscription, briefly cached
    coupons.py       Promotion codes with discount terms (models/discount.py)
    roles.py         Role to scopes, cached
    webhook.py       Stripe events: drop caches, then side effects (checklist, voice env)
    plans.py         Plans as Stripe products and prices, one cache for the account, the public list
    ttl.py           The per-instance expiring cache behind plans, tenants and event dedupe
    businesses.py    Sign-up: owner account + pending business row, idempotent per owner/email
    provision.py     Background provisioning of b-{id} (database, indexes, seed rows, claims; done once
                     the indexes are built)
    callers.py       Caller names, tags and blocks in b-{id}/callers
    voice.py         soneeva-voice env in Secret Manager, its URL and the numbers pointed at it
    sandbox.py       A business's sandbox twin and its rotating local key
    usage.py         Call seconds straight to the Stripe meter (identifier = callId)
    phone_numbers.py Twilio numbers, gated on a plan
    customers.py     Per-business Stripe detail for /v1/payment/businesses
    handoff.py       One-time serviceperson handoff codes: issue and redeem
    media.py         Which person may see or change each media path (one table)
    ivr.py           Per-number call menus (numbers/{e164}) and recordings (audios/{name}, v{n} archive)
    rate_limit.py    Per-key sliding window (password reset requests)
    people/          store.py: users, roles, assignments in (default); access.py: who is acting;
                     service.py: /v1/people rules; roles.py: /v1/roles; members.py: /v1/accounts
                     (business members); rules.py: scope rules and the role catalogue
    platform/        data.py: scope ids and demo calls; calls.py, records.py:
                     the browser demo's snake_case calls, mapped at the boundary
    tokens/          Ed25519 keys, verification, replay guard; caller.py, staff.py: who is calling
cli/                 The same operations from a terminal, plus one-time setup
storage.rules        The bucket's client rules: nothing (firebase.json deploys them)
scripts/             check_lines.py (300-line limit), hooks/pre-push, e2e.md
```

Every external dependency is a top-level `src/[resource]/`: `base.py` + a provider + `fake.py` +
`__init__.py` re-exporting it (`Accounts`, `DB`, `Storage`, `Payment`, `Deploy`, `Numbers`, `Remote`).
Swapping a provider means adding a file and changing that one import; only `src/db/` imports
Firestore, `src/storage/` Cloud Storage, `src/payment/` Stripe, `src/auth/` Firebase Auth
(`tests/test_providers.py`). Tests use the fakes and never call out.

## Data

[src/models/db.py](src/models/db.py) and [paths.py](src/models/paths.py) alone spell collections and
bucket paths (`tests/test_registry.py`); karbon-admin's `/platform` shows every field. Every field is
camelCase, nested too (`to_doc`; `STORED` models read it while the payment API stays snake_case
JSON; the fake database refuses snake_case), except the demo-owned `(default)/callers/…/messages`.

- `(default)`: apps, tools, users, roles, scopes, businesses, advertisements, secrets, audit, handoffs,
  agentTokens, assignments; `callers/…` demo-owned. No payment data: see Stripe below.
- A business's collections: users, roles, branches, settings, checklist, grants, numbers, audios,
  advertisements, callers/{e164}/calls/{id}/messages/{n}, orders, bookings, complaints, menu, audit. Where
  they are is its `dbId` ([src/db/place.py](src/db/place.py)): none, nested in `(default)` under
  `businesses/{id}` (the default region, US); `{region}/{id}`, nested the same way in the region's shared
  database `region-{region}` (EU: `region-eu`); else a database of its own, `b-{id}`, in the custom
  `dataLocation` chosen at sign-up (billed separately). Older businesses keep their `b-{id}`.
- Storage: avatars, `advertisements/{id}/icon|background`, `businesses/{id}/audios/{name}/active|v{n}`, voice caches `businesses/{id}/cache/…`, `cache/fillers/…`; `audio/…` demo-owned.
- Doc ids: callers/numbers E.164, audios slug, agentTokens SHA-256, assignments `{sp}_{uid}` (db.py).

### Stripe holds every payment object

| Object | Metadata | Notes |
| --- | --- | --- |
| Product | `app`, `code`, `status` (active/soon/hidden), `unit`, `contact`, `trialDays?` | one per plan; `marketing_features` = features, `images[0]` = icon, `default_price` |
| Price | `app`, `code` | immutable; metered prices bill the plan's meter per started unit |
| Billing meter | — | one per plan, event `{app}_{code}`; meter events carry `identifier = callId` |
| Coupon / Promotion code | `app` / `app`, `createdBy` | `applies_to` the app's products |
| Customer | `app`, `tenant` | one per tenant (businessId) |
| Checkout session, Subscription | `app`, `tenant` | the subscription's price `metadata.code` is the plan |
| Invoice, payment method, File | — | read per customer; plan icons are `business_logo` files with a public link |

Reads go through per-instance caches (`stripe.*` in settings.json): the account's plans, and each
tenant's subscription. Product/price webhooks drop the plan cache, subscription/invoice/checkout
webhooks the tenant's; the hub's own writes drop them at once. A customer id never changes, so a
found one is kept; it is found by Stripe search, and the customers created in the last
`stripe.searchLagSeconds` are listed too, because search lags creation. Webhook event ids are
remembered for `stripe.eventDedupeSeconds`. Listings of every tenant (karbon-admin's customers,
`GET /v1/businesses`) use subscription search and may lag a fresh checkout by a minute.

## Running

```bash
uv sync
cp .env.example .env      # Stripe keys, Twilio master pair
uv run python app.py           # PORT (default 8080); reloads when ENVIRONMENT=development
```

Google clients use application default credentials — set `GOOGLE_APPLICATION_CREDENTIALS` locally,
nothing on Cloud Run. The project id comes from those credentials; the region is `cloud.region` in
settings.json. karbon-hub holds no signing key of its own: it only receives calls.

Stripe webhooks locally:

```bash
stripe listen --forward-to localhost:8080/webhooks/stripe/soneeva   # its whsec_ in STRIPE_WEBHOOK_SECRET
# test mode: /webhooks/stripe/soneeva_staging and STRIPE_TEST_WEBHOOK_SECRET
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

A pre-push hook runs `ruff check`, `scripts/check_lines.py` (no `.py`/`.html` file over 300
lines) and `pytest` before every `git push`, and blocks the push if any fails. The hook
lives in [scripts/hooks/pre-push](scripts/hooks/pre-push) (tracked in git, unlike `.git/hooks/`) and
is wired up per-clone with:

```bash
git config core.hooksPath scripts/hooks
```

pyright is not part of the hook — the codebase isn't clean under strict mode yet, so gating pushes
on it today would block everything. Run `uv run pyright` manually, and once the existing findings
are cleared, add `uv run pyright` to `scripts/hooks/pre-push` to enforce it on every push too.

## Deploying to Cloud Run

See [ROLLOUT.md](ROLLOUT.md): service, domain, service-account roles, APIs, secrets, Stripe
webhooks and the one-time CLI commands, in order. Caches (keys, roles, apps) are per instance
with a TTL of a minute or less, so an edit on one container reaches the others within a minute.
