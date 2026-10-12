# Local renderer service

The website's `Roblox.Rendering.RenderingHandler` posts JSON to `Render:BaseUrl` and
expects `{"success": bool, "message": ..., "data": "<base64 image>"}`. This service is
that endpoint for the local stack: it turns each request into a JSON `Thumbnail` job
for RCC 2020 and returns the real image.

RCC runs **inside this same container**, started by `supervise.sh` through the launcher
from `docker/rcc-wine`, because RCCService binds its SOAP port to the loopback of its own
container: a separate RCC container is healthy inside and answers `Connection refused` to
everything else. The supervisor waits until RCC really answers SOAP, then becomes the
renderer so container shutdown reaches the renderer first. RCC gets no privileges, no
published port and a read-only root; the only published port is the renderer's own 3521,
bound to `127.0.0.1`. Nothing is faked: when a render cannot be produced the answer is a JSON error
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
generated or rotated and no second secret has to be kept in sync. The same value is
recorded in the generated `docker/local/compose.env`, which the stack passes to compose,
so the renderer container receives it as `RENDERER_AUTHORIZATION`. That is deliberate: a
single-file bind mount does not reliably show a config rewritten after the container
started, and compose recreates a service whose environment changed, so starting or
recreating the renderer always hands it the enabled value. The mounted config stays as
the fallback for a manual `docker run`. Enabling therefore means starting (or recreating)
the `renderer` profile; the test starts it after enabling. Without the profile (or with
`Render:BaseUrl` empty) rendering stays disabled, uploads and asset delivery keep
working, and thumbnails simply stay `Pending`.

`scripts/test_local_renderer.py` does exactly this against a disposable `*_tests`
stack and asserts the rendered pixels, so the flow is checked in CI rather than by
hand.

## Request contract

Every route is POST with `Authorization` and a JSON body; a successful answer has
`data` as base64 of a PNG. Each route selects one of **RCC 2020's own thumbnail
scripts** (`internalscripts/thumbnails/<Settings.Type>.lua`) and passes the arguments
that script declares in its own first `local ... = ...` line.
`scripts/test_renderer_contract.py` parses those files and fails when a route and a
script stop agreeing, and it also fails if any file of ours would shadow one of them.
No Roblox-authored script is modified, replaced or installed by this repository.

| Route | Body | RCC script | Arguments |
| --- | --- | --- | --- |
| `POST /image/image` | `assetId` | `Image.lua` | image id, base URL, `PNG`, 420, 420 |
| `POST /catalog/model` | `assetId` | `Model.lua` | asset URL, `PNG`, 420, 420, base URL |
| `POST /catalog/hat` | `assetId` | `Hat.lua` | asset URL, `PNG`, 420, 420, base URL |
| `POST /catalog/mesh` | `assetId` | `Mesh.lua` | asset URL, `PNG`, 420, 420, base URL |
| `POST /player/thumbnail` | `userId` | `Avatar.lua` (R6) / `Avatar_R15_Action.lua` (R15) | base URL and appearance URL, in that script's own order, `PNG`, 420, 420 |
| `POST /player/headshot` | `userId` | `Closeup.lua` | base URL, appearance URL, `PNG`, 420, 420, `true`, 40, 60, 0, 0 |
| `GET /healthz` | - | - | RCC version, how long ago RCC last answered, whether a render is in progress, and the warm-up report |

The closeup zooms are the values this repository's own `game-renderer/scripts/Closeup.json`
recorded; `Avatar.lua` and `Avatar_R15_Action.lua` disagree about the first two
arguments (the shipped files are the authority and the renderer follows each one).

Avatar and headshot renders hand RCC the account's appearance document at
`<WEBSITE_BASE_URL>/v1.1/avatar-fetch?userId=`, which is the same document the game
server uses; RCC's own scripts then load the character. That path is *attempted*, not
claimed: the integration test reports what a real call returned.

Asset URLs are built from configured values only
(`<WEBSITE_BASE_URL>/asset/?id=<assetId>`), never from the request body, so the renderer
cannot be made to fetch an arbitrary URL.

These endpoints answer `501` with the concrete missing piece, so the thumbnail stays
pending instead of being faked:

| Route | Why it is not answered yet |
| --- | --- |
| `/image/clothing` | RCC's `Shirt.lua`/`Pants.lua` load a mannequin asset; this stack has no local one |
| `/image/teeshirt` | this build ships no TeeShirt thumbnail script |
| `/catalog/head` | RCC's `Head.lua` needs the same mannequin asset |
| `/catalog/bodypart`, `/catalog/package` | RCC's scripts need a rig URL this stack does not serve |
| `/catalog/animationsilhouette`, `/catalog/animation` | animation assets are not wired in |
| `/game/thumbnail` | place thumbnails need a real place to load |
| `/player/thumbnail-3d` | this build exposes no model/mesh renderer for 3D objects |

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `RENDERER_PORT` | `3521` | listen port inside the container |
| `RCC_SOAP_URL` | `http://127.0.0.1:64989/` | RCC SOAP endpoint, in this container |
| `RCC_SOAP_NAMESPACE` | `http://silrev.biz/` | SOAP namespace of this build |
| `WEBSITE_BASE_URL` | `http://website:5000` | where asset and appearance documents come from |
| `RENDERER_AUTHORIZATION` | unset | the accepted `Authorization` value; set from `docker/local/compose.env` by `--enable-renderer` and used in preference to the mounted config |
| `RENDERER_WEBSITE_CONFIG` | `/config/website.json` | mounted website config to read `Render:Authorization` from |
| `RENDERER_JOB_TIMEOUT` | `120` | seconds a single RCC job may run |
| `RENDERER_QUERY_TIMEOUT` | `10` | seconds for a SOAP query such as `GetVersion` |
| `RENDERER_MAX_RETRIES` | `1` | additional attempts after a failed render (bounded) |
| `RENDERER_HEALTH_STALE` | `120` | seconds a recent RCC answer keeps `/healthz` green |

## Deliberate limits

* One render at a time, because RCC renders into a single DataModel. A render queue
  under this lock is what keeps a burst of uploads from corrupting each other's scene.
* RCC and the renderer share a container and a memory/CPU limit; the renderer is the
  foreground process, so a stuck RCC is stopped by the same `SIGTERM`/`stop_grace_period`
  that stops the renderer.
* The renderer deliberately does not declare `depends_on: website`. It needs neither the
  website nor a healthy database to start, and depending on the website makes
  `restart website` restart the renderer and cut the renders it is doing. The website is
  the one that needs the renderer configured, so a website restart never interrupts a
  render. The renderer takes `RENDERER_AUTHORIZATION` from the environment and only
  falls back to re-reading `Render:Authorization` from the mounted config.
* Every job gets an `expirationInSeconds` and a matching client timeout, and `CloseJob`
  runs in a `finally` block, so a stuck render is reported and cleaned up instead of
  hammering RCC.
* Retries are bounded by `RENDERER_MAX_RETRIES` and stop on the first success.
* A render is refused, not stored, when it is one of the fallback images the shipped
  scripts carry. `Image.lua` answers a render it cannot do with an embedded 512x512
  transparent PNG - larger than the 420 pixels a render must be, so a size check passes it
  and the website then resizes it into a blank thumbnail that looks finished. The renderer
  extracts those literals from the build's own scripts (`RCC_SCRIPTS_DIRECTORY`) and
  answers 502 naming the script, so the thumbnail stays pending instead of white.
* `scripts/probe_rcc_image.py` asks RCC itself what the shipped script's calls do
  (`GetObjects`, `ClickTexture` on the asset URL, on a static website PNG, the same after
  `PreloadAsync`, and `ClickTexture` on `rbxassetid://`). It writes a unique diagnostic
  script under `internalscripts/scripts/` - never next to the shipped thumbnail scripts -
  sends it as the JSON `ExecuteScript` job this build accepts (a raw Lua chunk is refused
  with "Attempted to execute with invalid JSON"), removes it again and stores nothing. The
  integration test calls it when a texture assertion fails, so the reason comes from RCC
  rather than from a guess.
* The response is checked before it is returned: it must be decodable base64, a PNG,
  and at least as large as the requested edge. This rejects RCC's own 1x1 hourglass
  placeholder, so a placeholder can never be stored as a finished thumbnail.
* The process runs as an unprivileged user with `cap_drop: [ALL]`,
  `no-new-privileges`, and no Docker socket; RCC gets no privileges either and is
  reachable only from this service on the compose network.
* The website's own client gives a render request ten seconds
  (`RenderingHandler`'s `HttpClient.Timeout`). The startup warm-up renders the build's
  own avatar rig through RCC's own `Model.lua`, which pays Wine's and RCC's
  first-render cost before user requests arrive; a render that still exceeds the
  ten-second budget fails in the website's log and the thumbnail stays pending instead
  of being stored wrong. Longer renders need a website-side change and are not
  pretended to work here.
* `/healthz` distinguishes a busy RCC from a dead one. `OpenJobEx` only answers when
  its render finishes, so while a job is in flight the renderer reports the last RCC
  contact it had and stays green; while idle it re-asks RCC and only answers `503`
  when RCC has not spoken within `RENDERER_HEALTH_STALE` seconds (120 by default).
  RCC is contacted once at startup, before the warm-up job takes it.

## What is not proven

`scripts/test_local_renderer.py` covers image assets (two different textures, checked
pixel by pixel) and model assets through the real website, and it reports the result of
a real avatar render attempt instead of assuming one. The hat, mesh, headshot and
clothing routes are implemented (or refused with a reason) but **not covered by that
test**, so they are unproven. Place thumbnails, animations, packages, body parts and
the `-thumbnail3d` objects are unimplemented on purpose. No client-visible claim is made
about any of them.
