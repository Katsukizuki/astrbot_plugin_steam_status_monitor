"""Web 设置读取时的通知开关默认值补全（与 _conf_schema.json 对齐）。"""

import unittest

from src.presentation.web.settings_defaults import with_notification_defaults


class SettingsDefaultsTests(unittest.TestCase):
    def test_missing_keys_get_schema_defaults(self):
        merged = with_notification_defaults({"steam_api_key": "K"})
        self.assertEqual("K", merged["steam_api_key"])
        self.assertFalse(merged["enable_user_online_notify"])
        self.assertFalse(merged["enable_user_offline_notify"])
        self.assertTrue(merged["notify_send_image"])
        self.assertTrue(merged["notify_send_text"])

    def test_existing_values_are_preserved(self):
        merged = with_notification_defaults({"notify_send_image": False, "notify_send_text": False})
        self.assertFalse(merged["notify_send_image"])
        self.assertFalse(merged["notify_send_text"])

    def test_original_config_not_mutated(self):
        config = {"steam_api_key": "K"}
        with_notification_defaults(config)
        self.assertNotIn("notify_send_image", config)


if __name__ == "__main__":
    unittest.main()
