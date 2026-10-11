# Houston Flood & Hurricane Voice Assistant

A phone-first, multilingual voice assistant for Houston residents. The caller talks to an **ElevenLabs voice agent** (Gemini as its brain). The agent calls this **backend** to get nearby gauge readings and weather alerts, shelters, and a consented household profile, then explains it in plain words.

> **Prototype decision-support. Not official guidance.** It always states how old its data is, never says an area is "safe", and tells callers to call 911 in immediate danger.

## Folder layout

```
orion-flood/
├── README.md                 you are here
├── .env.example              copy to .env, fill in (never commit .env)
├── docker-compose.yml        api + postgres + caddy (automatic HTTPS)
├── Caddyfile
├── api/                      the backend (FastAPI)
│   ├── Dockerfile  requirements.txt  requirements-dev.txt  pytest.ini
│   ├── app/
│   │   ├── main.py           all endpoints
│   │   ├── config.py         settings from environment
│   │   ├── db.py             tables: gauge readings, profiles, call summaries
│   │   ├── security.py       API-key check, phone-number hashing
│   │   ├── sources/usgs.py   USGS gauge heights (live data)
│   │   ├── sources/nws.py    National Weather Service alerts
│   │   ├── geo.py            distance + address geocoding
│   │   ├── risk.py           conservative risk rules
│   │   ├── series.py         shared gauge structure + trend maths
│   │   ├── service.py        builds the answer the agent speaks from
│   │   ├── poller.py         background fetch + cache
│   │   ├── replay.py         demo replay mode
│   │   ├── memory.py         Backboard memory (optional, untested)
│   │   └── triage.py         Gemini post-call triage
│   └── tests/                36 tests
├── agent/
│   ├── system_prompt.md      paste into the ElevenLabs agent
│   └── tools.json            how to set up the 5 webhook tools
├── data/
│   ├── shelters.json         EMPTY on purpose, fill from official sources
│   └── replay/sample_synthetic.json   fictional test storm
├── scripts/
│   ├── smoke.sh              test the outside data sources from your network
│   ├── demo.sh               switch live/replay and step through a storm
│   └── fetch_replay.py       build a REAL-storm replay from USGS history
├── web/index.html            dashboard map (served at /)
└── docs/PLAN.md              the full build plan
```

## Run it locally (5 minutes)

Needs Python 3.11 or newer.

```bash
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r api/requirements-dev.txt
cp .env.example .env
# edit .env: set API_SHARED_SECRET and PHONE_HASH_SALT (or set ALLOW_INSECURE_DEV=1 for local only)

cd api
python -m pytest -q                       # expect: 36 passed
uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 for the dashboard and http://localhost:8000/docs for the interactive API docs.

### Try the demo with no internet and no storm

```bash
export BASE=http://localhost:8000 KEY=<your API_SHARED_SECRET>
bash scripts/demo.sh on sample_synthetic 0     # switch to the fictional storm
curl -H "X-API-Key: $KEY" "$BASE/flood-status?lat=29.76&lon=-95.37"
bash scripts/demo.sh step 1                    # advance an hour, ask again, risk escalates
bash scripts/demo.sh off                       # back to live data
```

## FIRST THING TO DO on your own network

My build environment could not reach USGS, NWS or the Census geocoder, so the live calls are **untested**. Run:

```bash
bash scripts/smoke.sh
```

Each line should say OK. If USGS fails, check the `countyCd` parameter name in `api/app/sources/usgs.py`. If the geocoder fails, check the benchmark name in `api/app/geo.py`.

## Endpoints

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /health` | none | status, mode, last poll |
| `GET /overview` | none | public data for the dashboard |
| `GET /flood-status?address=...` or `?lat=&lon=` | key | risk, nearest gauges, alerts, `speak_hint` |
| `GET /shelters?lat=&lon=` | key | nearest shelters from `data/shelters.json` |
| `POST /profile` | key | save household facts, **consent required** |
| `POST /profile/lookup` | key | what we remember about this caller |
| `POST /profile/forget` | key | delete it |
| `POST /post-call` | key | Gemini triage of a transcript (transcript is not stored) |
| `GET /calls` | key | recent triage results |
| `POST /admin/mode`, `/admin/replay/step` | key | live/replay switch for demos |

"key" means header `X-API-Key: <API_SHARED_SECRET>`.

## Connect the ElevenLabs agent

1. Create an agent, choose a **Gemini Flash** model as its LLM, paste `agent/system_prompt.md`.
2. Add languages (English and Spanish first). Check whether Vietnamese appears in the dropdown.
3. Enable the **language detection** tool.
4. Add the five webhook tools from `agent/tools.json` (needs your public HTTPS domain). Store the API key as a secret header.
5. Import a Twilio number (Phone Numbers tab) and assign the agent. **Twilio trial numbers only accept verified callers**, so upgrade, or use the agent's web widget for judges.

## Deploy to Vultr

1. Claim the $100 credit (`mlh.link/vultr-signup`, then `mlh.link/vultr-giftcode`, gift code from Opening Ceremony).
2. Create a Cloud Compute VM (Ubuntu), open ports 22, 80, 443. Point your domain's **A record** at its IP.
3. On the VM:
   ```bash
   curl -fsSL https://get.docker.com | sh
   git clone <your repo> && cd <repo>
   cp .env.example .env && nano .env     # set every value, including POSTGRES_PASSWORD (site names are in the Caddyfile)
   docker compose up -d --build
   ```
4. Check `https://<your domain>/health`. Caddy gets the HTTPS certificate automatically.

## What is and is not verified

| Item | Status |
|---|---|
| Parsing, trend maths, risk rules, auth, consent, phone hashing, replay, triage fallback | **Tested** (36 tests pass) |
| Full local run in replay mode, dashboard served | **Tested** |
| Live USGS / NWS / Census calls | **Untested**, run `scripts/smoke.sh` |
| `countyCd` parameter and NWS `?point=` query | **Untested**, verify with `smoke.sh` |
| Docker build, Postgres, Caddy HTTPS | **Untested** |
| Gemini post-call triage with a real key | **Untested** (keyword fallback is tested) |
| Backboard memory | **Untested**, and per-user scoping is unconfirmed (one assistant per household) |
| ElevenLabs tool wiring, caller-ID variable, Twilio | **Untested**, written from the docs |

## Known limits (be honest about these in your pitch)

- **Risk is a heuristic.** No official flood-stage thresholds per gauge yet. Risk comes from NWS alerts and how fast a gauge is rising. When data is missing or stale it says `unknown`, never `low`.
- **USGS data lags** (readings roughly every 15 minutes, transmitted about hourly).
- **Shelters list is empty** until you fill `data/shelters.json` from official sources. Do not invent addresses.
- **No rate limiting** and a single server process. Fine for a demo. Add both before real use.
- **"Forget me" deletes our copy only.** Deleting the Backboard assistant is not implemented.
- **Logs:** web servers log query strings. Addresses appear in `/flood-status?address=` logs. Phone numbers are sent in request bodies on purpose so they are not logged.
- **Dashboard** loads Leaflet and map tiles from the internet.

## Hackathon rules reminder

Code must be written during the event, and some hackathons require disclosing AI assistance. Check the rules at the opening ceremony. Make your first commit after hacking starts, and commit as yourselves.
