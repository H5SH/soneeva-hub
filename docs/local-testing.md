# Testing an app's billing locally

This service plus one consuming app, Stripe test mode, all from a terminal.

```bash
export PS=/Users/saad/work/apps/app-payment-service    # this repo
export APP=/path/to/your/app
```

## Always running

```bash
cd $PS && uv run python main.py                                   # TEST Stripe keys
stripe listen --forward-to localhost:8080/webhooks/stripe/myapp   # whsec_ → $PS/.env, restart
```

That is the whole of this side. `stripe listen` carries webhooks over its own connection, so
**this service never needs a tunnel.**

## Configure

```bash
cd $PS
uv run python -m cli app:create myapp --name "My App" \
    --return-url https://myapp.example/billing --hosts myapp.example,localhost
uv run python -m cli plan:save myapp free --name Free
uv run python -m cli plan:save myapp pro  --name Pro --amount 2900 --trial 7
uv run python -m cli app:update myapp --free-plan free
uv run python -m cli app:show myapp          # every paid plan must have a price_id
uv run python -m cli key:rotate myapp        # env block → $APP, shown once
```

Add `PAYMENT_SERVICE_URL=http://localhost:8080` to the app's env. Flags: [../CLI.md](../CLI.md).

## Run the app

Whatever the app normally does. **Does it need a tunnel?** Only if its platform calls *it*
inbound — a store or marketplace extension does; a web, mobile, desktop or CLI client does
not, because it only ever calls out.

| Client | Tunnel | Checkout opens in |
|---|---|---|
| Web app | no | the same browser |
| Mobile / desktop | no | a system browser or web view, from `POST /checkout/session` |
| Platform extension | yes, for the platform's own callbacks | the embedded frame, broken out to the top window |

With a tunnel, its host must be in `--hosts` or the post-checkout redirect is *silently*
replaced by `--return-url`. With no browser at all, `--return-url` can be blank — read
`/entitlements` after checkout instead of relying on a redirect.

## Verify

```bash
cd $PS && uv run python -m cli subscription myapp <tenant>
```

| Step | Expect |
|---|---|
| the app's plans view | every tier, prices live from Stripe |
| Checkout `4242 4242 4242 4242` | back on the success URL, or the app reads `active` |
| `stripe listen` | `checkout.session.completed`, then a subscription event |
| `cli subscription` | `active=true`, the plan you bought |
| A gated action | permitted — refused before |

The three that actually find bugs:

```bash
uv run python -m cli coupon:create myapp --code TEST1234   # redeem → real $0 sub, active
# stop this service, reload the app   → last known plan, never free
# pick a second plan while subscribed → 409 → plan change, not a new checkout
```

## Gotchas

- **Test keys only** — `sk_live_…` charges real cards.
- **Apps cache entitlements** (Snapback: 60s), so a fresh checkout can read stale.
- **A new tunnel URL** changes the app's base URL, its platform callbacks, and
  `cli app:update myapp --hosts NEW,localhost`. Reserve a domain.

Broken? `cli app:show myapp`, `cli subscription myapp <tenant>`, `cli apps`, plus the
`stripe listen` output.
