# CLI

Everything the dashboard does, from a terminal. Run from the repo root — it imports `app` and
reads `.env` from the working directory.

```bash
uv run python -m cli --help
uv run python -m cli <command> --help
```

`--json` is a **root** flag, so it goes before the command:

```bash
uv run python -m cli --json app:show snapback | jq .plans
```

## Commands

### Apps

```bash
uv run python -m cli apps                       # id, name, plan count, key count, free plan
uv run python -m cli app:show snapback          # plans, keys, return hosts
uv run python -m cli app:create snapback --name Snapback \
    --return-url https://x.ngrok-free.app/plans \
    --hosts x.ngrok-free.app,localhost
uv run python -m cli app:update snapback --free-plan free
uv run python -m cli app:update snapback --hosts new.ngrok-free.app,localhost
uv run python -m cli app:delete snapback        # refused while anyone is subscribed
```

| | |
|---|---|
| `app_id` | becomes the consuming app's `PAYMENT_APPLICATION_ID`. **Cannot be changed.** |
| `--hosts` | comma-separated. A success URL on an unlisted host is **silently** replaced by `--return-url` |
| `--return-url` | where a checkout lands when the caller supplies nothing usable. A client with no browser can leave it blank |

### Plans

```bash
uv run python -m cli plan:save snapback free --name Free
uv run python -m cli plan:save snapback pro --name Pro --amount 7500 --trial 7 \
    --features "Unlimited restores|Priority support"
uv run python -m cli plan:delete snapback pro
```

- **`--amount` is in minor units** — `7500` is $75.00. Omit it (or `0`) for the free tier.
- A paid plan creates its Stripe product and price on first save. Saving again keeps the
  existing price, so editing a name does not re-price anyone.
- `--features` is split on `|`.
- `plan:delete` removes it here only. The Stripe price stays, so existing subscribers keep
  billing — that is deliberate.

### Keys

```bash
uv run python -m cli key:rotate snapback
```

Prints the env block once: `PAYMENT_APPLICATION_ID`, `PAYMENT_KEY_ID`, `PAYMENT_PRIVATE_KEY`
with newlines escaped, ready to paste. **The private half is never stored and never shown
again.** Rotating moves the previous key to `retiring` rather than deleting it, so tokens in
flight keep verifying while you redeploy.

### Coupons

```bash
uv run python -m cli coupons snapback
uv run python -m cli coupon:create snapback                  # generates a 10-char code
uv run python -m cli coupon:create snapback --code LAUNCH99 --max 50
uv run python -m cli coupon:delete snapback LAUNCH99
```

A Stripe promotion code at 100% off, forever. The customer checks out normally and ends up
with a **real** subscription at zero — active status, webhooks fired, entitlements unchanged.
Nothing downstream knows a coupon was involved. Needs at least one paid plan first; the
products it is scoped to are what stop the code working on another app's checkout.

### Inspection

```bash
uv run python -m cli subscription snapback <tenant>
```

Entitlements exactly as the consuming app receives them from
`GET /v1/apps/{id}/entitlements`. The first thing to run when an app shows the wrong plan —
it says whether the service is wrong or the app's own cache is stale.

### People and roles

```bash
uv run python -m cli users
uv run python -m cli user:create dev@codinative.com     # prints a password-set link
uv run python -m cli user:save <uid> --role serviceperson --apps snapback
uv run python -m cli user:save <uid> --disabled         # locks them out immediately
uv run python -m cli user:delete <uid>

uv run python -m cli roles
uv run python -m cli role:show superadmin
uv run python -m cli role:save auditor --scopes App.View,Plan.View,Coupon.View
uv run python -m cli role:save owner --scopes all
uv run python -m cli role:delete auditor                # refused while anyone holds it
uv run python -m cli scopes                             # every valid scope
```

`user:create` never sets a password — the link is the credential, and the person can equally
use "Forgotten your password?" on the login page. `--disabled` takes effect on the next
request, because the user row is read every time.

## Two things to understand

**It is not a second way to configure anything.** Every command calls the same service method
the matching dashboard route calls. That is why `AdminService.define_plan` exists instead of
the Stripe price logic living in the route handler — a command whose behaviour is not in a
service is a fork waiting to diverge. Put the behaviour in a service first, then expose it.

**No scope checks, deliberately.** Running this requires `GOOGLE_APPLICATION_CREDENTIALS`,
which already grants full Firestore access, so a scope check here would be theatre. Scopes
constrain people holding a dashboard session — a different threat.

## Exit codes

`0` on success. A refusal you can act on ("An app called 'snapback' already exists.") prints
one line and exits non-zero with no traceback, so it is safe in a script:

```bash
uv run python -m cli app:create snapback || echo "already there, moving on"
```
