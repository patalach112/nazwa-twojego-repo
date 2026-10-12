# Browser rendering checks

Compilation and the .NET startup smoke test cannot prove that the frontend is
visible or interactive. `npm run test:browser` starts an isolated Next server
and uses real Chromium at desktop and mobile widths. It never replaces an
existing `config.json`; `FRONTEND_CONFIG_PATH` points Next at a temporary test
configuration instead.

```bash
cd frontend
npm install
npx playwright-core install --with-deps chromium
npm run test:browser
BROWSER_MODE=production npm run test:browser
```

Alternatively, set `BROWSER_EXECUTABLE_PATH` to an existing Chrome/Chromium
executable. Development mode detects missing browser hydration; production
mode also runs a real `next build` and checks the resulting server. The workflow
runs both. Screenshots and `report.json` are written to ignored
`.visual-results/<mode>/` and retained as CI artifacts, including failures.

The initial review reproduced a completely white development page caused by
removing Next's Fast Refresh runtime. Further checks found an empty anonymous
root, navigation outside a phone's viewport, page-wide carousel overflow,
missing thumbnail placeholders, stale image aspect-ratio probes, failed
external font requests and a dashboard feed that compared hostnames instead
of origins. These are rendering and wiring failures, not just build failures.

The checks cover the anonymous entry page, Games, Catalog and a signed-in
**sample** dashboard at 1440, 390 and 320 pixels. They exercise the search
suggestions and mobile sidebar, and check JavaScript errors, unexpected local HTTP failures,
thumbnail loading, fonts, logo preferences, clipped navigation and horizontal
page overflow. Legacy logo presets without bundled images fall back to the
default instead of requesting nonexistent files.
Login navigation is checked against `/auth/accountlogin`, the existing Razor
password/2FA form; signup links use `/auth/signup` (local accounts when enabled,
otherwise the original application flow). `/login` redirects rather than importing an external
cookie or calling a disabled authentication API. Direct Next previews rewrite
`/auth/*` to the configured backend using relative browser URLs.

## Important limits

`test/visual-fixtures.cjs` is an opt-in, in-memory fixture API. Its account,
items, thumbnails and games are **sample data**, never real database records or
sessions. The orange banner identifies this explicitly. Fixtures are not
loaded by normal `npm run dev` or `npm start`, and do not implement login.

These browser checks do not test the actual Razor page's visual layout, account
creation, successful authentication, 2FA, chat, every profile/editor page,
avatar rendering or game joins. The separate game-server workflow uses the
real .NET website, Postgres and Redis and verifies startup, authentication
boundaries and captcha-free invalid login. Real successful login and client
joins still need their own end-to-end tests. Original private signing keys
remain compromised, and removal of the global CSRF gate remains an intentional
security downgrade described in the root README.

## Container routing

`serverRuntimeConfig.backend.baseUrl` can point Next at an internal backend
service without exposing that address as the browser's API origin. Public URLs
must match `publicRuntimeConfig.backend.baseUrl` exactly (scheme, host and port)
and are remapped by the proxy. Existing configs without the private URL keep
using their public backend URL. Server authorization stays private.

The website's `FrontendBaseUrl` defaults to `http://localhost:3000`; containers
use `http://frontend:3000`. The website proxy preserves methods, bodies, cookies
and binary data, routes `/api/proxy` to Next and does not share personalized
HTML in a URL-only cache. Auth forms use the already-bundled Bootstrap assets
rather than requiring external CDNs. Pages using runtime configuration render
on the server instead of baking a build-time deployment URL into static HTML.

## Actual website accounts

The fixture suite remains a rendering regression, not login evidence.
`test/real-website.cjs` is a separate opt-in test against the real local Docker
stack, .NET session middleware, PostgreSQL and Redis. It registers through Razor,
logs out, rejects an invalid password, logs in correctly and checks account
session persistence after restarting frontend/backend. It uses a disposable
`*_tests` database and never seeds a fixture session. See
`../../docker/local-stack/README.md`; artifacts use `.visual-results/integration/`.
