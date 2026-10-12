#!/bin/sh
set -eu

# Prefix initialization and the console process both need the virtual display.
# Bound wineboot so a failed Wine installation cannot masquerade as a live RCC.
if ! timeout --kill-after=5s 90s wineboot --init >/tmp/rcc-wineboot.log 2>&1; then
    cat /tmp/rcc-wineboot.log >&2
    exit 1
fi
exec wine "$@"
