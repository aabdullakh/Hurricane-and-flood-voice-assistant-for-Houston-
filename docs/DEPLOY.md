# Deploying and running the backend

Server: Vultr, Ubuntu 24.04, `45.76.57.234`, served at **https://45-76-57-234.sslip.io**.
App directory on the server: `/home/deploy/app` (owned by `deploy`). No secrets in this file; they live only in the server's `/home/deploy/app/.env` (chmod 600).

## How it's set up

- **Login:** `ssh deploy@45.76.57.234` with your SSH key. Root login and password login are disabled. `deploy` uses `sudo` with its own password (key-only SSH, password only for sudo).
- **Firewall:** UFW allows only 22, 80, 443. fail2ban bans an IP for 1 h after 5 failed SSH logins in 10 min.
- **Updates:** Ubuntu security updates install automatically (`unattended-upgrades`).
- **Containers** (`docker compose`): `caddy` (HTTPS, the only container with public ports), `api` (FastAPI, non-root uid 10001, read-only filesystem, no capabilities, port 8000 internal only), `db` (Postgres, internal only, data in the `app_pgdata` volume).
- **HTTPS:** Caddy gets and renews the Let's Encrypt certificate by itself. http:// redirects to https://.
- **Production switches in `.env`:** `ALLOW_INSECURE_DEV=0`, `ENABLE_DOCS=0` (/docs, /redoc, /openapi.json return 404), `ENABLE_ADMIN=0` (/admin/* return 404, so the server always runs live data, never replay).
- **Bad API keys:** 10 wrong keys from one IP within 5 minutes → that IP gets HTTP 429 for the rest of the window (`AUTH_FAIL_LIMIT`, `AUTH_FAIL_WINDOW_SECONDS`).

Docker commands need `sudo` (deploy is deliberately not in the `docker` group, which would be the same as root without a password).

## Update to a new version

From your Mac, in the project folder (copies code only, never your local `.env`, database or git data):

```bash
rsync -av \
  --include='.env.example' --exclude='.env' --exclude='.env.*' \
  --exclude='.git/' --exclude='.git-backups/' --exclude='.idea/' --exclude='.vscode/' --exclude='.venv/' \
  --exclude='__pycache__/' --exclude='.pytest_cache/' --exclude='.DS_Store' --exclude='*.db' --exclude='Backend/' \
  ./ deploy@45.76.57.234:app/
```

Run the tests locally first (`cd api && python -m pytest -q`). Then on the server:

```bash
ssh deploy@45.76.57.234
cd ~/app
sudo docker compose up -d --build      # rebuilds the api image and replaces only what changed
sudo docker compose ps                 # api should become "healthy" within ~30 s
curl -s https://45-76-57-234.sslip.io/health
```

## Restart

```bash
sudo docker compose restart            # all containers (data is kept: it's in Docker volumes)
sudo docker compose restart api        # just the API
```

`restart` does **not** re-read `.env`. After editing `.env`, use `sudo docker compose up -d --force-recreate api` instead.

## Logs

```bash
sudo docker compose logs -f api              # live API log (Ctrl+C to stop)
sudo docker compose logs --since 1h caddy    # HTTPS / certificate messages
sudo docker compose ps                       # status and health
sudo fail2ban-client status sshd             # banned SSH attackers
sudo ufw status verbose                      # firewall rules
```

Container logs are rotated automatically (3 × 10 MB per container).

## Rotate the API key

Do this if the key may have leaked, or on a schedule. The old key stops working immediately, so have the ElevenLabs dashboard open to paste the new one.

```bash
ssh deploy@45.76.57.234
cd ~/app
NEW=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
sed -i "s|^API_SHARED_SECRET=.*|API_SHARED_SECRET=$NEW|" .env && unset NEW
stat -c '%a %n' .env                                  # must still say 600
sudo docker compose up -d --force-recreate api       # load the new key
exit
```

Copy the new key to your Mac clipboard without showing it:

```bash
ssh deploy@45.76.57.234 "grep ^API_SHARED_SECRET= ~/app/.env | cut -d= -f2-" | tr -d '\n' | pbcopy
```

Then paste it into the `X-API-Key` secret in ElevenLabs, and copy something else to clear the clipboard.

**Do not rotate `PHONE_HASH_SALT`** unless you want every saved household profile to be forgotten: profiles are keyed by a hash of the phone number made with this salt.

## Add the Gemini key later

Edit `~/app/.env` on the server (`nano .env`), set `GEMINI_API_KEY=...`, save, then `sudo docker compose up -d --force-recreate api`.

## If HTTPS ever fails

`sudo docker compose logs caddy | grep -i -E "error|rate"`. sslip.io names get their own Let's Encrypt limits; if Let's Encrypt refuses, Caddy automatically tries ZeroSSL. Long-term fix: a real domain with an A record pointing at `45.76.57.234`, then set `DOMAIN=` in `.env` and `sudo docker compose up -d --force-recreate caddy`.

## Locked out?

Use the **Console** button for the server in the Vultr dashboard (works even if SSH is broken). Log in there as `deploy` with the sudo password.
