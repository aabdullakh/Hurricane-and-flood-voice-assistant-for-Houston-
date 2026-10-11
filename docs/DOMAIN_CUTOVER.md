# Domain cutover: 45-76-57-234.sslip.io → thefloodfather.surf

Same server (Vultr, `45.76.57.234`), new name. The site names are in the **Caddyfile's site address**
(`thefloodfather.surf, 45-76-57-234.sslip.io {`); Caddy gets an HTTPS certificate for each name itself. Both names are
served during the cutover, so the voice agent keeps working while it is switched over: no downtime.
(Earlier versions read the name from `DOMAIN` in `~/app/.env`; that setting is no longer used.)

Order matters: **DNS first, then the server.** Let's Encrypt only issues the certificate once the name points at the
server, and repeated failed attempts hit its rate limit (5 failures per name per hour).

## 1. DNS at Porkbun

For `thefloodfather.surf` (Domain Management → DNS):

| Type | Host | Answer | TTL |
|---|---|---|---|
| **A** | *(blank / `@`)* | `45.76.57.234` | 600 |

- **Delete the old `ALIAS` record on `@`** (Porkbun's parking default). An ALIAS and an A record on the same name conflict.
- Also delete any **`AAAA` on `@`** and Porkbun's parking `CNAME` on `*`. A stray AAAA makes Let's Encrypt try IPv6 and fail.
- If a **`CAA`** record exists, it must allow `letsencrypt.org` (or delete it).
- Optional `www`: add `CNAME www → thefloodfather.surf`, and add `www.thefloodfather.surf` to the Caddyfile's site address.

Check from your Mac until it answers correctly (can take a few minutes):

```bash
dig +short thefloodfather.surf A @1.1.1.1      # must print exactly: 45.76.57.234
dig +short thefloodfather.surf A @8.8.8.8      # same
dig +short thefloodfather.surf AAAA @1.1.1.1   # must print nothing
```

## 2. Server: serve both names

From your Mac (ships the Caddyfile with both names, reloads Caddy, checks everything):

```bash
DEPLOY_DOMAIN=45-76-57-234.sslip.io bash scripts/deploy.sh --api   # checks via the old name until the new one has its certificate
```

Then, on the server, the contact name Nominatim/NWS see (optional, no secrets on these lines):

```bash
ssh deploy@45.76.57.234
cd ~/app
cp -p .env ".env.bak-$(date +%Y%m%d-%H%M)"          # rollback copy (stays chmod 600)
sed -i '/^NWS_USER_AGENT=/s|45-76-57-234\.sslip\.io|thefloodfather.surf|' .env
grep -E '^NWS_USER_AGENT=' .env && stat -c '%a %n' .env   # must still say 600
sudo docker compose up -d --force-recreate api        # picks up the new NWS_USER_AGENT; a few seconds
sudo docker compose logs --since 10m caddy | grep -iE "certificate obtained|error" | tail -5
```

You want `certificate obtained successfully` for `thefloodfather.surf`. If you see a `challenge` or `rate` error,
fix DNS first (step 1) and do not keep recreating Caddy.

## 3. Health check (from your Mac)

```bash
curl -s https://thefloodfather.surf/health                         # {"ok":true,...}
curl -sI http://thefloodfather.surf/ | grep -iE '^(HTTP|location)'  # 308 -> https://thefloodfather.surf/
echo | openssl s_client -connect thefloodfather.surf:443 -servername thefloodfather.surf 2>/dev/null | openssl x509 -noout -subject -issuer -enddate
curl -s https://45-76-57-234.sslip.io/health                       # old name still works during the cutover
bash scripts/deploy.sh                                               # full check against the new name: ends with DEPLOYED OK
```

## 4. Switch the voice agent (ElevenLabs dashboard)

- In all 5 webhook tools, change the base URL to `https://thefloodfather.surf` (paths unchanged, see `docs/elevenlabs-tools.md`).
- Agent → **Security** → allowlist: add `thefloodfather.surf` (the website widget only works on allowed hosts if an allowlist is set).
- Test one call from the website and one tool call.

## 5. Drop the old name (after the agent is switched)

In the Caddyfile, change the site address to `thefloodfather.surf {`, then `bash scripts/deploy.sh` (zero-downtime reload).

## Rollback

Old name back as the only name (DNS can stay as it is; the sslip.io name never depended on it): change the
Caddyfile's site address to `45-76-57-234.sslip.io {` and run
`DEPLOY_DOMAIN=45-76-57-234.sslip.io bash scripts/deploy.sh`. (If you changed `NWS_USER_AGENT`, restore it from the
`.env.bak-*` copy on the server and `sudo docker compose up -d --force-recreate api`.)

Then point the ElevenLabs tools back at `https://45-76-57-234.sslip.io`, and run deploys with
`DEPLOY_DOMAIN=45-76-57-234.sslip.io bash scripts/deploy.sh` until the new name is fixed.

## Notes

- Nothing in the app is tied to the hostname: no CORS or allowed-hosts setting, the website only calls its own
  origin (relative URLs), and the CSP uses `'self'`. Only Caddy (certificate) and the places above use the name.
- `scripts/deploy.sh` checks `https://thefloodfather.surf` by default. Until step 3 passes, use
  `DEPLOY_DOMAIN=45-76-57-234.sslip.io bash scripts/deploy.sh`.
- SSH keeps using the IP (`deploy@45.76.57.234`); it does not depend on DNS.
