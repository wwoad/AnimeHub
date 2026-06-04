"""采集任务模型

跟踪每个动画的采集状态, 支持:
- 平台标识:区分不同平台的采集任务
- 定时调度:根据 next_crawl_at 决定何时采集
- 优先级控制:在播动画优先采集
- 暂停/恢复:可暂停指定任务的采集
- 失败恢复:自动重试, 指数退避
- 标签分组:按标签筛选和管理任务
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class CrawlTask(Base):
    __tablename__ = "crawl_task"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="bilibili",
        index=True,
        comment="来源平台(bilibili/iqiyi/tencent/youku)",
    )
    season_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True, comment="动画唯一标识(平台ID)")
    title: Mapped[str] = mapped_column(String(200), default="", comment="动画标题(冗余存储)")
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="pending",
        comment="任务状态: pending/running/success/failed",
    )
    priority: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        comment="优先级: 0=在播(最频繁), 1=普通, 2=低频(已完结)",
    )
    interval_minutes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=60,
        comment="采集间隔(分钟)",
    )
    paused: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否暂停采集",
    )
    tags: Mapped[str] = mapped_column(
        String(500),
        default="",
        comment="自定义标签(分号分隔)",
    )
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="上次采集时间")
    next_crawl_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="下次采集时间")
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="连续失败次数")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True, comment="最近一次错误信息")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
        comment="更新时间",
    )

    def __repr__(self) -> str:
        return f"<CrawlTask platform={self.platform} season_id={self.season_id} status='{self.status}'>"
