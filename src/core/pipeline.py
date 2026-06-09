"""数据管道

负责单个动画的完整采集流程:fetch → transform → store。
将 API 客户端获取的原始数据转换为 ORM 模型并持久化。

不涉及调度逻辑, 仅处理单次采集的数据流转。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter
from tqdm import tqdm

from client.base import BaseClient
from core.path_manager import ensure_dir, get_raw_path, sanitize_title
from db.repository import Repository
from models.anime import Anime
from models.anime_stat import AnimeStat
from models.crawl_task import CrawlTask
from models.episode_stat import EpisodeStat

logger = logging.getLogger(__name__)

_progress_listeners: list = []


def add_progress_listener(callback) -> None:
    _progress_listeners.append(callback)


def remove_progress_listener(callback) -> None:
    try:
        _progress_listeners.remove(callback)
    except ValueError:
        pass


def _notify_progress(season_id: int, title: str, current: int, total: int) -> None:
    for fn in _progress_listeners:
        fn(season_id, title, current, total)


def _parse_pub_time(pub_time_str: str) -> datetime | None:
    """将发布时间字符串转为datetime(兼容多种格式)"""
    if not pub_time_str:
        return None
    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(pub_time_str, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _parse_timestamp(ts) -> datetime | None:
    """将Unix时间戳转为datetime"""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts))
    except (ValueError, TypeError, OSError):
        return None


class Pipeline:
    """数据管道:fetch → transform → store

    将客户端获取的数据转换为 ORM 模型并写入数据库。
    每个方法对应一个采集阶段, 可被 Orchestrator 组合调用。
    """

    def __init__(self, repo: Repository, client: BaseClient, position: int | None = None) -> None:
        self._repo = repo
        self._client = client
        self._fast_mode = getattr(client, "_fast_mode", False)
        self._position = position
        self._raw_info = None
        self._raw_stat = None
        self._raw_episode_stats: list[dict | None] | None = None
        self._raw_ep_ids: list[int] | None = None
        self._season_id: int | None = None
        self._anime_title: str | None = None

    def _tag(self) -> str:
        """日志前缀: [season_id-标题] 或 [season_id]"""
        sid = self._season_id or ""
        title = self._anime_title
        if title:
            return f"[{sid}-{title}]"
        if sid:
            return f"[{sid}]"
        return ""

    def _log(self, level: str, msg: str, *args) -> None:
        prefixed = f"{self._tag()} {msg}"
        getattr(logger, level)(prefixed, *args)

    @staticmethod
    def _log_blank() -> None:
        """往日志文件写一个真正的空行(不带时间戳)"""
        for handler in logging.getLogger().handlers:
            if isinstance(handler, logging.FileHandler):
                handler.stream.write("\n")
                handler.stream.flush()

    async def fetch_and_save_anime(self, season_id: int, platform: str = "bilibili") -> Anime | None:
        """获取动画详情并保存/更新到数据库

        Args:
            season_id: 动画 season_id
            platform: 来源平台标识

        Returns:
            保存后的 Anime 对象, 获取失败返回 None
        """
        self._season_id = season_id
        info = await self._client.get_season_info(season_id)
        self._raw_info = info  # 缓存原始数据
        if info is None:
            self._log("error", "获取动画详情失败: platform=%s", platform)
            return None

        self._anime_title = info.title
        anime_data = self._transform_season_info(info, platform)
        existing = self._repo.get_anime(season_id)
        if existing:
            for key, value in anime_data.items():
                if key != "season_id" and value is not None:
                    setattr(existing, key, value)
            existing.updated_at = datetime.now()
            self._repo._session.flush()
            return existing

        anime = Anime(**anime_data)
        return self._repo.add_anime(anime)

    async def fetch_and_save_anime_stat(self, season_id: int, platform: str = "bilibili") -> AnimeStat | None:
        """获取动画统计快照并保存

        Args:
            season_id: 动画 season_id
            platform: 来源平台标识

        Returns:
            保存后的 AnimeStat 记录, 获取失败返回 None
        """
        stat = await self._client.get_season_stat(season_id)
        self._raw_stat = stat  # 缓存原始数据
        if stat is None:
            self._log("warning", "获取动画统计失败")
            return None

        anime_stat = self._transform_season_stat(season_id, stat, platform)
        return self._repo.add_anime_stat(anime_stat)

    async def fetch_and_save_episodes(self, season_id: int, platform: str = "bilibili") -> None:
        """获取动画集数列表并同步到数据库

        优先使用缓存的season_info, 避免重复API调用。
        不会删除已有集数, 仅做插入/更新。
        """
        info = self._raw_info
        if info is None:
            info = await self._client.get_season_info(season_id)
        if info is None:
            self._log("error", "获取动画详情失败(集数同步)")
            return

        episode_dicts = self._transform_all_episodes(info, platform)
        self._repo.sync_episodes(season_id, episode_dicts)

        # 诊断日志: 打印各类型 episode 数量
        type_counts = {"main": 0, "trailer": 0, "special": 0, "misc": 0}
        for d in episode_dicts:
            t = d.get("episode_type", "misc")
            type_counts[t] = type_counts.get(t, 0) + 1
        self._log(
            "info",
            "[3/5] 同步完成: main=%d trailer=%d special=%d misc=%d total=%d",
            type_counts["main"], type_counts["trailer"], type_counts["special"],
            type_counts["misc"], len(episode_dicts),
        )

    async def fetch_and_save_episode_stats(self, season_id: int, platform: str = "bilibili") -> list[EpisodeStat]:
        """获取所有单集的统计数据并批量保存

        Args:
            season_id: 动画 season_id
            platform: 来源平台标识

        Returns:
            保存成功的 EpisodeStat 列表
        """
        episodes = self._repo.get_episodes(season_id)
        if not episodes:
            self._log("warning", "未找到动画集数")
            return []

        ep_ids = [ep.ep_id for ep in episodes]
        if not ep_ids:
            self._log("warning", "未找到正片 ep_id")
            return []

        from config import get_settings
        show_bar = get_settings().show_progress_bar

        pbar = tqdm(
            total=len(ep_ids),
            desc=f"{self._tag()} 分集数据",
            unit="集",
            disable=not show_bar,
            position=self._position,
            leave=False,
        )

        progress_count = 0
        season_id_snapshot = self._season_id
        title_snapshot = self._anime_title

        def _on_progress() -> None:
            nonlocal progress_count
            pbar.update(1)
            progress_count += 1
            for listener in _progress_listeners:
                try:
                    listener(season_id_snapshot, title_snapshot, progress_count, len(ep_ids))
                except Exception:
                    pass

        self._log("info", "开始采集 %d 集单集数据...", len(ep_ids))
        stats = await self._client.get_bangumi_ep_stats_batch(ep_ids, progress_callback=_on_progress)
        pbar.close()

        self._raw_episode_stats = stats
        self._raw_ep_ids = ep_ids

        now = datetime.now()
        stat_records: list[EpisodeStat] = []
        for ep_id, stat in zip(ep_ids, stats):
            if stat is None:
                continue

            stat_kwargs = {
                "episode_id": ep_id,
                "views": stat.get("view", 0),
                "danmaku": stat.get("dm", 0),
                "reply": stat.get("reply", 0),
                "favorite": stat.get("favorite", 0),
                "share": stat.get("share", 0),
                "likes": stat.get("like", 0),
                "coins": stat.get("coin", 0),
                "captured_at": now,
            }

            stat_records.append(EpisodeStat(**stat_kwargs))

        if stat_records:
            self._repo.add_episode_stats(stat_records)
            self._log("info", "已保存 %d 条单集统计数据", len(stat_records))

        return stat_records

    async def crawl_once(self, season_id: int, platform: str = "bilibili") -> bool:
        """执行一次完整的单动画采集流程

        步骤1和2并发执行(无数据依赖), 步骤3-5串行。
        任一阶段失败不阻塞后续阶段, 但返回False表示有异常。

        Args:
            season_id: 动画 season_id
            platform: 来源平台标识

        Returns:
            全部成功返回True, 任一阶段失败返回False
        """
        success = True

        self._season_id = season_id

        # 步骤1和步骤2没有数据依赖, 并发执行
        self._log("info", "[1/5] 获取动画信息... [2/5] 获取统计快照... (并发)")
        anime, stat = await asyncio.gather(
            self.fetch_and_save_anime(season_id, platform),
            self.fetch_and_save_anime_stat(season_id, platform),
        )
        if anime is None:
            self._log("error", "[1/5] 失败: 动画信息获取为空")
            success = False
        else:
            self._log("info", "[1/5] OK")
            if self._raw_info and self._raw_info.season_type not in (2,):
                main_count = self._raw_info.total or sum(
                    1 for ep in (self._raw_info.episodes or []) if ep.get("section_type", 0) == 0
                )
                if main_count == 0:
                    self._log("warning", "[跳过] 正片集数为0 (未上映/已下架), 24小时后重试")
                    saved_task = self._repo.get_crawl_task(season_id)
                    if saved_task:
                        saved_task.interval_minutes = 24 * 60
                        saved_task.updated_at = datetime.now()
                        self._repo._session.flush()
                    return False
        if stat is None:
            self._log("warning", "[2/5] 跳过: 统计快照未获取")
            success = False
        else:
            self._log("info", "[2/5] OK")

        self._log("info", "[3/5] 同步集数列表...")
        await self.fetch_and_save_episodes(season_id, platform)
        self._log("info", "[3/5] OK")

        self._log("info", "[4/5] 采集单集数据...")
        episode_stats = await self.fetch_and_save_episode_stats(season_id, platform)
        self._log("info", "[4/5] OK: %d 集", len(episode_stats) if episode_stats else 0)

        if episode_stats and stat and self._raw_info:
            main_ep_ids = {
                (ep.get("id") if ep.get("id") is not None else ep.get("ep_id"))
                for ep in self._raw_info.episodes
                if ep.get("section_type", 0) == 0
            }

            all_ep_ids = {
                (ep.get("id") if ep.get("id") is not None else ep.get("ep_id"))
                for ep in self._raw_info.episodes
            }
            for section in self._raw_info.sections:
                for ep in section.get("episodes", []):
                    eid = ep.get("id") if ep.get("id") is not None else ep.get("ep_id")
                    if eid:
                        all_ep_ids.add(eid)

            ep_stat_map = {es.episode_id: es for es in episode_stats}

            all_stats = [
                stat for eid, stat in ep_stat_map.items()
                if eid in all_ep_ids
            ]
            main_stats = [
                stat for eid, stat in ep_stat_map.items()
                if eid in main_ep_ids
            ]

            try:
                if all_stats:
                    updated = False

                    # ORM 与 API 字段名映射: share→shares
                    _raw_field = {"share": "shares"}

                    for f in ("reply", "favorite"):
                        total = sum(getattr(es, f, 0) for es in all_stats)
                        api_val = getattr(stat, f, 0)
                        if total > api_val:
                            setattr(stat, f, total)
                            if self._raw_stat is not None:
                                setattr(self._raw_stat, _raw_field.get(f, f), total)
                            updated = True

                    for f in ("views", "danmaku", "likes", "coins", "share"):
                        main_total = sum(getattr(es, f, 0) for es in main_stats) if main_stats else 0
                        api_val = getattr(stat, f, 0)
                        if main_total > api_val:
                            setattr(stat, f, main_total)
                            if self._raw_stat is not None:
                                setattr(self._raw_stat, _raw_field.get(f, f), main_total)
                            updated = True

                    if updated:
                        self._repo._session.flush()
                        self._log("info", "[分集累加] 全量reply/favorite + 主集max修正 完成")
            except Exception as e:
                self._log("warning", "[分集累加] 跳过: %s", e)

        self._log("info", "[5/5] 保存原始数据...")
        if not self._save_raw_excel(season_id, platform):
            success = False

        return success

    def create_crawl_task(
        self,
        season_id: int,
        platform: str = "bilibili",
        priority: int = 1,
        interval_minutes: int = 60,
        title: str = "",
        tags: str = "",
    ) -> CrawlTask:
        """为动画创建采集任务

        Args:
            season_id: 动画 season_id
            platform: 来源平台标识
            priority: 优先级 (0=在播, 1=普通, 2=低频/已完结)
            interval_minutes: 采集间隔(分钟)
            title: 动画标题(冗余存储)
            tags: 自定义标签(分号分隔)

        Returns:
            创建的 CrawlTask 对象
        """
        existing = self._repo.get_crawl_task(season_id)
        if existing:
            self._log("info", "采集任务已存在: platform=%s", platform)
            return existing

        task = CrawlTask(
            season_id=season_id,
            platform=platform,
            title=title,
            status="pending",
            priority=priority,
            interval_minutes=interval_minutes,
            tags=tags,
        )
        return self._repo.add_crawl_task(task)

    # ============================================================
    # 数据转换方法
    # ============================================================

    # ============================================================
    # 原始数据保存(Excel 双Sheet, 中文列名, 无ep_id)
    # ============================================================

    _ANIME_CN = {
        "动画ID": "season_id",
        "标题": "title",
        "地区": "area",
        "风格": "styles",
        "评分": "rating_score",
        "评分人数": "rating_count",
        "是否完结": "is_finish",
        "总集数": "total_episodes",
        "开播时间": "pub_time",
        "副标题": "subtitle",
        "类型": "season_type",
        "简介": "evaluate",
        "创作者": "up_name",
    }
    _ANIME_STAT_CN = {
        "追番数": "follow",
        "播放量": "views",
        "弹幕": "danmaku",
        "点赞": "likes",
        "投币": "coins",
        "分享": "share",
    }
    _EP_STAT_CN = {
        "集号": "title",
        "集副标题": "long_title",
        "BV号": "bvid",
        "播放量": "views",
        "弹幕": "danmaku",
        "评论": "reply",
        "收藏": "favorite",
        "点赞": "likes",
        "投币": "coins",
        "分享": "share",
    }

    def _build_anime_sheet_rows(self, season_id: int, platform: str = "bilibili") -> list[dict]:
        """构建动画级总数据行"""
        info = self._raw_info
        stat = self._raw_stat
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        row: dict[str, Any] = {
            "SID": info.season_id if info else season_id,
            "动画标题": info.title if info else "",
            "地区": info.area if info else "",
            "评分": info.rating_score if info else 0,
            "评分人数": info.rating_count if info else 0,
            "状态": "完结" if (info and info.is_finish) else "连载中",
            "总集数": info.total if info.total else sum(1 for ep in (info.episodes or []) if ep.get("section_type", 0) == 0) if info else 0,
            "追番数": stat.follow if stat else 0,
            "播放量": stat.views if stat else 0,
            "弹幕": stat.danmaku if stat else 0,
            "评论": getattr(stat, "reply", 0) if stat else 0,
            "点赞": stat.likes if stat else 0,
            "投币": stat.coins if stat else 0,
            "收藏": stat.favorite if stat else 0,
            "分享": stat.shares if stat else 0,
            "采集时间": now,
        }
        return [row]

    def _build_ep_sheet_rows(self, season_id: int, platform: str = "bilibili"):
        """构建分集数据与分段汇总, 返回 (main_rows, trailer_row, special_row, misc_row)

        优先从 info.episodes 获取正片(保留顺序),
        非正片从 API 和 DB 双源汇总, DB 作为兜底确保无遗漏。
        """
        info = self._raw_info
        if not info:
            return [], None, None, None

        stat_map: dict[int, dict] = {}
        if self._raw_episode_stats and self._raw_ep_ids:
            for ep_id, stat in zip(self._raw_ep_ids, self._raw_episode_stats):
                if stat is not None:
                    stat_map[ep_id] = stat

        def _fmt_ts(ep_data):
            ts = ep_data.get("pub_time")
            if not ts:
                return ""
            try:
                return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
            except (ValueError, TypeError, OSError):
                return ""

        def _make_ep_row(ep_data, es, db_extra=None):
            row = {
                "集号": str(ep_data.get("title", "")),
                "标题": ep_data.get("long_title", "") or ep_data.get("show_title", ""),
                "BV号": ep_data.get("bvid", ""),
                "发布时间": _fmt_ts(ep_data),
                "时长(分)": round((ep_data.get("duration", 0) or 0) / 60000, 1) if isinstance(ep_data.get("duration", 0), (int, float)) else 0,
                "备注": ep_data.get("badge", "") or "限免",
                "播放量": es.get("view", 0),
                "弹幕": es.get("dm", 0),
                "评论": es.get("reply", 0),
                "点赞": es.get("like", 0),
                "投币": es.get("coin", 0),
                "收藏": es.get("favorite", 0),
                "分享": es.get("share", 0),
            }
            if db_extra and not row["BV号"]:
                row["BV号"] = db_extra.get("bvid", "")
            if db_extra and row["备注"] == "限免":
                row["备注"] = db_extra.get("badge", "") or "限免"
            return row

        section_meta: dict[str, dict] = {
            "trailer": {"count": 0, "duration_sum": 0},
            "special": {"count": 0, "duration_sum": 0},
            "misc": {"count": 0, "duration_sum": 0},
        }
        section_stats: dict[str, dict[str, int]] = {
            "trailer": {},
            "special": {},
            "misc": {},
        }

        total_stats: dict[str, int] = {}

        def _acc_total(es):
            for k in ("view", "dm", "reply", "like", "coin", "favorite", "share"):
                total_stats[k] = total_stats.get(k, 0) + es.get(k, 0)

        def _acc_sec(ep_type, ep_data, es):
            meta = section_meta[ep_type]
            meta["count"] += 1
            meta["duration_sum"] += ep_data.get("duration", 0) or 0
            sums = section_stats[ep_type]
            for k in ("view", "dm", "reply", "like", "coin", "favorite", "share"):
                sums[k] = sums.get(k, 0) + es.get(k, 0)

        # API 数据查找表 (优先 episodes, 其次 section episodes)
        api_eps: dict[int, dict] = {}
        for ep_data in (info.episodes or []):
            ep_id = ep_data.get("id") if ep_data.get("id") is not None else ep_data.get("ep_id")
            if ep_id:
                api_eps.setdefault(ep_id, ep_data)
        for section in (info.sections or []):
            for ep_data in section.get("episodes", []):
                ep_id = ep_data.get("id") if ep_data.get("id") is not None else ep_data.get("ep_id")
                if ep_id:
                    api_eps.setdefault(ep_id, ep_data)

        # Section 查找表 (用于分类)
        section_map: dict[int, dict] = {}
        for section in (info.sections or []):
            sid = section.get("id")
            if sid:
                section_map[sid] = section

        # DB 查找表 (兜底元数据)
        db_episodes = self._repo.get_episodes(season_id)
        db_ep_map: dict[int, dict] = {}
        for db_ep in db_episodes:
            db_ep_map[db_ep.ep_id] = db_ep.extra or {}

        main_rows: list[dict] = []
        processed_ids: set[int] = set()

        # 第一遍: info.episodes (保留正片顺序)
        for ep_data in (info.episodes or []):
            ep_id = ep_data.get("id") if ep_data.get("id") is not None else ep_data.get("ep_id")
            if not ep_id or ep_id in processed_ids:
                continue
            processed_ids.add(ep_id)
            es = stat_map.get(ep_id, {})
            db_extra = db_ep_map.get(ep_id)

            if ep_data.get("section_type", 0) == 0:
                main_rows.append(_make_ep_row(ep_data, es, db_extra))
                _acc_total(es)
            else:
                section_id = ep_data.get("section_id", 0)
                section = section_map.get(section_id, {})
                ep_type = Pipeline._classify_episode_type(section) if section else "misc"
                _acc_sec(ep_type, ep_data, es)
                _acc_total(es)

        # 第二遍: section episodes (B站 section 接口返回的)
        for section in (info.sections or []):
            ep_type = Pipeline._classify_episode_type(section)
            for ep_data in section.get("episodes", []):
                ep_id = ep_data.get("id") if ep_data.get("id") is not None else ep_data.get("ep_id")
                if not ep_id or ep_id in processed_ids:
                    continue
                processed_ids.add(ep_id)
                es = stat_map.get(ep_id, {})
                _acc_sec(ep_type, ep_data, es)
                _acc_total(es)

        # 第三遍: DB 兜底 —— 仍未被覆盖的 episode
        for db_ep in db_episodes:
            if db_ep.ep_id in processed_ids:
                continue
            processed_ids.add(db_ep.ep_id)
            es = stat_map.get(db_ep.ep_id, {})
            ep_data = api_eps.get(db_ep.ep_id, {})
            db_extra = db_ep.extra or {}
            dur = ep_data.get("duration", 0) or db_ep.duration_ms

            if db_ep.episode_type == "main":
                if not ep_data:
                    ep_data = {
                        "title": db_ep.title,
                        "long_title": db_ep.long_title,
                        "duration": db_ep.duration_ms,
                    }
                if not ep_data.get("bvid"):
                    ep_data["bvid"] = db_extra.get("bvid", "")
                if not ep_data.get("badge"):
                    ep_data["badge"] = db_extra.get("badge", "")
                main_rows.append(_make_ep_row(ep_data, es, db_extra))
                _acc_total(es)
            else:
                _acc_sec(db_ep.episode_type, {"duration": dur}, es)
                _acc_total(es)

        def _build_summary(ep_type, label):
            meta = section_meta[ep_type]
            sums = section_stats[ep_type]
            if meta["count"] == 0:
                return None
            total_dur = round(meta["duration_sum"] / 60000, 1)
            avg_dur = round(total_dur / meta["count"], 1)
            return {
                "分类": label,
                "视频数": meta["count"],
                "总时长(分)": total_dur,
                "平均时长(分)": avg_dur,
                "播放量": sums.get("view", 0),
                "弹幕": sums.get("dm", 0),
                "评论": sums.get("reply", 0),
                "点赞": sums.get("like", 0),
                "投币": sums.get("coin", 0),
                "收藏": sums.get("favorite", 0),
                "分享": sums.get("share", 0),
            }

        return (
            main_rows,
            _build_summary("trailer", "预告汇总"),
            _build_summary("special", "花絮汇总"),
            _build_summary("misc", "杂项汇总"),
            total_stats,
        )

    def _save_raw_excel(self, season_id: int, platform: str = "bilibili") -> bool:
        """按五段式布局保存单 Sheet Excel, 返回是否保存成功

        [总数据] → 空2行 → [分集数据] → 空2行 → [预告] → [花絮] → [杂项]
        所有单元格右对齐, 采集时间仅动画级行保留。
        """
        if not self._raw_info and not self._raw_stat and not self._raw_episode_stats:
            return False

        title_slug = sanitize_title(self._raw_info.title) if self._raw_info else str(season_id)
        raw_path = get_raw_path(title_slug, platform)
        ensure_dir(raw_path.parent)

        anime_rows = self._build_anime_sheet_rows(season_id, platform)
        main_ep_rows, trailer_row, special_row, misc_row, total_stats = self._build_ep_sheet_rows(season_id, platform)

        if total_stats and anime_rows:
            anime_rows[0]["评论"] = total_stats.get("reply", 0)
            anime_rows[0]["收藏"] = total_stats.get("favorite", 0)
            anime_rows[0]["分享"] = total_stats.get("share", 0)

        if not anime_rows and not main_ep_rows:
            return False

        try:
            from openpyxl import Workbook

            wb = Workbook()
            ws = wb.active
            ws.title = "动画统计"

            font = Font(name="宋体", size=16)
            ralign = Alignment(horizontal="right", vertical="center")

            def _write_cell(r, c, value):
                cell = ws.cell(row=r, column=c, value=value)
                cell.font = font
                cell.alignment = ralign
                if isinstance(value, int):
                    cell.number_format = "#,##0"
                return cell

            row = 1

            # ================================================
            # 段1: 动画级总数据
            # ================================================
            if anime_rows:
                anime_data = anime_rows[0]
                anime_keys = list(anime_data.keys())
                for col, key in enumerate(anime_keys, 1):
                    _write_cell(row, col, key)
                row += 1
                for col, key in enumerate(anime_keys, 1):
                    _write_cell(row, col, anime_data[key])
                row += 1

            row += 2

            # ================================================
            # 段2: 分集数据
            #   独有列(col 1-6) + 空2列(col 7-8) → 播放量对齐顶部表头 col 9
            #   播放量~分享不再写表头, 由顶部表头统一覆盖
            # ================================================
            ep_unique_keys = ["集号", "标题", "BV号", "发布时间", "时长(分)", "备注"]
            ep_stat_keys = ["播放量", "弹幕", "评论", "点赞", "投币", "收藏", "分享"]
            if main_ep_rows:
                for col, key in enumerate(ep_unique_keys, 1):
                    _write_cell(row, col, key)
                row += 1
                for ep_row in main_ep_rows:
                    for col, key in enumerate(ep_unique_keys, 1):
                        _write_cell(row, col, ep_row.get(key, ""))
                    for col, key in enumerate(ep_stat_keys, 9):
                        _write_cell(row, col, ep_row.get(key, ""))
                    row += 1

            row += 2

            # ================================================
            # 段3-5: 汇总行
            #   独有列(col 1-4) + 空4列(col 5-8) → 播放量对齐顶部表头 col 9
            # ================================================
            summary_unique_keys = ["分类", "视频数", "总时长(分)", "平均时长(分)"]
            summary_stat_keys = ["播放量", "弹幕", "评论", "点赞", "投币", "收藏", "分享"]
            summary_rows = [r for r in [trailer_row, special_row, misc_row] if r is not None]
            if summary_rows:
                for col, key in enumerate(summary_unique_keys, 1):
                    _write_cell(row, col, key)
                row += 1
            for srow in summary_rows:
                for col, key in enumerate(summary_unique_keys, 1):
                    _write_cell(row, col, srow.get(key, ""))
                for col, key in enumerate(summary_stat_keys, 9):
                    _write_cell(row, col, srow.get(key, ""))
                row += 1

            # 自适应列宽
            for col_cells in ws.columns:
                col_letter = get_column_letter(col_cells[0].column)
                max_w = 0
                for cell in col_cells:
                    val = str(cell.value or "")
                    w = sum(2.2 if ord(c) > 127 else 1.1 for c in val) + 3
                    if w > max_w:
                        max_w = w
                ws.column_dimensions[col_letter].width = min(max_w * 1.5, 60)

            wb.save(str(raw_path))
            self._log("info", "原始数据已保存: %s", raw_path)
            return True
        except Exception as e:
            self._log("error", "保存原始数据失败: %s", e)
            return False

    # ============================================================
    # 数据转换方法 - 按平台分发
    # ============================================================

    @staticmethod
    def _transform_season_info(info, platform: str = "bilibili") -> dict:
        """将平台详情数据转换为 Anime ORM 字典

        通用字段放在顶层, 平台专有字段放入 extra。
        """
        core = {
            "season_id": info.season_id,
            "title": info.title,
            "cover": info.cover,
            "area": info.area,
            "styles": ",".join(info.styles) if isinstance(getattr(info, "styles", None), list) else "",
            "rating_score": info.rating_score,
            "rating_count": info.rating_count,
            "is_finish": info.is_finish,
            "total_episodes": info.total if info.total and info.total > 0 else sum(1 for ep in (info.episodes or []) if ep.get("section_type", 0) == 0),
            "pub_time": _parse_pub_time(info.pub_time),
            "subtitle": info.subtitle,
            "season_type": getattr(info, "season_type", 0),
            "evaluate": getattr(info, "evaluate", ""),
            "up_name": getattr(info, "up_name", ""),
        }

        if platform == "bilibili":
            core["extra"] = {
                "media_id": getattr(info, "media_id", 0),
                "up_mid": getattr(info, "up_mid", 0),
                "update_weekday": getattr(info, "update_weekday", 0),
                "share_url": getattr(info, "share_url", ""),
                "new_ep_title": getattr(info, "new_ep_title", ""),
                "new_ep_id": getattr(info, "new_ep_id", 0),
            }

        return core

    @staticmethod
    def _classify_episode_type(section: dict) -> str:
        """根据 section 的 type 和 title 判断类型
        返回: "trailer" | "special" | "misc"
        """
        title = str(section.get("title", ""))
        if section.get("type") == 1 or any(k in title for k in ("预告", "PV", "CM", "宣传", "先导", "倒计时", "角色来电", "角色PV")):
            return "trailer"
        if any(k in title for k in ("花絮", "幕后", "制作", "动捕", "设计", "建模", "配音")):
            return "special"
        return "misc"

    @staticmethod
    def _transform_season_stat(season_id: int, stat, platform: str = "bilibili") -> AnimeStat:
        """将平台统计对象转换为 AnimeStat ORM 对象"""
        stat_kwargs = {
            "anime_id": season_id,
            "views": getattr(stat, "views", 0),
            "follow": getattr(stat, "follow", 0),
            "danmaku": getattr(stat, "danmaku", 0),
            "likes": getattr(stat, "likes", 0),
            "coins": getattr(stat, "coins", 0),
            "share": getattr(stat, "shares", 0),
            "reply": getattr(stat, "reply", 0),
            "favorite": getattr(stat, "favorite", 0),
            "captured_at": datetime.now(),
        }

        return AnimeStat(**stat_kwargs)

    @staticmethod
    def _transform_all_episodes(info, platform: str = "bilibili") -> list[dict]:
        """将集数数据转换为 Episode ORM 字典列表

        处理 info.episodes(正片) 和 info.sections(花絮/预告/杂项)。
        使用 _classify_episode_type 对 section 分类。
        输出顺序: main → special → trailer → misc。
        """

        def _build_ep(ep_data, ep_type, section_id=0, section_title=""):
            ep_id = ep_data.get("id") if ep_data.get("id") is not None else ep_data.get("ep_id")
            if not ep_id:
                return None
            core = {
                "ep_id": ep_id,
                "anime_id": info.season_id,
                "title": str(ep_data.get("title", "")),
                "long_title": ep_data.get("long_title", ""),
                "duration_ms": ep_data.get("duration", 0) if isinstance(ep_data.get("duration"), (int, float)) else 0,
                "pub_time": _parse_timestamp(ep_data.get("pub_time")),
                "episode_type": ep_type,
            }
            if platform == "bilibili":
                core["extra"] = {
                    "aid": ep_data.get("aid", 0),
                    "bvid": ep_data.get("bvid", ""),
                    "cid": ep_data.get("cid", 0),
                    "badge": ep_data.get("badge", "") or "限免",
                    "section_type": ep_data.get("section_type", 0),
                    "section_id": section_id,
                    "section_title": section_title,
                }
            return core

        seen_ids: set[int] = set()
        main_eps: list[dict] = []
        special_eps: list[dict] = []
        trailer_eps: list[dict] = []
        misc_eps: list[dict] = []

        for ep_data in (info.episodes or []):
            ep_id = ep_data.get("id") if ep_data.get("id") is not None else ep_data.get("ep_id")
            if not ep_id or ep_id in seen_ids:
                continue
            seen_ids.add(ep_id)
            section_id = ep_data.get("section_id", 0)

            if ep_data.get("section_type", 0) == 0:
                section_title = ""
                for section in (info.sections or []):
                    if section.get("id") == section_id and section_id > 0:
                        section_title = section.get("title", "")
                        break
                ep = _build_ep(ep_data, "main", section_id, section_title)
                if ep:
                    main_eps.append(ep)
            else:
                section = None
                if section_id > 0:
                    for s in (info.sections or []):
                        if s.get("id") == section_id:
                            section = s
                            break
                ep_type = Pipeline._classify_episode_type(section) if section else "misc"
                section_title = section.get("title", "") if section else ""
                ep = _build_ep(ep_data, ep_type, section_id, section_title)
                target_list = {"special": special_eps, "trailer": trailer_eps}.get(ep_type, misc_eps)
                if ep:
                    target_list.append(ep)

        for section in (info.sections or []):
            ep_type = Pipeline._classify_episode_type(section)
            section_id = section.get("id", 0)
            section_title = section.get("title", "")
            target_list = {"special": special_eps, "trailer": trailer_eps}.get(ep_type, misc_eps)
            for ep_data in section.get("episodes", []):
                ep_id = ep_data.get("id") if ep_data.get("id") is not None else ep_data.get("ep_id")
                if not ep_id or ep_id in seen_ids:
                    continue
                seen_ids.add(ep_id)
                ep = _build_ep(ep_data, ep_type, section_id, section_title)
                if ep:
                    target_list.append(ep)

        return main_eps + special_eps + trailer_eps + misc_eps

    # ============================================================
