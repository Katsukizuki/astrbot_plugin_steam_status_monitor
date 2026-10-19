"""游玩时长格式化：中文描述与排行榜紧凑格式共用一套换算。"""


def format_duration(minutes, *, style: str = "cn") -> str:
    """把分钟数格式化为可读时长。

    Args:
        minutes: 时长（分钟），负数按 0 处理。
        style: ``cn`` 输出「1.5小时 / 45.0分钟」，``compact`` 输出「1.5h / 45min」。
    """
    total = max(0.0, float(minutes or 0))
    if style == "compact":
        hours = total / 60
        return f"{hours:.1f}h" if hours >= 1 else f"{int(total)}min"
    if total < 60:
        return f"{total:.1f}分钟"
    return f"{total / 60:.1f}小时"
