#!/usr/bin/env python3
"""Collects renderer diagnostics for a failed CI run without leaking secrets.

Pastes the tail of the disposable stack's own logs and the test log into the job
summary and the check annotations, with every secret replaced first. Run it only
against the disposable ``*_tests`` stack.
"""

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "docker/local"
SERVICES = ["renderer", "website"]
TAIL = 60
MAX_CHARS = 3000


def read(path):
    return Path(path).read_text() if Path(path).is_file() else ""


def secrets():
    values = []

    def collect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, str) and child and any(
                        word in key.lower() for word in ("secret", "authorization", "sessions", "games", "postgres")):
                    values.append(child)
                else:
                    collect(child)

    try:
        collect(json.loads((DIRECTORY / "website.json").read_text()))
    except (OSError, ValueError):
        pass
    try:
        config = json.loads((DIRECTORY / "website.json").read_text())
        for part in str(config.get("Postgres", "")).split(";"):
            for key in ("Host=", "Password="):
                if part.startswith(key) and len(part) > len(key):
                    values.append(part[len(key):])
    except (OSError, ValueError):
        pass
    try:
        values.append(json.loads((DIRECTORY / "migrations.json").read_text())["knex"]["connection"]["password"])
    except (OSError, KeyError, ValueError):
        pass
    return sorted(set(values), key=len, reverse=True)


def redact(text, values):
    for value in values:
        text = text.replace(value, "[redacted]")
    return text


def stack(*arguments):
    result = subprocess.run(["bash", "scripts/local-stack.sh", *arguments],
                            cwd=ROOT, capture_output=True, text=True, timeout=180)
    return (result.stdout + result.stderr).strip()


def main():
    values = secrets()
    sections = [("Renderer stack state", stack("ps", "-a")),
                ("Renderer, RCC and website logs", stack("logs", "--no-color", "--tail", str(TAIL), *SERVICES)),
                ("Renderer test log", read(os.environ.get("RENDERER_TEST_LOG", "/dev/null")))]
    summary = Path(os.environ.get("GITHUB_STEP_SUMMARY", os.devnull))
    for title, text in sections:
        diagnostics = "\n".join(redact(text, values).splitlines()[-45:])[-MAX_CHARS:]
        if not diagnostics.strip():
            continue
        print(diagnostics)
        with summary.open("a") as stream:
            stream.write(f"## {title}\n\n```\n{diagnostics}\n```\n\n")
        escaped = diagnostics.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error title={title}::{escaped}")
    if not values:
        print("::warning title=Renderer diagnostics::no secrets were collected for redaction", file=sys.stderr)


if __name__ == "__main__":
    main()
