#!/usr/bin/env python3
"""
Catalog asset import and seeding tool for Marine.

Can seed starter clothing (fully offline using valid 585x559 templates)
or import classic assets from Roblox (hats, hair, faces, clothing, accessories).

Usage examples:
    # Seed free starter clothing pack (offline, no external connection required):
    python3 scripts/import_catalog_assets.py --starter-pack --username test --password <password>

    # Or if you already have your session cookie from the browser:
    python3 scripts/import_catalog_assets.py --starter-pack --cookie <PEKOSECURITY_COOKIE>

    # Import a specific Roblox item by ID (e.g. Classic Baseball Cap: 1028856):
    python3 scripts/import_catalog_assets.py --roblox-id 1028856 --cookie <COOKIE>

    # Import popular classic starter items:
    python3 scripts/import_catalog_assets.py --popular --cookie <COOKIE>

    # List recommended classic items:
    python3 scripts/import_catalog_assets.py --list-popular
"""

import argparse
import html.parser
import json
import re
import struct
import sys
import urllib.error
import urllib.parse
import urllib.request
import zlib

POPULAR_CLASSICS = [
    {"id": 1028856, "name": "Classic Baseball Cap", "type": "Hat"},
    {"id": 1079419, "name": "Blue Baseball Cap", "type": "Hat"},
    {"id": 11999247, "name": "Classic Fedora", "type": "Hat"},
    {"id": 1031429, "name": "Top Hat", "type": "Hat"},
    {"id": 1365551, "name": "Black Ponytail", "type": "HairAccessory"},
    {"id": 62234425, "name": "Brown Hair", "type": "HairAccessory"},
    {"id": 376527350, "name": "Chestnut Bun", "type": "HairAccessory"},
    {"id": 376526888, "name": "Brown Charmer Hair", "type": "HairAccessory"},
    {"id": 7699174, "name": "Chill Face", "type": "Face"},
    {"id": 86487700, "name": "Smile", "type": "Face"},
]


class FormTokenParser(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "input":
            return
        attributes = dict(attrs)
        if attributes.get("name") == "__RequestVerificationToken":
            self.token = attributes.get("value")


def encode_multipart(fields, files):
    boundary = "----marine-catalog-seed-" + zlib.crc32(str(fields).encode()).to_bytes(4, "big").hex()
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, filename, content in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                     f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode())
        parts.append(content)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return "multipart/form-data; boundary=" + boundary, b"".join(parts)


def make_png_template(width=585, height=559, base_rgba=(40, 60, 100, 255), pattern="solid"):
    """
    Generates a valid 32-bit RGBA PNG image with the exact dimensions
    required by Roblox clothing validation (585x559).
    """
    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    # Pre-render scanlines
    scanlines = bytearray()
    r, g, b, a = base_rgba

    for y in range(height):
        scanlines.append(0)  # filter type None
        for x in range(width):
            if pattern == "plaid":
                stripe = ((x // 16) % 2) ^ ((y // 16) % 2)
                pr = min(255, int(r * (1.3 if stripe else 0.7)))
                pg = min(255, int(g * (1.3 if stripe else 0.7)))
                pb = min(255, int(b * (1.3 if stripe else 0.7)))
                scanlines.extend((pr, pg, pb, a))
            elif pattern == "denim":
                noise = ((x * 17 + y * 31) % 23) - 11
                scanlines.extend((
                    max(0, min(255, r + noise)),
                    max(0, min(255, g + noise)),
                    max(0, min(255, b + noise * 2)),
                    a,
                ))
            elif pattern == "hoodie":
                # Shading with pocket / zipper center line
                is_center = 280 <= x <= 305 and 150 <= y <= 350
                is_pocket = 240 <= x <= 345 and 270 <= y <= 360
                factor = 0.6 if is_center else (0.85 if is_pocket else 1.0)
                scanlines.extend((int(r * factor), int(g * factor), int(b * factor), a))
            else:
                scanlines.extend((r, g, b, a))

    raw = bytes(scanlines)
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b""))


class CatalogClient:
    def __init__(self, base_url="http://localhost:5000", session_cookie=None):
        self.base_url = base_url.rstrip("/")
        self.session_cookie = session_cookie
        self.csrf_token = None

    def request(self, method, path, data=None, headers=None):
        url = urllib.parse.urljoin(self.base_url, path)
        req_headers = {
            "User-Agent": "Marine-Catalog-Importer/1.0",
        }
        if self.session_cookie:
            req_headers["Cookie"] = f".PEKOSECURITY={self.session_cookie}"
        if self.csrf_token:
            req_headers["x-csrf-token"] = self.csrf_token
        if headers:
            req_headers.update(headers)

        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                token = resp.headers.get("x-csrf-token")
                if token:
                    self.csrf_token = token
                content = resp.read()
                return resp.status, dict(resp.headers), content
        except urllib.error.HTTPError as err:
            token = err.headers.get("x-csrf-token")
            if token:
                self.csrf_token = token
            body = err.read()
            return err.code, dict(err.headers), body

    def login(self, username, password):
        print(f"[*] Logging in as '{username}' on {self.base_url}...")
        status, _, body = self.request("GET", "/auth/login")
        if status != 200:
            raise RuntimeError(f"Failed to fetch login page (HTTP {status})")

        parser = FormTokenParser()
        parser.feed(body.decode("utf-8", "replace"))
        if not parser.token:
            raise RuntimeError("Antiforgery token not found on login page")

        payload = urllib.parse.urlencode({
            "username": username,
            "password": password,
            "__RequestVerificationToken": parser.token,
        }).encode("utf-8")

        # Disable auto-follow to capture Set-Cookie
        req = urllib.request.Request(
            f"{self.base_url}/auth/login",
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "Marine-Catalog-Importer/1.0",
            },
            method="POST",
        )
        try:
            urllib.request.urlopen(req, timeout=15)
        except urllib.error.HTTPError as err:
            cookie_header = err.headers.get("Set-Cookie", "")
            match = re.search(r"\.PEKOSECURITY=([^;]+)", cookie_header)
            if match:
                self.session_cookie = match.group(1)
                print(f"[+] Login successful! Session acquired.")
                return True
            raise RuntimeError(f"Login failed (HTTP {err.code}): {err.read()[:200].decode('utf-8', 'replace')}")
        except Exception as ex:
            raise RuntimeError(f"Unexpected login error: {ex}")

        raise RuntimeError("Login failed: no session cookie received")

    def create_clothing(self, name, description, clothing_type, png_bytes, price=0):
        print(f"[*] Uploading clothing '{name}' ({clothing_type}, {len(png_bytes)} bytes)...")
        fields = {
            "name": name,
            "description": description,
            "assetTypeId": clothing_type,
            "price": str(price),
            "isForSale": "true",
            "genre": "All",
        }
        files = [("file", f"{clothing_type.lower()}.png", png_bytes)]
        content_type, body = encode_multipart(fields, files)
        status, _, resp_body = self.request(
            "POST",
            "/admin-api/api/asset/create/clothing",
            data=body,
            headers={"Content-Type": content_type},
        )
        if status != 200:
            raise RuntimeError(f"Failed to create clothing '{name}' (HTTP {status}): {resp_body[:300].decode('utf-8', 'replace')}")

        data = json.loads(resp_body)
        asset_id = data.get("assetId")
        print(f"[+] Successfully created {clothing_type} '{name}' -> Asset ID: {asset_id}")
        return asset_id

    def copy_from_roblox(self, roblox_asset_id):
        print(f"[*] Requesting copy of Roblox asset #{roblox_asset_id}...")
        # Try both the asset copy and clothing copy endpoints
        body = json.dumps({"assetId": roblox_asset_id, "force": False}).encode("utf-8")
        status, _, resp_body = self.request(
            "POST",
            "/admin-api/api/asset/copy-from-roblox",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        if status == 200:
            data = json.loads(resp_body)
            asset_id = data.get("assetId")
            print(f"[+] Successfully copied asset #{roblox_asset_id} -> Local Asset ID: {asset_id}")
            return asset_id

        # Fallback to alternate endpoint if it's clothing / bundle
        body = json.dumps({"url": str(roblox_asset_id)}).encode("utf-8")
        status2, _, resp_body2 = self.request(
            "POST",
            "/admin-api/api/asset/create/from-roblox",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        if status2 == 200:
            data = json.loads(resp_body2)
            asset_id = data.get("assetId")
            print(f"[+] Successfully migrated item #{roblox_asset_id} -> Local Asset ID: {asset_id}")
            return asset_id

        raise RuntimeError(
            f"Failed to copy Roblox asset #{roblox_asset_id} (HTTP {status} / {status2}): "
            f"{resp_body[:200].decode('utf-8', 'replace')}"
        )


def seed_starter_pack(client, price=0):
    print("=" * 60)
    print("Seeding Starter Clothing Pack to Catalog")
    print("=" * 60)

    items = [
        {
            "name": "Classic Marine Hoodie",
            "desc": "Official Marine dark navy and orange hoodie. Warm, stylish and classic.",
            "type": "Shirt",
            "rgba": (28, 48, 85, 255),
            "pattern": "hoodie",
        },
        {
            "name": "Classic Red Plaid Shirt",
            "desc": "Timeless red and black checkered flannel shirt for everyday adventures.",
            "type": "Shirt",
            "rgba": (180, 35, 35, 255),
            "pattern": "plaid",
        },
        {
            "name": "Classic Denim Jeans",
            "desc": "Durable blue denim jeans that match any outfit.",
            "type": "Pants",
            "rgba": (45, 75, 120, 255),
            "pattern": "denim",
        },
        {
            "name": "Classic Black Slacks",
            "desc": "Clean and sharp black trousers suitable for both casual and formal occasions.",
            "type": "Pants",
            "rgba": (25, 25, 30, 255),
            "pattern": "solid",
        },
        {
            "name": "Classic Marine Logo Tee",
            "desc": "Classic white t-shirt featuring the orange Marine logo.",
            "type": "TeeShirt",
            "rgba": (240, 240, 245, 255),
            "pattern": "solid",
        },
    ]

    created = []
    for item in items:
        png = make_png_template(585, 559, item["rgba"], item["pattern"])
        aid = client.create_clothing(item["name"], item["desc"], item["type"], png, price=price)
        created.append((aid, item["name"]))

    print("=" * 60)
    print(f"[+] Done! Successfully seeded {len(created)} items to the catalog:")
    for aid, name in created:
        print(f"    - ID {aid}: {name} (http://localhost:3000/catalog/{aid}/--)")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="Marine Catalog Importer & Seeder")
    parser.add_argument("--backend", default="http://localhost:5000", help="Base backend URL (default: http://localhost:5000)")
    parser.add_argument("--cookie", help="Existing .PEKOSECURITY cookie value")
    parser.add_argument("--username", help="Staff/Owner username to login")
    parser.add_argument("--password", help="Staff/Owner password to login")
    parser.add_argument("--starter-pack", action="store_true", help="Seed 5 classic starter clothing items (works offline)")
    parser.add_argument("--roblox-id", type=int, help="Import a single Roblox asset by its ID")
    parser.add_argument("--roblox-ids", help="Comma-separated Roblox asset IDs to import")
    parser.add_argument("--popular", action="store_true", help="Import the curated list of popular classic Roblox items")
    parser.add_argument("--list-popular", action="store_true", help="Print the curated list of popular classic items")
    parser.add_argument("--price", type=int, default=0, help="Price in Robux for created items (default: 0 / Free)")

    args = parser.parse_args()

    if args.list_popular:
        print("Curated classic Roblox catalog items:")
        for item in POPULAR_CLASSICS:
            print(f"  - ID {item['id']}: {item['name']} ({item['type']})")
        return

    if not args.starter_pack and not args.roblox_id and not args.roblox_ids and not args.popular:
        parser.print_help()
        print("\nTip: To quickly seed default starter clothing into your catalog, run:")
        print("  python3 scripts/import_catalog_assets.py --starter-pack --username <owner_user> --password <pass>")
        return

    client = CatalogClient(base_url=args.backend, session_cookie=args.cookie)
    if not client.session_cookie:
        if not args.username or not args.password:
            print("Error: You must provide either --cookie or both --username and --password to authenticate as owner/staff.")
            sys.exit(1)
        client.login(args.username, args.password)

    if args.starter_pack:
        seed_starter_pack(client, price=args.price)

    ids_to_import = []
    if args.roblox_id:
        ids_to_import.append(args.roblox_id)
    if args.roblox_ids:
        for sid in args.roblox_ids.split(","):
            sid = sid.strip()
            if sid.isdigit():
                ids_to_import.append(int(sid))
    if args.popular:
        ids_to_import.extend([item["id"] for item in POPULAR_CLASSICS])

    if ids_to_import:
        print(f"[*] Importing {len(ids_to_import)} item(s) from Roblox...")
        for rid in ids_to_import:
            try:
                client.copy_from_roblox(rid)
            except Exception as err:
                print(f"[-] Failed to import #{rid}: {err}")


if __name__ == "__main__":
    main()
