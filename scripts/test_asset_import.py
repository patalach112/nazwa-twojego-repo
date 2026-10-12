#!/usr/bin/env python3
"""Import one controlled PNG through the real admin API and verify the result.

Runs against the disposable local Docker stack (``./scripts/local-stack.sh``
after ``python3 scripts/configure_local_docker.py --test``) whose PostgreSQL
database ends in ``_tests``. No fixture, direct SQL insert or forged session is
used: accounts are registered through the real Razor signup form, the owner is
selected with the documented ``OwnerUserId`` configuration key (never by making
the first account an administrator), and the upload goes through
``/admin-api/api/asset/create`` like the future admin panel will.

Checks:

1. an anonymous and an ordinary authenticated account cannot use the admin import,
2. a valid upload returns a local asset id and version,
3. the ``asset``/``asset_version`` rows exist and the file is in the local store,
4. ``GET /asset/?id=`` returns the exact uploaded PNG, not HTML or a placeholder,
5. the asset is still served after a backend restart,
6. an invalid file, a path-like file name and an oversized upload are refused
   without leaking configuration secrets,
7. a disabled renderer leaves the thumbnail pending instead of faking a result,
8. the staff panel that calls this endpoint is served from the built Svelte
   bundle for the configured owner and stays closed to everyone else.
"""

import html.parser
import json
import os
import re
import secrets
import struct
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "docker/local"
DATA_PREFIX = "/data/"
PNG_NAME = "marine-asset-import.png"
REQUEST_TIMEOUT = 120
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
GENERATOR_NOTICE = "Local Docker configs are ready"


# Configured secrets are replaced in every message this helper prints, so a
# failure can be published as an annotation without exposing the local config.
REDACT = []


def scrub(text):
    for value in REDACT:
        if value:
            text = text.replace(value, "[redacted]")
    return text


def trace(step):
    print(f"Asset import: {time.strftime('%H:%M:%S')} {step}", flush=True)


# Negative checks (scripts/test_renderer_contract.py proves this path on purpose) raise
# the same failure; they set this to False so that a green run carries no failure
# annotation that reads like something actually broke.
annotate_failures = True


def fail(message):
    text = scrub("Asset import test failed: " + message)
    print(text, file=sys.stderr, flush=True)
    # Raw step logs are not always retrievable, so publish the reason as a
    # workflow annotation and in the job summary instead of only in the log.
    if annotate_failures and os.environ.get("GITHUB_ACTIONS") == "true":
        escaped = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title=Asset import check::{escaped}")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            try:
                with open(summary, "a") as stream:
                    stream.write("### Asset import check\n\n```\n" + text[:3000] + "\n```\n")
            except OSError:
                pass
    raise SystemExit(text)


def notice(message):
    message = scrub(message)
    print("Asset import: " + message, flush=True)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("::notice title=Asset import check::" + message)


class FormTokenParser(html.parser.HTMLParser):
    """Reads the antiforgery token from a normal Razor form."""

    def __init__(self):
        super().__init__()
        self.token = None

    def handle_starttag(self, tag, attributes):
        values = dict(attributes)
        if tag == "input" and values.get("name") == "__RequestVerificationToken":
            self.token = values.get("value")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, target):
        return None


class Client:
    """Small HTTP client with explicit cookies; it never follows redirects.

    A status of ``None`` means the server closed the connection before sending a
    response, which is how an oversized body can be rejected on the wire.

    ``client_ip`` becomes the same ``cf-connecting-ip`` value for every request,
    which is how the website identifies a client behind Cloudflare. The test uses
    two different, synthetic client addresses because local signup keeps a real
    five-minute per-IP cooldown that is deliberately not disabled.
    """

    def __init__(self, client_ip=None):
        self.cookies = {}
        self.client_ip = client_ip
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method, url, data=None, headers=None):
        values = dict(headers or {})
        if self.client_ip:
            values.setdefault("cf-connecting-ip", self.client_ip)
        request = urllib.request.Request(url, data=data, method=method, headers=values)
        if self.cookies:
            request.add_header("Cookie", "; ".join(f"{name}={value}" for name, value in self.cookies.items()))
        try:
            response = self.opener.open(request, timeout=REQUEST_TIMEOUT)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, OSError, ConnectionError) as error:
            detail = getattr(error, "reason", error)
            return None, {}, str(detail).encode()
        for value in response.headers.get_all("Set-Cookie") or []:
            name, _, rest = value.partition("=")
            if name.strip():
                self.cookies[name.strip()] = rest.split(";", 1)[0]
        return response.status, dict(response.headers), response.read()

    def get(self, url):
        return self.request("GET", url)

    def post_form(self, url, fields):
        body = urllib.parse.urlencode(fields).encode()
        return self.request("POST", url, data=body,
                            headers={"Content-Type": "application/x-www-form-urlencoded"})

    def post_json(self, url, value):
        return self.request("POST", url, data=json.dumps(value).encode(),
                            headers={"Content-Type": "application/json"})

    def post_multipart(self, url, fields, files):
        content_type, body = encode_multipart(fields, files)
        return self.request("POST", url, data=body, headers={"Content-Type": content_type})


def encode_multipart(fields, files):
    boundary = "----marine-asset-" + secrets.token_hex(16)
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


def make_png(width=64, height=64, colour=(12, 34, 56)):
    """Builds a tiny valid PNG without any third-party dependency."""

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(colour) * width for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9))
            + chunk(b"IEND", b""))


def run_stack(*arguments, timeout=180):
    result = subprocess.run(["bash", "scripts/local-stack.sh", *arguments], cwd=ROOT,
                            capture_output=True, text=True, timeout=timeout)
    output = "\n".join(line for line in result.stdout.splitlines() if not line.startswith(GENERATOR_NOTICE))
    if result.returncode != 0:
        fail(f"scripts/local-stack.sh {' '.join(arguments)} failed: "
             + (result.stderr.strip() or output.strip())[-500:])
    return output


def psql(database, sql):
    output = run_stack("exec", "-T", "postgres", "psql", "--username", "postgres", "--dbname", database,
                       "--tuples-only", "--no-align", "--command", sql)
    rows = [line for line in output.splitlines() if line.strip()]
    if not rows:
        fail(f"the real database returned no rows for: {sql}")
    return rows


def website_log_tail():
    """Last website container lines, used when the backend is unreachable."""
    try:
        result = subprocess.run(["bash", "scripts/local-stack.sh", "logs", "--no-color", "--tail", "40",
                                 "website"], cwd=ROOT, capture_output=True, text=True, timeout=60)
        lines = [line for line in (result.stdout + result.stderr).splitlines() if line.strip()]
        return " | ".join(lines[-12:])[-900:]
    except (subprocess.SubprocessError, OSError):
        return ""


def wait_ready(backend, frontend):
    deadline = time.monotonic() + 120
    last = ""
    while time.monotonic() < deadline:
        try:
            try:
                urllib.request.urlopen(backend + "/apisite/users/v1/users/authenticated", timeout=5).close()
                fail("the backend answered an anonymous account request instead of 401")
            except urllib.error.HTTPError as error:
                if error.code != 401:
                    fail(f"the backend answered HTTP {error.code} for an anonymous account request")
            with urllib.request.urlopen(frontend + "/", timeout=5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError) as error:
            last = str(getattr(error, "reason", error))[:200]
        time.sleep(0.5)
    detail = website_log_tail()
    fail("the local stack did not become reachable within two minutes"
         + (f" ({last})" if last else "") + (f": {detail}" if detail else ""))


def write_config_bytes(path, content):
    """Rewrites a generated config in place so bind mounts keep working."""
    with open(path, "r+b") as stream:
        stream.seek(0)
        stream.write(content)
        stream.truncate()
    os.chmod(path, 0o600)


def write_config(path, value):
    write_config_bytes(path, json.dumps(value, indent=2).encode() + b"\n")


def host_directory(container_path):
    if not isinstance(container_path, str) or not container_path.startswith(DATA_PREFIX):
        fail(f"unexpected container directory {container_path!r}")
    return DIRECTORY / "data" / container_path[len(DATA_PREFIX):].strip("/")


def directory_state(directory):
    if not directory.is_dir():
        return []
    return sorted((entry.name, entry.stat().st_size) for entry in directory.iterdir() if entry.is_file())


def page_message(body):
    """Short, tag-free excerpt of a Razor result page for failure messages."""
    text = re.sub(r"<[^>]+>", " ", body.decode("utf-8", "replace"))
    return " ".join(text.split())[:300]


def register(client, backend, prefix):
    # Local usernames are limited to 20 characters, so keep the prefix and the
    # random suffix inside that budget.
    username = prefix + secrets.token_hex(3)
    password = "asset-import-" + secrets.token_hex(16)
    status, _, body = client.get(backend + "/auth/signup")
    if status != 200:
        fail(f"the signup page returned HTTP {status}")
    form = FormTokenParser()
    form.feed(body.decode("utf-8", "replace"))
    if not form.token:
        fail("the signup page lost its antiforgery token")
    status, headers, body = client.post_form(backend + "/auth/signup", {
        "username": username, "password": password, "__RequestVerificationToken": form.token,
    })
    if status != 302 or headers.get("Location") != "/home":
        fail(f"signup returned HTTP {status} instead of a dashboard redirect: {page_message(body)}")
    if ".PEKOSECURITY" not in client.cookies:
        fail("signup did not issue a session cookie")
    status, _, body = client.get(backend + "/apisite/users/v1/users/authenticated")
    if status != 200:
        fail(f"the new account could not authenticate (HTTP {status})")
    account = json.loads(body)
    if account.get("name") != username or not account.get("id"):
        fail("the new account returned unexpected details")
    return username, account["id"]


def upload(client, backend, name, filename, content):
    return client.post_multipart(backend + "/admin-api/api/asset/create", {
        "name": name, "description": "Controlled import test", "assetTypeId": "Image",
        "genre": "All", "isForSale": "false", "isLimited": "false", "isLimitedUnique": "false",
    }, [("rbxm", filename, content)])


def expect_status(response, allowed, what):
    status, headers, body = response
    if status not in allowed:
        fail(f"{what} returned HTTP {status}, expected one of {allowed}: "
             + body.decode("utf-8", "replace")[:200])
    return headers, body


def follow_redirects(client, url, limit=5):
    """Follows a short redirect chain and reports where the content was served."""
    for _ in range(limit):
        status, headers, body = client.get(url)
        location = headers.get("Location")
        if status in (301, 302, 303, 307, 308) and location:
            url = urllib.parse.urljoin(url, location)
            continue
        return status, headers, body, url
    fail(f"too many redirects while following {url}")


def assert_no_secrets(secret_values, body, what):
    text = body.decode("utf-8", "replace")
    for value in secret_values:
        if value and value in text:
            fail(f"{what} leaked a configured secret")


def collect_secrets(website):
    values = set()

    def walk(value, key=""):
        if isinstance(value, dict):
            for child_key, child in value.items():
                walk(child, child_key)
        elif isinstance(value, str) and value and any(
                part in key.lower()
                for part in ("secret", "authorization", "games", "sessions", "password", "postgres")):
            values.add(value)

    walk(website)
    return sorted(values, key=len, reverse=True)


def main():
    if os.environ.get("ASSET_IMPORT_TEST") != "1":
        raise SystemExit("Use ASSET_IMPORT_TEST=1 with a disposable *_tests Docker stack")
    if not (DIRECTORY / "manifest.json").is_file():
        raise SystemExit("Generate the disposable Docker config first: "
                         "python3 scripts/configure_local_docker.py --test")
    manifest = json.loads((DIRECTORY / "manifest.json").read_text())
    if not manifest.get("test") or not str(manifest.get("database", "")).endswith("_tests"):
        raise SystemExit("Use ASSET_IMPORT_TEST=1 with a disposable *_tests Docker stack")

    website_path = DIRECTORY / "website.json"
    original_website = website_path.read_bytes()
    website = json.loads(original_website)
    if f"Database={manifest['database']};" not in website["Postgres"]:
        fail("the website is not configured for the disposable database")
    public = urllib.parse.urlsplit(manifest["publicUrl"])
    if public.hostname not in ("localhost", "127.0.0.1", "::1"):
        fail("only local test targets are permitted")
    backend = f"{public.scheme}://{public.hostname}:{manifest['backendPort']}"
    frontend = manifest["publicUrl"]
    database = manifest["database"]
    assets_directory = host_directory(website["Directories"]["Asset"])
    thumbnails_directory = host_directory(website["Directories"]["Thumbnails"])
    renderer_configured = bool(website.get("Render", {}).get("BaseUrl"))
    configuration_secrets = collect_secrets(website)
    REDACT.extend(configuration_secrets)

    try:
        trace("waiting for the disposable stack")
        wait_ready(backend, frontend)
        # TEST-NET-2 addresses keep the two real registrations from sharing the
        # five-minute per-IP signup cooldown without weakening it for real clients.
        owner = Client(client_ip="198.51.100.11")
        plain = Client(client_ip="198.51.100.12")
        owner_username, owner_id = register(owner, backend, "AssetOwner")
        plain_username, plain_id = register(plain, backend, "AssetPlain")
        notice(f"registered owner {owner_username} (#{owner_id}) and ordinary account {plain_username} (#{plain_id})")

        # The owner is configured explicitly, exactly like an operator would, instead of
        # promoting whoever registered first. No secret and no existing key is touched.
        if website.get("OwnerUserId"):
            fail("the disposable config already lists an owner; refusing to replace it")
        patched = json.loads(original_website)
        patched["OwnerUserId"] = [owner_id]
        # The website container bind-mounts this exact file. Rewriting the same
        # inode keeps that mount valid across `docker compose restart`, which a
        # rename would not: the container would keep reading the old file.
        write_config(website_path, patched)
        trace("owner configured; restarting the website")
        run_stack("restart", "website")
        wait_ready(backend, frontend)
        if json.loads(website_path.read_text())["OwnerUserId"] != [owner_id]:
            fail("the owner configuration did not survive the restart")
        notice("explicitly configured owner through OwnerUserId; no secret was rotated")

        asset_count = int(psql(database, "SELECT count(*) FROM asset;")[0])
        assets_before = directory_state(assets_directory)

        # 1. Authorization boundaries: anonymous and ordinary accounts must not import.
        headers, body = expect_status(upload(Client(), backend, "AnonymousUpload", PNG_NAME, make_png()),
                                      (401, 403), "an anonymous admin import")
        assert_no_secrets(configuration_secrets, body, "an anonymous admin import")
        headers, body = expect_status(upload(plain, backend, "OrdinaryUpload", PNG_NAME, make_png()),
                                      (401, 403), "an ordinary account admin import")
        assert_no_secrets(configuration_secrets, body, "an ordinary account admin import")
        if int(psql(database, "SELECT count(*) FROM asset;")[0]) != asset_count:
            fail("a refused admin import still created an asset row")
        notice("anonymous and ordinary accounts are refused the admin import")

        # 2. A valid import returns a local asset id and version.
        png = make_png()
        headers, body = expect_status(upload(owner, backend, "MarineAssetImport", PNG_NAME, png),
                                      (200,), "the owner PNG import")
        created = json.loads(body)
        asset_id = int(created["assetId"])
        version_id = int(created["assetVersionId"])
        if asset_id <= 0 or version_id <= 0:
            fail("the import did not return a local asset id and version")
        notice(f"owner uploaded a {len(png)} byte PNG as asset {asset_id} version {version_id}")

        # 3. Rows and file on disk. The admin import path attributes the asset to the
        # existing admin creator (user 1) exactly like the current code does; the owner
        # role only decides who is allowed to call the endpoint.
        asset_rows = psql(database, "SELECT asset_type, creator_id, creator_type, moderation_status, name "
                                    f"FROM asset WHERE id = {asset_id};")
        if asset_rows != ["1|1|1|1|MarineAssetImport"]:
            fail(f"unexpected asset row: {asset_rows}")
        version_rows = psql(database, "SELECT version_number, creator_id, content_id, content_url "
                                      f"FROM asset_version WHERE asset_id = {asset_id};")
        if len(version_rows) != 1:
            fail(f"expected exactly one asset_version row, found {version_rows}")
        number, version_creator, content_id, content_url = version_rows[0].split("|")
        if (number, version_creator, content_id) != ("1", "1", ""):
            fail(f"unexpected asset_version row: {version_rows[0]}")
        if not HEX_SHA256.fullmatch(content_url):
            fail(f"the stored content key is not a SHA-256 name: {content_url}")
        stored = assets_directory / content_url
        if not stored.is_file() or stored.read_bytes() != png:
            fail("the uploaded PNG is not stored in the local asset directory")
        if stored.parent != assets_directory:
            fail("the stored file escaped the configured asset directory")
        assets_after_import = directory_state(assets_directory)
        if len(assets_after_import) != len(assets_before) + 1:
            fail("the import did not add exactly one file to the asset directory")
        notice("asset and asset_version rows and the content-addressed file match the upload")

        # 4. Delivery returns the real bytes.
        status, headers, body = Client().get(f"{backend}/asset/?id={asset_id}")
        if status != 200 or body != png:
            fail(f"GET /asset/?id={asset_id} returned HTTP {status} with {len(body)} bytes")
        if "html" in headers.get("Content-Type", "").lower() or b"<html" in body[:200].lower():
            fail("asset delivery returned HTML instead of the PNG")
        notice("anonymous GET /asset/?id= serves the exact uploaded PNG bytes")

        # 6. Rejections that must not leave a broken asset or a stray file behind.
        rejected = [
            ("a renamed text file", PNG_NAME, b"this is not an image\n", (400,)),
            ("a path-like file name", "../../evil.png", make_png(), (400,)),
            ("an image over the per-image limit", PNG_NAME,
             b"\x89PNG\r\n\x1a\n" + b"\x00" * (9 * 1024 * 1024), (400,)),
            ("a body over the endpoint limit", PNG_NAME,
             b"\x89PNG\r\n\x1a\n" + b"\x00" * (31 * 1024 * 1024), (400, 413, None)),
        ]
        for what, filename, content, allowed in rejected:
            headers, body = expect_status(upload(owner, backend, "RejectedImport", filename, content),
                                          allowed, what)
            assert_no_secrets(configuration_secrets, body, what)
            if int(psql(database, "SELECT count(*) FROM asset;")[0]) != asset_count + 1:
                fail(f"{what} created an asset row")
            if directory_state(assets_directory) != assets_after_import:
                fail(f"{what} wrote files into the asset directory")
        notice("invalid file, path-like name, oversized image and oversized body are refused cleanly")

        # 7. No renderer: the thumbnail stays pending and no fake image is stored.
        if renderer_configured:
            print("Asset import: Render:BaseUrl is configured; skipping the disabled-renderer checks")
        else:
            if psql(database, f"SELECT count(*) FROM asset_thumbnail WHERE asset_id = {asset_id};") != ["0"]:
                fail("a thumbnail row exists while no renderer is configured")
            status, _, body = owner.post_json(f"{backend}/v1/batch", [
                {"requestId": "asset-import", "type": "AssetThumbnail", "targetId": asset_id}])
            if status != 200:
                fail(f"the thumbnail batch endpoint returned HTTP {status}")
            entry = json.loads(body)["data"][0]
            if entry.get("state") == "Completed" or entry.get("imageUrl"):
                fail(f"a disabled renderer still reported a finished thumbnail: {entry}")
            if directory_state(thumbnails_directory):
                fail("a disabled renderer still wrote files into the thumbnail directory")
            notice("disabled renderer keeps the thumbnail pending instead of faking a result")

        # 5. Everything survives a backend restart without rotating keys.
        run_stack("restart", "website")
        wait_ready(backend, frontend)
        status, headers, body = Client().get(f"{backend}/asset/?id={asset_id}")
        if status != 200 or body != png:
            fail(f"the asset did not survive the restart (HTTP {status}, {len(body)} bytes)")
        if psql(database, f"SELECT content_url FROM asset_version WHERE asset_id = {asset_id};") != [content_url]:
            fail("the asset version changed across the restart")
        if json.loads(website_path.read_text())["OwnerUserId"] != [owner_id]:
            fail("the restart test modified the owner configuration")
        notice("asset rows, file and delivery survive a backend restart")

        # 8. The staff panel that calls this import surface: every page it needs is
        #    served to the owner from the built bundle, and it stays staff-only.
        status, headers, body = owner.get(f"{backend}/admin/")
        assert_no_secrets(configuration_secrets, body, "the staff panel page")
        if status != 200 or "html" not in headers.get("Content-Type", "").lower():
            fail(f"the staff panel returned HTTP {status} for the configured owner")
        if b"/admin/build-redirect/bundle.js" not in body:
            fail("the staff panel page does not load its bundle")
        status, headers, body, served_from = follow_redirects(owner, f"{backend}/admin/build-redirect/bundle.js")
        assert_no_secrets(configuration_secrets, body, "the staff panel bundle")
        if status != 200 or "javascript" not in headers.get("Content-Type", "").lower():
            fail(f"the staff panel bundle returned HTTP {status}")
        if not re.fullmatch(r"/admin/build/[^/]+/bundle\.js", urllib.parse.urlsplit(served_from).path):
            fail(f"the staff panel bundle is served from an unexpected path: {served_from}")
        if len(body) < 200_000 or b"/asset/create" not in body or b"Create Item" not in body:
            fail(f"the staff panel bundle looks like a stub ({len(body)} bytes)")
        status, headers, body, _ = follow_redirects(owner, f"{backend}/admin/build-redirect/bundle.css")
        if status != 200 or "css" not in headers.get("Content-Type", "").lower() or len(body) < 500:
            fail(f"the staff panel stylesheet returned HTTP {status} with {len(body)} bytes")
        status, _, body = owner.get(f"{backend}/admin-api/api/permissions")
        if status != 200 or json.loads(body)["rank"]["name"] != "Owner":
            fail(f"the staff panel permissions endpoint returned HTTP {status}: "
                 + body.decode("utf-8", "replace")[:200])
        for client, who in ((Client(), "an anonymous visitor"), (plain, "an ordinary account")):
            status, headers, _ = client.get(f"{backend}/admin/")
            if status != 302 or headers.get("Location") != "/home":
                fail(f"{who} got HTTP {status} instead of a redirect away from the staff panel")
        headers, body = expect_status(plain.get(f"{backend}/admin-api/api/applications/pending-num"),
                                      (401, 403), "an ordinary account staff API call")
        assert_no_secrets(configuration_secrets, body, "an ordinary account staff API call")
        notice("staff panel pages, bundle and permissions work for the owner and stay closed to others")
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001 - the reason must reach the annotation
        # An unexpected exception would otherwise leave only a traceback in a raw
        # log that is not always retrievable, so report it like any other failure
        # and redact the config first.
        fail(f"unexpected {type(error).__name__}: {error}\n" + traceback.format_exc()[-1200:])
    finally:
        # Hand the disposable configuration back exactly as it was generated.
        write_config_bytes(website_path, original_website)

    print("Asset import through the real admin API, the staff panel, local storage and restart behaviour passed.")
    print("Rendering (RCC), avatar thumbnails, game joins and the remaining panel screens stay separate work.")


if __name__ == "__main__":
    main()
