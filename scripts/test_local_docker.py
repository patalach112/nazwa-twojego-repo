"""Check Docker config preservation without requiring a daemon or .NET."""
import json
import os
from pathlib import Path
import re
import tempfile
import unittest

from configure_local_docker import configure, FILES


class DockerConfigTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='local docker test ')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'private config'

    def read(self, name):
        return json.loads((self.directory / name).read_text())

    def snapshot(self):
        return {name: (self.directory / name).read_bytes() for name in FILES}

    def test_fresh_configs_coordinate_services_without_exposing_private_credentials(self):
        configure(self.directory)
        website = self.read('website.json')
        frontend = self.read('frontend.json')
        migration = self.read('migrations.json')['knex']['connection']
        self.assertTrue(website['LocalRegistrationEnabled'])
        self.assertEqual(website['OwnerUserId'], [])
        self.assertEqual(website['BaseUrl'], 'http://localhost:3000')
        self.assertEqual(website['FrontendBaseUrl'], 'http://frontend:3000')
        self.assertEqual(frontend['serverRuntimeConfig']['backend']['baseUrl'], 'http://website:5000')
        self.assertEqual(frontend['serverRuntimeConfig']['backend']['authorization'], website['Authorization'])
        self.assertEqual(frontend['publicRuntimeConfig']['backend']['baseUrl'], website['BaseUrl'])
        self.assertEqual(frontend['publicRuntimeConfig']['backend']['flags']['downloadGameClients'], [])
        self.assertIn('Password=' + migration['password'], website['Postgres'])
        self.assertNotIn(migration['password'], json.dumps(frontend))
        self.assertNotIn(website['Authorization'], json.dumps(frontend['publicRuntimeConfig']))
        self.assertNotIn('website:5000', json.dumps(frontend['publicRuntimeConfig']))
        self.assertNotEqual(website['Jwt']['Sessions'], website['Jwt']['Games'])
        self.assertEqual(self.read('game-servers.json')['GameServers'], [])
        for name in FILES:
            self.assertEqual((self.directory / name).stat().st_mode & 0o777, 0o600)
        for name in ('assets', 'storage', 'thumbnails', 'group-icons', 'economy-chat', 'rcc-lua'):
            self.assertTrue((self.directory / 'data' / name).is_dir())

    def test_restart_preserves_every_key_password_and_config_byte(self):
        configure(self.directory)
        before = self.snapshot()
        configure(self.directory)
        self.assertEqual(before, self.snapshot())

    def test_custom_deployment_origin_and_ports_remain_consistent(self):
        configure(self.directory, 'https://local.example', frontend_port=3100, backend_port=5100)
        self.assertEqual(self.read('website.json')['BaseUrl'], 'https://local.example')
        self.assertEqual(self.read('frontend.json')['publicRuntimeConfig']['backend']['apiFormat'], 'https://local.example/apisite/{0}{1}')
        self.assertIn('FRONTEND_PORT=3100\n', (self.directory / 'compose.env').read_text())

    def test_disposable_test_setup_requires_an_explicit_tests_database(self):
        with self.assertRaisesRegex(ValueError, '_tests'):
            configure(self.directory, test=True)
        configure(self.directory, database='website_tests', project='website-tests', test=True)
        self.assertTrue(self.read('manifest.json')['test'])

    def test_partial_existing_config_is_not_overwritten(self):
        self.directory.mkdir()
        path = self.directory / 'website.json'
        path.write_text('preserve this exact content')
        with self.assertRaisesRegex(ValueError, 'Partial'):
            configure(self.directory)
        self.assertEqual(path.read_text(), 'preserve this exact content')

    def test_changed_options_do_not_silently_rotate_a_running_database(self):
        configure(self.directory)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'differs'):
            configure(self.directory, database='different_database')
        self.assertEqual(before, self.snapshot())

    def test_a_real_setup_cannot_be_reclassified_as_a_disposable_test(self):
        configure(self.directory)
        with self.assertRaisesRegex(ValueError, 'not a disposable'):
            configure(self.directory, test=True)

    def test_invalid_network_options_create_no_configs(self):
        for address in ('file:///tmp/site', 'http://user:pass@localhost:3000', 'http://localhost:3000/path',
                        'http://localhost:3000/?x=1', 'http://localhost:3000/#fragment', 'http://local host:3000'):
            with self.subTest(address=address), self.assertRaises(ValueError):
                configure(self.directory, address)
        self.assertFalse(self.directory.exists())
        with self.assertRaisesRegex(ValueError, 'distinct'):
            configure(self.directory, frontend_port=3000, backend_port=3000)
        self.assertFalse(self.directory.exists())


class AvatarSchemaTest(unittest.TestCase):
    """The local schema comes from api/migrations, so it has to carry the avatar columns.

    A column that only existed in the original deployment's database makes the website
    fail before it can ask for a render: the avatar redraw threw ``column
    "thumbnail_3d_url" of relation "user_avatar" does not exist`` on its own first
    update, so no render was ever requested and every local avatar stayed pending.
    """

    def setUp(self):
        self.root = Path(__file__).resolve().parents[1]
        self.migrations = '\n'.join(path.read_text()
                                    for path in sorted((self.root / 'api/migrations').glob('*.js')))

    def test_the_columns_the_avatar_model_reads_are_migrated(self):
        model = (self.root / 'Roblox/Roblox.Services/DbModels/Users/Avatar.cs').read_text()
        columns = re.findall(r'public\s+[\w<>?\[\]]+\s+(\w+)\s*\{\s*get', model)
        # Guard the parse itself: the picture columns are the ones a redraw depends on.
        self.assertIn('thumbnail_3d_url', columns)
        missing = [name for name in columns if name not in self.migrations]
        self.assertEqual(missing, [],
                         'the avatar model reads columns no migration creates: ' + repr(missing))
        self.assertIn("hasColumn('user_avatar'", self.migrations)

    def test_the_columns_the_redraw_writes_are_migrated(self):
        source = (self.root / 'Roblox/Roblox.Services/Users/Avatar.cs').read_text()
        columns = set()
        for match in re.finditer(r'UPDATE user_avatar SET', source):
            statement = source[match.end():]
            statement = statement[:statement.index('WHERE user_id')]
            columns.update(re.findall(r'(\w+)\s*=\s*:', statement))
        # Guard the parse itself: every write statement was read, including the picture one.
        self.assertTrue({'thumbnail_url', 'headshot_thumbnail_url', 'thumbnail_3d_url'}
                        <= columns, columns)
        missing = [name for name in columns if name not in self.migrations]
        self.assertEqual(missing, [],
                         'the avatar redraw writes columns no migration creates: ' + repr(missing))


if __name__ == '__main__':
    unittest.main()
