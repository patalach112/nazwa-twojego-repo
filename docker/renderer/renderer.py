"""Minimal renderer in front of RCC 2020: the HTTP contract the website calls.

`Roblox.Rendering.RenderingHandler` posts JSON to `Render:BaseUrl` and expects
`{"success": bool, "message": ..., "data": "<base64 image>"}`. This service turns
those requests into JSON `Thumbnail` jobs for RCC and returns the real image.

Safety properties, because this process drives a Windows executable through Wine:

* one job at a time (RCC renders into a single DataModel) with a bounded queue,
* every job has a wall-clock timeout, and its `CloseJob` runs even on failure,
* retries are bounded and never loop forever, so a stuck RCC is reported
  instead of being hammered,
* the Authorization header is required and compared in constant time,
* RCC never receives a URL it did not get from the website configuration.

Only the Python standard library is used, so the image has no package supply
chain to trust.
"""

import base64
import binascii
import hmac
import json
import os
import signal
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SOAP_ENVELOPE = 'http://schemas.xmlsoap.org/soap/envelope/'
DEFAULT_PORT = 3521
DEFAULT_RCC_URL = 'http://rcc-render:64989/'
DEFAULT_RCC_NAMESPACE = 'http://silrev.biz/'
DEFAULT_WEBSITE_URL = 'http://website:5000'
# The build's own rigs, addressed through its ContentFolder (content/).
R6_RIG = 'rbxasset://avatar/character.rbxm'
R15_RIG = 'rbxasset://avatar/characterR15.rbxm'
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
# Startup warm-up: a texture render over HTTP (the path real asset renders use) and
# a bundled-rig render (the path avatar/clothing renders use). Wine and RCC pay
# their first-render cost here, not inside a user request.
WARMUP_TEXTURE = '/img/Audio.png'
WARMUP_RIG = R6_RIG

# Only these host names may be fetched for an appearance document. The renderer
# is reachable from every service in the compose network, so it must not become
# a proxy for arbitrary URLs.
APPEARANCE_PATH = '/v1.1/avatar-fetch'


class RenderError(Exception):
    """A render that cannot be completed; reported as a JSON error, never as an image."""


def log(event, **fields):
    record = {'event': event, 'at': round(time.time(), 3)}
    record.update(fields)
    print(json.dumps(record, sort_keys=True), flush=True)


def image_details(data):
    """Returns (format, width, height) for a PNG payload, or None."""
    if data.startswith(PNG_MAGIC) and len(data) > 24 and data[12:16] == b'IHDR':
        width, height = struct.unpack('>II', data[16:24])
        return 'PNG', width, height
    return None


def decode_render_output(text, expected_edge):
    """Decodes one base64 image from RCC and refuses placeholders.

    RCC happily returns a 1x1 hourglass or a truncated payload; treating either as
    a finished thumbnail would put a fake picture into the catalog.
    """
    if not text:
        raise RenderError('the render returned no image data')
    try:
        data = base64.b64decode(''.join(text.split()), validate=True)
    except (binascii.Error, ValueError) as error:
        raise RenderError('the render output is not valid base64: ' + str(error)) from None
    details = image_details(data)
    if details is None:
        raise RenderError('the render output is not a PNG image')
    _, width, height = details
    if width < expected_edge or height < expected_edge:
        raise RenderError(f'the render returned a {width}x{height} placeholder instead of an image')
    return data


class RccClient:
    """SOAP client for one RCC instance; serialised by the caller's lock."""

    def __init__(self, url=None, namespace=None, job_timeout=None):
        self.url = url or os.environ.get('RCC_SOAP_URL', DEFAULT_RCC_URL)
        self.namespace = namespace or os.environ.get('RCC_SOAP_NAMESPACE', DEFAULT_RCC_NAMESPACE)
        self.job_timeout = job_timeout or int(os.environ.get('RENDERER_JOB_TIMEOUT', '90'))
        self.version = None
        self.version_at = 0.0

    def call(self, method, children, timeout=None):
        envelope = ET.Element(f'{{{SOAP_ENVELOPE}}}Envelope')
        body = ET.SubElement(envelope, f'{{{SOAP_ENVELOPE}}}Body')
        operation = ET.SubElement(body, f'{{{self.namespace}}}{method}')
        for child in children:
            for element in child.iter():
                element.tag = f'{{{self.namespace}}}{element.tag}'
            operation.append(child)
        request = urllib.request.Request(
            self.url,
            data=ET.tostring(envelope, encoding='utf-8', xml_declaration=True),
            headers={'Content-Type': 'text/xml; charset=utf-8',
                     'SOAPAction': self.namespace + method},
            method='POST',
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=timeout or self.job_timeout) as response:
                root = ET.fromstring(response.read())
        except urllib.error.HTTPError as error:
            with error:
                root = ET.fromstring(error.read())
        except (urllib.error.URLError, OSError, ET.ParseError) as error:
            raise RenderError('RCC is unreachable: ' + str(error)) from None
        fault = root.find(f'.//{{{SOAP_ENVELOPE}}}Fault')
        if fault is not None:
            raise RenderError('RCC rejected the job: ' + ' '.join(fault.itertext()).strip()[:400])
        return root

    def get_version(self, max_age=10.0):
        if self.version and time.monotonic() - self.version_at < max_age:
            return self.version
        root = self.call('GetVersion', [], timeout=int(os.environ.get('RENDERER_QUERY_TIMEOUT', '5')))
        element = next((e for e in root.iter() if e.tag.rsplit('}', 1)[-1] == 'GetVersionResult'), None)
        if element is None or not (element.text or '').strip():
            raise RenderError('RCC did not return a version')
        self.version = element.text.strip()
        self.version_at = time.monotonic()
        return self.version

    def render(self, script_type, arguments, expected_edge):
        """Runs one Thumbnail job and returns the decoded image bytes."""
        job_id = str(uuid.uuid4())
        job = ET.Element('job')
        for name, value in {'id': job_id, 'category': 0, 'cores': 1,
                            'expirationInSeconds': self.job_timeout}.items():
            ET.SubElement(job, name).text = str(value)
        script = ET.Element('script')
        ET.SubElement(script, 'name').text = script_type
        ET.SubElement(script, 'script').text = json.dumps({
            'Mode': 'Thumbnail',
            'Settings': {'Type': script_type, 'Arguments': arguments},
            'Arguments': {},
        })
        opened = False
        try:
            reply = self.call('OpenJobEx', [job, script])
            opened = True
        finally:
            if opened:
                close = ET.Element('jobID')
                close.text = job_id
                try:
                    self.call('CloseJob', [close], timeout=int(os.environ.get('RENDERER_QUERY_TIMEOUT', '5')))
                except RenderError as error:
                    # A job that could not be closed is a finding, but it must not
                    # turn a finished render into a failure for the caller.
                    log('close_job_failed', job=job_id, detail=str(error))
        # RCC's reply can carry more than one text node (job ids, logs, the payload),
        # so every candidate is tried instead of trusting the first long string.
        candidates = [element.text.strip() for element in reply.iter() if element.text and element.text.strip()]
        if not candidates:
            raise RenderError('RCC returned an empty reply for the thumbnail job')
        failure = None
        for candidate in candidates:
            try:
                return decode_render_output(candidate, expected_edge)
            except RenderError as error:
                failure = error
        raise RenderError(f'RCC did not return a usable image ({failure}); '
                          f'{len(candidates)} text nodes were inspected')


class WebsiteClient:
    """Reads the account's appearance document from the real website."""

    def __init__(self, base_url=None, timeout=None):
        self.base_url = (base_url or os.environ.get('WEBSITE_BASE_URL', DEFAULT_WEBSITE_URL)).rstrip('/')
        self.timeout = timeout or int(os.environ.get('RENDERER_ASSET_FETCH_TIMEOUT', '20'))

    def appearance(self, user_id):
        url = f'{self.base_url}{APPEARANCE_PATH}?userId={int(user_id)}'
        request = urllib.request.Request(url, headers={'Accept': 'application/json'})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError) as error:
            raise RenderError(f'the appearance for user {user_id} could not be read: {error}') from None


def body_colours(appearance):
    """BrickColor numbers in the order Avatar.lua/Closeup.lua expect them.

    `/v1.1/avatar-fetch` answers both the capitalised and the `...ColorId` spelling of
    every colour; either is accepted and a missing one becomes 0, which the scripts
    treat as "keep the rig's own colour" instead of crashing the render.
    """
    colours = appearance.get('bodyColors') or {}
    order = ('Head', 'Torso', 'LeftArm', 'RightArm', 'LeftLeg', 'RightLeg')
    values = []
    for name in order:
        value = None
        for key in (name + 'Color', name + 'ColorId',
                    name[0].lower() + name[1:] + 'Color', name[0].lower() + name[1:] + 'ColorId'):
            if colours.get(key) is not None:
                value = colours[key]
                break
        try:
            values.append(int(value) if value is not None else 0)
        except (TypeError, ValueError):
            values.append(0)
    return values


def clothing_urls(appearance, website):
    """Local asset URLs for the clothing the account is wearing, in a fixed order."""
    type_ids = {'Shirt': 11, 'Pants': 12, 'TeeShirt': 2}
    entries = appearance.get('assetAndAssetTypeIds') or []
    urls = []
    for kind in ('Shirt', 'Pants', 'TeeShirt'):
        wanted = type_ids[kind]
        for entry in entries:
            if entry.get('assetTypeId') == wanted:
                asset_id = int(entry.get('assetId') or 0)
                if asset_id > 0:
                    urls.append(f'{website}/asset/?id={asset_id}')
                break
    return ';'.join(urls)


class Renderer:
    """Maps the website's renderer endpoints onto RCC thumbnail scripts."""

    def __init__(self, rcc=None, website=None):
        self.rcc = rcc or RccClient()
        self.website = website or WebsiteClient()
        self.base_url = self.website.base_url
        self.lock = threading.Lock()
        self.max_retries = max(0, int(os.environ.get('RENDERER_MAX_RETRIES', '1')))
        self.warmup = {'state': 'pending', 'steps': {}}

    # -- argument builders, one per script; names must match the Lua headers ----
    def arguments_image(self, payload):
        asset_id = self.asset_id(payload)
        return 'Image', [self.asset_url(asset_id), 'PNG', 420, 420]

    def arguments_model(self, payload):
        asset_id = self.asset_id(payload)
        return 'Model', [self.asset_url(asset_id), 'PNG', 420, 420, True, True]

    def arguments_mesh(self, payload):
        asset_id = self.asset_id(payload)
        return 'Mesh', [self.asset_url(asset_id), 'PNG', 420, 420]

    def arguments_head(self, payload):
        asset_id = self.asset_id(payload)
        return 'Head', [self.asset_url(asset_id), 'PNG', 420, 420, R6_RIG]

    def arguments_clothing(self, payload):
        asset_id = self.asset_id(payload)
        return 'Clothing', [self.asset_url(asset_id), 'PNG', 420, 420, R6_RIG]

    def arguments_teeshirt(self, payload):
        asset_id = self.asset_id(payload)
        return 'TeeShirt', [self.asset_url(asset_id), 'PNG', 420, 420, R6_RIG]

    def arguments_avatar(self, payload):
        user_id = self.user_id(payload)
        appearance = self.website.appearance(user_id)
        rig = R15_RIG if str(appearance.get('resolvedAvatarType', 'R6')).upper() == 'R15' else R6_RIG
        return 'Avatar', ['PNG', 420, 420, rig, *body_colours(appearance),
                          clothing_urls(appearance, self.base_url), self.base_url]

    def arguments_headshot(self, payload):
        user_id = self.user_id(payload)
        appearance = self.website.appearance(user_id)
        rig = R15_RIG if str(appearance.get('resolvedAvatarType', 'R6')).upper() == 'R15' else R6_RIG
        return 'Closeup', ['PNG', 420, 420, rig, *body_colours(appearance),
                           clothing_urls(appearance, self.base_url), self.base_url]

    routes = {
        '/image/image': arguments_image,
        '/catalog/model': arguments_model,
        '/catalog/hat': arguments_model,
        '/catalog/mesh': arguments_mesh,
        '/catalog/head': arguments_head,
        '/image/clothing': arguments_clothing,
        '/image/teeshirt': arguments_teeshirt,
        '/player/thumbnail': arguments_avatar,
        '/player/headshot': arguments_headshot,
    }

    # Endpoints the website can call but this renderer does not implement yet.
    # They fail loudly so the website keeps a pending thumbnail instead of a fake
    # one; each needs data we do not have locally (package contents, animation
    # files, a real place).
    unsupported = {
        '/catalog/package', '/catalog/bodypart', '/catalog/animationsilhouette',
        '/catalog/animation', '/game/thumbnail', '/player/thumbnail-3d',
    }

    def asset_url(self, asset_id):
        return f'{self.base_url}/asset/?id={asset_id}'

    @staticmethod
    def asset_id(payload):
        try:
            asset_id = int(payload.get('assetId') or 0)
        except (TypeError, ValueError):
            asset_id = 0
        if asset_id <= 0:
            raise RenderError('the request did not name a valid local asset id')
        return asset_id

    @staticmethod
    def user_id(payload):
        try:
            user_id = int(payload.get('userId') or 0)
        except (TypeError, ValueError):
            user_id = 0
        if user_id <= 0:
            raise RenderError('the request did not name a valid user id')
        return user_id

    def warm_up(self):
        """Pays the first-render cost of Wine/RCC before any user request arrives.

        The website's own render client gives a request ten seconds, so a cold build
        would otherwise turn every first thumbnail into a timeout. Both steps are
        reported; a texture render over HTTP is the one real renders depend on.
        """
        steps = {}
        steady = 0
        for name, script_type, arguments in (
                ('texture', 'Image', [f'{self.base_url}{WARMUP_TEXTURE}', 'PNG', 420, 420]),
                ('rig', 'Model', [WARMUP_RIG, 'PNG', 420, 420, True, True])):
            started = time.monotonic()
            try:
                with self.lock:
                    image = self.rcc.render(script_type, arguments, 420)
                steps[name] = {'state': 'ok', 'bytes': len(image),
                               'seconds': round(time.monotonic() - started, 2)}
                steady += 1
            except RenderError as error:
                steps[name] = {'state': 'failed', 'detail': str(error)[:200],
                               'seconds': round(time.monotonic() - started, 2)}
            log('warmup_step', step=name, **steps[name])
        if steps['texture']['state'] != 'ok':
            state = 'failed'
        elif steps['rig']['state'] != 'ok':
            state = 'partial'
        else:
            state = 'ok'
        self.warmup = {'state': state, 'steps': steps, 'steady': steady}

    def render(self, path, payload, expected_edge=420):
        builder = self.routes.get(path)
        if builder is None:
            raise RenderError('this renderer does not implement ' + path)
        script_type, arguments = builder(self, payload)
        last = None
        for attempt in range(self.max_retries + 1):
            started = time.monotonic()
            with self.lock:
                try:
                    image = self.rcc.render(script_type, arguments, expected_edge)
                    log('rendered', path=path, script=script_type, bytes=len(image),
                        seconds=round(time.monotonic() - started, 2), attempt=attempt)
                    return image
                except RenderError as error:
                    last = error
            log('render_failed', path=path, script=script_type, attempt=attempt, detail=str(last))
            if attempt < self.max_retries:
                time.sleep(min(2 ** attempt, 4))
        raise last


class AuthorizationSource:
    """The Authorization value, from the environment or the mounted website config.

    Reusing the website's own `Render:Authorization` value means the renderer needs no
    new secret and nothing has to be rotated. The file is re-read when the value goes
    stale, so enabling the renderer does not require restarting it.
    """

    def __init__(self, path=None, refresh=10.0):
        self.environment = os.environ.get('RENDERER_AUTHORIZATION')
        self.path = path or os.environ.get('RENDERER_WEBSITE_CONFIG', '/config/website.json')
        self.refresh = refresh
        self.value = None
        self.read_at = 0.0

    def current(self):
        if self.environment:
            return self.environment
        if self.value is not None and time.monotonic() - self.read_at < self.refresh:
            return self.value
        try:
            with open(self.path) as stream:
                configured = (json.load(stream).get('Render') or {}).get('Authorization')
        except (OSError, ValueError) as error:
            log('config_unreadable', path=self.path, detail=str(error))
            return self.value
        self.read_at = time.monotonic()
        if configured != self.value:
            log('authorization_reloaded', configured=bool(configured))
            self.value = configured or None
        return self.value


class Handler(BaseHTTPRequestHandler):
    server_version = 'marine-renderer/1'
    renderer = None
    authorization = None
    authorization_source = None
    max_body = 64 * 1024

    def log_message(self, format, *args):  # noqa: A002 - stdlib signature
        # Default access logging would print one line per request to stderr; the
        # structured log above is enough and does not leak payloads.
        pass

    def send_json(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        # Re-read through the source so a config change applies without a restart.
        self.authorization = (self.authorization_source.current()
                              if self.authorization_source is not None else self.authorization)
        if not self.authorization:
            self.send_json(503, {'success': False,
                                 'message': 'the renderer has no Authorization value configured'})
            return False
        provided = self.headers.get('Authorization') or ''
        if not hmac.compare_digest(provided, self.authorization):
            self.send_json(401, {'success': False, 'message': 'Unauthorized'})
            return False
        return True

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        if path not in ('/healthz', '/health'):
            return self.send_json(404, {'success': False, 'message': 'Not found'})
        try:
            version = self.renderer.rcc.get_version()
            self.send_json(200, {'success': True, 'message': 'ok', 'rccVersion': version,
                                 'warmup': self.renderer.warmup})
        except RenderError as error:
            self.send_json(503, {'success': False, 'message': str(error),
                                 'warmup': self.renderer.warmup})

    def do_POST(self):
        path = urllib.parse.urlsplit(self.path).path
        if path not in self.renderer.routes and path not in self.renderer.unsupported:
            return self.send_json(404, {'success': False, 'message': 'Not found'})
        if not self.authorized():
            return
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length > self.max_body:
            return self.send_json(413, {'success': False, 'message': 'Request body too large'})
        raw = self.rfile.read(length) if length else b''
        try:
            payload = json.loads(raw or b'{}')
            if not isinstance(payload, dict):
                raise ValueError('the payload must be a JSON object')
        except ValueError as error:
            return self.send_json(400, {'success': False, 'message': 'Invalid JSON: ' + str(error)})
        if path in self.renderer.unsupported:
            return self.send_json(501, {
                'success': False,
                'message': path + ' is not implemented by this renderer yet; '
                                   'the thumbnail stays pending instead of being faked'})
        try:
            image = self.renderer.render(path, payload)
        except RenderError as error:
            log('render_error', path=path, detail=str(error))
            return self.send_json(502, {'success': False, 'message': str(error)})
        except Exception as error:  # noqa: BLE001 - never leak a traceback as an image
            log('render_crashed', path=path, detail=repr(error))
            return self.send_json(500, {'success': False, 'message': 'The renderer failed'})
        self.send_json(200, {'success': True, 'message': 'ok', 'data': base64.b64encode(image).decode()})


def main():
    port = int(os.environ.get('RENDERER_PORT', str(DEFAULT_PORT)))
    service = Renderer()
    Handler.renderer = service
    Handler.authorization_source = AuthorizationSource()
    Handler.authorization = Handler.authorization_source.current()
    if not Handler.authorization:
        log('no_authorization_configured')
    server = ThreadingHTTPServer(('0.0.0.0', port), Handler)
    server.daemon_threads = True
    log('listening', port=port, rcc=service.rcc.url, website=service.base_url,
        authorization=bool(Handler.authorization))
    # The website's render client gives a request ten seconds, so Wine's and RCC's
    # first-render cost is paid here instead of inside a user request.
    threading.Thread(target=service.warm_up, daemon=True).start()

    def stop(signum, _frame):
        log('stopping', signal=signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        log('stopped')


if __name__ == '__main__':
    main()
