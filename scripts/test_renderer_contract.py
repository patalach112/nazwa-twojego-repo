"""Contract and behaviour tests for the renderer service; real RCC rendering needs CI.

These tests run without Docker or Wine:

* the renderer's routes are checked against the thumbnail scripts RCC 2020 ships and
  loads (`internalscripts/thumbnails/<Settings.Type>.lua`), parsed from the files
  themselves, so a script's argument list and the renderer cannot drift apart,
* nothing in this repository may shadow or replace one of those scripts,
* the HTTP surface is exercised against a real server with a substitute RCC:
  authorization, unimplemented endpoints, placeholder rejection, JSON errors,
* the SOAP reply parser is checked against a decoy text node, a reply with no image
  and a SOAP fault,
* the pixel reader used by the integration test is checked on real PNG bytes.
"""

import base64
import http.server
import importlib.util
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time

import yaml
import unittest
import urllib.error
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parents[1]
# The scripts RCC 2020 itself ships and loads by Settings.Type. The renderer has to
# satisfy their argument lists, and this repository must not shadow them.
SCRIPTS = ROOT / 'RCCService/RCCService2020/internalscripts/thumbnails'
sys.path.insert(0, str(ROOT / 'docker/renderer'))
import renderer  # noqa: E402


def make_png(width, height, left=(255, 0, 0), right=(0, 0, 255)):
    """Two-colour PNG (colour type 2), enough to tell a texture from a fallback."""
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF))

    rows = []
    for _ in range(height):
        row = b''
        for x in range(width):
            row += bytes(left if x < width // 2 else right)
        rows.append(b'\x00' + row)
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b''.join(rows), 6))
            + chunk(b'IEND', b''))


class FakeRcc:
    """Substitute RCC: records calls and answers with a real PNG of the asked size."""

    def __init__(self, image=None, error=None):
        self.image = image
        self.error = error
        self.calls = []
        self.jobs = []
        self.version = '0.450.0.411923'
        self.busy = False

    def get_version(self, max_age=10.0):
        if self.error:
            raise renderer.RenderError(self.error)
        return self.version

    def health(self):
        if self.error:
            raise renderer.RenderError(self.error)
        return {'version': self.version, 'lastContactSeconds': 0.4, 'busy': self.busy, 'stale': False}

    def render(self, script_type, arguments, expected_edge):
        self.calls.append((script_type, arguments, expected_edge))
        self.jobs.append(script_type)
        if self.error:
            raise renderer.RenderError(self.error)
        image = self.image if self.image is not None else make_png(420, 420)
        return renderer.decode_render_output(base64.b64encode(image).decode(), expected_edge)


class FakeWebsite:
    """Substitute appearance document, shaped like /v1.1/avatar-fetch."""

    def __init__(self, rig='R6', error=None):
        self.base_url = 'http://website.test'
        self.rig = rig
        self.error = error

    def appearance(self, user_id):
        if self.error:
            raise renderer.RenderError(self.error)
        return {
            'resolvedAvatarType': self.rig,
            'bodyColors': {'HeadColor': 1, 'TorsoColor': 2, 'LeftArmColor': 3, 'RightArmColor': 4,
                           'LeftLegColor': 5, 'RightLegColor': 6},
            'assetAndAssetTypeIds': [{'assetId': 11, 'assetTypeId': 11}],
        }


class LuaContractTest(unittest.TestCase):
    """The renderer must satisfy RCC's own thumbnail scripts, unmodified."""

    def declared_arguments(self, script):
        """The varargs list from the script's own first assignment to `...`."""
        for line in script.read_text().splitlines():
            if line.strip().startswith('--') or '=' not in line:
                continue
            head, _, tail = line.partition('=')
            if tail.strip() != '...':
                continue
            names = [name.strip() for name in head.replace('local', '', 1).split(',')]
            return [name for name in names if name]
        self.fail(f'{script.name} does not declare a varargs argument list')

    def renderer_for(self, rig='R6'):
        return renderer.Renderer(rcc=FakeRcc(), website=FakeWebsite(rig=rig))

    def test_the_build_ships_every_script_the_renderer_asks_for(self):
        required = [line.strip() for line in (ROOT / 'docker/rcc-wine/required-thumbnail-scripts.txt')
                    .read_text().splitlines() if line.strip() and not line.startswith('#')]
        self.assertIn('Image', required)
        self.assertIn('Avatar', required)
        for name in required:
            self.assertTrue((SCRIPTS / f'{name}.lua').is_file(),
                            f'{name}.lua is listed as required but the build does not ship it')

    def test_this_repository_never_shadows_a_shipped_script(self):
        offenders = [str(path.relative_to(ROOT)) for path in (ROOT / 'docker/rcc-wine').rglob('*.lua')
                     if path.parent.name in ('thumbnails', 'internalscripts')]
        self.assertEqual(offenders, [], 'a local script would shadow one RCC ships')

    def test_every_route_is_backed_by_a_shipped_script_and_matching_arguments(self):
        service = self.renderer_for()
        cases = {
            '/image/image': {'assetId': 7},
            '/catalog/model': {'assetId': 7},
            '/catalog/hat': {'assetId': 7},
            '/catalog/mesh': {'assetId': 7},
            '/player/thumbnail': {'userId': 3},
            '/player/headshot': {'userId': 3},
        }
        self.assertEqual(set(cases), set(service.routes), 'every route needs a case here')
        for route, payload in cases.items():
            script_type, arguments = service.routes[route](service, payload)
            script = SCRIPTS / (script_type + '.lua')
            self.assertTrue(script.is_file(), f'{route} needs {script.name}, which this build ships')
            declared = self.declared_arguments(script)
            self.assertEqual(len(arguments), len(declared),
                             f'{script.name} declares {declared} but {route} passes {arguments}')
            for name, value in zip(declared, arguments):
                if name == 'fileExtension':
                    self.assertEqual(value, 'PNG', f'{script.name}: unexpected format {value!r}')
                elif name in ('x', 'y', 'width', 'height'):
                    self.assertEqual(value, 420, f'{script.name}: unexpected size {value!r}')
                elif name == 'baseUrl':
                    self.assertEqual(value, 'http://website.test', f'{script.name}: unexpected base URL')
                elif name == 'characterAppearanceUrl':
                    self.assertEqual(value, 'http://website.test/v1.1/avatar-fetch?userId=3',
                                     f'{script.name}: the appearance document must be this website')
                elif name == 'assetUrl':
                    self.assertEqual(value, 'http://website.test/asset/?id=7',
                                     f'{script.name}: the asset URL must be this website')
                elif name == 'imageId':
                    self.assertEqual(value, 7, f'{script.name}: the image id must be passed through')
                else:
                    self.assertIn(value, (True, 40, 60, 0),
                                  f'{script.name}: {name} carries an unexpected value {value!r}')

    def test_the_closeup_zooms_are_the_ones_this_project_used(self):
        """game-renderer/scripts/Closeup.json is this project's own record of them."""
        template = json.loads((ROOT / 'game-renderer/scripts/Closeup.json').read_text())
        self.assertEqual(template['Settings']['Type'], 'Closeup')
        script_type, arguments = self.renderer_for().arguments_headshot({'userId': 3})
        self.assertEqual(script_type, 'Closeup')
        self.assertEqual(arguments[5:], [True, 40, 60, 0, 0])
        self.assertEqual(template['Settings']['Arguments'][5:], [True, 40, 60, 0, 0])

    def test_r6_and_r15_accounts_get_their_own_script_and_its_own_order(self):
        appearance = 'http://website.test/v1.1/avatar-fetch?userId=3'
        for rig, script, expected in (
                ('R6', 'Avatar', [appearance, 'http://website.test', 'PNG', 420, 420]),
                ('R15', 'Avatar_R15_Action', ['http://website.test', appearance, 'PNG', 420, 420])):
            script_type, arguments = self.renderer_for(rig).arguments_avatar({'userId': 3})
            self.assertEqual(script_type, script)
            self.assertEqual(arguments, expected,
                             f'{script}.lua declares the first two arguments in this order')

    def test_missing_ids_are_rejected_instead_of_rendering_asset_zero(self):
        service = self.renderer_for()
        for payload in ({}, {'assetId': 'x'}, {'assetId': 0}, {'assetId': -3}):
            with self.assertRaises(renderer.RenderError):
                service.arguments_image(payload)
            with self.assertRaises(renderer.RenderError):
                service.arguments_model(payload)
        for payload in ({}, {'userId': 'x'}, {'userId': 0}):
            with self.assertRaises(renderer.RenderError):
                service.arguments_avatar(payload)

    def test_unimplemented_endpoints_say_what_is_missing(self):
        service = self.renderer_for()
        self.assertEqual(set(service.unsupported) & set(service.routes), set(),
                         'an endpoint cannot be both supported and unsupported')
        for route in ('/image/clothing', '/catalog/head'):
            self.assertIn('mannequin', service.unsupported[route],
                          f'{route} stays pending because the shipped script needs a mannequin asset')
        for route, reason in service.unsupported.items():
            self.assertGreater(len(reason), 20, f'{route} needs a concrete reason')

    def test_untrusted_requests_cannot_choose_a_url(self):
        service = self.renderer_for()
        _, arguments = service.arguments_model({'assetId': 12, 'assetUrl': 'http://evil.example/'})
        self.assertEqual(arguments[0], 'http://website.test/asset/?id=12',
                         'the asset URL must come from configuration, never from the request')


class ContainerLayoutTest(unittest.TestCase):
    """RCC binds to its own container's loopback, so both run in one container."""

    def dockerfile_packages(self, path):
        text = (ROOT / path).read_text()
        start = text.index('apt-get install')
        block = text[start:text.index('&& rm -rf', start)]
        packages = set()
        for line in block.splitlines()[1:]:
            packages.update(line.replace('\\', ' ').split())
        return {package for package in packages if package and not package.startswith('-')}

    def test_the_renderer_image_installs_the_same_runtime_as_the_rcc_image(self):
        """A drifting package list would make the two images behave differently."""
        self.assertEqual(self.dockerfile_packages('docker/renderer/Dockerfile'),
                         self.dockerfile_packages('docker/rcc-wine/Dockerfile'))

    def test_the_renderer_image_reuses_the_rcc_launcher_instead_of_copying_it(self):
        text = (ROOT / 'docker/renderer/Dockerfile').read_text()
        for name in ('entrypoint.sh', 'run-wine.sh', 'healthcheck.py', 'required-thumbnail-scripts.txt'):
            self.assertIn(f'rcc-wine/{name}', text,
                          f'{name} must come from docker/rcc-wine, not be duplicated')

    def test_the_supervisor_starts_rcc_waits_for_soap_and_becomes_the_renderer(self):
        text = (ROOT / 'docker/renderer/supervise.sh').read_text()
        self.assertIn('/usr/local/bin/entrypoint.sh &', text, 'RCC must run in the background')
        self.assertIn('healthcheck.py', text, 'the supervisor must wait for a real SOAP answer')
        self.assertTrue(text.rstrip().endswith('exec python3 /app/renderer.py'),
                        'the renderer must be the foreground process so shutdown reaches it')

    def test_compose_runs_one_renderer_service_that_builds_this_image(self):
        compose = yaml.safe_load((ROOT / 'docker-compose.local.yml').read_text())['services']
        self.assertNotIn('rcc-render', compose, 'RCC has to share the renderer container')
        renderer = compose['renderer']
        self.assertEqual(renderer['profiles'], ['renderer'])
        self.assertEqual(renderer['build'], {'context': 'docker', 'dockerfile': 'renderer/Dockerfile'})
        self.assertEqual(renderer['environment']['RCC_VERIFY_THUMBNAIL_SCRIPTS'], '1')
        self.assertEqual(renderer['ports'], ['127.0.0.1:${RENDERER_PORT:-3521}:3521'])
        # Depending on the website makes `restart website` restart the renderer, which
        # cuts renders in flight; the renderer does not need the website to start.
        self.assertNotIn('depends_on', renderer)
        self.assertEqual(renderer['cap_drop'], ['ALL'])
        self.assertTrue(renderer['read_only'])

    def test_the_renderer_defaults_to_the_same_container_loopback(self):
        self.assertEqual(renderer.DEFAULT_RCC_URL, 'http://127.0.0.1:64989/')
        self.assertIn('RCC_SOAP_URL=http://127.0.0.1:64989/',
                      (ROOT / 'docker/renderer/Dockerfile').read_text())


class AppearanceTest(unittest.TestCase):
    """The appearance document is the website's; only its shape is assumed."""

    def test_the_rig_type_decides_which_shipped_avatar_script_runs(self):
        for rig, expected in (('R6', 'Avatar'), ('r15', 'Avatar_R15_Action'), ('R15', 'Avatar_R15_Action')):
            service = renderer.Renderer(rcc=FakeRcc(), website=FakeWebsite(rig=rig))
            self.assertEqual(service.avatar_script(3), expected)

    def test_a_missing_document_is_reported_instead_of_guessing_a_rig(self):
        service = renderer.Renderer(rcc=FakeRcc(),
                                    website=FakeWebsite(error='the appearance for user 3 could not be read'))
        with self.assertRaises(renderer.RenderError) as caught:
            service.arguments_avatar({'userId': 3})
        self.assertIn('appearance', str(caught.exception))


class RccReplyTest(unittest.TestCase):
    """The SOAP reply parser must find the image, not the first long string."""

    def start_rcc(self, body):
        class Soap(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                self.rfile.read(length)
                payload = body.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/xml; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *arguments):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Soap)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return f'http://127.0.0.1:{server.server_address[1]}/'

    def envelope(self, *texts):
        body = ''.join(f'<t>{text}</t>' for text in texts)
        return ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                f'<s:Body><OpenJobExResponse xmlns="http://silrev.biz/">{body}</OpenJobExResponse>'
                '</s:Body></s:Envelope>')

    def test_a_decoy_long_string_before_the_image_is_ignored(self):
        """A job id or a log line must not be mistaken for the rendered image."""
        image = base64.b64encode(make_png(420, 420)).decode()
        url = self.start_rcc(self.envelope('x' * 400, image))
        client = renderer.RccClient(url=url, job_timeout=30)
        rendered = client.render('Image', [7, 'http://website.test', 'PNG', 420, 420], 420)
        self.assertEqual(renderer.image_details(rendered), ('PNG', 420, 420))

    def test_a_reply_without_an_image_is_reported_clearly(self):
        url = self.start_rcc(self.envelope('job-finished-with-no-image-at-all'))
        client = renderer.RccClient(url=url, job_timeout=30)
        with self.assertRaises(renderer.RenderError) as caught:
            client.render('Image', [7, 'http://website.test', 'PNG', 420, 420], 420)
        self.assertIn('did not return a usable image', str(caught.exception))

    def test_a_soap_fault_is_reported_as_a_render_error(self):
        url = self.start_rcc('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                             '<s:Fault><faultstring>script file not found</faultstring></s:Fault>'
                             '</s:Body></s:Envelope>')
        client = renderer.RccClient(url=url, job_timeout=30)
        with self.assertRaises(renderer.RenderError) as caught:
            client.render('Image', [7, 'http://website.test', 'PNG', 420, 420], 420)
        self.assertIn('script file not found', str(caught.exception))

    def test_a_real_placeholder_of_1x1_is_never_returned_as_an_image(self):
        """RCC's own scripts fall back to a tiny hourglass; that is not a render."""
        hourglass = base64.b64encode(make_png(1, 1)).decode()
        url = self.start_rcc(self.envelope(hourglass))
        client = renderer.RccClient(url=url, job_timeout=30)
        with self.assertRaises(renderer.RenderError) as caught:
            client.render('Image', [7, 'http://website.test', 'PNG', 420, 420], 420)
        self.assertIn('placeholder', str(caught.exception))


class RccHealthTest(unittest.TestCase):
    """Health has to distinguish a busy RCC from a dead one."""

    def start_rcc(self, body):
        class Soap(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                self.rfile.read(length)
                payload = body.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/xml; charset=utf-8')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *arguments):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Soap)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return f'http://127.0.0.1:{server.server_address[1]}/'

    def version_reply(self, version='0.450.0.411923'):
        return ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
                f'<s:Body><GetVersionResponse xmlns="http://silrev.biz/"><GetVersionResult>{version}'
                '</GetVersionResult></GetVersionResponse></s:Body></s:Envelope>')

    def test_a_render_in_flight_is_healthy_not_broken(self):
        client = renderer.RccClient(url=self.start_rcc(self.version_reply()), job_timeout=30)
        client.version = '0.450.0.411923'
        client.last_contact_at = time.monotonic() - 300
        client.busy = True
        health = client.health()
        self.assertEqual(health['version'], '0.450.0.411923')
        self.assertTrue(health['busy'])
        self.assertGreater(health['lastContactSeconds'], 100)

    def test_a_stale_idle_renderer_asks_rcc_again(self):
        client = renderer.RccClient(url=self.start_rcc(self.version_reply('9.9.9.9')), job_timeout=30)
        client.last_contact_at = time.monotonic() - 300
        health = client.health()
        self.assertEqual(health['version'], '9.9.9.9')
        self.assertLess(health['lastContactSeconds'], 5)

    def test_an_unreachable_rcc_is_reported_as_unhealthy(self):
        client = renderer.RccClient(url='http://127.0.0.1:1/', job_timeout=2)
        client.last_contact_at = time.monotonic() - 300
        with self.assertRaises(renderer.RenderError):
            client.health()


class AuthorizationSourceTest(unittest.TestCase):
    """Enabling the renderer must not require restarting it."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'website.json'

    def write(self, value):
        self.path.write_text(json.dumps({'Render': {'BaseUrl': 'http://renderer:3521/', 'Authorization': value}}))

    def test_the_value_is_read_and_refreshed_from_the_mounted_config(self):
        self.write('first-value')
        source = renderer.AuthorizationSource(path=str(self.path), refresh=0.0)
        self.assertEqual(source.current(), 'first-value')
        self.write('second-value')
        # refresh=0.0 forces a re-read, as a restart of the website would produce.
        self.assertEqual(source.current(), 'second-value')
        self.write('')
        self.assertIsNone(source.current(), 'an emptied value must not stay accepted')

    def test_a_missing_file_does_not_raise(self):
        source = renderer.AuthorizationSource(path='/does/not/exist.json', refresh=0.0)
        self.assertIsNone(source.current())


class RendererRestartToleranceTest(unittest.TestCase):
    """A renderer that is being restarted must be waited for, not misread."""

    def setUp(self):
        os.environ.pop('RENDERER_TEST', None)
        spec = importlib.util.spec_from_file_location('renderer_test_retry',
                                                      ROOT / 'scripts/test_local_renderer.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def flaky_server(self, failures=1, break_length=None):
        """A server that resets the first `failures` connections, then answers JSON."""
        state = {'seen': 0}

        class Flaky(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                self.rfile.read(length)
                state['seen'] += 1
                if state['seen'] <= failures:
                    if break_length is not None:
                        # A response that stops in the middle of the status line.
                        self.wfile.write(b'HTTP/1.1 20')
                        self.wfile.flush()
                    self.close_connection = True
                    return
                payload = json.dumps({'success': True, 'data': 'x'}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *arguments):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Flaky)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return f'http://127.0.0.1:{server.server_address[1]}', state

    def test_a_reset_connection_is_retried_and_then_succeeds(self):
        url, state = self.flaky_server(failures=1)
        self.module.RENDERER_URL = url
        status, body = self.module.renderer_post('/image/image', {'assetId': 1}, attempts=3, pause=0)
        self.assertEqual(status, 200)
        self.assertTrue(body['success'])
        self.assertEqual(state['seen'], 2, 'the reset connection must be retried once')

    def test_a_truncated_answer_is_retried_too(self):
        url, state = self.flaky_server(failures=1, break_length=True)
        self.module.RENDERER_URL = url
        status, _ = self.module.renderer_post('/image/image', {'assetId': 1}, attempts=3, pause=0)
        self.assertEqual(status, 200)
        self.assertEqual(state['seen'], 2)

    def test_a_real_http_answer_is_never_retried(self):
        url, state = self.flaky_server(failures=0)
        self.module.RENDERER_URL = url
        status, _ = self.module.renderer_post('/image/image', {'assetId': 1}, attempts=3, pause=0)
        self.assertEqual((status, state['seen']), (200, 1))

    def test_an_unreachable_renderer_fails_with_a_clear_message(self):
        self.module.RENDERER_URL = 'http://127.0.0.1:1'
        with self.assertRaises(SystemExit) as caught:
            self.module.renderer_post('/image/image', {'assetId': 1}, attempts=2, pause=0)
        self.assertIn('did not answer POST /image/image', str(caught.exception))


class WarmupTest(unittest.TestCase):
    def test_the_warm_up_renders_the_builds_own_rig_through_its_own_script(self):
        rcc = FakeRcc()
        service = renderer.Renderer(rcc=rcc, website=FakeWebsite())
        service.warm_up()
        self.assertEqual(service.warmup['state'], 'ok', service.warmup)
        self.assertEqual(service.warmup['script'], renderer.WARMUP_SCRIPT)
        self.assertEqual(rcc.calls[0][0], 'Model')
        self.assertEqual(rcc.calls[0][1][0], renderer.R6_RIG)
        self.assertEqual(rcc.calls[0][1][4], 'http://website.test')

    def test_a_failed_warm_up_is_reported_not_hidden(self):
        service = renderer.Renderer(rcc=FakeRcc(error='RCC is unreachable'), website=FakeWebsite())
        service.warm_up()
        self.assertEqual(service.warmup['state'], 'failed')
        self.assertIn('unreachable', service.warmup['detail'])


class ServerCase(unittest.TestCase):
    authorization = 'unit-test-authorization-value'

    def start_server(self, rcc):
        server = renderer.ThreadingHTTPServer(('127.0.0.1', 0), renderer.Handler)
        server.daemon_threads = True
        renderer.Handler.renderer = renderer.Renderer(rcc=rcc, website=FakeWebsite())
        renderer.Handler.authorization_source = None
        renderer.Handler.authorization = self.authorization
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.port = server.server_address[1]
        self.url = f'http://127.0.0.1:{self.port}'

    def post(self, path, payload=None, authorization=..., raw=None):
        body = raw if raw is not None else json.dumps(payload or {}).encode()
        headers = {'Content-Type': 'application/json'}
        if authorization is ...:
            headers['Authorization'] = self.authorization
        elif authorization is not None:
            headers['Authorization'] = authorization
        request = urllib.request.Request(self.url + path, data=body, headers=headers, method='POST')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def test_a_supported_render_returns_a_real_image(self):
        rcc = FakeRcc()
        self.start_server(rcc)
        status, body = self.post('/image/image', {'assetId': 5})
        self.assertEqual(status, 200)
        self.assertTrue(body['success'])
        self.assertEqual(renderer.image_details(base64.b64decode(body['data'])), ('PNG', 420, 420))
        self.assertEqual(rcc.calls[0][0], 'Image')
        self.assertEqual(rcc.calls[0][2], 420)

    def test_missing_or_wrong_authorization_is_refused(self):
        self.start_server(FakeRcc())
        status, body = self.post('/image/image', {'assetId': 5}, authorization=None)
        self.assertEqual((status, body['success']), (401, False))
        status, _ = self.post('/image/image', {'assetId': 5}, authorization='wrong-value')
        self.assertEqual(status, 401)

    def test_an_unsupported_endpoint_is_explicit_about_the_missing_piece(self):
        self.start_server(FakeRcc())
        status, body = self.post('/image/clothing', {'assetId': 1})
        self.assertEqual(status, 501)
        self.assertIn('mannequin', body['message'])

    def test_a_placeholder_is_never_returned_as_success(self):
        rcc = FakeRcc(image=make_png(1, 1))
        self.start_server(rcc)
        status, body = self.post('/image/image', {'assetId': 5})
        self.assertEqual(status, 502)
        self.assertFalse(body['success'])
        self.assertIn('placeholder', body['message'])

    def test_a_render_failure_is_json_not_a_traceback(self):
        self.start_server(FakeRcc(error='RCC is unreachable: connection refused'))
        status, body = self.post('/image/image', {'assetId': 5})
        self.assertEqual(status, 502)
        self.assertIn('unreachable', body['message'])

    def test_invalid_json_and_unknown_paths_are_reported(self):
        self.start_server(FakeRcc())
        status, body = self.post('/image/image', raw=b'not json')
        self.assertEqual(status, 400)
        self.assertIn('Invalid JSON', body['message'])
        status, _ = self.post('/nope', {'assetId': 1})
        self.assertEqual(status, 404)

    def test_health_reports_rcc_and_the_warm_up_or_fails_honestly(self):
        self.start_server(FakeRcc())
        renderer.Handler.renderer.warmup = {'state': 'ok', 'script': 'Model'}
        with urllib.request.urlopen(self.url + '/healthz', timeout=10) as response:
            body = json.loads(response.read())
        self.assertEqual(body['rcc']['version'], '0.450.0.411923')
        self.assertEqual(body['warmup']['state'], 'ok')
        self.start_server(FakeRcc(error='RCC is unreachable'))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(self.url + '/healthz', timeout=10)
        self.assertEqual(caught.exception.code, 503)

    def test_a_failing_render_is_retried_a_bounded_number_of_times(self):
        rcc = FakeRcc(error='the render returned a 1x1 placeholder instead of an image')
        self.start_server(rcc)
        renderer.Handler.renderer.max_retries = 1
        status, _ = self.post('/image/image', {'assetId': 5})
        self.assertEqual(status, 502)
        self.assertEqual(len(rcc.calls), 2, 'one attempt plus one bounded retry, never a loop')


class PixelReaderTest(unittest.TestCase):
    """The integration test's pixel reader must be trustworthy on real PNG bytes."""

    def setUp(self):
        # Loading the integration test must not run its stack checks.
        os.environ.pop('RENDERER_TEST', None)
        spec = importlib.util.spec_from_file_location('renderer_test_helpers',
                                                      ROOT / 'scripts/test_local_renderer.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_left_and_right_halves_are_read_back(self):
        width, height, rows = self.module.read_png(make_png(64, 16))
        self.assertEqual((width, height), (64, 16))
        left = self.module.colour_at(rows, width, 5, 8)
        right = self.module.colour_at(rows, width, 60, 8)
        self.assertGreater(left[0], 200)
        self.assertLess(left[2], 60)
        self.assertGreater(right[2], 200)
        self.assertLess(right[0], 60)

    def test_a_filtered_png_is_decoded_correctly(self):
        """Real encoders emit filtered scanlines; the reader must undo them."""
        width, height, rows = self.module.read_png(make_png(8, 4))
        raw = b''
        for row in rows:
            filtered = bytes((row[i] - (row[i - 3] if i >= 3 else 0)) & 0xFF for i in range(len(row)))
            raw += b'\x01' + filtered

        def chunk(kind, payload):
            return (struct.pack('>I', len(payload)) + kind + payload
                    + struct.pack('>I', zlib.crc32(kind + payload) & 0xFFFFFFFF))

        container = (b'\x89PNG\r\n\x1a\n'
                     + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(raw, 6)) + chunk(b'IEND', b''))
        width2, height2, rows2 = self.module.read_png(container)
        self.assertEqual((width2, height2), (width, height))
        self.assertEqual(self.module.colour_at(rows2, width2, 1, 2), self.module.colour_at(rows, width, 1, 2))
        self.assertEqual(self.module.colour_at(rows2, width2, 6, 2), self.module.colour_at(rows, width, 6, 2))


if __name__ == '__main__':
    unittest.main()
