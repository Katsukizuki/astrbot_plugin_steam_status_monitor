"""群名单 + 联动推送范围的唯一口径。"""
from src.domain.monitoring.roster import (
    all_group_sids,
    all_sids,
    direct_sids,
    groups_for_sid,
    primary_group_of,
    push_sids,
    sids_for_group,
)

GROUPS = {"111": ["s1", "s2"], "222": ["s2", "s3"]}
PUSH = {"s2": ["111", "222"], "s9": ["222"]}


def test_direct_and_push_lists():
    assert direct_sids(GROUPS, "111") == ["s1", "s2"]
    assert push_sids(PUSH, "111") == ["s2"]
    assert push_sids(PUSH, "222") == ["s2", "s9"]


def test_group_scope_unions_direct_and_push_without_duplicates():
    assert sids_for_group(GROUPS, PUSH, "111") == ["s1", "s2"]
    assert sids_for_group(GROUPS, PUSH, "222") == ["s2", "s3", "s9"]


def test_groups_for_sid_includes_primary_and_push_targets():
    assert groups_for_sid(GROUPS, PUSH, "s2") == ["111", "222"]
    assert groups_for_sid(GROUPS, PUSH, "s9") == ["222"]
    assert groups_for_sid(GROUPS, PUSH, "unknown") == []


def test_primary_group_is_first_direct_owner():
    assert primary_group_of(GROUPS, "s3") == "222"
    assert primary_group_of(GROUPS, "s9") is None


def test_all_sids_distinguishes_primary_only_view():
    assert all_group_sids(GROUPS) == {"s1", "s2", "s3"}
    assert all_sids(GROUPS, PUSH) == {"s1", "s2", "s3", "s9"}
