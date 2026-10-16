#!/usr/bin/env python3
"""Serves the trusted asset host over TLS, because RCC 2020 fetches every asset over https.

Two facts about this build, both read from its own logs rather than guessed:

* RCC accepts asset fetches only from the host names compiled into ``RCCService.exe``
  (``www.silrev.biz`` and its siblings) - anything else is answered with
  ``Trust check failed``;
* it upgrades those URLs to **https**: the shipped ``Image.lua`` asks for
  ``http://www.silrev.biz/asset/?id=1`` and RCC opens a connection to port 443, while a
  plain http website only listens on 5000. The render was therefore reported as
  ``HttpError: ConnectFail`` even though the name resolved to the right container.

This module closes that gap inside the renderer container, without privileges:

* it generates a throwaway certificate authority and a server certificate for the
  trusted names (``openssl``, stdlib only otherwise),
* it appends the authority to a copy of the CA bundle RCC uses
  (``ssl/cacert.pem`` in the running build directory) and hands RCC that bundle through
  ``CURL_CA_BUNDLE``/``SSL_CERT_FILE``,
* it runs a small TLS reverse proxy in front of the local website, so
  ``https://www.silrev.biz:5000/asset/?id=...`` is answered by this stack and nothing
  leaves the compose network,
* ``--verify`` is the guard ``supervise.sh`` runs before RCC starts: the trusted name must
  answer with **this container's** certificate, so a name that resolves elsewhere stops
  the renderer instead of letting RCC render a foreign asset.

Nothing here touches the mounted RCC build: certificates live in the container's tmpfs and
the patched CA bundle is a copy in a runtime directory.
"""

import http.client
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import time

# A certificate is written next to the runtime copy of the build.
DEFAULT_TLS_DIRECTORY = '/home/rcc/tls'
DEFAULT_PORT = 5000
DEFAULT_UPSTREAM = 'website:5000'
# The CA bundle this build ships next to RCCService.exe; the engine loads it from the
# directory it runs in, which is the writable runtime copy the launcher makes.
DEFAULT_BUNDLE = '/home/rcc/runtime/ssl/cacert.pem'
# This stack's own bundle: the shipped authorities plus the local one. RCC is pointed at
# it through CURL_CA_BUNDLE/SSL_CERT_FILE, and the runtime copy above is patched as well
# for an engine build that loads the file from its working directory by name.
DEFAULT_LOCAL_BUNDLE = '/home/rcc/tls/cacert.pem'
SHIPPED_BUNDLE = '/opt/rcc/ssl/cacert.pem'
TRUSTED_NAMES = ('www.silrev.biz', 'assetgame.silrev.biz', 'assetdelivery.silrev.biz')
# The guard connects to these names, so they have to be in the certificate; localhost is
# there so the check can also be exercised without Docker DNS.
LOCAL_NAMES = ('localhost', '127.0.0.1')
CA_MARKER = '# marine renderer local certificate authority'
# Hop-by-hop headers must not be relayed.
HOP_BY_HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
              'te', 'trailer', 'transfer-encoding', 'upgrade'}
MAX_BODY = 64 * 1024 * 1024
MAX_HEADER_LINE = 64 * 1024


def log(event, **fields):
    record = {'event': event, 'at': round(time.time(), 3), 'service': 'asset-tls'}
    record.update(fields)
    print(json.dumps(record, sort_keys=True), flush=True)


class TlsError(Exception):
    """A certificate or endpoint problem; reported, never worked around silently."""


def nameserver_names():
    """The trusted names, plus any extra names the operator configured."""
    extra = [name.strip() for name in os.environ.get('RENDERER_TLS_NAMES', '').split(',') if name.strip()]
    return tuple(dict.fromkeys(list(TRUSTED_NAMES) + extra))


def openssl(*arguments, cwd=None, stdin=None):
    if shutil.which('openssl') is None:
        raise TlsError('openssl is not installed in this image; the asset host cannot be served over TLS')
    result = subprocess.run(['openssl', *arguments], cwd=cwd, input=stdin, capture_output=True, text=True)
    if result.returncode != 0:
        raise TlsError(f"openssl {' '.join(arguments)} failed: {result.stderr.strip()[:200]}")
    return result.stdout


def create_authority(directory, names=None, days=3650):
    """Generates a throwaway CA and a server certificate for the trusted names.

    Regenerated for every container start: the private key only ever signs a name that
    resolves inside this compose network, and it never leaves the container's tmpfs.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    names = tuple(names or nameserver_names())
    if all((directory / name).is_file() and (directory / name).stat().st_size for name in
           ('ca.crt', 'ca.key', 'server.crt', 'server.key')):
        # Certificates are per container start (tmpfs); a second call in the same start
        # only re-reads them, so the CA bundle and the served certificate stay in step.
        return {'ca_certificate': directory / 'ca.crt', 'ca_key': directory / 'ca.key',
                'server_certificate': directory / 'server.crt', 'server_key': directory / 'server.key',
                'names': names}
    extensions = directory / 'server.ext'
    extensions.write_text(
        'basicConstraints=CA:FALSE\n'
        'keyUsage=digitalSignature,keyEncipherment\n'
        'extendedKeyUsage=serverAuth\n'
        'subjectAltName=' + ','.join(f'DNS:{name}' for name in names + LOCAL_NAMES if not _is_address(name))
        + ''.join(f',IP:{name}' for name in names + LOCAL_NAMES if _is_address(name)) + '\n')
    openssl('req', '-x509', '-newkey', 'rsa:2048', '-sha256', '-nodes', '-days', str(days),
            '-subj', '/CN=marine-renderer-local-ca', '-keyout', 'ca.key', '-out', 'ca.crt',
            '-addext', 'basicConstraints=critical,CA:TRUE',
            '-addext', 'keyUsage=critical,keyCertSign,cRLSign', cwd=directory)
    openssl('req', '-newkey', 'rsa:2048', '-sha256', '-nodes', '-subj', '/CN=' + names[0],
            '-keyout', 'server.key', '-out', 'server.csr', cwd=directory)
    openssl('x509', '-req', '-sha256', '-days', str(days), '-in', 'server.csr',
            '-CA', 'ca.crt', '-CAkey', 'ca.key', '-CAcreateserial', '-extfile', extensions.name,
            '-out', 'server.crt', cwd=directory)
    for name in ('ca.key', 'server.key'):
        os.chmod(directory / name, 0o600)
    return {'ca_certificate': directory / 'ca.crt', 'ca_key': directory / 'ca.key',
            'server_certificate': directory / 'server.crt', 'server_key': directory / 'server.key',
            'names': names}


def _is_address(name):
    try:
        socket.inet_aton(name)
        return True
    except OSError:
        return False


def trust_authority(ca_certificate, bundle=None, marker=CA_MARKER):
    """Appends the local CA to the CA bundle RCC reads, once.

    The build ships ``ssl/cacert.pem`` next to ``RCCService.exe`` and a runtime copy of
    that directory is what RCC actually reads, so this writes to the copy. Returns True
    when the bundle changed.
    """
    bundle = Path(bundle or os.environ.get('RCC_CA_BUNDLE', DEFAULT_BUNDLE))
    authority = Path(ca_certificate).read_text()
    existing = bundle.read_text(errors='replace') if bundle.exists() else ''
    if marker in existing:
        return False
    if not bundle.exists():
        log('ca_bundle_missing', path=str(bundle))
        bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text(existing + ('\n' if existing and not existing.endswith('\n') else '')
                      + marker + '\n' + authority)
    os.chmod(bundle, 0o644)
    return True


def ssl_context(certificate, key):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(certificate), str(key))
    return context


class Terminator(http.server.ThreadingHTTPServer):
    """Minimal TLS reverse proxy: RCC's https asset fetches, relayed to the website.

    The website stays the only thing that serves assets; this only gives RCC the scheme
    it insists on, on the port the asset base URL names.
    """

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, context, upstream, timeout=30):
        host, port = upstream
        self.upstream = upstream
        self.upstream_timeout = timeout
        self.context = context
        self.body_limit = MAX_BODY
        super().__init__(address, ProxyHandler)
        self.host, self.port = address

    def get_request(self):
        connection, address = super().get_request()
        return self.context.wrap_socket(connection, server_side=True), address

    def handle_error(self, request, client_address):
        # A refused TLS handshake is a client problem (a probe, a scanner), not a crash.
        log('connection_failed', client=str(client_address), detail=sys.exc_info()[1] and str(sys.exc_info()[1])[:120])


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'marine-asset-terminator'
    sys_version = ''

    def log_message(self, format, *arguments):  # noqa: A002 - inherited signature
        log('request', client=self.client_address[0], message=format % arguments)

    # -- request relay ----------------------------------------------------------------

    def forwarded_headers(self):
        forwarded = {}
        for name, value in self.headers.items():
            if name.lower() in HOP_BY_HOP or name.lower() == 'content-length':
                continue
            forwarded[name] = value
        return forwarded

    def read_body(self):
        length = self.headers.get('Content-Length')
        if not length:
            return None
        try:
            length = int(length)
        except ValueError:
            return None
        if length <= 0:
            return None
        if length > self.server.body_limit:
            raise ValueError(f'the request body of {length} bytes is larger than this proxy relays')
        return self.rfile.read(length)

    def relay(self, method):
        try:
            body = self.read_body()
        except ValueError as error:
            self.send_error(413, str(error))
            return
        upstream_host, upstream_port = self.server.upstream
        connection = http.client.HTTPConnection(upstream_host, upstream_port, timeout=self.server.upstream_timeout)
        try:
            connection.request(method, self.path, body=body, headers=self.forwarded_headers())
            response = connection.getresponse()
            payload = response.read()
            status, headers = response.status, response.getheaders()
        except (OSError, http.client.HTTPException) as error:
            log('upstream_failed', path=self.path, detail=str(error)[:160])
            self.send_error(502, f'the local website could not be reached: {error}')
            return
        finally:
            connection.close()
        self.send_response(status)
        for name, value in headers:
            if name.lower() in HOP_BY_HOP or name.lower() == 'content-length':
                continue
            self.send_header(name, value)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        if method != 'HEAD' and payload:
            self.wfile.write(payload)

    def do_GET(self):  # noqa: N802 - inherited name
        self.relay('GET')

    def do_HEAD(self):  # noqa: N802 - inherited name
        self.relay('HEAD')

    def do_POST(self):  # noqa: N802 - inherited name
        self.relay('POST')


# -- operations ----------------------------------------------------------------------


def local_bundle(ca_certificate, bundle=None, shipped=None):
    """This stack's CA bundle: the authorities the build ships plus the local one.

    Returns the bundle path. Writing a file of our own instead of editing RCC's runtime
    copy avoids racing the launcher, which copies the mounted build into the runtime
    directory at startup.
    """
    bundle = Path(bundle or os.environ.get('RENDERER_TLS_BUNDLE', DEFAULT_LOCAL_BUNDLE))
    shipped = Path(shipped or os.environ.get('RCC_SHIPPED_BUNDLE', SHIPPED_BUNDLE))
    bundle.parent.mkdir(parents=True, exist_ok=True)
    base = shipped.read_text(errors='replace') if shipped.exists() else ''
    if not base:
        log('shipped_ca_bundle_missing', path=str(shipped))
    bundle.write_text(base + ('' if base.endswith('\n') else '\n') + CA_MARKER + '\n'
                      + Path(ca_certificate).read_text())
    os.chmod(bundle, 0o644)
    return bundle


def setup(directory=None, bundle=None, names=None):
    directory = Path(directory or os.environ.get('RENDERER_TLS_DIRECTORY', DEFAULT_TLS_DIRECTORY))
    authority = create_authority(directory, names)
    ours = local_bundle(authority['ca_certificate'], bundle)
    # Best effort for an engine that loads <working directory>/ssl/cacert.pem by name: the
    # launcher copies that directory before RCC starts, so this only patches an existing
    # copy (calling setup twice is deliberate and idempotent).
    runtime_bundle = Path(os.environ.get('RCC_CA_BUNDLE', DEFAULT_BUNDLE))
    patched = trust_authority(authority['ca_certificate'], runtime_bundle) if runtime_bundle.exists() else None
    log('authority_ready', names=list(authority['names']), directory=str(directory),
        bundle=str(ours), runtime_bundle_patched=patched)
    return authority


def serve(address=None, port=None, upstream=None, authority=None):
    authority = authority or setup()
    upstream = upstream or os.environ.get('RENDERER_TLS_UPSTREAM', DEFAULT_UPSTREAM)
    upstream_host, _, upstream_port = upstream.partition(':')
    if port is None:
        port = int(os.environ.get('RENDERER_TLS_PORT', str(DEFAULT_PORT)))
    host = address or os.environ.get('RENDERER_TLS_ADDRESS', '0.0.0.0')
    context = ssl_context(authority['server_certificate'], authority['server_key'])
    server = Terminator((host, port), context, (upstream_host, int(upstream_port or 443)))
    log('serving', address=f'{host}:{port}', upstream=f'{upstream_host}:{upstream_port}',
        names=list(authority['names']))
    return server


def verify(host=None, port=None, authority=None, timeout=None, attempts=None):
    """The guard: the trusted name must answer with *this container's* certificate.

    Returns ``(ok, message)``. A name that resolves to another container (or to the real
    internet host) either refuses the connection or presents another certificate, so
    neither case can start RCC. A connection that fails while the terminator is still
    starting is retried; another certificate refuses immediately.
    """
    attempts = attempts if attempts is not None else int(os.environ.get('RENDERER_TLS_VERIFY_ATTEMPTS', '5'))
    for attempt in range(max(1, attempts)):
        ok, message = probe_certificate(host, port, authority, timeout)
        if ok or 'did not answer' not in message:
            return ok, message
        if attempt + 1 < max(1, attempts):
            time.sleep(1)
    return ok, message


def probe_certificate(host=None, port=None, authority=None, timeout=None):
    """One attempt at ``verify``; the terminator may still be starting on the first."""
    authority = authority or setup()
    names = authority['names']
    host = host or os.environ.get('RENDERER_TLS_HOST', names[0])
    if port is None:
        port = int(os.environ.get('RENDERER_TLS_PORT', str(DEFAULT_PORT)))
    timeout = timeout or float(os.environ.get('RENDERER_TLS_VERIFY_TIMEOUT', '10'))
    expected = ssl.PEM_cert_to_DER_cert(Path(authority['server_certificate']).read_text())
    context = ssl.create_default_context(cafile=str(authority['ca_certificate']))
    try:
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                presented = tls.getpeercert(binary_form=True)
    except OSError as error:
        return False, (f'{host}:{port} did not answer with this container\'s certificate '
                       f'({error}); RCC must fetch assets from this stack, not from elsewhere')
    except ssl.SSLError as error:
        return False, (f'{host}:{port} presented a certificate this stack did not issue '
                       f'({error}); RCC must fetch assets from this stack, not from elsewhere')
    if presented != expected:
        return False, (f'{host}:{port} presented another certificate; RCC must fetch assets '
                       'from this stack, not from elsewhere')
    return True, f'{host}:{port} answers with this container\'s certificate'


def main():
    if '--setup' in sys.argv[1:]:
        setup()
        return
    if '--verify' in sys.argv[1:]:
        ok, message = verify()
        print(('asset host accepted: ' if ok else 'asset host refused: ') + message, flush=True)
        raise SystemExit(0 if ok else 1)
    server = serve()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        log('stopped')


if __name__ == '__main__':
    main()
