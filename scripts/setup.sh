#!/usr/bin/env bash
#
# Generates the local website, frontend and game-service configs, with paths
# pointing at wherever you cloned it. Configs are gitignored on purpose, so a
# fresh clone has none of them and the services will not start without them.
#
#   ./scripts/setup.sh
#
# Connection details can be overridden with env vars:
#   DB_HOST DB_PORT DB_USER DB_PASSWORD DB_NAME REDIS BASE_URL ARBITER_BASE_URL
#
set -euo pipefail
# New configs contain credentials. Existing files are never rewritten.
umask 077

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DB_HOST="${DB_HOST:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_PASSWORD="${DB_PASSWORD:-postgres}"
DB_NAME="${DB_NAME:-pekora}"
REDIS="${REDIS:-127.0.0.1:6379}"
BASE_URL="${BASE_URL:-http://localhost:5000}"
ARBITER_BASE_URL="${ARBITER_BASE_URL:-http://127.0.0.1:4310/}"

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
    python3 - "$WEBSITE" "$ROOT_NATIVE" "$DB_HOST" "$DB_PORT" "$DB_NAME" "$DB_USER" "$DB_PASSWORD" "$REDIS" "$BASE_URL" "$ARBITER_BASE_URL" <<'PY'
import json, os, secrets, sys

website, root, host, port, name, user, password, redis, base_url, arbiter_url = sys.argv[1:11]

with open(os.path.join(website, "appsettings.example.json")) as f:
    cfg = json.load(f)

# separator matters on windows, everything else can stay with forward slashes
sep = "\\" if os.name == "nt" else "/"
def p(*parts):
    return root + sep + sep.join(parts) + sep

cfg["Postgres"] = "Host=%s;Port=%s;Database=%s;Username=%s;Password=%s" % (host, port, name, user, password)
cfg["Redis"] = redis
cfg["BaseUrl"] = base_url
cfg["ArbiterBaseUrl"] = arbiter_url
# Each service gets the website's value, not a separately generated secret.
for key in ("ArbiterAuthorization", "GameServerAuthorization", "BotAuthorization", "RccAuthorization",
            "Authorization", "VerificationSecret", "UserAgentBypassSecret"):
    cfg[key] = secrets.token_hex(32)

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

# ------------------------------------------------------ game-service configs
python3 - "$WEBSITE" "$ROOT_NATIVE" <<'PY'
import json, os, sys

website, root = sys.argv[1:3]
with open(os.path.join(website, "appsettings.json")) as f:
    website_cfg = json.load(f)

for key in ("ArbiterAuthorization", "GameServerAuthorization", "BotAuthorization"):
    value = website_cfg.get(key)
    if not isinstance(value, str) or not value:
        sys.exit("%s/appsettings.json needs a nonempty %s before game-service configs can be generated" % (website, key))
if website_cfg["ArbiterAuthorization"].startswith("change-me"):
    sys.exit("%s/appsettings.json still has the example ArbiterAuthorization. Set a private random secret there and rerun setup; existing files have been left alone." % website)

sep = "\\" if os.name == "nt" else "/"
def p(*parts):
    return root + sep + sep.join(parts) + sep

def write_config(folder, values, secret_keys):
    filename = os.path.join(folder, "config.json")
    if os.path.exists(filename):
        print(filename + " already exists, leaving it alone")
        with open(filename) as f:
            existing = json.load(f)
        for key in secret_keys:
            if existing.get(key) != values[key]:
                sys.exit("%s: %s does not match the website. Update it manually; no existing config was overwritten." % (filename, key))
        return
    with open(os.path.join(folder, "config.example.json")) as f:
        cfg = json.load(f)
    cfg.update(values)
    with open(filename, "x") as f:
        json.dump(cfg, f, indent=4)
    print("wrote " + filename)

write_config("game-arbiter", {
    "authorization": website_cfg["ArbiterAuthorization"],
    "rccCwd": p("RCCService"),
}, ("authorization",))
write_config("game-server", {
    "authorization": website_cfg["GameServerAuthorization"],
    "websiteBotAuth": website_cfg["BotAuthorization"],
    "baseUrl": website_cfg["BaseUrl"].rstrip("/"),
    "rcc": p("RCCService"),
    "content": p("RCCService", "content"),
}, ("authorization", "websiteBotAuth"))
PY

# JWT keys are kept separately so an older appsettings.json remains byte-preserved.
python3 scripts/provision_website_secrets.py "$WEBSITE"

# data folders the website reads/writes at runtime
mkdir -p data/assets data/storage data/thumbnails data/group-icons data/economy-chat data/rcc-lua

# ------------------------------------------------------------ frontend/config.json
if [ -f frontend/config.json ]; then
    echo "frontend/config.json already exists, leaving it alone"
else
    if command -v node >/dev/null 2>&1; then
        (cd frontend && WEBSITE_CONFIG_PATH="../$WEBSITE/appsettings.json" node ./util/create_config.js)
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
echo "  5. start the arbiter separately        (cd game-arbiter && npm start)"
echo
echo "game servers still need a compatible RCC launcher; /gs/* callbacks are now implemented."
echo "the generated RCC paths target Windows binaries, not a working Debian game stack."
