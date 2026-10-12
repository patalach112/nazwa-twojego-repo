"""Contract and behaviour tests for the renderer service; real RCC rendering needs CI.

These tests run without Docker or Wine:

* every route the renderer answers must have a matching Lua script, and the
  argument names in the script header must equal the names the renderer builds,
  so a renamed or reordered argument cannot silently render the wrong thing,
* the HTTP surface is exercised against a real server with a substitute RCC:
  authorization, unsupported endpoints, placeholder rejection, JSON errors,
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
import unittest
import urllib.error
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / 'docker/rcc-wine/internalscripts/thumbnails'
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


class LuaContractTest(unittest.TestCase):
    """The renderer and the Lua scripts must agree on every argument."""

    def declared_arguments(self, script):
        header = script.read_text().splitlines()[0]
        self.assertTrue(header.startswith('-- args: '), f'{script.name} has no argument header')
        return [name.strip() for name in header[len('-- args: '):].split(',')]

    def built_arguments(self, route, payload):
        service = renderer.Renderer(rcc=FakeRcc(), website=FakeWebsite())
        builder = service.routes[route]
        return builder(service, payload)

    def test_every_route_is_backed_by_a_script_and_matching_arguments(self):
        """The Lua headers are the contract; the renderer must satisfy them exactly."""
        service = renderer.Renderer(rcc=FakeRcc(), website=FakeWebsite())
        cases = {
            '/image/image': {'assetId': 7},
            '/catalog/model': {'assetId': 7},
            '/catalog/hat': {'assetId': 7},
            '/catalog/mesh': {'assetId': 7},
            '/catalog/head': {'assetId': 7},
            '/image/clothing': {'assetId': 7},
            '/image/teeshirt': {'assetId': 7},
            '/player/thumbnail': {'userId': 3},
            '/player/headshot': {'userId': 3},
        }
        self.assertEqual(set(cases), set(service.routes), 'every route needs a case here')
        script_types = set()
        for route, payload in cases.items():
            builder = service.routes[route]
            script_type, arguments = builder(service, payload)
            script_types.add(script_type)
            script = SCRIPTS / (script_type + '.lua')
            self.assertTrue(script.is_file(), f'{route} needs {script.name}')
            declared = self.declared_arguments(script)
            self.assertEqual(len(arguments), len(declared),
                             f'{script.name} declares {declared} but {route} passes {arguments}')
            for name, value in zip(declared, arguments):
                if name == 'fileExtension':
                    self.assertEqual(value, 'PNG', f'{script.name}: unexpected format {value!r}')
                elif name in ('width', 'height'):
                    self.assertEqual(value, 420, f'{script.name}: unexpected size {value!r}')
                elif name.endswith('Url'):
                    self.assertIsInstance(value, str, f'{script.name}: {name} must be a URL string')
                    self.assertTrue(value, f'{script.name}: {name} must not be empty')
                elif name == 'clothingUrls':
                    self.assertIsInstance(value, str, f'{script.name}: {name} must be a string')
                elif name == 'baseUrl':
                    self.assertEqual(value, 'http://website.test', f'{script.name}: unexpected base URL')
                elif name.endswith('Color'):
                    self.assertIsInstance(value, int, f'{script.name}: {name} must be a BrickColor number')
                elif name in ('hideSky', 'crop'):
                    self.assertIsInstance(value, bool, f'{script.name}: {name} must be a flag')
                else:
                    self.fail(f'{script.name}: the renderer passes {name}, which no test covers')
        expected_types = {'Image', 'Model', 'Mesh', 'Head', 'Clothing', 'TeeShirt', 'Avatar', 'Closeup'}
        self.assertEqual(script_types, expected_types, 'a script is unused or missing from this contract')
        self.assertTrue((SCRIPTS / 'README.md').is_file(), 'the script contract must be documented')

    def test_avatar_arguments_carry_the_worn_clothing_in_script_order(self):
        script_type, arguments = self.built_arguments('/player/thumbnail', {'userId': 3})
        self.assertEqual(script_type, 'Avatar')
        self.assertEqual(arguments[:4], ['PNG', 420, 420, renderer.R6_RIG])
        self.assertEqual(len(arguments), 12)
        self.assertEqual(arguments[4:10], [1, 2, 3, 4, 5, 6], 'body colours keep the script order')
        self.assertEqual(arguments[10], 'http://website.test/asset/?id=11', 'worn clothing is passed')
        self.assertEqual(arguments[11], 'http://website.test')

    def test_an_r15_account_uses_the_r15_rig(self):
        service = renderer.Renderer(rcc=FakeRcc(), website=FakeWebsite(rig='R15'))
        script_type, arguments = service.arguments_avatar({'userId': 3})
        self.assertEqual(script_type, 'Avatar')
        self.assertEqual(arguments[3], renderer.R15_RIG)

    def test_missing_ids_are_rejected_instead_of_rendering_asset_zero(self):
        for payload in ({}, {'assetId': 'x'}, {'assetId': 0}, {'assetId': -3}):
            with self.assertRaises(renderer.RenderError):
                self.built_arguments('/image/image', payload)
        for payload in ({}, {'userId': 'x'}, {'userId': 0}):
            with self.assertRaises(renderer.RenderError):
                self.built_arguments('/player/thumbnail', payload)

    def test_unsupported_endpoints_are_explicit_and_disjoint(self):
        service = renderer.Renderer(rcc=FakeRcc(), website=FakeWebsite())
        self.assertTrue(service.unsupported, 'the unsupported list must not be empty')
        self.assertEqual(service.unsupported & set(service.routes), set(),
                         'an endpoint cannot be both supported and unsupported')

    def test_untrusted_requests_cannot_choose_a_url(self):
        service = renderer.Renderer(rcc=FakeRcc(), website=FakeWebsite())
        _, arguments = service.arguments_image({'assetId': 12, 'assetUrl': 'http://evil.example/'})
        self.assertEqual(arguments[0], 'http://website.test/asset/?id=12',
                         'the asset URL must come from configuration, never from the request')


class FakeRcc:
    def __init__(self, image=None, error=None):
        self.image = image
        self.error = error
        self.calls = []
        self.jobs = []

    def get_version(self, max_age=10.0):
        if self.error:
            raise renderer.RenderError(self.error)
        return '0.450.0.411923'

    def render(self, script_type, arguments, expected_edge):
        self.calls.append((script_type, arguments, expected_edge))
        self.jobs.append(script_type)
        if self.error:
            raise renderer.RenderError(self.error)
        image = self.image if self.image is not None else make_png(420, 420)
        return renderer.decode_render_output(base64.b64encode(image).decode(), expected_edge)


class FakeWebsite:
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


class AppearanceTest(unittest.TestCase):
    """The appearance document is the website's, so only its shape is assumed."""

    def test_both_colour_spellings_are_accepted_in_script_order(self):
        appearance = {'bodyColors': {'HeadColor': 1, 'TorsoColor': 2, 'leftArmColorId': 3,
                                     'RightArmColor': 4, 'LeftLegColor': '5'}}
        self.assertEqual(renderer.body_colours(appearance), [1, 2, 3, 4, 5, 0],
                         'a missing colour must become 0, not shift the list')

    def test_an_empty_or_missing_document_does_not_raise(self):
        self.assertEqual(renderer.body_colours({}), [0] * 6)
        self.assertEqual(renderer.body_colours({'bodyColors': {'HeadColor': 'not-a-number'}})[0], 0)

    def test_clothing_is_passed_in_a_fixed_order_and_only_from_this_website(self):
        appearance = {'assetAndAssetTypeIds': [
            {'assetId': 21, 'assetTypeId': 12},   # Pants
            {'assetId': 11, 'assetTypeId': 11},   # Shirt
            {'assetId': 31, 'assetTypeId': 8},    # Hat, not clothing
            {'assetId': 41, 'assetTypeId': 2},    # TeeShirt
        ]}
        urls = renderer.clothing_urls(appearance, 'http://website.test').split(';')
        self.assertEqual(urls, ['http://website.test/asset/?id=11', 'http://website.test/asset/?id=21',
                                'http://website.test/asset/?id=41'])

    def test_an_account_without_clothing_or_ids_yields_no_urls(self):
        self.assertEqual(renderer.clothing_urls({}, 'http://website.test'), '')
        self.assertEqual(renderer.clothing_urls({'assetAndAssetTypeIds': [{'assetId': 0, 'assetTypeId': 11}]},
                                                'http://website.test'), '')


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
        decoy = 'x' * 400
        url = self.start_rcc(self.envelope(decoy, image))
        client = renderer.RccClient(url=url, job_timeout=30)
        rendered = client.render('Image', ['http://website.test/asset/?id=1', 'PNG', 420, 420], 420)
        self.assertEqual(renderer.image_details(rendered), ('PNG', 420, 420))

    def test_a_reply_without_an_image_is_reported_clearly(self):
        url = self.start_rcc(self.envelope('job-finished-with-no-image-at-all'))
        client = renderer.RccClient(url=url, job_timeout=30)
        with self.assertRaises(renderer.RenderError) as caught:
            client.render('Image', ['http://website.test/asset/?id=1', 'PNG', 420, 420], 420)
        self.assertIn('did not return a usable image', str(caught.exception))

    def test_a_soap_fault_is_reported_as_a_render_error(self):
        url = self.start_rcc('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
                             '<s:Fault><faultstring>script file not found</faultstring></s:Fault>'
                             '</s:Body></s:Envelope>')
        client = renderer.RccClient(url=url, job_timeout=30)
        with self.assertRaises(renderer.RenderError) as caught:
            client.render('Image', ['http://website.test/asset/?id=1', 'PNG', 420, 420], 420)
        self.assertIn('script file not found', str(caught.exception))


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

    def test_the_environment_wins_and_survives_a_missing_file(self):
        source = renderer.AuthorizationSource(path=str(self.path), refresh=0.0)
        self.assertIsNone(source.current())
        self.assertTrue(renderer.AuthorizationSource(
            path='/does/not/exist.json').current() in (None, ''))


class WarmupTest(unittest.TestCase):
    def test_warmup_reports_every_step_and_states(self):
        rcc = FakeRcc()
        service = renderer.Renderer(rcc=rcc, website=FakeWebsite())
        service.warm_up()
        self.assertEqual(service.warmup['state'], 'ok', service.warmup)
        self.assertEqual(set(service.warmup['steps']), {'texture', 'rig'})
        self.assertEqual(rcc.calls[0][0], 'Image')
        self.assertTrue(rcc.calls[0][1][0].endswith(renderer.WARMUP_TEXTURE))

    def test_a_failing_texture_warmup_is_reported_not_hidden(self):
        service = renderer.Renderer(rcc=FakeRcc(error='RCC is unreachable'), website=FakeWebsite())
        service.warm_up()
        self.assertEqual(service.warmup['state'], 'failed')
        self.assertIn('unreachable', service.warmup['steps']['texture']['detail'])


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
        if authorization is not ...:
            if authorization is not None:
                headers['Authorization'] = authorization
        else:
            headers['Authorization'] = self.authorization
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

    def test_an_unsupported_endpoint_is_explicit(self):
        self.start_server(FakeRcc())
        status, body = self.post('/game/thumbnail', {'placeId': 1})
        self.assertEqual(status, 501)
        self.assertIn('not implemented', body['message'])

    def test_a_placeholder_is_never_returned_as_success(self):
        rcc = FakeRcc(image=base64.b64decode(
            b'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=='))
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
        status, body = self.post('/image/image', {}, )
        self.assertEqual(status, 502)

    def test_invalid_json_and_unknown_paths_are_reported(self):
        self.start_server(FakeRcc())
        status, body = self.post('/image/image', raw=b'not json')
        self.assertEqual(status, 400)
        self.assertIn('Invalid JSON', body['message'])
        status, body = self.post('/nope', {'assetId': 1})
        self.assertEqual(status, 404)

    def test_health_reports_the_rcc_version_the_warm_up_and_a_failure_honestly(self):
        self.start_server(FakeRcc())
        renderer.Handler.renderer.warmup = {'state': 'ok', 'steps': {}}
        with urllib.request.urlopen(self.url + '/healthz', timeout=10) as response:
            body = json.loads(response.read())
        self.assertEqual(body['rccVersion'], '0.450.0.411923')
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
        sys.path.insert(0, str(ROOT / 'scripts'))
        spec = importlib.util.spec_from_file_location('renderer_test_helpers',
                                                      ROOT / 'scripts/test_local_renderer.py')
        # The integration test must not run its stack checks on import.
        os.environ.pop('RENDERER_TEST', None)
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_left_and_right_halves_are_read_back(self):
        data = make_png(64, 16)
        width, height, rows = self.module.read_png(data)
        self.assertEqual((width, height), (64, 16))
        left = self.module.colour_at(rows, width, 5, 8)
        right = self.module.colour_at(rows, width, 60, 8)
        self.assertGreater(left[0], 200)
        self.assertLess(left[2], 60)
        self.assertGreater(right[2], 200)
        self.assertLess(right[0], 60)

    def test_a_filtered_png_is_decoded_correctly(self):
        # Build the same image, then re-encode it with filter type 1 (Sub) on every
        # row, which is what real encoders emit; the reader must still agree.
        width, height, rows = self.module.read_png(make_png(8, 4))
        raw = b''
        previous = [0] * (width * 3)
        for row in rows:
            filtered = bytes((row[i] - (row[i - 3] if i >= 3 else 0)) & 0xFF for i in range(len(row)))
            raw += b'\x01' + filtered
            previous = row
        data = make_png(8, 4)
        # Rebuild a container around the re-encoded scanlines.
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
