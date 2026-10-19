"""业务日边界：凌晨 4:00 之前仍算前一天。

统计、排行、游戏库预算和快照清理共用同一口径，避免各处手写 `hour < 4`。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

DAY_ROLLOVER_HOUR = 4


def business_day_start(now: Optional[datetime] = None) -> datetime:
    """当前业务日的起点（最近一个 4:00）。"""
    current = now or datetime.now()
    boundary = current.replace(hour=DAY_ROLLOVER_HOUR, minute=0, second=0, microsecond=0)
    if current < boundary:
        boundary -= timedelta(days=1)
    return boundary


def business_day_key(offset_days: int = 0, now: Optional[datetime] = None) -> str:
    """业务日 key（YYYY-MM-DD），offset_days 为相对当前业务日的偏移。"""
    return (business_day_start(now) + timedelta(days=offset_days)).strftime("%Y-%m-%d")


def business_day_start_for_key(date_key: str) -> datetime:
    """由业务日 key 还原该日窗口起点（当日 4:00）。"""
    return datetime.strptime(date_key, "%Y-%m-%d").replace(hour=DAY_ROLLOVER_HOUR)


def seconds_to_next_business_day(now: Optional[datetime] = None) -> int:
    """距离下一个业务日切换的秒数（至少 60 秒，避免立即唤醒）。"""
    current = now or datetime.now()
    next_boundary = business_day_start(current) + timedelta(days=1)
    return max(60, int((next_boundary - current).total_seconds()) + 60)
