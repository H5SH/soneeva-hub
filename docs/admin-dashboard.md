# Admin dashboard

Server-rendered at `/admin`. Create apps, define plans, issue keys, manage coupons and
people.

## First run

**1. Enable Email/Password** in the Firebase console → Authentication → Sign-in method, and
add yourself under Users with a password. Only this first account needs the console —
everyone after is created from the dashboard.

**2. Environment.** The Firebase pair is from Project settings → Your apps → Web app, and is
public by design. `SESSION_SECRET` is not — it signs the session.

```
FIREBASE_API_KEY=
FIREBASE_AUTH_DOMAIN=
SESSION_SECRET=            # openssl rand -base64 32
```

No project id — the browser only uses Firebase Auth, which needs the key and the domain.
Everything server-side reads the project from `GOOGLE_APPLICATION_CREDENTIALS`.

**3. Run it and sign in.**

```bash
uv run python main.py
```

Then <http://localhost:8080/admin>.

**The first person to sign in becomes superadmin.** There is no sign-up and no seed script:
accounts only exist if someone created them in the Firebase console, so whoever signs in
first already had console access. Everyone after that gets an account with no role and sees
nothing until a superadmin grants them one.

That also means a lockout is recoverable — if every role is revoked, the next sign-in takes
superadmin again.

Default roles are written on boot if missing, and are editable at `/admin/users` afterwards.

Apps are configured in one place only: the dashboard. There is no config file to edit and
no seed script — `/admin/apps/new` creates the app, its plans and its keys, and the registry
reads them straight from Firestore.

## Screens

| Screen | Path | Needs |
|---|---|---|
| Dashboard | `/admin` | — |
| Revenue panel | `/admin/revenue` | superadmin |
| Apps | `/admin/apps` | `App.View` |
| People | `/admin/users` | `User.View` |
| Roles | `/admin/roles` | `User.View` |
| Integration guide | `/admin/guide` | — |

The sidebar collapses to a 60px rail. The state is a `ps_rail` cookie read server-side, so
the width is right on the first paint rather than snapping after it.

`/admin/revenue` is the revenue panel on its own, fetched by the dashboard after it paints.
Stripe is the slowest call in the service and nothing else on the page needs it, so it no
longer holds up the first byte; a skeleton stands in meanwhile, and Stripe being down leaves
the rest of the dashboard intact.

## The CLI

Same operations, no browser. Useful for scripted setup, and for anyone who cannot complete a
Firebase sign-in in a browser.

```bash
uv run python -m cli --help
uv run python -m cli apps
uv run python -m cli app:create snapback --name Snapback \
    --return-url https://x.ngrok-free.app/plans --hosts x.ngrok-free.app,localhost
uv run python -m cli plan:save snapback starter --name Starter --amount 2900 --trial 7
uv run python -m cli key:rotate snapback          # prints the env block once
uv run python -m cli coupon:create snapback --code LAUNCH99
uv run python -m cli subscription snapback <store-hash>
uv run python -m cli --json app:show snapback     # pipe into jq
```

**It is not a second way to configure anything.** Every command calls the same service method
the matching dashboard route calls — that is why `AdminService.define_plan` exists rather than
the Stripe price logic sitting in the route handler. A command whose behaviour is not in a
service would be a fork waiting to diverge; put the behaviour in the service first.

**No scope checks, by design.** Running it needs `GOOGLE_APPLICATION_CREDENTIALS`, which
already grants full Firestore access, so a scope check here would be theatre. Scopes
constrain people holding a dashboard session, which is a different threat.

## Caching

Four in-process caches, all with a 60s TTL (30s for the registry) and an explicit drop when
the dashboard writes, so an edit applies at once on the instance that made it.

| Cache | Holds | Dropped on |
|---|---|---|
| `RoleStore` | role → scopes | saving or deleting a role |
| `KeyStore` | `applications/{id}` for token verification | any write to an app, incl. key rotation |
| `AppRegistry` | `AppConfig` for `/v1` callers | TTL only |
| `StripeGateway` price cache | price → amount/interval | TTL only |

**Across instances the TTL is the floor.** Cloud Run runs several containers; an edit made on
one leaves the others stale for up to a minute. That is the trade for not reading roles from
Firestore on every request.

Two things are deliberately *not* cached. The user row is read on every authenticated
request, so disabling an account or changing someone's role takes effect immediately. And
the dashboard's counts are read fresh, because a stale number on a dashboard is worse than a
slow one.

## Sharing the session with another app

Firebase authenticates someone at sign-in. After that the session is a **plain HS256 JWT
signed with `SESSION_SECRET`**, so any application holding the same secret can verify it
without a Firebase admin SDK:

```ts
import jwt from "jsonwebtoken";
const { sub, email } = jwt.verify(cookie, process.env.SESSION_SECRET, {
  algorithms: ["HS256"],
  issuer: "payment-service",
});
```

The token carries identity only — `sub`, `email`, `iss`, `iat`, `exp`. Roles and scopes stay
in Firestore and are read per request, so disabling someone or changing their role takes
effect immediately instead of when a token expires.

**Not NextAuth's cookie format.** NextAuth encrypts its session as a JWE with a key derived
through HKDF; reimplementing that here would break the first time they change it. An app that
wants to accept this session should verify the token itself — any HS256 library will do — rather
than expect NextAuth to pick it up natively.

| Setting | |
|---|---|
| `SESSION_SECRET` | Shared with every app that accepts the session. Required for `/admin`. |
| `SESSION_COOKIE_NAME` | Default `codinative_session`. |
| `SESSION_COOKIE_DOMAIN` | `.example.com` to share across subdomains. Blank keeps it to this host. |

**Cookies only cross a shared registrable domain.** `pay.example.com` and `app.example.com`
can share one with `SESSION_COOKIE_DOMAIN=.example.com`. Different domains cannot see each
other's cookies at all — there the token has to be handed over explicitly rather than
inherited.

**Revocation.** A signed token cannot be recalled; that is the price of being verifiable
without us. What keeps revocation immediate is the user row being read on every request —
setting `disabled` locks someone out at once, whatever token they hold. Another app
verifying the token on its own does not get that for free, and should check with us for
anything sensitive.

## People

`/admin/users`. Create an account, grant a role, assign apps, remove someone.

**No password is ever chosen on someone's behalf.** Creating an account generates a random
password that is never shown, and hands you a link for the person to set their own. They can
equally use "Forgotten your password?" on the sign-in page, so the link is a convenience
rather than the only way in.

There is still no public sign-up — creating an account needs `User.Invite`.

Removing someone deletes the Firebase Auth account as well as the record here. You cannot
remove or change the role on your own account, so the dashboard cannot be locked by a slip.

## Roles

Stored in Firestore and editable at `/admin/users`, so changing who can do what needs no
deploy.

| Role | Can |
|---|---|
| `superadmin` | everything, on every app |
| `serviceperson` | view an assigned app, rotate its key, view plans, view and create coupons |

**Scope and membership are separate checks and both must pass.** The scope says an action is
allowed at all; `users/{uid}.apps` says it is allowed *on this app*. A serviceperson holding
`Key.Rotate` who is not a member of an app cannot rotate it. Superadmin skips the membership
half, never the scope half — a superadmin whose role has no scopes can still do nothing.

Someone who can authenticate but has no role signs in and sees nothing, which is safer than
refusing: refusing leaves no record for a superadmin to grant against.

## Adding an app

1. **New app** — id, name, return URL, allowed return hosts. The id goes in the consuming
   app's `PAYMENT_APPLICATION_ID` and cannot be changed afterwards.
2. **Plans** — amount in minor units (`2900` is $29.00). A paid plan creates a Stripe product
   and price the first time it is saved; `0` means a free tier. Set the free plan code in
   Settings.
3. **Issue key** — shows `PAYMENT_APPLICATION_ID`, `PAYMENT_KEY_ID` and
   `PAYMENT_PRIVATE_KEY` once.

### Keys are never retrievable

The private half is shown at issue and never again — it is not stored. Lost one means
**Rotate**, which issues a new pair and moves the old key to `retiring` rather than deleting
it, so tokens already in flight keep verifying while you deploy.

## Coupons

A coupon is a Stripe coupon at **100% off, forever**, exposed as a promotion code. The
customer checks out normally and ends up with a real subscription at zero — status active,
webhooks fired, entitlements unchanged. Nothing downstream knows a coupon was involved, which
is the whole point: no faked state, no conditional code in the consuming app.

Minimum 8 characters, letters and numbers only. **Generate** produces 10 characters from an
alphabet with no `O`/`0` or `I`/`1`, since these get read aloud and typed by hand.

Two bindings, both needed. The `coupons` row records which app owns the code, and that is
what the scope check reads. Stripe's `applies_to.products` is what actually stops the code
being redeemed on another app's checkout — promotion codes are global to the Stripe account,
products are not.

## Deleting an app

Refused while anyone is still subscribed. Deleting would leave live Stripe subscriptions
billing against an app with nothing left to read their entitlements.
