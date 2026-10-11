#!/usr/bin/env bash
# Deploy the dashboard (web/), the Caddyfile and docker-compose.yml to the server in one command.
#
#   bash scripts/deploy.sh                  # deploy web/ + Caddyfile + compose (sudo asks for your password once)
#   bash scripts/deploy.sh --api            # ALSO ship the backend (api/) and rebuild only the api container
#   bash scripts/deploy.sh --dry-run        # show what would change; touches nothing on the server
#   bash scripts/deploy.sh --api --dry-run
#
# What it does:
#   1. Refuses to run if a secret could be copied (.env, keys, certificates, databases, dotfiles,
#      symlinks, or any file containing a value from your local .env or a private-key/API-key pattern).
#   2. Compares SHA-256 checksums of every file, local vs server, copies only what differs, and then
#      re-reads the server's checksums to prove the copy worked.
#   3. Restarts only what's needed:
#      - web/: nothing. The api container serves ~/app/web from a read-only mount, so changes are live.
#      - Caddyfile: validated inside the running Caddy, then a zero-downtime `caddy reload`. If the check
#        fails, the previous Caddyfile is put back and Caddy keeps running unchanged.
#      - docker-compose.yml (or api missing the web/ mount): `docker compose up -d --no-build`, which
#        recreates only containers whose settings changed (a few seconds of downtime for those).
#      - api/ (only with --api): `docker compose up -d --build api` rebuilds and recreates the api container only
#        (a few seconds of API downtime; the database and Caddy keep running).
#   4. Checks from outside over HTTPS: /health, the security headers match the Caddyfile, every web/ file is
#      served byte-for-byte as your local copy, the live page contains the voice widget, and the website's
#      /public/* backend endpoints answer (if they don't, run again with --api).
#   Ends with "DEPLOYED OK" or "DEPLOY FAILED: <reason>".
#
# This script never prints secrets and never copies .env.
set -Eeuo pipefail

HOST="deploy@45.76.57.234"
APP_DIR="app"                       # relative to deploy's home: ~/app
DOMAIN="45-76-57-234.sslip.io"
BASE="https://$DOMAIN"
DRY_RUN=0; API=0
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --api) API=1 ;;
    *) echo "usage: bash scripts/deploy.sh [--api] [--dry-run]"; exit 2 ;;
  esac
done

cd "$(dirname "$0")/.."             # repo root, wherever the script is called from
WORK="$(mktemp -d)"; chmod 700 "$WORK"   # private; only holds the secret-value list during the secret check
trap 'rm -rf "$WORK"' EXIT

fail() { echo; echo "DEPLOY FAILED: $*"; exit 1; }
trap 'fail "unexpected error at line $LINENO (exit $?)"' ERR
step() { echo "==> $*"; }
sum() { shasum -a 256 "$1" | cut -d' ' -f1; }

for f in web/index.html Caddyfile docker-compose.yml; do [ -f "$f" ] || fail "missing $f (run from the project)"; done
if [ "$API" = 1 ]; then for f in api/Dockerfile api/requirements.txt api/app/main.py; do [ -f "$f" ] || fail "missing $f"; done; fi
# api/ files that get shipped: everything except Python caches.
api_find() { find api \( -name __pycache__ -o -name .pytest_cache \) -prune -o -not -name '*.pyc' -not -name .DS_Store \( -type f -o -type l \) -print0; }

# ---------------------------------------------------------------- 1. secret guard
step "Checking that nothing secret would be copied"
FILES=()
while IFS= read -r -d '' f; do FILES+=("$f"); done < <(find web -not -name .DS_Store \( -type f -o -type l \) -print0)
FILES+=(Caddyfile docker-compose.yml)
if [ "$API" = 1 ]; then while IFS= read -r -d '' f; do FILES+=("$f"); done < <(api_find); fi

bad=()
for f in "${FILES[@]}"; do
  base="$(basename "$f")"
  if [ -L "$f" ]; then bad+=("$f (symlink)"); continue; fi
  case "$base" in
    .*) bad+=("$f (hidden file)") ;;
    *.env|*.env.*|*.pem|*.key|*.p12|*.pfx|*.crt|*.cer|*.der|*.jks|*.kdbx|*.db|*.sqlite|*.sqlite3|*.db-*|id_rsa*|id_ed25519*|id_ecdsa*|*secret*|*credential*|*password*)
      bad+=("$f (secret-looking file name)") ;;
  esac
done
[ ${#bad[@]} -eq 0 ] || { printf '   refusing: %s\n' "${bad[@]}"; fail "secret-looking files would be copied"; }

# Content: private keys / common API-key formats, and the actual values in your local .env (never printed).
pattern_hits="$(grep -lE -e '-----BEGIN [A-Z ]*PRIVATE KEY-----' -e 'AKIA[0-9A-Z]{16}' -e 'sk_(live|test)_[0-9A-Za-z]{10,}' \
                       -e 'xi-api-key' -e 'AIza[0-9A-Za-z_-]{30,}' -- "${FILES[@]}" 2>/dev/null || true)"
# shellcheck disable=SC2086  # word-splitting the file list is intended here
[ -z "$pattern_hits" ] || { printf '   refusing (key-like content): %s\n' $pattern_hits; fail "a file contains something that looks like a key"; }
if [ -f .env ]; then
  # Values of KEY/SECRET/SALT/PASSWORD/TOKEN settings, 12+ characters, one per line, in a private temp file.
  secrets_list="$WORK/values"
  sed -nE 's/^[A-Z0-9_]*(KEY|SECRET|SALT|PASSWORD|TOKEN)[A-Z0-9_]*=//p' .env | tr -d '\r' | sed -E "s/^[\"']//; s/[\"']$//" \
    | awk 'length($0) >= 12' > "$secrets_list"
  if [ -s "$secrets_list" ]; then
    value_hits="$(grep -lF -f "$secrets_list" -- "${FILES[@]}" 2>/dev/null || true)"
    # shellcheck disable=SC2086
    [ -z "$value_hits" ] || { rm -f "$secrets_list"; printf '   refusing (contains a value from .env): %s\n' $value_hits; fail "a file contains a secret from your .env"; }
  fi
  rm -f "$secrets_list"
fi
echo "   ${#FILES[@]} files checked, nothing secret"

# Optional early check of the Caddyfile on your Mac (if Caddy is installed locally).
if command -v caddy >/dev/null 2>&1; then
  DOMAIN="$DOMAIN" caddy validate --config Caddyfile --adapter caddyfile >/dev/null 2>&1 || fail "Caddyfile is invalid (run: DOMAIN=$DOMAIN caddy validate --config Caddyfile)"
  echo "   Caddyfile valid (checked locally)"
fi

# ---------------------------------------------------------------- 2. what differs (checksums, local vs server)
# Manifest lines: "<sha256>  ./<path>" for web/ files, "<sha256>  <name>" for the two config files.
local_web() { (cd web && find . -type f ! -name .DS_Store -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256); }
local_api() { (cd api && find . \( -name __pycache__ -o -name .pytest_cache \) -prune -o -type f ! -name '*.pyc' ! -name .DS_Store -print0 | LC_ALL=C sort -z | xargs -0 shasum -a 256); }
remote_state() {
  ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" \
    "cd ~/$APP_DIR && test -f .env && sha256sum Caddyfile docker-compose.yml && echo '--web--' && (cd web && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum) && echo '--api--' && (cd api && find . \\( -name __pycache__ -o -name .pytest_cache \\) -prune -o -type f ! -name '*.pyc' -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum)" \
    || fail "can't read the server state (is $HOST reachable, and does ~/$APP_DIR/.env exist?)"
}
paths_of() { sed -E 's/^[0-9a-f]{64}  \.\///'; }

compare() {   # sets CADDY_CHANGED, COMPOSE_CHANGED, WEB_UPDATED (new/changed paths), WEB_DELETED, WEB_CHANGED, API_CHANGED
  local state lw rw la ra
  state="$(remote_state)"
  CADDY_CHANGED=1; COMPOSE_CHANGED=1; WEB_CHANGED=0; API_CHANGED=0; API_DIFF=""
  [ "$(sum Caddyfile)" = "$(printf '%s\n' "$state" | awk '$2=="Caddyfile"{print $1}')" ] && CADDY_CHANGED=0
  [ "$(sum docker-compose.yml)" = "$(printf '%s\n' "$state" | awk '$2=="docker-compose.yml"{print $1}')" ] && COMPOSE_CHANGED=0
  lw="$(local_web)"
  rw="$(printf '%s\n' "$state" | sed -n '/^--web--$/,/^--api--$/p' | sed '1d;$d')"
  if [ "$API" = 1 ]; then
    la="$(local_api)"
    ra="$(printf '%s\n' "$state" | sed -n '/^--api--$/,$p' | sed '1d')"
    API_DIFF="$( { comm -23 <(printf '%s\n' "$la" | LC_ALL=C sort) <(printf '%s\n' "$ra" | LC_ALL=C sort) | paths_of;
                   comm -13 <(printf '%s\n' "$la" | paths_of | LC_ALL=C sort) <(printf '%s\n' "$ra" | paths_of | LC_ALL=C sort); } | LC_ALL=C sort -u)"
    [ -n "$API_DIFF" ] && API_CHANGED=1
  fi
  WEB_UPDATED="$(comm -23 <(printf '%s\n' "$lw" | LC_ALL=C sort) <(printf '%s\n' "$rw" | LC_ALL=C sort) | paths_of)"
  WEB_DELETED="$(comm -13 <(printf '%s\n' "$lw" | paths_of | LC_ALL=C sort) <(printf '%s\n' "$rw" | paths_of | LC_ALL=C sort))"
  [ -n "$WEB_UPDATED$WEB_DELETED" ] && WEB_CHANGED=1
  return 0
}
show() { [ -n "$2" ] && printf '%s\n' "$2" | sed "s|^|   $1 web/|"; return 0; }

step "Comparing checksums with the server"
compare
[ "$CADDY_CHANGED" = 1 ] && echo "   changed  Caddyfile"
[ "$COMPOSE_CHANGED" = 1 ] && echo "   changed  docker-compose.yml"
show "changed " "$WEB_UPDATED"
show "deleted " "$WEB_DELETED"
[ -n "$API_DIFF" ] && printf '%s\n' "$API_DIFF" | sed 's|^|   changed  api/|'
[ "$CADDY_CHANGED$COMPOSE_CHANGED$WEB_CHANGED$API_CHANGED" = 0000 ] && echo "   server files already match your local copies"

if [ "$DRY_RUN" = 1 ]; then
  echo; echo "Dry run: Caddyfile changed=$CADDY_CHANGED, docker-compose.yml changed=$COMPOSE_CHANGED, web changed=$WEB_CHANGED$([ "$API" = 1 ] && echo ", api changed=$API_CHANGED")."
  echo "Nothing was changed on the server. (A real run also recreates api if it lacks the web/ mount.)"
  exit 0
fi

# ---------------------------------------------------------------- 3. copy what differs, then prove it
if [ "$CADDY_CHANGED$COMPOSE_CHANGED$WEB_CHANGED$API_CHANGED" != 0000 ]; then
  step "Copying changed files"
  # Keep the current Caddyfile so a bad one can be rolled back.
  ssh -o BatchMode=yes "$HOST" "cp -p ~/$APP_DIR/Caddyfile ~/$APP_DIR/.Caddyfile.prev"
  # Only these three paths are ever sent (never .env). web/ is mirrored; the Caddyfile is written in place so the
  # running Caddy container (which mounts that one file) sees the new content.
  if [ "$WEB_CHANGED" = 1 ]; then rsync -rc --delete --exclude=.DS_Store web/ "$HOST:$APP_DIR/web/"; fi
  if [ "$CADDY_CHANGED" = 1 ]; then rsync -c --inplace Caddyfile "$HOST:$APP_DIR/Caddyfile"; fi
  if [ "$COMPOSE_CHANGED" = 1 ]; then rsync -c docker-compose.yml "$HOST:$APP_DIR/docker-compose.yml"; fi
  if [ "$API_CHANGED" = 1 ]; then rsync -rc --delete --exclude=__pycache__/ --exclude=.pytest_cache/ --exclude='*.pyc' --exclude=.DS_Store api/ "$HOST:$APP_DIR/api/"; fi
  copied="$CADDY_CHANGED$COMPOSE_CHANGED$WEB_CHANGED$API_CHANGED"
  compare
  [ "$CADDY_CHANGED$COMPOSE_CHANGED$WEB_CHANGED$API_CHANGED" = 0000 ] || fail "after copying, the server's files still differ from yours"
  echo "   copy verified: server checksums now match your local files"
  CADDY_CHANGED="${copied:0:1}"; COMPOSE_CHANGED="${copied:1:1}"; WEB_CHANGED="${copied:2:1}"; API_CHANGED="${copied:3:1}"
fi

# The api container (uid 10001) reads the mounted web/ files: make them world-readable. Not secret.
ssh -o BatchMode=yes "$HOST" "find ~/$APP_DIR/web -type d -exec chmod 755 {} + && find ~/$APP_DIR/web -type f -exec chmod 644 {} + && chmod 644 ~/$APP_DIR/Caddyfile"

# ---------------------------------------------------------------- 4. apply on the server (sudo asks once)
read -r -d '' REMOTE <<'EOS' || true
set -Eeuo pipefail
cd ~/app
say()  { echo "   [server] $*"; }
die()  { echo "   [server] ERROR: $*"; exit 1; }
restore_caddyfile() { [ -f .Caddyfile.prev ] && cat .Caddyfile.prev > Caddyfile && say "put the previous Caddyfile back; Caddy keeps running unchanged"; }
web_mounted() {
  local id; id="$(sudo docker compose ps -q api)"
  [ -n "$id" ] && [ "$(sudo docker inspect "$id" --format '{{range .Mounts}}{{if eq .Destination "/srv/web"}}yes{{end}}{{end}}')" = yes ]
}

sudo -v || die "sudo was not accepted"
if [ "$API_DEPLOY" = 1 ]; then
  sudo docker compose config --quiet || die "docker-compose.yml is invalid"
  say "rebuilding the api image and recreating only the api container"
  sudo docker compose up -d --build api 2>&1 | grep -E "Built|Recreat|Start|rror|ERROR" | sed 's/^/   [server] /' || true
fi
[ -n "$(sudo docker compose ps -q api)" ] || die "api container is not running"

if [ "$COMPOSE_CHANGED" = 1 ] || ! web_mounted; then
  sudo docker compose config --quiet || die "docker-compose.yml is invalid"
  say "applying docker-compose.yml (only containers whose settings changed are recreated)"
  sudo docker compose up -d --no-build 2>&1 | grep -E "Recreat|Start|Running|rror" | sed 's/^/   [server] /' || true
  web_mounted || die "api still doesn't have the web/ mount; is ./web:/srv/web:ro in docker-compose.yml?"
  say "api serves ~/app/web from the read-only mount"
fi

host_sum="$(sha256sum Caddyfile | cut -d' ' -f1)"
cont_sum="$(sudo docker compose exec -T caddy sha256sum /etc/caddy/Caddyfile | cut -d' ' -f1)"
if [ "$CADDY_CHANGED" = 1 ] || [ "$host_sum" != "$cont_sum" ]; then
  if [ "$host_sum" = "$cont_sum" ]; then
    sudo docker compose exec -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 \
      || { restore_caddyfile; die "new Caddyfile failed validation"; }
    sudo docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 \
      || { restore_caddyfile; sudo docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 || true; die "caddy reload failed"; }
    say "Caddyfile valid; Caddy reloaded with no downtime"
  else
    # The running container still has an older copy of the file mounted (it was replaced, not edited in place).
    sudo docker compose run --rm --no-deps -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 \
      || { restore_caddyfile; die "new Caddyfile failed validation"; }
    say "Caddyfile valid; running Caddy had an old copy mounted, recreating it (about 2 seconds)"
    sudo docker compose up -d --no-build --force-recreate caddy 2>&1 | grep -E "Recreat|Start|rror" | sed 's/^/   [server] /' || true
  fi
else
  say "Caddyfile unchanged; Caddy left alone"
fi
if [ "$WEB_CHANGED" = 1 ]; then say "web/ updated; served live from the mounted folder, no restart"; fi

for _ in $(seq 1 45); do
  api="$(sudo docker compose ps api --format '{{.Health}}')"; caddy="$(sudo docker compose ps caddy --format '{{.State}}')"
  if [ "$api" = healthy ] && [ "$caddy" = running ]; then say "api healthy, caddy running"; exit 0; fi
  sleep 2
done
die "containers not healthy after 90 s (api: ${api:-?}, caddy: ${caddy:-?}). Logs: sudo docker compose logs --tail 50 api caddy"
EOS

step "Applying on the server (sudo will ask for your password)"
ssh -t -o ConnectTimeout=15 "$HOST" \
  "API_DEPLOY=$API COMPOSE_CHANGED=$COMPOSE_CHANGED CADDY_CHANGED=$CADDY_CHANGED WEB_CHANGED=$WEB_CHANGED bash -c $(printf '%q' "$REMOTE")" \
  || fail "server step failed (see the [server] lines above)"

# ---------------------------------------------------------------- 5. check from outside
step "Checking $BASE"
ok=0
for _ in $(seq 1 15); do
  case "$(curl -sS -m 10 "$BASE/health" 2>/dev/null || true)" in *'"ok":true'*) ok=1; break ;; esac
  sleep 2
done
[ "$ok" = 1 ] || fail "/health did not return ok over HTTPS"
echo "   /health ok over valid HTTPS"

# Headers: what the server sends must be exactly what the Caddyfile says (HTTP/2 header names are lowercase).
headers="$(curl -sS -m 10 -D - -o /dev/null "$BASE/" | tr -d '\r')"
for h in Content-Security-Policy Permissions-Policy Referrer-Policy; do
  want="$(sed -nE "s/^[[:space:]]*$h \"(.*)\"[[:space:]]*$/\1/p" Caddyfile | head -1)"
  lower="$(printf '%s' "$h" | tr 'A-Z' 'a-z')"
  got="$(printf '%s\n' "$headers" | sed -nE "s/^$lower: (.*)$/\1/p" | head -1)"
  [ -n "$want" ] && [ "$want" = "$got" ] || fail "server's $h header doesn't match the Caddyfile"
done
echo "   security headers match the Caddyfile"

# Content: every web/ file must be served with exactly the local bytes (proves the mount, not the image copy).
n=0
while IFS= read -r path; do
  [ "$(curl -sS -m 15 "$BASE/$path" | shasum -a 256 | cut -d' ' -f1)" = "$(sum "web/$path")" ] \
    || fail "server serves an old or different /$path (still the copy baked into the image?)"
  n=$((n + 1))
done < <(local_web | paths_of)
echo "   all $n web files served with exactly your local content"

# The live page must contain the voice widget, its pinned script, and its self-hosted audio processors.
page="$(curl -sS -m 15 "$BASE/")"
agent="$(sed -nE 's/.*<elevenlabs-convai agent-id="([^"]+)".*/\1/p' web/index.html | head -1)"
[ -n "$agent" ] || fail "web/index.html has no <elevenlabs-convai agent-id=...> (nothing to check)"
printf '%s' "$page" | grep -qF "<elevenlabs-convai agent-id=\"$agent\"" || fail "live page has no voice widget"
# The widget script is injected by assets/app.js (not a <script> tag in the HTML). Check that chain end to end:
# page loads app.js -> app.js pins the widget URL + SRI hash -> that URL really has that hash -> the CSP allows it.
printf '%s' "$page" | grep -qF '<script src="/assets/app.js"' || fail "live page doesn't load /assets/app.js (which loads the widget)"
appjs="$(curl -sS -m 15 "$BASE/assets/app.js")"
wsrc="$(printf '%s' "$appjs" | sed -nE 's/.*WIDGET_SRC = "([^"]+)".*/\1/p' | head -1)"
wsri="$(printf '%s' "$appjs" | sed -nE 's/.*WIDGET_SRI = "([^"]+)".*/\1/p' | head -1)"
[ -n "$wsrc" ] && [ -n "$wsri" ] || fail "live assets/app.js doesn't pin the widget script (WIDGET_SRC / WIDGET_SRI)"
got_sri="sha384-$(curl -sS -m 30 "$wsrc" | openssl dgst -sha384 -binary | openssl base64 -A)"
[ "$got_sri" = "$wsri" ] || fail "the widget file at $wsrc no longer matches its integrity hash: browsers will refuse to run it"
csp_script="$(printf '%s\n' "$headers" | sed -nE 's/^content-security-policy: .*script-src ([^;]*).*/\1/p' | head -1)"
csp_ok=0
for src in $csp_script; do case "$wsrc" in "$src"*) csp_ok=1 ;; esac; done
[ "$csp_ok" = 1 ] || fail "the Content-Security-Policy script-src doesn't allow $wsrc"
echo "   widget script: loaded by app.js, integrity hash matches, allowed by the CSP"
w=0
while IFS= read -r wpath; do
  [ "$(curl -sS -m 10 -o /dev/null -w '%{http_code}' "$BASE$wpath")" = 200 ] || fail "widget audio processor $wpath is not served"
  w=$((w + 1))
done < <(printf '%s' "$page" | sed -nE 's/.*worklet-path-[a-z-]+="([^"]+)".*/\1/p')
echo "   live page contains the voice widget (agent $agent) and its $w audio processors"

# The website reads these public, read-only endpoints. If they're missing, the server runs an older backend.
code="$(curl -sS -m 15 -o /dev/null -w '%{http_code}' "$BASE/public/shelters")"
[ "$code" = 200 ] || fail "/public/shelters returned $code: the server's backend is older than the website. Run: bash scripts/deploy.sh --api"
curl -sS -m 30 "$BASE/public/conditions?lat=29.76&lon=-95.37" | grep -q '"risk"' || fail "/public/conditions doesn't answer"
[ "$(curl -sS -m 20 -o /dev/null -w '%{http_code} %{content_type}' "$BASE/public/tiles/12/962/1695.png")" = "200 image/png" ] || fail "map tiles (/public/tiles) don't answer"
echo "   website endpoints answer: /public/shelters, /public/conditions, /public/tiles"

echo
echo "DEPLOYED OK"
