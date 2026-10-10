#!/usr/bin/env bash
# Control the demo replay from a terminal.
#   export BASE=http://localhost:8000 KEY=yoursecret
#   bash scripts/demo.sh on [replay_name] [start_index]   # switch to replay (default: first file)
#   bash scripts/demo.sh step [n]                         # advance n frames (default 1)
#   bash scripts/demo.sh off                              # back to live data
set -eu
BASE="${BASE:-http://localhost:8000}"; KEY="${KEY:-}"
H=(-H "X-API-Key: $KEY" -H "Content-Type: application/json")
case "${1:-}" in
  on)   name="${2:-}"
        if [ -n "$name" ]; then rep="\"$name\""; else rep="null"; fi
        curl -s "${H[@]}" -X POST "$BASE/admin/mode" -d "{\"mode\":\"replay\",\"replay\":$rep,\"index\":${3:-0}}" ;;
  step) curl -s "${H[@]}" -X POST "$BASE/admin/replay/step?delta=${2:-1}" ;;
  off)  curl -s "${H[@]}" -X POST "$BASE/admin/mode" -d '{"mode":"live"}' ;;
  *)    echo "usage: demo.sh on [name] [index] | step [n] | off"; exit 1 ;;
esac
echo
