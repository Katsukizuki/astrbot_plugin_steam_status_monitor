"""Plugin Pages 请求解析：把重复的 JSON / 查询参数兜底收到一处。"""
from __future__ import annotations

from astrbot.api.web import json_response


async def read_json_object(request):
    """解析 JSON 请求体。

    Returns:
        (payload, None) 成功；payload 一定是 dict。
        (None, response) 失败；response 为 400 响应，调用方直接 return。
    """
    try:
        payload = await request.json()
    except Exception:
        return None, json_response({"error": "invalid JSON"}, status_code=400)
    if not isinstance(payload, dict):
        return None, json_response({"error": "invalid JSON"}, status_code=400)
    return payload, None


def query_int(request, key, default, *, minimum=None, maximum=None) -> int:
    """读取整型查询参数；非法值回退 default，并按需夹取范围。"""
    try:
        value = int(request.query.get(key, default))
    except (TypeError, ValueError):
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value
