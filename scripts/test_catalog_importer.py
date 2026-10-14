#!/usr/bin/env python3
"""Unit tests for the catalog importer tool."""

import email.message
import io
import os
import sys
import unittest
import urllib.error
import zlib
import struct

sys.path.insert(0, os.path.dirname(__file__))
from import_catalog_assets import (
    make_png_template,
    encode_multipart,
    FormTokenParser,
    CatalogClient,
    POPULAR_CLASSICS,
)


class MockResponse:
    def __init__(self, status=200, headers=None, body=b""):
        self.status = status
        self.code = status
        msg = email.message.Message()
        for k, v in (headers or {}).items():
            if isinstance(v, list):
                for item in v:
                    msg.add_header(k, item)
            else:
                msg.add_header(k, v)
        self.headers = msg
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return self._body


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

    def test_direct_cookie_initialization(self):
        client = CatalogClient(session_cookie="pre_existing_cookie_value")
        self.assertEqual(client.session_cookie, "pre_existing_cookie_value")

    def test_popular_classics_structure(self):
        self.assertGreater(len(POPULAR_CLASSICS), 5)
        for item in POPULAR_CLASSICS:
            self.assertIn("id", item)
            self.assertIsInstance(item["id"], int)
            self.assertIn("name", item)
            self.assertIn("type", item)

    def test_login_mobileapi_success(self):
        client = CatalogClient(base_url="http://localhost:5000")

        def mock_open(req, timeout=30):
            url = req.full_url
            if "/mobileapi/login" in url:
                return MockResponse(
                    status=200,
                    headers={"Set-Cookie": ".PEKOSECURITY=mocked_jwt_token_12345; path=/"},
                    body=b'{"Status":"OK","UserInfo":{"UserName":"test2","UserID":2}}',
                )
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        client.opener.open = mock_open
        success = client.login("test2", "qsx12345")
        self.assertTrue(success)
        self.assertEqual(client.session_cookie, "mocked_jwt_token_12345")

    def test_login_fallback_accountlogin(self):
        client = CatalogClient(base_url="http://localhost:5000")

        def mock_open(req, timeout=30):
            url = req.full_url
            if "/mobileapi/login" in url or "/v1/login" in url:
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
            if "/auth/accountlogin" in url:
                if req.data is None:  # GET form
                    html = (
                        '<html><form action="/auth/accountlogin" method="post">'
                        '<input type="hidden" name="__RequestVerificationToken" value="csrf-token-abc" />'
                        '</form></html>'
                    )
                    return MockResponse(status=200, body=html.encode())
                else:  # POST form
                    # Successful login sets cookie
                    err = urllib.error.HTTPError(
                        url, 302, "Found",
                        {"Set-Cookie": ".PEKOSECURITY=razor_token_999; path=/"},
                        io.BytesIO(b"")
                    )
                    raise err
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        client.opener.open = mock_open
        success = client.login("test2", "qsx12345")
        self.assertTrue(success)
        self.assertEqual(client.session_cookie, "razor_token_999")

    def test_login_invalid_credentials_rejected(self):
        client = CatalogClient(base_url="http://localhost:5000")

        def mock_open(req, timeout=30):
            url = req.full_url
            if "/mobileapi/login" in url:
                err = urllib.error.HTTPError(
                    url, 403, "Forbidden",
                    {},
                    io.BytesIO(b'{"message":"Incorrect username or password. Please try again."}')
                )
                raise err
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        client.opener.open = mock_open
        with self.assertRaises(RuntimeError) as ctx:
            client.login("test2", "wrong_password")
        self.assertIn("Incorrect username or password", str(ctx.exception))

    def test_login_all_endpoints_404_raises(self):
        client = CatalogClient(base_url="http://localhost:5000")

        def mock_open(req, timeout=30):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b""))

        client.opener.open = mock_open
        with self.assertRaises(RuntimeError) as ctx:
            client.login("test2", "password")
        self.assertIn("Could not authenticate", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
