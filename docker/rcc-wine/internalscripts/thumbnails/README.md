# Thumbnail scripts RCC 2020 loads

RCC's JSON `Thumbnail` mode reads `internalscripts/thumbnails/<Settings.Type>.lua` from
its runtime copy and passes `Settings.Arguments` to that file as varargs. The bundled
2020 build ships no thumbnail scripts, so this directory supplies them and
`docker/rcc-wine/entrypoint.sh` installs the set into the ephemeral runtime copy on
startup (the mounted build stays untouched).

Each file declares its argument list in its first line as `-- args: a, b, c` and the
HTTP renderer in `docker/renderer/` builds exactly those arguments in that order.
`scripts/test_renderer_contract.py` fails when a script and the renderer disagree, so a
renamed or reordered argument cannot silently render the wrong thing.

The shapes follow this repository's own legacy scripts in
`Roblox/Roblox.Rendering/internalscripts/` (`Image.lua`, `Model`/`Hat`, `Mesh`, `Head`,
`Clothing`, `Avatar`, `Closeup`), converted from `%placeholder%` text substitution to
varargs because that is what this build passes. No code from the Korone reference was
used here. The avatar rigs come from the build's own `content/avatar/character.rbxm`
and `characterR15.rbxm`, so avatar and clothing renders need no body-part download.

| File | `Settings.Type` values the renderer uses | Purpose |
| --- | --- | --- |
| `Image.lua` | `Image`, `Decal` | texture render of an uploaded image asset |
| `Model.lua` | `Model`, `Hat` | 3D model or accessory photographed in an empty scene |
| `Mesh.lua` | `Mesh` | mesh on a part through a `SpecialMesh` |
| `Head.lua` | `Head` | uploaded mesh replacing the bundled rig's head |
| `Clothing.lua` | `Clothing` | bundled rig wearing an uploaded shirt or pants texture |
| `TeeShirt.lua` | `TeeShirt` | the same framing for a t-shirt asset |
| `Avatar.lua` | `Avatar` | full avatar: bundled rig, account colours, local clothing |
| `Closeup.lua` | `Closeup` | headshot: the same rig framed on the head |

Every script asserts its arguments and returns the base64 image string from
`ThumbnailGenerator`. A built-in fallback cannot hide here: the renderer rejects responses
that are not decodable images with the requested size, and the integration test compares
real pixels.
