"""ORM 基类与通用混入

提供 SQLAlchemy 声明式基类和时间戳混入, 供所有模型继承使用。
"""

from datetime import datetime

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类, 所有模型的元数据容器"""


class TimestampMixin:
    """时间戳混入, 为模型提供 created_at / updated_at 自动维护"""

    created_at: datetime
    updated_at: datetime
