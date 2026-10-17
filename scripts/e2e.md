# Local end to end

karbon-hub, the console and soneeva-voice on one machine, Stripe in test mode, real Firebase
(`soniva-63b25`). Run from `hub/` unless noted.

## 1. Environment

```bash
cp .env.example .env
uv sync
```

Fill `STRIPE_SECRET_KEY` (test) and leave
`GOOGLE_APPLICATION_CREDENTIALS=../firebaseServiceAccount.json`. Off Cloud Run voices run locally
from `deploy.localRepo` (settings.json). Twilio keys are only needed for step 7.

## 2. Run

```bash
uv run python main.py                                              # :8080
stripe listen --forward-to localhost:8080/webhooks/stripe/soneeva  # whsec_ → STRIPE_WEBHOOK_SECRET, restart
```

`curl localhost:8080/health` lists the apps.

## 3. Seed

```bash
uv run python -m cli seed:soneeva           # app soneeva, tools karbon-admin, soneeva-voice; scopes, plans
uv run python -m cli seed:servicepersons
uv run python -m cli key:rotate soneeva           # console key, shown once
uv run python -m cli tool:key:rotate karbon-admin # internal tool key, shown once
curl 'localhost:8080/v1/public/plans?app=soneeva'   # Stripe products: per-minute 0.1 / minute active, the rest soon
```

`key:rotate` prints `PAYMENT_*` names; in the console and karbon-admin use them as `HUB_APP_ID`,
`HUB_KEY_ID`, `HUB_PRIVATE_KEY`, with `HUB_URL=http://localhost:8080`.

## 4. Sign up a business

Sign up in the console (it calls `POST /v1/businesses`), or by hand for an existing Firebase uid:

```bash
uv run python - <<'PY'
import os, time, uuid, jwt, httpx
key = os.environ["CONSOLE_PRIVATE_KEY"].replace("\\n", "\n")
now = int(time.time())
token = jwt.encode({"iss": "soneeva", "sub": "-", "aud": "karbon-hub", "jti": str(uuid.uuid4()),
                    "iat": now, "exp": now + 60}, key, algorithm="EdDSA",
                   headers={"kid": os.environ["CONSOLE_KEY_ID"]})
body = {"ownerUid": os.environ["OWNER_UID"], "name": "Pizza Roma", "type": "restaurant",
        "country": "NL", "timezone": "Europe/Amsterdam", "languages": ["nl", "en"], "branchCount": 1}
print(httpx.post("http://localhost:8080/v1/businesses", json=body,
                 headers={"Authorization": f"Bearer {token}"}, timeout=120).json())
PY
```

Creating the `b-{id}` database takes up to a minute. Check it in the Firebase console
(Firestore → databases), with roles, checklist, `users/{owner}` and `settings/agent`. The
owner's token now carries `{businessId, username}` (sign out and in again in the console).

## 5. Pay

From the console's billing page (it calls `POST /v1/businesses/{id}/checkout` with
`sub` = businessId and the owner's uid), choose Per minute and pay with `4242 4242 4242 4242`.
`checkout.session.completed` arrives through `stripe listen`, and karbon-hub starts
soneeva-voice on port 8100+ with the business env. Log: `.deploy-local/{id}.log`.

```bash
uv run python -m cli businesses        # voiceUrl is set
```

## 6. Reach the voice service

Twilio needs a public URL: `ngrok http 8100`, then
`uv run python -m cli business:deploy <id> --public-url https://<your-tunnel>`; later redeploys keep it.

## 7. Number and a call

Needs `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` of an upgraded (non-trial) account: trial
accounts cannot create subaccounts. From the console's numbers page (or the API):
`GET /v1/businesses/{id}/numbers?country=NL`, then `POST` one. The first purchase makes the
business's subaccount and redeploys the voice service with its credentials. Some countries
(NL, DE) need a regulatory bundle before Twilio sells a number; US numbers do not.

Call the number. When the call ends soneeva-voice posts `/v1/usage`; it becomes a meter event in
Stripe (Billing → Meters) and, once Stripe has aggregated it, the day's seconds in
`GET /v1/payment/businesses/{id}/usage` (karbon-admin).

## 8. Clean up

```bash
pkill -f "uvicorn src.main:app"; rm -rf .deploy-local
```

Business databases are deleted from the Firebase console (or `gcloud firestore databases delete`).
