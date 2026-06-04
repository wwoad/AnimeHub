"""数据分析层

提供只读查询接口, 返回 pandas DataFrame。
所有通用列名使用中文, 平台专有字段通过 display 注册表注入。

导入 client/bilibili/display 即可自动注册 B站字段。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

# 导入各平台 display 自动注册字段
import client.bilibili.display  # noqa: F401
from client.display import get_extra_fields
from db.repository import Repository
from db.session import get_session


def _extra(obj, key: str, default=0):
    """从 extra JSON 中安全读取字段"""
    if obj is None:
        return default
    extra = getattr(obj, "extra", None) or {}
    return extra.get(key, default)


class Analyzer:
    """数据分析器

    所有查询方法返回 DataFrame, 供导出层和CLI使用。
    每个方法内部创建独立的 Repository 实例, 确保事务隔离。
    """

    # ============================================================
    # 动画列表
    # ============================================================

    def get_anime_list(self) -> pd.DataFrame:
        """获取所有跟踪中的动画列表"""
        with get_session() as session:
            repo = Repository(session)
            anime_list = repo.get_all_anime()
            rows = []

            for a in anime_list:
                row = {
                    "动画ID": a.season_id,
                    "标题": a.title,
                    "平台": a.platform,
                    "地区": a.area,
                    "风格": a.styles,
                    "评分": a.rating_score,
                    "评分人数": a.rating_count,
                    "是否完结": "是" if a.is_finish else "否",
                    "总集数": a.total_episodes,
                    "开播时间": a.pub_time,
                    "副标题": a.subtitle,
                }
                # 平台专有字段注入
                for f in get_extra_fields(a.platform, "anime"):
                    row[f.label] = _extra(a, f.key, "")
                rows.append(row)

            return pd.DataFrame(rows)

    # ============================================================
    # 动画总览
    # ============================================================

    def get_anime_overview(self, season_id: int) -> pd.DataFrame:
        """获取动画总览(基本信息 + 最新统计快照)"""
        with get_session() as session:
            repo = Repository(session)
            anime = repo.get_anime(season_id)
            if not anime:
                return pd.DataFrame()

            latest_stat = repo.get_latest_anime_stat(season_id)

            row = {
                "标题": anime.title,
                "动画ID": anime.season_id,
                "平台": anime.platform,
                "地区": anime.area,
                "风格": anime.styles,
                "评分": anime.rating_score,
                "评分人数": anime.rating_count,
                "是否完结": "是" if anime.is_finish else "否",
                "总集数": anime.total_episodes,
                "开播时间": anime.pub_time,
                "副标题": anime.subtitle,
            }
            # 平台专有字段注入
            for f in get_extra_fields(anime.platform, "anime"):
                row[f.label] = _extra(anime, f.key, "")

            if latest_stat:
                stat_row = {
                    "总播放量": latest_stat.views,
                    "总弹幕数": latest_stat.danmaku,
                    "总点赞数": latest_stat.likes,
                    "追番/订阅数": latest_stat.follow,
                    "总分享数": latest_stat.share,
                }
                # 平台专有统计字段
                for f in get_extra_fields(anime.platform, "anime_stat"):
                    safe_key = f.key.split(".")[0]
                    val = _extra(latest_stat, safe_key, 0)
                    if val:
                        stat_row[f.label] = val
                row.update(stat_row)

            return pd.DataFrame([row])

    # ============================================================
    # 单集详情
    # ============================================================

    def get_episode_detail(self, season_id: int) -> pd.DataFrame:
        """获取动画单集详情列表(含最新统计)"""
        with get_session() as session:
            repo = Repository(session)
            anime = repo.get_anime(season_id)
            platform = anime.platform if anime else "bilibili"

            all_episodes = repo.get_episodes(season_id)
            # 按 section_type 过滤正片(存在 extra 中)
            episodes = [e for e in all_episodes if (e.extra or {}).get("section_type", 0) == 0]
            if not episodes:
                episodes = all_episodes

            ep_ids = [ep.ep_id for ep in episodes]
            latest_stats = repo.get_latest_episode_stats(ep_ids)

            rows = []
            for ep in episodes:
                ep_extra = ep.extra or {}
                row = {
                    "集号": ep.title,
                    "副标题": ep.long_title,
                    "播放量": 0,
                    "弹幕数": 0,
                    "点赞数": 0,
                    "评论数": 0,
                    "收藏数": 0,
                    "分享数": 0,
                    "时长(秒)": round(ep.duration_ms / 1000) if ep.duration_ms else 0,
                    "发布时间": ep.pub_time,
                }
                # 平台专有 episode 字段
                for f in get_extra_fields(platform, "episode"):
                    val = ep_extra.get(f.key, "")
                    if val:
                        row[f.label] = val

                stat = latest_stats.get(ep.ep_id)
                if stat:
                    row.update(
                        {
                            "播放量": stat.views,
                            "弹幕数": stat.danmaku,
                            "点赞数": stat.likes,
                            "评论数": stat.reply,
                            "收藏数": stat.favorite,
                            "分享数": stat.share,
                        }
                    )
                    stat_extra = stat.extra or {}
                    for f in get_extra_fields(platform, "episode_stat"):
                        val = stat_extra.get(f.key, 0)
                        if val:
                            row[f.label] = val
                rows.append(row)

            return pd.DataFrame(rows)

    # ============================================================
    # 每日增量
    # ============================================================

    def get_daily_diff(self, season_id: int, days: int = 7) -> pd.DataFrame:
        """计算每日增量数据

        需要至少2次快照才能计算增量。
        通用指标始终显示, 平台专有指标从 extra 通过注册表读取。
        """
        since = datetime.now() - timedelta(days=days + 1)
        with get_session() as session:
            repo = Repository(session)
            anime = repo.get_anime(season_id)
            platform = anime.platform if anime else "bilibili"

            stats = repo.get_anime_stats_since(season_id, since)

            if len(stats) < 2:
                return pd.DataFrame()

            rows = []
            for i in range(1, len(stats)):
                curr = stats[i]
                prev = stats[i - 1]
                row = {
                    "日期": curr.captured_at.strftime("%Y-%m-%d"),
                    "播放量增量": curr.views - prev.views,
                    "弹幕增量": curr.danmaku - prev.danmaku,
                    "点赞增量": curr.likes - prev.likes,
                    "追番增量": curr.follow - prev.follow,
                    "总播放量": curr.views,
                    "追番/订阅数": curr.follow,
                }
                # 平台专有增量字段
                for f in get_extra_fields(platform, "anime_stat"):
                    safe_key = f.key
                    curr_val = _extra(curr, safe_key, 0)
                    prev_val = _extra(prev, safe_key, 0)
                    if curr_val or prev_val:
                        row[f"{f.label}增量"] = curr_val - prev_val
                rows.append(row)

            return pd.DataFrame(rows)

    # ============================================================
    # 单集排行
    # ============================================================

    def get_episode_rank(self, season_id: int) -> pd.DataFrame:
        """获取单集排行榜(按播放量排序, 含互动率)"""
        df = self.get_episode_detail(season_id)
        if df.empty:
            return df

        df["互动率(%)"] = 0.0
        if "播放量" in df.columns and "点赞数" in df.columns:
            mask = df["播放量"] > 0
            df.loc[mask, "互动率(%)"] = (df.loc[mask, "点赞数"] / df.loc[mask, "播放量"] * 100).round(2)
        if "播放量" in df.columns and "弹幕数" in df.columns:
            df["弹幕密度(%)"] = 0.0
            mask = df["播放量"] > 0
            df.loc[mask, "弹幕密度(%)"] = (df.loc[mask, "弹幕数"] / df.loc[mask, "播放量"] * 100).round(4)

        df = df.sort_values("播放量", ascending=False).reset_index(drop=True)
        df.index = df.index + 1
        df.index.name = "排名"
        return df
