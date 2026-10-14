#!/usr/bin/env python3
"""Unit tests for the catalog importer tool."""

import io
import os
import sys
import unittest
import zlib
import struct

sys.path.insert(0, os.path.dirname(__file__))
from import_catalog_assets import make_png_template, encode_multipart, FormTokenParser, POPULAR_CLASSICS


class CatalogImporterTests(unittest.TestCase):
    def test_png_template_dimensions_and_signature(self):
        png = make_png_template(585, 559, (10, 20, 30, 255), "solid")
        self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
        # Parse IHDR
        ihdr_offset = 12  # 8 bytes magic + 4 bytes length
        self.assertEqual(png[ihdr_offset:ihdr_offset + 4], b"IHDR")
        width, height, bit_depth, color_type = struct.unpack(">IIBB", png[ihdr_offset + 4:ihdr_offset + 14])
        self.assertEqual(width, 585)
        self.assertEqual(height, 559)
        self.assertEqual(bit_depth, 8)
        self.assertEqual(color_type, 6)  # RGBA
        self.assertTrue(png.endswith(b"IEND\xaeB`\x82"))

    def test_png_patterns(self):
        for pattern in ("solid", "plaid", "denim", "hoodie"):
            png = make_png_template(585, 559, (40, 50, 60, 255), pattern)
            self.assertGreater(len(png), 1000)

    def test_multipart_encoder(self):
        fields = {"name": "Test Shirt", "assetTypeId": "Shirt", "price": "10"}
        files = [("file", "test.png", b"\x89PNGfake")]
        content_type, body = encode_multipart(fields, files)
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertIn(b'name="name"\r\n\r\nTest Shirt\r\n', body)
        self.assertIn(b'name="file"; filename="test.png"', body)
        self.assertIn(b"\x89PNGfake\r\n", body)

    def test_form_token_parser(self):
        html_doc = """
        <html>
            <form action="/auth/login" method="post">
                <input type="hidden" name="__RequestVerificationToken" value="secret-token-xyz-123" />
                <input type="text" name="username" />
            </form>
        </html>
        """
        parser = FormTokenParser()
        parser.feed(html_doc)
        self.assertEqual(parser.token, "secret-token-xyz-123")

    def test_popular_classics_structure(self):
        self.assertGreater(len(POPULAR_CLASSICS), 5)
        for item in POPULAR_CLASSICS:
            self.assertIn("id", item)
            self.assertIsInstance(item["id"], int)
            self.assertIn("name", item)
            self.assertIn("type", item)


if __name__ == "__main__":
    unittest.main()
