"""定时调度器

基于 CrawlTask 表的调度信息, 定期自动执行采集任务。
检查 next_crawl_at 决定哪些任务到期, 按优先级执行。
跳过 paused=True 的暂停任务。
"""

from __future__ import annotations

import asyncio
import logging
import signal
from datetime import datetime

from client.factory import ClientFactory
from config import get_settings
from core.path_manager import raw_file_exists_today
from core.pipeline import Pipeline
from db.repository import Repository
from db.session import get_session, init_db

logger = logging.getLogger(__name__)


class Scheduler:
    """定时调度器

    定期检查 CrawlTask 表, 找出到期任务并执行采集。
    支持:
    - 按 next_crawl_at 调度到期任务
    - 按 priority 排序(在播优先)
    - 跳过 paused=True 的暂停任务
    - 采集失败时指数退避
    - 优雅关闭(SIGINT/SIGTERM)
    """

    def __init__(self) -> None:
        self._running = False
        init_db()

    async def start(self) -> None:
        """启动调度器主循环"""
        settings = get_settings()
        check_interval = settings.schedule_check_seconds

        self._running = True
        logger.info("调度器已启动, 检查间隔: %ds", check_interval)

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self._handle_signal, sig)
            except NotImplementedError:
                pass

        while self._running:
            await self._run_due_tasks()
            await asyncio.sleep(check_interval)

        logger.info("调度器已停止")

    def stop(self) -> None:
        """请求调度器停止"""
        self._running = False
        logger.info("收到停止信号, 调度器将在当前任务完成后退出...")

    def _handle_signal(self, sig: signal.Signals) -> None:
        logger.info("收到信号 %s, 准备停止...", sig.name)
        self.stop()

    async def _run_due_tasks(self) -> None:
        """检查并执行到期的采集任务"""
        with get_session() as session:
            repo = Repository(session)
            due_tasks = repo.get_due_tasks()

        if not due_tasks:
            return

        logger.info("发现 %d 个到期任务", len(due_tasks))

        for task in due_tasks:
            if not self._running:
                break

            if task.status == "success" and task.last_crawled_at \
                    and task.last_crawled_at.date() == datetime.now().date() \
                    and task.title and raw_file_exists_today(task.title, task.platform):
                logger.info("[%s-%s] 今天已成功采集，跳过", task.season_id, task.title)
                continue

            try:
                async with ClientFactory.create(platform=task.platform) as client:
                    with get_session() as session:
                        repo = Repository(session)
                        pipeline = Pipeline(repo, client)
                        repo.mark_task_running(task.id)
                        session.flush()

                    success = await pipeline.crawl_once(task.season_id, platform=task.platform)

                    with get_session() as session:
                        repo = Repository(session)
                        if success:
                            repo.mark_task_success(task.id, task.interval_minutes)
                        else:
                            repo.mark_task_failed(task.id, "采集过程中部分阶段失败")

            except Exception as e:
                logger.error("任务执行失败 season_id=%s platform=%s: %s", task.season_id, task.platform, e)
                try:
                    with get_session() as session:
                        repo = Repository(session)
                        repo.mark_task_failed(task.id, str(e)[:2000])
                except Exception:
                    logger.error("更新任务状态失败", exc_info=True)
