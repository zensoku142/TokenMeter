import json
import os
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

import config_manager
from config import credentials


class ConfigTests(unittest.TestCase):
    def test_initialize_is_idempotent(self):
        original_initialized = config_manager._initialized
        original_state = config_manager._location_state
        original_dir = config_manager.CONFIG_DIR
        try:
            config_manager._initialized = False
            active = Path.cwd() / ".test-appdata" / "initialized"
            with (
                patch.object(
                    config_manager,
                    "_initialize_data_dir",
                    return_value=(active, {"data_dir": str(active)}),
                ) as initialize_dir,
                patch.object(config_manager, "logger") as initialize_logger,
                patch.object(config_manager, "load_config") as load_config,
            ):
                config_manager.initialize()
                config_manager.initialize()

            initialize_dir.assert_called_once_with()
            initialize_logger.assert_called_once_with()
            load_config.assert_called_once_with()
            self.assertEqual(config_manager.CONFIG_DIR, active)
        finally:
            config_manager._set_runtime_paths(original_dir)
            config_manager._location_state = original_state
            config_manager._initialized = original_initialized

    def test_legacy_data_directory_and_new_credential_prefix(self):
        self.assertEqual(config_manager.DEFAULT_CONFIG_DIR.name, "TokenSpider")
        self.assertEqual(
            config_manager._credential_target("DEEPSEEK_API_KEY"),
            "TokenMeter/DEEPSEEK_API_KEY",
        )

    def test_credentials_fall_back_in_order_and_copy_forward(self):
        with (
            patch.object(credentials.os, "name", "nt"),
            patch.object(
                credentials,
                "read_credential_target",
                side_effect=["", "legacy-secret"],
            ) as read_target,
            patch.object(credentials, "write_credential") as write_credential,
        ):
            self.assertEqual(credentials.read_credential("MIMO_COOKIE"), "legacy-secret")

        self.assertEqual(
            [call.args[0] for call in read_target.call_args_list],
            ["TokenMeter/MIMO_COOKIE", "TokenSpider/MIMO_COOKIE"],
        )
        write_credential.assert_called_once_with("MIMO_COOKIE", "legacy-secret")

    def test_e2e_mode_does_not_read_real_credentials(self):
        with (
            patch.dict(
                credentials.os.environ,
                {"TOKENMETER_E2E_DISABLE_CREDENTIALS": "1"},
            ),
            patch.object(credentials, "read_credential_target") as read_target,
        ):
            self.assertEqual(credentials.read_credential("MIMO_COOKIE"), "")

        read_target.assert_not_called()

    def test_credential_copy_failure_still_returns_legacy_value(self):
        with (
            patch.object(credentials.os, "name", "nt"),
            patch.object(
                credentials,
                "read_credential_target",
                side_effect=["", "", "scope-secret"],
            ),
            patch.object(credentials, "write_credential", side_effect=OSError),
        ):
            self.assertEqual(credentials.read_credential("DEEPSEEK_AUTH"), "scope-secret")

    def test_deepseek_peak_pricing_defaults_and_period_validation(self):
        defaults = config_manager.validate_config({})
        self.assertFalse(defaults["DEEPSEEK_PEAK_PRICING_ENABLED"])
        self.assertEqual(defaults["DEEPSEEK_PEAK_PERIOD_1_START"], "09:00")
        self.assertEqual(defaults["DEEPSEEK_PEAK_PERIOD_2_END"], "18:00")
        enabled = config_manager.validate_config(
            {"DEEPSEEK_PEAK_PRICING_ENABLED": "true"}
        )
        self.assertTrue(enabled["DEEPSEEK_PEAK_PRICING_ENABLED"])

        with self.assertRaisesRegex(ValueError, "HH:mm"):
            config_manager.validate_config({"DEEPSEEK_PEAK_PERIOD_1_START": "9:00"})
        with self.assertRaisesRegex(ValueError, "开始时间必须早于结束时间"):
            config_manager.validate_config(
                {
                    "DEEPSEEK_PEAK_PERIOD_1_START": "12:00",
                    "DEEPSEEK_PEAK_PERIOD_1_END": "12:00",
                }
            )
        with self.assertRaisesRegex(ValueError, "不能重叠"):
            config_manager.validate_config(
                {
                    "DEEPSEEK_PEAK_PERIOD_1_END": "14:01",
                    "DEEPSEEK_PEAK_PERIOD_2_START": "14:00",
                }
            )
        adjacent = config_manager.validate_config(
            {
                "DEEPSEEK_PEAK_PERIOD_1_END": "14:00",
                "DEEPSEEK_PEAK_PERIOD_2_START": "14:00",
            }
        )
        self.assertEqual(adjacent["DEEPSEEK_PEAK_PERIOD_2_START"], "14:00")

    def test_data_directory_migration_copies_all_entries_and_keeps_source(self):
        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            root = Path(directory)
            source = root / "source"
            target = root / "target"
            source.mkdir()
            target.mkdir()
            (source / "config.json").write_text("{}", encoding="utf-8")
            with closing(sqlite3.connect(source / "usage.db")) as connection:
                connection.execute("CREATE TABLE sample (value TEXT)")
                connection.commit()
            profile = source / "mimo-chrome" / "Default"
            profile.mkdir(parents=True)
            (profile / "Cookies").write_bytes(b"cookies")

            with patch.object(config_manager, "DEFAULT_CONFIG_DIR", root / "default"):
                config_manager._migrate_data_dir(source, target)

            self.assertEqual((target / "config.json").read_text(encoding="utf-8"), "{}")
            with closing(sqlite3.connect(target / "usage.db")) as connection:
                self.assertEqual(connection.execute("PRAGMA quick_check").fetchone(), ("ok",))
            self.assertEqual(
                (target / "mimo-chrome" / "Default" / "Cookies").read_bytes(),
                b"cookies",
            )
            self.assertTrue((source / "config.json").exists())
            self.assertTrue((source / "usage.db").exists())
            self.assertTrue((source / "mimo-chrome" / "Default" / "Cookies").exists())

    def test_custom_data_directory_must_be_empty(self):
        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            root = Path(directory)
            current = root / "current"
            target = root / "target"
            current.mkdir()
            target.mkdir()
            (target / "existing.txt").write_text("keep", encoding="utf-8")

            with (
                patch.object(config_manager, "CONFIG_DIR", current),
                patch.object(config_manager, "DEFAULT_CONFIG_DIR", root / "default"),
            ):
                with self.assertRaisesRegex(ValueError, "必须为空"):
                    config_manager.validate_data_dir_target(target)

            self.assertEqual((target / "existing.txt").read_text(encoding="utf-8"), "keep")

    def test_scheduling_data_directory_change_writes_bootstrap_pointer(self):
        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            root = Path(directory)
            current = root / "current"
            target = root / "target"
            default = root / "default"
            location_path = default / "location.json"
            current.mkdir()
            target.mkdir()
            old_state = config_manager._location_state
            try:
                with (
                    patch.object(config_manager, "CONFIG_DIR", current),
                    patch.object(config_manager, "DEFAULT_CONFIG_DIR", default),
                    patch.object(config_manager, "LOCATION_PATH", location_path),
                ):
                    config_manager._location_state = {"data_dir": str(current)}
                    self.assertTrue(config_manager.schedule_data_dir_change(target))
                    state = json.loads(location_path.read_text(encoding="utf-8"))

                self.assertEqual(state["version"], 1)
                self.assertEqual(Path(state["data_dir"]), current.resolve())
                self.assertEqual(Path(state["pending_data_dir"]), target.resolve())
            finally:
                config_manager._location_state = old_state

    def test_startup_applies_pending_data_directory_only_after_copy(self):
        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            root = Path(directory)
            default = root / "default"
            source = root / "source"
            target = root / "target"
            default.mkdir()
            source.mkdir()
            target.mkdir()
            with closing(sqlite3.connect(source / "usage.db")) as connection:
                connection.execute("CREATE TABLE sample (value TEXT)")
                connection.commit()
            location_path = default / "location.json"
            location_path.write_text(
                json.dumps(
                    {"data_dir": str(source), "pending_data_dir": str(target)}
                ),
                encoding="utf-8",
            )

            with (
                patch.object(config_manager, "DEFAULT_CONFIG_DIR", default),
                patch.object(config_manager, "LOCATION_PATH", location_path),
                patch.object(config_manager, "_another_instance_running", return_value=False),
            ):
                active, state = config_manager._initialize_data_dir()

            saved_state = json.loads(location_path.read_text(encoding="utf-8"))
            self.assertEqual(active, target.resolve())
            self.assertEqual(state, {"data_dir": str(target.resolve())})
            self.assertNotIn("pending_data_dir", saved_state)
            self.assertTrue((target / "usage.db").exists())
            self.assertTrue(source.exists())

    def test_startup_keeps_old_directory_when_migration_fails(self):
        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            root = Path(directory)
            default = root / "default"
            source = root / "source"
            target = root / "target"
            default.mkdir()
            source.mkdir()
            target.mkdir()
            (source / "config.json").write_text("source", encoding="utf-8")
            (target / "existing.txt").write_text("keep", encoding="utf-8")
            location_path = default / "location.json"
            location_path.write_text(
                json.dumps(
                    {"data_dir": str(source), "pending_data_dir": str(target)}
                ),
                encoding="utf-8",
            )

            with (
                patch.object(config_manager, "DEFAULT_CONFIG_DIR", default),
                patch.object(config_manager, "LOCATION_PATH", location_path),
                patch.object(config_manager, "_another_instance_running", return_value=False),
            ):
                active, state = config_manager._initialize_data_dir()

            self.assertEqual(active, source.resolve())
            self.assertEqual(state["data_dir"], str(source.resolve()))
            self.assertTrue(state["migration_error"])
            self.assertEqual((source / "config.json").read_text(encoding="utf-8"), "source")
            self.assertEqual((target / "existing.txt").read_text(encoding="utf-8"), "keep")

    def test_startup_does_not_apply_pending_directory_while_instance_is_running(self):
        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            root = Path(directory)
            default = root / "default"
            source = root / "source"
            target = root / "target"
            default.mkdir()
            source.mkdir()
            target.mkdir()
            (source / "config.json").write_text("{}", encoding="utf-8")
            location_path = default / "location.json"
            location_path.write_text(
                json.dumps(
                    {"data_dir": str(source), "pending_data_dir": str(target)}
                ),
                encoding="utf-8",
            )

            with (
                patch.object(config_manager, "DEFAULT_CONFIG_DIR", default),
                patch.object(config_manager, "LOCATION_PATH", location_path),
                patch.object(config_manager, "_another_instance_running", return_value=True),
                patch.object(config_manager, "_migrate_data_dir") as migrate,
            ):
                active, state = config_manager._initialize_data_dir()

            migrate.assert_not_called()
            self.assertEqual(active, source.resolve())
            self.assertEqual(state["data_dir"], str(source.resolve()))
            saved_state = json.loads(location_path.read_text(encoding="utf-8"))
            self.assertEqual(saved_state["pending_data_dir"], str(target))
            self.assertFalse(any(target.iterdir()))

    def test_data_directory_rejects_relative_unc_and_nested_paths(self):
        with self.assertRaisesRegex(ValueError, "绝对路径"):
            config_manager._normalize_data_dir("relative/path")
        with self.assertRaisesRegex(ValueError, "网络共享"):
            config_manager._normalize_data_dir(r"\\server\share")

        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        current = temp_root / "current"
        nested = current / "nested"
        with patch.object(config_manager, "CONFIG_DIR", current.resolve()):
            with self.assertRaisesRegex(ValueError, "不能互相包含"):
                config_manager.validate_data_dir_target(nested)

    def test_boolean_and_provider_values_are_validated(self):
        self.assertEqual(
            config_manager.validate_config({})["MINUTE_USAGE_CHART_TYPE"],
            "bar",
        )
        self.assertEqual(
            config_manager.validate_config({"MINUTE_USAGE_CHART_TYPE": "LINE"})[
                "MINUTE_USAGE_CHART_TYPE"
            ],
            "line",
        )
        with self.assertRaises(ValueError):
            config_manager.validate_config({"MINUTE_USAGE_CHART_TYPE": "area"})
        self.assertEqual(
            config_manager.validate_config({})["MINUTE_USAGE_INTERVAL_MINUTES"],
            5,
        )
        self.assertEqual(
            config_manager.validate_config({"MINUTE_USAGE_INTERVAL_MINUTES": 60})[
                "MINUTE_USAGE_INTERVAL_MINUTES"
            ],
            60,
        )
        with self.assertRaisesRegex(ValueError, "不能小于"):
            config_manager.validate_config({"MINUTE_USAGE_INTERVAL_MINUTES": 0})
        with self.assertRaisesRegex(ValueError, "不能大于"):
            config_manager.validate_config({"MINUTE_USAGE_INTERVAL_MINUTES": 61})
        self.assertEqual(
            config_manager.validate_config({"MINUTE_USAGE_RETENTION_DAYS": 3})[
                "MINUTE_USAGE_RETENTION_DAYS"
            ],
            3,
        )
        with self.assertRaisesRegex(ValueError, "不能小于"):
            config_manager.validate_config({"MINUTE_USAGE_RETENTION_DAYS": 0})
        self.assertFalse(
            config_manager.validate_config({"EDGE_HIDE_ENABLED": "false"})[
                "EDGE_HIDE_ENABLED"
            ]
        )
        self.assertTrue(
            config_manager.validate_config({})[
                "PANEL_AUTO_COLLAPSE_ON_DEACTIVATE"
            ]
        )
        self.assertFalse(
            config_manager.validate_config(
                {"PANEL_AUTO_COLLAPSE_ON_DEACTIVATE": "false"}
            )["PANEL_AUTO_COLLAPSE_ON_DEACTIVATE"]
        )
        self.assertEqual(
            config_manager.validate_config({"UPDATE_CHANNEL": "prerelease"})["UPDATE_CHANNEL"],
            "prerelease",
        )
        with self.assertRaises(ValueError):
            config_manager.validate_config({"ACTIVE_PROVIDER": "unknown"})
        with self.assertRaises(ValueError):
            config_manager.validate_config({"UPDATE_CHANNEL": "nightly"})

    def test_ui_theme_values_are_validated_and_default_to_dark(self):
        self.assertEqual(config_manager.validate_config({})["UI_THEME"], "dark")
        for mode in ("system", "light", "dark"):
            self.assertEqual(
                config_manager.validate_config({"UI_THEME": mode})["UI_THEME"],
                mode,
            )
        self.assertEqual(
            config_manager.validate_config({"UI_THEME": " LIGHT "})["UI_THEME"],
            "light",
        )
        with self.assertRaises(ValueError):
            config_manager.validate_config({"UI_THEME": "sepia"})

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

            self.assertEqual(values["WIDGET_COMPACT_SIZE"], 88)

            config_path.write_text(
                json.dumps({"WIDGET_COMPACT_SIZE": 108}), encoding="utf-8"
            )
            with patch.object(config_manager, "CONFIG_PATH", config_path):
                previous_values = config_manager._load_public_config()

            self.assertEqual(previous_values["WIDGET_COMPACT_SIZE"], 88)

            config_path.write_text(
                json.dumps({"WIDGET_COMPACT_SIZE": 96}), encoding="utf-8"
            )
            with patch.object(config_manager, "CONFIG_PATH", config_path):
                latest_default = config_manager._load_public_config()

            self.assertEqual(latest_default["WIDGET_COMPACT_SIZE"], 88)

            config_path.write_text(
                json.dumps({"WIDGET_COMPACT_SIZE": 112}), encoding="utf-8"
            )
            with patch.object(config_manager, "CONFIG_PATH", config_path):
                custom_values = config_manager._load_public_config()

            self.assertEqual(custom_values["WIDGET_COMPACT_SIZE"], 112)

    def test_panel_auto_collapse_setting_round_trips(self):
        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            root = Path(directory)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(config_manager._public_values(config_manager.DEFAULT_CONFIG)),
                encoding="utf-8",
            )
            old_config = config_manager._config
            try:
                with (
                    patch.object(config_manager, "CONFIG_DIR", root),
                    patch.object(config_manager, "CONFIG_PATH", config_path),
                    patch.object(config_manager, "_write_credential"),
                    patch.object(config_manager, "_read_credential", return_value=""),
                ):
                    config_manager._config = config_manager.DEFAULT_CONFIG.copy()
                    saved = config_manager.save_config(
                        {"PANEL_AUTO_COLLAPSE_ON_DEACTIVATE": False}
                    )
                    loaded = config_manager.load_config()

                self.assertFalse(saved["PANEL_AUTO_COLLAPSE_ON_DEACTIVATE"])
                self.assertFalse(loaded["PANEL_AUTO_COLLAPSE_ON_DEACTIVATE"])
            finally:
                config_manager._config = old_config

    def test_save_ui_theme_only_replaces_public_preference(self):
        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            root = Path(directory)
            config_path = root / "config.json"
            disk_values = config_manager._public_values(config_manager.DEFAULT_CONFIG)
            disk_values["REFRESH_INTERVAL"] = 75_000
            config_path.write_text(json.dumps(disk_values), encoding="utf-8")
            old_config = config_manager._config
            try:
                draft = config_manager.DEFAULT_CONFIG.copy()
                draft["REFRESH_INTERVAL"] = 5_000
                draft["DEEPSEEK_API_KEY"] = "connection-test-draft"
                with (
                    patch.object(config_manager, "CONFIG_PATH", config_path),
                    patch.object(config_manager, "_config", draft),
                    patch.object(config_manager, "_write_credential") as write_credential,
                ):
                    self.assertEqual(config_manager.save_ui_theme("light"), "light")
                    saved = json.loads(config_path.read_text(encoding="utf-8"))

                    write_credential.assert_not_called()
                    self.assertEqual(saved["UI_THEME"], "light")
                    self.assertEqual(saved["REFRESH_INTERVAL"], 75_000)
                    self.assertNotIn("DEEPSEEK_API_KEY", saved)
                    self.assertEqual(
                        config_manager._config["DEEPSEEK_API_KEY"],
                        "connection-test-draft",
                    )
                    self.assertEqual(config_manager._config["UI_THEME"], "light")
            finally:
                config_manager._config = old_config

    def test_save_ui_theme_failure_keeps_existing_public_config(self):
        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            config_path = Path(directory) / "config.json"
            original = config_manager._public_values(config_manager.DEFAULT_CONFIG)
            config_path.write_text(json.dumps(original), encoding="utf-8")
            original_text = config_path.read_text(encoding="utf-8")

            def fail_after_temp_write(path: Path, values: dict) -> None:
                path.write_text(json.dumps(values), encoding="utf-8")
                raise OSError("simulated replace preparation failure")

            with (
                patch.object(config_manager, "CONFIG_PATH", config_path),
                patch.object(config_manager, "_write_json", side_effect=fail_after_temp_write),
                patch.object(config_manager, "logger"),
            ):
                with self.assertRaises(OSError):
                    config_manager.save_ui_theme("system")

            self.assertEqual(config_path.read_text(encoding="utf-8"), original_text)
            self.assertFalse(config_path.with_name("config.json.theme.tmp").exists())

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

    def test_panel_layout_state_round_trips_separately(self):
        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            layout_path = Path(directory) / "panel-layout.json"
            payload = {
                "sections": ["bottom", "top", "middle"],
                "top_cards": ["month", "today", "balance"],
                "bottom_cards": ["statistics", "trend"],
            }

            with patch.object(config_manager, "PANEL_LAYOUT_PATH", layout_path):
                config_manager.save_panel_layout_state(payload)
                self.assertEqual(config_manager.load_panel_layout_state(), payload)

                layout_path.write_text("[]", encoding="utf-8")
                self.assertEqual(config_manager.load_panel_layout_state(), {})

    def test_update_state_round_trips_separately(self):
        temp_root = Path.cwd() / ".test-appdata" / "tmp"
        temp_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temp_root) as directory:
            state_path = Path(directory) / "update-state.json"
            cleanup_path = Path(directory) / "pending-update-cleanup.json"
            payload = {"latest_version": "1.3.0", "last_checked_at": "2026-07-06T00:00:00+00:00"}
            cleanup_payload = {"version": 1, "cleanup_paths": ["C:/tmp/demo"]}

            with (
                patch.object(config_manager, "UPDATE_STATE_PATH", state_path),
                patch.object(config_manager, "PENDING_UPDATE_CLEANUP_PATH", cleanup_path),
            ):
                config_manager.save_update_state(payload)
                self.assertEqual(config_manager.load_update_state(), payload)
                config_manager.save_pending_update_cleanup(cleanup_payload)
                self.assertEqual(config_manager.load_pending_update_cleanup(), cleanup_payload)
                config_manager.clear_pending_update_cleanup()
                self.assertEqual(config_manager.load_pending_update_cleanup(), {})


if __name__ == "__main__":
    unittest.main()
