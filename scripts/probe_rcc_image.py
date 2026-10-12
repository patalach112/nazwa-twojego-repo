#!/usr/bin/env python3
"""Asks RCC itself what it does with one image asset, instead of guessing.

The build's own ``Image.lua`` renders a texture asset by asking
``game:GetObjects(baseUrl.."/asset/?id="..id)`` for a Decal and, when that does not give
it one, by clicking the asset URL directly; if either step fails it returns the
transparent placeholder it carries, and the stack resizes that placeholder into a blank
thumbnail. That makes the reason invisible from the website side, so this probe runs one
job inside the renderer container (the only place that has RCC, over loopback) and
returns RCC's answer for every URL that script could fetch.

RCC 2020 accepts only the host names compiled into its binary ("%s: Trust check failed"
for anything else), which is why the renderer hands RCC the trusted name of the local
website instead of the compose service name. The probe therefore clicks the same asset
through both names, over http and https, and through ``rbxassetid://`` - one job answers
which of those RCC accepts and whether the fetch itself then succeeds. A result whose
length equals the placeholder literal in ``Image.lua`` (1239 bytes of base64) is the
script's own fallback, not a picture, so the probe says so.

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
# The host name this build trusts for asset fetches, and the compose service the
# renderer otherwise talks to. Both are overridable on the command line.
DEFAULT_ASSET_BASE = "http://www.silrev.biz"
DEFAULT_WEBSITE = "http://website:5000"
# A file the website serves from api/public/img/, so a failure to load it cannot be a
# missing asset: it separates "RCC cannot fetch anything" from "RCC cannot fetch assets".
STATIC_PATH = "/img/Audio.png"
# The placeholder Image.lua returns when its render fails, as the length of the base64
# string it returns. 1239 bytes is that 512x512 transparent PNG.
PLACEHOLDER_LENGTH = 1239

LUA = """local arguments = {...}
local baseUrl = arguments[1]
local getObjectsUrl = arguments[3]
local entries = {}
for index = 4, #arguments - 1, 2 do
    table.insert(entries, {arguments[index], arguments[index + 1]})
end

local ContentProvider = game:GetService('ContentProvider')
local ThumbnailGenerator = game:GetService('ThumbnailGenerator')
local out = {}

local function report(name, ok, value)
    if ok then
        local shown = tostring(value)
        if tonumber(value) == __PLACEHOLDER__ then
            shown = shown .. ' (the script placeholder, not a picture)'
        end
        table.insert(out, name .. '=ok / bytes=' .. shown)
    else
        table.insert(out, name .. '=fail / ' .. tostring(value):gsub('%s+', ' '):sub(1, 160))
    end
end

report('SetBaseUrl', pcall(function() return ContentProvider:SetBaseUrl(baseUrl) end))
local objectsOk, objects = pcall(function() return game:GetObjects(getObjectsUrl) end)
if objectsOk and type(objects) == 'table' and objects[1] then
    local first = objects[1]
    local texture = ''
    if first:IsA('Decal') then
        texture = tostring(first.Texture)
    end
    report('GetObjects', true, texture ~= '' and texture or first.ClassName)
else
    report('GetObjects', false, tostring(objects))
end
for _, entry in ipairs(entries) do
    local name, url = entry[1], entry[2]
    report(name, pcall(function() return #ThumbnailGenerator:ClickTexture(url, 'PNG', 420, 420) end))
end

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


def url_matrix(image_id, asset_base, website_url):
    """Every URL Image.lua could fetch, in the order the probe reports them."""
    asset_url = f'{asset_base}/asset/?id={image_id}'
    return [
        ('asset_website_http', f'{website_url}/asset/?id={image_id}'),
        ('asset_trusted_http', asset_url),
        ('asset_trusted_https', asset_url.replace('http://', 'https://', 1)),
        ('asset_rbxassetid', f'rbxassetid://{image_id}'),
        ('static_website_http', f'{website_url}{STATIC_PATH}'),
        ('static_trusted_http', f'{asset_base}{STATIC_PATH}'),
    ]


def run_probe(image_id, asset_base=DEFAULT_ASSET_BASE, website_url=DEFAULT_WEBSITE, run=None):
    """Returns the probe's output (RCC's own answers, one line per URL) as one string.

    ``run`` runs a command in the stack and returns ``(returncode, output)``; it exists so
    the probe's shape can be tested without Docker.
    """
    image_id = int(image_id)
    asset_base = asset_base.rstrip('/')
    website_url = website_url.rstrip('/')
    entries = url_matrix(image_id, asset_base, website_url)
    arguments = [asset_base, image_id, f'{asset_base}/asset/?id={image_id}']
    for name, url in entries:
        arguments.extend([name, url])
    program = (PROGRAM.replace('__LUA__', json.dumps(LUA.replace('__PLACEHOLDER__', str(PLACEHOLDER_LENGTH))))
                      .replace('__ARGUMENTS__', json.dumps(arguments))
                      .replace('__TIMEOUT__', str(TIMEOUT - 60))
                      .replace('__RUNTIME__', RUNTIME))
    run = run or run_in_container
    code, output = run(program)
    if code != 0:
        return f"the probe could not run inside the {CONTAINER} container (exit {code}): {tail(output)}"
    return tail(output, lines=6, limit=2400)


def run_in_container(program):
    result = subprocess.run(["bash", "scripts/local-stack.sh", "exec", "-T", CONTAINER, "python3", "-"],
                            cwd=ROOT, input=program, capture_output=True, text=True, timeout=TIMEOUT)
    return result.returncode, (result.stdout + result.stderr).strip()


def tail(text, lines=8, limit=900):
    kept = [line for line in text.splitlines() if line.strip()]
    return "\n".join(kept[-lines:])[-limit:]


def main():
    if not 2 <= len(sys.argv) <= 4 or not sys.argv[1].isdigit():
        raise SystemExit("usage: probe_rcc_image.py <local asset id> [asset base url] [website url]")
    image_id = int(sys.argv[1])
    asset_base = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_ASSET_BASE
    website_url = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_WEBSITE
    print(run_probe(image_id, asset_base, website_url))


if __name__ == '__main__':
    main()
