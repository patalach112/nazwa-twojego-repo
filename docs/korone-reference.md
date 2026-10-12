# Korone reference snapshot

We reuse selected asset and rendering pieces of
[`muserlogiccpu/korone-src`](https://github.com/muserlogiccpu/korone-src) in
this repository. The project is **not** switched over to that code base; the
current Marine website, accounts, database and Docker layout stay in place and
only the specific files listed in the task are compared and, when needed,
ported to .NET 8.

## Snapshot

- Repository: `https://github.com/muserlogiccpu/korone-src`
- Analysed commit: `7b0f0b015ce6cd90b85346be4b80600e85683ffe` (*Update README.md*, 2026-09-24)
- License: `LICENSE` in that repository is the **Unlicense** (public domain
  dedication). Keep the attribution above when code is ported from it.
- The GitHub repository is around 1.4 GB, so never clone it inside this
  repository. Use a blobless, sparse reference copy outside the tracked code:

```bash
git clone --filter=blob:none --no-checkout \
  https://github.com/muserlogiccpu/korone-src /tmp/korone-src-reference
cd /tmp/korone-src-reference
git fetch --depth 1 origin 7b0f0b015ce6cd90b85346be4b80600e85683ffe
git sparse-checkout init --cone
git sparse-checkout set Roblox/Roblox.Services/Assets Roblox/Roblox.Rendering.Contracts \
  Roblox/Roblox.Rendering Roblox/Korone.RccServiceArbiter
git checkout 7b0f0b015ce6cd90b85346be4b80600e85683ffe
```

`docs/`, `scripts/templates/`, `Roblox/Korone.RccServiceArbiter/RenderScripts`
or other paths can be added to the sparse set when a later step needs them.

## Safety rules for this reference

The donor `README.md` states that parts of the source are AI-generated and that
a backdoor (a reverse shell) shipped in the August 2025 source it is based on.
Treat the snapshot as untrusted input:

- read and copy individual files; never run its patchers, bootstrappers,
  installers, `*.exe`, `*.bat`, `*.ps1` or any command taken from its
  documentation,
- never execute donor binaries or replace `RCCService/RCCService2020/` with
  donor binaries,
- inspect any copied file and its dependencies before using it, keep the .NET 8
  target of this repository, and adapt interfaces instead of importing whole
  projects.

## Status

- Stage 1 (one PNG import through the existing admin API) needs **no** donor code.
  This snapshot is the input for the renderer contracts, HTTP client and the
  minimal renderer service of the later stages.
