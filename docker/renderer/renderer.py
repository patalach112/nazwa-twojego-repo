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
from pathlib import Path
import re
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
# RCC listens on the loopback of its own container, which is also this container.
DEFAULT_RCC_URL = 'http://127.0.0.1:64989/'
DEFAULT_RCC_NAMESPACE = 'http://silrev.biz/'
DEFAULT_WEBSITE_URL = 'http://website:5000'
# The build's own rigs, addressed through its ContentFolder (content/).
R6_RIG = 'rbxasset://avatar/character.rbxm'
R15_RIG = 'rbxasset://avatar/characterR15.rbxm'
PNG_MAGIC = b'\x89PNG\r\n\x1a\n'
JPEG_MAGIC = b'\xff\xd8\xff'
# Where the build's own thumbnail scripts live; they carry their fallback images as
# base64 literals, and returning one of those as a render would fake a picture.
SCRIPTS_DIRECTORY = '/opt/rcc/internalscripts/thumbnails'
# The website's own render client allows ten seconds per request, so Wine's and
# RCC's first-render cost is paid at startup by rendering the build's own avatar rig
# through RCC's shipped Model script. Nothing is downloaded for it.
WARMUP_SCRIPT = 'Model'
# RCC answers SOAP while it is busy rendering only sometimes; a service that spoke to
# us recently is not reported as broken in the middle of a long render.
HEALTH_STALE_SECONDS = float(os.environ.get('RENDERER_HEALTH_STALE', '120'))

# Only these host names may be fetched for an appearance document. The renderer
# is reachable from every service in the compose network, so it must not become
# a proxy for arbitrary URLs.
APPEARANCE_PATH = '/v1.1/avatar-fetch'
# RCC 2020 answers every asset fetch it was not compiled to trust with "Trust check
# failed", and it upgrades accepted URLs to https: the accepted hosts (www.silrev.biz,
# assetgame.silrev.biz, assetdelivery.silrev.biz) are baked into RCCService.exe. The local
# stack therefore answers one of those names itself, on https, through the TLS terminator
# in docker/renderer/asset_tls.py, and this variable is that URL. It is used for the URLs
# RCC fetches (thumbnail script arguments), never for the renderer's own calls, which keep
# using the website service URL.
ASSET_BASE_URL_ENV = 'RENDERER_ASSET_BASE_URL'


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


PLACEHOLDER_LITERAL = re.compile(r'"([A-Za-z0-9+/]{400,}={0,2})"')


def load_placeholders(directory=None, limit=1024 * 1024):
    """The thumbnail scripts' own built-in fallback images, by payload.

    The shipped scripts answer a render they could not do with an embedded image (a
    transparent PNG for ``Image.lua``, an hourglass JPEG for ``Decal.lua``). Sizes are
    not enough to spot them - the transparent PNG is 512x512 and passes any size check -
    so the payloads are extracted from the scripts this build ships and refused by exact
    bytes. The mapping names the script, which is what a failure message should say.
    """
    directory = Path(directory or os.environ.get('RCC_SCRIPTS_DIRECTORY', SCRIPTS_DIRECTORY))
    if not directory.is_dir():
        log('placeholder_directory_missing', path=str(directory))
        return {}
    placeholders = {}
    for script in sorted(directory.glob('*.lua')):
        try:
            text = script.read_text(errors='replace')
        except OSError:
            continue
        for literal in PLACEHOLDER_LITERAL.findall(text):
            try:
                data = base64.b64decode(literal, validate=True)
            except (binascii.Error, ValueError):
                continue
            if len(data) <= limit and (data.startswith(PNG_MAGIC) or data.startswith(JPEG_MAGIC)):
                placeholders[data] = script.stem
    return placeholders


def decode_render_output(text, expected_edge, placeholders=None):
    """Decodes one base64 image from RCC and refuses placeholders.

    RCC happily returns a 1x1 hourglass, the script's own fallback image or a truncated
    payload; treating any of them as a finished thumbnail would put a fake picture into
    the catalog.
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
    if placeholders:
        source = placeholders.get(data)
        if source is not None:
            raise RenderError(
                f'RCC answered with the built-in fallback image of the {source} thumbnail script, '
                f'so the asset it was asked to load did not render')
    return data


class RccClient:
    """SOAP client for one RCC instance; serialised by the caller's lock."""

    def __init__(self, url=None, namespace=None, job_timeout=None):
        self.url = url or os.environ.get('RCC_SOAP_URL', DEFAULT_RCC_URL)
        self.namespace = namespace or os.environ.get('RCC_SOAP_NAMESPACE', DEFAULT_RCC_NAMESPACE)
        self.job_timeout = job_timeout or int(os.environ.get('RENDERER_JOB_TIMEOUT', '90'))
        self.version = None
        self.version_at = 0.0
        self.last_contact_at = 0.0
        self.busy = False

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
        # Any parsed reply, fault or not, proves RCC itself is alive.
        self.last_contact_at = time.monotonic()
        fault = root.find(f'.//{{{SOAP_ENVELOPE}}}Fault')
        if fault is not None:
            raise RenderError('RCC rejected the job: ' + ' '.join(fault.itertext()).strip()[:400])
        return root

    def health(self):
        """What /healthz reports: RCC's version and how recently it spoke to us.

        `OpenJobEx` only answers when its render finishes, so a job in flight means
        RCC is deliberately not answering: that is work in progress, not a failure.
        While no job is in flight, RCC has to have answered within
        HEALTH_STALE_SECONDS or the renderer reports itself unhealthy instead of
        pretending a dead RCC is fine.
        """
        age = time.monotonic() - self.last_contact_at if self.last_contact_at else None
        if not self.busy and (age is None or age > HEALTH_STALE_SECONDS):
            try:
                self.get_version(max_age=0)
                age = time.monotonic() - self.last_contact_at
            except RenderError as failure:
                raise RenderError(str(failure)) from None
        if age is None:
            raise RenderError('RCC has not answered any request yet')
        return {'version': self.version, 'lastContactSeconds': round(age, 1),
                'busy': self.busy, 'stale': age > 5.0}

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

    def render(self, script_type, arguments, expected_edge, placeholders=None):
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
        self.busy = True
        try:
            reply = self.call('OpenJobEx', [job, script])
            opened = True
        finally:
            self.busy = False
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
                return decode_render_output(candidate, expected_edge, placeholders)
            except RenderError as error:
                failure = error
        raise RenderError(f'RCC did not return a usable image ({failure}); '
                          f'{len(candidates)} text nodes were inspected')


class WebsiteClient:
    """The website service the renderer runs next to.

    The renderer builds every URL it hands RCC from configured values, and the URLs RCC
    fetches come from the asset base URL instead - see the module docstring. Nothing the
    renderer does for a render request needs this service to answer: the build's own
    avatar script fetches the appearance document inside RCC.
    """

    def __init__(self, base_url=None):
        self.base_url = (base_url or os.environ.get('WEBSITE_BASE_URL', DEFAULT_WEBSITE_URL)).rstrip('/')


class Renderer:
    """Maps the website's renderer endpoints onto RCC thumbnail scripts."""

    def __init__(self, rcc=None, website=None, placeholders=None):
        self.rcc = rcc or RccClient()
        self.website = website or WebsiteClient()
        self.base_url = self.website.base_url
        # What RCC is told to fetch from, which must be a host name this build trusts.
        # Unset means the website service itself, which is what a remote deployment
        # with a real trusted domain does not need to override.
        self.asset_base_url = (os.environ.get(ASSET_BASE_URL_ENV) or self.base_url).rstrip('/')
        self.lock = threading.Lock()
        self.max_retries = max(0, int(os.environ.get('RENDERER_MAX_RETRIES', '1')))
        self.warmup = {'state': 'pending', 'steps': {}}
        self._placeholders = placeholders

    @property
    def placeholders(self):
        """The build's own fallback images, read once from the mounted RCC build."""
        if self._placeholders is None:
            self._placeholders = load_placeholders()
            log('placeholders_loaded', scripts=sorted(set(self._placeholders.values())))
        return self._placeholders

    # -- argument builders ----------------------------------------------------
    # Every order and value below follows a script RCC ships in
    # RCCService/RCCService2020/internalscripts/thumbnails (declared in its first
    # "local ... = ..." line) and, where this repository has one, the matching
    # template in game-renderer/scripts/*.json. Nothing here writes or overrides a
    # Roblox-authored script; scripts/test_renderer_contract.py parses the shipped
    # files and fails when these builders stop matching them.

    def arguments_image(self, payload):
        """Image.lua: imageId, baseUrl, fileExtension, x, y."""
        return 'Image', [self.asset_id(payload), self.asset_base_url, 'PNG', 420, 420]

    def arguments_model(self, payload):
        """Model.lua: assetUrl, fileExtension, x, y, baseUrl."""
        return 'Model', [self.asset_url(self.asset_id(payload)), 'PNG', 420, 420, self.asset_base_url]

    def arguments_hat(self, payload):
        """Hat.lua: assetUrl, fileExtension, x, y, baseUrl."""
        return 'Hat', [self.asset_url(self.asset_id(payload)), 'PNG', 420, 420, self.asset_base_url]

    def arguments_mesh(self, payload):
        """Mesh.lua: assetUrl, fileExtension, x, y, baseUrl."""
        return 'Mesh', [self.asset_url(self.asset_id(payload)), 'PNG', 420, 420, self.asset_base_url]

    def appearance_url(self, user_id):
        """The appearance document RCC's avatar scripts fetch themselves."""
        return f'{self.asset_base_url}{APPEARANCE_PATH}?userId={int(user_id)}'

    # The build ships two avatar scripts, and this stack asks for the one that covers both
    # rigs: Avatar_R15_Action.lua says so itself ("For R6, this generates the normal
    # with/without gear pose"), the original stack's own render request
    # (Roblox/Roblox.Rendering/internalscripts/NewRenderJSON/Avatar.txt) used it for every
    # avatar, and Avatar.lua - the R6-only script - does two extra things a thumbnail does
    # not need: it sets ThumbnailGenerator.GraphicsMode and it fetches the appearance
    # document through HttpService, which RCC refuses for any host that is not compiled
    # into its binary ("Trust check failed", see the probe in scripts/probe_rcc_image.py).
    AVATAR_SCRIPT = 'Avatar_R15_Action'
    # Avatar_R15_Action.lua declares `baseUrl, characterAppearanceUrl, fileExtension, x, y`;
    # the contract test reads that header from the shipped script.
    avatar_argument_order = ('baseUrl', 'characterAppearanceUrl', 'fileExtension', 'x', 'y')

    def arguments_avatar(self, payload):
        """Avatar_R15_Action.lua, in the order its own header declares."""
        user_id = self.user_id(payload)
        values = {'baseUrl': self.asset_base_url, 'characterAppearanceUrl': self.appearance_url(user_id),
                  'fileExtension': 'PNG', 'x': 420, 'y': 420}
        return self.AVATAR_SCRIPT, [values[name] for name in self.avatar_argument_order]

    def arguments_headshot(self, payload):
        """Closeup.lua: baseUrl, characterAppearanceUrl, fileExtension, x, y,
        quadratic, baseHatZoom, maxHatZoom, cameraOffsetX, cameraOffsetY.

        The zoom values are the ones this repository's own Closeup template uses.
        """
        user_id = self.user_id(payload)
        return 'Closeup', [self.asset_base_url, self.appearance_url(user_id), 'PNG', 420, 420,
                           True, 40, 60, 0, 0]

    routes = {
        '/image/image': arguments_image,
        '/catalog/model': arguments_model,
        '/catalog/hat': arguments_hat,
        '/catalog/mesh': arguments_mesh,
        '/player/thumbnail': arguments_avatar,
        '/player/headshot': arguments_headshot,
    }

    # Endpoints the website can call but this renderer does not answer yet. Each one
    # needs something this stack does not have, and each fails loudly so the website
    # keeps a pending thumbnail instead of storing a substitute picture.
    unsupported = {
        '/image/clothing': 'RCC\'s own Shirt.lua/Pants.lua load a mannequin asset, and this '
                           'stack has no local mannequin asset yet',
        '/image/teeshirt': 'this build ships no TeeShirt thumbnail script',
        '/catalog/head': 'RCC\'s own Head.lua needs the same mannequin asset',
        '/catalog/bodypart': 'RCC\'s own BodyPart.lua needs a rig URL this stack does not serve',
        '/catalog/package': 'RCC\'s own Package.lua needs package contents and a rig URL',
        '/catalog/animationsilhouette': 'animation silhouettes need animation assets wired in',
        '/catalog/animation': 'animation renders need an animation URL and a rig',
        '/game/thumbnail': 'place thumbnails need a real place to load',
        '/player/thumbnail-3d': 'this build exposes no model/mesh renderer for 3D objects',
    }

    def asset_url(self, asset_id):
        return f'{self.asset_base_url}/asset/?id={asset_id}'

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

        The website's render client allows ten seconds per request, so a cold build
        would turn the first thumbnail into a timeout. The build's own Model script
        renders its own avatar rig: no asset upload and no download is involved. A
        failed warm-up is reported, not hidden, and does not stop the service.
        """
        started = time.monotonic()
        try:
            with self.lock:
                image = self.rcc.render(WARMUP_SCRIPT,
                                        [R6_RIG, 'PNG', 420, 420, self.asset_base_url], 420)
            self.warmup = {'state': 'ok', 'script': WARMUP_SCRIPT, 'bytes': len(image),
                           'seconds': round(time.monotonic() - started, 2)}
        except RenderError as error:
            self.warmup = {'state': 'failed', 'script': WARMUP_SCRIPT,
                           'detail': str(error)[:200],
                           'seconds': round(time.monotonic() - started, 2)}
        log('warmup', **self.warmup)

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
                    image = self.rcc.render(script_type, arguments, expected_edge, self.placeholders)
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
        # The generated env file is the primary source: a single-file bind mount does
        # not necessarily show a config that was rewritten outside the container, and
        # `scripts/configure_local_docker.py --enable-renderer` writes both.
        self.environment = os.environ.get('RENDERER_AUTHORIZATION')
        self.path = path or os.environ.get('RENDERER_WEBSITE_CONFIG', '/config/website.json')
        self.refresh = refresh
        self.value = None
        self.read_at = 0.0
        self.last_status = {'source': 'environment' if self.environment else 'config file',
                            'usable': bool(self.environment)}

    def status(self):
        """What the source knows, without ever revealing the value itself."""
        state = dict(self.last_status)
        state['path'] = self.path
        try:
            info = os.stat(self.path)
            state['exists'] = True
            state['bytes'] = info.st_size
            state['isFile'] = bool(info.st_mode & 0o170000 == 0o100000)
        except OSError as error:
            state['exists'] = False
            state['error'] = error.strerror or str(error)
        return state

    def current(self):
        if self.environment:
            return self.environment
        if self.value is not None and time.monotonic() - self.read_at < self.refresh:
            return self.value
        try:
            with open(self.path) as stream:
                document = json.load(stream)
            configured = (document.get('Render') or {}).get('Authorization')
        except (OSError, ValueError) as error:
            self.last_status = {'source': 'config file', 'usable': False, 'reason': str(error)}
            log('config_unreadable', path=self.path, detail=str(error))
            return self.value
        self.read_at = time.monotonic()
        self.last_status = {'source': 'config file', 'usable': bool(configured),
                            'hasRenderSection': 'Render' in document}
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
            status = (self.authorization_source.status()
                      if self.authorization_source is not None else {'source': 'none'})
            self.send_json(503, {'success': False,
                                 'message': 'the renderer has no Authorization value configured',
                                 'authorization': status})
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
            health = self.renderer.rcc.health()
            self.send_json(200, {'success': True, 'message': 'ok', 'rcc': health,
                                 'warmup': self.renderer.warmup})
        except RenderError as error:
            self.send_json(503, {'success': False, 'message': str(error),
                                 'rcc': {'version': self.renderer.rcc.version,
                                         'busy': self.renderer.rcc.busy},
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
                'message': path + ' is not implemented by this renderer yet because '
                                   + self.renderer.unsupported[path]
                                   + '; the thumbnail stays pending instead of being faked'})
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
        asset_base=service.asset_base_url, authorization=bool(Handler.authorization))
    # Contact RCC once before any job starts: the warm-up job keeps RCC busy, and a
    # renderer that has never spoken to RCC cannot report health during it.
    try:
        log('rcc_startup', version=service.rcc.get_version(max_age=0))
    except RenderError as error:
        log('rcc_startup_failed', detail=str(error))
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
