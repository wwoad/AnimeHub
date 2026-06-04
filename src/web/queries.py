"""数据库查询辅助函数

为Web UI提供只读数据查询, 不修改任何数据。
"""

from __future__ import annotations

import pandas as pd
from sqlalchemy import func

from config import get_settings
from db.session import get_session_factory, init_db
from models.anime import Anime
from models.anime_stat import AnimeStat
from models.crawl_task import CrawlTask
from models.episode import Episode
from models.episode_stat import EpisodeStat


def _ensure_db() -> None:
    init_db()


def get_anime_list() -> pd.DataFrame:
    """获取所有跟踪动画列表(含最新统计)

    以 CrawlTask 为主表, 包含未爬取的追踪项。
    已爬取的项会显示 Anime 的详细信息和最新统计数据。
    """
    _ensure_db()
    Session = get_session_factory()
    with Session() as session:
        tasks = session.query(CrawlTask).order_by(CrawlTask.priority, CrawlTask.title).all()
        if not tasks:
            return pd.DataFrame()

        season_ids = [t.season_id for t in tasks]

        animes = session.query(Anime).filter(Anime.season_id.in_(season_ids)).all()
        anime_map = {a.season_id: a for a in animes}

        subq = (
            session.query(
                AnimeStat.anime_id,
                func.max(AnimeStat.id).label("max_id"),
            )
            .filter(AnimeStat.anime_id.in_(season_ids))
            .group_by(AnimeStat.anime_id)
            .subquery()
        )
        latest_stats = (
            session.query(AnimeStat)
            .join(subq, AnimeStat.id == subq.c.max_id)
            .all()
        )
        stat_map = {s.anime_id: s for s in latest_stats}

        rows = []
        for t in tasks:
            a = anime_map.get(t.season_id)
            s = stat_map.get(t.season_id)
            rows.append(
                {
                    "season_id": t.season_id,
                    "platform": t.platform,
                    "title": a.title if a else (t.title or f"动画{t.season_id}"),
                    "area": a.area if a else "",
                    "rating_score": float(a.rating_score) if (a and a.rating_score is not None) else 0.0,
                    "total_episodes": int(a.total_episodes) if (a and a.total_episodes is not None) else 0,
                    "is_finish": "完结" if (a and a.is_finish) else "连载中",
                    "views": s.views if s else 0,
                    "follow": s.follow if s else 0,
                    "danmaku": s.danmaku if s else 0,
                    "likes": s.likes if s else 0,
                    "coins": s.coins if s else 0,
                    "favorite": s.favorite if s else 0,
                    "status": str({"pending": "待采集", "running": "采集中", "success": "成功", "failed": "失败"}.get(t.status or "pending", "待采集")),
                    "last_crawled": str(t.last_crawled_at.strftime("%m-%d %H:%M")) if t.last_crawled_at else "未采集",
                    "paused": t.paused if t.paused is not None else False,
                }
            )

        return pd.DataFrame(rows)


def get_anime_detail(season_id: int) -> dict:
    """获取单动画详情(基本信息+最新统计+集数列表)"""
    _ensure_db()
    Session = get_session_factory()
    with Session() as session:
        anime = session.get(Anime, season_id)
        if not anime:
            return {}

        latest_stat = session.query(AnimeStat).filter(AnimeStat.anime_id == season_id).order_by(AnimeStat.captured_at.desc()).first()

        episodes = session.query(Episode).filter(Episode.anime_id == season_id).order_by(Episode.ep_id).all()

        ep_ids = [ep.ep_id for ep in episodes]
        latest_ep_stats = {}
        if ep_ids:
            subq = (
                session.query(
                    EpisodeStat.episode_id,
                    func.max(EpisodeStat.id).label("max_id"),
                )
                .filter(EpisodeStat.episode_id.in_(ep_ids))
                .group_by(EpisodeStat.episode_id)
                .subquery()
            )
            stats = session.query(EpisodeStat).join(subq, EpisodeStat.id == subq.c.max_id).all()
            latest_ep_stats = {s.episode_id: s for s in stats}

        ep_rows = []
        for ep in episodes:
            es = latest_ep_stats.get(ep.ep_id)
            ep_rows.append(
                {
                    "ep_id": ep.ep_id,
                    "title": ep.title,
                    "long_title": ep.long_title,
                    "bvid": (ep.extra or {}).get("bvid", ""),
                    "views": es.views if es else 0,
                    "danmaku": es.danmaku if es else 0,
                    "reply": es.reply if es else 0,
                    "favorite": es.favorite if es else 0,
                    "likes": es.likes if es else 0,
                    "coins": es.coins if es else 0,
                    "share": es.share if es else 0,
                }
            )

        return {
            "anime": anime,
            "stat": latest_stat,
            "episodes": ep_rows,
        }


def get_anime_stat_history(season_id: int, days: int = 30) -> pd.DataFrame:
    """获取动画统计历史趋势"""
    from datetime import datetime, timedelta

    _ensure_db()
    Session = get_session_factory()
    with Session() as session:
        since = datetime.now() - timedelta(days=days)
        stats = session.query(AnimeStat).filter(AnimeStat.anime_id == season_id, AnimeStat.captured_at >= since).order_by(AnimeStat.captured_at).all()

        if not stats:
            return pd.DataFrame()

        rows = [
            {
                "时间": s.captured_at.strftime("%m-%d %H:%M"),
                "播放量": s.views,
                "追番": s.follow,
                "弹幕": s.danmaku,
                "点赞": s.likes,
                "投币": s.coins,
            }
            for s in stats
        ]

        return pd.DataFrame(rows)


def load_discover_csv() -> pd.DataFrame:
    """加载发现列表CSV"""
    csv_path = get_settings().db_path.parent / "anime_info" / "bilibili.csv"
    if not csv_path.exists():
        return pd.DataFrame()

    return pd.read_csv(csv_path, encoding="utf-8-sig")


def toggle_tracking_paused(season_id: int, paused: bool) -> None:
    """切换追踪任务的暂停状态"""
    from db.repository import Repository
    from db.session import get_session

    with get_session() as session:
        repo = Repository(session)
        repo.toggle_pause(season_id, paused=paused)


def delete_tracking(season_ids: list[int]) -> int:
    """删除追踪任务, 同时从CSV中移除

    Returns:
        成功删除的数量
    """
    import csv as csv_mod
    from pathlib import Path

    from config import get_settings
    from db.repository import Repository
    from db.session import get_session

    count = 0
    for sid in season_ids:
        with get_session() as session:
            repo = Repository(session)
            task = repo.get_crawl_task(sid)
            if task:
                session.delete(task)
                session.flush()
                count += 1

    csv_path = get_settings().tracking_dir / "bilibili.csv"
    if csv_path.exists():
        rows = []
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv_mod.DictReader(f)
            for row in reader:
                if int(row.get("season_id", 0)) not in season_ids:
                    rows.append(row)
        if rows:
            with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
                writer = csv_mod.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
        else:
            csv_path.unlink()

    return count


def update_csv_paused(season_ids: list[int], paused: bool) -> None:
    """更新CSV中指定项的暂停状态"""
    import csv as csv_mod

    from config import get_settings

    csv_path = get_settings().tracking_dir / "bilibili.csv"
    if not csv_path.exists():
        return

    rows = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv_mod.DictReader(f)
        for row in reader:
            if int(row.get("season_id", 0)) in season_ids:
                row["paused"] = "true" if paused else "false"
            rows.append(row)

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv_mod.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def add_to_tracking(selected_ids: list[int], title_map: dict[int, str] | None = None) -> int:
    """将选中的动画添加到追踪CSV和数据库

    Args:
        selected_ids: 要添加的season_id列表
        title_map: season_id→标题的映射(可选, 从发现CSV获取)

    Returns:
        成功添加的数量
    """
    import csv as csv_mod

    from config import get_settings
    from db.repository import Repository
    from db.session import get_session

    if title_map is None:
        title_map = {}

    csv_path = get_settings().tracking_dir / "bilibili.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)

    existing_ids: set[int] = set()
    if csv_path.exists():
        with open(csv_path, encoding="utf-8-sig") as f:
            reader = csv_mod.DictReader(f)
            existing_ids = {int(row["season_id"]) for row in reader if "season_id" in row}

    new_rows = []
    added_count = 0
    for sid in selected_ids:
        if sid in existing_ids:
            continue

        title = title_map.get(sid, f"动画{sid}")
        new_rows.append(
            {
                "season_id": str(sid),
                "title": title,
                "priority": "1",
                "interval_minutes": "120",
                "paused": "false",
                "tags": "国创",
            }
        )
        added_count += 1

    if new_rows:
        need_header = not csv_path.exists()
        with open(csv_path, "a", encoding="utf-8-sig", newline="") as f:
            fieldnames = ["season_id", "title", "priority", "interval_minutes", "paused", "tags"]
            writer = csv_mod.DictWriter(f, fieldnames=fieldnames)
            if need_header:
                writer.writeheader()
            writer.writerows(new_rows)

        for row in new_rows:
            with get_session() as session:
                repo = Repository(session)
                task = repo.get_crawl_task(int(row["season_id"]))
                if not task:
                    from models.crawl_task import CrawlTask

                    task = CrawlTask(
                        platform="bilibili",
                        season_id=int(row["season_id"]),
                        title=row["title"],
                        priority=int(row["priority"]),
                        interval_minutes=int(row["interval_minutes"]),
                        paused=False,
                        tags=row["tags"],
                    )
                    repo.add_crawl_task(task)

    return added_count