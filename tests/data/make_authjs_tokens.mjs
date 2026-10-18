// Makes tests/data/authjs_tokens.json with the admin's own @auth/core (dummy secret):
//   node tests/data/make_authjs_tokens.mjs > tests/data/authjs_tokens.json
import { encode } from "../../../admin/node_modules/@auth/core/jwt.js";
const secret = "dummy-auth-secret-for-hub-tests", salt = "soneeva-admin", year = 365 * 86400;
const make = (token, maxAge = 100 * year, s = salt) => encode({ token, secret, salt: s, maxAge });
const out = {
  secret,
  boss: await make({ uid: "boss", email: "boss@x.com" }),
  boss_secure: await make({ uid: "boss" }, 100 * year, "__Secure-soneeva-admin"),
  sales1: await make({ uid: "sales1" }),
  expired: await make({ uid: "boss" }, -3600),
  no_uid: await make({ email: "boss@x.com" }),
};
process.stdout.write(JSON.stringify(out, null, 2) + "\n");
