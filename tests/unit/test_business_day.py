"""业务日边界（凌晨 4:00 切换）的统一口径。"""
from datetime import datetime

from src.shared.timeutil import (
    business_day_key,
    business_day_start,
    business_day_start_for_key,
    seconds_to_next_business_day,
)


def test_before_rollover_belongs_to_previous_day():
    assert business_day_key(0, datetime(2026, 9, 12, 3, 59)) == "2026-09-11"
    assert business_day_key(0, datetime(2026, 9, 12, 4, 0)) == "2026-09-12"


def test_offset_days_moves_business_day():
    assert business_day_key(-1, datetime(2026, 9, 12, 2, 0)) == "2026-09-10"
    assert business_day_key(7, datetime(2026, 9, 12, 12, 0)) == "2026-09-19"


def test_day_start_is_nearest_rollover():
    assert business_day_start(datetime(2026, 9, 12, 2, 0)) == datetime(2026, 9, 11, 4, 0)
    assert business_day_start(datetime(2026, 9, 12, 12, 0)) == datetime(2026, 9, 12, 4, 0)


def test_key_round_trips_to_window_start():
    assert business_day_start_for_key("2026-09-11") == datetime(2026, 9, 11, 4, 0)


def test_seconds_to_next_business_day_never_returns_zero():
    remaining = seconds_to_next_business_day(datetime(2026, 9, 12, 3, 59, 30))
    assert 60 <= remaining <= 120
