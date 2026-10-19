"""手动冒烟：离线渲染开始卡/结束卡/列表卡，确认渲染管线可用。

用法：``python tests/smoke_render.py``（在插件根目录执行，无需网络与 API Key）。
"""
import asyncio

from src.presentation.renderers.game_end import render_game_end
from src.presentation.renderers.game_start import render_game_start
from src.presentation.renderers.steam_list import render_steam_list_image

SID = "76561198000000001"
ROWS = [
    {
        "sid": "1",
        "name": "P",
        "status": "playing",
        "game": "CS2",
        "gameid": "0",
        "play_str": "1.0小时",
        "avatar_url": "",
    }
]


async def main() -> None:
    start = await render_game_start(".", SID, "P", None, "0", "CS2")
    end = await render_game_end(
        ".", SID, "P", None, "0", "CS2", "2026-10-17 12:00", "tip", 1.5
    )
    listing = await render_steam_list_image(".", ROWS)
    for name, payload in (("start", start), ("end", end), ("list", listing)):
        assert payload and payload.startswith(b"\x89PNG"), name
        print(f"{name}: {len(payload)} bytes")


if __name__ == "__main__":
    asyncio.run(main())
