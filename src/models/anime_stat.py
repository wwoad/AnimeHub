"""动画统计快照模型

每次采集时追加一条快照记录, 不覆盖历史数据。
用于分析播放量/弹幕/点赞/收藏等指标的增量变化。
平台专有指标存入 extra 字段。
"""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class AnimeStat(Base):
    __tablename__ = "anime_stat"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    anime_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True, comment="关联动画season_id")
    views: Mapped[int] = mapped_column(BigInteger, default=0, comment="总播放量")
    follow: Mapped[int] = mapped_column(BigInteger, default=0, comment="追番数")
    danmaku: Mapped[int] = mapped_column(Integer, default=0, comment="弹幕数")
    likes: Mapped[int] = mapped_column(Integer, default=0, comment="点赞数")
    coins: Mapped[int] = mapped_column(Integer, default=0, comment="投币数")
    share: Mapped[int] = mapped_column(Integer, default=0, comment="分享数")
    favorite: Mapped[int] = mapped_column(Integer, default=0, comment="收藏数")
    extra: Mapped[dict | None] = mapped_column(JSON, default=dict, comment="平台专有数据")
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True, comment="采集时间")

    def __repr__(self) -> str:
        return f"<AnimeStat anime_id={self.anime_id} captured_at={self.captured_at}>"