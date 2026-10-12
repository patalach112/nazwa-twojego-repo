"""Check Docker config preservation without requiring a daemon or .NET."""
import json
import os
from pathlib import Path
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


if __name__ == '__main__':
    unittest.main()
