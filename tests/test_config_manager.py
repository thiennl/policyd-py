import os
import tempfile
import unittest

from policyd_py.management.config_manager import ConfigManager


class ConfigManagerTests(unittest.TestCase):
    def test_save_updates_and_reload(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "config.ini")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(
                    """
[General]
debug = false

[Web]
enable = false
port = 8080
"""
                )

            manager = ConfigManager(path)
            cfg1 = manager.get_config()
            self.assertFalse(cfg1.general.debug)
            self.assertFalse(cfg1.web.enable)

            cfg2 = manager.save(
                updates={
                    "General": {"debug": True},
                    "Web": {"enable": True, "port": 9090},
                }
            )
            self.assertTrue(cfg2.general.debug)
            self.assertTrue(cfg2.web.enable)
            self.assertEqual(cfg2.web.port, 9090)

            reloaded = manager.reload()
            self.assertTrue(reloaded.general.debug)
            self.assertTrue(reloaded.web.enable)
            self.assertEqual(reloaded.web.port, 9090)

    def test_invalid_content_does_not_replace_active_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = os.path.join(temp_dir, "config.ini")
            original = """
[General]
debug = false

[Web]
enable = false
port = 8080
"""
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(original)

            manager = ConfigManager(path)

            with self.assertRaises(Exception):
                manager.save(content="[General]\nsocket_permission = definitely-not-octal\n")

            with open(path, "r", encoding="utf-8") as handle:
                current = handle.read()
            self.assertEqual(current, original)

            cfg = manager.reload()
            self.assertFalse(cfg.general.debug)
            self.assertFalse(cfg.web.enable)
            self.assertEqual(cfg.web.port, 8080)


if __name__ == "__main__":
    unittest.main()
