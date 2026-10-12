#!/bin/sh
set -eu

# Binaries are supplied as a read-only mount, never downloaded or patched here.
case "$RCC_EXE" in
    */*|*\\*|'') echo 'RCC_EXE must be a filename inside /opt/rcc' >&2; exit 2 ;;
esac
if [ ! -f "/opt/rcc/$RCC_EXE" ]; then
    echo "Mount the selected Windows RCC directory at /opt/rcc (missing $RCC_EXE)" >&2
    exit 2
fi
case "$RCC_PORT" in
    ''|*[!0-9]*) echo 'RCC_PORT must be a number between 1 and 65535' >&2; exit 2 ;;
esac
if [ "$RCC_PORT" -lt 1 ] || [ "$RCC_PORT" -gt 65535 ]; then
    echo 'RCC_PORT must be a number between 1 and 65535' >&2
    exit 2
fi

mkdir -p /home/rcc/runtime
# The root RCC directory also contains three other builds. Do not copy them into
# the running build, and leave the mounted originals untouched.
for item in /opt/rcc/*; do
    [ -e "$item" ] || continue
    case "$(basename "$item")" in
        RCCService20[0-9][0-9]) [ ! -d "$item" ] || continue ;;
    esac
    cp -a "$item" /home/rcc/runtime/
done
cd /home/rcc/runtime

python3 - <<'PY'
from pathlib import Path
import os, struct, urllib.parse, xml.etree.ElementTree as ET
exe = Path(os.environ['RCC_EXE'])
with exe.open('rb') as stream:
    header = stream.read(64)
    if len(header) < 64 or header[:2] != b'MZ':
        raise SystemExit('RCC_EXE is not a Windows PE executable')
    stream.seek(struct.unpack_from('<I', header, 60)[0])
    pe = stream.read(6)
    if len(pe) != 6 or pe[:4] != b'PE\0\0':
        raise SystemExit('RCC_EXE has an invalid PE header')
    machine = struct.unpack_from('<H', pe, 4)[0]
    if machine == 0x8664 and os.environ.get('WINEARCH') == 'win32':
        raise SystemExit('This executable is 64-bit; use WINEARCH=win64 with a fresh prefix')
base_url = os.environ.get('RCC_BASE_URL')
if base_url:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise SystemExit('RCC_BASE_URL must be an HTTP(S) URL without embedded credentials')
    settings = ET.Element('Settings')
    ET.SubElement(settings, 'BaseUrl').text = base_url.rstrip('/')
    ET.SubElement(settings, 'ContentFolder').text = 'content'
    ET.ElementTree(settings).write('AppSettings.xml', encoding='utf-8', xml_declaration=True)
PY

if [ "$#" -eq 0 ]; then
    set -- -Console -Port "$RCC_PORT"
fi
exec xvfb-run --auto-servernum --server-args='-screen 0 1280x720x24 -nolisten tcp' \
    /usr/local/bin/run-wine.sh "$RCC_EXE" "$@"
