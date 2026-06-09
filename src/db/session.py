"""数据库会话管理

提供 SQLAlchemy 引擎、会话工厂和初始化功能。
使用全局单例模式避免重复创建连接。
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from config import get_settings
from models.base import Base


@lru_cache(maxsize=1)
def get_engine():
    """获取全局数据库引擎单例（缓存于函数对象，模块重载时自动重建）"""
    settings = get_settings()
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{settings.db_path}", echo=False, connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _set_wal(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return engine


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """获取全局会话工厂单例（缓存于函数对象，模块重载时自动重建）"""
    return sessionmaker(bind=get_engine())


def init_db() -> None:
    """初始化数据库, 创建所有尚未创建的表"""
    from models.fanren_monitor import FanrenMonitorLog  # noqa: F401 确保新表注册到Base

    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate_db(engine)


def _migrate_db(engine) -> None:
    """手动执行数据库迁移（添加新列等）"""
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(anime_stat)"))
        columns = {row[1] for row in result.fetchall()}
        if "reply" not in columns:
            conn.execute(text("ALTER TABLE anime_stat ADD COLUMN reply BIGINT DEFAULT 0"))
            conn.commit()

        result = conn.execute(text("PRAGMA table_info(episode)"))
        ep_cols = {row[1] for row in result.fetchall()}
        if "episode_type" not in ep_cols:
            conn.execute(text("ALTER TABLE episode ADD COLUMN episode_type VARCHAR(20) DEFAULT 'main'"))
            conn.execute(text("UPDATE episode SET episode_type = COALESCE(json_extract(extra, '$.episode_type'), 'main')"))
            conn.execute(text("CREATE INDEX IF NOT EXISTS idx_episode_type ON episode(episode_type)"))
            conn.commit()


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """获取数据库会话的上下文管理器

    自动处理 commit / rollback / close, 确保事务安全。
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
