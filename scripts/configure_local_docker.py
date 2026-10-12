"""Generate an isolated, persistent local Docker config without touching setup configs."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
FILES = ('website.json', 'frontend.json', 'migrations.json', 'game-servers.json', 'compose.env', 'manifest.json')


def parse_origin(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment or any(c.isspace() for c in value)):
        raise ValueError('public base URL must be an HTTP(S) origin without credentials, query, fragment or path')
    try:
        parsed.port
    except ValueError:
        raise ValueError('public base URL has an invalid port') from None
    return value.rstrip('/')


def configure(directory, public_url=None, database=None, project=None, frontend_port=None, backend_port=None, test=False):
    directory = Path(directory)
    if any((directory / name).exists() for name in FILES):
        if not all((directory / name).is_file() for name in FILES):
            raise ValueError('Partial local Docker config exists; no files were overwritten. Reconcile the missing files before retrying.')
        manifest = json.loads((directory / 'manifest.json').read_text())
        overrides = {'publicUrl': public_url, 'database': database, 'project': project,
                     'frontendPort': frontend_port, 'backendPort': backend_port}
        for key, value in overrides.items():
            if value is not None and value != manifest[key]:
                raise ValueError('Existing local Docker config differs at ' + key + '; edit it deliberately rather than rotating keys or database credentials.')
        if test and not manifest['test']:
            raise ValueError('Existing local Docker config is not a disposable test setup')
        return manifest

    frontend_port = frontend_port or 3000
    backend_port = backend_port or 5000
    if not (1024 <= frontend_port <= 65535 and 1024 <= backend_port <= 65535) or frontend_port == backend_port:
        raise ValueError('Use two distinct unprivileged frontend/backend ports')
    public_url = parse_origin(public_url or f'http://localhost:{frontend_port}')
    database = database or 'marine_local'
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,62}', database) or test and not database.endswith('_tests'):
        raise ValueError('Use a simple database name; disposable test databases must end in _tests')
    project = project or 'marine-local'
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,62}', project):
        raise ValueError('Use a simple Docker project name')
    if not hasattr(os, 'getuid'):
        raise ValueError('This generator targets Linux/Debian Docker hosts')

    website = json.loads((ROOT / 'Roblox/Roblox.Website/appsettings.example.json').read_text())
    password = secrets.token_hex(32)
    website.update({
        'Postgres': f'Host=postgres;Port=5432;Database={database};Username=postgres;Password={password}',
        'Redis': 'redis:6379', 'BaseUrl': public_url, 'FrontendBaseUrl': 'http://frontend:3000',
        'OwnerUserId': [], 'LocalRegistrationEnabled': True, 'ArbiterBaseUrl': 'http://arbiter:4310/',
        'Jwt': {'Sessions': secrets.token_hex(32), 'Games': secrets.token_hex(32)},
    })
    for key in ('Authorization', 'VerificationSecret', 'BotAuthorization', 'GameServerAuthorization', 'RccAuthorization', 'ArbiterAuthorization'):
        website[key] = secrets.token_hex(32)
    for key, name in {'Asset': 'assets', 'Storage': 'storage', 'Thumbnails': 'thumbnails',
                      'GroupIcons': 'group-icons', 'EconomyChatBundle': 'economy-chat', 'RCCLuaScripts': 'rcc-lua'}.items():
        website['Directories'][key] = '/data/' + name + '/'
    website['Directories'].update({'Public': '/app/public/', 'XmlTemplates': '/app/Templates/',
        'JsonData': '/app/Json/', 'ScriptsData': '/app/StudioScripts/', 'AdminBundle': '/app/admin/'})
    frontend = json.loads((ROOT / 'frontend/config.example.json').read_text())
    frontend['serverRuntimeConfig']['backend'] = {'baseUrl': 'http://website:5000', 'authorization': website['Authorization']}
    frontend['publicRuntimeConfig']['backend'].update({'baseUrl': public_url,
        'apiFormat': public_url + '/apisite/{0}{1}', 'proxyEnabled': True})
    # Do not advertise another revival's unverified client installers.
    frontend['publicRuntimeConfig']['backend']['flags']['downloadGameClients'] = []
    migrations = {'knex': {'client': 'pg', 'connection': {'host': 'postgres', 'port': 5432,
        'database': database, 'user': 'postgres', 'password': password}}}
    manifest = {'version': 1, 'publicUrl': public_url, 'database': database, 'project': project,
        'frontendPort': frontend_port, 'backendPort': backend_port, 'test': test}
    environment = {'COMPOSE_PROJECT_NAME': project, 'LOCAL_UID': os.getuid(), 'LOCAL_GID': os.getgid(),
        'POSTGRES_DB': database, 'POSTGRES_PASSWORD': password, 'FRONTEND_PORT': frontend_port, 'BACKEND_PORT': backend_port}
    payloads = {'website.json': website, 'frontend.json': frontend, 'migrations.json': migrations,
                'game-servers.json': {'GameServers': []}, 'manifest.json': manifest}
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ('assets', 'storage', 'thumbnails', 'group-icons', 'economy-chat', 'rcc-lua'):
        (directory / 'data' / name).mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in FILES:
        content = ''.join(f'{key}={value}\n' for key, value in environment.items()) if name == 'compose.env' else json.dumps(payloads[name], indent=2) + '\n'
        descriptor = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(content)
    return manifest


def enable_renderer(website, base_url='http://renderer:3521/'):
    """Points an existing local website config at the local renderer service.

    The authorization value is the website's own existing ``RccAuthorization``, so
    nothing is rotated and no second secret has to be kept in sync. Returns the
    values it set, so callers can log them without printing the secret itself.
    """
    parsed = parse_origin(base_url)
    authorization = website.get('RccAuthorization')
    if not authorization:
        raise ValueError('The website config has no RccAuthorization value to reuse for the renderer')
    website['Render'] = {'BaseUrl': parsed + '/', 'Authorization': authorization}
    return {'baseUrl': parsed + '/', 'authorization': 'reused from RccAuthorization'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, default=ROOT / 'docker/local')
    parser.add_argument('--public-base-url')
    parser.add_argument('--database-name')
    parser.add_argument('--project-name')
    parser.add_argument('--frontend-port', type=int)
    parser.add_argument('--backend-port', type=int)
    parser.add_argument('--test', action='store_true', help='Opt into a disposable *_tests database for browser tests')
    parser.add_argument('--enable-renderer', action='store_true',
                        help='Point an existing website config at the local renderer (no secret rotation)')
    parser.add_argument('--renderer-base-url', default='http://renderer:3521/',
                        help='Base URL written into Render:BaseUrl when enabling the renderer')
    args = parser.parse_args()
    try:
        if args.enable_renderer:
            website_path = args.directory / 'website.json'
            if not website_path.is_file():
                raise ValueError('No local website config yet; start the stack once, then enable the renderer')
            website = json.loads(website_path.read_text())
            result = enable_renderer(website, args.renderer_base_url)
            # Rewrite in place: the website container bind-mounts this exact file,
            # so replacing the inode would leave the running container on the old file.
            with open(website_path, 'r+b') as stream:
                stream.seek(0)
                stream.write(json.dumps(website, indent=2).encode() + b'\n')
                stream.truncate()
            os.chmod(website_path, 0o600)
            print('Renderer enabled at ' + result['baseUrl'] + '; authorization ' + result['authorization'] + '.')
            print('Start it with: ./scripts/local-stack.sh --profile renderer up -d, then restart the website.')
        else:
            configure(args.directory, args.public_base_url, args.database_name, args.project_name,
                      args.frontend_port, args.backend_port, args.test)
    except (ValueError, OSError) as error:
        parser.exit(1, str(error) + '\n')
    if not args.enable_renderer:
        print('Local Docker configs are ready; existing configs and credentials were preserved.')


if __name__ == '__main__':
    main()
