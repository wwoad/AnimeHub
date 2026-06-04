"""单集统计快照模型

每次采集为每一集生成一条快照, 追加模式不覆盖历史。
通过 episode_id 索引加速查询某一集的所有历史快照。
平台专有指标(如B站投币数、排名)存入 extra 字段。
"""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class EpisodeStat(Base):
    __tablename__ = "episode_stat"
    __table_args__ = {"extend_existing": True}

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    episode_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True, comment="关联单集ep_id")
    views: Mapped[int] = mapped_column(BigInteger, default=0, comment="播放量")
    danmaku: Mapped[int] = mapped_column(Integer, default=0, comment="弹幕数")
    reply: Mapped[int] = mapped_column(Integer, default=0, comment="评论数")
    favorite: Mapped[int] = mapped_column(Integer, default=0, comment="收藏数")
    likes: Mapped[int] = mapped_column(Integer, default=0, comment="点赞数")
    coins: Mapped[int] = mapped_column(Integer, default=0, comment="投币数")
    share: Mapped[int] = mapped_column(Integer, default=0, comment="分享数")
    extra: Mapped[dict | None] = mapped_column(JSON, default=dict, comment="平台专有数据(如排名)")
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True, comment="快照时间戳")

    def __repr__(self) -> str:
        return f"<EpisodeStat ep_id={self.episode_id} captured_at={self.captured_at}>"
