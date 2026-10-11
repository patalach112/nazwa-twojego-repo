<div align="center">
</div>

<h1 align="center">Pekora/Project X  (latest version from 30 AUGUST 2025)</h1>
<p align="center">Pekora is roblox revival, i decided to leak this because chloe is an big racist and a femboy.</p>
<p align="center">Also please do not contact me on trying to get the source setup. there are many guides on how to and if you read this guide properly you shouldn't need help.</p>

<div align="center">

[![Go](https://img.shields.io/badge/GoLang-blue?logo=go)](https://github.com/tooblewtf/bloxd)
[![Dotnet 6](https://img.shields.io/badge/.NET-6.0.0-purple?logo=dotnet)](https://github.com/tooble.wtf/bloxd)
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
├── api/                      # legacy api: knex migrations + public/ (css, js, img, admin bundle target)
├── Roblox/                   # .NET 6 solution - the real website + api
│   ├── Roblox.Website/       # main website/api (dotnet run here)
│   ├── Roblox.Services/      # business logic
│   ├── Roblox.Libraries/     # Json/, Templates/, StudioScripts/ (config points here)
│   └── ...
├── admin/                    # svelte admin panel (built into admin/public/)
├── frontend/                 # 2016-roblox frontend (Next.js)
├── AssetValidationServiceV2/ # Go service for validating uploaded assets (:4300)
├── game-server/              # rcc manager for game servers
├── game-renderer/            # rendering service
└── RCCService/               # RCC binaries + content
```

# HOW TO SETUP

## things you need

- <a href="https://nodejs.org/dist/v18.16.1/node-v18.16.1-x64.msi">Node.js</a>, *to run the renderer/build panel*
- <a href="https://sbp.enterprisedb.com/getfile.jsp?fileid=1258627">PostgreSQL</a>, *for the database*
- <a href="https://builds.dotnet.microsoft.com/dotnet/Sdk/6.0.412/dotnet-sdk-6.0.412-win-x64.exe">.NET 6.0</a>, *to run the website*
- <a href="https://go.dev/dl/go1.20.6.windows-amd64.msi">Go</a>, *for asset validation*
- <a href="https://github.com/redis-windows/redis-windows/releases/download/8.6.2/Redis-8.6.2-Windows-x64-cygwin-with-Service.zip">Redis Server</a> *important*

## requirements

- at least Windows 10, Linux is untested as my server is a Windows machine. You should use Wine to run everything if you are using linux (or if running a Debian vps, you can use Proxmox and run a windows VM.)
- a 10 character long domain that supports both HTTP and HTTPS
- knowledge on how things like this work (you should have at least some experience with websites and coding to be able to host this. it's really not hard to set up if you know what you're doing.)

## steps

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

2. Install nodejs, go (lang), and dotnet 6. Go into the **`api/`** directory in a terminal and run:

    ```bash
    npm i
    npx knex migrate:latest
    ```

    This creates the database schema. `api/` is legacy from here on - the real API lives in the .NET project.

3. Go into **`Roblox/Roblox.Website/`**. There is **no** `appsettings.json` in the repo (an old `appsettings.example.json` was never shipped with the leak, and `appsettings.json` is gitignored). This repo now includes **`appsettings.example.json`** - copy it to `appsettings.json` and fill it in:

    - `Postgres` / `Redis` - your DB and Redis connection strings.
    - `BaseUrl` - `http://localhost:5000` for local dev.
    - `OwnerUserId` - **an array**, e.g. `[ 12 ]`. Put your own user id in it after you register (see step 7).
    - `Directories` - **every value must end with a trailing slash**. Pre-filled in the example: `Public` → `api/public/`, `AdminBundle` → `admin/public/`, `XmlTemplates` → `Roblox/Roblox.Libraries/Templates/`, `JsonData` → `Roblox/Roblox.Libraries/Json/`, `ScriptsData` → `Roblox/Roblox.Libraries/StudioScripts/`. The asset/storage/thumbnail/group-icon folders are your own runtime data dirs - create them.

    Also copy **`game-servers.example.json`** to **`game-servers.json`** in the same folder. The website reads it on startup and **will not boot without it**.

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

    Then in the generated `config.json` change the:

    ```
    https://{0}.roblox.com{1}
    ```

    API format to:

    ```
    http://localhost:5000/apisite/{0}{1}
    ```

    The frontend runs on `http://localhost:3000`.

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

`start.bat` fires up the frontend, asset validation, the website, game-server and RCCService in separate windows. **Note:** `game-server` needs edits before it actually works as a game/render service, and this source does **not** include `game-arbiter` (see `Notes.md`) - you have to write one yourself or game servers will not work.

## common gotchas

- **Stale paths in scripts.** Some `.bat` files and service files still point at the original author's machine (`C:\goober\prjx-frontend-patches\...`, `/home/website/RobloxWeb/services/...`). Fix them for your own box.
- **`api/` is legacy** (`api/README.MD`): it is only used for its `public/` folder and migrations. The actual API is the .NET project in `Roblox/Roblox.Website/`.
- **`game-server/README.md` says "THIS IS OUTDATED, DO NOT USE".** Treat game-server/game-renderer as a starting point, not a finished service.
- **Directories need trailing slashes** - the code concatenates paths like `JsonDataDirectory + "avatar-colors.json"`. Missing slash = crash or missing files.
- **`game-servers.json` and `appsettings.json` are required**, not optional. Config files are gitignored, so they will never show up after a fresh clone.
