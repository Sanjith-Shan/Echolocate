#!/usr/bin/env bash
# Echolocate — one-command demo launcher.
#
# Boots the simulator and the backend together, prints the URL to open, and
# tears them both down on Ctrl-C. Use --hardware to point at a real ESP32
# instead of the simulator.
#
#   ./run-demo.sh                       # simulator (default)
#   ./run-demo.sh --hardware /dev/cu.usbmodem14101 192.168.1.42
#   ./run-demo.sh --scenario surge      # sim with auto-ramping crowding
#   ./run-demo.sh --port 8000           # change API port
#
# After boot, open http://localhost:8000/app/ — that's the PWA.

set -euo pipefail

cd "$(dirname "$0")"

API_PORT=8000
SIM_TCP_PORT=3333
SIM_HTTP_PORT=8088
MODE="sim"
SCENARIO=""
SERIAL_PORT_OVERRIDE=""
FIRMWARE_URL_OVERRIDE=""
SIM_LEVEL="empty"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hardware)
      MODE="hardware"
      SERIAL_PORT_OVERRIDE="${2:-}"
      FIRMWARE_URL_OVERRIDE="${3:-}"
      shift; [[ $# -gt 0 ]] && shift
      [[ $# -gt 0 ]] && shift
      ;;
    --scenario)
      SCENARIO="$2"; shift 2 ;;
    --level)
      SIM_LEVEL="$2"; shift 2 ;;
    --port)
      API_PORT="$2"; shift 2 ;;
    -h|--help)
      sed -n '/^#/,/^$/p' "$0" | sed 's/^# \?//' ; exit 0 ;;
    *)
      echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# Source .env so the user's API keys land in this shell session
if [[ -f .env ]]; then
  set -a; . ./.env; set +a
fi

# ----- venv -----
if [[ ! -d .venv ]]; then
  echo "==> creating .venv"
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
if ! python3 -c "import fastapi, uvicorn, numpy" 2>/dev/null; then
  echo "==> installing backend deps"
  pip install -q -r backend/requirements.txt
fi

PIDS=()
cleanup() {
  echo
  echo "==> stopping (${#PIDS[@]} processes)"
  for p in "${PIDS[@]:-}"; do kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ----- simulator (only in sim mode) -----
if [[ "$MODE" == "sim" ]]; then
  echo "==> starting simulator on tcp:$SIM_TCP_PORT, http:$SIM_HTTP_PORT (level=$SIM_LEVEL)"
  SIM_ARGS=(-m sim.esp32_sim --no-stdin
            --tcp-port "$SIM_TCP_PORT"
            --http-port "$SIM_HTTP_PORT"
            --level "$SIM_LEVEL")
  if [[ -n "$SCENARIO" ]]; then SIM_ARGS+=(--scenario "$SCENARIO"); fi
  python3 "${SIM_ARGS[@]}" &
  PIDS+=($!)
  sleep 1
  export SERIAL_PORT="tcp://127.0.0.1:$SIM_TCP_PORT"
  export FIRMWARE_HTTP_URL="http://127.0.0.1:$SIM_HTTP_PORT"
else
  if [[ -z "$SERIAL_PORT_OVERRIDE" ]]; then
    echo "ERROR: --hardware requires <serial-device> [<firmware-http-url>]" >&2
    exit 2
  fi
  export SERIAL_PORT="$SERIAL_PORT_OVERRIDE"
  if [[ -n "$FIRMWARE_URL_OVERRIDE" ]]; then
    export FIRMWARE_HTTP_URL="$FIRMWARE_URL_OVERRIDE"
  fi
  echo "==> hardware mode: SERIAL_PORT=$SERIAL_PORT  FIRMWARE_HTTP_URL=${FIRMWARE_HTTP_URL:-<not set>}"
fi

# ----- backend -----
echo "==> starting backend on :$API_PORT"
python3 -m uvicorn backend.main:app --host 0.0.0.0 --port "$API_PORT" --log-level info &
PIDS+=($!)

# Wait until the backend is answering before printing the banner
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$API_PORT/" >/dev/null 2>&1; then break; fi
  sleep 0.5
done

cat <<EOF

============================================================
  Echolocate is running.

  PWA:           http://localhost:$API_PORT/app/
  Diagnostics:   http://localhost:$API_PORT/api/diagnostics
  REST status:   http://localhost:$API_PORT/api/status
EOF

if [[ "$MODE" == "sim" ]]; then
cat <<EOF
  Simulator UI:  http://localhost:$SIM_HTTP_PORT/

  Drive crowd levels from another terminal:
    curl -X POST http://localhost:$SIM_HTTP_PORT/control \\
      -H 'Content-Type: application/json' -d '{"level":"high"}'
EOF
fi

cat <<EOF

  Press Ctrl-C to stop everything.
============================================================

EOF

# Keep foreground until a child exits or Ctrl-C
wait
