"""数据仓库层

封装所有数据库操作, 为上层提供领域化的数据访问接口。
核心层和业务层通过 Repository 访问数据, 不直接操作 SQLAlchemy Session。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from models.anime import Anime
from models.anime_stat import AnimeStat
from models.crawl_task import CrawlTask
from models.episode import Episode
from models.episode_stat import EpisodeStat


class Repository:
    """数据仓库, 封装所有数据库 CRUD 操作"""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ============================================================
    # 动画 (Anime) 操作
    # ============================================================

    def get_anime(self, season_id: int) -> Anime | None:
        """根据 season_id 获取动画, 不存在返回 None"""
        return self._session.get(Anime, season_id)

    def get_all_anime(self) -> list[Anime]:
        """获取所有跟踪中的动画列表"""
        return list(self._session.execute(select(Anime)).scalars().all())

    def add_anime(self, anime: Anime) -> Anime:
        """添加动画记录"""
        self._session.add(anime)
        self._session.flush()
        return anime

    def upsert_anime(self, anime_data: dict) -> Anime:
        """插入或更新动画信息

        如果 season_id 已存在则更新字段, 否则创建新记录。
        """
        existing = self._session.get(Anime, anime_data["season_id"])
        if existing:
            for key, value in anime_data.items():
                if key != "season_id":
                    setattr(existing, key, value)
            existing.updated_at = datetime.now()
            self._session.flush()
            return existing

        anime = Anime(**anime_data)
        self._session.add(anime)
        self._session.flush()
        return anime

    # ============================================================
    # 动画统计 (AnimeStat) 操作
    # ============================================================

    def add_anime_stat(self, stat: AnimeStat) -> AnimeStat:
        """追加一条动画统计快照"""
        self._session.add(stat)
        self._session.flush()
        return stat

    def get_latest_anime_stat(self, season_id: int) -> AnimeStat | None:
        """获取动画最新一条统计快照"""
        return self._session.execute(select(AnimeStat).where(AnimeStat.anime_id == season_id).order_by(AnimeStat.captured_at.desc()).limit(1)).scalar_one_or_none()

    def get_anime_stats_since(self, season_id: int, since: datetime) -> list[AnimeStat]:
        """获取动画指定时间之后的统计快照(按时间升序)"""
        return list(self._session.execute(select(AnimeStat).where(AnimeStat.anime_id == season_id, AnimeStat.captured_at >= since).order_by(AnimeStat.captured_at)).scalars().all())

    # ============================================================
    # 单集 (Episode) 操作
    # ============================================================

    def get_episodes(self, season_id: int) -> list[Episode]:
        """获取动画下的所有集数"""
        stmt = select(Episode).where(Episode.anime_id == season_id).order_by(Episode.ep_id)
        return list(self._session.execute(stmt).scalars().all())

    def get_episode(self, ep_id: int) -> Episode | None:
        """根据 ep_id 获取单集信息"""
        return self._session.get(Episode, ep_id)

    def add_episode(self, episode: Episode) -> Episode:
        """添加单集记录"""
        self._session.add(episode)
        self._session.flush()
        return episode

    def update_episode(self, episode: Episode, updates: dict) -> Episode:
        """更新单集字段"""
        for key, value in updates.items():
            setattr(episode, key, value)
        episode.updated_at = datetime.now()
        self._session.flush()
        return episode

    def sync_episodes(self, season_id: int, episode_dicts: list[dict]) -> None:
        """同步动画集数列表

        对已存在的集数更新字段, 新增的集数创建记录。
        不会删除已有的集数, 只做插入/更新。
        使用批量查询替代逐个session.get, 减少DB往返次数。
        """
        if not episode_dicts:
            return

        ep_ids = [d.get("ep_id") for d in episode_dicts if d.get("ep_id")]
        existing_map: dict[int, Episode] = {}
        if ep_ids:
            rows = self._session.execute(select(Episode).where(Episode.ep_id.in_(ep_ids))).scalars().all()
            existing_map = {ep.ep_id: ep for ep in rows}

        new_eps: list[Episode] = []
        for ep_data in episode_dicts:
            ep_id = ep_data.get("ep_id")
            if not ep_id:
                continue
            existing = existing_map.get(ep_id)
            if existing:
                for key, value in ep_data.items():
                    if key not in ("ep_id", "anime_id") and value is not None:
                        setattr(existing, key, value)
                existing.updated_at = datetime.now()
            else:
                new_eps.append(Episode(**ep_data))

        if new_eps:
            self._session.add_all(new_eps)
        self._session.flush()

    # ============================================================
    # 单集统计 (EpisodeStat) 操作
    # ============================================================

    def add_episode_stats(self, stats: Sequence[EpisodeStat]) -> None:
        """批量添加单集统计快照"""
        self._session.add_all(stats)
        self._session.flush()

    def get_latest_episode_stat(self, ep_id: int) -> EpisodeStat | None:
        """获取单集最新统计快照"""
        return self._session.execute(select(EpisodeStat).where(EpisodeStat.episode_id == ep_id).order_by(EpisodeStat.captured_at.desc()).limit(1)).scalar_one_or_none()

    def get_latest_episode_stats(self, ep_ids: list[int]) -> dict[int, EpisodeStat]:
        """批量获取多个单集的最新统计快照(单次查询)"""
        from sqlalchemy import func

        subq = select(EpisodeStat.episode_id, func.max(EpisodeStat.id).label("max_id")).where(EpisodeStat.episode_id.in_(ep_ids)).group_by(EpisodeStat.episode_id).subquery()
        stmt = select(EpisodeStat).join(subq, EpisodeStat.id == subq.c.max_id)
        stats = list(self._session.execute(stmt).scalars().all())
        return {s.episode_id: s for s in stats}

    # ============================================================
    # 采集任务 (CrawlTask) 操作
    # ============================================================

    def get_crawl_task(self, season_id: int) -> CrawlTask | None:
        """根据 season_id 获取采集任务"""
        return self._session.execute(select(CrawlTask).where(CrawlTask.season_id == season_id)).scalar_one_or_none()

    def get_due_tasks(self) -> list[CrawlTask]:
        """获取所有到期需要采集的任务

        条件: status != 'running' AND paused = false AND (next_crawl_at IS NULL OR next_crawl_at <= now)
        按优先级升序排列, 优先级低的先执行。
        """
        now = datetime.now()
        results = (
            self._session.execute(
                select(CrawlTask)
                .where(CrawlTask.status != "running")
                .where(CrawlTask.paused == False)  # noqa: E712
                .where((CrawlTask.next_crawl_at == None) | (CrawlTask.next_crawl_at <= now))  # noqa: E711
                .order_by(CrawlTask.priority, CrawlTask.next_crawl_at)
            )
            .scalars()
            .all()
        )
        return list(results)

    def get_all_tasks(self) -> list[CrawlTask]:
        """获取所有采集任务"""
        return list(self._session.execute(select(CrawlTask)).scalars().all())

    def add_crawl_task(self, task: CrawlTask) -> CrawlTask:
        """创建采集任务"""
        self._session.add(task)
        self._session.flush()
        return task

    def mark_task_running(self, task_id: int) -> None:
        """将任务标记为运行中"""
        self._session.execute(update(CrawlTask).where(CrawlTask.id == task_id).values(status="running", updated_at=datetime.now()))
        self._session.flush()

    def mark_task_success(self, task_id: int, interval_minutes: int) -> None:
        """将任务标记为采集成功, 并计算下次采集时间"""
        now = datetime.now()
        next_time = now + timedelta(minutes=interval_minutes)
        self._session.execute(
            update(CrawlTask)
            .where(CrawlTask.id == task_id)
            .values(
                status="success",
                last_crawled_at=now,
                next_crawl_at=next_time,
                fail_count=0,
                last_error=None,
                updated_at=now,
            )
        )
        self._session.flush()

    def mark_task_failed(self, task_id: int, error: str) -> None:
        """将任务标记为采集失败, 累加失败次数"""
        task = self._session.get(CrawlTask, task_id)
        if task:
            task.status = "failed"
            task.fail_count = (task.fail_count or 0) + 1
            task.last_error = error[:2000] if error else None
            task.updated_at = datetime.now()
            wait_minutes = min(60 * (2**task.fail_count), 1440)
            task.next_crawl_at = datetime.now() + timedelta(minutes=wait_minutes)
            self._session.flush()

    def toggle_pause(self, season_id: int, paused: bool | None = None) -> CrawlTask | None:
        """切换任务的暂停状态

        Args:
            season_id: 动画ID
            paused: True=暂停, False=恢复, None=切换当前状态

        Returns:
            更新后的任务, 不存在返回 None
        """
        task = self.get_crawl_task(season_id)
        if task is None:
            return None
        task.paused = not task.paused if paused is None else paused
        task.updated_at = datetime.now()
        self._session.flush()
        return task
