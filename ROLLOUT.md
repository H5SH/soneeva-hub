# Rollout

karbon-hub is now a JSON API only. Nothing is live yet, so this is a first deploy, not a migration.

## Before the first deploy

- `/recover` needs `AUTH_SECRET` (karbon-admin's value) and `FIREBASE_API_KEY`. For someone signed in to karbon-admin to
  be let in directly, both must share the parent domain (`admin.soneeva.com`, `hub.soneeva.com`) and karbon-admin needs
  `AUTH_COOKIE_DOMAIN=.soneeva.com`.
- There is no "first sign-in becomes superadmin": make the first one with the CLI (below).
- Deploy soneeva-voice and the console with this hub: both read an empty `dbId` as nested in `(default)`. The first
  nested sign-up adds the composite `calls (businessId, startedAt)` indexes to `(default)` (and `region-eu`), which
  takes a few minutes.
- Old objects under `greeting/…` and `businesses/{id}/greeting/…` can be deleted, they are regenerated. `audio/…` is
  the browser demo's: leave it.

## Hosting

- Cloud Run service `karbon-hub`, region `us-central1`, from this repo's Dockerfile
  (`gcloud run deploy karbon-hub --source . --region us-central1`).
- Ingress all, `--allow-unauthenticated`: Stripe must reach the webhooks; every other call is
  checked by the EdDSA token, not by Cloud Run IAM.
- Domain: `hub.soneeva.com` mapped to the service. It is used only
  server to server (the console's and karbon-admin's Next servers, soneeva-voice) and by Stripe;
  no browser calls it except a person on `/recover`. Set it as `SERVICE_BASE_URL` here and `HUB_URL` in the clients.
- Deploy with `--no-cpu-throttling`: provisioning runs after the sign-up answers, for up to 15 minutes.
- Min instances 0 is fine; the replay guard and caches are per instance.

## Runtime service account

Add:

| Role | For |
| --- | --- |
| `roles/datastore.owner` | `(default)`, creating business databases `b-{id}` and their indexes |
| `roles/firebaseauth.admin` | accounts, claims, reset links and reset emails |
| `roles/storage.objectAdmin` on the bucket `{project}.firebasestorage.app` only | `/v1/media`: avatars, icons, logos |
| `roles/secretmanager.admin` | writing each business's voice env as `voice-{businessId}` |
| `roles/secretmanager.secretAccessor` on the three secrets below only | Cloud Run reads them into env at start |

Remove, if granted earlier: `roles/run.admin` and `roles/iam.serviceAccountUser` (the hub deploys
nothing now), the `karbon-hub-key` and `session-secret` secrets, and any broad role (`roles/editor`,
`roles/firebase.admin`) given while testing.

## APIs to enable

`run`, `firestore`, `identitytoolkit`, `storage`, `firebasestorage`, `artifactregistry`, `cloudbuild`
(for `--source` deploys), `secretmanager`, `iam` — all `*.googleapis.com`.

## Secrets and env

| Name | How | Value |
| --- | --- | --- |
| `STRIPE_SECRET_KEY` | secret | live key (test key first) with write on Products, Prices, Files, Meters, Meter Events, Coupons, Promotion Codes, Customers, Checkout, Billing portal |
| `STRIPE_WEBHOOK_SECRET` | secret | optional fallback signing secret; per-app secrets (`webhook:create`) are preferred and the only thing that works once a second app exists |
| `TWILIO_ACCOUNT_SID` | env | master account (non-trial) |
| `TWILIO_AUTH_TOKEN` | secret | master account token |
| `CONNECTIONS_KEY` | secret | required in production: the console's value (32 bytes, base64), reads store tokens to charge Shopify usage |
| `ENVIRONMENT` | env | `production` |
| `SERVICE_BASE_URL` | env | `https://hub.soneeva.com` |
| `LOG_LEVEL` | env | `INFO` |
| `AUTH_SECRET` | secret | optional: `/recover` sessions and reading karbon-admin's session; the same value as karbon-admin's |
| `FIREBASE_API_KEY` | env | optional: `/recover` password check (the Firebase web API key) |

"secret" rows live in Secret Manager and reach the container as env through Cloud Run
(`--set-secrets STRIPE_SECRET_KEY=stripe-secret-key:latest,…`); the code reads plain env either way.
The rest are plain env vars. Remove `SESSION_SECRET`. Do not set `GOOGLE_APPLICATION_CREDENTIALS` on Cloud Run.

## Firestore TTL

One-time handoff codes and agent operator tokens expire through TTL policies on `(default)`:

```bash
gcloud firestore fields ttls update expireAt --collection-group=handoffs --enable-ttl --database='(default)'
gcloud firestore fields ttls update expiresAt --collection-group=agentTokens --enable-ttl --database='(default)'
```

TTL deletion is lazy (up to a day late); the hub checks `expiresAt` itself on every agent call, so
the policy only keeps `agentTokens` from growing. Only a SHA-256 of each `kat_…` token is stored.

## Storage

Create the default bucket once (Firebase console → Storage, or it exists already as
`{project}.firebasestorage.app`), then make it private for clients:

```bash
firebase deploy --only storage --project soniva-63b25    # storage.rules: no client reads or writes
gcloud storage buckets add-iam-policy-binding gs://soniva-63b25.firebasestorage.app \
  --member=serviceAccount:<karbon-hub runtime SA> --role=roles/storage.objectAdmin
```

Nothing is public: no download tokens, no `allUsers` binding. Browsers get images only through
the console's and karbon-admin's servers, which call `/v1/media`.

## Stripe

- Webhook endpoint per product app: `uv run python -m cli webhook:create soneeva` (or the
  "Create Stripe endpoint" button in karbon-admin) creates `{SERVICE_BASE_URL}/webhooks/stripe/soneeva`
  with exactly the events the hub handles and stores its signing secret on
  `apps/soneeva.stripeWebhookSecret` — never printed. Needs `SERVICE_BASE_URL` set to the
  public https URL. By hand instead: events `checkout.session.completed`,
  `checkout.session.async_payment_succeeded`,
  `customer.subscription.created|updated|deleted|paused|resumed|trial_will_end`, `invoice.paid`,
  `invoice.payment_succeeded`, `invoice.payment_failed`, `product.created|updated|deleted`,
  `price.created|updated|deleted`; paste the secret into the app's
  "Webhook secret" field (write-only) or `app:update soneeva --webhook-secret`.
- Staging: the same in the test account with `webhook:create soneeva_staging`
  (`/webhooks/stripe/soneeva_staging`, secret on `apps/soneeva.stagingWebhookSecret`).
- Activate the customer portal (Billing → Customer portal), in both modes, so `/portal` works.

## One-time CLI, in order

Run from `hub/` with the admin service account (`GOOGLE_APPLICATION_CREDENTIALS`) and the
production `.env` values.

```bash
uv run python -m cli seed:soneeva            # app soneeva, tools karbon-admin and soneeva-voice
                                             # with their hub.* scopes, scopes/soneeva, plans, platform roles
uv run python -m cli key:rotate soneeva      # the console's key
uv run python -m cli tool:key:rotate karbon-admin
uv run python -m cli person:save <your-uid> --role superadmin # the first superadmin
```

By hand `seed:soneeva` is `app:update soneeva --scopes …` and `tool:create` / `tool:update` (CLI.md
lists the scopes).

Where the keys go (each printed once):

| Caller | Goes to | As |
| --- | --- | --- |
| `soneeva` | the console's Cloud Run service (env); a staging console uses `HUB_APP_ID=soneeva_staging` | `HUB_APP_ID`, `HUB_KEY_ID`, `HUB_PRIVATE_KEY`, plus `HUB_URL` |
| `karbon-admin` | karbon-admin's hosting (env) | the same four names |
| `soneeva-voice` | Secret Manager `voice-{businessId}`, written by karbon-hub at subscription; the developer mounts it at `/run/secrets/voice.env` in that business's voice service (whose service account needs `roles/secretmanager.secretAccessor` on it) | the whole env |

## Check list

- [ ] `curl https://hub.soneeva.com/health` → `{"status":"ok"}` (no app ids, no environment)
- [ ] `/docs` → 404 in production; `/v1/agent/openapi.json` → the agent API schema
- [ ] karbon-admin › Let your agent do it: a superadmin (holds `Agent.Use` through `*`) mints a token,
      `GET /v1/agent/whoami` answers with it, "Revoke my tokens" makes it 401
- [ ] `curl 'https://hub.soneeva.com/v1/public/plans?app=soneeva'` → per-minute active, the rest soon
- [ ] karbon-admin signs in a superadmin; `GET /v1/me` shows `payment.*` and `platform.docs.view`
- [ ] karbon-admin lists apps, businesses, people, roles; a sales person sees only their apps
- [ ] the console signs up a business (`POST /v1/businesses`) as `soneeva`
- [ ] the console's billing page: plans, checkout with `4242 4242 4242 4242`, the webhook arrives,
      `voice-{businessId}` appears in Secret Manager, `plan.active` is true; the portal opens
- [ ] a serviceperson with a running grant gets a handoff URL (`?code=`); the console lets them in
      once, a second open of the same URL lands on `/login?reason=handoff`, and so does one after a minute
- [ ] the developer deploys the voice with that secret; a `Voice.Url.Write` person saves its URL in
      karbon-admin, the serviceperson ticks "Voice service deployed", and the console can buy a number
- [ ] a `Voice.Sandbox` person rotates the sandbox key; a local voice runs with that `.env`
- [ ] assigning a serviceperson ticks "Serviceperson assigned"; a checkout ticks "Subscribed to a plan"
- [ ] a call ends → `POST /v1/usage` → meter event in Stripe (identifier = the call id) → the day's
      seconds in karbon-admin's customer usage
- [ ] the console uploads a business logo and an avatar; a direct `https://firebasestorage.googleapis.com/…`
      request for the same path is refused
- [ ] "Forgot password?" on the console sends Firebase's reset email; an unknown address shows the same message

- Caller names and tags: `callers/{e164}` in each `b-{id}` gains `name`, `nameLower`, `tags`, `updatedBy`,
  `updatedAt` (soneeva-voice only merges its own fields, so they survive calls). New console scopes
  `callers.view` and `callers.edit` (re-run `seed:soneeva`; new businesses: manager both, staff view, owner `*`);
  roles of existing businesses need them added in the console's roles page. New routes
  `GET /v1/businesses/{id}/callers` (Businesses.View / `callers.view`) and `PATCH …/callers/{e164}`
  (Businesses.Write / `callers.edit`). No new index: every search runs on single-field indexes.

- Blocking callers: `callers/{e164}` gains `blocked`, `blockedReason`, `blockedBy`, `blockedAt`; blocking a
  number that never called creates its doc (phone and these fields only). New console scope `callers.block`
  (re-run `seed:soneeva`; owners get it through `*`, manager and staff do not; existing businesses add it
  to roles in the console). `PATCH …/callers/{e164}` takes `blocked` (+ `blockedReason`), `POST …/callers`
  blocks a new number, `GET …/callers?blocked=true` lists them (single-field index, nothing to create).
  Each block and unblock writes `caller.block` / `caller.unblock` to the business's `audit/`.
  soneeva-voice must be redeployed: it rejects blocked numbers (`<Reject reason="busy"/>`) before anything else.

- Shopify and BigCommerce stores: new collection `(default)/stores/{platform}:{storeId}`, new app scopes
  `hub.store.signin` and `hub.store.update` (re-run `seed:soneeva`), new business type `shop`. New console scopes
  `connections.view` and `connections.edit` for the console's page; the hub itself only lets a business's owner connect
  or disconnect a store, so existing owners need nothing. `POST /v1/businesses` takes `store` with an `owner` without a
  password. An uninstall sets status `deleted` and `uninstalled`; a reinstall gives the status back, and a business
  deleted on the platform stays deleted. A business paying through Shopify holds `storePlan`, which wins over Stripe
  everywhere its plan is read; the voice env is written when it goes active. Its calls are charged to Shopify on
  `POST /v1/usage`, so the hub needs the console's `CONNECTIONS_KEY`. An uninstall also ends a Shopify plan and answers
  `active: false` on `GET /v1/businesses/{id}/plan`, so the voice stops serving; re-deploy the voice to rely on it.
  The row's `uninstalled.at` is the uninstall date (a reinstall clears it): the retention job, which lives outside this
  repo, counts its 30 days from it, which meets Shopify's `shop/redact`, and must also delete the business's `stores`
  rows, or a reinstall finds it. `cli store:release` frees a store stuck on a business deleted on the platform.
  Voices must not report a call more than 24 hours late: Shopify forgets the charge's key then and charges again.
- Rotating an app or tool key now stops the old keys after `keys.retiringSeconds` (an hour), or at once with `--revoke`
  or the box on `/recover`: redeploy within that. A deleted business, and one whose store charges have failed for
  `stores.chargeGraceSeconds`, answers `active: false` on `GET /v1/businesses/{id}/plan`. A business token can no longer
  change `status` except pending to running.
  No new index.
