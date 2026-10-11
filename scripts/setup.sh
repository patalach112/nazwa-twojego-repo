#!/usr/bin/env bash
#
# Generates every config file this source does not ship with, with the paths
# pointing at wherever you cloned it. Configs are gitignored on purpose, so a
# fresh clone has none of them and half the services will not start without them.
#
#   ./scripts/setup.sh
#
# Connection details can be overridden with env vars:
#   DB_HOST DB_PORT DB_USER DB_PASSWORD DB_NAME REDIS BASE_URL
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-postgres}"
DB_NAME="${DB_NAME:-pekora}"
REDIS="${REDIS:-127.0.0.1:6379}"
BASE_URL="${BASE_URL:-http://localhost:5000}"

# git bash on windows gives us a /c/... path, dotnet wants C:\...
if command -v cygpath >/dev/null 2>&1; then
    ROOT_NATIVE="$(cygpath -w "$ROOT")"
else
    ROOT_NATIVE="$ROOT"
fi

echo "using source at: $ROOT_NATIVE"
echo

# ---------------------------------------------------------------- api/config.json
if [ -f api/config.json ]; then
    echo "api/config.json already exists, leaving it alone"
else
    python3 - "$DB_HOST" "$DB_PORT" "$DB_USER" "$DB_PASSWORD" "$DB_NAME" <<'PY'
import json, sys
host, port, user, password, name = sys.argv[1:6]
json.dump({
    "knex": {
        "client": "pg",
        "connection": {
            "host": host,
            "port": int(port),
            "user": user,
            "password": password,
            "database": name,
        },
    },
}, open("api/config.json", "w"), indent=4)
PY
    echo "wrote api/config.json"
fi

# ------------------------------------------------- Roblox.Website/appsettings.json
WEBSITE=Roblox/Roblox.Website
if [ -f "$WEBSITE/appsettings.json" ]; then
    echo "$WEBSITE/appsettings.json already exists, leaving it alone"
else
    [ -f "$WEBSITE/appsettings.example.json" ] || {
        echo "missing $WEBSITE/appsettings.example.json, cannot continue" >&2
        exit 1
    }
    python3 - "$WEBSITE" "$ROOT_NATIVE" "$DB_HOST" "$DB_PORT" "$DB_NAME" "$DB_USER" "$DB_PASSWORD" "$REDIS" "$BASE_URL" <<'PY'
import json, os, sys

website, root, host, port, name, user, password, redis, base_url = sys.argv[1:10]

with open(os.path.join(website, "appsettings.example.json")) as f:
    cfg = json.load(f)

# separator matters on windows, everything else can stay with forward slashes
sep = "\\" if os.name == "nt" else "/"
def p(*parts):
    return root + sep + sep.join(parts) + sep

cfg["Postgres"] = "Host=%s;Port=%s;Database=%s;Username=%s;Password=%s" % (host, port, name, user, password)
cfg["Redis"] = redis
cfg["BaseUrl"] = base_url

cfg["Directories"].update({
    "Asset": p("data", "assets"),
    "Storage": p("data", "storage"),
    "Thumbnails": p("data", "thumbnails"),
    "GroupIcons": p("data", "group-icons"),
    "Public": p("api", "public"),
    "XmlTemplates": p("Roblox", "Roblox.Libraries", "Templates"),
    "JsonData": p("Roblox", "Roblox.Libraries", "Json"),
    "ScriptsData": p("Roblox", "Roblox.Libraries", "StudioScripts"),
    "AdminBundle": p("admin", "public"),
    "EconomyChatBundle": p("data", "economy-chat"),
    "RCCLuaScripts": p("data", "rcc-lua"),
})

with open(os.path.join(website, "appsettings.json"), "w") as f:
    json.dump(cfg, f, indent=4)
PY
    echo "wrote $WEBSITE/appsettings.json"
fi

# ----------------------------------------------------------- game-servers.json
if [ -f "$WEBSITE/game-servers.json" ]; then
    echo "$WEBSITE/game-servers.json already exists, leaving it alone"
else
    cp "$WEBSITE/game-servers.example.json" "$WEBSITE/game-servers.json"
    echo "wrote $WEBSITE/game-servers.json"
fi

# data folders the website reads/writes at runtime
mkdir -p data/assets data/storage data/thumbnails data/group-icons data/economy-chat data/rcc-lua

# ------------------------------------------------------------ frontend/config.json
if [ -f frontend/config.json ]; then
    echo "frontend/config.json already exists, leaving it alone"
else
    if command -v node >/dev/null 2>&1; then
        (cd frontend && node ./util/create_config.js)
    else
        echo "node is not installed, skipping frontend/config.json (run util/create_config.js later)"
    fi
fi

# ---------------------------------------------------------------------- done
echo
echo "configs are in place. next:"
echo "  1. start postgres and redis            docker compose up -d"
echo "  2. install + migrate the database      (cd api && npm i && npx knex migrate:latest)"
echo "  3. run the website                     (cd $WEBSITE && dotnet run)"
echo "  4. run the frontend                    (cd frontend && npm i && npm run dev)"
