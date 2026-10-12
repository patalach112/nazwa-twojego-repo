#!/usr/bin/env python3
"""Real thumbnails through the local stack: website -> renderer -> RCC under Wine.

This test never calls RCC directly. It drives the running website over HTTP, so the
thumbnail it inspects came from the real code path
(`AssetsService.RenderAsset` -> `RenderingHandler` -> `renderer:3521` -> RCC).

It proves, against the disposable ``*_tests`` stack started with the ``renderer``
profile:

0. enabling rendering hands the renderer the website's own Authorization value through the
   environment, without rotating a secret or touching anything else,
1. the renderer reaches RCC and reports its real version,
2. an uploaded image asset gets a 420x420 thumbnail whose pixels are the uploaded
   texture, stored in the configured thumbnail directory and served from its URL,
3. an uploaded model asset gets its own, different 3D render, stored as a separate file
   and recorded as awaiting staff approval in the database (asserted, not hidden),
4. with the renderer stopped the upload and delivery still work and nothing claims a
   finished thumbnail,
5. a caller without the Authorization value, and an endpoint the renderer does not
   implement, are refused instead of returning a picture.
"""

import base64
import http.client
import json
import os
from pathlib import Path
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
# The HTTP helpers are shared with the Stage 1 check on purpose: one client, one
# set of assertions about cookies, redirects and secret redaction.
# `psql` is the strict helper: it fails on an empty result, which is exactly what the
# stopped-renderer case asserts. Waiting for a thumbnail row uses thumbnail_rows instead,
# because an empty result there means "not yet" rather than "wrong".
from test_asset_import import (  # noqa: E402
    Client, collect_secrets, expect_status, fail, make_png, notice, psql, register,
    run_stack, trace, wait_ready, write_config, write_config_bytes,
)
import configure_local_docker  # noqa: E402
from probe_rcc_image import run_probe as rcc_image_probe  # noqa: E402

DIRECTORY = ROOT / "docker/local"
DATA_PREFIX = "/data/"
RENDERER_URL = "http://127.0.0.1:" + os.environ.get("RENDERER_PORT", "3521")
THUMBNAIL_EDGE = 420
RENDER_TIMEOUT = int(os.environ.get("RENDERER_TEST_TIMEOUT", "240"))
WARMUP_TIMEOUT = int(os.environ.get("RENDERER_WARMUP_TIMEOUT", "300"))
MODEL_FIXTURE = ROOT / "RCCService/RCCService2020/ExtraContent/models/AvatarContextMenu/AvatarContextArrow.rbxm"
APPROVED = 1
AWAITING_APPROVAL = 2


def read_png(data):
    """Minimal PNG reader (8-bit, no interlace) returning (width, height, rows).

    Only what the assertions need: real pixel values, so a built-in fallback or a
    uniformly grey placeholder cannot pass as a rendered texture.
    """
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        fail("the stored thumbnail is not a PNG file")
    offset = 8
    width = height = colour_type = None
    compressed = b""
    while offset + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[offset:offset + 8])
        payload = data[offset + 8:offset + 8 + length]
        if len(payload) != length:
            fail("the stored thumbnail is truncated")
        if kind == b"IHDR":
            width, height, depth, colour_type, compression, filter_method, interlace = struct.unpack(
                ">IIBBBBB", payload[:13])
            if depth != 8 or interlace != 0 or compression != 0 or filter_method != 0:
                fail(f"the stored thumbnail uses unsupported PNG features ({depth} bit, interlace {interlace})")
            if colour_type not in (0, 2, 4, 6):
                fail(f"the stored thumbnail uses an unsupported colour type {colour_type}")
        elif kind == b"IDAT":
            compressed += payload
        elif kind == b"IEND":
            break
        offset += 12 + length
    if width is None or colour_type is None or not compressed:
        fail("the stored thumbnail has no usable PNG image data")
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[colour_type]
    raw = zlib.decompress(compressed)
    stride = width * channels
    rows = []
    previous = bytearray(stride)
    position = 0
    for _ in range(height):
        filter_type = raw[position]
        position += 1
        line = bytearray(raw[position:position + stride])
        if len(line) != stride:
            fail("the stored thumbnail has incomplete scanlines")
        position += stride
        for index in range(stride):
            left = line[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 0:
                value = line[index]
            elif filter_type == 1:
                value = line[index] + left
            elif filter_type == 2:
                value = line[index] + above
            elif filter_type == 3:
                value = line[index] + (left + above) // 2
            elif filter_type == 4:
                estimate = left + above - upper_left
                distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
                value = line[index] + (left if distances[0] <= distances[1] and distances[0] <= distances[2]
                                       else above if distances[1] <= distances[2] else upper_left)
            else:
                fail(f"the stored thumbnail uses an unknown PNG filter {filter_type}")
            line[index] = value & 0xFF
        rows.append(bytes(line))
        previous = line
    return width, height, rows


def channels_of(rows, width):
    return len(rows[0]) // width


def colour_at(rows, width, x, y):
    """RGB pixel at (x, y) for the 8-bit PNG kinds this stack produces."""
    channels = channels_of(rows, width)
    offset = x * channels
    row = rows[y]
    if channels in (3, 4):
        return row[offset], row[offset + 1], row[offset + 2]
    return (row[offset],) * 3


def is_colourish(pixel, wanted, tolerance=50):
    return all(abs(component - target) <= tolerance for component, target in zip(pixel, wanted))


def upload_asset(client, backend, name, filename, content, asset_type="Image"):
    return client.post_multipart(backend + "/admin-api/api/asset/create", {
        "name": name, "description": "Local renderer check", "assetTypeId": asset_type,
        "genre": "All", "isForSale": "false", "isLimited": "false", "isLimitedUnique": "false",
    }, [("rbxm", filename, content)])


def thumbnail_state(client, backend, asset_id):
    """Asks the website's own thumbnail API, exactly like the browser does."""
    status, _, body = client.post_json(f"{backend}/v1/batch", [
        {"requestId": "renderer-check", "type": "AssetThumbnail", "targetId": asset_id}])
    if status != 200:
        fail(f"the thumbnail API returned HTTP {status}: {body.decode('utf-8', 'replace')[:200]}")
    return json.loads(body)["data"][0]


def thumbnail_rows(database, asset_id):
    """Stored thumbnail rows, without treating "no row yet" as an error."""
    output = run_stack("exec", "-T", "postgres", "psql", "--username", "postgres",
                       "--dbname", database, "--tuples-only", "--no-align", "--command",
                       "SELECT moderation_status, content_url FROM asset_thumbnail "
                       f"WHERE asset_id = {asset_id};")
    return [line.strip() for line in output.splitlines() if line.strip()]


def renderer_probe(asset_id, authorization):
    """The renderer's own answer for one asset, used as a failure message.

    This is a diagnostic, never a substitute for the website path: it says whether the
    renderer itself could produce the picture, which is what separates "the render is
    broken" from "the website never asked for it or never stored it".
    """
    try:
        status, body = renderer_post_once("/image/image", {"assetId": asset_id},
                                          authorization=authorization, timeout=RENDER_TIMEOUT)
    except (urllib.error.URLError, ConnectionError, http.client.HTTPException) as error:
        return f"the renderer was not reachable ({error})"
    if status == 200 and body.get("success"):
        return (f"the renderer rendered it (HTTP 200, {len(base64.b64decode(body['data']))} bytes), "
                f"so the website did not store the render")
    return f"the renderer answered HTTP {status}: {body.get('message')}"


def rcc_image_report(asset_id):
    """RCC's own answers for the calls the image script makes, for failure messages.

    A blank or placeholder thumbnail does not say why RCC could not load the asset, and
    the shipped script swallows the reason. This asks RCC directly; it renders nothing
    into the website and is only used when an assertion already failed.
    """
    try:
        return rcc_image_probe(asset_id)
    except Exception as error:  # noqa: BLE001 - a diagnostic must never hide the failure
        return f"the RCC image probe could not run: {type(error).__name__}: {error}"


def wait_for_thumbnail(client, backend, database, asset_id, what, authorization=None,
                       timeout=RENDER_TIMEOUT):
    """Waits for the render to reach the database, then for the website to serve it.

    The database row is the progress signal on purpose: while the thumbnail row is
    missing this build's batch API answers errorCode 4 "The requested Ids are invalid",
    which is how it reports "no picture yet" - reading that as a failure would call a
    render in flight a broken renderer. Returns the API entry, the stored moderation
    status and the stored key.
    """
    deadline = time.monotonic() + timeout
    rows = []
    while time.monotonic() < deadline:
        rows = thumbnail_rows(database, asset_id)
        if rows:
            break
        time.sleep(3)
    if not rows:
        fail(f"{what} never got a thumbnail row within {timeout}s: "
             f"{renderer_probe(asset_id, authorization)}; the website answered "
             f"{thumbnail_state(client, backend, asset_id)}; RCC said: "
             f"{rcc_image_report(asset_id)}")
    if len(rows) != 1:
        fail(f"{what} must have exactly one thumbnail row, the database has {rows}")
    moderation, key = rows[0].split("|")
    deadline = time.monotonic() + 30
    last = None
    while time.monotonic() < deadline:
        last = thumbnail_state(client, backend, asset_id)
        if last.get("state") == "Completed" and last.get("imageUrl"):
            return last, moderation, key
        time.sleep(2)
    fail(f"{what} has thumbnail {key} in the database but the website reported {last}")


def served_thumbnail_path(image_url, key, what):
    """The path the website must serve, which is the stored hash plus the extension."""
    path = urllib.parse.urlsplit(image_url).path
    if path != f"/images/thumbnails/{key}.png":
        fail(f"{what} is served from {path}, not /images/thumbnails/{key}.png")
    return path


def fetch_thumbnail(image_url, backend):
    """Fetches a thumbnail URL the way a browser does: absolute URLs stay absolute.

    The website builds absolute URLs from its own BaseUrl, and that URL is what the
    browser gets; a relative one is resolved against the backend as a fallback.
    """
    url = image_url if urllib.parse.urlsplit(image_url).scheme else backend + image_url
    status, _, body = Client().get(url)
    if status != 200 or body == b"" or b"<html" in body[:200].lower():
        fail(f"the thumbnail URL {url} answered HTTP {status} with {len(body)} bytes")
    return url, body


def thumbnail_file(thumbnails_directory, image_url):  # noqa: D103
    """Maps a served thumbnail URL back to the file the website wrote."""
    name = urllib.parse.urlsplit(image_url).path.rsplit("/", 1)[-1]
    path = thumbnails_directory / name
    if not path.is_file():
        fail(f"the reported thumbnail {image_url} has no file {name} in the thumbnail directory")
    return path


def thumbnail_file_for_key(thumbnails_directory, key):
    # UploadAssetContent hashes the content and returns the plain hash while writing
    # "<hash>.png"; the website turns that key into "/images/thumbnails/<hash>.png".
    if key.endswith(".png"):
        fail(f"the stored thumbnail key {key} must be the plain hash, without the extension")
    path = thumbnails_directory / (key + ".png")
    if not path.is_file():
        fail(f"the stored thumbnail key {key} has no file {path.name} in the thumbnail directory")
    return path



def wait_for_health(timeout=WARMUP_TIMEOUT):
    """Waits for the renderer's health answer.

    A render job in flight leaves RCC unable to answer a version query, so a busy
    renderer is waited for and reported rather than treated as broken.
    """
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(RENDERER_URL + "/healthz", timeout=30) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                try:
                    last = json.loads(error.read())
                except ValueError:
                    last = {"message": f"HTTP {error.code}"}
        except (urllib.error.URLError, ConnectionError, http.client.HTTPException) as error:
            last = {"message": f"the renderer was not reachable: {error}"}
        time.sleep(5)
    fail(f"the renderer never reported healthy RCC within {timeout}s; last answer: {last}")


def png_constants(data):
    """(format, width, height) without decoding the pixels; None when not a PNG."""
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) < 24 or data[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", data[16:24])
    return "PNG", width, height


def wait_for_renderer_authorization(authorization, timeout=90):
    """Waits until the renderer accepts the enabled value.

    The value reaches the renderer through its environment, read from the generated
    compose environment file, so this asserts that the running container really holds
    the enabled secret. A request without a valid asset id answers immediately, which is
    enough to tell an accepted secret from a rejected one.
    """
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            status, body = renderer_post_once("/image/image", {}, authorization=authorization)
        except (urllib.error.URLError, ConnectionError, http.client.HTTPException) as error:
            last = (None, {"message": f"the renderer was not reachable: {error}"})
            time.sleep(3)
            continue
        if status not in (401, 503):
            return status, body
        last = (status, body)
        time.sleep(3)
    fail(f"the renderer rejected or never saw the enabled Authorization value within {timeout}s: {last}")


def renderer_post_once(path, payload, authorization=None, timeout=30):
    headers = {"Content-Type": "application/json"}
    if authorization is not None:
        headers["Authorization"] = authorization
    request = urllib.request.Request(RENDERER_URL + path, data=json.dumps(payload).encode(),
                                     headers=headers, method="POST")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        # A real answer, including 401/501/502: never retried as a transport failure.
        with error:
            return error.code, json.loads(error.read())


def renderer_post(path, payload, authorization=None, timeout=30, attempts=6, pause=5):
    """Posts to the renderer, tolerating the service being restarted meanwhile.

    Restarting a service in this stack can restart the containers that depend on it, so
    a connection reset here means "wait for the renderer", not a renderer failure.
    """
    last = None
    for attempt in range(attempts):
        try:
            return renderer_post_once(path, payload, authorization, timeout)
        except (urllib.error.URLError, ConnectionError, http.client.HTTPException) as error:
            last = error
            if attempt < attempts - 1:
                time.sleep(pause)
    fail(f"the renderer did not answer POST {path} after {attempts} attempts: {last}")


def main():
    if os.environ.get("RENDERER_TEST") != "1":
        raise SystemExit("Use RENDERER_TEST=1 with a disposable *_tests Docker stack "
                         "started with the renderer profile")
    if not (DIRECTORY / "manifest.json").is_file():
        raise SystemExit("Generate the disposable Docker config first: "
                         "python3 scripts/configure_local_docker.py --test")
    manifest = json.loads((DIRECTORY / "manifest.json").read_text())
    if not manifest.get("test") or not str(manifest.get("database", "")).endswith("_tests"):
        raise SystemExit("Use RENDERER_TEST=1 with a disposable *_tests Docker stack")
    if not MODEL_FIXTURE.is_file():
        raise SystemExit("The bundled model fixture is missing: " + str(MODEL_FIXTURE))

    website_path = DIRECTORY / "website.json"
    original_website = website_path.read_bytes()
    website = json.loads(original_website)
    public = urllib.parse.urlsplit(manifest["publicUrl"])
    if public.hostname not in ("localhost", "127.0.0.1", "::1"):
        fail("only local test targets are permitted")
    backend = f"{public.scheme}://{public.hostname}:{manifest['backendPort']}"
    frontend = manifest["publicUrl"]
    database = manifest["database"]
    thumbnails_directory = DIRECTORY / "data" / website["Directories"]["Thumbnails"][len(DATA_PREFIX):].strip("/")
    configuration_secrets = collect_secrets(website)
    owner = Client(client_ip="198.51.100.13")

    try:
        trace("starting the website and waiting for it")
        run_stack("up", "-d")
        wait_ready(backend, frontend)

        # The owner is configured explicitly, like an operator would. The renderer
        # reuses the website's own Render:Authorization value: no new secret, no rotation.
        owner_username, owner_id = register(owner, backend, "RenderOwner")
        patched = json.loads(original_website)
        if patched.get("OwnerUserId"):
            fail("the disposable config already lists an owner; refusing to replace it")
        patched["OwnerUserId"] = [owner_id]
        # The same value, no rotation: the website sends Render:Authorization, and the
        # generated compose environment file hands it to the renderer container.
        configure_local_docker.enable_renderer(patched, compose_env_path=DIRECTORY / "compose.env")
        write_config(website_path, patched)
        run_stack("restart", "website")
        wait_ready(backend, frontend)
        authorization = patched["Render"]["Authorization"]

        # Start the renderer only now, so it starts with the enabled value in its
        # environment. It needs neither the website nor a healthy database to boot, and
        # nothing here depends on a container noticing a host file that changed after it
        # started. Compose recreates the service when this value changes.
        run_stack("--profile", "renderer", "up", "-d")
        notice(f"registered {owner_username} (#{owner_id}) as owner; the renderer starts with the "
               f"enabled Authorization value from the generated environment, without rotating a secret")

        # 1. Health is RCC's own answer, not an open TCP port. While RCC is busy with
        #    a render job it cannot answer a version query, so this waits for it.
        health = wait_for_health()
        rcc = health.get("rcc") or {}
        if not health.get("success") or not rcc.get("version"):
            fail(f"the renderer did not report a working RCC: {health}")
        if rcc.get("lastContactSeconds") is None or rcc["lastContactSeconds"] > 60:
            fail(f"the renderer has not spoken to RCC recently: {rcc}")
        notice(f"the renderer reaches RCC {rcc['version']} "
               f"(last contact {rcc['lastContactSeconds']}s ago, busy={rcc.get('busy')})")

        # The website's own render client gives a request ten seconds, so the first
        # render must not happen inside a user request. Wait for the startup warm-up
        # and report what it proved instead of assuming it.
        deadline = time.monotonic() + WARMUP_TIMEOUT
        warmup = health.get("warmup", {})
        while warmup.get("state") == "pending" and time.monotonic() < deadline:
            time.sleep(5)
            try:
                with urllib.request.urlopen(RENDERER_URL + "/healthz", timeout=30) as response:
                    warmup = json.loads(response.read()).get("warmup", {})
            except (urllib.error.URLError, ConnectionError, http.client.HTTPException) as error:
                warmup = {"state": "pending", "detail": f"health is not reachable: {error}"}
        if warmup.get("state") in (None, "pending"):
            fail(f"the renderer never finished its startup warm-up within {WARMUP_TIMEOUT}s: {warmup}")
        # The warm-up is a real render through RCC: it pays Wine's first-render cost so
        # the website's ten-second request is not the one that waits for it, and it says
        # whether this container can render at all. A failure here is reported now,
        # before an upload, instead of surfacing as a vague thumbnail error.
        if warmup.get("state") != "ok":
            fail(f"the renderer's startup warm-up did not succeed: {warmup}")
        notice(f"the renderer rendered its warm-up {warmup.get('script')} thumbnail "
               f"({warmup.get('bytes')} bytes, {warmup.get('seconds')}s) before any upload")

        # 1b. The enabled value is the one the renderer holds: a caller without it, or
        #     with a wrong one, is refused further down. This is asserted, not assumed.
        status, body = wait_for_renderer_authorization(authorization)
        notice(f"the renderer accepted the enabled Authorization value (HTTP {status} for a probe request)")

        # 5. Refusals first: they must not need a working render.
        status, body = renderer_post("/image/image", {"assetId": 1}, authorization=None)
        if status != 401 or body.get("success"):
            fail(f"the renderer answered without Authorization: HTTP {status}, {body}")
        status, _ = renderer_post("/image/image", {"assetId": 1}, authorization="wrong-value")
        if status != 401:
            fail(f"the renderer accepted a wrong Authorization: HTTP {status}")
        status, body = renderer_post("/game/thumbnail", {"placeId": 1}, authorization=authorization)
        if status != 501 or "not implemented" not in body.get("message", ""):
            fail(f"an unimplemented endpoint returned HTTP {status}: {body}")
        status, body = renderer_post("/image/clothing", {"assetId": 1}, authorization=authorization)
        if status != 501 or "mannequin" not in body.get("message", ""):
            fail(f"clothing must stay pending with a concrete reason, got HTTP {status}: {body}")
        notice("the renderer refuses missing secrets and names what an unimplemented endpoint needs")

        # 2. Real image assets: two different uploaded textures must produce two
        #    different renders, each showing its own colour. That is what separates a
        #    real render from a fixed fallback image.
        rendered = {}
        for label, colour, filename in (("Red", (220, 40, 40), "renderer-red.png"),
                                        ("Green", (20, 200, 40), "renderer-green.png")):
            headers, body = expect_status(upload_asset(owner, backend, "Render" + label, filename,
                                                       make_png(64, 64, colour=colour)),
                                          (200,), f"the {label.lower()} texture import")
            asset_id = int(json.loads(body)["assetId"])
            entry, moderation, key = wait_for_thumbnail(owner, backend, database, asset_id,
                                                        f"{label.lower()} image asset {asset_id}",
                                                        authorization=authorization)
            # A texture render is approved automatically: the catalog would show a
            # pending asset as no picture at all, so the stored status is asserted.
            if moderation != str(APPROVED):
                fail(f"the {label.lower()} texture thumbnail was stored with moderation "
                     f"status {moderation}, not approved ({APPROVED})")
            served_thumbnail_path(entry["imageUrl"], key, f"the {label.lower()} thumbnail")
            path = thumbnail_file(thumbnails_directory, entry["imageUrl"])
            if path.name != key + ".png":
                fail(f"the served file {path.name} is not the stored key {key} plus .png")
            served_url, served = fetch_thumbnail(entry["imageUrl"], backend)
            if served != path.read_bytes():
                fail(f"the bytes served from {served_url} are not the stored thumbnail")
            width, height, rows = read_png(path.read_bytes())
            if (width, height) != (THUMBNAIL_EDGE, THUMBNAIL_EDGE):
                fail(f"the stored thumbnail is {width}x{height}, not {THUMBNAIL_EDGE}x{THUMBNAIL_EDGE}")
            samples = [colour_at(rows, width, x, y)
                       for x in (width // 4, width // 2, width - width // 4)
                       for y in (height // 4, height // 2, height - height // 4)]
            matching = sum(1 for pixel in samples if is_colourish(pixel, colour))
            if matching < len(samples) // 2:
                fail(f"the {label.lower()} render does not show the uploaded colour "
                     f"({matching}/{len(samples)}): {samples}; RCC said: "
                     f"{rcc_image_report(asset_id)}")
            rendered[label] = (path.read_bytes(), entry["imageUrl"], samples[0])
            notice(f"{label.lower()} texture {asset_id} rendered as {key}.png, served at {served_url}, "
                   f"showing {samples[0]}")
        if rendered["Red"][0] == rendered["Green"][0]:
            fail("two different uploaded textures produced the same thumbnail file")
        if is_colourish(rendered["Red"][2], (20, 200, 40)) or is_colourish(rendered["Green"][2], (220, 40, 40)):
            fail(f"the renders are swapped or fixed: red {rendered['Red'][2]}, green {rendered['Green'][2]}")
        path = thumbnail_file(thumbnails_directory, rendered["Red"][1])
        notice("uploaded images render as real 420x420 textures, served from their own URLs")

        # 3. A real model asset: its own, different 3D render. Every thumbnail the
        #    renderer produces is stored as awaiting staff approval - the catalog shows
        #    it because the asset itself is approved, and that is asserted, not assumed.
        headers, body = expect_status(upload_asset(owner, backend, "RenderModel", "renderer-check.rbxm",
                                                   MODEL_FIXTURE.read_bytes(), asset_type="Model"),
                                      (200,), "the model import")
        model_id = int(json.loads(body)["assetId"])
        model_entry, moderation, key = wait_for_thumbnail(owner, backend, database, model_id,
                                                          f"model asset {model_id}",
                                                          authorization=authorization)
        rows = psql(database, "SELECT moderation_status, content_url FROM asset_thumbnail "
                              f"WHERE asset_id = {model_id};")
        if len(rows) != 1:
            fail(f"model asset {model_id} must have exactly one thumbnail row, database said: {rows}")
        if rows[0] != moderation + "|" + key:
            fail(f"the database row {rows[0]} and the served thumbnail {moderation}|{key} disagree")
        if moderation != str(AWAITING_APPROVAL):
            fail(f"the model thumbnail was stored with moderation status {moderation}, "
                 f"not awaiting approval ({AWAITING_APPROVAL})")
        served_thumbnail_path(model_entry["imageUrl"], key, "the model thumbnail")
        model_path = thumbnail_file_for_key(thumbnails_directory, key)
        fetch_thumbnail(model_entry["imageUrl"], backend)
        model_width, model_height, _ = read_png(model_path.read_bytes())
        if (model_width, model_height) != (THUMBNAIL_EDGE, THUMBNAIL_EDGE):
            fail(f"the model thumbnail is {model_width}x{model_height}, not {THUMBNAIL_EDGE}x{THUMBNAIL_EDGE}")
        if model_path.read_bytes() == path.read_bytes():
            fail("the model thumbnail is the same file as the texture thumbnail")
        notice(f"model asset {model_id} rendered its own 3D thumbnail, stored as {key} "
               f"(awaiting approval {moderation}) and served at {model_entry['imageUrl']}")

        # 3b. The avatar path is attempted and reported, because RCC's own Avatar.lua
        #     loads a real character through the website. It is not claimed to work
        #     unless it returns a real image, and it never returns a substitute.
        status, body = renderer_post("/player/thumbnail", {"userId": owner_id},
                                     authorization=authorization, timeout=RENDER_TIMEOUT)
        if status == 200 and body.get("success"):
            avatar = base64.b64decode(body["data"])
            details = png_constants(avatar)
            if details is None:
                fail("the avatar render did not return a decodable PNG")
            if details[1] < THUMBNAIL_EDGE or details[2] < THUMBNAIL_EDGE:
                fail(f"the avatar render came back as {details[1]}x{details[2]}, which is a placeholder")
            notice(f"RCC's own Avatar script rendered user {owner_id} through the website "
                   f"({details[1]}x{details[2]}, {len(avatar)} bytes)")
        elif status in (501, 502):
            notice(f"the avatar render is not working yet and stayed pending: "
                   f"HTTP {status} - {body.get('message')}")
        else:
            fail(f"the avatar render returned HTTP {status}: {body}")

        # 4. No renderer: the asset still works and no thumbnail is faked.
        run_stack("--profile", "renderer", "stop", "renderer")
        headers, body = expect_status(upload_asset(owner, backend, "RenderOffline", "offline.png",
                                                   make_png(32, 32, colour=(10, 200, 10))),
                                      (200,), "an upload with the renderer stopped")
        offline_id = int(json.loads(body)["assetId"])
        status, _, delivered = Client().get(f"{backend}/asset/?id={offline_id}")
        if status != 200 or delivered == b"":
            fail("an upload with the renderer stopped did not survive")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if thumbnail_state(owner, backend, offline_id).get("state") == "Completed":
                fail("a thumbnail was reported as finished while the renderer was stopped")
            time.sleep(4)
        if psql(database, f"SELECT count(*) FROM asset_thumbnail WHERE asset_id = {offline_id};") != ["0"]:
            fail("a thumbnail row appeared while the renderer was stopped")
        notice("with the renderer stopped the asset still saves, serves and keeps a pending thumbnail")
        run_stack("--profile", "renderer", "start", "renderer")
    except SystemExit:
        raise
    except Exception as error:  # noqa: BLE001 - the reason must reach the annotation
        import traceback
        fail(f"unexpected {type(error).__name__}: {error}\n" + traceback.format_exc()[-1500:])
    finally:
        write_config_bytes(website_path, original_website)

    print("Real thumbnails through the website, the renderer and RCC 2020 passed.")
    print("Avatar objects, packages, animations and place thumbnails stay unimplemented: "
          "the renderer answers 501 for them instead of faking a picture.")


if __name__ == "__main__":
    main()
