# settings.json

Decisions that are safe to change. Read once at start; a change needs a restart. Secrets and
per-machine values live in `.env` (see `.env.example`); contracts are constants in the code.

| Key | What changing it does |
| --- | --- |
| `cloud.region` | Region of new Cloud Run voice services and new business databases (`b-{id}`). Existing ones stay where they are. |
| `deploy.image` | Image new voice revisions run on Cloud Run (`{region}`, `{project}` are filled in). |
| `deploy.localRepo` | soneeva-voice checkout the local driver runs (relative to `hub/`). |
| `deploy.localPortStart` | First port the local driver tries. |
| `handoff.consoleUrl` | Console a serviceperson handoff lands on (`{consoleUrl}/handoff?code=`). |
| `handoff.localConsoleUrl` | The same when karbon-hub runs with `ENVIRONMENT=development`. |
| `scopes.<appId>` | Fallback scope catalogue for an app (`[{id, group}]`); `seed:soneeva` writes it to `scopes/soneeva`. Generated from the console's `scopes.json`; a test keeps them equal. |
| `plans.currency` | Currency of the plans `seed:soneeva` creates. |
| `stripe.plansCacheSeconds` | How long the account's plans (products with default prices) are kept before Stripe is listed again. Product/price webhooks and the hub's own writes drop them at once; this only bounds another instance. |
| `stripe.tenantCacheSeconds` | How long one tenant's subscription is kept. Subscription, invoice and checkout webhooks drop it at once. |
| `stripe.searchLagSeconds` | Customers created this recently are listed as well as searched: Stripe search lags creation, and a missed one would be created twice. |
| `stripe.eventDedupeSeconds` | How long a handled webhook event id is remembered, so a redelivery does not redeploy a voice. |
| `businesses.idLength` | Length of new business ids. |
| `businesses.indexWaitSeconds` | How long provisioning waits for a new `b-{id}`'s indexes (src/db/indexes.py) before recording `provisionError` "indexes still building"; the retry waits again. |
| `businesses.indexPollSeconds` | How often it checks them meanwhile. |
| `businesses.types` | Business types allowed at sign-up. |
| `businesses.roles` | Roles seeded into each new `b-{id}`: only `owner`, with `name` and `scopes` (`*` = the app's whole catalogue). Every other business role is made in the console. |
| `businesses.checklistByType` | Checklist a type gets; unlisted types get `default`. |
| `businesses.checklistStart` | Items every checklist starts with, before the type's own. `assigned` and `subscribed` are kept by the hub (serviceperson set, subscription active); `meeting` the assigned serviceperson or the owner ticks; the rest follow review → done. |
| `businesses.checklists` | Checklist items per checklist, in order. |
| `businesses.checklistLabels` | Label stored for each item (the owner can edit it). |
| `media.bucket` | Bucket `/v1/media` reads and writes (`{project}` is filled in from the credentials). |
| `media.maxBytes` | Largest image `PUT /v1/media/{path}` accepts (413 above it). |
| `media.types` | Image types it accepts (415 otherwise; the first bytes must match too). |
| `advertisements.backgrounds` | An advertisement's `background`: a theme colour token, or `image` (its background image). |
| `advertisements.maxBusinesses` | Most businesses one `POST /v1/advertisements/{id}/publish` names. |
| `accounts.resetLimit`, `accounts.resetWindowSeconds` | Reset emails `POST /v1/accounts/password-reset-request` sends per address per window (per instance); more are dropped silently. |
| `voice.language`, `voice.voice` | Defaults written to `settings/agent` at sign-up. |
| `numbers.type` | Twilio number kind searched: `Local`, `Mobile` or `TollFree`. |
| `numbers.searchLimit` | Most numbers a search returns. |
| `agent.minMinutes`, `agent.maxMinutes`, `agent.defaultMinutes` | Lifetime bounds of an operator token for `/v1/agent` (`POST /v1/agent-tokens`). |
| `numbers.requireStatuses` | Subscription statuses that allow searching and buying numbers. |

Call menus and recordings have no tunables here: their rules (E.164 phones, keys 1–9, a recording name
as a slug, 5–60 ring seconds, text up to 1000 characters) are the contract, in `src/models/ivr.py`. Who
may change them is the console scopes `ivr.view`, `ivr.edit` and `audio.edit` in `scopes.soneeva`.
| `callers.pageSize` / `maxPageSize` | Callers `GET /v1/businesses/{id}/callers` returns without `limit`, and the most it ever returns. |
| `callers.maxName` / `maxTags` / `maxTagLength` | Longest caller name, most tags per caller, longest tag; the console checks the same (`callers.*`). Too many or too long is refused (422), never cut. |
| `callers.maxBlockedReason` | Longest reason given when blocking a number (the console checks the same). Blocking needs the console scope `callers.block` (owner via `*`; other roles only when the business gives it) or Businesses.Write. |
| `usage.maxSeconds` | Longest call `/v1/usage` accepts. |
| `customers.invoicesShown` | Invoices fetched from Stripe for `/v1/payment/businesses/{id}/invoices`. |
| `customers.usageDaysShown` | Days of Stripe meter summaries `/v1/payment/businesses/{id}/usage` returns. |
| `platformScopes` | The console scope ids a platform role can hold, by group (`twins`: an `Assigned.` twin exists); `GET /v1/roles` serves it with the `payment.*`/`platform.*` scopes. |
| `platformRoles` | Console scopes (`*` = every console scope; `Profile.Write` edits your own name, password and avatar) and `platform.*` scopes the platform roles also hold. A missing role is created with them (at boot and by `seed:soneeva`); an existing role is never added to, so edits in Roles stick (`*` covers new console scopes by itself). |
| `people.readScopes` | Console scopes (or their `Assigned.` twin) that let a platform person list platform people through `/v1/people`. |
| `people.callScopes` | Console scopes `/v1/platform/calls` accepts as `scope`; the person's reach on it decides whose calls they get. |
| `people.callsPageMax` | Most calls one `/v1/platform/calls` page returns. |
| `people.callDays` | Days `/v1/platform/calls/daily` and `/v1/platform/home` cover when no range is given. |
| `people.grantNever` | Scopes a serviceperson's business grant never confers here; keep equal to the console's `grants.never`. |
