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

## Still outside this stack

There is no operational RCC launcher/proxy or verified real game join. The
experimental RCC Wine compose file is separate; 2020 JSON execution still needs
testing. Disabled rendering produces pending thumbnails, not rendered avatars.
Chat, trade/economy workflows, uploads/Go validation, account recovery and 2FA
end-to-end behavior are not established by the basic account test.

The original signing keys shipped with this source remain compromised; rotate
and coordinate compatible keys before public use. ApplicationGuard/global
CSRF removal remains intentional, and cookie-authenticated API writes need a
replacement CSRF strategy before public exposure. This stack does not silently
restore the middleware the owner requested to remove.
