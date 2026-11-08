import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.plugin.runtime_config import apply_hot_update, apply_runtime_config, migrate_legacy_steam_ids


def test_migrate_legacy_steam_ids_moves_flat_list():
    config = migrate_legacy_steam_ids({"steam_ids": "111,222"})
    assert config["group_steam_ids"] == {"default": ["111", "222"]}
    assert "steam_ids" not in config


def test_apply_runtime_config_sets_runtime_fields():
    plugin = SimpleNamespace()
    config = {
        "steam_api_key": "KEY",
        "group_steam_ids": {"111": ["s1"]},
        "smart_poll_intervals": "1,2,3",
        "rank_push_hour": 9,
        "rank_push_minute": 15,
        "enable_proxy": False,
    }
    with patch("src.plugin.runtime_config.configure_tls"), patch(
        "src.plugin.runtime_config.ITADClient"
    ) as client_cls, patch("src.plugin.runtime_config.register_sensitive_values"):
        apply_runtime_config(plugin, config)

    assert plugin.API_KEY == "KEY"
    assert plugin.group_steam_ids == {"111": ["s1"]}
    assert plugin.smart_poll_intervals == [1, 2, 3]
    assert plugin.config["smart_poll_intervals"] == "1,2,3"
    assert plugin.rank_push_hour == 9
    assert plugin.rank_push_minute == 15
    assert plugin.config["notify_quiet_hours"] == "0,0"
    assert plugin.proxy is None
    client_cls.assert_called_once()


def test_apply_runtime_config_keeps_quiet_hours():
    plugin = SimpleNamespace()
    config = {"notify_quiet_hours": "23,8"}
    with patch("src.plugin.runtime_config.configure_tls"), patch(
        "src.plugin.runtime_config.ITADClient"
    ), patch("src.plugin.runtime_config.register_sensitive_values"):
        apply_runtime_config(plugin, config)

    assert plugin.config["notify_quiet_hours"] == "23,8"


def test_apply_hot_update_parses_intervals_and_ints():
    plugin = SimpleNamespace(
        config={"smart_poll_intervals": "1,3,5", "fixed_poll_interval": 0, "steam_api_key": "K"},
        smart_poll_intervals=[1, 3, 5],
    )

    ok, msg = apply_hot_update(plugin, "smart_poll_intervals", "2,4,6")
    assert ok is True
    assert plugin.smart_poll_intervals == [2, 4, 6]
    assert plugin.config["smart_poll_intervals"] == "2,4,6"
    assert "已设置" in msg

    ok, msg = apply_hot_update(plugin, "fixed_poll_interval", "600")
    assert ok is True
    assert plugin.fixed_poll_interval == 600

    ok, msg = apply_hot_update(plugin, "missing", "1")
    assert ok is False
    assert "无效参数" in msg


def test_apply_hot_update_owned_style_string():
    # 新装用户配置文件里没有该键 → 靠 OWNED_GAMES_HOT_KEYS 白名单放行
    plugin = SimpleNamespace(config={"steam_api_key": "K"})

    ok, msg = apply_hot_update(plugin, "owned_games_notify_style", "compact")

    assert ok is True
    assert plugin.config["owned_games_notify_style"] == "compact"
    assert isinstance(plugin.config["owned_games_notify_style"], str)
    assert "已设置" in msg


def test_apply_hot_update_owned_compact_ints():
    plugin = SimpleNamespace(config={"steam_api_key": "K"})

    ok, _ = apply_hot_update(plugin, "owned_games_compact_page_size", "12")

    assert ok is True
    assert plugin.config["owned_games_compact_page_size"] == 12
    assert isinstance(plugin.config["owned_games_compact_page_size"], int)


def test_apply_hot_update_owned_compact_non_int_rejected():
    plugin = SimpleNamespace(config={"steam_api_key": "K"})

    ok, msg = apply_hot_update(plugin, "owned_games_compact_max_pages", "abc")

    assert ok is False
    assert "类型错误" in msg
    assert "owned_games_compact_max_pages" not in plugin.config


class NotificationBoolHotUpdateTests(unittest.TestCase):
    """上下线与图文开关的热更新：布尔语义、缺键放行、非法值拒绝。"""

    def test_presence_bool_missing_key_is_allowed(self):
        # 老配置没有该键：白名单放行，按布尔解析
        plugin = SimpleNamespace(config={})
        ok, _ = apply_hot_update(plugin, "enable_user_online_notify", "true")
        self.assertTrue(ok)
        self.assertIs(plugin.config["enable_user_online_notify"], True)

    def test_bool_false_words(self):
        for word in ("false", "0", "off", "no", "关闭"):
            with self.subTest(word=word):
                plugin = SimpleNamespace(config={})
                ok, _ = apply_hot_update(plugin, "notify_send_image", word)
                self.assertTrue(ok)
                self.assertIs(plugin.config["notify_send_image"], False)

    def test_invalid_bool_rejected(self):
        plugin = SimpleNamespace(config={})
        ok, msg = apply_hot_update(plugin, "notify_send_text", "abc")
        self.assertFalse(ok)
        self.assertIn("类型错误", msg)
        self.assertNotIn("notify_send_text", plugin.config)

    def test_existing_true_value_still_switches_off(self):
        plugin = SimpleNamespace(config={"enable_user_offline_notify": True})
        ok, _ = apply_hot_update(plugin, "enable_user_offline_notify", "false")
        self.assertTrue(ok)
        self.assertIs(plugin.config["enable_user_offline_notify"], False)
