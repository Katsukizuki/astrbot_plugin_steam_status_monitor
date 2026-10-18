"""头像与头像框本地缓存。

三个调用方（开始卡、结束卡、列表/排行）共用同一份下载与 TTL 逻辑，
避免各自维护一份 httpx + 文件缓存实现。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

import httpx
from PIL import Image

from ...shared.logging import logger
from ...shared.network import httpx_client_kwargs

AVATAR_TTL_SECONDS = 86400
AVATAR_FRAME_TTL_SECONDS = 604800

_frame_url_cache = {}  # {steamid: (url_or_None, timestamp)}


async def get_avatar_path(data_dir, steamid, url, force_update=False, proxy=None):
    """下载并缓存玩家头像，返回本地路径；无图且无缓存时返回 None。"""
    avatar_dir = os.path.join(data_dir, "avatars")
    os.makedirs(avatar_dir, exist_ok=True)
    path = os.path.join(avatar_dir, f"{steamid}.jpg")
    if os.path.exists(path) and not force_update:
        if AVATAR_TTL_SECONDS == 0 or time.time() - os.path.getmtime(path) < AVATAR_TTL_SECONDS:
            return path
    if not url:
        return path if os.path.exists(path) else None
    try:
        async with httpx.AsyncClient(timeout=10, **httpx_client_kwargs(proxy)) as client:
            response = await client.get(url)
        if response.status_code == 200:
            with open(path, "wb") as handle:
                handle.write(response.content)
            return path
    except Exception as exc:
        logger.debug("[avatar] 下载失败: %s", exc)
    return path if os.path.exists(path) else None


async def load_avatar_image(data_dir, sid, url, proxy=None) -> Image.Image | None:
    """返回已下载头像的 PIL 对象，供列表/排行直接绘制。"""
    path = await get_avatar_path(data_dir, sid, url, proxy=proxy)
    if not path:
        return None
    try:
        return Image.open(path).convert("RGBA")
    except OSError as exc:
        logger.debug("[avatar] 读取失败 %s: %s", path, exc)
        return None


async def get_avatar_frame_url(steamid, proxy=None):
    """从 Steam 个人资料页解析头像框图片地址；无头像框返回 None。"""
    if steamid in _frame_url_cache:
        cached_url, cached_at = _frame_url_cache[steamid]
        if time.time() - cached_at < AVATAR_FRAME_TTL_SECONDS:
            return cached_url
    try:
        url = f"https://steamcommunity.com/profiles/{steamid}/?l=english"
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, **httpx_client_kwargs(proxy)) as client:
            response = await client.get(url)
        if response.status_code != 200:
            return None
        div_match = re.search(r'<div class="profile_avatar_frame">(.*?)</div>', response.text, re.DOTALL)
        if div_match:
            sources = re.findall(r'srcset="([^"]+)"', div_match.group(1))
            if sources:
                result = sources[-1]
                _frame_url_cache[steamid] = (result, time.time())
                return result
        data_match = re.search(r'g_rgProfileData\s*=\s*({.+?});', response.text, re.DOTALL)
        if data_match:
            try:
                raw = data_match.group(1).replace(r"\/", "/")
                avatar_frame = json.loads(raw).get("avatar_frame")
                if isinstance(avatar_frame, dict) and avatar_frame.get("url"):
                    result = avatar_frame["url"]
                    _frame_url_cache[steamid] = (result, time.time())
                    return result
            except (ValueError, TypeError) as exc:
                logger.debug("[avatar] 头像框 JSON 解析失败: %s", exc)
        _frame_url_cache[steamid] = (None, time.time())
    except Exception as exc:
        logger.debug("[avatar] 获取头像框失败: %s", exc)
    return None


async def get_avatar_frame_path(data_dir, steamid, url=None, proxy=None):
    """下载并缓存头像框，返回本地路径；无缓存且无 url 时返回 None。"""
    frame_dir = os.path.join(data_dir, "avatar_frames")
    os.makedirs(frame_dir, exist_ok=True)
    if os.path.isdir(frame_dir):
        for filename in os.listdir(frame_dir):
            if filename.startswith(f"{steamid}_") and filename.endswith(".png"):
                path = os.path.join(frame_dir, filename)
                if time.time() - os.path.getmtime(path) < AVATAR_FRAME_TTL_SECONDS:
                    return path
    if not url:
        return None
    url_hash = hashlib.md5(url.encode()).hexdigest()[:12]
    path = os.path.join(frame_dir, f"{steamid}_{url_hash}.png")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < AVATAR_FRAME_TTL_SECONDS:
        return path
    try:
        async with httpx.AsyncClient(timeout=10, **httpx_client_kwargs(proxy)) as client:
            response = await client.get(url)
        if response.status_code == 200:
            with open(path, "wb") as handle:
                handle.write(response.content)
            return path
    except Exception as exc:
        logger.debug("[avatar] 头像框下载失败: %s", exc)
    return path if os.path.exists(path) else None


async def resolve_avatar_frame(data_dir, steamid, proxy=None):
    """本地缓存优先，其次抓取个人资料页中的头像框地址再下载。"""
    path = await get_avatar_frame_path(data_dir, steamid, proxy=proxy)
    if path:
        return path
    frame_url = await get_avatar_frame_url(steamid, proxy=proxy)
    if not frame_url:
        return None
    return await get_avatar_frame_path(data_dir, steamid, frame_url, proxy=proxy)
