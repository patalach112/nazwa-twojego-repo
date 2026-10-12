# Local renderer service

The website's `Roblox.Rendering.RenderingHandler` posts JSON to `Render:BaseUrl` and
expects `{"success": bool, "message": ..., "data": "<base64 image>"}`. This service is
that endpoint for the local stack: it turns each request into a JSON `Thumbnail` job
for RCC 2020 (running under Wine in the `rcc-render` container) and returns the real
image. Nothing is faked: when a render cannot be produced the answer is a JSON error
with a non-2xx status, so the website keeps a pending thumbnail.

## Enable it (off by default)

```sh
./scripts/local-stack.sh up -d --build                 # first start creates docker/local/*
python3 scripts/configure_local_docker.py --enable-renderer
./scripts/local-stack.sh --profile renderer up -d --build
./scripts/local-stack.sh restart website
```

`--enable-renderer` writes `Render:BaseUrl = http://renderer:3521/` and reuses the
website's existing `RccAuthorization` value as `Render:Authorization`; no secret is
generated or rotated and no second secret has to be kept in sync. The renderer reads
that value from the mounted `docker/local/website.json` and re-reads it while it is
stale, so enabling or changing it needs no restart of the renderer. Without the
profile (or with `Render:BaseUrl` empty) rendering stays disabled, uploads and asset
delivery keep working, and thumbnails simply stay `Pending`.

`scripts/test_local_renderer.py` does exactly this against a disposable `*_tests`
stack and asserts the rendered pixels, so the flow is checked in CI rather than by
hand.

## Request contract

Every route is POST with `Authorization` and a JSON body; a successful answer has
`data` as base64 of a PNG. The argument list of each route is the varargs list of the
matching script in `docker/rcc-wine/internalscripts/thumbnails/` and is verified by
`scripts/test_renderer_contract.py`, so a script and this table cannot drift apart.

| Route | Body | RCC script | Arguments passed |
| --- | --- | --- | --- |
| `POST /image/image` | `assetId` | `Image.lua` | asset URL, `PNG`, 420, 420 |
| `POST /catalog/model` | `assetId` | `Model.lua` | asset URL, `PNG`, 420, 420, hide sky, crop |
| `POST /catalog/hat` | `assetId` | `Model.lua` | same as the model render |
| `POST /catalog/mesh` | `assetId` | `Mesh.lua` | asset URL, `PNG`, 420, 420 |
| `POST /catalog/head` | `assetId` | `Head.lua` | asset URL, `PNG`, 420, 420, bundled rig |
| `POST /image/clothing` | `assetId` | `Clothing.lua` | asset URL, `PNG`, 420, 420, bundled rig |
| `POST /image/teeshirt` | `assetId` | `TeeShirt.lua` | asset URL, `PNG`, 420, 420, bundled rig |
| `POST /player/thumbnail` | `userId` | `Avatar.lua` | `PNG`, 420, 420, rig, six body colours, worn clothing URLs, base URL |
| `POST /player/headshot` | `userId` | `Closeup.lua` | same as the avatar render, framed on the head |
| `GET /healthz` | - | - | RCC version plus the warm-up report (no secrets) |

Asset URLs are built from configured values only
(`<WEBSITE_BASE_URL>/asset/?id=<assetId>`), never from the request body, so the renderer
cannot be made to fetch an arbitrary URL. Avatar requests read the
account's appearance through `GET http://website:5000/v1.1/avatar-fetch?userId=`, which
is the same document the game server uses.

`/catalog/package`, `/catalog/bodypart`, `/catalog/animationsilhouette`,
`/catalog/animation`, `/game/thumbnail` and `/player/thumbnail-3d` answer `501`: this
renderer does not implement them yet, and returning a substitute image would put a
wrong picture into the catalog.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `RENDERER_PORT` | `3521` | listen port inside the container |
| `RCC_SOAP_URL` | `http://rcc-render:64989/` | RCC SOAP endpoint |
| `RCC_SOAP_NAMESPACE` | `http://silrev.biz/` | SOAP namespace of this build |
| `WEBSITE_BASE_URL` | `http://website:5000` | where asset and appearance documents come from |
| `RENDERER_AUTHORIZATION` | unset | overrides the value read from the mounted config |
| `RENDERER_WEBSITE_CONFIG` | `/config/website.json` | mounted website config to read `Render:Authorization` from |
| `RENDERER_JOB_TIMEOUT` | `120` | seconds a single RCC job may run |
| `RENDERER_QUERY_TIMEOUT` | `10` | seconds for a SOAP query such as `GetVersion` |
| `RENDERER_MAX_RETRIES` | `1` | additional attempts after a failed render (bounded) |

## Deliberate limits

* One render at a time, because RCC renders into a single DataModel. A render queue
  under this lock is what keeps a burst of uploads from corrupting each other's scene.
* Every job gets an `expirationInSeconds` and a matching client timeout, and `CloseJob`
  runs in a `finally` block, so a stuck render is reported and cleaned up instead of
  hammering RCC.
* Retries are bounded by `RENDERER_MAX_RETRIES` and stop on the first success.
* The response is checked before it is returned: it must be decodable base64, a PNG,
  and at least as large as the requested edge. This rejects RCC's own 1x1 hourglass
  placeholder, so a placeholder can never be stored as a finished thumbnail.
* The process runs as an unprivileged user with `cap_drop: [ALL]`,
  `no-new-privileges`, and no Docker socket; RCC gets no privileges either and is
  reachable only from this service on the compose network.
* The website's own client gives a render request ten seconds
  (`RenderingHandler`'s `HttpClient.Timeout`). The startup warm-up pays Wine's and
  RCC's first-render cost before user requests arrive; a render that still exceeds the
  ten-second budget fails in the website's log and the thumbnail stays pending instead
  of being stored wrong. Longer renders need a website-side change and are not
  pretended to work here.

## What is not proven

`scripts/test_local_renderer.py` covers image assets (two different textures, checked
pixel by pixel) and model assets through the real website. The avatar, headshot,
clothing, head and mesh routes are implemented but **not yet covered by that test**, so
they are unproven. Placement/game thumbnails, animations, packages, body parts and the
`-thumbnail3d` objects are unimplemented on purpose. No client-visible claim is made
about any of them.
