"""群名单与联动推送范围的纯函数。

监控范围的口径只有一处：``群主名单 ∪ 该群的 push_group 路由``。
命令、Web API、排行、通知和统计都必须走这里，避免各自推导导致偏差。
"""
from __future__ import annotations

from typing import Mapping


def direct_sids(groups: Mapping, group_id) -> list[str]:
    """该群自己的监控名单（不含联动推送）。"""
    return [str(sid) for sid in (groups.get(group_id) or groups.get(str(group_id)) or [])]


def push_sids(push_groups: Mapping, group_id) -> list[str]:
    """其它群把自己监控的玩家联动推送到该群时，这些玩家的 SteamID。"""
    target = str(group_id)
    return [
        str(sid)
        for sid, targets in (push_groups or {}).items()
        if target in {str(item) for item in (targets or [])}
    ]


def sids_for_group(groups: Mapping, push_groups: Mapping, group_id) -> list[str]:
    """该群可见的全部玩家：主名单在前，联动推送在后，去重保序。"""
    return list(dict.fromkeys([*direct_sids(groups, group_id), *push_sids(push_groups, group_id)]))


def groups_for_sid(groups: Mapping, push_groups: Mapping, sid) -> list[str]:
    """该玩家会被推送到哪些群：主监控群 + 所有联动推送群。"""
    target = str(sid)
    result = [
        str(group_id)
        for group_id, steam_ids in (groups or {}).items()
        if target in {str(item) for item in (steam_ids or [])}
    ]
    for raw_sid, targets in (push_groups or {}).items():
        if str(raw_sid) == target:
            result.extend(str(group_id) for group_id in (targets or []))
    return list(dict.fromkeys(result))


def primary_group_of(groups: Mapping, sid) -> str | None:
    """唯一主监控群；同一玩家只允许一个群真正轮询。"""
    target = str(sid)
    for group_id, steam_ids in (groups or {}).items():
        if target in {str(item) for item in (steam_ids or [])}:
            return str(group_id)
    return None


def all_group_sids(groups: Mapping) -> set[str]:
    """仅出现在群主名单里的玩家（排行等按主监控口径统计）。"""
    return {
        str(sid)
        for steam_ids in (groups or {}).values()
        for sid in (steam_ids or [])
    }


def all_sids(groups: Mapping, push_groups: Mapping | None = None) -> set[str]:
    """全部被监控玩家（含仅作为推送目标出现的玩家）。"""
    result = all_group_sids(groups)
    result.update(str(sid) for sid in (push_groups or {}))
    return result
