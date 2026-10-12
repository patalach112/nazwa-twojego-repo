#!/usr/bin/env python3
"""Asks RCC itself what it does with one image asset, instead of guessing.

The build's own ``Image.lua`` renders a texture asset by asking
``game:GetObjects(baseUrl.."/asset/?id="..id)`` for a Decal and, when that does not give
it one, by clicking the asset URL directly; if either step fails it returns the
transparent placeholder it carries, and the stack resizes that placeholder into a blank
thumbnail. That makes the reason invisible from the website side, so this probe runs one
Lua job inside the renderer container (the only place that has RCC, over loopback) and
prints RCC's answer for each call the script makes.

It is a diagnostic: nothing is uploaded, no thumbnail row and no file is written, and the
result is never treated as a render. It reports RCC's own messages, which is what a fix
has to be based on.
"""

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CONTAINER = "renderer"
TIMEOUT = 240

# Tokens are substituted literally: the Lua contains % patterns of its own (gsub).
LUA = """local baseUrl, assetUrl, assetId = "__BASE_URL__", "__ASSET_URL__", __ASSET_ID__
local ContentProvider = game:GetService('ContentProvider')
local ThumbnailGenerator = game:GetService('ThumbnailGenerator')
local out = {}
local function report(name, ok, value)
    local shown = tostring(value):gsub('%s+', ' '):sub(1, 220)
    table.insert(out, name .. '=' .. tostring(ok) .. ' / ' .. shown)
end
report('SetBaseUrl', pcall(function() return ContentProvider:SetBaseUrl(baseUrl) end))
report('GetObjects', pcall(function() return #game:GetObjects(assetUrl) end))
report('ClickTextureUrl', pcall(function() return #ThumbnailGenerator:ClickTexture(assetUrl, 'PNG', 420, 420) end))
report('PreloadUrl', pcall(function() return ContentProvider:PreloadAsync({assetUrl}) end))
report('ClickTextureUrlAfterPreload', pcall(function() return #ThumbnailGenerator:ClickTexture(assetUrl, 'PNG', 420, 420) end))
report('ClickTextureAssetId', pcall(function() return #ThumbnailGenerator:ClickTexture('rbxassetid://' .. tostring(assetId), 'PNG', 420, 420) end))
return table.concat(out, ' | ')
"""

# Runs inside the container: reuse the renderer's own SOAP client rather than a second
# implementation of the protocol, and close the job in a finally block.
PROGRAM = '''
import sys
import uuid
import xml.etree.ElementTree as ET

sys.path.insert(0, '/app')
import renderer

lua = __LUA__
client = renderer.RccClient()
job_id = str(uuid.uuid4())
job = ET.Element('job')
for key, value in {'id': job_id, 'category': 0, 'cores': 1, 'expirationInSeconds': __TIMEOUT__}.items():
    ET.SubElement(job, key).text = str(value)
script = ET.Element('script')
ET.SubElement(script, 'name').text = 'ImageProbe'
ET.SubElement(script, 'script').text = lua
opened = False
try:
    reply = client.call('OpenJobEx', [job, script], timeout=__TIMEOUT__)
    opened = True
finally:
    if opened:
        identifier = ET.Element('jobID')
        identifier.text = job_id
        try:
            client.call('CloseJob', [identifier], timeout=30)
        except renderer.RenderError as error:
            print('CloseJob failed: ' + str(error), file=sys.stderr)
texts = [element.text.strip() for element in reply.iter() if element.text and element.text.strip()]
print(max(texts, key=len) if texts else 'RCC returned no text for the probe job')
'''


def run_probe(image_id, base_url="http://website:5000", run=None):
    """Returns the probe's output lines (RCC's own answers) as one string.

    ``run`` runs a command in the stack and returns ``(returncode, output)``; it exists so
    the probe's shape can be tested without Docker.
    """
    asset_url = f"{base_url.rstrip('/')}/asset/?id={int(image_id)}"
    lua = (LUA.replace('__BASE_URL__', base_url.rstrip('/'))
              .replace('__ASSET_URL__', asset_url)
              .replace('__ASSET_ID__', str(int(image_id))))
    program = (PROGRAM.replace('__LUA__', json.dumps(lua))
                      .replace('__TIMEOUT__', str(TIMEOUT - 30)))
    run = run or run_in_container
    code, output = run(program)
    if code != 0:
        return f"the probe could not run inside the {CONTAINER} container (exit {code}): {tail(output)}"
    return tail(output, lines=6)


def run_in_container(program):
    result = subprocess.run(["bash", "scripts/local-stack.sh", "exec", "-T", CONTAINER, "python3", "-"],
                            cwd=ROOT, input=program, capture_output=True, text=True, timeout=TIMEOUT)
    return result.returncode, (result.stdout + result.stderr).strip()


def tail(text, lines=8):
    kept = [line for line in text.splitlines() if line.strip()]
    return "\n".join(kept[-lines:])[-900:]


def main():
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        raise SystemExit("usage: probe_rcc_image.py <local asset id>")
    print(run_probe(int(sys.argv[1])))


if __name__ == '__main__':
    main()
