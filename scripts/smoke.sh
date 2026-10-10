#!/usr/bin/env bash
# Checks that the outside data sources work from YOUR network, then checks your API.
# Usage:  bash scripts/smoke.sh                      (external sources only)
#         BASE=http://localhost:8000 KEY=yoursecret bash scripts/smoke.sh   (also test your API)
set -u
UA="${NWS_USER_AGENT:-(orion-flood-smoke, you@example.com)}"
ok()   { printf "  OK    %s\n" "$1"; }
bad()  { printf "  FAIL  %s\n" "$1"; }

echo "== USGS gauge height, Harris County (countyCd=48201) =="
out=$(curl -s --compressed -m 30 "https://waterservices.usgs.gov/nwis/iv/?format=json&countyCd=48201&parameterCd=00065&siteStatus=active&period=PT3H")
echo "$out" | head -c 300; echo
echo "$out" | grep -q '"timeSeries"' && ok "USGS returned timeSeries" || bad "USGS: no timeSeries (check parameter names)"

echo "== NWS alerts at downtown Houston =="
out=$(curl -s -m 20 -H "User-Agent: $UA" -H "Accept: application/geo+json" "https://api.weather.gov/alerts/active?point=29.7604,-95.3698")
echo "$out" | head -c 300; echo
echo "$out" | grep -q '"features"' && ok "NWS returned features" || bad "NWS: unexpected response"

echo "== Census geocoder =="
out=$(curl -s -m 20 "https://geocoding.geo.census.gov/geocoder/locations/onelineaddress?address=901+Bagby+St+Houston+TX&benchmark=Public_AR_Current&format=json")
echo "$out" | head -c 300; echo
echo "$out" | grep -q 'addressMatches' && ok "Census geocoder answered" || bad "Census geocoder: unexpected response"

if [ -n "${BASE:-}" ]; then
  echo "== Your API at $BASE =="
  curl -s -m 10 "$BASE/health"; echo
  curl -s -m 20 -H "X-API-Key: ${KEY:-}" "$BASE/flood-status?lat=29.76&lon=-95.37" | head -c 600; echo
fi
