"""任务编排器

协调 Pipeline 和 Repository, 编排完整的采集业务流程。
替代旧版 Tracker 类, 提供更高层的业务逻辑。
"""

from __future__ import annotations

import logging

from client.base import BaseClient
from client.factory import ClientFactory
from core.pipeline import Pipeline
from db.repository import Repository
from db.session import get_session, init_db
from models.anime import Anime

logger = logging.getLogger(__name__)


class Orchestrator:
    """任务编排器

    协调 Pipeline 和 Repository, 处理:
    - 添加动画(搜索 + 创建记录 + 创建采集任务 + 首次采集)
    - 手动触发单个动画采集
    - 手动触发全量采集
    - 采集任务的状态管理
    """

    def __init__(self) -> None:
        init_db()

    async def add_anime(self, keyword: str = "", season_id: int | None = None) -> Anime | None:
        """添加动画到跟踪列表

        支持两种模式:
        1. 通过 season_id 直接添加
        2. 通过关键词搜索后由用户选择

        添加后自动创建采集任务并执行首次采集。
        """
        async with ClientFactory.create("bilibili") as client:
            if season_id:
                return await self._add_by_season_id(client, season_id)

            if not keyword:
                print("请提供动画关键词或使用 --season-id 指定")
                return None

            results = await client.search(keyword)
            if not results:
                logger.info("未找到动画: %s", keyword)
                return None

            print(f"\n搜索到 {len(results)} 个结果:")
            for i, r in enumerate(results, 1):
                print(f"  [{i}] {r.title} | 类型: {r.season_type_name} | 集数: {r.ep_size} | 评分: {r.score} | {r.area} | {r.index_show}")

            choice = input("\n请选择动画编号 (0取消): ").strip()
            if not choice.isdigit() or int(choice) == 0:
                return None
            idx = int(choice) - 1
            if idx < 0 or idx >= len(results):
                return None

            return await self._add_by_season_id(client, results[idx].season_id)

    async def _add_by_season_id(self, client: BaseClient, season_id: int) -> Anime | None:
        """通过 season_id 添加动画"""
        with get_session() as session:
            repo = Repository(session)
            pipeline = Pipeline(repo, client)

            existing = repo.get_anime(season_id)
            if existing:
                logger.info("动画已存在: %s (season_id=%s)", existing.title, season_id)
                return existing

            anime = await pipeline.fetch_and_save_anime(season_id)
            if anime is None:
                return None

            await pipeline.fetch_and_save_episodes(season_id)
            pipeline.create_crawl_task(
                season_id,
                title=anime.title,
            )
            session.flush()

        logger.info("已添加动画: %s (season_id=%s)", anime.title, anime.season_id)
        await self.crawl_once(season_id)
        return anime

    async def crawl_once(self, season_id: int, platform: str = "bilibili") -> bool:
        """手动触发单个动画的完整采集"""
        logger.info("")
        logger.info("=" * 60)
        logger.info("[%s] 开始采集 (platform=%s)", season_id, platform)

        async with ClientFactory.create(platform) as client:
            with get_session() as session:
                repo = Repository(session)
                pipeline = Pipeline(repo, client)

                error_msg = None
                try:
                    success = await pipeline.crawl_once(season_id, platform)
                    if not success:
                        error_msg = "采集过程中部分阶段返回失败"
                except Exception as e:
                    error_msg = f"{type(e).__name__}: {e}"
                    logger.error("[%s] 采集中异常: %s", season_id, error_msg)
                    success = False

                # 无论成功/失败, 都更新任务状态
                task = repo.get_crawl_task(season_id)
                if task:
                    if success:
                        repo.mark_task_success(task.id, task.interval_minutes)
                        logger.info("[%s] 采集完成 (success)", season_id)
                    else:
                        repo.mark_task_failed(task.id, error_msg or "未知错误")
                        logger.warning("[%s] 采集完成 (failed: %s)", season_id, error_msg)

                return success

    async def crawl_all(self) -> None:
        """手动触发全量采集"""
        with get_session() as session:
            repo = Repository(session)
            anime_list = repo.get_all_anime()
            season_ids = [a.season_id for a in anime_list]

        if not season_ids:
            logger.info("没有跟踪中的动画, 请先用 add 命令添加")
            return

        for sid in season_ids:
            logger.info("[%s] 正在采集", sid)
            try:
                await self.crawl_once(sid)
            except Exception as e:
                logger.error("[%s] 采集失败: %s", sid, e)
