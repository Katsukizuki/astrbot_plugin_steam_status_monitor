"""上线/下线状态卡：渐变底 + 头像/头像框 + 状态标题 + 时间与本次在线时长。

风格与 game_start / game_end 一致（512×192、圆角头像、右下角版本号水印），
纯本地绘制；只有头像/头像框下载是异步的。
"""

import asyncio
import io
from datetime import datetime
from typing import Optional

from PIL import Image, ImageDraw

from ...shared.fonts import load_truetype
from ...shared.logging import logger
from ...shared.paths import IMAGES_DIR
from .game_start import get_avatar_frame_path, get_avatar_frame_url, get_avatar_path

IMG_W, IMG_H = 512, 192
AVATAR_SIZE = 80
AVATAR_X, AVATAR_Y = 36, 40
TEXT_X = AVATAR_X + AVATAR_SIZE + 24

# 上线（绿）与离线（深紫）两套渐变底
BG_TOP_ONLINE = (49, 80, 66)
BG_BOTTOM_ONLINE = (28, 35, 44)
BG_TOP_OFFLINE = (24, 18, 48)
BG_BOTTOM_OFFLINE = (8, 8, 16)

STATE_COLOR_ONLINE = (120, 255, 170)
STATE_COLOR_OFFLINE = (200, 170, 255)


def _gradient_canvas(width, height, color_top, color_bottom):
    """竖向渐变底：1×h 渐变列横向拉伸，避免逐像素画线。"""
    column = Image.new("RGB", (1, height))
    for y in range(height):
        ratio = y / max(1, height - 1)
        column.putpixel(
            (0, y),
            tuple(int(color_top[i] * (1 - ratio) + color_bottom[i] * ratio) for i in range(3)),
        )
    return column.resize((width, height)).convert("RGBA")


def _load_avatar_image(source, size):
    """打开并缩放头像；路径损坏/格式非法时返回 None 由调用方降级。"""
    try:
        with Image.open(source) as src:
            return src.convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception as e:
        logger.warning(f"[presence_card] 头像加载失败: {e}")
        return None


def _paste_avatar(img, avatar_path, avatar_frame_path):
    """圆角头像 + 头像框叠加；头像缺失或缓存损坏时用内置默认头像占位。"""
    avatar = _load_avatar_image(avatar_path, AVATAR_SIZE) if avatar_path else None
    if avatar is None:
        fallback = IMAGES_DIR / "unknown_avatar.jpg"
        if fallback.exists():
            avatar = _load_avatar_image(str(fallback), AVATAR_SIZE)
    if avatar is not None:
        mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, AVATAR_SIZE, AVATAR_SIZE), radius=AVATAR_SIZE // 5, fill=255
        )
        avatar.putalpha(mask)
        img.alpha_composite(avatar, (AVATAR_X, AVATAR_Y))
    if avatar_frame_path:
        try:
            frame_size = AVATAR_SIZE + 12
            frame_offset = (frame_size - AVATAR_SIZE) // 2
            with Image.open(avatar_frame_path) as src:
                frame = src.convert("RGBA").resize((frame_size, frame_size), Image.LANCZOS)
            img.alpha_composite(frame, (AVATAR_X - frame_offset, AVATAR_Y - frame_offset))
        except Exception as e:
            logger.warning(f"[presence_card] 头像框加载失败: {e}")


def _format_clock(event_time):
    """事件时间戳 → 本地 HH:MM；缺失/非法返回空串。"""
    try:
        return datetime.fromtimestamp(int(event_time)).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _fit_text(draw, text, font, max_width):
    """超宽文本逐字截断并加省略号。"""
    if draw.textlength(text, font=font) <= max_width:
        return text
    ellipsis = "…"
    while text and draw.textlength(text + ellipsis, font=font) > max_width:
        text = text[:-1]
    return text + ellipsis


def render_presence_card_image(
    player_name,
    kind,
    *,
    avatar_path=None,
    avatar_frame_path=None,
    online_minutes=None,
    event_time=None,
    font_path=None,
    version=None,
):
    """同步绘制上线/下线状态卡，返回 RGBA 图像。"""
    is_online = kind == "online"
    color_top, color_bottom = (
        (BG_TOP_ONLINE, BG_BOTTOM_ONLINE) if is_online else (BG_TOP_OFFLINE, BG_BOTTOM_OFFLINE)
    )
    state_color = STATE_COLOR_ONLINE if is_online else STATE_COLOR_OFFLINE

    img = _gradient_canvas(IMG_W, IMG_H, color_top, color_bottom)
    draw = ImageDraw.Draw(img)
    _paste_avatar(img, avatar_path, avatar_frame_path)

    extra = (font_path,) if font_path else ()
    font_title = load_truetype("NotoSansHans-Medium.otf", 28, fallbacks=extra)
    font_state = load_truetype("NotoSansHans-Medium.otf", 20, fallbacks=extra)
    font_info = load_truetype("NotoSansHans-Regular.otf", 15, fallbacks=extra)
    font_version = load_truetype("NotoSansHans-Medium.otf", 7, fallbacks=extra)

    max_text_w = IMG_W - TEXT_X - 24
    # 玩家名（截断保护，避免压到状态区）
    name_text = _fit_text(draw, player_name or "", font_title, max_text_w)
    draw.text((TEXT_X, 34), name_text, font=font_title, fill=(255, 255, 255, 255))

    # 状态标题：色点 + 文案
    state_text = "已上线" if is_online else "已离线"
    draw.ellipse((TEXT_X, 82, TEXT_X + 10, 92), fill=state_color + (255,))
    draw.text((TEXT_X + 18, 74), state_text, font=font_state, fill=state_color + (255,))

    # 详情行：时间 + （离线时）本次在线时长
    clock = _format_clock(event_time)
    info_lines = []
    if clock:
        info_lines.append(f"{'在线开始' if is_online else '离线时间'} {clock}")
    if not is_online and online_minutes is not None:
        info_lines.append(f"本次在线 {_format_minutes(online_minutes)}")
    for index, line in enumerate(info_lines):
        draw.text((TEXT_X, 112 + index * 22), line, font=font_info, fill=(210, 218, 230, 230))

    # 右下角版本号水印（与 game_start 同款低饱和绿）
    if version:
        v_text = f"v{version}"
        v_bbox = draw.textbbox((0, 0), v_text, font=font_version)
        draw.text(
            (IMG_W - (v_bbox[2] - v_bbox[0]) - 6, IMG_H - (v_bbox[3] - v_bbox[1]) - 4),
            v_text,
            font=font_version,
            fill=(115, 150, 105),
        )
    return img


def _format_minutes(minutes):
    """分钟数 → 人读时长（与游戏通知的 format_play_duration 口径一致）。"""
    try:
        value = float(minutes)
    except (TypeError, ValueError):
        return "未知"
    if value < 60:
        return f"{value:.1f}分钟"
    return f"{value / 60:.1f}小时"


async def _resolve_avatar_frame(data_dir, steamid, proxy):
    """先读本地缓存，再取 URL 补缓存（与 game_start 内部逻辑一致）。"""
    path = await get_avatar_frame_path(data_dir, steamid, proxy=proxy)
    if path:
        return path
    frame_url = await get_avatar_frame_url(steamid, proxy=proxy)
    if not frame_url:
        return None
    return await get_avatar_frame_path(data_dir, steamid, frame_url, proxy=proxy)


async def render_presence_card(
    data_dir,
    steamid,
    player_name,
    kind,
    *,
    avatar_url: Optional[str] = None,
    online_minutes: Optional[float] = None,
    event_time: Optional[int] = None,
    font_path: Optional[str] = None,
    proxy: Optional[str] = None,
    version: Optional[str] = None,
) -> bytes:
    """下载头像素材并渲染上线/下线状态卡，返回 PNG bytes。

    素材请求独立降级：头像或头像框任一失败都不阻断出图（头像回落内置占位）。
    """
    avatar_path, avatar_frame_path = await asyncio.gather(
        get_avatar_path(data_dir, steamid, avatar_url, proxy=proxy),
        _resolve_avatar_frame(data_dir, steamid, proxy),
        return_exceptions=True,
    )
    if isinstance(avatar_path, BaseException):
        logger.warning(f"[presence_card] 头像获取失败: {avatar_path}")
        avatar_path = None
    if isinstance(avatar_frame_path, BaseException):
        logger.warning(f"[presence_card] 头像框获取失败: {avatar_frame_path}")
        avatar_frame_path = None
    img = render_presence_card_image(
        player_name,
        kind,
        avatar_path=avatar_path,
        avatar_frame_path=avatar_frame_path,
        online_minutes=online_minutes,
        event_time=event_time,
        font_path=font_path,
        version=version,
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
