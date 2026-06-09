"""采集管理命令: crawl / schedule / tasks"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from core.orchestrator import Orchestrator
from core.path_manager import raw_file_exists_today
from core.pipeline import Pipeline
from core.scheduler import Scheduler
from db.repository import Repository
from db.session import get_session, init_db
from db.worker import WorkerPool
from models.crawl_task import CrawlTask

logger = logging.getLogger(__name__)


async def _crawl_all(targets: list[tuple[int, str, str, int, int]]) -> None:
    """并发采集所有目标, 每个 slot 写入独立 worker DB 彻底消除锁竞争"""
    import contextlib

    from client.factory import ClientFactory
    from config import get_settings
    from utils.rate_limiter import RateLimiter

    settings = get_settings()
    max_concurrent = settings.max_concurrent_anime

    pool = WorkerPool(n_workers=max_concurrent, db_path=settings.db_path)
    pool.setup()

    shared_limiter = RateLimiter(rate=10, burst=12)
    clients = [ClientFactory.create("bilibili", rate_limiter=shared_limiter)]
    if settings.bilibili_cookie_2:
        clients.append(ClientFactory.create("bilibili", headers={"Cookie": settings.bilibili_cookie_2}, rate_limiter=shared_limiter))
        logger.info("双 Cookie 模式: %d 个客户端 (共享限速器)", len(clients))

    async def _crawl_one(season_id: int, platform: str, title: str, priority: int, interval_minutes: int, client) -> None:
        _ensure_task(season_id, platform, title, priority, interval_minutes)

        if _was_crawled_today(season_id, title, platform):
            logger.info("[%s-%s] 今天已成功采集，跳过", season_id, title)
            return

        slot = await pool.acquire()
        try:
            success = await _crawl_single(client, season_id, platform, pool, slot)
            if success:
                logger.info("[%s-%s] 采集完成", season_id, title)
            else:
                logger.warning("[%s-%s] 采集部分失败", season_id, title)
        except Exception as e:
            logger.error("[%s-%s] 采集失败: %s", season_id, title, e)
        finally:
            pool.release(slot)

    tasks = []
    for i, t in enumerate(targets):
        client = clients[i % len(clients)]
        tasks.append(_crawl_one(*t, client))

    async with contextlib.AsyncExitStack() as stack:
        for c in clients:
            await stack.enter_async_context(c)
        await asyncio.gather(*tasks)

    pool.merge()
    pool.cleanup()


async def _crawl_single(client, season_id: int, platform: str, pool: WorkerPool | None = None, worker_idx: int | None = None) -> bool:
    """在已有事件循环和客户端中采集单个动画

    Args:
        pool: WorkerPool, 设置后使用 worker DB 而非主库
        worker_idx: pool 中分配的 slot 编号
    """
    logger.info("")
    logger.info("=" * 60)
    logger.info("[%s] 开始采集 (platform=%s)", season_id, platform)

    if pool is not None and worker_idx is not None:
        ctx = pool.get_session(worker_idx)
    else:
        ctx = get_session()

    with ctx as session:
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

        task = repo.get_crawl_task(season_id)
        if task:
            if success:
                if pipeline._raw_info and pipeline._raw_info.is_finish:
                    task.interval_minutes = 24 * 60
                repo.mark_task_success(task.id, task.interval_minutes)
            else:
                repo.mark_task_failed(task.id, error_msg or "未知错误")

        return success


def cmd_crawl(args: any) -> None:
    """执行一次数据采集(无参数时直接读追踪CSV)"""

    if args.season_id:
        _ensure_task(args.season_id)

        title = ""
        platform = "bilibili"
        with get_session() as session:
            repo = Repository(session)
            task = repo.get_crawl_task(args.season_id)
            if task:
                title = task.title or ""
                platform = task.platform or "bilibili"
            if not title:
                anime = repo.get_anime(args.season_id)
                if anime:
                    title = anime.title or ""

        if _was_crawled_today(args.season_id, title, platform):
            logger.info("[%s] 今天已成功采集，跳过", args.season_id)
            return

        orchestrator = Orchestrator()
        success = asyncio.run(orchestrator.crawl_once(args.season_id))
        if success:
            logger.info("[%s] 采集完成", args.season_id)
        else:
            logger.warning("[%s] 采集部分失败", args.season_id)
        return

    from config import get_settings
    from core.tracking import TrackingTable

    dir_path = get_settings().tracking_dir
    if not dir_path.exists():
        logger.error("追踪目录不存在: %s", dir_path)
        return

    targets: list[tuple[int, str, str, int, int]] = []
    crawl_mode = getattr(args, "mode", "full")
    for csv_file in sorted(dir_path.glob("*.csv")):
        platform = csv_file.stem
        table = TrackingTable(csv_file)
        all_targets = table.load(platform_hint=platform)
        if not all_targets:
            continue
        active = [t for t in all_targets if not t.paused]
        if crawl_mode == "fast":
            active = [t for t in active if t.priority == 0]
            logger.info("[%s] 追踪 %d 项, 其中活跃 %d 项, 快速模式过滤后 %d 项", platform, len(all_targets), len([t for t in all_targets if not t.paused]), len(active))
        elif crawl_mode == "full":
            active = [t for t in active if t.priority >= 1]
            logger.info("[%s] 追踪 %d 项, 其中活跃 %d 项, 全量模式过滤后 %d 项", platform, len(all_targets), len([t for t in all_targets if not t.paused]), len(active))
        else:
            logger.info("[%s] 追踪 %d 项, 其中活跃 %d 项", platform, len(all_targets), len(active))
        for t in active:
            targets.append((t.season_id, t.platform, t.title, t.priority, t.interval_minutes))

    if targets:
        asyncio.run(_crawl_all(targets))
    logger.info("全量采集完成, 共 %d 项", len(targets))


def _was_crawled_today(season_id: int, title: str = "", platform: str = "bilibili") -> bool:
    """检查指定动画今天是否已成功采集过（DB AND 文件夹双重确认）

    AND 逻辑: DB 有成功记录 且 文件夹有文件 → 跳过。
    任一不满足 → 需要重采。
    """
    init_db()
    with get_session() as session:
        repo = Repository(session)
        task = repo.get_crawl_task(season_id)
        if not (task and task.status == "success" and task.last_crawled_at
                and task.last_crawled_at.date() == datetime.now().date()):
            return False

    if not title:
        return False

    if not raw_file_exists_today(title, platform):
        return False

    return True


def _ensure_task(season_id: int, platform: str = "bilibili", title: str = "", priority: int = 1, interval_minutes: int = 120) -> None:
    """确保 CrawlTask 存在"""
    init_db()
    with get_session() as session:
        repo = Repository(session)
        existing = repo.get_crawl_task(season_id)
        if existing:
            return
        task = CrawlTask(
            season_id=season_id,
            platform=platform,
            title=title,
            priority=priority,
            interval_minutes=interval_minutes,
        )
        repo.add_crawl_task(task)
        logger.info("已创建采集任务: season_id=%s", season_id)


def cmd_schedule(_: any) -> None:
    """启动定时调度器"""
    scheduler = Scheduler()
    try:
        asyncio.run(scheduler.start())
    except KeyboardInterrupt:
        scheduler.stop()


def cmd_tasks(_: any) -> None:
    """查看采集任务状态"""
    init_db()
    rows: list[dict] = []
    with get_session() as session:
        repo = Repository(session)
        tasks = repo.get_all_tasks()
        for task in tasks:
            last_crawled = task.last_crawled_at.strftime("%m-%d %H:%M") if task.last_crawled_at else "未采集"
            next_crawl = task.next_crawl_at.strftime("%m-%d %H:%M") if task.next_crawl_at else "立即"
            title_trunc = task.title[:18] + ".." if len(task.title) > 18 else task.title
            rows.append(
                dict(
                    id=task.id,
                    platform=task.platform,
                    season_id=task.season_id,
                    title_trunc=title_trunc,
                    status=task.status,
                    priority=task.priority,
                    paused="是" if task.paused else "否",
                    interval_minutes=task.interval_minutes,
                    last_crawled=last_crawled,
                    next_crawl=next_crawl,
                    fail_count=task.fail_count,
                )
            )

    if not rows:
        print("没有采集任务, 请先用 add 或 import 命令添加")
        return

    print(f"{'ID':<5} {'platform':<10} {'season_id':<12} {'标题':<20} {'状态':<10} {'优先级':<8} {'暂停':<6} {'间隔':<8} {'上次采集':<18} {'下次采集':<18} {'失败':<6}")
    print("-" * 125)
    for row in rows:
        print(f"{row['id']:<5} {row['platform']:<10} {row['season_id']:<12} {row['title_trunc']:<20} {row['status']:<10} {row['priority']:<8} {row['paused']:<6} {row['interval_minutes']:<8} {row['last_crawled']:<18} {row['next_crawl']:<18} {row['fail_count']:<6}")
