"""Web 设置页读取时的通知开关默认值（与 _conf_schema.json 保持一致）。

老配置缺少这些键时，GET 返回 schema 等价值，避免前端复选框显示为关闭、
"保存全部设置"时把运行默认值（图片/文本默认开启）意外写成 false。
"""

NOTIFICATION_SETTING_DEFAULTS = {
    "enable_user_online_notify": False,
    "enable_user_offline_notify": False,
    "notify_send_image": True,
    "notify_send_text": True,
}


def with_notification_defaults(config):
    """返回配置副本，并补全缺失的通知开关默认值。"""
    merged = dict(config or {})
    for key, default in NOTIFICATION_SETTING_DEFAULTS.items():
        merged.setdefault(key, default)
    return merged
