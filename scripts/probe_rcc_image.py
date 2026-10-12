#!/usr/bin/env python3
"""Asks RCC itself what it does with one image asset, instead of guessing.

The build's own ``Image.lua`` renders a texture asset by asking
``game:GetObjects(baseUrl.."/asset/?id="..id)`` for a Decal and, when that does not give
it one, by clicking the asset URL directly; if either step fails it returns the
transparent placeholder it carries, and the stack resizes that placeholder into a blank
thumbnail. That makes the reason invisible from the website side, so this probe runs one
job inside the renderer container (the only place that has RCC, over loopback) and
returns RCC's answer for each call the script can make.

RCC 2020 takes its job as JSON: the script text is
``{"Mode": "ExecuteScript", "Settings": {"Type": <script>, "Arguments": [...]}}`` and the
script is loaded from ``internalscripts/scripts/<Type>.lua``. This is the mechanism the
step-1 JSON probe verified, so the probe writes its diagnostic script under that directory
with a unique name, never over a script the build ships, and deletes it again in a
``finally`` block. It uploads nothing, stores no thumbnail row and touches no file of the
website; the integration test calls it only after an assertion already failed.
"""

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
CONTAINER = "renderer"
TIMEOUT = 300
RUNTIME = "/home/rcc/runtime"

LUA = """local baseUrl, assetUrl, assetId, staticUrl = ...

local ContentProvider = game:GetService('ContentProvider')
local ThumbnailGenerator = game:GetService('ThumbnailGenerator')
local out = {}
local function report(name, result, value)
    local shown = tostring(value):gsub('%s+', ' '):sub(1, 200)
    table.insert(out, name .. '=' .. tostring(result) .. ' / ' .. shown)
end

report('SetBaseUrl', pcall(function() return ContentProvider:SetBaseUrl(baseUrl) end))
-- A static PNG the website always serves, to tell "ClickTexture is broken" apart from
-- "the asset URL is the problem".
report('ClickTextureStaticPng', pcall(function()
    return #ThumbnailGenerator:ClickTexture(staticUrl, 'PNG', 420, 420)
end))
report('GetObjects', pcall(function() return #game:GetObjects(assetUrl) end))
report('ClickTextureUrl', pcall(function()
    return #ThumbnailGenerator:ClickTexture(assetUrl, 'PNG', 420, 420)
end))
report('PreloadUrl', pcall(function() return ContentProvider:PreloadAsync({assetUrl}) end))
report('ClickTextureUrlAfterPreload', pcall(function()
    return #ThumbnailGenerator:ClickTexture(assetUrl, 'PNG', 420, 420)
end))
report('ClickTextureAssetId', pcall(function()
    return #ThumbnailGenerator:ClickTexture('rbxassetid://' .. tostring(assetId), 'PNG', 420, 420)
end))

return table.concat(out, ' | ')
"""

# Runs inside the container: reuse the renderer's own SOAP client instead of a second
# implementation of the protocol, and always close the job and remove the script.
PROGRAM = '''
import json
import os
import sys
import uuid
import xml.etree.ElementTree as ET

sys.path.insert(0, '/app')
import renderer

lua = __LUA__
arguments = __ARGUMENTS__
timeout = __TIMEOUT__

directory = os.path.join('__RUNTIME__', 'internalscripts', 'scripts')
os.makedirs(directory, exist_ok=True)
script_type = 'ImageProbe' + uuid.uuid4().hex[:8]
path = os.path.join(directory, script_type + '.lua')
with open(path, 'x') as stream:
    stream.write(lua)

client = renderer.RccClient()
job_id = str(uuid.uuid4())
job = ET.Element('job')
for key, value in {'id': job_id, 'category': 0, 'cores': 1, 'expirationInSeconds': timeout}.items():
    ET.SubElement(job, key).text = str(value)
script = ET.Element('script')
ET.SubElement(script, 'name').text = script_type
ET.SubElement(script, 'script').text = json.dumps({
    'Mode': 'ExecuteScript',
    'Settings': {'Type': script_type, 'Arguments': arguments},
    'Arguments': {},
})

opened = False
try:
    reply = client.call('OpenJobEx', [job, script], timeout=timeout)
    opened = True
    texts = [element.text.strip() for element in reply.iter() if element.text and element.text.strip()]
    print(max(texts, key=len) if texts else 'RCC returned no text for the probe job')
finally:
    if opened:
        identifier = ET.Element('jobID')
        identifier.text = job_id
        try:
            client.call('CloseJob', [identifier], timeout=30)
        except renderer.RenderError as error:
            print('CloseJob failed: ' + str(error), file=sys.stderr)
    try:
        os.unlink(path)
    except OSError as error:
        print('the probe script could not be removed: ' + str(error), file=sys.stderr)
'''


def run_probe(image_id, base_url="http://website:5000", run=None):
    """Returns the probe's output lines (RCC's own answers) as one string.

    ``run`` runs a command in the stack and returns ``(returncode, output)``; it exists so
    the probe's shape can be tested without Docker.
    """
    base = base_url.rstrip('/')
    arguments = [base, f"{base}/asset/?id={int(image_id)}", int(image_id), f"{base}/img/Audio.png"]
    program = (PROGRAM.replace('__LUA__', json.dumps(LUA))
                      .replace('__ARGUMENTS__', json.dumps(arguments))
                      .replace('__TIMEOUT__', str(TIMEOUT - 60))
                      .replace('__RUNTIME__', RUNTIME))
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
