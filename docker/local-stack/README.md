# Real local website on Debian

This stack runs the **actual .NET website, Next frontend, PostgreSQL and Redis**.
It does not load visual fixtures or create a predefined account. RCC, game
clients, asset validation, avatar rendering and game joins are separate work.
The current client target is 2020L; that is not a claim of a working game join.

## Requirements and start

Use a Linux Docker host with Docker Engine, the Compose plugin and Python 3.
The images build .NET 8 and Node 22 inside Docker; the host does not need those
SDKs. Internet access is required for the initial image/dependency downloads.
From the repository root:

```bash
./scripts/local-stack.sh
./scripts/local-stack.sh ps -a
./scripts/local-stack.sh logs --tail 80 website frontend migrations
```

Wait for both `website` and `frontend` to become healthy, not merely running.
Migrations must finish successfully before the backend starts. Open:

- **http://localhost:3000/** — Next frontend and relative backend rewrites.
- **http://localhost:5000/** — .NET backend, which also proxies frontend pages.

Register at `/auth/signup` with an ordinary username and password, then use
`/auth/accountlogin`. Password recovery and external OAuth are not configured;
keep your password safe. Local registration skips application approval and
starter-place/rendering side effects. Account status, staff authorization,
username/password validation, rate limits and Razor antiforgery remain.

Both ports bind only to host loopback. PostgreSQL and Redis are not published
on host ports, so this can coexist with the older database-only compose file.
Container addresses are internal; browser code uses the public site origin and
relative proxy routes instead of trying to reach `website` or container localhost.

## Persistent private configuration

The wrapper generates separate files under ignored `docker/local/`, including
random database/service/JWT secrets and writable data directories. It does not
replace `frontend/config.json`, website `appsettings.json`, or the native setup's
other files. Rerunning it preserves existing keys, passwords and configs byte
for byte; partial or conflicting configuration is reported instead of replaced.
Containers run as the calling Linux user's UID/GID so private 0600 config files
and uploads are accessible without making them world-readable.

```bash
./scripts/local-stack.sh down                       # keeps data and credentials
./scripts/local-stack.sh up -d                      # restarts the existing stack
./scripts/local-stack.sh restart website frontend   # keeps account sessions
```

Database/Redis state uses named Docker volumes; uploads use `docker/local/data/`.
Back up those volumes **and** the private configuration/data directory. Do not
use `down -v`, delete credentials or change the PostgreSQL password without a
planned backup/migration. Avoid sharing logs or private JSON/env files.

To choose another public origin or ports, configure **before the first start**:

```bash
python3 scripts/configure_local_docker.py \
  --public-base-url http://localhost:3100 --frontend-port 3100 --backend-port 5100
./scripts/local-stack.sh
```

Existing files are intentionally not rewritten when options change. A public
HTTPS deployment needs a deliberate reverse-proxy/origin configuration and
security review; this localhost stack is not a production deployment recipe.

No account is automatically made an owner. After registering, put your actual
user ID into `OwnerUserId` in `docker/local/website.json` and restart `website`.
Do not use a fixed guessed ID or grant every registered user staff privileges.

## Staff panel

The website image builds the Svelte staff panel from `admin/` and copies it to
`/app/admin/`, which is where `Directories:AdminBundle` points. An account in
`OwnerUserId` can therefore open **http://localhost:5000/admin/** and use the
real forms, for example **Create Item** (`/asset/create`) and **Create Clothing**.
Everyone else is redirected to `/home`, and an ordinary account keeps getting
401/403 from the `/admin-api/api/` endpoints behind those screens.

The panel is served by the .NET website, so use the backend port; the Next
frontend on port 3000 proxies the API but not `/admin/` itself. Panel screens
that need a renderer (re-render, thumbnails) remain pending until a renderer
exists; create, version and manage screens work without one. A native
(non-Docker) website still needs `cd admin && npm i && npm run build`.

## Real account/browser regression

The `Real website accounts` workflow builds these images and runs real Chromium
against both ports with a dedicated disposable database. It checks ordinary
registration, login, invalid passwords, logout/session replay, staff denial,
member views, local styles/images and session survival after backend/frontend
restart. Screenshots and a report are retained, never session cookies/configs.
No fixture account or fabricated authentication cookie is used.

Manual test reproduction must use a **new disposable setup**, not your real DB:

```bash
python3 scripts/configure_local_docker.py \
  --database-name marine_website_tests --project-name marine-website-tests --test
./scripts/local-stack.sh up --build -d
cd frontend
npm install
npx playwright-core install --with-deps chromium
cd ..
REAL_WEBSITE_TEST=1 node frontend/test/real-website.cjs
```

The generator refuses to reclassify an existing real setup as a test. The
browser script requires explicit opt-in, a matching `*_tests` database and
loopback URLs. It creates actual test accounts and restarts containers, so do
not point it at personal or shared production data.

## Local asset import check

`scripts/test_asset_import.py` imports one controlled PNG through the real
`/admin-api/api/asset/create` endpoint of the same disposable stack and verifies
the stored rows, the content-addressed file in `docker/local/data/assets/`,
anonymous delivery through `/asset/?id=`, survival of a `website` restart, the
refusal of a renamed text file, a path-like file name and oversized uploads, and
that a disabled renderer leaves the thumbnail pending instead of reporting a
finished one. It also loads the staff panel as the configured owner: the page,
the redirected `bundle.js`/`bundle.css` and the permissions endpoint must come
from the built bundle, while an anonymous visitor and an ordinary account are
still sent away from `/admin/`. It registers its own accounts, writes the new
user id into `OwnerUserId` (the documented mechanism, not SQL) and restores
`docker/local/website.json` afterwards.

```bash
python3 scripts/configure_local_docker.py \
  --database-name marine_website_tests --project-name marine-website-tests --test
./scripts/local-stack.sh up --build -d
ASSET_IMPORT_TEST=1 python3 scripts/test_asset_import.py
```

It refuses to run against a non-`*_tests` configuration. Rendering, avatar
thumbnails, the remaining panel screens that need a renderer and game joins are
**not** covered by this check; a green run proves the local import/delivery path
and that the panel is served and gated, not that rendering works.

## Still outside this stack

There is no operational RCC launcher/proxy or verified real game join. The
experimental RCC Wine compose file is separate. A controlled 2020 JSON
`ExecuteScript` job and one real 420x420 PNG thumbnail render of two different
DataModels passed under Wine (`RCC Wine smoke`); `GameServer` mode, avatar
renders and client joins remain untested. The website still has no renderer
service in front of RCC, so disabled rendering keeps producing pending
thumbnails rather than a rendered avatar.
Chat, trade/economy workflows, uploads/Go validation, account recovery and 2FA
end-to-end behavior are not established by the basic account test.

The original signing keys shipped with this source remain compromised; rotate
and coordinate compatible keys before public use. ApplicationGuard/global
CSRF removal remains intentional, and cookie-authenticated API writes need a
replacement CSRF strategy before public exposure. This stack does not silently
restore the middleware the owner requested to remove.
