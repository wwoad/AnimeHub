"""单集主表模型

存储每一集的基础信息, ep_id 作为平台内唯一标识。
extra 存储各平台专有数据(如B站的aid/bvid/cid/徽章/分类等)。
"""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class Episode(TimestampMixin, Base):
    __tablename__ = "episode"
    __table_args__ = {"extend_existing": True}

    ep_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="单集唯一标识")
    anime_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True, comment="所属动画season_id")
    title: Mapped[str] = mapped_column(String(100), default="", comment="集号")
    long_title: Mapped[str] = mapped_column(String(200), default="", comment="副标题")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0, comment="时长(毫秒)")
    episode_type: Mapped[str] = mapped_column(String(20), default="main", index=True, comment="集类型: main/trailer/special/misc")
    pub_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="发布时间")
    extra: Mapped[dict | None] = mapped_column(JSON, default=dict, comment="平台专有数据(徽章/分类/av号等)")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间",
    )

    def __repr__(self) -> str:
        return f"<Episode ep_id={self.ep_id} title='{self.title}' long_title='{self.long_title}'>"