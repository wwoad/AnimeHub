"""追踪表管理

提供两种追踪方式:
1. 单文件追踪(旧): 一个 CSV 包含所有平台, 带 platform 列
2. 目录追踪(新): data/tracking/{platform}.csv, 文件名决定平台

用户通过编辑 CSV 文件管理追踪目标(增删改行),
sync 命令自动对比 CSV 与数据库, 同步差异。
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from pathlib import Path

from config import get_settings
from db.repository import Repository
from db.session import get_session, init_db
from models.crawl_task import CrawlTask

logger = logging.getLogger(__name__)

# 支持的平台标识列表
SUPPORTED_PLATFORMS = {"bilibili"}

# CSV 必填列(单文件模式需要 platform 列, 目录模式不需要)
REQUIRED_COLUMNS = {"season_id"}


@dataclass
class TrackingTarget:
    """追踪目标数据类"""

    platform: str = "bilibili"
    season_id: int = 0
    title: str = ""
    priority: int = 1
    interval_minutes: int = 60
    paused: bool = False
    tags: str = ""


@dataclass
class ReconcileDiff:
    """同步差异报告"""

    created: list[TrackingTarget] = field(default_factory=list)
    paused: list[tuple[str, int]] = field(default_factory=list)
    updated: list[TrackingTarget] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.created or self.paused or self.updated)


class TrackingTable:
    """追踪表管理器

    支持两种模式:
    - 单文件模式: 传入 path 参数读取单个 CSV(向后兼容)
    - 目录模式: scan_dir() 读取 tracking/ 下所有 {platform}.csv
    """

    def __init__(self, path: Path | str | None = None) -> None:
        init_db()
        if path is not None:
            self._path = Path(path)
        else:
            settings = get_settings()
            self._path = settings.tracking_file

    # ============================================================
    # 单文件读取(向后兼容)
    # ============================================================

    def load(self, platform_hint: str = "") -> list[TrackingTarget]:
        """读取单个 CSV 文件, 返回目标列表

        Args:
            platform_hint: 文件名推定的平台(当 CSV 无 platform 列时使用)

        Returns:
            解析后的 TrackingTarget 列表
        """
        if not self._path.exists():
            raise FileNotFoundError(f"追踪文件不存在: {self._path}")

        with open(self._path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                raise ValueError("CSV 文件为空")

            targets: list[TrackingTarget] = []
            has_platform_col = "platform" in reader.fieldnames

            for row_num, row in enumerate(reader, start=2):
                try:
                    platform = platform_hint
                    if has_platform_col and row.get("platform", "").strip():
                        platform = row["platform"].strip().lower()

                    target = TrackingTarget(
                        platform=platform,
                        season_id=int(row["season_id"]),
                        title=row.get("title", "").strip(),
                        priority=int(row.get("priority", "1")),
                        interval_minutes=int(row.get("interval_minutes", "60")),
                        paused=self._parse_bool(row.get("paused", "false")),
                        tags=row.get("tags", "").strip(),
                    )
                    targets.append(target)
                except (ValueError, KeyError) as e:
                    logger.warning("第 %d 行解析失败, 跳过: %s", row_num, e)

        return targets

    def validate(self, targets: list[TrackingTarget]) -> list[str]:
        """校验追踪目标列表, 返回警告信息列表"""
        warnings: list[str] = []

        for i, target in enumerate(targets):
            if target.platform not in SUPPORTED_PLATFORMS:
                warnings.append(f"第 {i + 1} 项: 不支持的平台 '{target.platform}', 支持: {SUPPORTED_PLATFORMS}")
            if target.season_id <= 0:
                warnings.append(f"第 {i + 1} 项: season_id 必须为正整数")
            if target.priority not in (0, 1, 2):
                warnings.append(f"第 {i + 1} 项: priority 应为 0/1/2, 当前值: {target.priority}")
            if target.interval_minutes < 1:
                warnings.append(f"第 {i + 1} 项: interval_minutes 必须 >= 1")

        return warnings

    def sync_to_db(self, targets: list[TrackingTarget]) -> tuple[int, int]:
        """将追踪目标同步到 CrawlTask 表

        Returns:
            (新建数量, 更新数量)
        """
        created = 0
        updated = 0

        for target in targets:
            with get_session() as session:
                repo = Repository(session)
                existing = repo.get_crawl_task(target.season_id)

                if existing:
                    existing.priority = target.priority
                    existing.interval_minutes = target.interval_minutes
                    existing.paused = target.paused
                    existing.tags = target.tags
                    if target.title:
                        existing.title = target.title
                    session.flush()
                    updated += 1
                    logger.info("更新任务: season_id=%s title='%s'", target.season_id, target.title)
                else:
                    task = CrawlTask(
                        platform=target.platform,
                        season_id=target.season_id,
                        title=target.title,
                        priority=target.priority,
                        interval_minutes=target.interval_minutes,
                        paused=target.paused,
                        tags=target.tags,
                    )
                    repo.add_crawl_task(task)
                    created += 1
                    logger.info("创建任务: season_id=%s title='%s'", target.season_id, target.title)

        return created, updated

    async def crawl_all(self, targets: list[TrackingTarget] | None = None) -> None:
        """依次抓取追踪目标中未暂停的内容

        Pipeline.crawl_once 内部自动处理:
        - Anime 不存在则创建
        - 已存在则更新字段+追加快照
        """
        from core.orchestrator import Orchestrator

        if targets is None:
            targets = self.load()

        orchestrator = Orchestrator()

        for target in targets:
            if target.paused:
                logger.info("跳过暂停项: season_id=%s title='%s'", target.season_id, target.title)
                continue

            if target.platform != "bilibili":
                logger.warning("暂不支持平台: %s, 跳过", target.platform)
                continue

            try:
                logger.info("执行采集: season_id=%s title='%s'", target.season_id, target.title)
                await orchestrator.crawl_once(target.season_id)
            except Exception as e:
                logger.error("采集失败 season_id=%s: %s", target.season_id, e)

    # ============================================================
    # 目录扫描 + 差异同步(新流程)
    # ============================================================

    @staticmethod
    def scan_dir(dir_path: Path | None = None) -> dict[str, list[TrackingTarget]]:
        """扫描追踪目录, 读取所有 {platform}.csv 文件

        Args:
            dir_path: 追踪目录(默认 config 中的 tracking_dir)

        Returns:
            {platform: [TrackingTarget, ...]} 映射

        Raises:
            FileNotFoundError: 目录不存在或为空
        """
        if dir_path is None:
            dir_path = get_settings().tracking_dir

        if not dir_path.exists():
            raise FileNotFoundError(f"追踪目录不存在: {dir_path}")

        result: dict[str, list[TrackingTarget]] = {}

        for csv_file in sorted(dir_path.glob("*.csv")):
            platform = csv_file.stem  # 文件名(不含扩展名)作为平台名
            if platform not in SUPPORTED_PLATFORMS:
                logger.warning("跳过未注册平台文件: %s", csv_file.name)
                continue

            with open(csv_file, encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                if reader.fieldnames is None:
                    continue

                targets: list[TrackingTarget] = []
                for row_num, row in enumerate(reader, start=2):
                    try:
                        target = TrackingTarget(
                            platform=platform,
                            season_id=int(row["season_id"]),
                            title=row.get("title", "").strip(),
                            priority=int(row.get("priority", "1")),
                            interval_minutes=int(row.get("interval_minutes", "60")),
                            paused=TrackingTable._parse_bool(row.get("paused", "false")),
                            tags=row.get("tags", "").strip(),
                        )
                        targets.append(target)
                    except (ValueError, KeyError) as e:
                        logger.warning("%s 第 %d 行解析失败, 跳过: %s", csv_file.name, row_num, e)

            if targets:
                result[platform] = targets

        return result

    def reconcile(self, csv_targets: dict[str, list[TrackingTarget]]) -> ReconcileDiff:
        """对比 CSV 目标与数据库任务, 计算差异

        Args:
            csv_targets: scan_dir() 返回的平台→目标映射

        Returns:
            ReconcileDiff: 需要创建/暂停/更新的内容
        """
        diff = ReconcileDiff()

        # 构建 CSV 条目集合: {(platform, season_id)}
        csv_set: set[tuple[str, int]] = set()
        for platform, targets in csv_targets.items():
            for t in targets:
                csv_set.add((platform, t.season_id))

        # 在会话内提取 DB 任务数据, 避免 DetachedInstanceError
        db_records: list[dict] = []
        with get_session() as session:
            repo = Repository(session)
            for task in repo.get_all_tasks():
                db_records.append(
                    {
                        "platform": task.platform,
                        "season_id": task.season_id,
                        "title": task.title,
                        "priority": task.priority,
                        "interval_minutes": task.interval_minutes,
                        "paused": task.paused,
                        "tags": task.tags,
                    }
                )

        # 构建 DB 映射: {(platform, season_id): dict}
        db_map: dict[tuple[str, int], dict] = {}
        for rec in db_records:
            db_map[(rec["platform"], rec["season_id"])] = rec

        # CSV 有但 DB 无 → 创建
        for platform, targets in csv_targets.items():
            for t in targets:
                key = (platform, t.season_id)
                if key not in db_map:
                    diff.created.append(t)
                else:
                    existing = db_map[key]
                    if existing["priority"] != t.priority or existing["interval_minutes"] != t.interval_minutes or existing["paused"] != t.paused or existing["tags"] != t.tags or (t.title and existing["title"] != t.title):
                        diff.updated.append(t)

        # DB 有但 CSV 无 → 暂停
        for key, rec in db_map.items():
            if key not in csv_set and not rec["paused"]:
                diff.paused.append((rec["platform"], rec["season_id"]))

        return diff

    def show_diff(self, diff: ReconcileDiff) -> None:
        """打印差异摘要"""
        if not diff.has_changes:
            print("追踪表与数据库一致, 无需变更")
            return

        print("\n同步计划:")
        if diff.created:
            print(f"  [新建] ({len(diff.created)} 项):")
            for t in diff.created:
                print(f"    [{t.platform}] {t.season_id} {t.title}")
        if diff.updated:
            print(f"  [更新] ({len(diff.updated)} 项):")
            for t in diff.updated:
                print(f"    [{t.platform}] {t.season_id} {t.title}")
        if diff.paused:
            print(f"  [暂停] ({len(diff.paused)} 项):")
            for platform, sid in diff.paused:
                print(f"    [{platform}] {sid}")

    def apply_diff(self, diff: ReconcileDiff) -> tuple[int, int, int]:
        """执行差异同步

        Returns:
            (created_count, updated_count, paused_count)
        """
        # 新建
        for t in diff.created:
            with get_session() as session:
                repo = Repository(session)
                task = CrawlTask(
                    platform=t.platform,
                    season_id=t.season_id,
                    title=t.title,
                    priority=t.priority,
                    interval_minutes=t.interval_minutes,
                    paused=t.paused,
                    tags=t.tags,
                )
                repo.add_crawl_task(task)
                logger.info("新建: season_id=%s title='%s'", t.season_id, t.title)

        # 更新
        for t in diff.updated:
            with get_session() as session:
                repo = Repository(session)
                existing = repo.get_crawl_task(t.season_id)
                if existing:
                    existing.priority = t.priority
                    existing.interval_minutes = t.interval_minutes
                    existing.paused = t.paused
                    existing.tags = t.tags
                    if t.title:
                        existing.title = t.title
                    session.flush()
                    logger.info("更新: season_id=%s title='%s'", t.season_id, t.title)

        # 暂停
        for platform, season_id in diff.paused:
            with get_session() as session:
                repo = Repository(session)
                task = repo.get_crawl_task(season_id)
                if task:
                    task.paused = True
                    session.flush()
                    logger.info("暂停: season_id=%s", season_id)

        return len(diff.created), len(diff.updated), len(diff.paused)

    async def sync_all(self, dir_path: Path | None = None, dry_run: bool = False) -> None:
        """完整同步流程: 扫描 → 对比 → 应用 → 采集

        Args:
            dir_path: 追踪目录(默认 config 中的 tracking_dir)
            dry_run: 仅预览不执行
        """
        # 1. 扫描
        try:
            csv_targets = self.scan_dir(dir_path)
        except FileNotFoundError:
            print(f"追踪目录不存在, 请先创建: {get_settings().tracking_dir}")
            return

        if not csv_targets:
            print("追踪目录中没有有效的 CSV 文件")
            return

        total_targets = sum(len(v) for v in csv_targets.values())
        print(f"扫描到 {total_targets} 个追踪目标 ({len(csv_targets)} 个平台)")

        # 2. 校验
        all_targets = [t for targets in csv_targets.values() for t in targets]
        warnings = self.validate(all_targets)
        if warnings:
            print("校验警告:")
            for w in warnings:
                print(f"  ⚠ {w}")

        # 3. 对比
        diff = self.reconcile(csv_targets)

        # 4. 预览
        self.show_diff(diff)

        if dry_run or not diff.has_changes:
            return

        # 5. 应用
        created, updated, paused = self.apply_diff(diff)
        print(f"执行完成: 新建={created}, 更新={updated}, 暂停={paused}")

        # 6. 采集(只采新建的)
        created_targets = diff.created
        if created_targets:
            print(f"开始首次采集 {len(created_targets)} 项...")
            await self.crawl_all(created_targets)

    # ============================================================
    # 工具方法
    # ============================================================

    @staticmethod
    def _parse_bool(value: str) -> bool:
        return value.strip().lower() in ("true", "1", "yes", "是")

    @staticmethod
    def create_template(path: Path) -> None:
        """创建 CSV 模板文件"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["platform", "season_id", "title", "priority", "interval_minutes", "paused", "tags"])
            writer.writerow(["bilibili", "425", "某科学的超电磁炮", "0", "60", "false", ""])
            writer.writerow(["bilibili", "39567", "葬送的芙莉莲", "1", "120", "false", ""])
            writer.writerow(["bilibili", "100332", "进击的巨人 最终季", "2", "1440", "true", "完结"])
