"""数据库会话管理

提供 SQLAlchemy 引擎、会话工厂和初始化功能。
使用全局单例模式避免重复创建连接。
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from config import get_settings
from models.base import Base

_engine = None
_SessionFactory = None


def get_engine():
    """获取全局数据库引擎单例"""
    global _engine
    if _engine is None:
        settings = get_settings()
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(f"sqlite:///{settings.db_path}", echo=False, connect_args={"timeout": 30})
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    """获取全局会话工厂单例"""
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine())
    return _SessionFactory


def init_db() -> None:
    """初始化数据库, 创建所有尚未创建的表"""
    engine = get_engine()
    Base.metadata.create_all(engine)


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
