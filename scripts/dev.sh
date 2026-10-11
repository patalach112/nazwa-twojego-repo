#!/usr/bin/env bash
#
# local dev stack in one terminal: website + frontend + asset validator.
# expects postgres and redis to already be up (docker compose up -d) and the
# configs to exist (./scripts/setup.sh). ctrl+c stops everything.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"

pids=()
cleanup() {
    echo
    echo "stopping..."
    for pid in "${pids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

start() {
    local name="$1" dir="$2"
    shift 2
    ( cd "$dir" && exec "$@" ) >"$LOG_DIR/$name.log" 2>&1 &
    local pid=$!
    pids+=("$pid")
    echo "  $name -> logs/$name.log (pid $pid)"
}

if [ ! -f Roblox/Roblox.Website/appsettings.json ]; then
    echo "Roblox/Roblox.Website/appsettings.json is missing, run ./scripts/setup.sh first" >&2
    exit 1
fi

echo "running migrations..."
( cd api && npx knex migrate:latest >/dev/null )

echo "starting:"
start website      Roblox/Roblox.Website dotnet run
start validator    AssetValidationServiceV2 go run main.go
start frontend     frontend npm run dev

echo
echo "website  http://localhost:5000"
echo "frontend http://localhost:3000"
echo
echo "the website takes a minute to compile the first time, watch"
echo "logs/website.log if it looks like nothing is happening"
wait
