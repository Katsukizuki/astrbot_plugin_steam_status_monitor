# 代码 Review 与质量优化 Plan

- 评审范围：`src/` 全部 83 个 Python 文件、15,826 行（`tests/` 7,713 行不在此次优化范围，但作为验收基线）
- 评审视角：重复实现、兜底逻辑、命名与注释、死代码、函数复杂度、嵌套、硬编码
- 结论定位：**不改功能、不改命令、不改数据格式**，只做可验证的结构与表达优化
- 关联文档：`REFACTORING.md`（模块拆分）、`docs/adr/adr-command-layer-split.md`、`docs/adr/adr-inline-passthrough-helpers.md`

---

## 0. 结论摘要

架构分层已经清晰，本轮问题不在"文件大"，而在**同一件事被写了多遍**和**防御性代码层层叠加**：

1. **7 处**重复实现"群名单 + 推送组 → SteamID 集合"的查询（`player_status_view` / `monitor_admin` / `ranking` / `statistics`×3 / `admin_api`），这是全项目复用率最高、也最容易改漏的一条规则。
2. **渲染层存在三套平行实现**：头像下载 3 份、竖版封面/SGDB 抓取 2 份、文本换行 4 份、渐变背景 2 份、头像框解析 3 份。`game_end.py` 的副本已和 `game_start.py` 产生行为差异（缺少 URL 编码）。
3. **大量"永不触发"的兜底**：懒初始化 dict（会静默分裂状态）、`getattr(plugin, "xxx", None)`（属性在 `__init__` 必然存在）、测试才需要的 `_record_playtime` 分支（该函数生产代码根本不存在）。
4. **异常被吞掉**：`except Exception: return []`（排行聚合）、`except: pass`（3 处裸 except）、82 处 `print()` 调试输出绕过统一脱敏 logger。
5. **一个真实的潜在崩溃**：`achievement_monitor.py:390` 以错误参数个数调用 `get_player_achievements`，该分支一旦被走到必抛 `TypeError`；目前因调用方总会传 `unlocked_set` 而不可达，属于"死分支里藏着 bug"。

优化总量预估：可删除约 **600–800 行**（重复 + 死代码 + 调试输出），新增约 **350–450 行**（共享模块 + docstring），净减约 300 行，但**改动集中在 20 个文件、4 个批次**，每批可独立回归。

---

## 1. 现状度量

| 指标 | 数值 | 说明 |
| --- | --- | --- |
| `getattr(...)` 调用 | 213 | 其中约 158 处是对 `self` / `plugin` 的属性兜底 |
| `except Exception` | 206 | 其中约 30 处只 `return` 默认值，未记录日志 |
| 裸 `except:` | 3 | `game_end.py:177/199`、`game_start.py:86` |
| `print(` | 82 | 集中在 `game_start`(39)、`game_end`(22)、`achievement_monitor`(12) |
| `config.get(key, 默认值)` | 75 | 默认值分散在 12 个文件，与 `_conf_schema.json` 不联动 |
| ≥60 行函数 | 44 | 最长 331 行（`render_achievement_image`） |
| 生产 0 调用的模块 | 2 | `domain/monitoring/transitions.py`、`domain/price/models.py` |

---

## 2. 问题与你的 7 条要求对照

| 要求 | 本方案章节 | 主要产出 |
| --- | --- | --- |
| 1 重复功能提取 / 同族方法聚拢 | §3 A1–A21 | 6 个新共享模块 + 14 处合并 |
| 2 兜底逻辑审验（a/b/c/d） | §4 B1–B5 | 删除 23 处、合并 9 处、补 3 处 |
| 3 命名与注释 | §5 | 改名 26 个、枚举化 1 处、注释与 docstring 规范 |
| 4 删除无用方法 | §6 | 15 个符号 + 2 个文件 |
| 5 长函数拆分 | §7 | 14 个重点函数拆分方案 |
| 6 减少嵌套、提前返回 | §8 | 6 处典型改写 |
| 7 硬编码集中 | §9 | 9 组常量集中 |

---

## 3. A 组：重复功能提取

原则（对齐 `adr-inline-passthrough-helpers`）：**只抽"有规则、有副作用、被多处调用"的东西**；一行的纯转发不抽，避免把刚删掉的中介层加回来。

### A1 群名单 + 推送组查询（最高优先级）

同一段规则被抄了 7 遍：`直接成员 ∪ 该群的 push_group 成员`。

| 位置 | 函数 | 形态 |
| --- | --- | --- |
| `application/services/player_status_view.py:132` | `steam_ids_for_group` | 返回三元组 |
| `application/services/monitor_admin.py:514` | `list_group_players` | 内联推导 |
| `application/services/ranking.py:224` | `target_sids` | 内联推导 |
| `presentation/web/statistics.py:119` | `build_groups` | 内联推导 |
| `presentation/web/statistics.py:148` | `build_player_search_index` | 内联推导 |
| `presentation/web/statistics.py:311` | `_resolve_allowed_sids` | 内联推导 |
| `application/services/notification_tracking.py:37` | `monitored_groups` | 反向查询（sid → 群） |

**动作**：新建 `src/domain/monitoring/roster.py`（纯函数、无插件依赖）：

```python
def sids_for_group(groups, push_groups, group_id) -> list[str]
def groups_monitoring_sid(groups, push_groups, sid) -> list[str]
def all_monitored_sids(groups, push_groups) -> set[str]
def direct_sids(groups, group_id) -> list[str]
```

7 处调用点改为调用它，`player_status_view.steam_ids_for_group` 保留为读模型方法（它同时返回 direct/push/combined 三元组，是命令层需要的形状），内部改为组合上述函数。

**收益**：一处修改对所有入口生效；`statistics` 与 `monitor_admin` 不会再出现"某处忘了算 push 群"的偏差。

### A2 头像下载（3 份）

| 位置 | 签名差异 |
| --- | --- |
| `presentation/renderers/game_start.py:24` `get_avatar_path` | 返回路径，带 TTL 刷新 |
| `presentation/renderers/rank.py:108` `_fetch_avatar` | 返回 `Image`，无 TTL |
| `presentation/renderers/steam_list.py:66` `fetch_avatar` | 返回 `Image`，无 TTL |

**动作**：`presentation/renderers/assets.py`

```python
async def ensure_cached_file(data_dir, subdir, filename, url, *, ttl_seconds=0, proxy=None) -> str | None
async def load_cached_image(data_dir, subdir, filename, url, *, proxy=None) -> Image.Image | None
```

`ensure_cached_file` 承载 TTL 判断（`ttl==0` 表示不过期），`load_cached_image` 复用它再 `Image.open`。TTL 由调用方传入（见 §9 缓存配置接线），不在函数内读全局 `_cache_config`。

### A3 竖版封面与 SGDB 抓取（2 份，已产生行为差异）

`game_end.py:30/94` 与 `game_start.py:127/211` 是同一算法的两份拷贝，差异：

- `game_start` 版本对搜索词做了 `urllib.parse.quote`，`game_end` 版本没有 → **游戏名含空格/`&`/中文标点时 `game_end` 的 SGDB 搜索会失败或命中错误条目**；
- `game_end` 版本的兜底分支被删减（主查无封面时直接 `return None`），实际可用率更低；
- 参数名不同：`steam_api_base` vs `steam_store_base`，调用方容易传错。

**动作**：删除 `game_end.py` 的 `get_sgdb_vertical_cover` / `get_cover_path` / `render_gradient_bg` / `text_wrap`，从 `game_start` 或新的 `renderers/covers.py` 导入。这同时修复上述 URL 编码缺陷（**属于行为修复，需在验收单列**）。

### A4 文本换行与截断（4 + 2 份）

- 换行：`achievement_monitor.py:341`、`game_detail.py:83`、`game_end.py:214`、`game_start.py:295`
- 截断：`rank.py:91` `_truncate_text`、`steam_list.py:159` `_fit_text`（二分查找版本，更优）

**动作**：`presentation/renderers/text.py`

```python
def wrap_text(draw, text, font, max_width, max_lines=None, ellipsis="…") -> list[str]
def truncate_text(draw, text, font, max_width, ellipsis="…") -> str
```

保留 `steam_list._fit_text` 的二分实现，`rank._truncate_text` 的线性实现删除。

### A5 渐变背景（2 份逐字相同）

`game_start.py:332` 与 `game_end.py:80` 完全一致，且都是 `putpixel` 双重循环（512×192 = 98k 次调用，可改用 `Image.linear_gradient` 或按行 `draw.line`）。

**动作**：`renderers/background.py::vertical_gradient(width, height, top, bottom)`，顺带改为按行绘制（`rank.py:305` 已有按行实现，统一到这一份）。

### A6 `_async_none`（3 份）

`game_start.py:581`、`steam_list.py:71`、`rank.py:494` 各定义一个"异步返回 None"。

**动作**：删除。调用点改为先建任务再过滤：

```python
tasks = [fn(...) for fn in (...) if condition]
results = await asyncio.gather(*tasks)
```

或在 `gather` 前用 `None` 占位并 `if task is not None`。不要为占位新建共享工具函数。

### A7 结束提示文案（3 份阶梯）

| 位置 | 内容 |
| --- | --- |
| `application/services/session_quit.py:14` `_end_game_tip` | 11 级阶梯（生产用） |
| `presentation/commands/ops.py:266-287` | 同样的 11 级阶梯（测试命令用） |
| `application/services/notification_tracking.py:104` | 硬编码最后一级文案作 fallback |

**动作**：`session_quit._end_game_tip` 提为模块级函数 `end_game_tip(duration_min)` + 常量 `DEFAULT_END_TIP`，`ops` 与 `notification_tracking` 都调用它。`SessionQuitMixin` 只保留 `session_service` 属性（ADR 已说明该 Mixin 暂时不能删）。

### A8 头像框解析（3 份 + 1 变体）

`game_start.py:544`、`game_end.py:416`、`owned_game.py:339` 各有一个 `resolve_avatar_frame()` 闭包；`rank_view.py:83` 是同样的"先查本地，再取 URL，再下载"逻辑的 dict 版本。

**动作**：`renderers/assets.py::resolve_avatar_frame(data_dir, sid, proxy)` 返回路径；`rank_view.collect_frames` 循环调用它。

### A9 业务日边界（4:00）散落 5 处

| 位置 | 表达式 |
| --- | --- |
| `application/services/ranking.py:85` | `if current.hour < 4: -1 day` |
| `presentation/web/statistics.py:21` | `now.replace(hour=4...)` 前后推 |
| `application/services/owned_games.py:30,608-618,630-635` | `DAY_ROLLOVER_HOUR = 4` + 手写回退 |
| `infrastructure/persistence/plugin_data.py:454` | 又手写一遍 |
| `presentation/web/admin_api.py:429,556` | `+ 4 * 3600` / `replace(hour=4)` |

**动作**：`src/shared/timeutil.py`

```python
DAY_ROLLOVER_HOUR = 4
def business_day_key(offset_days=0, now=None) -> str
def business_day_start(now=None) -> datetime
def seconds_to_next_business_day(now=None) -> int
```

`RankingService.day_key` 保留为薄调用（它已注入 clock，测试依赖），内部改用 `business_day_key`；其余 4 处全部替换。

### A10 `session_id` 生成（2 份）

`ranking.py:119` 与 `plugin_data.py:433` 生成完全相同的 `f"{date}_{start}_{gameid}"`。

`plugin_data._record_session` 是"没有 ranking_service 时"的回退，而 `ranking_service` 在 `__init__` 必然存在（见 B1-4）。

**动作**：删除 `plugin_data._record_session`，`session_id` 生成收敛到 `RankingService`（可提为 `RankingService.build_session_id()` 供测试直接断言）。

### A11 排行聚合（3 份）

`ranking.aggregate()` 之外，还有两份内联聚合：

- `admin_api.py:294-324`（`_api_dashboard_rank_image`）：按 `play_records` 逐日累加 → 排序 → 取前 25；
- `admin_api.py:421-448`（`_api_gantt_data`）：把 `play_records` 按分钟伪造成时间片。

**动作**：前者改调 `plugin.ranking_service.aggregate(days, sids, base_day_offset)` + `rank_view.enrich`（`rank_view.render_aggregated` 已是现成路径，补充 `render_only` 分支即可）。后者保留"伪造时间片"逻辑（语义不同于聚合），但把"从 play_records 生成时间片"抽为 `statistics.build_play_record_segments(day_data)`。

### A12 名单清空（2 份）

`commands/ops.py:131` (`/steam rs`) 与 `monitor_admin.clear_all_ids()` 都手工清 15 个 dict。

**动作**：`MonitorAdminService.reset_runtime_state()` 单一入口；`ops.rs` 调用它，并额外清 `superpower` / `_game_name_cache` 这类纯展示缓存（通过 `plugin` 的可选钩子，不在 admin 服务里直接摸两个缓存字段）。

### A13 通知目标解析（2 份）

`notification_tracking._get_notify_sessions`（含 push 群、可发送性过滤）与 `achievement_tracking.notify_new_achievements:112-120`（只有主群 + push 群，且 key 未做 `str()` 归一化）。

**动作**：`_get_notify_sessions` 提为模块函数 `resolve_notify_sessions(plugin, group_id, sid)`，成就通知复用；顺带修掉成就通知里 `push_groups` 用原始 key 取值、与主流程 `str()` 归一化不一致的问题。

### A14 状态枚举（4 张表）

| 位置 | 表 | 用途 |
| --- | --- | --- |
| `player_status_view.py:8` | `_PERSONA_STATUS` | code → status 字符串 |
| `formatters/status.py:4` | `_PERSONA_TEXT` / `_PERSONA_ICON` | code → 中文/图标 |
| `openbox.py:35` | `_PERSONA_STATES` | code → 中文（多 5/6 两档） |
| `steam_list.py:35,123` | `STATUS_COLORS` / `get_status_text` | status → 颜色/中文 |

**动作**：`domain/monitoring/presence.py` 增加：

```python
PERSONA_STATUS_BY_CODE = {0: "offline", 1: "online", 2: "busy", 3: "away", 4: "snooze"}
PERSONA_LABELS = {"offline": "离线", "online": "在线", "busy": "忙碌", "away": "离开", "snooze": "打盹", "playing": "游戏中", "error": "异常"}
def persona_status(code) -> str
def persona_label(status) -> str
```

渲染层只保留颜色表（颜色属于展示决定，不下沉到领域层）。

### A15 JSON 读写样板（14 个函数）

`plugin_data.py` 里 `_load_play_records` / `_save_play_records` / `_load_session_records` / `_save_session_records` / `_load_bind_data` / `_save_bind_data` / `_load_push_groups` / `_save_push_groups` / `_load_rank_push_groups` / `_save_rank_push_groups` / `_load_group_switches` … 每个都在重复 `os.path.exists` + `open` + `try/except` + `logging.warning`。

**动作**：统一到已有的 `_load_plain_json` / `_save_plain_json` 升级版：

```python
def _read_json(self, filename, default, *, indent=None) -> Any
def _write_json(self, filename, data, *, indent=None) -> bool
```

- 用 `try/except FileNotFoundError → default` 取代 `os.path.exists` 预检（B3-1）；
- 写入失败返回 `False` 由调用方决定是否重试，而不是只打日志。

预计从 14 个函数（约 180 行）压到 6 个函数（约 90 行）。

### A16 Web 层样板（15 + 7 处）

- `data = await request.json()` + `except → 400` 出现 **15 次**；
- `int(request.query.get("x", "d"))` + `except → d` 出现 **7 次**。

**动作**：`presentation/web/http.py`

```python
async def read_json_object(request) -> tuple[dict | None, Response | None]
def query_int(request, key, default, *, minimum=None, maximum=None) -> int
```

15 处改为 `payload, error = await read_json_object(request); if error: return error`（或写一个 `@requires_json` 装饰器，与现有 `safe_api` 组合）。

### A17 配置整数解析（`owned_games` 4 份）

`_interval` / `_max_per_notify` / `_grace_seconds` / `_daily_budget` 结构完全相同：`try int(...) except (TypeError, ValueError)` + 默认值 + `max/min` 夹取。

**动作**：`_config_int(key, default, *, minimum=None, maximum=None)`，4 个方法各保留 1 行语义化夹取。

### A18 `DEFAULT_MENU_COMMANDS`（2 份）

`application/services/qq_menu_management.py:15` 与 `presentation/web/qqofficial_settings.py:9` 是同一份 5 项菜单。

**动作**：从 `qqofficial_settings` 导入（它已经是配置默认值的归属地）。

### A19 时长格式化（4 份）

| 位置 | 输出 |
| --- | --- |
| `formatters/status.py:35` `format_play_duration` | `1.5小时` / `45.0分钟` |
| `player_status_view.py:50` | `1.5小时` / `45.0分钟`（重复实现） |
| `renderers/rank.py:83` `_format_hours` | `1.5h` / `45min` |
| `renderers/game_end.py:394` | `已玩1.5小时` / `已玩45分钟` |

**动作**：`shared/utils/duration.py::format_duration(minutes, *, style="cn" | "compact")`；`player_status_view`、`game_end` 复用，`rank` 用 `compact`。

### A20 默认 base URL（4 组重复）

`https://api.steampowered.com` 出现在 `runtime_config.py:85`、`achievement_monitor.py:17,19`、`admin_api.py:1303,1414`、`steam_cover.py:27`；store/sgdb 同理。

**动作**：`shared/endpoints.py`

```python
STEAM_API_BASE = "https://api.steampowered.com"
STEAM_STORE_BASE = "https://store.steampowered.com"
SGDB_API_BASE = "https://www.steamgriddb.com"
ITAD_API_BASE = "https://api.isthereanydeal.com"
```

### A21 显示名解析（3 份，1 份还是错的）

- `plugin_data._resolve_bind_name:483`（生产用）
- `statistics._build_display_names:30`（批量版，合理）
- `admin_api._get_player_display_name:46` —— 与 `_resolve_bind_name` 同义，且第 57-65 行**连写两个语义相同的循环**（第二个是第一个的超集），纯冗余。

**动作**：`admin_api._get_player_display_name` 删除，改调 `plugin._resolve_bind_name`；批量场景保留 `_build_display_names`，但内部改为构建 `sid → nickname` 映射后复用同一优先级规则（备注 > Steam 名 > sid）。

---

## 4. B 组：兜底逻辑审验

判定口径：
- **B-1 不可触发** → 删除（保留只会掩盖真实 bug）；
- **B-2 连续相同守卫** → 合并为一条表达式；
- **B-3 上游已有保护** → 删除下游重复校验；
- **B-4 过度默认值** → 移除永不生效的 `or 默认值`；
- **B-5 缺失兜底** → 补上（本轮只补 3 处会造成崩溃的）。

### B-1 触发条件实际不可能（删除）

| # | 位置 | 现状 | 判定依据 |
| --- | --- | --- | --- |
| B1-1 | `session_service.py:236-248` `_sessions` / `_meta` | 取不到就新建 `{}` 并回写 plugin | `__init__` 必然初始化 `playing_sessions` / `_session_meta`；凭空新建会让"锁内读到空字典"静默丢会话 |
| B1-2 | `domain/monitoring/state.py:31-36` `_state()` | 取不到就新建 store | `MonitorStateStore` 是 `__init__` 第一行赋值；`test_monitor_state` 也显式赋值 |
| B1-3 | `session_service.py:294-319` `_on_closed` | 没有 `ranking_service` 时退回 `_record_playtime` / `_record_session` | `ranking_service` 在 `__init__` 第 129 行创建；且 `_record_playtime` **全项目无定义**，该分支即使命中也是空操作 |
| B1-4 | `plugin_data.py:422-447` `_record_session` | 无 ranking 时手写一份记账 | 同 B1-3，且与 `ranking.record_session` 逐字重复 |
| B1-5 | `plugin_data.py:449-457` `_get_day_key` | 无 ranking 时手写日界 | 同 B1-3 |
| B1-6 | `plugin/steam_status_monitor.py:51-54` | `if hasattr(self,'_ssm_running') and self._ssm_running: return` | 新实例首次 `__init__` 时该属性必然不存在；真正命中也只会留下半初始化对象（`return` 早于 `self.context` 等赋值），比继续执行更危险 |
| B1-7 | `qq_menu_management.py:95-100` `_qq_menu_lock_for_use` | lock 懒建 | `__init__` 已建 `_qq_menu_lock` |
| B1-8 | `notification_tracking.py:240-242` | flush lock 懒建 | 可在 `__init__` 建；懒建会让两个 task 在首次并发时各建一把锁 |
| B1-9 | `notification_tracking.py:137-142` | `_sent_notification_events` / `_inflight_notification_events` 懒建 | 同上，`__init__` 统一建 |
| B1-10 | `itad.py:12-30` | `try: from ...errors import ProviderError except ImportError:` 自定义类 | 同包内相对导入不会失败（模块不是脚本执行）；`httpx_client_kwargs` / `steam_store_client_kwargs` 同理 |
| B1-11 | `achievement_monitor.py:127,195` | `hasattr(self,'achievement_blacklist')` | `__init__` 必设该属性 |
| B1-12 | `steam.py:771-772` | `hasattr(self,'_game_cover_cache')` | 全项目**从未赋值**，分支永远为假 |
| B1-13 | `ranking.py:121-122,155` | `self._plugin.session_records = records` 回写 | `records` 本来就是通过 property 从 plugin 取出的同一对象 |
| B1-14 | `ranking.py` 全类 | `plugin=None` 时的内部 dict 分支（4 组 property） | 生产永远传 plugin；测试也不传 `None`。若确实需要无插件模式，改为显式 `StandaloneRanking` 或直接注入 dict，别在一套类里维护两套存储 |
| B1-15 | `domain/monitoring/transitions.py` 全文 | 仅 `tests/unit/test_transitions.py` 调用 | 会话状态机已由 `session.py` 接管；该文件连同测试一起删 |
| B1-16 | `domain/price/models.py` `Money` | 仅 `tests/unit/test_price_currency_contract.py` 使用 | 价格换算走 `shared/utils/price.py`；删除后删测试，或明确"下一轮要迁移到 Money"再保留（二选一，不能悬空） |
| B1-17 | `shared/network.py:38` `requests_verify`、`:21` `get_ssl_ca_file` | 0 调用 | 删除 |
| B1-18 | `shared/paths.py:22` `asset_path`、`PAGES_DIR` | 0 调用 | 删除 |
| B1-19 | `game_start.py:48` `set_cache_config` | 0 调用 | 删除（缓存 TTL 改由调用方传参，见 §9） |
| B1-20 | `achievement_monitor.py:301` `check_new_achievements` | 0 调用 | 删除 |
| B1-21 | `owned_games.py:58` `forget_many` | 0 调用 | 删除 |
| B1-22 | `commands/store.py:186-189` | `return` 之后的 `if card.store_message` | 不可达（try/except 两支都以 `return` 结束） |
| B1-23 | `rank_view.py:21-23` | `days = int(period) or 1` 后又 `if days <= 0: days = 1` | `isdigit()` 通过后 `int>=0`，`or 1` 已覆盖 0，第二段不可达 |
| B1-24 | `price_query.py:384-387` | `reviews = None` 后立刻 `(reviews or {}).get(...)` | `review_all` / `review_schinese` 恒为 `{}`；核心阶段本就不取评价，删除这两行的赋值，改由 `_attach_enhancements` 写入 |
| B1-25 | `price_query.py:465-481` | `isinstance(compare_task.result(), BaseException)` | 前面的 `await asyncio.gather(*pending)` 未用 `return_exceptions=True`，任务异常会直接抛出，这里的判断不可达；改为 `gather(..., return_exceptions=True)` 后判断才有意义（二选一，推荐后者并保留日志） |
| B1-26 | `session_service.py:353-359` | `iscoroutine` + `get_running_loop()` try/except | `_on_closed` 的两个调用点都在 async 上下文 |
| B1-27 | `session_service.py:399-402,437-440` | 建 task 前 `get_running_loop()` 探测 | 调用链全部 async |
| B1-28 | `game_filter.py:11-14` | `try: str(ids_str).split(",")` | `str.split` 不会抛异常 |
| B1-29 | `admin_api.py:721-724` | `try: resolved = await resolve_steam_input(...) except: None` | `resolve_steam_input` 内部已捕获所有异常并返回 `None` |
| B1-30 | `admin_api.py:1269,1323,1377` | `hasattr(p,'get_game_cover_url')` | MRO 固定，属性必然存在 |
| B1-31 | `ops.py:146,312,342` | `getattr(plugin,'owned_games'/'font_pack', None)` | `__init__` 必设 |
| B1-32 | `commands/store.py:100-103` | `raw_msg` 取 `message_str`，取不到再 `get_message_str` | `AstrMessageEvent` 两者都有；保留 `get_message_str()` 一条即可 |

**批次建议**：B1-1 ~ B1-9、B1-13 ~ B1-14 属"删掉后行为不变"的低风险项，第 1 批处理；B1-3/4/5 需要同步改 `test_session_service.FakePlugin`（它正是依赖这条回退来断言记账）；B1-15/16 会删两个测试文件，需在 PR 说明。

### B-2 连续相同守卫合并

| # | 位置 | 现状 | 合并后 |
| --- | --- | --- | --- |
| B2-1 | `status_change_tracking.py:25-28` | 两个 `if` 各自 `skip_push = True` | `skip_push = skip_push or self._startup_stale_groups.get(group_id, False) or not self.group_monitor_enabled.get(str(group_id), True)` |
| B2-2 | `session_service.py:335,349` | `not skip_push and monitor_on and not self._skip_game(...) and plugin.config.get(...)` 写两遍（结束/开始各一次） | `def _is_notify_allowed(self, session, skip_push, config_key)` 一处判定 |
| B2-3 | `session_service.py:369` | 同上结构 | 同上 |
| B2-4 | `notification_tracking.py:200-212` | `if not msg_chain:` 里两个分支分别 `finish + return` | 统一：`for n in matched: self._finish_notification(n, session, success=False)`；返回值按"是否允许图片-only 重试"决定，写成 `retry = self.config.get("notify_send_image", True) and not self.config.get("notify_send_text", True)` |
| B2-5 | `monitor_admin.py:236-246` 与 `:261-271` | "群已空 → 停轮询 + 清开关 + 清 session + 存盘" 写两遍 | `_drop_group_state(group_id)` |
| B2-6 | `monitor_admin.py:63-82` | `primary_group` 判定后两个 return 分支都先 `_remember_notify_session` | 提前调用一次，再判 `already` |
| B2-7 | `owned_games.py:244-255` | 三个 `except` 均构造同一 dict `{"baseline": False, "added": 0, "failed": True}` | `return self._fail_result()`，异常分类只影响日志 |
| B2-8 | `owned_games.py:87-92` 与 `163-168` | "flush + 清 dirty + 异常日志" 两处 | `_flush_if_dirty()` |
| B2-9 | `commands/store.py:148-156` | 两个 `except` 都 `cache.pop` + `return` | `except (TimeoutError, Exception)` 分开仅为文案，可用 `except Exception as exc:` + `isinstance(exc, TimeoutError)` 或 `finally: cache.pop(...)` |
| B2-10 | `admin_api.py` 15 处 | JSON 解析样板 | 见 A16 |

### B-3 上游已有校验，下游重复保护

| # | 位置 | 上游 | 下游多余 | 动作 |
| --- | --- | --- | --- | --- |
| B3-1 | `plugin_data._read_json` 系列 | `open()` 已抛 `FileNotFoundError` | `os.path.exists()` 预检 | 删预检，`except FileNotFoundError` 返回默认值 |
| B3-2 | `persistence._save_notify_session:196` | 调用方都在 `__init__` 之后 | `hasattr(self,'notify_sessions')` | 删 |
| B3-3 | `persistence._save_play_records:376` / `_save_session_records:408` | 同上 | 同上 | 删 |
| B3-4 | `monitor_control.start:76` | 命令层已校验群号 | `any(isinstance(x,str) and x.strip() ...)` 对内部 list 再校验一遍 | 简化为 `if not steam_ids:` |
| B3-5 | `admin_api._api_groups_import_batch:844-859` | `add_player` 已实现"已存在/满员/push 判定" | 又手写一遍 `existing` / `max_size` / `primary_exists` | 改为按 `GroupMutationResult.code` 分支（需先做 §10 枚举化），删除重复判定 |
| B3-6 | `admin_api._api_groups_add:725` | 命令层与 `add_player` 都校验 group_id | Web 层再校验一次（合理，入口不同） | 保留，但统一用 `is_valid_group_id` |
| B3-7 | `session_service._on_closed:334` | `handle` 的 `skip_push` 已由上层算好 | 再叠 `monitor_on` / `_skip_game` / config 三层 | 合并为 B2-2 的单一判定 |
| B3-8 | `plugin.terminate:166-193` | 这些任务属性都在 `__init__` 赋值 | 7 个 `getattr` | 改为直接属性访问（`__init__` 中途 `return` 的坑随 B1-6 一起修） |
| B3-9 | `steam_list.list_parent` | `AstrMessageEvent` 必有 `get_sender_id/name` | `try/except → (None,None)` | 删 try，直接取；异常交给命令层统一处理 |

### B-4 过度默认值（前置条件已保证，fallback 永不触发）

| # | 位置 | 多余默认 | 说明 |
| --- | --- | --- | --- |
| B4-1 | `owned_games._interval:104` | `max(600, seconds or 1800)` | 先 `except → 1800`，再 `or 1800` 是二次兜底；保留 `max(MIN_INTERVAL_SECONDS, seconds)` 即可 |
| B4-2 | `owned_games._max_per_notify:111` | `min(20, max(1, value or DEFAULT))` | 同上 |
| B4-3 | `owned_games._grace_seconds` / `_daily_budget` | 同构 | 统一走 A17 的 `_config_int` |
| B4-4 | `game_end.py:249` | `_END_TITLE_SUFFIX or "结束游戏"` + `.strip()` | 模块常量非空且已是干净字符串，两项处理均永不生效 |
| B4-5 | `session_quit._end_game_tip` 的 3 处调用点 | 调用方再写 `or "默认文案"`（`notification_tracking.py:104`） | 该函数 11 个分支必然有返回值，`or` 是第三份文案副本（见 A7） |
| B4-6 | `player_status_view.build_player_row` | `name or sid`、`zh_game_name` 默认 `""` | 调用方已保证 `name`（`_resolve_bind_name` 必回退 sid）；保留一层即可，删重复层 |
| B4-7 | `admin_api._api_push_settings` | `getattr(p,"rank_push_hour",8)` 等 6 处 | `__init__` 已设；`8`/`30` 默认值与 `runtime_config` 重复（见 §9） |
| B4-8 | `admin_api._api_dashboard_stats:259` | `ranking.day_key(0) if ranking else datetime.now()` | ranking 必存在 |
| B4-9 | `owned_games._current_day_key:609-618` | `try: ranking.day_key() except: pass` + 手写回退 | ranking 必存在（同 B1-3） |
| B4-10 | `plugin_steam_status_monitor.__init__` | `self.group_steam_ids = {}` 等 5 行 | 这些是 property，`MonitorStateStore` 已给默认空 dict；赋值只是重建了 5 个空容器（无害但误导：注释写着"分群管理"实际是代理） |

### B-5 缺失兜底（本轮只补会导致崩溃的 3 处）

| # | 位置 | 风险 | 动作 |
| --- | --- | --- | --- |
| B5-1 | `achievement_monitor.py:387-390` | `get_player_achievements(api_key, steamid, appid)` **少传 `group_id`**，命中即 `TypeError` | 该参数是"兼容旧调用"，改为必传；缺参直接 `raise ValueError`（快速失败好过静默崩在渲染里） |
| B5-2 | `superpower.py:21` | `abilities.txt` 为空 → `random.choice([])` → `IndexError` | 加载时校验非空，空则回退到内置默认列表并 `logger.warning` |
| B5-3 | `notification_tracking._render_notification_image:98` | 未知 `type` 会走"结束卡"分支并 `noti["duration_min"]` → `KeyError`（被外层 `except` 吞掉后静默无图） | 显式 `elif noti["type"] == "end":`，未知类型 `logger.warning` + `return None` |

---

## 5. C 组：命名与注释

### 5.1 函数命名（动词 + 名词，表意优先）

| 现名 | 建议 | 理由 |
| --- | --- | --- |
| `SessionService.handle` | `apply_snapshot` | `handle` 无信息量；该方法做的是"投递快照并推进状态机" |
| `domain.session.apply` | `advance` | 纯状态机推进，与 `apply_snapshot` 区分 |
| `SessionService.tick_due` | `close_expired_sessions` | 只做"到期关闭" |
| `check_status_change` | `sync_player_status` | 它同时更新基线、调度、presence，不只是"check" |
| `MonitorControlService.ensure_running` | `start_if_needed` | `ensure_*` 易被误解为断言 |
| `OwnedGamesService._announce` | `announce_new_games` | 动词短语 + 明确对象 |
| `OwnedGamesService._flush_item_once` | `deliver_item_once` | 与"flush 落盘"区分 |
| `OwnedGamesService._retry_item` | `redeliver_item` | 语义是"重投递"，不是"重试一次" |
| `OwnedGamesService.sync_all` | `sync_all_players` | 明确对象 |
| `_get_notify_sessions` | `resolve_notify_sessions` | 有过滤/归一化，不是 getter |
| `_should_send_notification` | `try_claim_notification` | 它**有副作用**（写入 inflight），`should_*` 暗示纯判断 |
| `_render_notification_image` | `render_notification_card` | 产物是卡片图 |
| `steam_list.list_parent` | `resolve_sender_identity` | 返回 (昵称, 头像)，与"parent"无关 |
| `_settle_pending_offline_presence` | `flush_due_offline_notices` | 与"settle"含糊相比更具体 |
| `rank_view.render_aggregated` | `render_rank_for_scope` | 表达"按范围聚合后出图" |
| `statistics._build_display_names` | `build_display_names` | 同模块内部无需下划线前缀（非私有语义） |
| `_api_gantt_data` | `_api_gantt_payload` | 返回 JSON payload |
| `_PluginPageRequest` | `PluginPageRequestAdapter` | 明确是适配器 |

**保留原名**（有框架/领域含义，不值得改）：`GroupMutationResult`、`PriceCard`、`PlayingSession`、`MonitorStateStore`、`steam_on/steam_off` 等注册桩、`_key/_store`。

### 5.2 布尔值命名（is/has/can/should）

| 现名 | 建议 |
| --- | --- |
| `AddPlayersResult.started` | `is_started` |
| `GroupMutationResult.changed` | `is_changed` |
| `PriceCard.locked` | `is_region_locked` |
| `_ssm_running` | `is_running` |
| `FontPackService.enabled` | `is_enabled` |
| `_data_dirty` / `_session_dirty` | `has_unsaved_state` / `has_unsaved_sessions` |
| 局部 `delivered` / `announced` | `is_delivered` / `is_announced` |
| `detailed_poll_log` | `is_detailed_poll_log`（配置键不改，只改运行时属性） |

> 注意：`GroupMutationResult.changed` 等字段被 Web/命令层读取（约 12 处），改名需同批完成，不放第 1 批。

### 5.3 变量命名

| 现名 | 位置 | 建议 |
| --- | --- | --- |
| `p` | `admin_api.py` 全文件（约 40 处） | `plugin` |
| `data` | web handler 的请求体 | `payload` |
| `tmp` / `tmp_path` | 通知、排行、命令层 | `image_file` / `image_path` |
| `msg` | 多处 | `message`（字符串）/ `log_line`（日志行） |
| `logstr` | `polling_tracking.py:137` | `log_text` |
| `gid` / `sid` | 全项目 | **保留**：Steam 领域缩写，且类型注释已标明 |
| `_low_cursor` / `_price_x` / `_cur` | `game_detail.py:353-370` | `cursor_x` / `price_x` / `cursor_x`（去掉下划线前缀，它们是局部变量） |
| `ckd_*` | `itad.py` | `cdk_*`（拼写错误，共 8 处；不影响持久化字段前先确认无外部依赖） |

### 5.4 注释规范

删除"复述代码"和"历史修复叙述"的注释，保留"为什么/约束/不变量"。

**删**：

- `game_end.py:1` `# filepath: c:\Users\Maoer\Desktop\...`（开发机残留）
- `game_start.py:454` `# 头像渲染（只保留一次）`
- `game_start.py:440` `# 3. 文本区域不再为在线人数预留宽度（...）`（变更史）
- `persistence.py` 多处 `# 新增：...` / `# <--- 修复：...`
- `achievement_monitor.py` 两处重复的 `# 检查是否有描述`

**保留/改写为"意图"**：

- `session.py` 的"switch 立即 close，不进 confirming_exit" —— 不变量，保留；
- `polling_tracking.py:71-73`"presence 跟踪必须在 next_poll 写入之后" —— 顺序约束，保留并补一句后果；
- `notification_tracking.py:125`"600 秒窗口内不同次的转换不被误去重" —— 保留（但 `600` 应提常量）。

**公共 API docstring**（补 8 处，Google 风格）：

`SessionService.apply_snapshot` / `RankingService.record_closed` / `RankingService.aggregate` / `MonitorAdminService.add_players` / `MonitorAdminService.remove_player` / `PriceQueryService.build_card` / `OwnedGamesService.sync_all` / `MonitorControlService.start`。

模板：

```python
async def apply_snapshot(self, group_id, sid, observed_gameid, now, *, ...):
    """把一次 Steam 快照推进到会话状态机，并在事件发生时记账与推送。

    Args:
        group_id: 群标识，会话按 (group_id, sid) 隔离。
        observed_gameid: 本轮观测到的 AppID；None/""/"0" 表示未在游戏中。
        now: 秒级时间戳，用于状态机判定与时长计算。
        skip_push: True 时只更新状态不推送（启动基线、手动同步）。

    Returns:
        (next_session, events)。next_session 为 None 表示会话已关闭或从未开启。

    Raises:
        ValueError: observed_gameid 不是字符串/数字/None（上游客户端已保证，此处仅防御契约）。
    """
```

---

## 6. D 组：死代码移除清单

见 §4 B1-15 ~ B1-21，汇总：

| 符号 / 文件 | 证据 | 测试影响 |
| --- | --- | --- |
| `domain/monitoring/transitions.py` | 生产 0 调用 | 删 `tests/unit/test_transitions.py` |
| `domain/price/models.py` `Money` | 生产 0 调用 | 删 `tests/unit/test_price_currency_contract.py` |
| `PersistenceMixin._record_session` / `_get_day_key` | 仅被回退分支调用 | 无（`test_session_service` 需改用 RankingService） |
| `shared/network.requests_verify` / `get_ssl_ca_file` | 0 调用 | 无 |
| `shared/paths.asset_path` / `PAGES_DIR` | 0 调用 | 无 |
| `game_start.set_cache_config` | 0 调用 | 无（但 `cache_*_hours` 配置项要一并处理，见 §9） |
| `AchievementMonitor.check_new_achievements` | 0 调用 | 无 |
| `OwnedGamesService.forget_many` | 0 调用 | 无 |
| `steam.get_chinese_game_name` | 与 `get_game_names` 重复 | 合并后调用方改 `get_game_names(...)[0]`（5 处） |
| `steam.fetch_player_summary` | 与 `fetch_player_status` 共用同一 URL/错误处理，只是"原始字段"vs"7 字段投影" | 保留两个**投影**，抽出共用的 `_request_player_summaries(steam_ids) -> list[dict]`（一处 HTTP + 一处异常包装），两个公开方法只做字段映射 |
| `commands/store.py:186-189` | 不可达 | 无 |
| `admin_api._get_player_display_name` | 与 `_resolve_bind_name` 重复 | 无 |
| `admin_api._api_test_cover` | 页面已无调用 | 删除 |

> `_api_test_steam` 仍被页面"连通性测试"使用，保留，但抽掉 `print` 与硬编码 `"730"`。

---

## 7. E 组：长函数拆分

按"改动收益 / 风险"排序，前 14 个：

| # | 函数 | 行数 | 拆分方案 |
| --- | --- | --- | --- |
| 1 | `achievement_monitor.render_achievement_image` | 331 | `_measure_achievement_cards`（文本/高度预计算）、`_draw_progress_header`、`_fetch_achievement_icons`（aiohttp 并发段）、`_draw_achievement_card`（单卡） |
| 2 | `game_detail.render_game_detail_image` | 294 | `_resolve_price_display`（当前价/折扣/史低 → 三元文本）、`_layout_sections`（高度与分栏）、`_draw_info_column`、`_draw_price_column`、`_draw_region_rows` |
| 3 | `owned_game.render_owned_game_image` | 231 | `_measure_left_column`、`_measure_right_column`、`_draw_left_column`、`_draw_right_column`、`_draw_badge` |
| 4 | `rank.render_rank_image` | 217 | `_card_heights`、`_draw_rank_header`、`_draw_player_card`（复用 `_draw_segment_bar`） |
| 5 | `game_start.render_game_start_image` | 167 | `_paste_cover`、`_draw_avatar_block`（含头像框/超能力）、`_draw_text_block`、`_draw_online_badge`、`_draw_version_watermark` |
| 6 | `game_end.render_game_end_image` | 158 | 同 5，另加 `_draw_luck_badge`、`_draw_duration_bar`（现有 `draw_duration_bar` 保留） |
| 7 | `admin_api._api_gantt_data` | 121 | `_gantt_from_session_records`、`_gantt_from_play_records`、`_gantt_game_details`、`_gantt_colors` |
| 8 | `steam_list._render_card_style` | 121 | `_draw_status_card`（单卡）+ 主函数只做循环与统计条 |
| 9 | `plugin.__init__` | 113 | `_init_runtime_state`（状态/缓存 dict）、`_init_services`（admin/price/rank/web）、`_restore_persisted_state`（5 个 `_load_*`）、`_start_background_tasks`（6 个 task） |
| 10 | `achievement_monitor.get_achievement_details` | 112 | `_fetch_schema`、`_fetch_percentages`、`_fallback_from_player_achievements`、`_to_icon_url`（现为闭包，提为模块函数） |
| 11 | `admin_api._api_heatmap_player` | 111 | `_collect_player_sessions`、`_collect_player_play_records`、`_build_player_heatmap_response` |
| 12 | `statistics.build_active_profile` | 110 | `_collect_activity_buckets`、`_build_bucket_series`、`_summarize_activity` |
| 13 | `polling_tracking.global_poll_and_log_loop` | 98 | `_collect_due_sids`、`_maybe_push_daily_rank`、`_flush_throttled_state`、`_poll_groups`、`_emit_round_logs` |
| 14 | `monitor_admin.remove_player` | 84 | `_remove_push_route`、`_promote_primary_group`、`_detach_player_everywhere`、`_drop_group_state` |

剩余 30 个 60–80 行函数（`check_status_change`、`_send_merged_notification`、`price`、`_api_settings_update` 等）在第 4 批按"能否抽出无副作用的纯函数"择机处理，不强行拆。

---

## 8. F 组：减少嵌套与提前返回

| # | 位置 | 现状 | 改写 |
| --- | --- | --- | --- |
| F1 | `achievement_monitor.get_achievement_details` | `for lang → try → if status==400 → for lang2 → if 200 → try → if ... → for`（6 层） | 用 guard + 早返回：`schema = await self._fetch_schema(...); if schema: return self._merge(...)`；400 降级独立成函数 |
| F2 | `session.apply` | `playing` / `confirming_exit` 两个大 if 各 10 行 | `_advance_playing()` / `_advance_confirming()`，主函数只做分派 |
| F3 | `admin_api._api_settings_update` | 12 个 `elif key == ...` | `_SETTING_COERCERS: dict[str, Callable]` + 必填校验表；`bool` 类合并为一组集合 |
| F4 | `notification_tracking._send_merged_notification` | 内层 `send_one` 里再嵌 `if not msg_chain:` 双分支 | `_build_message_chain(notifs, images)`、`_send_and_finish(session, notifs)` |
| F5 | `monitor_admin.remove_player` | `if old != owner → if not in targets → return`；再 `if targets → ...` | 三个场景各自 `return` 的 guard 结构，每段一个私有方法（同 §7-14） |
| F6 | `price_query.resolve_games` | 单行 4 条件 `if games or status!=... or translator is None or not contains_chinese` | `_should_try_translated_fallback(...)` 命名后自解释 |

---

## 9. G 组：硬编码参数集中

| 常量 | 现值 | 出现位置 | 归属 |
| --- | --- | --- | --- |
| `NETWORK_FLUCTUATION_WINDOW` | `180` | `session.py:6`、`transitions.py:6`、`presence.py:13`、`session_service.py:150` | `domain/monitoring/windows.py`（或被 `session`/`presence` 共同导入） |
| `DAY_ROLLOVER_HOUR` | `4` | 见 A9（5 处） | `shared/timeutil.py` |
| `STEAM_BATCH_SIZE` | `100` | `steam.py:215`（函数内局部） | 模块常量 |
| `HTTP_TIMEOUT_SEC` / `HTTP_TIMEOUT_LONG_SEC` | `10` / `15` / `20` | `steam.py` 9 处、`itad.py:68,91`、`game_detail.py:145`、`achievement_monitor` 3 处、`steam_cover.py:27` | `shared/network.py` |
| `NOTIFICATION_DEDUPE_WINDOW_SEC` | `600` | `notification_tracking.py:144` | 模块常量 |
| `PLAYTIME_DEDUPE_WINDOW_SEC` | `300` | `ranking.py:148` | 模块常量 |
| `ACHIEVEMENT_POLL_INTERVAL_SEC` / `ACHIEVEMENT_FINAL_CHECK_DELAY_SEC` / `ACHIEVEMENT_FAIL_LIMIT` | `1200` / `300` / `10` | `achievement_tracking.py:20,58,33,72` | 模块常量 |
| `RECENT_GAMES_LIMIT` | `8` | `session_service.py:366` | 模块常量 |
| `MAX_TIMEOUT_SEC` 等卡片几何 | 大量 | `game_start/game_end/rank/steam_list/game_detail/owned_game` 顶部已有常量，但函数体内仍有裸数字（如 `avatar_margin=24`、`line_height=36`、`text_x = avatar_x + avatar_size + avatar_margin`） | 每个渲染模块顶部一组布局常量 + `@dataclass Layout`，函数内不再出现魔法数 |

另外三类"配置与常量脱节"，需要一并处理：

1. **版本号双源**：`metadata.yaml` 的 `version: 4.10.2` 与 `plugin.__init__:55` 的字符串字面量。改为 `importlib.metadata` 读取失败时回退 `"unknown"`，避免发版漏改。
2. **缓存配置未接线**：`_conf_schema.json` 有 `cache_avatar_hours` / `cache_avatar_frame_hours` / `cache_cover_vertical_hours` / `cache_cover_horizontal_hours`，但 `src/` **0 次读取**；`game_start._cache_config` 是另一套默认值，`set_cache_config` 从未被调用。二选一：接进 `runtime_config` 并让 A2 的函数按参数接收 TTL，或删除这 4 个配置项（推荐前者，用户已能在面板里改）。
3. **残留配置键**：`runtime_config.py:170-172` 读取 `steam_ids` / `notify_group_id`，这两个键不在 schema 中且 `migrate_legacy_steam_ids` 已把 `steam_ids` 迁走；`poll_interval_mid_sec` / `poll_interval_long_sec` 只写到属性、无人读取。删除这 4 行及对应属性。

**配置默认值单一来源**（与 §4 B4 呼应）：新增 `src/plugin/settings.py`，用只读访问器集中 75 处 `config.get(key, default)`：

```python
class PluginSettings:
    def __init__(self, config): self._config = config
    @property
    def is_game_start_notify_enabled(self) -> bool: ...
    @property
    def max_group_size(self) -> int: ...
```

不做依赖注入、不改 `plugin.config` 的字典身份（Web 面板仍直接写 dict），只是把"键名 + 类型 + 默认值 + 范围"收在一处。

---

## 10. H 组：语义化改造（字符串当枚举）

`GroupMutationResult.message` 现在是**人类可读文案**，却被当作控制流：

- `monitor_admin.add_players:111-128`：`if result.message == "already exists" / "added as push group" / ...`
- `commands/monitor.py:72`：`if result.message == "removed push route"`
- `admin_api.py:733,857`：`if result.message in {"already exists", ...}`

任何文案微调都会静默改变分支走向（例如把提示改成中文就会全部落到 `unhandled`）。

**动作**：

```python
class MutationCode(str, Enum):
    ADDED_PRIMARY = "added_primary"
    ADDED_PUSH_ROUTE = "added_push_route"
    ALREADY_EXISTS = "already_exists"
    ALREADY_PUSH_ROUTE = "already_push_route"
    GROUP_FULL = "group_full"
    REMOVED_PRIMARY = "removed_primary"
    REMOVED_PUSH_ROUTE = "removed_push_route"
    NOT_FOUND = "not_found"
    INVALID_GROUP = "invalid_group"
```

`GroupMutationResult` 增加 `code: MutationCode`，`message` 只做展示。所有分支改判 `code`。这是**第 4 批**内容，因为它同时动 `monitor_admin` / `commands` / `admin_api` 三处，需要一次改完。

---

## 11. 实施批次与验收

每批独立可回滚，批内按文件顺序提交。

### 第 1 批：清理（不改行为）

范围：§4 B1-1~B1-14、B1-17~B1-24、B1-26~B1-32；§6 死代码；82 处 `print` → `logger`；3 处裸 `except`；`game_end.py` 顶部残留注释。

- 删除后必须保证：`grep -rn "def _record_playtime" src` 为空、`grep -rn "print(" src` 为空。
- 测试：`test_session_service` 的 `FakePlugin` 改用真实 `RankingService`（断言 `session_records`），删除 `test_transitions.py` 与 `test_price_currency_contract.py`。
- 验收：`python -m unittest discover -s tests -q` 全绿；手工跑 `/steam list`、`/steam rank`、`/steam price`、`/steam off/on`。

### 第 2 批：机械提取（行为等价，唯一例外见下）

范围：A1 roster、A2/A3/A4/A5/A6/A8 渲染共享模块、A9 业务日、A10 session_id、A14 状态枚举、A15 JSON helper、A16 Web helper、A17 配置整数、A18、A19、A20、A21。

- **唯一行为变化**：A3 合并后 `game_end` 的 SGDB 搜索会带上 URL 编码（修复）。需单独准备 3 个含空格/中文/`&` 的游戏名做封面命中率对比。
- 验收：4 类卡片（开始/结束/列表/排行）各出 1 张图，与改前做像素 diff；允许"结束卡封面命中率提升"的差异，不允许布局位移。
- 新增测试：`test_roster.py`（7 个入口同一结果）、`test_business_day.py`（3:59/4:00/跨月）、`test_duration_format.py`。

### 第 3 批：拆分与降嵌套

范围：§7 前 14 个函数、§8 F1–F6、§5.1 的方法改名。

- 每个函数拆完补一条"输入 → 输出"单测（纯函数部分），带 IO 的保持端到端烟雾测试。
- 验收：`test_polling_loop`、`test_session_service`、`test_price_card_pipeline`、`test_rank_render`、`test_web_route_smoke` 全绿；轮询日志与改前逐行对比（同一天同一批玩家）。

### 第 4 批：语义化与护栏

范围：§10 枚举化、§9 `settings.py`、§5.2 布尔改名、注释与 docstring 补全。

- 验收：Web 面板增删玩家/批量导入/删除群三条路径回归（这三处都依赖 `code`）；`test_monitor_admin` / `test_monitor_admin_promotion` / `test_web_route_smoke` 全绿。

### 护栏（第 4 批一起加）

在 `tests/unit/test_code_hygiene.py` 用 AST 做 4 条硬约束，防止回流：

1. `src/` 下禁止 `print(`；
2. 禁止裸 `except:`；
3. 禁止 `except ImportError` 包裹**同包内**相对导入（`itad.py` 那种）；
4. 允许的 `getattr(plugin|self, "...")` 白名单（如 Web 兼容访问 `config.save_config`），新增即失败。

可选：加 `ruff` 配置（`E,F,W,B` + `T201` 禁 print），但项目目前未引入 lint 依赖，建议放到独立 PR，不阻塞本计划。

---

## 12. 明确不做

对齐既有 ADR，避免把上一轮删掉的东西加回来：

- **不新增透传层**：不建 `BaseCommand`、不给每个 HTTP 方法套 application 包装、不把 `_resolve_notify_sessions` 再包一层。
- **不动注册桩与 MRO**：`@filter.command` 仍逐个函数；Mixin 结构不变（`test_modular_structure.py` 会校验）。
- **不改数据格式**：`play_records` / `session_records` / `playing_sessions.json` / `owned_games.json` 的落盘结构与路径不变。
  - 「每群 4 个 JSON 合并成 1 个」属于写放大优化，但需要迁移，另开一轮。
- **不引入 DI 框架 / 事件总线**：单实例 `Star` 下 `self` 作为组合根足够。
- **不为缩短行数拆文件**：拆分只按"变化原因"和"单一职责"。

需要另开一轮的已知项（记录在案，不在本计划）：
1. 会话细粒度锁与成就补偿（`REFACTORING.md` 已列为下一轮）；
2. JSON 持有权从插件 dict 迁出；
3. `session_records` 的 `group_ids` 多群归属模型。

---

## 13. 附录：逐文件问题索引（Top 10 文件）

| 文件 | 行数 | 主要问题 |
| --- | --- | --- |
| `presentation/web/admin_api.py` | 1424 | 15 处 JSON 样板、7 处 query 解析、排行聚合重复、`p` 命名、`print`、调试端点、硬编码 `"730"` |
| `infrastructure/clients/steam.py` | 802 | 玩家摘要/游戏名双实现、`_game_cover_cache` 死分支、超时硬编码、每请求新建 `AsyncClient` |
| `application/services/achievement_monitor.py` | 692 | 331 行渲染函数、6 层嵌套、12 处 `print`、错误参数个数的死分支、`hasattr` 兜底 |
| `infrastructure/fonts/pack_service.py` | 695 | 整体质量较好；`except (CancelledError, Exception)` 写法怪异，可读性小修 |
| `application/services/owned_games.py` | 635 | 4 份配置整数解析、日界重复、`_current_day_key` 回退、结果 dict 当结构体（建议 `SyncOutcome` dataclass） |
| `presentation/renderers/game_start.py` | 582 | 39 处 `print`、封面/SGDB/头像/换行/渐变全部与 `game_end` 重复、167 行绘制函数 |
| `application/services/monitor_admin.py` | 557 | 字符串当枚举、`remove_player` 84 行且"空群清理"重复、`_clear_runtime_state` 用属性名字符串 |
| `presentation/renderers/steam_list.py` | 551 | 头像/截断/字体与 `rank` 重复、`_render_card_style` 121 行、`get_status_*` if-elif 链 |
| `infrastructure/persistence/plugin_data.py` | 526 | 14 个 JSON 读写样板、`_record_session`/`_get_day_key` 死回退、`hasattr` 兜底 |
| `application/services/price_query.py` | 494 | `reviews` 死赋值、不可达的异常判断、`_build_core_card` 67 行 |

---

## 14. 预期结果

| 维度 | 现在 | 优化后（预估） |
| --- | --- | --- |
| 重复实现 | 7 处名单规则、3 份头像、4 份换行、2 份封面管线 | 各 1 处 |
| 不可达兜底 | ≥ 30 处 | 0（白名单除外） |
| `print` | 82 | 0 |
| ≥60 行函数 | 44 | ≤ 30（重点 14 个已拆） |
| 硬编码窗口/日界 | 9 组散落 | 集中在 3 个模块 |
| 净代码量 | 15,826 | 约 15,300（删 700+ / 增 400−） |

真正的验收标准不是行数，而是：**改一条规则（名单范围、日界、去重窗口）只需改一个地方，且现有测试能立刻告诉你哪里坏了。**

---

## 15. 实施记录（2026-10-17）

回归环境：SAKAWA `192.168.101.13` 的 `astrbot2` 容器（Python 3.12 + AstrBot 4.29），
代码同步到 `/tmp/ssm_review` 后执行 `python -m pytest -q tests`。

| 批次 | 结果 | 回归 |
| --- | --- | --- |
| 基线 | — | 2 failed / 394 passed / 4 skipped（2 个 presence 文案断言已过期） |
| 第 1 批 清理 | 完成 | 386 passed / 0 failed |
| 第 2 批 提取 | 完成 | 386 passed |
| 第 3 批 拆分 | 完成（范围见下） | 386 passed |
| 第 4 批 语义化 | 完成（范围见下） | 399 passed（新增 13 条） |

### 15.1 已落地

**第 1 批（清理）**

- 82 处 `print` → `logger.debug/info`（`shared/logging.py` 的 stderr 降级出口保留）。
- 3 处裸 `except`：两处字体加载改为 `load_truetype`（同时去掉 `msyhbd.ttc` 系统字体硬编码），
  一处 JSON 解析改为 `except (ValueError, TypeError)` 并记录日志。
- 删除：`domain/monitoring/transitions.py`、`domain/price/`（`Money`）、`network.requests_verify/get_ssl_ca_file`、
  `paths.asset_path/PAGES_DIR`、`game_start.set_cache_config`、`AchievementMonitor.check_new_achievements`、
  `OwnedGamesService.forget_many`、`admin_api` 的显示名重复实现、`store.price` 不可达尾部。
- 删除不可达兜底：`SessionService._sessions/_meta` 懒建、`_on_closed` 的 `_record_playtime` 回退
  （该函数不存在）、`plugin_data._record_session/_get_day_key`、`_ssm_running` 守卫、QQ 菜单/通知/累积事件的懒初始化、
  `itad.py` 三段 `except ImportError`、`steam._game_cover_cache`、`achievement_monitor` 的 `hasattr` 守卫、
  `game_filter` 的 split 兜底、`rank_view` 不可达的 `days<=0`、`price_query` 的死赋值。
- 顺带修复：`achievement_monitor` 错误参数个数的死分支（改为显式 `unlocked_set` 契约）。

**第 2 批（提取，新增 8 个模块）**

| 新模块 | 收敛的重复 |
| --- | --- |
| `domain/monitoring/roster.py` | 群名单+push 群查询（原 7 处） |
| `presentation/renderers/assets.py` | 头像下载 3 份、头像框解析 4 份 |
| `presentation/renderers/drawing.py` | 换行 4 份、截断 2 份、渐变背景 2 份 |
| `shared/timeutil.py` | 4:00 业务日边界 5 处 |
| `shared/endpoints.py` | 默认服务地址 4 组 |
| `shared/utils/duration.py` | 时长格式化 4 份 |
| `presentation/web/http.py` | Web JSON 解析 15 处、query int 7 处 |
| `plugin_data._read_json/_write_json` | 14 个 JSON 读写样板压到 2 个 helper |

- `game_end.py` 删除与 `game_start.py` 平行的 SGDB/封面实现（同时修复搜索词未做 URL 编码的缺陷），文件从 448 → 约 300 行。
- `_async_none` 3 份全部删除，改为按需构建任务列表。
- 状态码/文案表收敛到 `domain/monitoring/presence.py`（`persona_status`）与 `formatters/status.py`（`persona_label`）。
- `DEFAULT_MENU_COMMANDS` 只保留 `qqofficial_settings.py` 一份。

**第 3 批（拆分，已做的 3 个）**

- `polling_tracking.global_poll_and_log_loop`（98 行）→ 6 个方法：对齐分钟、收集到点 sid、
  每日推送、节流落盘、并发轮询、统一日志。
- `session_service._on_closed`（73 行）→ `_should_push`（开始/结束共用）、`_remember_quit_time`、
  `_finish_achievement_tracking`、`_enqueue_end_notification`、`_schedule_final_achievement_check`。
- `monitor_admin.remove_player`（84 行）→ `_detach_group_if_empty` 去重两处空群清理，
  晋升候选/运行时清理改用直接属性访问。

**第 4 批（语义化与护栏）**

- `GroupMutationResult.code` + `MutationCode` 枚举；`add_players`、`/steam delid`、Web 添加玩家
  不再用展示文案做分支。
- 5 个公共 API 补 Google 风格 docstring（会话投递、记账、价格卡、游戏库同步、监控启动）。
- 新增护栏 `tests/unit/test_code_hygiene.py`：禁止 `print`、裸 `except`、同包 `except ImportError`；
  白名单只有 `shared/logging.py`、`runtime_config.py`、`qqofficial_panel.py`（可选依赖）。
- 新增 `test_roster.py`（7 条）、`test_business_day.py`（5 条）、`tests/smoke_render.py`（离线三卡渲染）。

### 15.2 有意未做（需单独一轮）

| 项 | 原因 |
| --- | --- |
| 26 个方法/字段批量改名（§5.1/5.2） | 跨命令层、Web、测试约 40 个调用点；收益是表达，风险是漏改，建议随下轮功能改动一起做 |
| 6 个渲染大函数拆分（§7 #1–#6） | 纯绘制代码，拆分需逐像素回归；当前已有出图冒烟，但缺乏基线图对比 |
| `admin_api._api_gantt_data` / `_api_dashboard_rank_image` 改调 `RankingService` | 涉及 Web 面板展示口径，需要前端配合验证 |
| `settings.py` 收敛 75 处 `config.get` | 需与 `_conf_schema.json` 默认值逐项对齐，单独一轮更安全 |
| `cache_*_hours` 配置接线或删除 | 属于产品决策（是否暴露缓存 TTL），不是纯代码问题 |
| `remove_player` 三场景进一步拆成策略对象 | 现有 guard + 私有方法已可读，继续拆会增加跳转层级 |

### 15.3 验证方式

```bash
python -m pytest -q tests          # 399 passed, 4 skipped
python -m tests.smoke_render       # start/end/list 三张 PNG 均生成
```
