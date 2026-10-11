# ElevenLabs webhook tools: paste-in settings

Agent: **Houston Flood Help Line** → **Tools** → **Add tool** → **Webhook**.

Base URL (the production server, HTTPS by Caddy):

```
https://thefloodfather.surf
```

> During the domain cutover the old name `https://45-76-57-234.sslip.io` keeps working only while it is still listed in `DOMAIN` on the server (see `docs/DOMAIN_CUTOVER.md`). Switch all 5 tools to the new URL before removing it.

## Auth header (same for all 5 tools)

| Header | Value |
|---|---|
| `X-API-Key` | `YOUR_KEY` (the value of `API_SHARED_SECRET` in `.env`) |

Store `YOUR_KEY` as a **workspace secret** in ElevenLabs and pick it as the header value. Do not type it as plain text, and never put it in the system prompt.

For POST tools, also set `Content-Type: application/json` if the dashboard doesn't add it for you.

## Caller phone number

Tools that take `caller_id` should use the **dynamic variable** `system__caller_id` (value type: *Dynamic variable*), not an LLM-filled value. This stops the model from making up or mixing up phone numbers.

- On a **Twilio phone call** this variable holds the caller's number.
- In the **web widget / dashboard test** there is no caller number. The backend needs 7–32 characters, so profile tools will fail there (HTTP 422). For widget testing you can set a test default for the dynamic variable in the agent's settings (for example a fake number like `+15550000001`).
- [VERIFY] Not yet tested end to end. Confirm the variable name in your dashboard's dynamic-variable picker.

---

## 1. `lookup_profile`

| Field | Value |
|---|---|
| Name | `lookup_profile` |
| Description | Look up what we remember about the person on the phone (name, language, household needs). Call this once at the start of every call, before answering questions. |
| Method | `POST` |
| URL | `https://thefloodfather.surf/profile/lookup` |
| Headers | `X-API-Key: YOUR_KEY` |

**Body parameters**

| Identifier | Type | Required | Value type | Value / description |
|---|---|---|---|---|
| `caller_id` | string | yes | Dynamic variable | `system__caller_id` |

Returns `{"found": false}` or `{"found": true, "language", "name", "needs", "notes", "memory"}`.

## 2. `get_flood_status`

| Field | Value |
|---|---|
| Name | `get_flood_status` |
| Description | Get current flood risk, nearest stream gauge readings and active National Weather Service alerts for a Houston-area address. Use when the caller asks about flooding, water levels, or storm conditions near them. Always tell the caller the data time and source from the result. |
| Method | `GET` |
| URL | `https://thefloodfather.surf/flood-status` |
| Headers | `X-API-Key: YOUR_KEY` |

**Query parameters**

| Identifier | Type | Required | Value type | Description (for the LLM) |
|---|---|---|---|---|
| `address` | string | yes | LLM prompt | Street address, nearest cross street, or ZIP code in the Houston area, as the caller said it. Add "Houston TX" if the caller did not give a city. Max 200 characters. |

Returns `found`, `risk` (`low` / `elevated` / `high` / `unknown`), `reasons`, `caveat`, `data_as_of`, `nearest_gauges`, `alerts`, `speak_hint`, `location.lat`, `location.lon`, `replay`.

## 3. `get_shelters`

| Field | Value |
|---|---|
| Name | `get_shelters` |
| Description | Find the nearest shelters that FEMA lists as open. Pass the caller's location (ZIP code, city, address or landmark), or lat and lon from get_flood_status. Read at most two, say they are FEMA's list and that distances are in a straight line, and tell the caller to confirm a shelter is open before going. If the list is empty or unavailable, read the message and tell the caller to call 211. |
| Method | `GET` |
| URL | `https://thefloodfather.surf/shelters` |
| Headers | `X-API-Key: YOUR_KEY` |

**Query parameters** (give `location`, or both `lat` and `lon`)

| Identifier | Type | Required | Value type | Description / value |
|---|---|---|---|---|
| `location` | string | no | LLM prompt | The caller's ZIP code, city, street address or landmark, as they said it. Max 200 characters. |
| `lat` | number | no | LLM prompt | Latitude, copied exactly from `location.lat` in the last get_flood_status result. Only together with `lon`. |
| `lon` | number | no | LLM prompt | Longitude, copied exactly from `location.lon` in the last get_flood_status result. Only together with `lat`. |
| `limit` | integer | no | Constant | `3` (the maximum) |

Returns `count`, `shelters` (each: `name`, `address`, `distance_miles`, `source`), `available` (false if FEMA can't be reached), `checked_at`, and a `message` to read: "FEMA lists no open shelters near you right now. Call 211." when there are none.

## 4. `save_profile`

| Field | Value |
|---|---|
| Name | `save_profile` |
| Description | Save household details so we can help faster next time. Call ONLY after the caller clearly says yes to "Would you like me to remember this for next time?". Never call it without that yes. |
| Method | `POST` |
| URL | `https://thefloodfather.surf/profile` |
| Headers | `X-API-Key: YOUR_KEY` |

**Body parameters**

| Identifier | Type | Required | Value type | Description (for the LLM) / value |
|---|---|---|---|---|
| `caller_id` | string | yes | Dynamic variable | `system__caller_id` |
| `consent` | boolean | yes | LLM prompt | `true` only if the caller explicitly agreed to be remembered in this call. (The backend refuses to save when this is false.) |
| `language` | string | no | LLM prompt | Language of the call: `en` or `es`. |
| `name` | string | no | LLM prompt | Caller's first name, if they gave it. Max 100 characters. |
| `needs` | array of strings | no | LLM prompt | Short household needs the caller mentioned, e.g. `["oxygen", "dog", "cannot climb stairs"]`. Max 20 items. |
| `notes` | string | no | LLM prompt | Short free-text note about the household, max 500 characters. Do not include other people's information. |

Returns `{"saved": true, "backboard": ...}`.

## 5. `forget_profile`

| Field | Value |
|---|---|
| Name | `forget_profile` |
| Description | Delete everything we saved about the caller. Use when the caller asks to be forgotten or to delete their information, then confirm it is done. |
| Method | `POST` |
| URL | `https://thefloodfather.surf/profile/forget` |
| Headers | `X-API-Key: YOUR_KEY` |

**Body parameters**

| Identifier | Type | Required | Value type | Value / description |
|---|---|---|---|---|
| `caller_id` | string | yes | Dynamic variable | `system__caller_id` |

Returns `{"deleted": true}` or `{"deleted": false}` (nothing was stored). This deletes our database copy only. Deleting the Backboard memory is not implemented.
