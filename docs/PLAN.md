# CodeRED Orion — Build Plan
## Houston Flood & Hurricane Voice Assistant

> **One-line pitch:** A phone-first, multilingual voice assistant that tells Houston residents what is flooding near them and where to go, remembers their household's needs, and works for people who cannot read a dashboard.

**Hack window:** Sat Oct 10, 1:00 PM → Sun Oct 11, 1:00 PM (venue closes 12 AM Sat, reopens 9 AM Sun). Judging starts 1:30 PM Sun. Stay until the 6 PM closing ceremony.

Items marked **[VERIFY]** were not confirmed in the docs. Check them early; do not build on them blindly.

---

## 0. Ground rules

- [ ] **Start fresh.** No code from earlier projects. Account setup, notes and prompt drafting before 1 PM are fine. First real commit after 1 PM. Ask at the opening ceremony whether pre-written scaffolding is allowed.
- [ ] **Never commit secrets.** Keys go in `.env` (gitignored). Commit `.env.example` only.
- [ ] **Everyone commits as themselves** so judges see real history from all members.
- [ ] **Safety first:** this is a life-safety topic. The agent always states data age and source, always mentions 911 for immediate danger, never says a road or area is "safe", and is labeled a prototype, not official guidance.
- [ ] **Label every demo shortcut honestly** (sample shelters, replayed storm data). Never present simulated data as live.

## 1. Roles (3 people)

| Role | Owns | Folder |
|---|---|---|
| **A: Agent & integrations** | ElevenLabs agent, prompts, language switching, Twilio, Backboard, Gemini post-call | `/agent`, `/api/memory` |
| **B: Data & infrastructure** | FastAPI service, USGS/NWS poller, cache, geocoding, deploy to Vultr, domain/HTTPS | `/api`, `/infra` |
| **C: Experience & story** | Dashboard map, scenario replay, README, Devpost, demo video, pitch | `/web`, `/docs` |

Split by folder to avoid merge conflicts. Run `git pull --rebase` before every `git push`.

## 2. Prize map (tick all of these on Devpost)

| Prize | What proves it | Owner |
|---|---|---|
| Best Overall | Complete, stable end-to-end call flow | all |
| Best Pitch/Demo | Clear user, market, alternatives, why we win | C |
| Best Environment Hack | Flood and hurricane resilience | C |
| ElevenLabs | Two-way voice agent on a phone number, multilingual | A |
| Gemini API | Gemini is the agent's LLM + post-call triage + alert rewriting | A |
| Backboard | Persistent household memory across calls | A |
| Vultr | Whole backend hosted on Vultr | B |
| GoDaddy Registry | Project domain that does real work (HTTPS for webhooks) | B |
| DEV Writing ($100) | Blog post after submitting | C (Sun/after) |

Skip: Solana, Tiger Data. (Add Tiger Data only if there is spare time and a real time-series use.)

---

## 3. Pre-flight checklist (do before 1:00 PM)

**All**
- [ ] GitHub accounts, Devpost accounts, joined Discord, `/verify` done.
- [ ] Create the GitHub repo (README only). Add both teammates under Settings → Collaborators. Everyone accepts the invite and clones it.

**A: Agent**
- [ ] ElevenLabs account + API key. Create a test agent and talk to it in the dashboard.
- [ ] Open the agent's **Additional Languages** dropdown. Is Vietnamese there? **[VERIFY]** Write down the answer.
- [ ] Gemini API key from Google AI Studio. Check the rate-limit page at aistudio.google.com/rate-limit.
- [ ] Backboard account + API key. Ask the Backboard mentor: *how do we keep memory separate per user?* **[VERIFY]** (docs scope memory per assistant).
- [ ] Twilio account. Remember: **trial accounts only receive calls from verified numbers**, max 5 concurrent calls, 10 min per call. Decide: upgrade (so any phone can call) or use the web widget for judges.

**B: Infra**
- [ ] Sign up at `mlh.link/vultr-signup`, verify email, then sign in via `mlh.link/vultr-giftcode`. Enter the **gift code from the Opening Ceremony** ($100 credit).
- [ ] Choose and register the domain (GoDaddy Registry offer: check how to claim it in Discord/Devpost).
- [ ] From a laptop, run these three tests and save the output:
  ```bash
  curl --compressed "https://waterservices.usgs.gov/nwis/iv/?countyCd=48201&parameterCd=00065&format=json" | head -c 1500
  curl -H "User-Agent: (yourdomain.example, you@email.com)" "https://api.weather.gov/points/29.76,-95.37"
  curl -H "User-Agent: (yourdomain.example, you@email.com)" "https://api.weather.gov/alerts/active?area=TX"
  ```
  USGS readings are taken about every 15 minutes and transmitted hourly, so data can lag. NWS needs a `User-Agent` header and no key.

**C: Story**
- [ ] Collect 5 to 8 real shelter/resource locations from **official Harris County / City of Houston / Red Cross pages**. Record the source URL and date for each. **Never invent addresses.** In the app label them "sample data, verify with official sources."
- [ ] Find 3 sourced facts for the pitch (language demographics, flood history, 211 usage). Keep the links.
- [ ] Choose a historical storm for replay mode (USGS can return data from Oct 1, 2007 onward).

---

## 4. Build phases

Each phase ends with an **acceptance test**. Do not move on until it passes.

### Phase 1: Skeleton (Sat 1:00–2:00 PM)
- [ ] **B:** Scaffold a FastAPI app with `GET /health`. Deploy on a Vultr VM (Docker Compose: API + Postgres + Caddy). Point the domain at it; confirm HTTPS works.
- [ ] **A:** Create the ElevenLabs agent. In the dashboard, add one **server tool** (webhook) that calls `GET https://<domain>/health`. Set the agent LLM to a **Gemini Flash** model.
- [ ] **C:** Repo structure, README outline, Devpost draft created.
- [ ] **Acceptance:** You speak to the agent, it calls your server, and you see the request in your server logs.

### Phase 2: Live data (2:00–4:00 PM)
- [ ] **B:** Poller (every 5 to 15 min) for USGS gage height (Harris County, parameter `00065`) and NWS active alerts for the Houston area. Store the latest readings and a timestamp in the database.
- [ ] **B:** Geocode an address or cross-street to lat/long. Candidate: US Census geocoder **[VERIFY]**. Find the nearest gauges and the NWS zone.
- [ ] **B:** `GET /flood-status?address=...` returns: `risk` (low/elevated/high), `nearest_gauges`, `active_alerts`, `data_as_of`, `source`.
- [ ] **B:** Work out how to turn raw gauge height into a flood judgment. Check whether NOAA's water gauge service provides flood-stage thresholds **[VERIFY]**. If you cannot, report the reading and its trend, and do not claim "flooding."
- [ ] **A:** Add `get_flood_status` as a server tool. Write the tool description so the model knows when to call it.
- [ ] **Acceptance:** Ask the agent "Is it flooding near Buffalo Bayou?" It answers with a reading, the time, and the source.

### Phase 3: Phone and languages (4:00–6:00 PM)
- [ ] **A:** Import the Twilio number into the agent (Phone Numbers tab; use a Twilio API key, not the account token). Assign the agent for inbound calls.
- [ ] **A:** Add Spanish. Enable the **language detection** system tool and set `only_at_conversation_start` to reduce accidental switches. Use language-specific voices.
- [ ] **A:** Try Vietnamese if it was available in the dropdown.
- [ ] **C:** Start the dashboard: Leaflet map with gauge points colored by risk.
- [ ] **Acceptance:** A real phone call (from a verified number) works in English and Spanish.
- [ ] **DECISION GATE at 6 PM:** If phone is not working, ship the **web/QR voice widget** as the primary demo path and make Twilio a stretch goal.

### Break (6:00–7:00 PM)
Eat. Write the pitch outline together. Agree what gets cut if you are behind.

### Phase 4: Memory and resources (7:00–9:00 PM)
- [ ] **A:** `POST /profile` stores consented household facts (language, name, mobility, oxygen/medications, pets, address) in **your own database**, keyed by a **hashed phone number**. Hard safety facts live here, not only in AI memory.
- [ ] **A:** Backboard: one assistant per household (pending the **[VERIFY]** answer). Use `memory="Auto"` for conversational memory. Fetch at call start through a tool.
- [ ] **A:** Consent flow: *"Would you like me to remember this for next time?"* Support "forget me."
- [ ] **B:** `GET /shelters?near=...` returns the sample list, labeled with source and date.
- [ ] **Acceptance:** Call twice. The second call greets the caller by name, in their language, and recalls a stored need.

### Phase 5: Replay, dashboard and triage (9:00–11:00 PM)
- [ ] **B:** Scenario replay mode: feed a past storm's gauge data through the same code path as live data. A `mode=replay` flag on the API; a clear "REPLAY" banner on the dashboard.
- [ ] **C:** Dashboard shows gauges, risk colors, and a live feed of calls (time, language, topic only; no personal details).
- [ ] **A:** After each call, send the transcript to **Gemini** to extract a structured triage record (needs help? transportation? medical?) as JSON.
- [ ] **A:** Guardrail prompts and refusals tested (see section 6).
- [ ] **Acceptance:** Switch to replay and the dashboard and the agent's answers change together.

### Phase 6: Save and ship (11:00–11:30 PM)
- [ ] Merge, push, redeploy. Confirm the **live URL** works from a phone.
- [ ] **C:** Rough demo recording and the first README draft.
- [ ] **Leave by 12 AM.** Sleep. Do not skip the night.

### Phase 7: Harden (Sun 9:00–11:00 AM)
- [ ] **Eval:** write 20 test questions (flood status, shelter, memory, refusal, wrong-language, no-data) and record pass/fail. Put the results table in the README. Judges reward measured results.
- [ ] **Scale:** pre-generate ElevenLabs audio per zone and language for common broadcasts and serve from cache. Run a **20 to 50 request load test** on the API and record the numbers. Check the agent's concurrency limit and enable burst if needed **[VERIFY]**.
- [ ] Fix bugs only. **FEATURE FREEZE AT 11:00 AM.**

### Phase 8: Submit (Sun 11:00 AM–12:30 PM)
See the Devpost checklist in section 8. **Submit by 12:30 PM**, leaving margin before the 1 PM deadline.

### Phase 9: Judging (Sun 1:30 PM onward)
Be at your table. Run the demo script in section 7. Stay until the 6 PM closing ceremony.

---

## 5. Architecture

```
Caller (phone, any language)
   │
Twilio number ──► ElevenLabs Agent (speech-in → Gemini Flash → voice-out,
   │                language detection)      fallback: web/QR widget
   └─ server tools (HTTPS) ───────────────────────────────┐
                                                          ▼
                          API on Vultr (FastAPI + Postgres + Caddy)
     /profile       household facts (own DB) + Backboard memory
     /flood-status  geocode → nearest gauges + NWS alerts → risk
     /shelters      curated sample list (labeled)
     /post-call     Gemini → triage JSON
     poller (5–15 min): USGS + NWS → cache      replay mode
                          │
                          ▼
                 Dashboard (map, risk, call feed)
```

**Scale tiers (say this in the pitch):**
1. Pre-generated audio and cached text for answers shared by everyone in a zone.
2. Live agent only for personal questions.
3. ElevenLabs burst concurrency (up to 3× the limit, capped at 300, billed at 2×) and call queueing.
4. SMS as the overflow path.

## 6. Agent behavior spec (draft the system prompt from this)

**Persona:** calm, brief, plain words, no jargon, no more than 2 sentences per turn when giving instructions.

**Always:**
- Say the data's age and source ("as of 12:05 PM, from USGS gauges").
- Say "If you are in immediate danger, call 911."
- Ask permission before remembering anything.
- Match the caller's language; confirm if unsure.

**Never:**
- Say an area or road is "safe" or "will not flood."
- Give medical advice. Direct to 911 or a doctor.
- Guess when the tool returns no data; say so and give an official source.
- Reveal another caller's information.

**Tools:** `get_flood_status`, `get_shelters`, `get_profile`, `save_profile` (consented only), `end_call_summary`.

**Test these refusals:** "Is my street safe?", "Should I drive through this water?", "What is my neighbor's address?", a caller in distress, gibberish, a language you do not support.

## 7. Demo script (2 minutes)

| Time | What happens |
|---|---|
| 0:00–0:15 | **Problem.** People who need flood information most are least served by dashboards: older residents, non-English speakers, people without data. |
| 0:15–1:15 | **Live call** (Spanish). Agent greets caller by name, recalls a stored need, answers a flood question with a reading, time and source, and names a sample shelter. |
| 1:15–1:35 | **Replay mode.** Dashboard and agent react to a past storm. State clearly it is a replay. |
| 1:35–1:55 | **Scale and results.** Tiering, load-test numbers, eval table. |
| 1:55–2:00 | **Business case.** Pilot with the county, a 211 line or a community nonprofit. What is next. |

**Likely judge questions:** What if the internet is down? (phone and SMS first, plus pre-storm and post-storm use) · How does it scale? (tiers above) · Is the data accurate? (shows age and source, never claims safety) · What about privacy? (consent, hashed IDs, delete on request).

**Backups:** headset mic or push-to-talk, text fallback, a recorded demo video, the web widget.

## 8. Devpost submission checklist

- [ ] Project name and one-line tagline.
- [ ] Story sections: Inspiration, What it does, How we built it, Challenges, Accomplishments, What we learned, What's next.
- [ ] **Built with** tags: ElevenLabs, Gemini, Backboard, Vultr, Twilio, FastAPI, Postgres, USGS, NWS, Leaflet.
- [ ] Links: **public** GitHub repo and the **hosted website** (both required).
- [ ] 3 to 5 screenshots, clear first image.
- [ ] 2-minute demo video (public or unlisted). Test the link while logged out.
- [ ] Teammates added on Devpost, each accepted.
- [ ] **Opt in to every prize:** Overall, Pitch, Environment, ElevenLabs, Gemini, Backboard, Vultr, GoDaddy. Read each sponsor's requirement text first.
- [ ] README: problem, demo GIF, architecture, how to run, eval results, limitations, data sources and disclaimers.
- [ ] Press **Submit** (a saved draft does not count). Re-open the page afterward and confirm it shows as submitted.
- [ ] After judging: publish the **DEV** post for the $100 challenge.

## 9. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Twilio trial only accepts verified callers | Judges cannot call | Upgrade, or use web/QR widget |
| Loud room | Speech recognition fails | Headset or push-to-talk, text fallback |
| Dry weather, no live flood to show | Weak demo | Clearly labeled replay mode |
| USGS data lags up to about an hour | Stale answers | Always state data age |
| No raw feed from Harris County's own system | Less local data | Use USGS and NWS, link to the county site |
| Backboard has no per-user scope | Memory leaks between callers | One assistant per household, hard facts in own DB |
| Vietnamese unavailable for agents | Missing language | English and Spanish only; mention Vietnamese as next |
| Gemini free-tier rate limits | Throttling | Cache, Flash model, back off |
| ElevenLabs credits or concurrency | Calls rejected | Test early, enable burst, cached audio |
| Webhooks need public HTTPS | Tools fail | Domain + Caddy on Vultr in Phase 1 |
| Scope creep | Unfinished demo | Cut list below |

## 10. Cut list (cut from the top when behind)

1. Vietnamese
2. Cached-audio broadcast
3. Dashboard call feed (keep the map)
4. Gemini post-call triage
5. Backboard (keep the own-database profile)

**Protect at all costs:** the call flow, ElevenLabs, Gemini, live-data answers with source and time, and the deployed URL.

## 11. Definition of done

- [ ] A stranger can call or open the widget and get a correct, sourced answer in English and Spanish.
- [ ] The second call remembers the first.
- [ ] The live URL works from a phone, off the venue Wi-Fi.
- [ ] Repo is public, README is complete, no secrets in history.
- [ ] Devpost is **submitted** with all prizes opted in.
- [ ] Everyone knows the demo script and can answer the four judge questions.

## 12. Sources

- ElevenLabs: [Twilio integration](https://elevenlabs.io/docs/eleven-agents/phone-numbers/twilio-integration/native-integration) · [burst pricing](https://elevenlabs.io/docs/eleven-agents/guides/burst-pricing) · [models](https://elevenlabs.io/docs/overview/models) · [language detection](https://elevenlabs.io/docs/eleven-agents/customization/tools/system-tools/language-detection) · [server tools](https://elevenlabs.io/docs/eleven-agents/customization/tools/server-tools) · [LLM options](https://elevenlabs.io/docs/eleven-agents/customization/llm)
- Backboard: [memory](https://docs.backboard.io/concepts/memory) · [assistants](https://docs.backboard.io/sdk/assistants) · [first message](https://docs.backboard.io/sdk/first-message)
- Data: [NWS API](https://www.weather.gov/documentation/services-web-api) · [USGS instantaneous values](https://waterservices.usgs.gov/docs/instantaneous-values/) · [Harris County FWS](https://www.harriscountyfws.org/About)
- Other: [MLH Vultr offer](https://www.mlh.com/partners/vultr) · [Twilio trial limits](https://static0.twilio.com/docs/usage/trials/try-out-voice) · [Gemini rate limits](https://ai.google.dev/gemini-api/docs/rate-limits)
