import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

import config_manager


class ConfigTests(unittest.TestCase):
    def test_boolean_and_provider_values_are_validated(self):
        self.assertFalse(
            config_manager.validate_config({"EDGE_HIDE_ENABLED": "false"})[
                "EDGE_HIDE_ENABLED"
            ]
        )
        with self.assertRaises(ValueError):
            config_manager.validate_config({"ACTIVE_PROVIDER": "unknown"})

    def test_legacy_default_compact_size_is_migrated(self):
        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(
                json.dumps({"WIDGET_COMPACT_SIZE": 120}), encoding="utf-8"
            )
            with patch.object(config_manager, "CONFIG_PATH", config_path):
                values = config_manager._load_public_config()

            self.assertEqual(values["WIDGET_COMPACT_SIZE"], 96)

            config_path.write_text(
                json.dumps({"WIDGET_COMPACT_SIZE": 108}), encoding="utf-8"
            )
            with patch.object(config_manager, "CONFIG_PATH", config_path):
                previous_values = config_manager._load_public_config()

            self.assertEqual(previous_values["WIDGET_COMPACT_SIZE"], 96)

            config_path.write_text(
                json.dumps({"WIDGET_COMPACT_SIZE": 112}), encoding="utf-8"
            )
            with patch.object(config_manager, "CONFIG_PATH", config_path):
                custom_values = config_manager._load_public_config()

            self.assertEqual(custom_values["WIDGET_COMPACT_SIZE"], 112)

    def test_backups_exclude_secrets_and_are_limited(self):
        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            root = Path(directory)
            config_path = root / "config.json"
            old_config = config_manager._config
            values = config_manager.validate_config({
                "DEEPSEEK_API_KEY": "secret-api-key",
                "DEEPSEEK_AUTH": "secret-auth",
                "DEEPSEEK_COOKIE": "secret-cookie",
            })
            public = config_manager._public_values(values)
            config_path.write_text(json.dumps(public), encoding="utf-8")
            try:
                with (
                    patch.object(config_manager, "CONFIG_DIR", root),
                    patch.object(config_manager, "CONFIG_PATH", config_path),
                    patch.object(config_manager, "_config", values),
                    patch.object(config_manager, "_write_credential"),
                ):
                    for interval in range(5):
                        config_manager.save_config({"REFRESH_INTERVAL": 60_000 + interval})
                    files = list(root.glob("config.json.bak-*"))
                    self.assertEqual(len(files), 3)
                    content = "\n".join(path.read_text(encoding="utf-8") for path in files)
                    self.assertNotIn("secret-api-key", content)
                    self.assertNotIn("secret-auth", content)
                    self.assertNotIn("secret-cookie", content)
            finally:
                config_manager._config = old_config


if __name__ == "__main__":
    unittest.main()
