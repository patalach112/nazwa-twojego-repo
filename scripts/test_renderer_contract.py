#!/usr/bin/env python3
"""Contract tests for the local renderer, its RCC scripts and its container layout.

These run without Docker, so they are the tests the renderer workflow runs first. They
check three things that have to stay true:

* the renderer calls RCC exactly the way RCC's own thumbnail scripts expect - the scripts
  are the build's, loaded by ``Settings.Type``, and are never modified or shadowed here;
* the renderer refuses to answer with a placeholder (including the fallback images the
  shipped scripts carry), an unimplemented endpoint or a wrong secret;
* the container layout keeps RCC and the renderer in one container, because RCC binds its
  SOAP port to the loopback of whichever container it runs in.
"""

import base64
import contextlib
import http.client
import io
import http.server
import importlib.util
import json
import os
from pathlib import Path
import shutil
import ssl
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

import urllib.error
import urllib.request
import yaml
import zlib

ROOT = Path(__file__).resolve().parents[1]
# The scripts RCC 2020 itself ships and loads by Settings.Type. The renderer has to
# satisfy their argument lists, and this repository must not shadow them.
SCRIPTS = ROOT / 'RCCService/RCCService2020/internalscripts/thumbnails'
sys.path.insert(0, str(ROOT / 'docker/renderer'))
import asset_tls  # noqa: E402
import renderer  # noqa: E402
sys.path.insert(0, str(ROOT / 'scripts'))
import configure_local_docker  # noqa: E402
import probe_rcc_image  # noqa: E402


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
            + chunk(b'IDAT', zlib.compress(b''.join(rows), 9))
            + chunk(b'IEND', b''))


class FakeRcc:
    """Substitute RCC: records the jobs it is asked for and answers with a real PNG."""

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

    def render(self, script_type, arguments, expected_edge, placeholders=None):
        self.calls.append((script_type, arguments, expected_edge))
        self.jobs.append(script_type)
        if self.error:
            raise renderer.RenderError(self.error)
        image = self.image if self.image is not None else make_png(420, 420)
        return renderer.decode_render_output(base64.b64encode(image).decode(), expected_edge,
                                             placeholders)


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


def declared_arguments(script):
    """The argument names in the first 'local ... = ...' line of a shipped script."""
    for line in (SCRIPTS / f'{script}.lua').read_text().splitlines():
        if '= ...' not in line:
            continue
        names = line.split('=')[0].strip()
        if names.startswith('local '):
            names = names[len('local '):]
        return [name.strip() for name in names.split(',')]
    raise AssertionError(f'{script}.lua does not declare its arguments')


class LuaContractTest(unittest.TestCase):
    """The renderer must satisfy RCC's own scripts, and never shadow them."""

    def service(self, **kwargs):
        kwargs.setdefault('rcc', FakeRcc())
        kwargs.setdefault('website', FakeWebsite(**kwargs.pop('website_kwargs', {})))
        kwargs.setdefault('placeholders', {})
        return renderer.Renderer(**kwargs)

    def test_every_route_uses_a_script_the_build_ships(self):
        service = self.service()
        payloads = {'/image/image': {'assetId': 7}, '/catalog/model': {'assetId': 7},
                    '/catalog/hat': {'assetId': 7}, '/catalog/mesh': {'assetId': 7},
                    '/player/thumbnail': {'userId': 3}, '/player/headshot': {'userId': 3}}
        self.assertEqual(set(payloads), set(service.routes))
        for path, payload in payloads.items():
            script, arguments = service.routes[path](service, payload)
            self.assertTrue((SCRIPTS / f'{script}.lua').is_file(), f'{path} -> {script}.lua is missing')
            self.assertEqual(len(arguments), len(declared_arguments(script)),
                             f'{path} -> {script}.lua takes {declared_arguments(script)}')

    def test_every_argument_matches_the_declared_name(self):
        service = self.service()
        for path, payload, expected in (
                ('/image/image', {'assetId': 7},
                 {'imageId': 7, 'baseUrl': 'http://website.test', 'fileExtension': 'PNG', 'x': 420, 'y': 420}),
                ('/catalog/model', {'assetId': 7},
                 {'assetUrl': 'http://website.test/asset/?id=7', 'fileExtension': 'PNG',
                  'x': 420, 'y': 420, 'baseUrl': 'http://website.test'}),
                ('/catalog/hat', {'assetId': 7},
                 {'assetUrl': 'http://website.test/asset/?id=7', 'fileExtension': 'PNG',
                  'x': 420, 'y': 420, 'baseUrl': 'http://website.test'}),
                ('/catalog/mesh', {'assetId': 7},
                 {'assetUrl': 'http://website.test/asset/?id=7', 'fileExtension': 'PNG',
                  'x': 420, 'y': 420, 'baseUrl': 'http://website.test'}),
                ('/player/thumbnail', {'userId': 3},
                 {'characterAppearanceUrl': 'http://website.test/v1.1/avatar-fetch?userId=3',
                  'baseUrl': 'http://website.test', 'fileExtension': 'PNG', 'x': 420, 'y': 420}),
                ('/player/headshot', {'userId': 3},
                 {'baseUrl': 'http://website.test',
                  'characterAppearanceUrl': 'http://website.test/v1.1/avatar-fetch?userId=3',
                  'fileExtension': 'PNG', 'x': 420, 'y': 420, 'quadratic': True,
                  'baseHatZoom': 40, 'maxHatZoom': 60, 'cameraOffsetX': 0, 'cameraOffsetY': 0})):
            script, arguments = service.routes[path](service, payload)
            names = declared_arguments(script)
            self.assertEqual([names[i] for i in range(len(arguments))], names,
                             f'{path} must pass exactly the declared arguments of {script}.lua')
            values = dict(zip(names, arguments))
            for name, wanted in expected.items():
                self.assertEqual(values[name], wanted, f'{path} passes {name}')

    def test_the_avatar_route_uses_the_script_that_covers_both_rigs(self):
        # The build's R6-only Avatar.lua sets GraphicsMode and fetches the appearance
        # document through HttpService, which RCC refuses for a host that is not compiled
        # into its binary; the original stack's own render request asked for
        # Avatar_R15_Action for every avatar, and that script says it covers R6 as well.
        for service in (self.service(), self.service(website_kwargs={'rig': 'R15'})):
            script, arguments = service.routes['/player/thumbnail'](service, {'userId': 3})
            self.assertEqual(script, 'Avatar_R15_Action')
            # The order and the count are the script's own declared ones, read from the
            # shipped file - not from a copy of the header that lives in this repository.
            declared = declared_arguments(script)
            self.assertEqual(declared, list(service.avatar_argument_order))
            self.assertEqual(len(arguments), len(declared))

    def test_the_closeup_zoom_slice_matches_this_projects_own_template(self):
        template = json.loads((ROOT / 'game-renderer/scripts/Closeup.json').read_text())
        arguments = template[0]['Settings']['Arguments'] if isinstance(template, list) else template['Settings']['Arguments']
        service = self.service()
        _, built = service.routes['/player/headshot'](service, {'userId': 3})
        self.assertEqual(built[5:], arguments[5:], 'the Closeup tail must stay the template values')
        self.assertEqual(built[5], True)

    def test_urls_come_from_configuration_not_from_the_request(self):
        service = self.service()
        payload = {'assetId': 7, 'assetUrl': 'http://evil.test/asset/?id=7',
                   'baseUrl': 'http://evil.test', 'fileExtension': 'EXE', 'x': 1, 'y': 1}
        for path in ('/image/image', '/catalog/model', '/catalog/hat', '/catalog/mesh'):
            script, arguments = service.routes[path](service, payload)
            for value in arguments:
                if isinstance(value, str):
                    self.assertNotIn('evil.test', value, f'{path} used a request supplied URL')
            self.assertIn('PNG', arguments)
            self.assertNotIn('EXE', arguments)

    def test_a_request_without_a_real_id_is_refused(self):
        service = self.service()
        for payload in ({}, {'assetId': 0}, {'assetId': -3}, {'assetId': 'abc'}, {'assetId': None},
                        {'userId': 0}, {'userId': 'x'}):
            for path in service.routes:
                with self.assertRaises(renderer.RenderError, msg=f'{path} accepted {payload}'):
                    service.routes[path](service, payload)

    def test_the_website_does_not_have_to_answer_for_an_avatar_argument_list(self):
        # The build's own avatar script fetches the appearance document inside RCC, so a
        # website that is slow or down must not stand between the request and the render.
        service = self.service(website_kwargs={'error': 'the website did not answer'})
        script, arguments = service.routes['/player/thumbnail'](service, {'userId': 3})
        self.assertEqual(script, 'Avatar_R15_Action')
        self.assertIn('/v1.1/avatar-fetch?userId=3', arguments[1])

    def test_unimplemented_endpoints_name_what_is_missing(self):
        service = self.service()
        expected = {'/image/clothing': 'mannequin', '/catalog/head': 'mannequin',
                    '/catalog/bodypart': 'rig', '/catalog/package': 'rig',
                    '/game/thumbnail': 'place', '/player/thumbnail-3d': 'renderer',
                    '/catalog/animation': 'animation', '/catalog/animationsilhouette': 'animation',
                    '/image/teeshirt': 'no TeeShirt thumbnail script'}
        self.assertEqual(set(expected), set(service.unsupported))
        for path, word in expected.items():
            self.assertIn(word, service.unsupported[path], path)
        for path in service.unsupported:
            with self.assertRaises(renderer.RenderError):
                service.render(path, {'assetId': 7})

    def test_the_scripts_the_build_ships_stay_untouched(self):
        shipped = sorted(path.name for path in SCRIPTS.glob('*.lua'))
        self.assertIn('Image.lua', shipped)
        self.assertIn('Avatar.lua', shipped)
        self.assertTrue((SCRIPTS / 'modules').is_dir(), 'the shipped modules must stay with the scripts')
        # This repository ships probes, not render scripts: nothing here may stand in for
        # a Roblox-authored script that RCC loads by Settings.Type.
        ours = sorted(str(path.relative_to(ROOT)) for path in (ROOT / 'docker').rglob('*.lua'))
        self.assertEqual(ours, ['docker/rcc-wine/json_probe.lua', 'docker/rcc-wine/thumbnail_probe.lua'])

    def test_the_required_script_list_matches_the_build(self):
        listed = [line.strip() for line in
                  (ROOT / 'docker/rcc-wine/required-thumbnail-scripts.txt').read_text().splitlines()
                  if line.strip() and not line.startswith('#')]
        self.assertTrue(listed)
        missing = [name for name in listed if not (SCRIPTS / f'{name}.lua').is_file()]
        self.assertEqual([], missing, 'the entrypoint would refuse to start for these')


class RccReplyTest(unittest.TestCase):
    """RCC's reply is searched for the image instead of trusting the first text node."""

    def serve(self, body):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                self.rfile.read(length)
                payload = body.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/xml')
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *arguments):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return f'http://127.0.0.1:{server.server_address[1]}/'

    def envelope(self, result_text, decoy=None):
        """A reply shaped like RCC's: several text nodes, only one of them the image."""
        head = f'<joblog>{decoy}</joblog>' if decoy else ''
        return ('<?xml version="1.0"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
                '<soap:Body><OpenJobExResult xmlns="http://silrev.biz/">'
                f'<jobid>00000000-0000-0000-0000-000000000000</jobid>{head}'
                f'<result>{result_text}</result>'
                '</OpenJobExResult></soap:Body></soap:Envelope>')

    def test_a_decoy_text_node_does_not_hide_the_render(self):
        image = base64.b64encode(make_png(420, 420)).decode()
        client = renderer.RccClient(url=self.serve(self.envelope(image, decoy='x' * 400)))
        self.assertEqual(len(client.render('Image', [1], 420)), len(base64.b64decode(image)))

    def test_a_reply_without_an_image_is_a_failure(self):
        client = renderer.RccClient(url=self.serve(self.envelope('nothing here')))
        with self.assertRaises(renderer.RenderError):
            client.render('Image', [1], 420)

    def test_a_soap_fault_is_reported_with_its_text(self):
        fault = ('<?xml version="1.0"?><soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
                 '<soap:Body><soap:Fault><faultstring>job refused</faultstring></soap:Fault>'
                 '</soap:Body></soap:Envelope>')
        client = renderer.RccClient(url=self.serve(fault))
        with self.assertRaises(renderer.RenderError) as caught:
            client.call('GetVersion', [])
        self.assertIn('job refused', str(caught.exception))


class AuthorizationSourceTest(unittest.TestCase):
    """The secret comes from the generated environment, with the config as fallback."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'website.json'
        self.saved = os.environ.pop('RENDERER_AUTHORIZATION', None)
        self.addCleanup(self.restore)

    def restore(self):
        os.environ.pop('RENDERER_AUTHORIZATION', None)
        if self.saved is not None:
            os.environ['RENDERER_AUTHORIZATION'] = self.saved

    def write(self, value):
        self.path.write_text(json.dumps({'Render': {'BaseUrl': 'http://renderer:3521/',
                                                    'Authorization': value}}))

    def test_the_value_is_read_and_refreshed_from_the_mounted_config(self):
        self.write('first-value')
        source = renderer.AuthorizationSource(path=str(self.path), refresh=0.0)
        self.assertEqual(source.current(), 'first-value')
        self.write('second-value')
        self.assertEqual(source.current(), 'second-value')
        self.write('')
        self.assertIsNone(source.current(), 'an emptied value must not stay accepted')

    def test_a_missing_file_does_not_raise_and_is_explained(self):
        source = renderer.AuthorizationSource(path='/does/not/exist.json', refresh=0.0)
        self.assertIsNone(source.current())
        status = source.status()
        self.assertFalse(status['exists'])
        self.assertFalse(status['usable'])

    def test_the_generated_environment_wins_over_the_mounted_config(self):
        # A single-file bind mount does not reliably show a config rewritten after the
        # container started, so the environment is the source that cannot be missed.
        self.write('from-the-file')
        os.environ['RENDERER_AUTHORIZATION'] = 'from-the-environment'
        source = renderer.AuthorizationSource(path=str(self.path), refresh=0.0)
        self.assertEqual(source.current(), 'from-the-environment')
        self.assertTrue(source.status()['usable'])
        self.assertEqual(source.status()['source'], 'environment')

    def test_an_empty_environment_value_falls_back_to_the_config(self):
        self.write('from-the-file')
        os.environ['RENDERER_AUTHORIZATION'] = ''
        source = renderer.AuthorizationSource(path=str(self.path), refresh=0.0)
        self.assertEqual(source.current(), 'from-the-file')


class WarmupTest(unittest.TestCase):
    """The first render must not happen inside a user's ten-second request."""

    def test_a_successful_warmup_is_reported(self):
        rcc = FakeRcc()
        service = renderer.Renderer(rcc=rcc, website=FakeWebsite(), placeholders={})
        service.warm_up()
        self.assertEqual(service.warmup['state'], 'ok')
        self.assertEqual(rcc.jobs, [service.warmup['script']])
        self.assertGreater(service.warmup['bytes'], 0)

    def test_a_failed_warmup_is_reported_and_does_not_crash(self):
        service = renderer.Renderer(rcc=FakeRcc(error='RCC is busy'), website=FakeWebsite(),
                                    placeholders={})
        service.warm_up()
        self.assertEqual(service.warmup['state'], 'failed')
        self.assertIn('busy', service.warmup['detail'])


class HttpSurfaceTest(unittest.TestCase):
    """The HTTP answer the website's render client reads, without any Docker."""

    def start(self, service, authorization='secret-value'):
        class TestHandler(renderer.Handler):
            pass

        TestHandler.renderer = service
        TestHandler.authorization_source = None
        TestHandler.authorization = authorization
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), TestHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        self.base = f'http://127.0.0.1:{server.server_address[1]}'

    def post(self, path, payload, authorization='secret-value'):
        body = json.dumps(payload).encode()
        headers = {'Content-Type': 'application/json'}
        if authorization is not None:
            headers['Authorization'] = authorization
        request = urllib.request.Request(self.base + path, data=body, headers=headers, method='POST')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def get(self, path):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(self.base + path, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def service(self, rcc=None, placeholders=None):
        return renderer.Renderer(rcc=rcc or FakeRcc(), website=FakeWebsite(),
                                 placeholders=placeholders if placeholders is not None else {})

    def test_a_render_comes_back_as_base64_png_data(self):
        service = self.service()
        self.start(service)
        status, body = self.post('/image/image', {'assetId': 7})
        self.assertEqual(status, 200)
        self.assertTrue(body['success'])
        self.assertEqual(len(base64.b64decode(body['data'])), len(make_png(420, 420)))

    def test_the_job_always_closes_and_the_script_is_the_expected_one(self):
        rcc = FakeRcc()
        self.start(self.service(rcc=rcc))
        self.post('/catalog/hat', {'assetId': 7})
        self.assertEqual(rcc.jobs, ['Hat'])

    def test_a_missing_or_wrong_secret_is_refused(self):
        self.start(self.service())
        status, body = self.post('/image/image', {'assetId': 7}, authorization=None)
        self.assertEqual((status, body['success']), (401, False))
        status, _ = self.post('/image/image', {'assetId': 7}, authorization='wrong')
        self.assertEqual(status, 401)

    def test_an_unimplemented_endpoint_says_what_it_needs(self):
        self.start(self.service())
        status, body = self.post('/image/clothing', {'assetId': 7})
        self.assertEqual(status, 501)
        self.assertIn('mannequin', body['message'])
        self.assertIn('not implemented', body['message'])

    def test_a_placeholder_answer_is_a_502_not_a_picture(self):
        placeholders = renderer.load_placeholders(str(SCRIPTS))
        fallback = max(renderer.PLACEHOLDER_LITERAL.findall((SCRIPTS / 'Image.lua').read_text()),
                       key=len)
        image = base64.b64decode(fallback, validate=False)
        self.start(self.service(rcc=FakeRcc(image=image), placeholders=placeholders))
        status, body = self.post('/image/image', {'assetId': 7})
        self.assertEqual(status, 502)
        self.assertFalse(body['success'])
        self.assertIn('fallback image', body['message'])
        self.assertNotIn('data', body)

    def test_a_failing_render_is_an_error_not_an_image(self):
        self.start(self.service(rcc=FakeRcc(error='RCC did not answer')))
        status, body = self.post('/image/image', {'assetId': 7})
        self.assertEqual(status, 502)
        self.assertIn('RCC did not answer', body['message'])

    def test_a_request_without_an_asset_is_refused(self):
        self.start(self.service())
        status, _ = self.post('/image/image', {})
        self.assertEqual(status, 502)

    def test_invalid_json_and_unknown_paths_are_refused(self):
        self.start(self.service())
        request = urllib.request.Request(self.base + '/image/image', data=b'{not json',
                                           headers={'Authorization': 'secret-value',
                                                    'Content-Type': 'application/json'},
                                           method='POST')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(request, timeout=30)
        self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.post('/nope/nope', {'assetId': 7})[0], 404)

    def test_an_oversized_body_is_refused_before_it_is_read(self):
        self.start(self.service())
        request = urllib.request.Request(self.base + '/image/image',
                                         data=b'x' * (renderer.Handler.max_body + 1),
                                         headers={'Authorization': 'secret-value',
                                                  'Content-Type': 'application/json'},
                                         method='POST')
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(request, timeout=30)
        self.assertEqual(caught.exception.code, 413)

    def test_health_is_rccs_own_answer(self):
        self.start(self.service())
        status, body = self.get('/healthz')
        self.assertEqual(status, 200)
        self.assertTrue(body['success'])
        self.assertEqual(body['rcc']['version'], '0.450.0.411923')
        self.assertIn('busy', body['rcc'])
        self.assertIn('warmup', body)

    def test_health_is_503_while_rcc_never_answered(self):
        self.start(self.service(rcc=FakeRcc(error='RCC has not answered any request yet')))
        status, body = self.get('/healthz')
        self.assertEqual(status, 503)
        self.assertFalse(body['success'])
        self.assertIn('has not answered', body['message'])


class PlaceholderTest(unittest.TestCase):
    """The shipped scripts' own fallback images must never pass as a render.

    Image.lua answers a render it cannot do with a 512x512 transparent PNG: it is larger
    than the 420 pixels a render must be, so a size check accepts it, and the website then
    resizes it into a blank thumbnail that looks finished. That is exactly what happened
    once; the payloads are refused by exact bytes now.
    """

    def fallback(self, script):
        text = (SCRIPTS / f'{script}.lua').read_text()
        literals = renderer.PLACEHOLDER_LITERAL.findall(text)
        self.assertTrue(literals, f'{script}.lua carries no embedded image literal')
        return max((base64.b64decode(value, validate=True) for value in literals), key=len)

    def test_the_shipped_fallbacks_are_found_and_named(self):
        placeholders = renderer.load_placeholders(str(SCRIPTS))
        self.assertIn('Image', set(placeholders.values()))
        self.assertIn('Decal', set(placeholders.values()))
        image = self.fallback('Image')
        self.assertEqual(placeholders[image], 'Image')
        self.assertEqual((len(image), struct.unpack('>II', image[16:24])), (1239, (512, 512)))

    def test_the_placeholder_is_refused_by_name(self):
        placeholders = renderer.load_placeholders(str(SCRIPTS))
        encoded = base64.b64encode(self.fallback('Image')).decode()
        with self.assertRaises(renderer.RenderError) as caught:
            renderer.decode_render_output(encoded, 420, placeholders)
        self.assertIn('built-in fallback image of the Image thumbnail script', str(caught.exception))
        # 512x512 passes any size check, so the size rule alone is not enough.
        self.assertIsNotNone(renderer.decode_render_output(encoded, 420, {}))

    def test_a_real_render_still_passes(self):
        encoded = base64.b64encode(make_png(420, 420)).decode()
        self.assertIsNotNone(renderer.decode_render_output(encoded, 420,
                                                           renderer.load_placeholders(str(SCRIPTS))))

    def test_a_missing_script_directory_is_not_fatal(self):
        self.assertEqual(renderer.load_placeholders('/does/not/exist'), {})


class RendererEnvironmentTest(unittest.TestCase):
    """Enabling the renderer must hand the running container the enabled secret."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.compose_env = Path(self.directory.name) / 'compose.env'

    def enable(self, website=None, value='secret-value-0123456789'):
        website = website if website is not None else {'RccAuthorization': value}
        return configure_local_docker.enable_renderer(website, compose_env_path=self.compose_env)

    def test_enabling_records_the_value_for_the_renderer_container(self):
        secret = 'a' * 64
        self.compose_env.write_text('COMPOSE_PROJECT_NAME=marine-local\nLOCAL_UID=1000\n')
        os.chmod(self.compose_env, 0o600)
        self.enable({'RccAuthorization': secret})
        content = self.compose_env.read_text()
        self.assertIn(f'RENDERER_AUTHORIZATION={secret}\n', content)
        self.assertIn('COMPOSE_PROJECT_NAME=marine-local\n', content, 'other values must survive')
        self.assertEqual(oct(os.stat(self.compose_env).st_mode & 0o777), '0o600')
        self.assertEqual(content.count('RENDERER_AUTHORIZATION='), 1)

    def test_enabling_replaces_an_old_value_instead_of_duplicating_it(self):
        self.compose_env.write_text('RENDERER_AUTHORIZATION=' + 'b' * 64 + '\nPOSTGRES_DB=marine_local\n')
        self.enable({'RccAuthorization': 'c' * 64})
        content = self.compose_env.read_text()
        self.assertEqual(content.count('RENDERER_AUTHORIZATION='), 1)
        self.assertIn('RENDERER_AUTHORIZATION=' + 'c' * 64, content)
        self.assertNotIn('b' * 64, content, 'the rotated value must not stay behind')
        self.assertIn('POSTGRES_DB=marine_local', content)

    def test_a_value_that_is_not_env_safe_is_refused(self):
        self.compose_env.write_text('COMPOSE_PROJECT_NAME=marine-local\n')
        for bad in ('short', 'two words', 'quote"d', 'line\nbreak', 'semi;colon'):
            with self.assertRaises(ValueError, msg=bad):
                self.enable({'RccAuthorization': bad})
        self.assertNotIn('RENDERER_AUTHORIZATION', self.compose_env.read_text())

    def test_a_missing_environment_file_is_reported(self):
        with self.assertRaises(ValueError) as caught:
            self.enable({'RccAuthorization': 'd' * 64})
        self.assertIn('start the stack once', str(caught.exception))

    def test_the_website_and_the_renderer_share_one_value(self):
        self.compose_env.write_text('COMPOSE_PROJECT_NAME=marine-local\n')
        website = {'RccAuthorization': 'e' * 64}
        result = self.enable(website)
        self.assertEqual(result['baseUrl'], 'http://renderer:3521/')
        self.assertEqual(website['Render']['Authorization'], 'e' * 64,
                         'the website must send the same value, not a second secret')
        self.assertIn('RENDERER_AUTHORIZATION=' + 'e' * 64, self.compose_env.read_text())

    def test_the_compose_environment_reaches_the_renderer_service(self):
        services = yaml.safe_load((ROOT / 'docker-compose.local.yml').read_text())['services']
        environment = services['renderer']['environment']
        self.assertEqual(environment['RENDERER_AUTHORIZATION'], '${RENDERER_AUTHORIZATION:-}')


class WebsiteThumbnailContractTest(unittest.TestCase):
    """The website behaviour the end-to-end test encodes, checked without Docker.

    `scripts/test_local_renderer.py` cannot run in a plain job: it needs the whole stack.
    These assertions read the website's own code, so a change there fails here with the
    reason instead of turning the integration test into a mysterious timeout.
    """

    def source(self, relative):
        return (ROOT / relative).read_text()

    def test_the_stored_thumbnail_key_is_the_plain_hash(self):
        service = self.source('Roblox/Roblox.Services/Assets/AssetsService.cs')
        method = service.split('private async Task<string> UploadAssetContent')[1].split('public Task DeleteAssetContent')[0]
        self.assertIn('var hash = plainHash;', method)
        self.assertIn('hash += "." + extension;', method)
        self.assertEqual(method.count('return plainHash;'), 2,
                         'both the existing-file and the written-file path return the plain hash')

    def test_asset_thumbnails_are_served_as_the_hash_plus_png(self):
        service = self.source('Roblox/Roblox.Services/Thumbnails.cs')
        self.assertIn('c.imageUrl = "/images/thumbnails/" + c.imageUrl + ".png";', service)

    def test_image_textures_are_approved_and_models_wait_for_staff(self):
        service = self.source('Roblox/Roblox.Services/Assets/AssetsService.cs')
        texture = service.split('private async Task CreateAssetTextureThumbnail')[1].split('private async Task CreatePackageThumbnail')[0]
        self.assertIn('UploadThumbnail(assetId, response, 420, 420, ModerationStatus.ReviewApproved)', texture)
        model = service.split('private async Task CreateModelThumbnail')[1].split('private async Task CreateMeshPartThumbnail')[0]
        self.assertIn('UploadThumbnail(assetId, render, 420, 420, ModerationStatus.AwaitingApproval)', model)

    def test_a_missing_thumbnail_answers_with_this_builds_pending_error(self):
        # While the thumbnail row is missing the batch API reports errorCode 4, which is
        # how this build says "no picture yet": the integration test must wait for the
        # database row instead of reading that answer as a failed render.
        controller = self.source('Roblox/Roblox.Website/Controllers/RobloxApi/Thumbnails.cs')
        self.assertIn('errorCode = 4,', controller)
        self.assertIn('state = ThumbnailState.Error,', controller)
        self.assertIn('c.imageUrl == null ? ThumbnailState.Pending', self.source('Roblox/Roblox.Services/Thumbnails.cs'))

    def test_the_website_posts_to_the_routes_this_renderer_answers(self):
        handler = self.source('Roblox/Roblox.Rendering/RenderingHandler.cs')
        # The website writes the route without a leading slash and sends the body the
        # renderer expects: these six are the ones it answers, the rest stay 501.
        for route in ('image/image', 'catalog/model', 'catalog/hat', 'catalog/mesh',
                      'player/thumbnail', 'player/headshot', 'image/clothing',
                      'game/thumbnail', 'player/thumbnail-3d'):
            self.assertIn('url = "' + route + '"', handler, f'{route} is no longer called')


class ContainerLayoutTest(unittest.TestCase):
    """RCC and the renderer must stay in one container over loopback."""

    def compose(self):
        return yaml.safe_load((ROOT / 'docker-compose.local.yml').read_text())['services']

    def test_rcc_is_not_a_separate_published_service(self):
        services = self.compose()
        self.assertNotIn('rcc-render', services,
                         'RCC binds to its own loopback, so a second container cannot reach it')
        self.assertEqual(sorted(services),
                         ['frontend', 'migrations', 'postgres', 'redis', 'renderer', 'website'])

    def test_the_renderer_service_runs_rcc_itself_behind_its_profile(self):
        renderer_service = self.compose()['renderer']
        self.assertEqual(renderer_service['profiles'], ['renderer'])
        self.assertEqual(renderer_service['build'],
                         {'context': 'docker', 'dockerfile': 'renderer/Dockerfile'})
        self.assertEqual(renderer_service['ports'], ['127.0.0.1:${RENDERER_PORT:-3521}:3521'])
        # Depending on the website makes `restart website` restart the renderer, which
        # cuts renders in flight; the renderer does not need the website to start.
        self.assertNotIn('depends_on', renderer_service)
        environment = renderer_service['environment']
        self.assertEqual(environment['RCC_VERIFY_THUMBNAIL_SCRIPTS'], '1')
        self.assertEqual(environment['WEBSITE_BASE_URL'], 'http://website:5000')
        # RCC is given the trusted name over https, and only this stack answers to it.
        self.assertEqual(environment['RENDERER_ASSET_BASE_URL'], 'https://www.silrev.biz:5000')
        self.assertEqual(environment['RCC_BASE_URL'], 'https://www.silrev.biz:5000')
        self.assertEqual(environment['RENDERER_TLS_UPSTREAM'], 'website:5000')
        self.assertEqual(environment['RENDERER_TLS_HOST'], 'www.silrev.biz')
        self.assertEqual(environment['CURL_CA_BUNDLE'], '/home/rcc/tls/cacert.pem')

    def test_the_trusted_host_name_is_a_local_alias_of_the_renderer(self):
        # RCC connects to the trusted name, so whatever answers it must be inside this
        # stack: the renderer's TLS terminator, which relays to the website.
        aliases = self.compose()['renderer']['networks']['default']['aliases']
        self.assertIn('www.silrev.biz', aliases)
        self.assertIn('assetgame.silrev.biz', aliases)
        self.assertNotIn('silrev.biz', aliases, 'the bare domain is not what RCC is told to use')
        self.assertIsNone(self.compose()['website'].get('networks'),
                          'the website itself does not answer the trusted names')

    def test_the_renderer_refuses_to_start_when_the_trusted_name_is_not_local(self):
        sh = (ROOT / 'docker/renderer/supervise.sh').read_text()
        guard = sh.index('--verify')
        self.assertLess(guard, sh.index('entrypoint.sh &'),
                        'the check must run before RCC is started')
        self.assertIn('asset_tls.py', sh)
        dockerfile = (ROOT / 'docker/renderer/Dockerfile').read_text()
        self.assertIn('renderer/asset_tls.py', dockerfile)
        self.assertIn('openssl', dockerfile, 'the terminator calls openssl for its authority')

    def test_the_renderer_image_builds_the_same_runtime_as_the_wine_probe(self):
        renderer_dockerfile = (ROOT / 'docker/renderer/Dockerfile').read_text()
        wine_dockerfile = (ROOT / 'docker/rcc-wine/Dockerfile').read_text()
        sh = (ROOT / 'docker/renderer/supervise.sh').read_text()
        self.assertIn('COPY rcc-wine/entrypoint.sh', renderer_dockerfile)
        self.assertIn('healthcheck.py', sh)
        self.assertIn('/app/renderer.py', sh)
        for package in ('wine64', 'xvfb'):
            self.assertIn(package, renderer_dockerfile)
            self.assertIn(package, wine_dockerfile)
        self.assertIn('RCC_READY_TIMEOUT', sh)
        self.assertIn("'http://127.0.0.1:64989/'", (ROOT / 'docker/renderer/renderer.py').read_text())

    def test_the_diagnostics_ask_about_the_hosts_this_stack_uses(self):
        # The probe and the integration test are diagnostics, so a drifted default would
        # describe another stack than the one being tested.
        environment = self.compose()['renderer']['environment']
        self.assertEqual(probe_rcc_image.DEFAULT_ASSET_BASE, environment['RENDERER_ASSET_BASE_URL'])
        self.assertEqual(probe_rcc_image.DEFAULT_WEBSITE, environment['WEBSITE_BASE_URL'])
        import test_local_renderer
        self.assertEqual(test_local_renderer.RENDERER_ASSET_BASE, environment['RENDERER_ASSET_BASE_URL'])

    def test_the_website_still_delivers_assets_without_a_renderer(self):
        # The renderer profile is optional: nothing else may depend on it.
        for name, service in self.compose().items():
            if name == 'renderer':
                continue
            self.assertNotIn('renderer', service.get('depends_on') or {}, name)


class RendererRestartToleranceTest(unittest.TestCase):
    """A renderer that is being restarted must be waited for, not misread."""

    def setUp(self):
        os.environ.pop('RENDERER_TEST', None)
        spec = importlib.util.spec_from_file_location('renderer_test_retry',
                                                      ROOT / 'scripts/test_local_renderer.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def flaky_server(self, failures=1, break_status_line=False):
        state = {'seen': 0}

        class Flaky(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get('Content-Length') or 0)
                self.rfile.read(length)
                state['seen'] += 1
                if state['seen'] <= failures:
                    if break_status_line:
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
        threading.Thread(target=server.serve_forever, daemon=True).start()
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
        url, state = self.flaky_server(failures=1, break_status_line=True)
        self.module.RENDERER_URL = url
        status, _ = self.module.renderer_post('/image/image', {'assetId': 1}, attempts=3, pause=0)
        self.assertEqual((status, state['seen']), (200, 2))

    def test_a_real_http_answer_is_never_retried(self):
        url, state = self.flaky_server(failures=0)
        self.module.RENDERER_URL = url
        status, _ = self.module.renderer_post('/image/image', {'assetId': 1}, attempts=3, pause=0)
        self.assertEqual((status, state['seen']), (200, 1))

    def test_an_unreachable_renderer_fails_with_a_clear_message(self):
        self.module.RENDERER_URL = 'http://127.0.0.1:1'
        # This check runs the failure path on purpose, so its workflow annotation is
        # suppressed: a green run must not carry an annotation that reads like a break.
        import test_asset_import
        with mock.patch.object(test_asset_import, 'annotate_failures', False):
            with self.assertRaises(SystemExit) as caught:
                self.module.renderer_post('/image/image', {'assetId': 1}, attempts=2, pause=0)
        self.assertIn('did not answer POST /image/image', str(caught.exception))


class ProbeTest(unittest.TestCase):
    """The RCC image probe reports what the shipped script's calls do."""

    def program(self):
        sent = {}

        def run(program):
            sent['program'] = program
            return 0, 'GetObjects=(true) / 3'

        output = probe_rcc_image.run_probe(9, run=run)
        return sent['program'], output

    def test_the_container_program_runs_every_call_the_script_makes(self):
        program, output = self.program()
        self.assertIn('GetObjects', output)
        self.assertIn("sys.path.insert(0, '/app')", program, 'the probe reuses the renderer client')
        start = program.index('lua = ') + len('lua = ')
        lua = json.loads(program[start:program.index('\n', start)])
        for call in ('SetBaseUrl', 'GetObjects', "ThumbnailGenerator:ClickTexture(url",
                     'ContentProvider:SetBaseUrl'):
            self.assertIn(call, lua, call)
        self.assertNotIn('__PLACEHOLDER__', lua, 'the placeholder length must be substituted')
        self.assertIn('1239', lua, 'the probe names the script placeholder by its length')

    def test_the_matrix_asks_rcc_about_every_url_the_script_could_fetch(self):
        # The shipped script builds its own asset URL from the base URL it is given, so
        # the only way to know which host names RCC accepts is to click all of them.
        matrix = dict((name, url) for name, url in
                      probe_rcc_image.url_matrix(9, 'https://www.silrev.biz:5000', 'http://website:5000'))
        self.assertEqual(matrix['asset_website_http'], 'http://website:5000/asset/?id=9')
        self.assertEqual(matrix['asset_trusted_https_port'], 'https://www.silrev.biz:5000/asset/?id=9')
        self.assertEqual(matrix['asset_trusted_http_port'], 'http://www.silrev.biz:5000/asset/?id=9')
        # RCC upgrades the scheme and then connects to the default port, which is why the
        # renderer has to answer on the port the base URL names.
        self.assertEqual(matrix['asset_trusted_https_default'], 'https://www.silrev.biz/asset/?id=9')
        self.assertEqual(matrix['asset_rbxassetid'], 'rbxassetid://9')
        self.assertEqual(matrix['static_trusted_https_port'], 'https://www.silrev.biz:5000/img/Audio.png')

    def test_the_job_is_the_json_spec_this_build_accepts(self):
        # RCC 2020 rejects a raw Lua chunk with "Attempted to execute with invalid JSON":
        # the script text has to be the JSON job spec, and the Lua has to live in
        # internalscripts/scripts under a name the build does not ship.
        program, _ = self.program()
        self.assertIn("'Mode': 'ExecuteScript'", program)
        self.assertIn("internalscripts', 'scripts'", program)
        self.assertIn("uuid.uuid4().hex[:8]", program, 'the probe script name must be unique')
        self.assertIn('os.unlink(path)', program, 'the probe script must be removed again')
        self.assertIn('CloseJob', program)
        self.assertIn('/asset/?id=9', program, 'the asset URL is passed as a job argument')
        self.assertNotIn('internalscripts/thumbnails', program,
                         'the probe must never write next to the shipped thumbnail scripts')

    def test_the_generated_program_is_valid_python(self):
        # It is sent over stdin, so a typo only fails a whole CI cycle later.
        program, _ = self.program()
        compile(program, '<probe>', 'exec')

    def test_a_probe_that_cannot_run_is_reported_not_raised(self):
        self.assertIn('could not run', probe_rcc_image.run_probe(9, run=lambda program: (1, 'no such service')))


class RendererAssetBaseTest(unittest.TestCase):
    """RCC only trusts the host names compiled into it, so its URLs use the asset base."""

    def service(self):
        return renderer.Renderer(rcc=FakeRcc(), website=FakeWebsite(), placeholders={})

    def test_rcc_is_given_the_trusted_host_and_the_renderer_keeps_the_service_url(self):
        with mock.patch.dict(os.environ, {'RENDERER_ASSET_BASE_URL': 'http://www.silrev.biz/',
                                          'WEBSITE_BASE_URL': 'http://website:5000'}):
            service = self.service()
        # The renderer's own calls (the appearance document) keep the service URL: no
        # trust check is involved in them, and the service name always resolves.
        self.assertEqual(service.base_url, service.website.base_url)
        self.assertEqual(service.asset_base_url, 'http://www.silrev.biz')
        self.assertEqual(service.asset_url(7), 'http://www.silrev.biz/asset/?id=7')
        for arguments in (service.arguments_image({'assetId': 7})[1],
                          service.arguments_model({'assetId': 7})[1],
                          service.arguments_hat({'assetId': 7})[1],
                          service.arguments_mesh({'assetId': 7})[1]):
            self.assertIn('http://www.silrev.biz', arguments)
            self.assertNotIn('http://website:5000', arguments)
        # The avatar scripts fetch the appearance document themselves, so that URL goes
        # to RCC as well and must use the trusted name too.
        self.assertEqual(service.appearance_url(3),
                         'http://www.silrev.biz/v1.1/avatar-fetch?userId=3')
        avatar_script, avatar_arguments = service.arguments_avatar({'userId': 3})
        # Avatar_R15_Action.lua declares baseUrl, characterAppearanceUrl, fileExtension, x, y.
        self.assertEqual(avatar_script, 'Avatar_R15_Action')
        self.assertEqual(avatar_arguments[0], 'http://www.silrev.biz')
        self.assertEqual(avatar_arguments[1], 'http://www.silrev.biz/v1.1/avatar-fetch?userId=3')
        self.assertEqual(service.arguments_headshot({'userId': 3})[1][0], 'http://www.silrev.biz')

    def test_without_the_override_rcc_gets_the_website_service(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('RENDERER_ASSET_BASE_URL', None)
            service = self.service()
        self.assertEqual(service.asset_base_url, service.base_url)


class AssetTlsTest(unittest.TestCase):
    """The trusted asset host is served by this stack over TLS, never by another host."""

    @classmethod
    def setUpClass(cls):
        if shutil.which('openssl') is None:
            raise unittest.SkipTest('openssl is needed to generate the local authority')

    def authority(self, names=('www.silrev.biz',)):
        directory = tempfile.mkdtemp(prefix='asset-tls-')
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        return asset_tls.create_authority(Path(directory) / 'tls', names=names)

    def test_the_certificate_covers_the_names_rcc_is_told_to_use(self):
        authority = self.authority(names=('www.silrev.biz', 'assetgame.silrev.biz'))
        details = asset_tls.openssl('x509', '-in', str(authority['server_certificate']), '-noout', '-text')
        self.assertIn('DNS:www.silrev.biz', details)
        self.assertIn('DNS:assetgame.silrev.biz', details)
        self.assertIn('DNS:localhost', details, 'the guard needs a name it can always reach')

    def test_the_local_authority_is_kept_out_of_the_shipped_build(self):
        # Everything the terminator writes lives in the container's tmpfs; the mounted
        # build (RCCService/) is read-only and must stay byte-for-byte as shipped.
        authority = self.authority()
        for path in authority.values():
            if hasattr(path, 'exists'):
                self.assertNotIn(str(ROOT / 'RCCService'), str(path))

    def test_the_bundle_rcc_verifies_with_carries_the_local_authority(self):
        authority = self.authority()
        directory = Path(tempfile.mkdtemp(prefix='asset-bundle-'))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        shipped = directory / 'shipped.pem'
        shipped.write_text('shipped authority\n')
        bundle = asset_tls.local_bundle(authority['ca_certificate'], directory / 'cacert.pem', shipped)
        text = bundle.read_text()
        self.assertIn('shipped authority', text, 'the build\'s own authorities must survive')
        self.assertIn(asset_tls.CA_MARKER, text)
        self.assertIn(Path(authority['ca_certificate']).read_text().strip().splitlines()[0], text)
        again = asset_tls.local_bundle(authority['ca_certificate'], bundle, shipped)
        self.assertEqual(again, bundle)
        self.assertEqual(bundle.read_text().count(asset_tls.CA_MARKER), 1,
                         'a second call must not append the authority twice')

    def test_the_guard_refuses_a_host_that_does_not_answer_with_our_certificate(self):
        authority = self.authority()
        other = self.authority()
        upstream = self.serve_upstream()
        server = asset_tls.serve(address='127.0.0.1', port=0, upstream=upstream, authority=authority)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        port = server.server_address[1]
        ok, message = asset_tls.verify(host='localhost', port=port, authority=authority)
        self.assertTrue(ok, message)
        refused, refusal = asset_tls.verify(host='localhost', port=port, authority=other)
        self.assertFalse(refused, refusal)
        self.assertIn('not from elsewhere', refusal)

    def test_the_guard_refuses_a_name_nobody_answers(self):
        authority = self.authority()
        ok, message = asset_tls.verify(host='localhost', port=1, authority=authority)
        self.assertFalse(ok, message)
        self.assertIn('not from elsewhere', message)

    def test_the_terminator_relays_the_website_to_rcc_over_tls(self):
        authority = self.authority()
        upstream = self.serve_upstream()
        server = asset_tls.serve(address='127.0.0.1', port=0, upstream=upstream, authority=authority)
        self.addCleanup(server.server_close)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        context = ssl.create_default_context(cafile=str(authority['ca_certificate']))
        request = urllib.request.Request(f'https://localhost:{server.server_address[1]}/asset/?id=7')
        with urllib.request.urlopen(request, context=context, timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers['Content-Type'], 'image/png')
            self.assertEqual(response.read(), b'PNG:/asset/?id=7')
        # A website the renderer cannot reach is reported, never faked.
        broken = asset_tls.serve(address='127.0.0.1', port=0, upstream='127.0.0.1:1', authority=authority)
        self.addCleanup(broken.server_close)
        threading.Thread(target=broken.serve_forever, daemon=True).start()
        self.addCleanup(broken.shutdown)
        with self.assertRaises(urllib.error.HTTPError) as failure:
            urllib.request.urlopen(f'https://localhost:{broken.server_address[1]}/asset/?id=7',
                                   context=context, timeout=10)
        self.assertEqual(failure.exception.code, 502)

    def serve_upstream(self):
        """A stand-in website: answers the path it was asked for."""
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'PNG:' + self.path.encode()
                self.send_response(200)
                self.send_header('Content-Type', 'image/png')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *arguments):
                pass

        upstream = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        self.addCleanup(upstream.shutdown)
        self.addCleanup(upstream.server_close)
        return f'127.0.0.1:{upstream.server_address[1]}'


class NoticeBudgetTest(unittest.TestCase):
    """The renderer check reports through GitHub notices, and GitHub drops the ones past ten.

    The lines that explain an avatar render which stayed pending are emitted last, so they
    are the first to disappear: the check has to stay inside the budget, say in the log
    when it cannot, and keep the diagnosis in the job summary as well.
    """

    def setUp(self):
        os.environ.pop('RENDERER_TEST', None)
        spec = importlib.util.spec_from_file_location('local_renderer_notices',
                                                      ROOT / 'scripts/test_local_renderer.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_the_budget_is_what_github_publishes(self):
        import test_asset_import
        self.assertEqual(test_asset_import.MAX_NOTICE_ANNOTATIONS, 10)

    def test_a_multi_line_notice_survives_as_one_annotation(self):
        # The avatar diagnosis is four lines long: a raw newline would end the workflow
        # command and the annotation would show only the first line.
        import test_asset_import
        self.addCleanup(setattr, test_asset_import, 'notice_annotations',
                        test_asset_import.notice_annotations)
        test_asset_import.notice_annotations = 0
        printed = io.StringIO()
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}), \
                contextlib.redirect_stdout(printed):
            test_asset_import.notice('first line\nsecond line 100%')
        commands = [line for line in printed.getvalue().splitlines()
                    if line.startswith('::notice title=Asset import check::')]
        self.assertEqual(len(commands), 1)
        self.assertIn('first line%0Asecond line 100%25', commands[0])
        self.assertIn('first line\nsecond line 100%', printed.getvalue().split('::notice')[0])

    def test_a_notice_past_the_budget_is_still_written_with_the_reason(self):
        import test_asset_import
        self.addCleanup(setattr, test_asset_import, 'notice_annotations',
                        test_asset_import.notice_annotations)
        test_asset_import.notice_annotations = 0
        printed = io.StringIO()
        with mock.patch.dict(os.environ, {'GITHUB_ACTIONS': 'true'}), \
                contextlib.redirect_stdout(printed):
            for index in range(test_asset_import.MAX_NOTICE_ANNOTATIONS + 1):
                test_asset_import.notice(f'probe {index}')
        text = printed.getvalue()
        self.assertEqual(text.count('::notice title=Asset import check::'), 10)
        self.assertIn('probe 10', text)
        self.assertIn('publishes only the first 10', text)

    def test_the_run_cannot_spend_more_notices_than_the_budget(self):
        source = (ROOT / 'scripts/test_local_renderer.py').read_text()
        body = source[source.index('def main():'):]
        # A call site inside a loop would emit once per pass, and the check has no such
        # loop, so the number of call sites is the number of notices a run can produce.
        call_sites = [line.strip() for line in body.splitlines()
                      if 'notice(' in line and not line.strip().startswith('#')]
        self.assertLessEqual(len(call_sites), 10,
                             'a run must not emit more notices than GitHub publishes: '
                             + repr(call_sites))

    def test_the_avatar_diagnosis_is_one_notice_and_reaches_the_summary(self):
        source = (ROOT / 'scripts/test_local_renderer.py').read_text()
        branch = source[source.index('elif status in (501, 502):'):]
        branch = branch[:branch.index('\n        else:')]
        self.assertEqual(branch.count('notice('), 1)
        self.assertIn('job_summary(', branch)

    def test_the_summary_helper_appends_and_never_raises(self):
        with mock.patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': ''}, clear=False):
            self.module.job_summary('nowhere to write this')  # no summary: a silent no-op
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'summary.md'
            with mock.patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': str(path)}):
                self.module.job_summary('the body\n')
            self.assertEqual(path.read_text(), 'the body\n')
            with mock.patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': directory}):
                self.module.job_summary('writing to a directory must not raise')


class PixelReaderTest(unittest.TestCase):
    """The integration test judges pixels, so its reader has to be exact."""

    def setUp(self):
        spec = importlib.util.spec_from_file_location('local_renderer_pixels',
                                                      ROOT / 'scripts/test_local_renderer.py')
        self.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.module)

    def test_a_two_colour_png_reads_back_pixel_exact(self):
        width, height, rows = self.module.read_png(make_png(8, 8))
        self.assertEqual((width, height), (8, 8))
        self.assertEqual(self.module.colour_at(rows, width, 0, 0), (255, 0, 0))
        self.assertEqual(self.module.colour_at(rows, width, width - 1, height - 1), (0, 0, 255))
        self.assertTrue(self.module.is_colourish((250, 5, 5), (255, 0, 0)))

    def test_a_solid_png_is_not_confused_with_the_other_colour(self):
        width, height, rows = self.module.read_png(make_png(8, 8, left=(10, 200, 10), right=(10, 200, 10)))
        self.assertFalse(self.module.is_colourish(self.module.colour_at(rows, width, 4, 4), (220, 40, 40)))


if __name__ == '__main__':
    unittest.main()
