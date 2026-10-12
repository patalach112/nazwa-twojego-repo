"""Bounded full-website startup smoke test; no frontend, validator or RCC is needed."""

import json
import os
import re
from pathlib import Path
import signal
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import uuid


ROOT = Path(__file__).resolve().parents[1]
WEBSITE = ROOT / "Roblox/Roblox.Website"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, target):
        return None


def main():
    primary = json.loads((WEBSITE / "appsettings.json").read_text())
    secret = primary["GameServerAuthorization"]
    # The workflow must explicitly opt into an isolated migrated test database.
    database = re.search(r'(?i)(?:^|;)\s*(?:Database|Initial Catalog)\s*=\s*([^;]+)', primary["Postgres"])
    if os.environ.get("WEBSITE_STARTUP_TEST") != "1" or database is None or not database.group(1).strip().strip('"\'').endswith("_tests"):
        raise SystemExit("Use WEBSITE_STARTUP_TEST=1 with a dedicated *_tests database and Redis instance")
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    dll = WEBSITE / "bin/Release/net8.0/Roblox.Website.dll"
    payload = json.dumps({"authorization": secret, "serverId": str(uuid.uuid4())}).encode()
    url = f"http://127.0.0.1:{port}/gs/activity"
    with tempfile.TemporaryDirectory(prefix="pekora-startup-test-") as directory:
        log_path = Path(directory) / "website.log"
        with log_path.open("w") as log:
            process = subprocess.Popen(
                ["dotnet", str(dll), "--urls", f"http://0.0.0.0:{port}"],
                cwd=WEBSITE, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                env={**os.environ, "ASPNETCORE_ENVIRONMENT": "Production"},
            )
            try:
                deadline = time.monotonic() + 90
                while True:
                    if process.poll() is not None:
                        raise RuntimeError("The website exited before it accepted requests")
                    try:
                        request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
                        with urllib.request.urlopen(request, timeout=3) as response:
                            result = json.load(response)
                            if response.status != 200 or result.get("isAlive") is not False:
                                raise RuntimeError("The full website did not return the expected unknown-job activity")
                        break
                    except urllib.error.URLError as error:
                        if isinstance(error, urllib.error.HTTPError):
                            raise RuntimeError(f"The full website returned HTTP {error.code} instead of activity") from None
                        if time.monotonic() >= deadline:
                            raise RuntimeError("The full website did not become reachable within 90 seconds") from None
                        time.sleep(0.2)
                unauthorized = urllib.request.Request(url, data=b'{"serverId":"00000000-0000-4000-8000-000000000001"}', headers={"Content-Type": "application/json"}, method="POST")
                try:
                    urllib.request.urlopen(unauthorized, timeout=3).close()
                    raise RuntimeError("The full website accepted an unauthenticated game-server callback")
                except urllib.error.HTTPError as error:
                    if error.code != 401:
                        raise RuntimeError(f"The full website returned HTTP {error.code} for an unauthenticated callback") from None
                opener = urllib.request.build_opener(NoRedirect())
                for user_agent in ("", "curl/8.0", "Mozilla/5.0 Chrome/60.0"):
                    account = urllib.request.Request(f"http://127.0.0.1:{port}/apisite/users/v1/users/authenticated", headers={"User-Agent": user_agent})
                    try:
                        opener.open(account, timeout=3).close()
                        raise RuntimeError("An anonymous request obtained authenticated account details")
                    except urllib.error.HTTPError as error:
                        if error.code != 401:
                            raise RuntimeError(f"An anonymous account request returned HTTP {error.code} instead of 401") from None
                old_captcha = urllib.request.Request(f"http://127.0.0.1:{port}/auth/captcha", headers={"User-Agent": "curl/8.0"})
                try:
                    opener.open(old_captcha, timeout=3).close()
                    raise RuntimeError("The retired browser captcha route did not redirect")
                except urllib.error.HTTPError as error:
                    if error.code != 302 or error.headers.get("Location") != "/":
                        raise RuntimeError("The retired browser captcha route did not redirect to the site") from None
                    if error.headers.get("Set-Cookie"):
                        raise RuntimeError("The retired browser captcha still issued a bypass cookie") from None
                print("Full website startup, signing setup, public routing without browser gates and retained account/game-server authorization passed on Linux.")
            except Exception:
                # Never print generated credentials while diagnosing startup errors.
                text = log_path.read_text()
                values = []
                def collect(value):
                    if isinstance(value, dict):
                        for key, child in value.items():
                            if isinstance(child, str) and any(part in key.lower() for part in ("secret", "authorization", "sessions", "games", "postgres")):
                                values.append(child)
                            elif isinstance(child, dict):
                                collect(child)
                collect(primary)
                overlay = WEBSITE / "local-secrets.json"
                if overlay.exists():
                    collect(json.loads(overlay.read_text()))
                for value in values:
                    if value:
                        text = text.replace(value, "[redacted]")
                diagnostics = "\n".join(text.splitlines()[-50:])
                print(diagnostics)
                if os.environ.get("GITHUB_ACTIONS") == "true":
                    annotation = diagnostics.replace('%', '%25').replace('\r', '%0D').replace('\n', '%0A')
                    print('::error title=Website startup regression::' + annotation)
                raise
            finally:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)


if __name__ == "__main__":
    main()
