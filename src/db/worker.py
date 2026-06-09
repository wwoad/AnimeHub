"""多 DB 并行写入

采集前将主库复制为 N 个 worker, 每个 worker 绑定一个 slot。
采集完成后用 ATTACH + INSERT 合并回主库, 彻底消除 SQLite 写锁竞争。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from contextlib import contextmanager, suppress
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)


class WorkerPool:
    """多 DB 并行写入池

    Usage::

        pool = WorkerPool(n=3, db_path="data/anime.db")
        pool.setup()

        async with pool.acquire() as slot:
            with pool.get_session(slot) as session:
                repo = Repository(session)
                pipeline = Pipeline(repo, client)
                await pipeline.crawl_once(sid, platform)

        pool.merge()
        pool.cleanup()
    """

    def __init__(self, n_workers: int, db_path: Path) -> None:
        self.n_workers = n_workers
        self.main_db = db_path.resolve()
        self.workers_dir = self.main_db.parent / "workers"
        self._slots: asyncio.Queue[int] = asyncio.Queue()
        self._engines: dict[int, object] = {}
        self._session_factories: dict[int, object] = {}
        self._max_anime_stat_id = 0
        self._max_episode_stat_id = 0

    def setup(self) -> None:
        """复制主库到每个 worker, 初始化 slot 队列"""

        self.workers_dir.mkdir(parents=True, exist_ok=True)

        # 清旧 worker 文件（跳过被其他进程锁定的）
        for p in self.workers_dir.glob("worker_*.db*"):
            with suppress(OSError):
                p.unlink()

        # WAL checkpoint: 确保所有已提交数据写入 .db 文件, worker 复制才完整
        main_engine = create_engine(f"sqlite:///{self.main_db}", echo=False, connect_args={"timeout": 30})
        with main_engine.connect() as conn:
            conn.execute(text("PRAGMA wal_checkpoint(TRUNCATE)"))
            self._max_anime_stat_id = conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM anime_stat")).scalar() or 0
            self._max_episode_stat_id = conn.execute(text("SELECT COALESCE(MAX(id), 0) FROM episode_stat")).scalar() or 0
        main_engine.dispose()
        logger.info("WorkerPool: WAL checkpoint done, max_id: anime_stat=%d episode_stat=%d",
                     self._max_anime_stat_id, self._max_episode_stat_id)

        for i in range(self.n_workers):
            worker_path = self.workers_dir / f"worker_{i}.db"
            shutil.copy2(self.main_db, worker_path)
            self._slots.put_nowait(i)

        logger.info("WorkerPool: 已复制 %d 个 worker", self.n_workers)

    def _ensure_engine(self, idx: int) -> object:
        if idx not in self._engines:
            path = self.workers_dir / f"worker_{idx}.db"
            engine = create_engine(f"sqlite:///{path}", echo=False, connect_args={"timeout": 30})

            @event.listens_for(engine, "connect")
            def _setup(dbapi_conn, _rec):
                c = dbapi_conn.cursor()
                c.execute("PRAGMA journal_mode=WAL")
                c.execute("PRAGMA synchronous=NORMAL")
                c.close()

            self._engines[idx] = engine
            self._session_factories[idx] = sessionmaker(bind=engine)

        return self._engines[idx]

    @contextmanager
    def get_session(self, idx: int):
        """获取该 worker 的 DB 会话"""
        self._ensure_engine(idx)
        session = self._session_factories[idx]()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def worker_path(self, idx: int) -> Path:
        return self.workers_dir / f"worker_{idx}.db"

    async def acquire(self) -> int:
        """获取一个可用 slot, 阻塞直到有可用"""
        return await self._slots.get()

    def release(self, idx: int) -> None:
        """归还一个 slot"""
        self._slots.put_nowait(idx)

    def merge(self) -> None:
        """将所有 worker DB 的数据合并回主库"""
        main_engine = create_engine(
            f"sqlite:///{self.main_db}", echo=False, connect_args={"timeout": 60}
        )

        with main_engine.connect() as conn:
            conn.execute(text("PRAGMA journal_mode=WAL"))

            for i in range(self.n_workers):
                worker = str(self.worker_path(i))
                if not Path(worker).exists():
                    logger.warning("Worker %d 文件不存在, 跳过合并", i)
                    continue

                try:
                    conn.execute(text(f"ATTACH DATABASE '{worker}' AS w{i}"))

                    _merge_anime(conn, i)
                    _merge_anime_stat(conn, i, self._max_anime_stat_id)
                    _merge_episode(conn, i)
                    _merge_episode_stat(conn, i, self._max_episode_stat_id)
                    _merge_crawl_task(conn, i)

                    conn.execute(text(f"DETACH DATABASE w{i}"))
                    logger.info("Worker %d 合并完成", i)
                except Exception:
                    logger.exception("Worker %d 合并失败", i)
                    with suppress(Exception):
                        conn.execute(text(f"DETACH DATABASE w{i}"))

            conn.commit()

    def cleanup(self) -> None:
        """删除所有 worker DB 文件"""
        for p in self.workers_dir.glob("worker_*.db*"):
            with suppress(OSError):
                p.unlink()
        with suppress(OSError):
            self.workers_dir.rmdir()


def _merge_anime(conn, alias_idx: int) -> None:
    conn.execute(text(f"""INSERT OR REPLACE INTO main.anime SELECT * FROM w{alias_idx}.anime"""))


def _merge_anime_stat(conn, alias_idx: int, max_id: int) -> None:
    conn.execute(text(f"""
        INSERT INTO main.anime_stat
        (anime_id, views, follow, danmaku, likes, coins, share, favorite, extra, captured_at)
        SELECT anime_id, views, follow, danmaku, likes, coins, share, favorite, extra, captured_at
        FROM w{alias_idx}.anime_stat
        WHERE id > {max_id}
    """))


def _merge_episode(conn, alias_idx: int) -> None:
    conn.execute(text(f"""INSERT OR REPLACE INTO main.episode SELECT * FROM w{alias_idx}.episode"""))


def _merge_episode_stat(conn, alias_idx: int, max_id: int) -> None:
    conn.execute(text(f"""
        INSERT INTO main.episode_stat
        (episode_id, views, danmaku, reply, favorite, likes, coins, share, extra, captured_at)
        SELECT episode_id, views, danmaku, reply, favorite, likes, coins, share, extra, captured_at
        FROM w{alias_idx}.episode_stat
        WHERE id > {max_id}
    """))


def _merge_crawl_task(conn, alias_idx: int) -> None:
    conn.execute(text(f"""
        UPDATE main.crawl_task SET
            last_crawled_at = (SELECT last_crawled_at FROM w{alias_idx}.crawl_task w WHERE w.season_id = main.crawl_task.season_id),
            next_crawl_at = (SELECT next_crawl_at FROM w{alias_idx}.crawl_task w WHERE w.season_id = main.crawl_task.season_id),
            status = (SELECT status FROM w{alias_idx}.crawl_task w WHERE w.season_id = main.crawl_task.season_id),
            last_error = (SELECT last_error FROM w{alias_idx}.crawl_task w WHERE w.season_id = main.crawl_task.season_id),
            fail_count = (SELECT fail_count FROM w{alias_idx}.crawl_task w WHERE w.season_id = main.crawl_task.season_id),
            interval_minutes = (SELECT interval_minutes FROM w{alias_idx}.crawl_task w WHERE w.season_id = main.crawl_task.season_id)
        WHERE EXISTS (SELECT 1 FROM w{alias_idx}.crawl_task w WHERE w.season_id = main.crawl_task.season_id)
    """))
