"""Contract and cleanup tests for the thumbnail probe; real rendering needs the Wine CI job.

A substitute RPC proves the decision logic only: what counts as a decodable
image, what counts as a built-in placeholder, and that a temporary render script
never survives a job, successful or not.
"""

import base64
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zlib

PROBES = Path(__file__).resolve().parents[1] / 'docker/rcc-wine'
sys.path.insert(0, str(PROBES))
import thumbnail_probe


def make_png(width, height, colour=(200, 30, 30)):
    def chunk(kind, data):
        return (struct.pack('>I', len(data)) + kind + data
                + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF))

    raw = b''.join(b'\x00' + bytes(colour) * width for _ in range(height))
    return (b'\x89PNG\r\n\x1a\n'
            + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 9))
            + chunk(b'IEND', b''))


def make_jpeg(width, height):
    # SOI, APP0, SOF0 with real dimensions, EOI: enough for header parsing.
    app0 = b'\xff\xe0' + struct.pack('>H', 16) + b'JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
    sof0 = b'\xff\xc0' + struct.pack('>H', 17) + b'\x08' + struct.pack('>HH', height, width) + b'\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01'
    return b'\xff\xd8' + app0 + sof0 + b'\xff\xd9'


class ImageDetailsTest(unittest.TestCase):
    def test_a_png_reports_its_container_and_dimensions(self):
        self.assertEqual(thumbnail_probe.image_details(make_png(420, 420)), ('PNG', 420, 420))

    def test_a_jpeg_reports_its_container_and_dimensions(self):
        self.assertEqual(thumbnail_probe.image_details(make_jpeg(150, 75)), ('JPEG', 150, 75))

    def test_text_and_truncated_data_are_not_images(self):
        for value in (b'', b'not an image', b'\x89PNG\r\n\x1a\n' + b'\x00' * 40):
            self.assertIsNone(thumbnail_probe.image_details(value))

    def test_only_decodable_base64_is_accepted_as_an_image(self):
        self.assertIsNone(thumbnail_probe.decode_image_text('short'))
        self.assertIsNone(thumbnail_probe.decode_image_text('! not base64 !' * 20))
        self.assertIsNone(thumbnail_probe.decode_image_text(base64.b64encode(b'plain text' * 40).decode()))
        self.assertEqual(thumbnail_probe.decode_image_text(base64.b64encode(make_png(120, 120)).decode()),
                         make_png(120, 120))


class ReplyTest(unittest.TestCase):
    def test_an_image_is_found_inside_a_soap_reply(self):
        reply = ET.Element('OpenJobExResponse')
        ET.SubElement(reply, 'value').text = base64.b64encode(make_png(200, 200)).decode()
        data, source = thumbnail_probe.image_from_reply(reply)
        self.assertEqual(thumbnail_probe.image_details(data), ('PNG', 200, 200))
        self.assertEqual(source, 'value')

    def test_a_reply_without_an_image_is_reported_as_none(self):
        reply = ET.Element('OpenJobExResponse')
        ET.SubElement(reply, 'value').text = 'job finished'
        self.assertEqual(thumbnail_probe.image_from_reply(reply), (None, None))


class ValidateTest(unittest.TestCase):
    def test_the_built_in_hourglass_placeholder_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, 'built-in placeholder'):
            thumbnail_probe.validate(('JPEG', 1, 1), 'the render')

    def test_a_real_render_size_passes(self):
        thumbnail_probe.validate(('PNG', 420, 420), 'the render')


class FakeRpc:
    """Substitute SOAP endpoint that returns a per-variant PNG instead of RCC."""

    def __init__(self, runtime, images=None, identical=False, faults=None):
        self.runtime = Path(runtime)
        self.images = images or {}
        self.identical = identical
        self.faults = dict(faults or {})
        self.attempted = []

    def __call__(self, method, children):
        if method == 'CloseJob':
            return ET.Element('CloseJobResponse')
        payload = json.loads(children[1].findtext('script'))
        script_type = payload['Settings']['Type']
        assert script_type.startswith('WineThumbnail'), 'a temporary render type is required'
        matches = list(self.runtime.rglob(script_type + '.lua'))
        assert len(matches) == 1, 'the render script must exist while its job runs'
        directory = matches[0].parent.name
        self.attempted.append(directory)
        if directory in self.faults:
            raise RuntimeError(self.faults.pop(directory))
        variant = payload['Settings']['Arguments'][3]
        if variant in self.images:
            image = self.images[variant]
        elif self.identical:
            image = make_png(420, 420, (10, 10, 10))
        else:
            colour = (200, 30, 30) if variant == 'scene' else (10, 10, 10)
            image = make_png(420, 420, colour)
        reply = ET.Element('OpenJobExResponse')
        ET.SubElement(reply, 'value').text = base64.b64encode(image).decode()
        return reply


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='thumbnail probe test ')
        self.addCleanup(self.directory.cleanup)
        self.runtime = Path(self.directory.name)

    def leftover_scripts(self):
        return list(self.runtime.rglob('*.lua'))

    def test_two_different_scenes_are_required_to_prove_a_render(self):
        rpc = FakeRpc(self.runtime)
        with patch.object(thumbnail_probe, 'call', side_effect=rpc):
            summary = thumbnail_probe.probe(self.runtime)
        self.assertEqual(summary['mode'], 'Thumbnail')
        self.assertEqual(summary['scriptDirectory'], 'internalscripts/thumbnails')
        self.assertEqual((summary['format'], summary['width'], summary['height']), ('PNG', 420, 420))
        self.assertTrue(summary['sceneMatchesRequestedType'])
        self.assertEqual(self.leftover_scripts(), [], 'the render script must be removed')

    def test_identical_bytes_are_rejected_as_a_placeholder(self):
        rpc = FakeRpc(self.runtime, identical=True)
        with patch.object(thumbnail_probe, 'call', side_effect=rpc):
            with self.assertRaisesRegex(RuntimeError, 'no thumbnail render was established'):
                thumbnail_probe.probe(self.runtime)
        self.assertEqual(self.leftover_scripts(), [])

    def test_the_placeholder_reply_is_reported_and_leaves_no_script(self):
        rpc = FakeRpc(self.runtime, images={'scene': make_jpeg(1, 1), 'empty': make_jpeg(1, 1)})
        with patch.object(thumbnail_probe, 'call', side_effect=rpc):
            with self.assertRaisesRegex(RuntimeError, 'built-in placeholder'):
                thumbnail_probe.probe(self.runtime)
        self.assertEqual(self.leftover_scripts(), [])

    def test_a_soap_fault_moves_to_the_next_attempt(self):
        rpc = FakeRpc(self.runtime, faults={'thumbnails': 'RCC SOAP fault: Failed to open script file'})
        with patch.object(thumbnail_probe, 'call', side_effect=rpc):
            summary = thumbnail_probe.probe(self.runtime)
        self.assertEqual(rpc.attempted[0], 'thumbnails')
        self.assertEqual((summary['mode'], summary['scriptDirectory']),
                         ('Thumbnail', 'internalscripts/scripts'))
        self.assertEqual(self.leftover_scripts(), [])

    def test_a_failure_reports_every_attempt(self):
        with patch.object(thumbnail_probe, 'call',
                          side_effect=RuntimeError('RCC SOAP fault: rejected')):
            with self.assertRaisesRegex(RuntimeError, 'no thumbnail render was established'):
                thumbnail_probe.probe(self.runtime)
        self.assertEqual(self.leftover_scripts(), [])


if __name__ == '__main__':
    unittest.main()
