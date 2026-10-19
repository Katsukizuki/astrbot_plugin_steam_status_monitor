import asyncio
import io
import tempfile
from typing import Optional

from ...presentation.renderers.steam_list import render_steam_list_image
from ...presentation.renderers.assets import resolve_avatar_frame
from ...shared.utils.mentions import qq_avatar_url
from .player_status_view import PlayerStatusViewService, build_player_row

__all__ = ["build_player_row", "handle_steam_list", "render_user_list_image", "list_parent"]


def list_parent(event):
    """返回 (触发者昵称, QQ头像URL)；获取失败返回 (None, None)。"""
    try:
        sender_id = event.get_sender_id()
        sender_name = event.get_sender_name()
    except Exception:
        return None, None
    url = qq_avatar_url(sender_id)
    return sender_name, url


async def collect_list_assets(plugin, user_list, *, proxy=None):
    steam_style = (getattr(plugin, "config", {}) or {}).get("enable_steam_style", False)
    from ...presentation.renderers.game_start import get_cover_path

    async def collect_user_assets(user):
        sid = user.get("sid", "")
        gid = user.get("gameid", "")
        if not sid:
            return sid, None, None

        frame_task = resolve_avatar_frame(plugin.data_dir, sid, proxy=proxy)
        if not gid or steam_style:
            return sid, await frame_task, None
        frame_path, cover_path = await asyncio.gather(
            frame_task,
            get_cover_path(
                plugin.data_dir,
                gid,
                user.get("game", ""),
                sgdb_api_key=plugin.SGDB_API_KEY,
                appid=gid,
                proxy=proxy,
                sgdb_api_base=plugin.SGDB_API_BASE,
            ),
        )
        return sid, frame_path, cover_path

    assets = await asyncio.gather(*(collect_user_assets(user) for user in user_list))
    avatar_frame_paths = {
        sid: frame_path for sid, frame_path, _ in assets if sid and frame_path
    }
    covers = {
        sid: cover_path for sid, _, cover_path in assets if sid and cover_path
    }
    return avatar_frame_paths, covers, steam_style


async def render_user_list_image(plugin, event, user_list, *, font_path: Optional[str] = None, proxy=None):
    proxy = plugin.proxy if proxy is None else proxy
    avatar_frame_paths, covers, steam_style = await collect_list_assets(plugin, user_list, proxy=proxy)
    parent_name, parent_avatar_url = list_parent(event)
    img_bytes = await render_steam_list_image(
        plugin.data_dir,
        user_list,
        font_path=font_path,
        proxy=proxy,
        avatar_frame_paths=avatar_frame_paths,
        covers=covers,
        steam_style=steam_style,
        parent_name=parent_name,
        parent_avatar_url=parent_avatar_url,
    )
    if not img_bytes:
        return None
    with io.BytesIO(img_bytes) as buf:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
            tmp.write(buf.read())
            return tmp.name


async def handle_steam_list(
    self,
    event,
    *,
    group_id: Optional[str] = None,
    font_path: Optional[str] = None,
    proxy: str = None,
    **_kwargs,
):
    """列出所有玩家当前状态（图片美化版，分群支持）"""
    if not group_id:
        if hasattr(event, "get_group_id"):
            group_id = str(event.get_group_id() or "default")
        elif hasattr(event, "group_id"):
            group_id = str(event.group_id or "default")
        else:
            group_id = "default"
    view = getattr(self, "player_status_view", None) or PlayerStatusViewService(self)
    user_list = await view.build_group_rows(group_id)
    tmp_path = await render_user_list_image(self, event, user_list, font_path=font_path, proxy=proxy)
    if tmp_path:
        yield event.image_result(tmp_path)
    else:
        yield event.plain_result("渲染图片失败")
