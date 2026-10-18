import asyncio
import os
import io
import time
from PIL import Image, ImageDraw
from .assets import get_avatar_path, resolve_avatar_frame
from .game_start import get_cover_path, get_horizontal_cover_path
from ...shared.fonts import load_truetype
from .drawing import vertical_gradient, wrap_text
from ...shared.logging import logger
from ...shared.paths import IMAGES_DIR

# 更深的蓝紫色到黑色渐变
BG_COLOR_TOP = (24, 18, 48)   # 顶部深蓝紫
BG_COLOR_BOTTOM = (8, 8, 16)  # 底部接近黑色
AVATAR_SIZE = 80
COVER_W, COVER_H = 80, 120
IMG_W, IMG_H = 512, 192

_MEASURE_DRAW = ImageDraw.Draw(Image.new("RGB", (1, 1)))

# 星星素材位于统一图片资源目录
STAR_BG_PATH = str(IMAGES_DIR / "随机散布的小星星767x809xp.png")


def draw_duration_bar(draw, x, y, width, height, duration_h):
    pad = 1
    # 先画底色和描边
    draw.rounded_rectangle([x-pad, y-pad, x+width+pad, y+height+pad], radius=(height+pad)//2, fill=(0,0,0,180))
    draw.rounded_rectangle([x, y, x + width, y + height], radius=height//2, outline=(0,0,0,255), width=1)
    draw.rounded_rectangle([x-2, y-2, x + width+2, y + height+2], radius=(height+4)//2, outline=(255,255,255,220), width=1)
    bar_colors = [
        (80, 200, 120),    # 1小时 绿色
        (255, 220, 80),    # 3小时 黄色
        (255, 160, 80),    # 5小时 橙色
        (255, 80, 80),     # 7小时 红色
        (200, 80, 160),    # 9小时 紫红色
        (120, 80, 200)     # 12小时 深紫色
    ]
    seg_limits = [1, 3, 5, 7, 9, 12]
    seg_starts = [0] + seg_limits[:-1]
    seg_texts = [None, "2X", "3X", "4X", "5X", "6X"]
    if duration_h > 12:
        # 彩色渐变条
        for i in range(width):
            ratio = i / max(width-1, 1)
            # 渐变色：红橙黄绿青蓝紫
            from colorsys import hsv_to_rgb
            rgb = hsv_to_rgb(ratio, 0.8, 1.0)
            color = tuple(int(c*255) for c in rgb)
            draw.line([(x+i, y), (x+i, y+height)], fill=color, width=1)
        # 叠加MAX文字
        font = load_truetype("NotoSansHans-Medium.otf", height + 8)
        text = "MAX"
        text_bbox = draw.textbbox((0,0), text, font=font)
        text_w = text_bbox[2] - text_bbox[0]
        text_h = text_bbox[3] - text_bbox[1]
        center_x = x + width // 2 - text_w // 2
        center_y = y + height // 2 - text_h // 2 - 5
        draw.text((center_x, center_y), text, font=font, fill=(255,255,255,255), stroke_width=2, stroke_fill=(0,0,0,180))
    else:
        # 普通分段条
        for i, (seg_start, seg_end, color) in enumerate(zip(seg_starts, seg_limits, bar_colors)):
            seg_val = min(max(duration_h - seg_start, 0), seg_end - seg_start)
            seg_ratio = seg_val / (seg_end - seg_start) if seg_end > seg_start else 0
            seg_w = int(width * seg_ratio)
            if seg_w > 0:
                draw.rounded_rectangle([x, y, x + seg_w, y + height], radius=height//2, fill=color)
        for i, (seg_start, seg_end, color) in enumerate(zip(seg_starts, seg_limits, bar_colors)):
            if (seg_texts[i] and duration_h > seg_start):
                text = seg_texts[i]
                font = load_truetype("NotoSansHans-Medium.otf", height + 6)
                text_bbox = draw.textbbox((0,0), text, font=font)
                text_w = text_bbox[2] - text_bbox[0]
                text_h = text_bbox[3] - text_bbox[1]
                center_x = x + width // 2 - text_w // 2
                center_y = y + height // 2 - text_h // 2 - 5
                draw.text((center_x, center_y), text, font=font, fill=color, stroke_width=2, stroke_fill=(0,0,0,180))

# 与开始卡一致：名字区按实测宽度拉长，上限 360px，再换行。
_MAX_NAME_LINE_W = 360
_MIN_NAME_LINE_W = 220
_END_TITLE_SUFFIX = " 结束游戏"


def _measure_text_width(text, font):
    dummy_img = Image.new("RGB", (10, 10))
    draw = ImageDraw.Draw(dummy_img)
    bbox = draw.textbbox((0, 0), text or "", font=font)
    return bbox[2] - bbox[0]


def fit_end_card_player_name(player_name, font, text_x, extra_right=0, default_w=IMG_W):
    """与开始卡相同：28px 字号、按文字拉长画布、超过 360px 换行。
    「结束游戏」作为独立一行紧跟玩家名（第一行玩家名、第二行结束游戏、第三行游戏名）；
    时间文本作为右上角叠标，不撑宽画布（extra_right 保留为兼容参数）。"""
    name = player_name or ""
    name_width = _measure_text_width(name, font)
    name_line_w = min(max(name_width, _MIN_NAME_LINE_W), _MAX_NAME_LINE_W)
    lines = wrap_text(_MEASURE_DRAW, name, font, _MAX_NAME_LINE_W)
    suffix = _END_TITLE_SUFFIX.strip()
    lines = lines + [suffix]
    content_w = max(name_line_w, _measure_text_width(suffix, font))
    img_w = max(default_w, text_x + content_w + 24)
    return lines, img_w

def render_game_end_image(player_name, avatar_path, game_name, cover_path, end_time_str, tip_text, duration_h, font_path=None, avatar_frame_path=None, horizontal_cover_path=None):
    # 字体
    font_title = load_truetype("NotoSansHans-Medium.otf", 28)
    font_game = load_truetype("NotoSansHans-Regular.otf", 22)
    font_tip = load_truetype("NotoSansHans-Regular.otf", 16)
    font_luck = load_truetype("NotoSansHans-Regular.otf", 14)
    font_time = load_truetype("NotoSansHans-Regular.otf", 8)

    # 先量封面宽度，再按长玩家名决定是否加宽画布。
    cover_area_h = IMG_H
    new_w = COVER_W
    if cover_path and os.path.exists(cover_path):
        try:
            with Image.open(cover_path) as cover_src:
                new_w = int(cover_src.width * (cover_area_h / cover_src.height))
        except Exception as e:
            logger.debug(f"[game_end_render] 封面尺寸获取失败: {e}")

    avatar_x = new_w + 24
    text_x = avatar_x + AVATAR_SIZE + 20
    try:
        from datetime import datetime
        t = datetime.strptime(end_time_str, "%Y-%m-%d %H:%M")
        time_str = t.strftime("%H:%M")
    except Exception:
        time_str = end_time_str[-5:] if end_time_str else ""
    time_w = _measure_text_width(time_str, font_time) + 18
    player_lines, img_w = fit_end_card_player_name(player_name, font_title, text_x, extra_right=time_w)
    if new_w > img_w:
        img_w = new_w

    img = vertical_gradient(img_w, IMG_H, BG_COLOR_TOP, BG_COLOR_BOTTOM).convert("RGBA")
    draw = ImageDraw.Draw(img)

    # 1. 背景星星横向平铺（等比例缩放高度，透明度30%）
    try:
        star_bg = Image.open(STAR_BG_PATH).convert("RGBA")
        star_w, star_h = star_bg.size
        scale = IMG_H / star_h
        star_tile_w = int(star_w * scale)
        new_h = IMG_H
        star_bg_resized = star_bg.resize((star_tile_w, new_h), Image.LANCZOS)
        # 设置透明度30%
        alpha = star_bg_resized.split()[-1].point(lambda p: int(p * 0.3))
        star_bg_resized.putalpha(alpha)
        for x in range(0, img_w, star_tile_w):
            img.alpha_composite(star_bg_resized, (x, 0))
    except Exception as e:
        logger.debug(f"[game_end_render] 星星背景加载失败: {e}")

    # 2. 封面图左侧，等比例缩放高度，宽度自适应，不裁剪，左贴右留空
    if cover_path and os.path.exists(cover_path):
        try:
            cover_src = Image.open(cover_path).convert("RGBA")
            scale = cover_area_h / cover_src.height
            new_w = int(cover_src.width * scale)
            new_h = cover_area_h
            cover_resized = cover_src.resize((new_w, new_h), Image.LANCZOS)
            if new_w > img_w:
                cover_resized = cover_resized.crop((0, 0, img_w, new_h))
                new_w = img_w
            img.paste(cover_resized, (0, 0), cover_resized)
            # 竖版封面缺失（missingcover）时，叠加横版header_image
            if os.path.basename(cover_path) == "missingcover.jpg" and horizontal_cover_path and os.path.exists(horizontal_cover_path):
                try:
                    h_cover = Image.open(horizontal_cover_path).convert("RGBA")
                    h_scale = new_w / h_cover.width
                    h_new_w = new_w
                    h_new_h = int(h_cover.height * h_scale)
                    h_cover_resized = h_cover.resize((h_new_w, h_new_h), Image.LANCZOS)
                    h_offset_y = (cover_area_h - h_new_h) // 2
                    img.paste(h_cover_resized, (0, h_offset_y), h_cover_resized)
                    logger.debug(f"[game_end_render] 横版封面叠加成功: {horizontal_cover_path} ({h_new_w}x{h_new_h})")
                except Exception as e:
                    logger.debug(f"[game_end_render] 横版封面叠加失败: {e}")
        except Exception as e:
            logger.debug(f"[game_end_render] 封面加载失败: {e}")

    # 3. 头像（仅圆角，无柔光特效）
    avatar_y = 16
    if avatar_path and os.path.exists(avatar_path):
        try:
            logger.debug(f"[game_end_render] 尝试打开头像: {avatar_path}")
            avatar = Image.open(avatar_path).convert("RGBA").resize((AVATAR_SIZE, AVATAR_SIZE))
            # 圆角遮罩
            mask = Image.new("L", (AVATAR_SIZE, AVATAR_SIZE), 0)
            draw_mask = ImageDraw.Draw(mask)
            draw_mask.rounded_rectangle((0, 0, AVATAR_SIZE, AVATAR_SIZE), radius=AVATAR_SIZE//5, fill=255)
            avatar_rgba = avatar.copy()
            avatar_rgba.putalpha(mask)
            img.alpha_composite(avatar_rgba, (avatar_x, avatar_y))
            if avatar_frame_path and os.path.exists(avatar_frame_path):
                try:
                    frame_size = AVATAR_SIZE + 12
                    frame_offset = (frame_size - AVATAR_SIZE) // 2
                    frame_img = Image.open(avatar_frame_path).convert("RGBA").resize((frame_size, frame_size), Image.LANCZOS)
                    img.alpha_composite(frame_img, (avatar_x - frame_offset, avatar_y - frame_offset))
                except Exception as e:
                    logger.debug(f"[game_end_render] 头像框渲染失败: {e}")
        except Exception as e:
            import traceback
            logger.debug(f"[game_end_render] 头像加载失败: {e}\n{traceback.format_exc()}")

    # 今日人品（0~100），显示在头像正下方，字体更小，每个steamid每天固定
    import datetime, hashlib
    today = datetime.date.today().isoformat()
    luck_seed = f"{player_name}_{today}".encode("utf-8")
    today_luck = int(hashlib.md5(luck_seed).hexdigest(), 16) % 101
    luck_text = f"今日人品：{today_luck}"
    luck_font_y = avatar_y + AVATAR_SIZE + 8
    draw.text((avatar_x, luck_font_y), luck_text, font=font_luck, fill=(200,220,255,220), stroke_width=1, stroke_fill=(0,0,0,255))

    # 当前时间叠加在最上方右上角，字号更小
    bbox = draw.textbbox((0,0), time_str, font=font_time, stroke_width=2)
    time_x = img_w - bbox[2] + bbox[0] - 18  # 右上角，留边距
    time_y = 6
    draw.text((time_x, time_y), time_str, font=font_time, fill=(255,255,255,220), stroke_width=2, stroke_fill=(0,0,0,255))

    # 4. 玩家名 + 「结束游戏」：与开始卡相同，按文字拉长画布，超宽换行。
    title_x = text_x
    title_y = 16
    title_line_h = getattr(font_title, "size", 28) + 2
    for idx, line in enumerate(player_lines):
        draw.text((title_x, title_y + idx * title_line_h), line, font=font_title, fill=(180,160,255,255), stroke_width=2, stroke_fill=(0,0,0,255))

    # 5. 游戏名，头像右侧居左，玩家名下方自动换行
    game_name_y = title_y + len(player_lines) * title_line_h + 6
    max_game_name_w = img_w - title_x - 24
    game_name_lines = wrap_text(draw, game_name, font_game, max_game_name_w)
    max_lines = 2
    for idx, line in enumerate(game_name_lines[:max_lines]):
        draw.text((title_x, game_name_y + idx * (font_game.size + 2)), line, font=font_game, fill=(220,220,255,255), stroke_width=2, stroke_fill=(0,0,0,255))

    # 6. 空几行（间隔）
    tip_y = game_name_y + font_game.size + 28

    # 7. 进度条和时长文本，放在头像列的底部，与今日人品同列
    bar_x = avatar_x
    bar_y = IMG_H - 24
    if duration_h < 1:
        min_text = f"已玩{int(duration_h*60)}分钟："
    else:
        min_text = f"已玩{duration_h:.1f}小时："
    # 文字略抬高，进度条略降低
    draw.text((bar_x, bar_y-2), min_text, font=font_tip, fill=(180, 220, 255, 220), stroke_width=1, stroke_fill=(0,0,0,255))
    min_text_bbox = draw.textbbox((bar_x, bar_y-2), min_text, font=font_tip)
    bar_start_x = min_text_bbox[2] + 6
    bar_w = img_w - bar_start_x - 18  # 进度条延伸到画布结尾，右侧留18px
    bar_h = 6
    if bar_w > 0:
        draw_duration_bar(draw, bar_start_x, bar_y+6, bar_w, bar_h, duration_h)
    else:
        logger.debug(f"[game_end_render] 跳过进度条渲染，bar_w={bar_w}")

    # 8. 友好提示词，玩家名列底部，且与进度条有间隔
    tip_y = bar_y - font_tip.size - 8
    draw.text((bar_x, tip_y), tip_text, font=font_tip, fill=(200,180,255,200), stroke_width=1, stroke_fill=(0,0,0,255))
    return img.convert("RGB")

# render_game_end 里 await get_cover_path
async def render_game_end(data_dir, steamid, player_name, avatar_url, gameid, game_name, end_time_str, tip_text, duration_h, sgdb_api_key=None, font_path=None, sgdb_game_name=None, appid=None, proxy=None, api_key=None, sgdb_api_base=None, steam_store_base=None):
    avatar_path, cover_path, horizontal_cover_path, avatar_frame_path = await asyncio.gather(
        get_avatar_path(data_dir, steamid, avatar_url, proxy=proxy),
        get_cover_path(
            data_dir, gameid, game_name, sgdb_api_key=sgdb_api_key,
            sgdb_game_name=sgdb_game_name, appid=appid, proxy=proxy,
            api_key=api_key, sgdb_api_base=sgdb_api_base,
        ),
        get_horizontal_cover_path(
            data_dir, gameid, appid=appid, proxy=proxy,
            steam_store_base=steam_store_base,
        ),
        resolve_avatar_frame(data_dir, steamid, proxy=proxy),
    )
    img = render_game_end_image(
        player_name, avatar_path, game_name, cover_path, end_time_str,
        tip_text, duration_h, font_path=font_path,
        avatar_frame_path=avatar_frame_path,
        horizontal_cover_path=horizontal_cover_path,
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()
