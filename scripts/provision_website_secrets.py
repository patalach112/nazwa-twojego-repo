"""Provision missing JWT keys without rewriting the user's website config."""

import json
import os
from pathlib import Path
import secrets
import sys


def setting(config, path):
    # Microsoft configuration keys are case-insensitive and may use ':' paths.
    for key, value in config.items():
        if key.casefold() == path.casefold():
            return value
    parts = path.split(":", 1)
    if len(parts) == 1:
        return None
    parent = setting(config, parts[0])
    return setting(parent, parts[1]) if isinstance(parent, dict) else None


def valid(value):
    return isinstance(value, str) and len(value) >= 32 and not value.isspace() and "<SECRET>" not in value and not value.casefold().startswith("change-me")


def main():
    website = Path(sys.argv[1])
    with (website / "appsettings.json").open() as stream:
        primary = json.load(stream)
    path = website / "local-secrets.json"
    existing = path.exists()
    overlay = json.loads(path.read_text()) if existing else {}
    missing = {}
    for name in ("Sessions", "Games"):
        key = "Jwt:" + name
        main_value = setting(primary, key)
        local_value = setting(overlay, key)
        if local_value is not None:
            if not valid(local_value):
                sys.exit(f"{path}: {key} needs a private key of at least 32 characters; existing files were left alone")
            if valid(main_value) and main_value != local_value:
                sys.exit(f"{path}: {key} conflicts with appsettings.json; reconcile the keys manually without losing active sessions")
        elif not valid(main_value):
            if existing:
                sys.exit(f"{path}: missing {key}; add a private key manually, existing files were left alone")
            missing[name] = secrets.token_hex(32)
    if existing:
        print(str(path) + " already exists, leaving it alone")
    elif missing:
        data = json.dumps({"Jwt": missing}, indent=4) + "\n"
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(data)
        print("wrote " + str(path))
    else:
        print("website JWT keys are already configured")


if __name__ == "__main__":
    main()
