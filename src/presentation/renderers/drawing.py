"""卡片公共绘制原语：渐变背景与文本排版。"""
from __future__ import annotations

from PIL import Image, ImageDraw


def vertical_gradient(width: int, height: int, top, bottom) -> Image.Image:
    """竖向线性渐变背景（按行绘制，避免逐像素 putpixel）。"""
    image = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(image)
    span = max(1, height - 1)
    for y in range(height):
        ratio = y / span
        color = tuple(
            int(top[index] * (1 - ratio) + bottom[index] * ratio) for index in range(3)
        )
        draw.line([(0, y), (width, y)], fill=color)
    return image


def wrap_text(draw, text, font, max_width, max_lines=None, *, ellipsis="…", empty_text=""):
    """按像素宽度逐字换行；超过 max_lines 时末行加省略号。"""
    value = str(text or "")
    if not value:
        return [empty_text]

    lines = []
    current = ""
    for char in value:
        candidate = current + char
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)

    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textbbox((0, 0), last + ellipsis, font=font)[2] > max_width:
            last = last[:-1]
        lines[-1] = (last or empty_text) + ellipsis
    return lines or [empty_text]


def truncate_text(draw, text, font, max_width, *, ellipsis="…") -> str:
    """单行截断并追加省略号（二分查找可容纳的最长前缀）。"""
    value = str(text or "")
    if max_width <= 0:
        return ""
    if draw.textlength(value, font=font) <= max_width:
        return value
    low, high = 0, len(value)
    while low < high:
        middle = (low + high + 1) // 2
        if draw.textlength(value[:middle] + ellipsis, font=font) <= max_width:
            low = middle
        else:
            high = middle - 1
    return value[:low] + ellipsis
