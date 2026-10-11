<div align="center">
</div>

<h1 align="center">Pekora/Project X  (latest version from 30 AUGUST 2025)</h1>
<p align="center">Pekora is roblox revival, i decided to leak this because chloe is an big racist and a femboy.</p>
<p align="center">Also please do not contact me on trying to get the source setup. there are many guides on how to and if you read this guide properly you shouldn't need help.</p>

<div align="center">

[![Go](https://img.shields.io/badge/GoLang-blue?logo=go)](https://github.com/tooblewtf/bloxd)
[![Dotnet 8](https://img.shields.io/badge/.NET-8.0-purple?logo=dotnet)](https://github.com/tooble.wtf/bloxd)
[![Node.js](https://img.shields.io/badge/Node.JS-24.3.0-green?logo=nodedotjs)](https://github.com/tooblewtf/bloxd)
[![trklzz](https://img.shields.io/badge/LEAKED_BY-tooble/trklzz-red?logo=github)](https://github.com/tooblewtf)

Pekora is a heavily modified and skidded economy simulator source lmao

pekora discord bot : https://github.com/tooblewtf/pekora-discord-bot
pekora clients : https://github.com/tooblewtf/pekora-client

ALL LEAKED BY TOOBLE / TOOBLE TARAFINDAN LEAKLENDI

</div>

# WARNING

Please do not re-host this to the public. we know it is a source code that can start your own revival but re-hosting is unrecommended for reasons.
This repository is only created for leak this shitty ass revival and how they work.
If you want to build your own roblox please DON'T MAKE A REVIVAL WITH A SRC BUILD YOUR OWN.

# REPO LAYOUT

Old guides (including older versions of this file) tell you to look inside a `services/` folder and a `2016-roblox-main` folder. **Neither exists anymore.**

- The repo **root is the old `services/` folder** - every component sits directly in it.
- `2016-roblox-main` was renamed to **`frontend/`**.

| Old path (outdated guides) | Actual path in this repo |
| --- | --- |
| `services/api` | `api/` *(legacy - migrations + `public/` only)* |
| `services/Roblox/Roblox.Website` | `Roblox/Roblox.Website/` *(the actual .NET API/website)* |
| `services/Roblox/...` | `Roblox/...` |
| `services/admin` | `admin/` |
| `services/2016-roblox-main` | `frontend/` |
| `services/AssetValidationServiceV2` | `AssetValidationServiceV2/` |
| `services/game-server` | `game-server/` |
| `services/game-renderer` | `game-renderer/` |
| `services/RCCService` | `RCCService/` |

```
repo root/
├── scripts/                  # setup.sh (writes local configs) and dev.sh (runs the dev stack)
├── docker-compose.yml        # postgres + redis for local development
├── api/                      # legacy api: knex migrations + public/ (css, js, img, admin bundle target)
├── Roblox/                   # .NET 8 solution - the real website + api
│   ├── Roblox.Website/       # main website/api (dotnet run here)
│   ├── Roblox.Services/      # business logic
│   ├── Roblox.Libraries/     # Json/, Templates/, StudioScripts/ (config points here)
│   └── ...
├── admin/                    # svelte admin panel (built into admin/public/)
├── frontend/                 # 2016-roblox frontend (Next.js)
├── AssetValidationServiceV2/ # Go service for validating uploaded assets (:4300)
├── game-arbiter/             # authenticated RCC process manager (:4310)
├── game-server/              # legacy rcc manager for game servers
├── game-renderer/            # rendering service
└── RCCService/               # Windows RCC binaries + content
```

# WHAT IS STILL MISSING

The web stack and the game stack are not the same thing. Generating configs or
getting a green build does **not** make this source a working revival.

1. **There is no Linux RCC binary.** `RCCService/RCCService.exe` is Windows-only.
   `game-server/docker/rccservice.dockerfile` copies a `rcc-docker/` directory
   that is not in the repo. On Debian, Docker alone cannot supply that missing
   runtime; a compatible, legally obtained Linux RCC build or a Windows host
   is still needed. Wine has not been validated here.
2. **The arbiter needs a real game launcher.** This repo now includes
   `game-arbiter/`, replacing the omission described in `Notes.md`. It manages
   processes and the website's start/kill/eviction contract, but the example
   `rccCommand` only starts RCC's SOAP listener. Configure a launcher that opens
   the RCC job with the right Lua script and handles the requested network and
   proxy ports. A successful spawn is not a ready game. See
   [`game-arbiter/README.md`](game-arbiter/README.md).
3. **The source includes original private keys.** Treat the PEM files in
   `Roblox/Roblox.Website/Keys/` as compromised. They have not been changed by
   these setup fixes; do not reuse them for a public deployment. Configs,
   renderer wiring and client signing need a separate review before hosting.

# GAME SERVER REPORTING

The website now handles the callbacks used by the supplied Lua and legacy
`game-server`. Every route is a JSON POST and independently checks the body
`authorization` against `GameServerAuthorization`; Roblox headers, cookies and
arbiter credentials cannot authorize it. `serverId` is a nonempty UUID. Numeric
IDs may be numbers or the decimal strings emitted by Lua.

| Route | Other fields | Effect |
| --- | --- | --- |
| `/gs/players/report` | `placeId`, `userId`, `eventType` (`Join` or `Leave`) | Update one active membership, job-linked play history, visits and rewards |
| `/gs/ping` | `placeId`, optional nonnegative `ping` / `fps` | Mark this job ready and refresh its heartbeat; fractional statistics are rounded |
| `/gs/activity` | none | Return `isAlive` and `updatedAt` (null for an unknown job) |
| `/gs/shutdown` | `placeId` | Remove this job and close its remaining sessions |
| `/gs/delete` | none | The same cleanup, used by the legacy process manager |

A ready heartbeat expires after five minutes. Reports cannot modify a different
place. Concurrent duplicate joins/leaves do not create duplicate visits or
payments; a late Leave or deletion from a former job does not clear a newer
session in the same place. Shutdown/delete are idempotent **notifications**, not
a recursive call into the process that is reporting its own shutdown.

Launches now commit a Loading job and reserve the host's network, SOAP and proxy
ports **before** contacting the arbiter. Retried joins reuse the pending job, and
ready joins keep the same proxy port. A rejected start is cleaned up only after
the arbiter confirms termination; an uncertain kill leaves a Closing reservation
that cannot be revived by a heartbeat and can be retried safely. The launcher
regressions use a substitute HTTP handler plus real Postgres/Redis, not RCC.

Run all database migrations before enabling reports. The new reporting migration
refuses duplicate jobs, multiple active memberships for one user and orphan
memberships instead of silently throwing data away. Reconcile any reported
legacy conflicts before rerunning it.

## Reporting regression tests

Use an empty database whose name ends in `_tests` and a **separate Redis
instance**, not the development Redis. These commands never read or overwrite
`api/config.json`:

```bash
export TEST_DATABASE_URL='postgresql://postgres:postgres@127.0.0.1:5432/pekora_reporting_tests'
export GAME_SERVER_TEST_POSTGRES='Host=127.0.0.1;Port=5432;Database=pekora_reporting_tests;Username=postgres;Password=postgres'
export GAME_SERVER_TEST_REDIS='127.0.0.1:6380'
(cd api && npm install && npm run test:migrations)
dotnet test Roblox/Roblox.GameServer.Test/Roblox.GameServer.Test.csproj --configuration Release
```

The **Game server reports** CI job provisions disposable Postgres/Redis services,
checks the complete migration chain and rollback, then runs HTTP/lifecycle tests
with real database and cache operations. The focused ASP.NET TestHost includes
the CSRF, application guard and frontend proxy bypasses. Synthetic-key tests also
verify legacy/2048-bit signatures, portable key paths and the configured JWT keys;
they do not read the original leaked private keys. A separate Linux smoke test
boots the complete website with isolated generated configs, exercises signing-key
setup and checks authorized/unauthorized `/gs/activity` through the real middleware
pipeline. It does not start the frontend, validator or RCC, or prove that a real
client can join. Original signing keys remain compromised despite the portable
loader fix.

# HOW TO SETUP

## things you need

- <a href="https://nodejs.org/en/download">Node.js</a>, *for the frontend, admin and arbiter; Node 22 has been tested, the arbiter requires at least 18*
- Python 3, *for `scripts/setup.sh` and its tests*
- <a href="https://sbp.enterprisedb.com/getfile.jsp?fileid=1258627">PostgreSQL</a> *(or docker, see below)*
- <a href="https://dotnet.microsoft.com/download/dotnet/8.0">**.NET 8 SDK**</a>, *to run the website - every project in here targets net8.0, .NET 6 will not build it*
- <a href="https://go.dev/dl/">Go</a>, *for asset validation (any recent version works)*
- <a href="https://github.com/redis-windows/redis-windows/releases/download/8.6.2/Redis-8.6.2-Windows-x64-cygwin-with-Service.zip">Redis Server</a> *important*

## requirements

- The web stack targets Linux, macOS and Windows. The bundled RCC runtime still requires Windows; see **WHAT IS STILL MISSING** above.
- Local web development uses `localhost`; a domain and patched clients are separate deployment work.
- knowledge on how things like this work (you should have at least some experience with websites and coding to be able to host this. it's really not hard to set up if you know what you're doing.)

## quick start (linux, macOS, WSL)

The .NET website and frontend do not need Wine. This starts the **web development
stack**, not a playable game server. The Linux/Windows CI jobs compile the .NET
solution; RCC startup and real game joins still need a compatible host.

```bash
./scripts/setup.sh                          # local configs, pointed at your clone
docker compose up -d                        # postgres + redis
(cd api && npm i && npx knex migrate:latest) # database schema
(cd frontend && npm i)                       # frontend deps
./scripts/dev.sh                             # website + validator + frontend, ctrl+c stops it
```

`setup.sh` accepts `DB_HOST DB_PORT DB_USER DB_PASSWORD DB_NAME REDIS BASE_URL
ARBITER_BASE_URL` as env vars for **new** configs. It does not rewrite existing
files. A fresh website gets distinct random service, web-authorization, RCC,
verification and user-agent secrets. The generated service configs receive the
matching website values:

| Service config | Website setting |
| --- | --- |
| `game-arbiter/config.json`: `authorization` | `ArbiterAuthorization` |
| `game-server/config.json`: `authorization` | `GameServerAuthorization` |
| `game-server/config.json`: `websiteBotAuth` | `BotAuthorization` |

JWT keys are provisioned in the ignored, owner-readable
`Roblox/Roblox.Website/local-secrets.json`. It supplies independent `Jwt:Sessions`
and `Jwt:Games` keys without changing an older `appsettings.json`. The website
loads this overlay and fails early with an actionable error if a key is missing,
short or still an example. Rerun `./scripts/setup.sh` when upgrading an older
checkout. Existing valid JWT keys are reused; conflicting keys are reported, not
rotated, so rerunning setup does not log everyone out.

Fresh frontend configs copy the website's `Authorization` only into
`serverRuntimeConfig.backend.authorization` and use its `BaseUrl` for the public
API URLs. No secret goes into `publicRuntimeConfig`. Existing frontend configs
are preserved; if necessary, add the private backend authorization manually.
Other example authentication secrets in an **existing** website config are not
automatically replaced: review them before exposing the site publicly.

If you ran the older setup script, `ArbiterAuthorization` may still be `change-me`.
Set it to a private random secret in the existing `appsettings.json` before
rerunning setup. Existing service configs with mismatching secrets are reported,
not silently overwritten. Keep all real configs out of Git.

Start the arbiter separately with `(cd game-arbiter && npm start)`; it has no npm
dependencies. `dev.sh` intentionally only runs the web stack. Do not start the
legacy game-server expecting it to supply the missing Linux RCC runtime.

## steps

*(this is the manual version of the quick start above - skip it if you already ran the scripts)*

1. Create a PG user and DB, then create a file called `config.json` in **`api/`** (it is gitignored, so you have to make it yourself). Put this in it (replacing the DB, User, and Pass with your credentials):

    ```json
    {
        "knex": {
            "client": "pg",
            "connection": {
                "host": "127.0.0.1",
                "user": "postgres",
                "password": "postgres",
                "database": "db_name_here"
            }
        }
    }
    ```

2. Install nodejs, go (lang), and dotnet 8 (the projects all target net8.0). Go into the **`api/`** directory in a terminal and run:

    ```bash
    npm i
    npx knex migrate:latest
    ```

    This creates the database schema. `api/` is legacy from here on - the real API lives in the .NET project.

3. Go into **`Roblox/Roblox.Website/`**. There is **no** `appsettings.json` in the repo (an old `appsettings.example.json` was never shipped with the leak, and `appsettings.json` is gitignored). This repo now includes **`appsettings.example.json`** - copy it to `appsettings.json` and fill it in:

    - `Postgres` / `Redis` - your DB and Redis connection strings.
    - `BaseUrl` - `http://localhost:5000` for local dev.
    - `ArbiterBaseUrl` - `http://127.0.0.1:4310/` for the local arbiter, or the URL of the machine running it.
    - `ArbiterAuthorization`, `GameServerAuthorization`, `BotAuthorization` - private secrets, shared with the corresponding service configs (see the table above).
    - `Jwt:Sessions`, `Jwt:Games` - independent persistent secrets of at least 32 characters. Run `python3 scripts/provision_website_secrets.py Roblox/Roblox.Website` from the repo root to provision missing keys without rewriting this config.
    - `Authorization`, `RccAuthorization`, `VerificationSecret`, `UserAgentBypassSecret` - replace the example values with private secrets for a manual setup.
    - `OwnerUserId` - **an array**, e.g. `[ 12 ]`. Put your own user id in it after you register (see step 7).
    - `Directories` - **every value must end with a trailing slash**. Pre-filled in the example: `Public` → `api/public/`, `AdminBundle` → `admin/public/`, `XmlTemplates` → `Roblox/Roblox.Libraries/Templates/`, `JsonData` → `Roblox/Roblox.Libraries/Json/`, `ScriptsData` → `Roblox/Roblox.Libraries/StudioScripts/`. The asset/storage/thumbnail/group-icon folders are your own runtime data dirs - create them.

    Also copy **`game-servers.example.json`** to **`game-servers.json`** in the same folder. The website reads it on startup and **will not boot without it**.

    On linux/macOS `./scripts/setup.sh` writes all of this for you, with the paths already pointing at your clone - it is a lot less error prone than editing json by hand.

4. Still in `Roblox/Roblox.Website/`, run:

    ```bash
    dotnet run
    ```

    If everything is successful, you should be able to visit the site at `http://localhost:5000/`.

5. Start up the admin service by opening a new terminal, going into the **`admin/`** folder, and running:

    ```bash
    npm i
    npm run dev
    ```

    It does not host itself - the website serves the built bundle from `admin/public/` at `http://localhost:5000/admin/`.

6. Open **`frontend/docs/get-started.md`** and follow the guide for setting up 2016-roblox (this is the frontend). In short:

    ```bash
    node ./util/create_config.js
    npm i
    npm run dev
    ```

    The generator already points `baseUrl` at `http://localhost:5000` and
    `apiFormat` at `http://localhost:5000/apisite/{0}{1}`. Keep them on the same
    domain if you change either value. The frontend runs on `http://localhost:3000`.

    Also run `npm run build` before deploying. `next dev` skips the production
    type check; TypeScript 4.9 needs the pinned Node 16 declarations, not the
    latest `@types/node` pulled in by Jest.

7. Register an account, then copy your user id and replace the `12` in `"OwnerUserId"` (inside `appsettings.json`) with your user id - keeping it an **array**, e.g. `[ 123 ]`. `ctrl+c` the `dotnet run` command to close it, then run it again to start the site back up. You should now be able to go to:

    ```
    http://localhost:5000/admin/
    ```

    for admin stuff.

8. In order to upload things, you will have to start up the "asset validation service". You can do this by going into **`AssetValidationServiceV2/`** in a terminal and running:

    ```bash
    go run main.go
    ```

    It listens on `:4300` - make sure `AssetValidation:BaseUrl` in `appsettings.json` points at it.

## quick start on Windows

`start.bat` starts the legacy components in separate windows; it does not start the new arbiter. Generate or copy the configs first, then run `npm start` from `game-arbiter/` separately. The Windows RCC binaries do not supply the unfinished game launcher. See **WHAT IS STILL MISSING** above.

## common gotchas

- **`game-servers.json` and `appsettings.json` are required**, not optional. Config files are gitignored, so they will never show up after a fresh clone. `./scripts/setup.sh` writes both.
- **Directories need trailing slashes** - the code concatenates paths like `JsonDataDirectory + "avatar-colors.json"`. Missing slash = crash or missing files.
- **Run the website from its own folder** (`Roblox/Roblox.Website`). `appsettings.json` and `game-servers.json` are read from the working directory, so `dotnet run --project ...` from somewhere else will not find them. The published build works too, both json files are copied next to the binary now.
- **Everything is case sensitive on linux.** The css folder in the repo is `api/public/css/Roblox`, and the code used to ask for `css/roblox` - that only ever worked because windows does not care.
- **Build the admin at least once** (`cd admin && npm i && npm run build`). `/admin/` reads `admin/public/build/bundle.js`, which does not exist until you build it.
- **`api/` is legacy** (`api/README.MD`): it is only used for its `public/` folder and migrations. The actual API is the .NET project in `Roblox/Roblox.Website/`.
- **`game-server/README.md` says "THIS IS OUTDATED, DO NOT USE".** Treat game-server/game-renderer as a starting point, not a finished service. Its `config.example.json` is a configuration reference, not proof that the game/render stack works.
- **`Notes.md` describes the original omission.** `game-arbiter/` is now supplied, but requires a compatible RCC launcher. The generated command points at a Windows executable; Docker cannot replace binaries that are not in the repo.
