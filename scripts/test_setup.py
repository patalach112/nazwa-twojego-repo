"""Exercise setup in a small temporary checkout, without databases or RCC."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SOURCE = Path(__file__).resolve().parents[1]
WEBSITE = Path("Roblox/Roblox.Website")
CONFIGS = (
    Path("api/config.json"),
    WEBSITE / "appsettings.json",
    WEBSITE / "local-secrets.json",
    WEBSITE / "game-servers.json",
    Path("game-arbiter/config.json"),
    Path("game-server/config.json"),
    Path("frontend/config.json"),
)


class SetupTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pekora setup test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in (
            "scripts/setup.sh",
            "scripts/provision_website_secrets.py",
            "Roblox/Roblox.Website/appsettings.example.json",
            "Roblox/Roblox.Website/game-servers.example.json",
            "game-arbiter/config.example.json",
            "game-server/config.example.json",
            "frontend/util/create_config.js",
        ):
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(SOURCE / name, target)
        (self.root / "api").mkdir()
        self.env = os.environ.copy()
        self.env.update({
            "DB_HOST": "127.0.0.1", "DB_PORT": "5432", "DB_USER": "postgres",
            "DB_PASSWORD": "test-database-password", "DB_NAME": "pekora",
            "REDIS": "127.0.0.1:6379", "BASE_URL": "http://localhost:5000",
            "ARBITER_BASE_URL": "http://127.0.0.1:4310/",
        })

    def run_setup(self, **overrides):
        return subprocess.run(
            ["bash", str(self.root / "scripts/setup.sh")],
            cwd=self.root / "scripts",  # not the repo root
            env={**self.env, **overrides}, capture_output=True, text=True, timeout=30,
        )

    def read(self, name):
        return json.loads((self.root / name).read_text())

    def write(self, name, value):
        (self.root / name).write_text(json.dumps(value, indent=2) + "\n")

    def success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def snapshot(self):
        return {name: (self.root / name).read_bytes() for name in CONFIGS}

    def test_fresh_clone_gets_matching_secrets_and_absolute_paths(self):
        result = self.run_setup()
        self.success(result)
        website = self.read(WEBSITE / "appsettings.json")
        arbiter = self.read("game-arbiter/config.json")
        server = self.read("game-server/config.json")
        self.assertEqual(arbiter["authorization"], website["ArbiterAuthorization"])
        self.assertEqual(server["authorization"], website["GameServerAuthorization"])
        self.assertEqual(server["websiteBotAuth"], website["BotAuthorization"])
        secrets = [website[key] for key in ("ArbiterAuthorization", "GameServerAuthorization", "BotAuthorization")]
        secrets += [website[key] for key in ("RccAuthorization", "Authorization", "VerificationSecret", "UserAgentBypassSecret")]
        jwt = self.read(WEBSITE / "local-secrets.json")["Jwt"]
        secrets += [jwt["Sessions"], jwt["Games"]]
        self.assertEqual(len(set(secrets)), 9)
        frontend = self.read("frontend/config.json")
        self.assertEqual(frontend["serverRuntimeConfig"]["backend"]["authorization"], website["Authorization"])
        for secret in secrets:
            self.assertNotIn(secret, json.dumps(frontend["publicRuntimeConfig"]))
        for secret in secrets:
            self.assertRegex(secret, r"^[0-9a-f]{64}$")
            self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertNotIn(self.env["DB_PASSWORD"], result.stdout + result.stderr)
        self.assertEqual(website["ArbiterBaseUrl"], "http://127.0.0.1:4310/")
        self.assertEqual(server["baseUrl"], website["BaseUrl"])
        for value in website["Directories"].values():
            self.assertTrue(Path(value).is_absolute())
            self.assertTrue(value.endswith(os.sep))
        self.assertEqual(server["rcc"], str(self.root / "RCCService") + os.sep)
        self.assertEqual(server["content"], str(self.root / "RCCService/content") + os.sep)
        self.assertEqual(arbiter["rccCwd"], server["rcc"])
        for name in CONFIGS:
            self.assertTrue((self.root / name).is_file())
            if os.name == "posix":
                self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o600)
        for name in ("assets", "storage", "thumbnails", "group-icons", "economy-chat", "rcc-lua"):
            self.assertTrue((self.root / "data" / name).is_dir())

    def test_rerun_leaves_every_existing_config_byte_for_byte(self):
        self.success(self.run_setup())
        before = self.snapshot()
        self.success(self.run_setup(DB_PASSWORD="different", BASE_URL="http://other-host:5000"))
        self.assertEqual(before, self.snapshot())

    def test_new_services_use_existing_website_secrets_and_url(self):
        website = self.read(WEBSITE / "appsettings.example.json")
        website.update({
            "ArbiterAuthorization": "existing-arbiter-secret", "GameServerAuthorization": "existing-game-secret",
            "BotAuthorization": "existing-bot-secret", "BaseUrl": "https://local.example/", "OwnerUserId": [99],
        })
        self.write(WEBSITE / "appsettings.json", website)
        before = (self.root / WEBSITE / "appsettings.json").read_bytes()
        self.success(self.run_setup())
        self.assertEqual(before, (self.root / WEBSITE / "appsettings.json").read_bytes())
        self.assertEqual(self.read("game-arbiter/config.json")["authorization"], website["ArbiterAuthorization"])
        server = self.read("game-server/config.json")
        self.assertEqual(server["authorization"], website["GameServerAuthorization"])
        self.assertEqual(server["websiteBotAuth"], website["BotAuthorization"])
        self.assertEqual(server["baseUrl"], "https://local.example")

    def test_missing_service_config_reuses_secrets_without_rotating_them(self):
        self.success(self.run_setup())
        before = self.snapshot()
        for name in ("game-arbiter/config.json", "game-server/config.json"):
            (self.root / name).unlink()
        self.success(self.run_setup())
        self.assertEqual(before, self.snapshot())

    def test_example_arbiter_secret_is_rejected_without_changing_the_website(self):
        website = self.read(WEBSITE / "appsettings.example.json")
        self.write(WEBSITE / "appsettings.json", website)
        before = (self.root / WEBSITE / "appsettings.json").read_bytes()
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("example ArbiterAuthorization", result.stderr)
        self.assertEqual(before, (self.root / WEBSITE / "appsettings.json").read_bytes())
        self.assertFalse((self.root / "game-arbiter/config.json").exists())

    def test_conflicting_arbiter_secret_is_reported_and_not_overwritten(self):
        self.success(self.run_setup())
        arbiter = self.read("game-arbiter/config.json")
        arbiter["authorization"] = "do-not-overwrite-this-secret"
        self.write("game-arbiter/config.json", arbiter)
        before = self.snapshot()
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("authorization does not match", result.stderr)
        self.assertNotIn(arbiter["authorization"], result.stdout + result.stderr)
        self.assertEqual(before, self.snapshot())

    def test_conflicting_game_server_secrets_are_reported_and_not_overwritten(self):
        self.success(self.run_setup())
        original = self.read("game-server/config.json")
        for key in ("authorization", "websiteBotAuth"):
            with self.subTest(key=key):
                server = {**original, key: "keep-this-existing-value"}
                self.write("game-server/config.json", server)
                before = self.snapshot()
                result = self.run_setup()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key + " does not match", result.stderr)
                self.assertEqual(before, self.snapshot())

    def test_existing_jwt_keys_are_not_rotated_or_shadowed(self):
        website = self.read(WEBSITE / "appsettings.example.json")
        website.update({"ArbiterAuthorization": "existing-arbiter-secret", "GameServerAuthorization": "existing-game-secret", "BotAuthorization": "existing-bot-secret"})
        website["JWT"] = {"sessions": "a" * 64, "games": "b" * 64}
        website.pop("Jwt")
        self.write(WEBSITE / "appsettings.json", website)
        before = (self.root / WEBSITE / "appsettings.json").read_bytes()
        self.success(self.run_setup())
        self.assertEqual(before, (self.root / WEBSITE / "appsettings.json").read_bytes())
        self.assertFalse((self.root / WEBSITE / "local-secrets.json").exists())

    def test_incomplete_existing_secrets_fail_without_rewriting_them(self):
        self.success(self.run_setup())
        self.write(WEBSITE / "local-secrets.json", {"Jwt": {"Sessions": "private-existing-session-key-" + "x" * 32}})
        before = self.snapshot()
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing Jwt:Games", result.stderr)
        self.assertEqual(before, self.snapshot())

    def test_conflicting_jwt_keys_fail_without_losing_existing_sessions(self):
        self.success(self.run_setup())
        website = self.read(WEBSITE / "appsettings.json")
        website["Jwt"]["Sessions"] = "a" * 64
        self.write(WEBSITE / "appsettings.json", website)
        before = self.snapshot()
        result = self.run_setup()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("conflicts with appsettings.json", result.stderr)
        self.assertEqual(before, self.snapshot())

    def test_connection_overrides_reach_both_website_and_migrations(self):
        self.success(self.run_setup(
            DB_HOST="database.internal", DB_PORT="6543", DB_USER="pekora_user",
            DB_PASSWORD="private-password", DB_NAME="pekora_test", REDIS="redis.internal:6380",
            BASE_URL="https://website.example", ARBITER_BASE_URL="http://arbiter.internal:4310/",
        ))
        website = self.read(WEBSITE / "appsettings.json")
        connection = self.read("api/config.json")["knex"]["connection"]
        self.assertEqual(connection, {"host": "database.internal", "port": 6543, "user": "pekora_user", "password": "private-password", "database": "pekora_test"})
        self.assertEqual(website["Postgres"], "Host=database.internal;Port=6543;Database=pekora_test;Username=pekora_user;Password=private-password")
        self.assertEqual(website["Redis"], "redis.internal:6380")
        self.assertEqual(website["ArbiterBaseUrl"], "http://arbiter.internal:4310/")
        frontend = self.read("frontend/config.json")["publicRuntimeConfig"]["backend"]
        self.assertEqual(frontend["baseUrl"], "https://website.example")
        self.assertEqual(frontend["apiFormat"], "https://website.example/apisite/{0}{1}")
        self.assertEqual(self.read("game-server/config.json")["baseUrl"], website["BaseUrl"])


if __name__ == "__main__":
    unittest.main()
