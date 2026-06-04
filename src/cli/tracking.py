"""追踪管理命令: add / import / sync / pause / resume"""

from __future__ import annotations

import asyncio
import logging

from config import get_settings
from core.orchestrator import Orchestrator
from core.tracking import TrackingTable
from db.repository import Repository
from db.session import get_session, init_db

logger = logging.getLogger(__name__)


def cmd_add(args: any) -> None:
    """添加动画到跟踪列表"""
    orchestrator = Orchestrator()
    if args.season_id:
        anime = asyncio.run(orchestrator.add_anime("", season_id=args.season_id))
    else:
        if not args.keyword:
            print("请提供动画关键词或使用 --season-id 指定")
            return
        anime = asyncio.run(orchestrator.add_anime(args.keyword))
    if anime:
        print(f"已添加: {anime.title} (season_id={anime.season_id})")


def cmd_import(args: any) -> None:
    """从 CSV 文件批量导入追踪目标并采集"""
    file_path = args.file
    table = TrackingTable(file_path)

    try:
        targets = table.load()
    except FileNotFoundError:
        if args.file:
            print(f"追踪文件不存在: {file_path}")
        else:
            default_path = get_settings().tracking_file
            print(f"追踪文件不存在: {default_path}")
            print("请先创建追踪文件或指定 --file 参数")
            print("")
            print("创建示例追踪文件:")
            print(f"  python -c \"from core.tracking import TrackingTable; TrackingTable('{default_path}').create_template()\"")
        return

    warnings = table.validate(targets)
    if warnings:
        print("校验警告:")
        for w in warnings:
            print(f"  ⚠ {w}")

    if not targets:
        print("追踪文件为空或无有效数据")
        return

    print(f"读取到 {len(targets)} 个追踪目标:")
    for t in targets:
        paused_label = " [暂停]" if t.paused else ""
        print(f"  [{t.platform}] season_id={t.season_id} {t.title}{paused_label}")

    if args.dry_run:
        print("\n[仅校验预览, 未入库]")
        return

    print("\n正在同步到数据库...")
    created, updated = table.sync_to_db(targets)
    print(f"新建: {created}, 更新: {updated}")

    if args.skip_crawl:
        print("跳过本次采集")
        return

    print("\n开始执行采集...")
    asyncio.run(table.crawl_all(targets))
    print("批量导入完成")


def cmd_sync(args: any) -> None:
    """对比追踪目录与数据库, 自动同步差异"""
    table = TrackingTable()
    asyncio.run(table.sync_all(dry_run=args.dry_run))


def cmd_pause(args: any) -> None:
    """暂停指定动画的采集"""
    init_db()
    with get_session() as session:
        repo = Repository(session)
        task = repo.toggle_pause(args.season_id, paused=True)
        if task is None:
            print(f"未找到 season_id={args.season_id} 的采集任务")
            return
        print(f"已暂停: season_id={args.season_id} (title='{task.title}')")


def cmd_resume(args: any) -> None:
    """恢复指定动画的采集"""
    init_db()
    with get_session() as session:
        repo = Repository(session)
        task = repo.toggle_pause(args.season_id, paused=False)
        if task is None:
            print(f"未找到 season_id={args.season_id} 的采集任务")
            return
        print(f"已恢复: season_id={args.season_id} (title='{task.title}')")
