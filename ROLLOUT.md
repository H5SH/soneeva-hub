# Rollout

karbon-hub is now a JSON API only. Nothing is live yet, so this is a first deploy, not a migration.

## What breaks

- `/admin` (pages, sign-in, cookies) is gone. Use karbon-admin, which calls `/v1/me` and `/v1/payment/*`.
- `/billing/{appId}` (plan picker, `?token=`, `/return`, `/cancel`, `/portal`) is gone, with the
  `payment-service-hosted` audience. The console uses `GET /v1/businesses/{id}/plans` and
  `POST /v1/businesses/{id}/checkout` / `/portal`.
- Apps vs internal tools: `karbon-admin` and `soneeva-voice` are tools (`tools/{id}`, never in
  Stripe); `soneeva-console` is gone — the console signs as the app `soneeva` (`HUB_APP_ID=soneeva`,
  staging `soneeva_staging`), which now holds the console's `hub.*` scopes. Re-run `seed:soneeva`: it
  moves the old `apps/karbon-admin` and `apps/soneeva-voice` with their keys and deletes
  `apps/soneeva-console`. Then `key:rotate soneeva` for the console (karbon-admin's key moves as is).
- One people system: `/v1/payment/users`, `/v1/payment/roles`, `/v1/platform/roles`,
  `/v1/platform/scopes`, `/v1/platform/tools` and the platform half of `/v1/accounts` are gone. Platform
  people are `/v1/people` (role, `apps`, `businesses`, tokens, servicepersons on one record) and roles
  `/v1/roles` (console ids and `payment.*` together; `payment.user.*` and `Tools.View` no longer exist,
  `Users.*`/`Roles.*` guard both). `/v1/accounts` is only the console's business members and own
  account. karbon-admin needs the new `hub.people.view|update` (drops `hub.account.*` except `recover`
  and `hub.platform.update`): re-run `seed:soneeva`, which also deletes the stale `apps/karbon-hub`,
  `apps/soneeva-console`, `apps/karbon-admin`, `apps/soneeva-voice` (tools keep their keys in `tools`).
  CLI: `users`/`user:*` are now `people`/`person:*`.
- Staging: `{appId}_staging` is every app in Stripe test mode (`STRIPE_TEST_SECRET_KEY`, add it to
  the hub's env). A business signed up by a staging caller is `test: true` for good; a staging caller
  gets 403 on any other business. Create its endpoint with `webhook:create soneeva_staging`.
- An app with subscribers (live or test) is never deleted.
- Sign-up (`POST /v1/businesses`): `hours` is a week, `{"mon": [{"open": "09:00", "close": "17:00"}], …,
  "sun": []}` (empty is closed, at most two ranges a day, 24h `HH:MM`, open ≠ close; anything else is 422),
  stored as is on `branches/main.hours`. Only the `owner` role (`name`, every scope) is seeded; manager and
  staff are no longer made — businesses create their own roles in the console.
- `GET /v1/businesses/{id}/plan` for each voice (scope `hub.plan.view`, re-run `seed:soneeva`).
- `POST /v1/apps/{appId}/checkout/session` (generic billing API) now sends Stripe straight back to
  `success_url`; the subscription is synced by the webhook, not on the way back.
- `SESSION_SECRET` is no longer read. Password-reset emails
  (`/v1/people/{uid}/password-reset` with `send`) go through Identity Toolkit with the runtime
  service account's own token.
- Recovery: `/recover`, a break-glass page to rotate a hand-rotated internal tool's key (karbon-admin)
  when karbon-admin is down; it needs `Tools.Write` (superadmin's `*`). App keys rotate in karbon-admin.
  Needs `AUTH_SECRET` (the same value as karbon-admin's) and `FIREBASE_API_KEY` (the web API key);
  without them it says "not configured". Password only for now; 2FA comes later.
  Someone signed in to karbon-admin is let in directly (and `/` shows them "Open key recovery"): the hub
  reads karbon-admin's Auth.js session cookie with the shared `AUTH_SECRET`. For that cookie to reach the
  hub, both must share the parent domain (`admin.soneeva.com`, `hub.soneeva.com`) with the same
  `AUTH_SECRET`, and karbon-admin needs `AUTH_COOKIE_DOMAIN=.soneeva.com`. /recover's own sign-in stays
  for when karbon-admin is down; its sign-out ends only that cookie.
- `/docs` and `/openapi.json` exist only with `ENVIRONMENT=development`.
- settings.json: `session.*` removed, `dashboard.*` renamed `customers.*`, `handoff.*` added.
- There is no "first sign-in becomes superadmin" any more: make the first one with the CLI (below).
- The console's app no longer holds `hub.account.update` or `hub.platform.*` (re-run `seed:soneeva`).
  `/v1/scopes/{appId}` accepts `hub.business.view` instead.
- `GET /v1/servicepersons` with a business sub returns only that business's serviceperson card.
- Advertisements: `/v1/advertisements` with the new app scopes `hub.advertisement.view|update` (re-run
  `seed:soneeva` so karbon-admin holds them) and the person scopes `Advertise.View|Write` (superadmin's `*`).
- `seed:soneeva` now really deletes the moved/stale `apps` docs (karbon-admin, soneeva-voice, soneeva-console,
  karbon-hub), also ones an earlier run only flagged `isDeleted`.
- A serviceperson is a person whose role is `serviceperson` (`users/{uid}`, with `phone`): `/v1/servicepersons`
  and assigning a business read people. `(default)/servicepersons` and `seed:servicepersons` are gone; delete
  that collection, and give each serviceperson the role (`person:save <uid> --role serviceperson --phone …`).
- Each new `b-{id}` gets the console's `calls.startedAt` indexes at sign-up.
- Files: `/v1/media/{path}` serves avatars, app icons and logos from the private Firebase Storage
  bucket; `POST /v1/accounts/password-reset-request` emails a reset link (always 202). Both need
  the new app scopes from `seed:soneeva` (`hub.media.view|update`, `hub.account.recover`, and
  `hub.account.update` again for self-service name and password). A business token can no longer
  change another member's email or password.
- Platform people are checked on console ids, not `hub.*` ids in their role: `/v1/businesses` needs
  `Businesses.View` (or `.Access`/`.Assign`), PATCH `Businesses.Write` (status, configured) or
  `Businesses.Assign` (servicepersonUid), voice and numbers `Businesses.Write`. Add `Businesses.Write`
  to roles that change businesses (superadmin's `*` has it). `karbon-admin` gets the app scopes its
  pages call (re-run `seed:soneeva`).
- `servicepersons/{uid}.photoURL` is no longer read or returned (the picture is `users/{uid}/avatar`);
  soneeva-voice keeps each business's greeting under `businesses/{id}/cache/greeting/…` and its
  fillers under `cache/fillers/…` (older `greeting/…` and `businesses/{id}/greeting/…` objects can be
  deleted; they are regenerated). `audio/…` is the browser demo's: leave it.
- New scopes: console `profile.edit` (owner, manager, staff) and `business.edit` (owner), platform
  `Profile.Write` (every platform role). Roles of businesses created before this keep their stored
  scopes: add `profile.edit`/`business.edit` to them in the console's roles page (owner's `*` was
  expanded at sign-up).
- karbon-hub has no signing key: no `/.well-known/jwks.json`, no `karbon-hub-key` secret, no
  `cli hub:key`. Handoffs are one-time codes (`?code=`) the console redeems at
  `POST /v1/handoff/redeem`; the console's old `?token=` handoff and its `b-{id}/handoffs` store are gone.
- `MCP_URL` and `WHATSAPP_FROM` are no longer hub env: each business has `mcpUrl` and `whatsappFrom`
  (empty at sign-up), set in karbon-admin's "Agent connection" panel (`PATCH /v1/businesses/{id}`,
  `Businesses.Write`), which redeploys only that business's voice. Remove both from the hub's env.
  With no sender, the voice sends no WhatsApp confirmations.
- Every checklist starts with `assigned`, `meeting`, `subscribed`, `mcp`. `hub.business.update` also admits
  `Businesses.Access` people, for `POST /v1/businesses/{id}/checklist/meeting` only.

- Call menus and recorded messages: `numbers/{e164}` and `audios/{name}` in each `b-{id}`, recordings
  at `businesses/{id}/audios/{name}/active|v{n}`. New console scopes `ivr.view`, `ivr.edit`,
  `audio.edit` (re-run `seed:soneeva`); owners of businesses created before this need them added in
  the console's roles page. Buying a number now writes a default menu (straight to the agent, greeted
  by `opening` when it exists) and always restarts that business's voice. Every voice deploy carries
  `BUSINESS_TYPE` and `RESTART_AT` (a timestamp, so an otherwise unchanged env still makes a new
  Cloud Run revision).

- Structure clean-up (nothing live, so no data migration): every field karbon-hub and soneeva-voice
  write is camelCase (`isDeleted`, `createdAt`, `appId`, `startedAt`, `lastCallAt`, …);
  `applications` is now `apps`, the (default) `grants` (who looks after whom) is now `assignments`.
  A `b-{id}` provisioned before this needs the `calls.startedAt` indexes: `business:provision <id>`
  requests what src/db/indexes.py lists and waits for it, also for a provisioned business. The demo's `(default)/callers/…` stays snake_case: demo-owned.

- Stripe is the single source of truth for payment data. Delete the Firestore `(default)`
  collections `plans`, `coupons`, `customers`, `subscriptions`, `events` and `usage`, and the
  `resources` doc; nothing reads them. Plans are Stripe products (metadata `app`, `code`, `status`,
  `unit`, `contact`) with default prices, coupons are Stripe coupons + promotion codes (metadata
  `app`), a tenant is the Stripe customer with metadata `{app, tenant}` (was `{app_id, store_hash}`).
  Re-run `seed:soneeva` against the Stripe account to create the products; old prices without a
  product's metadata are ignored (archive them in the dashboard). Re-create the webhook endpoint
  (`webhook:create`): it now also takes `product.*` and `price.*` events.
- `/v1/apps/{appId}/*` responses say `tenant` instead of `store_hash` and have no `limits`;
  `/v1/payment/…/plans` has `icon` and no `version`/`newPrice` (a new amount is a new Stripe price);
  `/v1/payment/businesses/{id}/usage` is `{days:[{day, seconds}]}` from Stripe meter summaries.
  `roles:migrate` and `checklist:upgrade` are gone (nothing live to migrate).

## Hosting

- Cloud Run service `karbon-hub`, region `us-central1`, from this repo's Dockerfile
  (`gcloud run deploy karbon-hub --source . --region us-central1`).
- Ingress all, `--allow-unauthenticated`: Stripe must reach the webhooks; every other call is
  checked by the EdDSA token, not by Cloud Run IAM.
- Domain: `hub.soneeva.com` mapped to the service. It is used only
  server to server (the console's and karbon-admin's Next servers, soneeva-voice) and by Stripe;
  no browser calls it except a person on `/recover`. Set it as `SERVICE_BASE_URL` here and `HUB_URL` in the clients.
- Min instances 0 is fine; the replay guard and caches are per instance.

## Runtime service account

Add:

| Role | For |
| --- | --- |
| `roles/datastore.owner` | `(default)`, creating business databases `b-{id}` and their indexes |
| `roles/firebaseauth.admin` | accounts, claims, reset links and reset emails |
| `roles/storage.objectAdmin` on the bucket `{project}.firebasestorage.app` only | `/v1/media`: avatars, icons, logos |
| `roles/run.admin` | deploying soneeva-voice services |
| `roles/iam.serviceAccountUser` on the voice runtime service account | deploying voices as it |
| `roles/secretmanager.secretAccessor` on the three secrets below only | Cloud Run reads them into env at start |

Remove, if granted earlier: project-wide `roles/secretmanager.*` roles (delete the `karbon-hub-key` and
`session-secret` secrets), and any broad role (`roles/editor`, `roles/firebase.admin`) given while testing.

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
| `ENVIRONMENT` | env | `production` |
| `SERVICE_BASE_URL` | env | `https://hub.soneeva.com` |
| `LOG_LEVEL` | env | `INFO` |
| `AUTH_SECRET` | secret | optional: `/recover` sessions and reading karbon-admin's session; the same value as karbon-admin's |
| `FIREBASE_API_KEY` | env | optional: `/recover` password check (the Firebase web API key) |

"secret" rows live in Secret Manager and reach the container as env through Cloud Run
(`--set-secrets STRIPE_SECRET_KEY=stripe-secret-key:latest,…`); the code reads plain env either way.
The rest are plain env vars. Remove `SESSION_SECRET`. Do not set `GOOGLE_APPLICATION_CREDENTIALS` or
`VOICE_PUBLIC_URL` on Cloud Run.

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
| `soneeva-voice` | nowhere by hand: karbon-hub issues a per-business key at each voice deploy | — |

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
      the voice deploys, `plan.active` is true; the portal opens
- [ ] a serviceperson with a running grant gets a handoff URL (`?code=`); the console lets them in
      once, a second open of the same URL lands on `/login?reason=handoff`, and so does one after a minute
- [ ] karbon-admin's "Agent connection" saves an MCP URL; the business's voice redeploys with `MCP_URL`
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
