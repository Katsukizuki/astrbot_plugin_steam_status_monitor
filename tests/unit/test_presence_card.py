"""上下线状态卡渲染：PNG 输出契约、尺寸、默认头像占位与文本截断保护。"""

import io
import os
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image

from src.presentation.renderers import presence_card
from src.presentation.renderers.presence_card import render_presence_card_image


class PresenceCardRenderTests(unittest.TestCase):
    @staticmethod
    def _render(kind, **kwargs):
        img = render_presence_card_image("测试玩家", kind, event_time=1700000000, version="9.9.9", **kwargs)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_online_card_png_contract(self):
        data = self._render("online")
        self.assertTrue(data.startswith(b"\x89PNG"))
        with Image.open(io.BytesIO(data)) as img:
            self.assertEqual((512, 192), img.size)

    def test_offline_card_png_contract(self):
        data = self._render("offline", online_minutes=133.0)
        self.assertTrue(data.startswith(b"\x89PNG"))
        with Image.open(io.BytesIO(data)) as img:
            self.assertEqual((512, 192), img.size)

    def test_offline_without_duration_still_renders(self):
        # 重启后旧待定缺 online_minutes：省略时长行，不抛异常
        data = self._render("offline", online_minutes=None)
        self.assertTrue(data.startswith(b"\x89PNG"))

    def test_missing_event_time_renders_without_clock_line(self):
        img = render_presence_card_image("测试玩家", "online")
        self.assertEqual((512, 192), img.size)

    def test_long_name_is_truncated_not_overflowing(self):
        img = render_presence_card_image("超" * 80, "online", event_time=1700000000)
        self.assertEqual((512, 192), img.size)


class PresenceCardAssetDegradeTests(unittest.IsolatedAsyncioTestCase):
    async def test_asset_fetch_errors_still_produce_card(self):
        # 头像/头像框请求全部抛异常时仍应出图（回落内置占位）
        async def boom(*args, **kwargs):
            raise RuntimeError("network down")

        with patch.object(presence_card, "get_avatar_path", boom), patch.object(
            presence_card, "get_avatar_frame_url", boom
        ), patch.object(presence_card, "get_avatar_frame_path", boom):
            data = await presence_card.render_presence_card(
                "", "s1", "测试玩家", "offline", online_minutes=10.0, event_time=1700000000
            )
        self.assertTrue(data.startswith(b"\x89PNG"))
        with Image.open(io.BytesIO(data)) as img:
            self.assertEqual((512, 192), img.size)

    async def test_corrupt_cached_avatar_falls_back_to_default(self):
        # 缓存头像文件损坏：不得整卡失败，回退内置默认头像
        with tempfile.TemporaryDirectory() as tmp:
            avatar_dir = os.path.join(tmp, "avatars")
            os.makedirs(avatar_dir)
            with open(os.path.join(avatar_dir, "s1.jpg"), "wb") as f:
                f.write(b"not an image")

            async def cached_avatar(_data_dir, steamid, _url, force_update=False, proxy=None):
                return os.path.join(avatar_dir, f"{steamid}.jpg")

            async def no_frame(*_args, **_kwargs):
                return None

            with patch.object(presence_card, "get_avatar_path", cached_avatar), patch.object(
                presence_card, "get_avatar_frame_url", no_frame
            ), patch.object(presence_card, "get_avatar_frame_path", no_frame):
                data = await presence_card.render_presence_card(
                    tmp, "s1", "测试玩家", "online", event_time=1700000000
                )
        self.assertTrue(data.startswith(b"\x89PNG"))
        with Image.open(io.BytesIO(data)) as img:
            self.assertEqual((512, 192), img.size)


if __name__ == "__main__":
    unittest.main()
