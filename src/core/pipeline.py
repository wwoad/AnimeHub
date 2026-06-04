"""数据管道

负责单个动画的完整采集流程:fetch → transform → store。
将 API 客户端获取的原始数据转换为 ORM 模型并持久化。

不涉及调度逻辑, 仅处理单次采集的数据流转。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

import pandas as pd
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

    def __init__(self, repo: Repository, client: BaseClient) -> None:
        self._repo = repo
        self._client = client
        self._fast_mode = getattr(client, "_fast_mode", False)
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

        episode_dicts = self._transform_episodes(info, platform)
        self._repo.sync_episodes(season_id, episode_dicts)

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
            desc=f"{self._tag()} 正片数据",
            unit="集",
            disable=not show_bar,
        )

        def _on_progress() -> None:
            pbar.update(1)

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

        if episode_stats and stat:
            total_favorite = sum(es.favorite for es in episode_stats)
            if total_favorite > 0:
                if self._raw_stat is not None:
                    self._raw_stat.favorite = total_favorite
                stat.favorite = total_favorite
                self._repo._session.flush()
                self._log("info", "[收藏累加] 总收藏: %d", total_favorite)

        self._log("info", "[5/5] 保存原始数据...")
        self._save_raw_excel(season_id, platform)

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
        """构建动画统计Sheet数据"""
        info = self._raw_info
        stat = self._raw_stat
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        row: dict[str, Any] = {
            "动画ID": info.season_id if info else season_id,
            "动画名": info.title if info else "",
            "地区": info.area if info else "",
            "评分": info.rating_score if info else 0,
            "评分人数": info.rating_count if info else 0,
            "状态": "完结" if (info and info.is_finish) else "连载中",
            "总集数": info.total if info.total else sum(1 for ep in (info.episodes or []) if ep.get("section_type", 0) == 0) if info else 0,
            "追番数": stat.follow if stat else 0,
            "播放量": stat.views if stat else 0,
            "弹幕": stat.danmaku if stat else 0,
            "点赞": stat.likes if stat else 0,
            "投币": stat.coins if stat else 0,
            "收藏": stat.favorite if stat else 0,
            "分享": stat.shares if stat else 0,
            "采集时间": now,
        }
        return [row]

    def _build_ep_sheet_rows(self, season_id: int, platform: str = "bilibili") -> list[dict]:
        """构建正片数据Sheet数据(仅正片，仅集号/BV号/统计数据)"""
        info = self._raw_info
        if not info:
            return []

        ep_list = list(info.episodes) if info.episodes else []

        stat_map: dict[int, dict] = {}
        if self._raw_episode_stats and self._raw_ep_ids:
            for ep_id, stat in zip(self._raw_ep_ids, self._raw_episode_stats):
                if stat is not None:
                    stat_map[ep_id] = stat

        rows: list[dict] = []
        for ep_data in ep_list:
            if ep_data.get("section_type", 0) != 0:
                continue
            ep_id = ep_data.get("id") or ep_data.get("ep_id")
            es = stat_map.get(ep_id, {}) if ep_id else {}
            rows.append(
                {
                    "集号": str(ep_data.get("title", "")),
                    "标题": ep_data.get("long_title", "") or ep_data.get("show_title", ""),
                    "BV号": ep_data.get("bvid", ""),
                    "时长(分)": round((ep_data.get("duration", 0) or 0) / 60000, 1) if isinstance(ep_data.get("duration", 0), (int, float)) else 0,
                    "播放量": es.get("view", 0),
                    "弹幕": es.get("dm", 0),
                    "评论": es.get("reply", 0),
                    "收藏": es.get("favorite", 0),
                    "点赞": es.get("like", 0),
                    "投币": es.get("coin", 0),
                    "分享": es.get("share", 0),
                }
            )
        return rows

    def _save_raw_excel(self, season_id: int, platform: str = "bilibili") -> None:
        """将本次采集数据保存为双Sheet Excel

        Sheet1 "动画统计": 动画基本信息 + 统计快照(单行)
        Sheet2 "正片数据": 每集数据(集号/BV号/各统计指标)
        存储位置: data/archive/{日期}/{平台}/{标题}/原始数据.xlsx
        """
        if not self._raw_info and not self._raw_stat and not self._raw_episode_stats:
            return

        title_slug = sanitize_title(self._raw_info.title) if self._raw_info else str(season_id)
        raw_path = get_raw_path(title_slug, platform)
        ensure_dir(raw_path.parent)

        anime_rows = self._build_anime_sheet_rows(season_id, platform)
        ep_rows = self._build_ep_sheet_rows(season_id, platform)

        if not anime_rows and not ep_rows:
            return

        try:
            with pd.ExcelWriter(str(raw_path), engine="openpyxl") as writer:
                if anime_rows:
                    df_anime = pd.DataFrame(anime_rows)
                    df_anime.to_excel(writer, sheet_name="动画统计", index=False)
                if ep_rows:
                    df_ep = pd.DataFrame(ep_rows)
                    df_ep.to_excel(writer, sheet_name="正片数据", index=False)

                for ws in writer.sheets.values():
                    for cell in ws[1]:
                        cell.font = Font(name="宋体", size=16, bold=True, color="4BACC6")
                        cell.alignment = Alignment(horizontal="center", vertical="center")
                    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
                        for cell in row:
                            cell.font = Font(name="宋体", size=16)
                            cell.alignment = Alignment(vertical="center")
                            if isinstance(cell.value, int):
                                cell.number_format = "#,##0"
                    for col_cells in ws.columns:
                        col_letter = get_column_letter(col_cells[0].column)
                        max_w = 0
                        for cell in col_cells:
                            val = str(cell.value or "")
                            w = sum(2.2 if ord(c) > 127 else 1.1 for c in val) + 3
                            if w > max_w:
                                max_w = w
                        ws.column_dimensions[col_letter].width = max_w * 1.5
            self._log("info", "原始数据已保存: %s", raw_path)
        except Exception as e:
            self._log("warning", "保存原始数据失败: %s", e)

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
            "total_episodes": info.total if info.total else sum(1 for ep in (info.episodes or []) if ep.get("section_type", 0) == 0),
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
            "favorite": getattr(stat, "favorite", 0),
            "captured_at": datetime.now(),
        }

        return AnimeStat(**stat_kwargs)

    @staticmethod
    def _transform_episodes(info, platform: str = "bilibili") -> list[dict]:
        """将集数数据转换为 Episode ORM 字典列表
        ,
                只保留正片(info.episodes)，跳过花絮/特别篇等 sections。
                通用字段放顶层, 平台专有ID放 extra。
        """
        episode_dicts: list[dict] = []

        for ep_data in info.episodes:
            if ep_data.get("section_type", 0) != 0:
                continue

            ep_id = ep_data.get("id") or ep_data.get("ep_id")
            if not ep_id:
                continue

            core = {
                "ep_id": ep_id,
                "anime_id": info.season_id,
                "title": str(ep_data.get("title", "")),
                "long_title": ep_data.get("long_title", ""),
                "duration_ms": ep_data.get("duration", 0) if isinstance(ep_data.get("duration"), (int, float)) else 0,
                "pub_time": _parse_timestamp(ep_data.get("pub_time")),
            }

            if platform == "bilibili":
                core["extra"] = {
                    "aid": ep_data.get("aid", 0),
                    "bvid": ep_data.get("bvid", ""),
                    "cid": ep_data.get("cid", 0),
                    "badge": ep_data.get("badge", ""),
                    "section_type": ep_data.get("section_type", 0),
                    "section_id": ep_data.get("section_id", 0),
                }

            episode_dicts.append(core)

        return episode_dicts
