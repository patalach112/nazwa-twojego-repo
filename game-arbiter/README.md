# Game arbiter

The website already calls an arbiter, but the original source never included one
(see `../Notes.md`). This service implements that HTTP contract using Node's
standard library. Node 18 or newer is required; there are no npm dependencies.

Run `../scripts/setup.sh` from the repo root, or copy `config.example.json` to
`config.json` and set `authorization` to the website's `ArbiterAuthorization`.
The service refuses the example secret. Then run:

```bash
cd game-arbiter
npm start
```

The website's `ArbiterBaseUrl` defaults to `http://127.0.0.1:4310/`. If it is on
another machine, change that URL and the arbiter's `host`. Keep the arbiter and
RCC SOAP ports private; the shared secret is not a substitute for a firewall.
`ARBITER_HOST` and `ARBITER_PORT` can override the listener, for example in a
container. The default is loopback, not a public listener.

## Launching RCC

`rccCommand` is an **array**, with the executable first and one argument per
entry. It runs without a shell, so quotes, spaces and shell metacharacters in an
argument are literal. Paths in `rccCwd` are relative to `config.json`, not the
terminal's working directory. `{rccCwd}` expands to its absolute path.

Other placeholders match the website's start request: `{jobId}`, `{placeId}`,
`{universeId}`, `{creatorId}`, `{placeVersion}`, `{maxPlayerCount}`, `{year}`,
`{matchmakingContextId}`, `{rccPort}`, `{gameServerPort}` and `{proxyPort}`.
Unknown placeholders are rejected at startup. Requests are validated before
substitution; the website cannot choose an executable or supply a shell command.

The example points at the **Windows-only** `RCCService/RCCService.exe`. On Debian
it must be replaced with a compatible RCC launcher. The Dockerfile in
`game-server/docker` expects a `rcc-docker/` directory containing Linux binaries;
those binaries are not in this repo.

This arbiter supervises the configured process. A successful start means the
process was spawned, **not** that a place is ready to join. The launcher must
also open the RCC job with the appropriate Lua script and arrange any proxy
needed by the client. The bare example command only starts RCC's SOAP listener;
it does not load a place. A wrapper must stay alive for the job's lifetime and
forward shutdown signals to its children (including containers).

## HTTP contract

Every endpoint, including the server list, requires `PJX-ArbiterAUTH` with the
shared secret. POST requests require `Content-Type: application/json`.

- `POST /start-game-server`: the request from `ArbiterHttpClient` in
  `Roblox.Services/Games/GameServer.cs`. Returns the job ID and process ID.
  Repeating the same request, including concurrent retries, reuses the process.
  Changing an existing job's parameters or reusing a reserved port returns 409.
- `POST /kill-game-server`: `{ "jobId": "UUID" }`. Waits for process exit,
  escalating to a forceful kill after `stopTimeoutMs`. Killing an unknown job is
  harmless and returns 200.
- `POST /evict-player`: `{ "gameId": "UUID", "userId": 123, "messageVersionId": 0 }`.
  Sends a SOAP `Execute` request to that job's local RCC port to kick the player.
  Unknown jobs return 404; connection failures and SOAP faults return 502, not
  fake success. `soapNamespace` matches the legacy game-server's namespace and
  may need adjustment for a different RCC build.
- `GET /servers`: returns `{ "servers": [...] }` with launch metadata, never the
  authorization secret. The list is held in memory and cleared when a process
  exits. The arbiter shuts down tracked children on SIGINT/SIGTERM.

RCC eviction and real game startup still need a test on a compatible host. The
website's missing `/gs/*` handlers also prevent heartbeats and player reports
from reaching the database; this service does not add those handlers.

## Tests

```bash
npm test
```

Tests use a harmless Node process and a local fake SOAP endpoint, not RCC. They
exercise authentication, malformed requests, concurrent retries, placeholder
substitution, process cleanup, missing executables and SOAP error handling.
