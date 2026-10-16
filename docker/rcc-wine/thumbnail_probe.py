"""One real Thumbnail render in the bundled RCC 2020, or an explicit finding.

This is not the JSON ExecuteScript smoke test: the render must produce image
bytes that decode as the requested format, have the requested dimensions, and
differ between two different DataModels. A built-in fallback (the 1x1 hourglass
from the legacy image script) or any fixed placeholder fails those checks.
No asset, place file or network access is used.
"""

import base64
import binascii
import json
import os
from pathlib import Path
import struct
import time
import uuid
import xml.etree.ElementTree as ET

from lua_probe import call

WIDTH = 420
HEIGHT = 420
MINIMUM_EDGE = 100
SCRIPT_NAME = 'thumbnail_probe'

# The thumbnail directory is what the JSON "Thumbnail" mode reads; the
# ExecuteScript directory is the fallback used by the working JSON probe.
ATTEMPTS = (
    ('Thumbnail', 'internalscripts/thumbnails'),
    ('Thumbnail', 'internalscripts/scripts'),
    ('ExecuteScript', 'internalscripts/scripts'),
)

PNG_MAGIC = b'\x89PNG\r\n\x1a\n'


def image_details(data):
    """Returns (format, width, height) for a PNG or JPEG payload, or None."""
    if data.startswith(PNG_MAGIC) and len(data) > 24 and data[12:16] == b'IHDR':
        width, height = struct.unpack('>II', data[16:24])
        return 'PNG', width, height
    if data.startswith(b'\xff\xd8'):
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                index += 2
                continue
            if index + 4 > len(data):
                break
            length = struct.unpack('>H', data[index + 2:index + 4])[0]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB):
                height, width = struct.unpack('>HH', data[index + 5:index + 9])
                return 'JPEG', width, height
            if length < 2:
                break
            index += 2 + length
    return None


def decode_image_text(text):
    """Base64 payload from a SOAP reply element, if it decodes to a real image.

    Deliberately no minimum length: a built-in placeholder is tiny, and the
    probe must be able to report it instead of mistaking it for "no image".
    """
    if not text or len(text) < 24:
        return None
    try:
        data = base64.b64decode(''.join(text.split()), validate=True)
    except (binascii.Error, ValueError):
        return None
    return data if image_details(data) else None


def image_from_reply(reply):
    """The first decodable image inside a SOAP reply, plus its element name."""
    for element in reply.iter():
        data = decode_image_text(element.text)
        if data is not None:
            return data, (element.tag.rsplit('}', 1)[-1] or 'text')
    return None, None


def image_from_runtime(directory, since):
    """An image file RCC wrote itself, if the reply carried no inline image."""
    for path in sorted(Path(directory).rglob('*')):
        if not path.is_file() or path.suffix.lower() not in ('.png', '.jpg', '.jpeg'):
            continue
        try:
            if path.stat().st_mtime < since - 1:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        if image_details(data):
            return data, str(path)
    return None, None


def render(runtime, mode, script_directory, variant, timeout_seconds=60):
    """Runs one render job and returns (data, source, raw_reply)."""
    identifier = uuid.uuid4().hex
    script_type = 'WineThumbnail' + identifier[:8]
    directory = Path(runtime) / script_directory
    directory.mkdir(parents=True, exist_ok=True)
    filename = directory / (script_type + '.lua')
    source = Path(__file__).with_suffix('.lua').read_text()
    with filename.open('x') as stream:
        stream.write(source)

    job_id = str(uuid.uuid4())
    job = ET.Element('job')
    for name, value in {'id': job_id, 'category': 0, 'cores': 1,
                        'expirationInSeconds': timeout_seconds}.items():
        ET.SubElement(job, name).text = str(value)
    script = ET.Element('script')
    ET.SubElement(script, 'name').text = SCRIPT_NAME
    ET.SubElement(script, 'script').text = json.dumps({
        'Mode': mode,
        'Settings': {'Type': script_type, 'Arguments': ['PNG', WIDTH, HEIGHT, variant]},
        'Arguments': {},
    })
    opened = False
    started = time.time()
    try:
        reply = call('OpenJobEx', [job, script])
        opened = True
    finally:
        try:
            if opened:
                close_id = ET.Element('jobID')
                close_id.text = job_id
                call('CloseJob', [close_id])
        finally:
            filename.unlink()
            try:
                # The ExecuteScript types make RCC create a compiled artefact
                # next to the script; it belongs to this probe, not the build.
                directory.rmdir()
            except OSError:
                pass

    data, source_name = image_from_reply(reply)
    if data is None:
        data, source_name = image_from_runtime(runtime, started)
    return data, (f'{mode}:{script_directory}:{source_name or "no image element"}'), reply


def validate(details, what):
    kind, width, height = details
    if width < MINIMUM_EDGE or height < MINIMUM_EDGE:
        raise RuntimeError(f'{what} returned a {width}x{height} {kind} image, '
                           'which is a built-in placeholder rather than a render')


def probe(runtime=Path('/home/rcc/runtime'), script_directory=None, mode=None):
    """Renders two different DataModels and reports what actually came back."""
    attempts = [(mode, script_directory)] if mode and script_directory else ATTEMPTS
    findings = []
    for attempt_mode, attempt_directory in attempts:
        try:
            scene, source, _ = render(runtime, attempt_mode, attempt_directory, 'scene')
            if scene is None:
                raise RuntimeError('the job returned no decodable image')
            scene_details = image_details(scene)
            validate(scene_details, 'the render')
            empty, empty_source, _ = render(runtime, attempt_mode, attempt_directory, 'empty')
            if empty is None:
                raise RuntimeError('the second scene returned no decodable image')
            empty_details = image_details(empty)
            validate(empty_details, 'the second render')
            if scene == empty:
                raise RuntimeError('two different DataModels produced identical bytes, '
                                   'so this is a fixed placeholder and not a render')
            summary = {
                'mode': attempt_mode,
                'scriptDirectory': attempt_directory,
                'source': source,
                'format': scene_details[0],
                'width': scene_details[1],
                'height': scene_details[2],
                'sceneBytes': len(scene),
                'emptyBytes': len(empty),
                'sceneMatchesRequestedType': scene_details[0] == 'PNG',
                'secondSource': empty_source,
            }
            print('Rendered two different DataModels: {mode} via {scriptDirectory}, '
                  '{width}x{height} {format}, {sceneBytes} and {emptyBytes} bytes'.format(**summary))
            return summary
        except Exception as error:  # noqa: BLE001 - every finding is reported
            findings.append(f'{attempt_mode} with {attempt_directory}: {error}')
    raise RuntimeError('no thumbnail render was established. ' + ' | '.join(findings))


if __name__ == '__main__':
    # The last line is the machine-readable summary the Wine runner keeps.
    print(json.dumps(probe()))
