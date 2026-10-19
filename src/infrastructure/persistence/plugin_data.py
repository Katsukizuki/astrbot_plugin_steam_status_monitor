import json
import os
import time

from ...shared.logging import logger
from ...shared.timeutil import business_day_key
from ...shared.utils.notify_session import (
    build_group_notify_session,
    is_sendable_group_session,
    is_valid_group_id,
)

_STALE_STATE_THRESHOLD = 3600
_MISSING = object()


class PersistenceMixin:
    # ========== JSON 读写边界 ==========

    def _read_json(self, filename, default=_MISSING):
        """读取 data 目录下的 JSON；缺失或损坏时返回 default（缺省为空 dict）。"""
        fallback = {} if default is _MISSING else default
        path = os.path.join(self.data_dir, filename)
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
        except FileNotFoundError:
            return fallback
        except (OSError, ValueError) as exc:
            logger.warning("加载 %s 失败: %s", filename, exc)
            return fallback

    def _write_json(self, filename, data, *, indent=None) -> bool:
        """写入 data 目录下的 JSON；失败只记录日志并返回 False。"""
        path = os.path.join(self.data_dir, filename)
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle, ensure_ascii=False, indent=indent)
            return True
        except OSError as exc:
            logger.warning("保存 %s 失败: %s", filename, exc)
            return False

    def _group_file_name(self, group_id, key) -> str:
        return f"group_{group_id}_{key}.json"

    def _get_group_data_path(self, group_id, key):
        return os.path.join(self.data_dir, self._group_file_name(group_id, key))

    def _is_group_state_stale(self, group_id, threshold=_STALE_STATE_THRESHOLD):
        """判断该群状态缓存是否为插件停止期间遗留的旧数据。"""
        try:
            path = self._get_group_data_path(group_id, "states")
            if not os.path.exists(path):
                return False
            mtime = os.path.getmtime(path)
            return mtime < self._startup_time and (time.time() - mtime) > threshold
        except OSError as exc:
            logger.warning(f"[陈旧状态] 判断 states 新鲜度失败: {exc} (group_id={group_id})")
            return False

    # ========== 启动加载 ==========

    def _load_persistent_data(self):
        legacy_start_play_times = {}
        legacy_pending_quit = {}
        for group_id in self.group_steam_ids:
            states = self._read_json(self._group_file_name(group_id, "states"), None)
            if states is not None:
                self.group_last_states[group_id] = states
            start_times = self._read_json(self._group_file_name(group_id, "start_play_times"), None)
            if start_times is not None:
                legacy_start_play_times[group_id] = start_times
            quit_times = self._read_json(self._group_file_name(group_id, "last_quit_times"), None)
            if quit_times is not None:
                self.group_last_quit_times[group_id] = quit_times
            pending_logs = self._read_json(self._group_file_name(group_id, "pending_logs"), None)
            if pending_logs is not None:
                self.group_pending_logs[group_id] = pending_logs
            pending_quit = self._read_json(self._group_file_name(group_id, "pending_quit"), None)
            if pending_quit is not None:
                legacy_pending_quit[group_id] = pending_quit
            recent_games = self._read_json(self._group_file_name(group_id, "recent_games"), None)
            if recent_games is not None:
                self.group_recent_games[group_id] = recent_games

        session_path = os.path.join(self.data_dir, "playing_sessions.json")
        has_session_store = os.path.exists(session_path)
        if has_session_store:
            payload = self._read_json("playing_sessions.json", None)
            if payload is not None:
                self.session_service.load(payload)
        # 旧 pending_quit/start_play_times 只允许在首次升级时迁移一次。
        # playing_sessions.json 一旦存在就是唯一数据源，否则旧文件中的 notified=false
        # 会在每次重启时把已结算会话重新复活并重复发送结束卡。
        if not has_session_store:
            self.session_service.hydrate_from_legacy(
                pending_all=legacy_pending_quit,
                start_all=legacy_start_play_times,
                last_all=self.group_last_states,
            )
            self._write_json("playing_sessions.json", self.session_service.dump())
        # 在线起始时间与下线防抖待定（presence）：跨重启保持计时与确认窗
        self._presence_online_since = self._read_json("presence_online_since.json")
        self._presence_pending_offline = self._read_json("presence_pending_offline.json")

    def _save_persistent_data(self, force=False):
        '''分群保存各群的状态数据。
        - force=True 或距上次保存超过 _save_interval 才真正落盘
        - 否则只标记脏位，由主循环周期性 flush
        '''
        if not force and (time.time() - self._last_save_time) < self._save_interval:
            self._data_dirty = True
            return
        self._data_dirty = False
        self._last_save_time = time.time()
        for group_id in self.group_steam_ids:
            self._write_json(
                self._group_file_name(group_id, "states"),
                self.group_last_states.get(group_id, {}),
            )
            self._write_json(
                self._group_file_name(group_id, "last_quit_times"),
                self.group_last_quit_times.get(group_id, {}),
            )
            self._write_json(
                self._group_file_name(group_id, "pending_logs"),
                self.group_pending_logs.get(group_id, {}),
            )
            self._write_json(
                self._group_file_name(group_id, "recent_games"),
                self.group_recent_games.get(group_id, []),
            )
        self._write_json("playing_sessions.json", self.session_service.dump())
        self._write_json("presence_online_since.json", self._presence_online_since)
        self._write_json("presence_pending_offline.json", self._presence_pending_offline)
        self._save_play_records()
        self._save_session_records()

    # ========== 通知目标 ==========

    def _load_notify_session(self):
        self.notify_sessions = self._read_json("notify_sessions.json")
        logger.info(f"[SteamStatusMonitor] 已加载 notify_sessions: {self.notify_sessions}")
        self._sanitize_notify_sessions()

    def _save_notify_session(self):
        if self._write_json("notify_sessions.json", self.notify_sessions):
            logger.info(f"[SteamStatusMonitor] 已保存 notify_sessions: {self.notify_sessions}")

    def _record_platform_id(self, event):
        """从消息事件中提取并缓存平台ID，用于WebUI自动构造通知目标"""
        if self._platform_id:
            return
        self._platform_id = event.unified_msg_origin.split(":")[0]
        self._auto_fill_notify_sessions()

    def _auto_fill_notify_sessions(self):
        """为WebUI添加的群（有群有ID但无notify_sessions）自动构造通知目标"""
        if not self._platform_id:
            return
        filled = 0
        group_ids = set(self.group_steam_ids or {})
        for targets in (self.push_groups or {}).values():
            group_ids.update(str(gid) for gid in (targets or []))
        for gid in group_ids:
            if not is_valid_group_id(gid):
                continue
            if is_sendable_group_session(self.notify_sessions.get(gid)):
                continue
            self.notify_sessions[gid] = build_group_notify_session(self._platform_id, gid)
            filled += 1
        if filled:
            self._save_notify_session()
            logger.info(f"[WebUI自动投递] 已为 {filled} 个群补全通知目标")

    def _sanitize_notify_sessions(self):
        cleaned = {}
        dropped = []
        for gid, session in (self.notify_sessions or {}).items():
            if is_valid_group_id(gid) and is_sendable_group_session(session):
                cleaned[str(gid)] = session
            else:
                dropped.append(gid)
        self.notify_sessions = cleaned
        if dropped:
            logger.warning("已丢弃无效 notify_sessions: %s", dropped)
            self._save_notify_session()

    # ========== 群名单与推送路由 ==========

    def _sanitize_group_steam_ids(self):
        cleaned = {}
        dropped = []
        for gid, steam_ids in (self.group_steam_ids or {}).items():
            if is_valid_group_id(gid):
                cleaned[str(gid)] = steam_ids
            else:
                dropped.append(gid)
        if dropped:
            self.monitor_state.group_steam_ids = cleaned
            logger.warning("已丢弃无效监控群: %s", dropped)
            self._save_group_steam_ids()

    def _load_group_steam_ids(self):
        """从 steam_groups.json 加载所有群的 SteamID 列表"""
        groups = self._read_json("steam_groups.json")
        self.monitor_state.group_steam_ids = groups
        self._sanitize_group_steam_ids()

    def _save_group_steam_ids(self):
        """保存所有群的 SteamID 列表到 steam_groups.json"""
        groups = self.monitor_state.group_steam_ids
        if self._write_json("steam_groups.json", groups, indent=2):
            logger.info(f"[SteamStatusMonitor] 已保存 steam_groups.json: {groups}")

    def _load_push_groups(self):
        """加载 SteamID -> 群号列表 的推送映射"""
        self.push_groups = self._read_json("push_groups.json")

    def _save_push_groups(self):
        """保存 SteamID -> 群号列表 的推送映射"""
        self._write_json("push_groups.json", self.push_groups, indent=2)

    def _load_group_switches(self):
        """加载各群监控/成就开关。缺省为开启，避免旧数据把群误关。"""
        payload = self._read_json("group_switches.json") or {}
        self.group_monitor_enabled = {
            str(gid): bool(enabled)
            for gid, enabled in (payload.get("monitor") or {}).items()
            if is_valid_group_id(gid)
        }
        self.group_achievement_enabled = {
            str(gid): bool(enabled)
            for gid, enabled in (payload.get("achievement") or {}).items()
            if is_valid_group_id(gid)
        }

    def _save_group_switches(self):
        payload = {
            "monitor": {
                str(gid): bool(enabled)
                for gid, enabled in (self.group_monitor_enabled or {}).items()
                if is_valid_group_id(gid)
            },
            "achievement": {
                str(gid): bool(enabled)
                for gid, enabled in (self.group_achievement_enabled or {}).items()
                if is_valid_group_id(gid)
            },
        }
        self._write_json("group_switches.json", payload, indent=2)

    # ========== 排行榜账本 ==========

    def _load_play_records(self):
        self.play_records = self._read_json("play_records.json")

    def _save_play_records(self):
        """保存游玩时长记录，并自动清理超过30天的旧记录"""
        cutoff_date = business_day_key(-30)
        self.play_records = {
            date_str: data
            for date_str, data in self.play_records.items()
            if date_str >= cutoff_date
        }
        self._write_json("play_records.json", self.play_records)

    def _load_session_records(self):
        self.session_records = self._read_json("session_records.json")

    def _save_session_records(self):
        """保存 session 记录，自动清理超过90天的旧数据"""
        cutoff_date = business_day_key(-90)
        self.session_records = {
            sid: [item for item in sessions if item.get("date", "") >= cutoff_date]
            for sid, sessions in self.session_records.items()
        }
        self._write_json("session_records.json", self.session_records)

    # ========== QQ-SteamID 绑定 ==========

    def _load_bind_data(self):
        self._bind_data = self._read_json("bind_data.json")

    def _save_bind_data(self):
        self._write_json("bind_data.json", self._bind_data)

    def _resolve_bind_name(self, sid, steam_name=None):
        """根据绑定表返回显示名：自定义备注 > Steam原始名 > SteamID"""
        for info in self._bind_data.values():
            if info.get("sid") == str(sid):
                nick = info.get("nickname", "")
                if nick and nick != "*":
                    return nick
                break
        return steam_name or str(sid)

    # ========== 排行榜推送群 ==========

    def _load_rank_push_groups(self):
        """加载开启了每日排行榜推送的群列表及 rank_push_all 标志"""
        raw = self._read_json("rank_push_groups.json", None)
        if isinstance(raw, dict):
            self.rank_push_groups = raw.get("groups", [])
            self.rank_push_all = raw.get("all", False)
        elif isinstance(raw, list):
            # 兼容旧格式（纯列表）
            self.rank_push_groups = raw
            self.rank_push_all = False
        else:
            self.rank_push_groups = []
            self.rank_push_all = False

    def _save_rank_push_groups(self):
        """保存开启了每日排行榜推送的群列表及 rank_push_all 标志"""
        self._write_json(
            "rank_push_groups.json",
            {"groups": self.rank_push_groups, "all": self.rank_push_all},
        )
