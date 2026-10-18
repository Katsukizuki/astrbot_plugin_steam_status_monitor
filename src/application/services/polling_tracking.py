import asyncio
import time
from datetime import datetime

from ...shared.logging import logger


class PollingTrackingMixin:
    """Global polling scheduling and startup initialization orchestration."""

    async def init_poll_time_once(self):
        '''插件启动后10秒内进行一次全员初始化轮询，设置每个SteamID的next_poll_time，并输出一次初始日志'''
        await asyncio.sleep(10)
        all_logs = []
        # 一次性标记"插件停止期间遗留的陈旧群"：init 过程中 check_status_change 末尾会刷新
        # states.json 的 mtime，若不预先缓存，同一次 init 内后续 sid 会因 mtime 已刷新而漏判。
        # init 完成后（含异常）清空，后续正常轮询不再跳过播报。
        self._startup_stale_groups = {
            gid: self._is_group_state_stale(gid)
            for gid in self.group_steam_ids
        }
        try:
            # Steam 状态查询按 SID 去重，但状态基线必须按群分别建立。
            unique_sids = list(dict.fromkeys(
                sid
                for gid, steam_ids in self.group_steam_ids.items()
                if self.group_monitor_enabled.get(gid, True)
                for sid in steam_ids
            ))
            status_map = await self.fetch_player_statuses_batch(unique_sids) if unique_sids else {}
            for group_id in self.group_steam_ids:
                if not self.group_monitor_enabled.get(group_id, True):
                    continue
                steam_ids = self.group_steam_ids[group_id]
                group_lines = []
                for sid in steam_ids:
                    # 状态基线按群保存；即使同一玩家属于多个群，也必须逐群初始化。
                    msg = await self.check_status_change(
                        group_id,
                        single_sid=sid,
                        status_override=status_map.get(sid),
                        skip_push=True,
                    )
                    if msg:
                        group_lines.append(msg)
                if group_lines:
                    all_logs.append(f"群{group_id}：\n" + "\n".join(group_lines))
            if all_logs:
                logger.info("====== Steam状态监控初始化日志 ======\n" + "\n".join(all_logs) + "\n=====================================================")
        finally:
            self._startup_stale_groups.clear()

    async def global_poll_and_log_loop(self):
        """全局定时轮询：每分钟对齐整分钟，跨群合并查询并统一输出日志。"""
        while True:
            try:
                await self._sleep_to_next_minute()
                group_sids, now = self._collect_due_sids()
                await self._maybe_push_daily_rank()
                await self._flush_state_if_due()
                # 离线玩家可能数十分钟才再入轮询，deadline 必须每分钟单独检查。
                # 必须在 Steam 请求之前结算并立刻 flush，否则超时会把结束卡拖到下一局开始才发出。
                await self.session_service.tick_due(int(now))
                self._settle_pending_offline_presence(int(now))
                await self._flush_pending_end_notifications()
                if not group_sids:
                    await asyncio.sleep(40)  # 本轮无到点，跳过
                    continue
                await self._poll_groups(group_sids)
                await self._flush_pending_end_notifications()
                await asyncio.sleep(40)
                self._emit_round_logs()
            except asyncio.CancelledError:
                logger.info("[SteamStatusMonitor] 主轮询循环已取消")
                raise
            except Exception:
                # 保留堆栈后继续循环，防止单次异常导致轮询彻底失效
                logger.exception("[SteamStatusMonitor] 主轮询循环异常，5 秒后继续")
                await asyncio.sleep(5)

    async def _sleep_to_next_minute(self):
        """对齐到下一个整分钟，保证 tick_due 与批量查询的节奏稳定。"""
        now = time.time()
        next_minute = (int(now) // 60 + 1) * 60
        await asyncio.sleep(max(0, next_minute - now))

    def _collect_due_sids(self):
        """跨群收集本轮到点的 SteamID。

        Returns:
            ({group_id: [sid, ...]}, now)。只包含已启动且开启监控的群。
        """
        group_sids = {}
        now = time.time()
        for group_id in list(self.running_groups):
            if not self.group_monitor_enabled.get(group_id, True):
                continue
            next_poll = self.next_poll_time.setdefault(group_id, {})
            due = list(dict.fromkeys(
                sid
                for sid in self.group_steam_ids.get(group_id, [])
                if now >= next_poll.get(sid, 0)
            ))
            if due:
                group_sids[group_id] = due
        return group_sids, now

    async def _maybe_push_daily_rank(self):
        """到达配置的推送时刻且当天未推过时，异步触发昨日排行榜。"""
        now_dt = datetime.now()
        push_hour = self.rank_push_hour
        push_minute = self.rank_push_minute
        if now_dt.hour != push_hour or now_dt.minute != push_minute:
            return
        push_date_key = self.ranking_service.day_key(-1)
        if self._last_rank_push_date == push_date_key:
            return
        if not (self.rank_push_groups or self.rank_push_all):
            return
        self._last_rank_push_date = push_date_key
        logger.info(
            f"[排行榜] 开始每日自动推送，时间={push_hour}:{push_minute:02d}，"
            f"目标群: {self.rank_push_groups if self.rank_push_groups else '全部群(rank_push_all)'}"
        )
        asyncio.create_task(self.rank_view.push_daily())

    async def _flush_state_if_due(self):
        """节流落盘：有脏数据且超过保存间隔时写盘，避免每次状态变更都 IO。"""
        if not self._data_dirty:
            return
        if (time.time() - self._last_save_time) < self._save_interval:
            return
        try:
            self._save_persistent_data(force=True)
        except Exception as exc:
            logger.error(f"[SteamStatusMonitor] 节流保存失败: {exc}")

    async def _poll_groups(self, group_sids):
        """批量查询一次状态，并让各群并发消费同一份结果。"""
        all_sids = list(dict.fromkeys(
            sid for sids in group_sids.values() for sid in sids
        ))
        status_map = await self._fetch_statuses_while_ticking(all_sids)

        async def query_one_group(gid, sids):
            if not self.group_monitor_enabled.get(gid, True):
                return
            tasks = [
                self.check_status_change(
                    gid,
                    single_sid=sid,
                    status_override=status_map.get(sid),
                )
                for sid in sids
            ]
            if not tasks:
                return
            results = await asyncio.gather(*tasks, return_exceptions=True)
            lines = []
            for message in results:
                if isinstance(message, Exception):
                    logger.error(f"[轮询] check_status_change 异常: {message} (gid={gid})")
                elif message:
                    lines.append(message)
            if lines:
                self._last_round_logs.append((gid, "\n".join(lines)))

        await asyncio.gather(
            *(query_one_group(gid, sids) for gid, sids in group_sids.items()),
            return_exceptions=True,
        )

    def _emit_round_logs(self):
        """本轮日志统一输出：详细模式按群展开，否则只报一句成功。"""
        if not self._last_round_logs:
            return
        if self.detailed_poll_log:
            logs = [f"群{group_id}：\n{log_text}" for group_id, log_text in self._last_round_logs]
            logger.info(
                "====== Steam状态监控轮询日志 ======\n"
                + "\n".join(logs)
                + "\n====================================================="
            )
        else:
            logger.info("周期轮询成功")
        self._last_round_logs.clear()

    async def _fetch_statuses_while_ticking(self, steam_ids, tick_interval=1.0):
        """拉取 Steam 状态期间继续结算到期会话，避免超时把结束卡堵住。"""
        if not steam_ids:
            return {}
        fetch_task = asyncio.create_task(self.fetch_player_statuses_batch(steam_ids))
        try:
            while not fetch_task.done():
                try:
                    return await asyncio.wait_for(asyncio.shield(fetch_task), timeout=tick_interval)
                except asyncio.TimeoutError:
                    await self.session_service.tick_due(int(time.time()))
                    await self._flush_pending_end_notifications()
            return fetch_task.result()
        except asyncio.CancelledError:
            fetch_task.cancel()
            raise

