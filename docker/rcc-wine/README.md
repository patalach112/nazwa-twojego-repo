# Windows RCC on a Linux Docker host

This is an **experimental Wine runtime**, not a Linux RCC binary, a complete
revival deployment or a guarantee that every Windows executable works.
The old `game-server/docker/rccservice.dockerfile` requires a missing
`rcc-docker/` directory. This image instead uses the actual bundled Windows
executables through 32-bit Wine, Xvfb and software OpenGL on an amd64 Linux host.
It does not download, patch or redistribute the executable inside the image.
Only use binaries you are entitled to run.

## Start one SOAP service on Debian

Docker Engine and its Compose plugin must already be installed. From the
repository root:

```bash
docker compose -f docker-compose.rcc-wine.yml up --build
# In another terminal: inspect the real SOAP health result, not just "running".
docker compose -f docker-compose.rcc-wine.yml ps
docker compose -f docker-compose.rcc-wine.yml exec rcc-wine python3 /usr/local/bin/healthcheck.py
# A separate Lua smoke job, without loading any place or connecting a client:
docker compose -f docker-compose.rcc-wine.yml exec rcc-wine python3 /usr/local/bin/lua_probe.py
# Stop RCC, Wine and Xvfb together:
docker compose -f docker-compose.rcc-wine.yml down
```

The default is the 2016 executable in `RCCService/`. Select a different build by
mounting its **whole directory**, including its own content, shaders and DLLs:

```bash
RCC_DIRECTORY=./RCCService/RCCService2017 docker compose -f docker-compose.rcc-wine.yml up --build
RCC_DIRECTORY=./RCCService/RCCService2018 docker compose -f docker-compose.rcc-wine.yml up --build
RCC_DIRECTORY=./RCCService/RCCService2020 docker compose -f docker-compose.rcc-wine.yml up --build
```

Stop the previous container before changing builds. The Wine prefix is ephemeral
and is initialized for each new container, so a previous build cannot poison a
later one. No Docker socket, host network or privileged mode is required. The
process runs as UID 1000, drops capabilities and mounts the binaries read-only.
The selected build is copied into a private writable runtime so logs and
`AppSettings.xml` cannot change the original files. The nested RCC builds are
excluded when using the root 2016 directory.

`RCC_BASE_URL` defaults to `http://host.docker.internal:5000`, the .NET website on
the Docker host. Set it to a reachable website URL when the backend is elsewhere.
The generated XML only exists inside the container. Never use container
`localhost` to address a backend on the host. `RCC_SOAP_PORT` changes the
**host-side** port; RCC still listens on 64989 inside the container. SOAP is
published on host loopback only and must not be exposed to the internet.

## What the test can and cannot establish

`scripts/test_rcc_wine.py` runs all four real executables, one at a time, with
`--network none`. It waits for a nonempty SOAP `GetVersionResult`, opens a tiny
Lua job returning a known string, closes that job, and checks signal shutdown.
Each build has a bounded startup deadline and logs are retained on failure.
The GitHub workflow is an **optional compatibility experiment**; its green
conclusion alone is not proof of RCC compatibility. Read its per-build report
and annotations. A failed build remains unsupported.

These checks do **not** load a real place, render an avatar, create the game UDP
listener, start the client proxy, send `/gs/*` callbacks or join from a client.
The new arbiter still needs a launcher that performs that bootstrap. Do not
point its process command at a detached `docker run` and treat the container ID
as a ready game. A production wrapper must stay alive, arrange the reserved
ports, load the correct Lua script and stop its container when killed.

Local manual reproduction of the isolated compatibility test:

```bash
docker build --tag rcc-wine-test docker/rcc-wine
python3 scripts/test_rcc_wine.py
# Restrict the experiment if needed:
RCC_WINE_YEARS=2016 python3 scripts/test_rcc_wine.py
```

## Other executables

The four RCC executables have PE machine type `0x14c` (32-bit x86). Several
adjacent DLLs supply Visual C++ runtimes. The root `Log.dll` and `boost.dll` are
13-byte text placeholders, not usable DLLs; they are not in the static import
table, but a dynamically loaded dependency can still fail. One unrelated
`.exe` file in the 2018 folder is not a valid PE file. These files are not
silently "fixed" by installing Wine.

A different console executable can be mounted with its own dependencies and
selected using `RCC_EXE`, but it has no RCC SOAP health contract. GUI launchers
and updaters may need extra dependencies and an interactive display; Xvfb only
provides a headless display, not a desktop in the browser. Compatibility must
be checked per executable. No bootstrapper or patcher is executed by this test.

Linux containers cannot supply a Windows kernel. Native Windows containers
require a Windows host or VM. Wine provides a compatibility layer on Debian,
not full Windows virtualization. The .NET 8 website already runs natively on
Linux, and Linux builds should be used for tools such as FFmpeg rather than
routing every Windows `.exe` through this RCC image.
