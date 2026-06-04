"""动画主表模型

存储动画的基础信息, season_id 作为平台内唯一标识。
platform 字段标识来源平台, extra 存储各平台专有数据。
所有模型字段均为跨平台通用字段。
"""

from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin


class Anime(TimestampMixin, Base):
    __tablename__ = "anime"
    __table_args__ = {"extend_existing": True}

    season_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, comment="动画唯一标识(平台ID)")
    platform: Mapped[str] = mapped_column(String(20), nullable=False, default="bilibili", comment="来源平台")
    title: Mapped[str] = mapped_column(String(200), nullable=False, comment="标题")
    cover: Mapped[str] = mapped_column(Text, default="", comment="封面URL")
    area: Mapped[str] = mapped_column(String(100), default="", comment="地区")
    styles: Mapped[str] = mapped_column(Text, default="", comment="风格标签(逗号分隔)")
    rating_score: Mapped[float] = mapped_column(default=0.0, comment="评分")
    rating_count: Mapped[int] = mapped_column(Integer, default=0, comment="评分人数")
    is_finish: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否完结")
    total_episodes: Mapped[int] = mapped_column(Integer, default=0, comment="总集数")
    pub_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, comment="开播时间")
    subtitle: Mapped[str] = mapped_column(String(500), default="", comment="副标题")
    season_type: Mapped[int] = mapped_column(Integer, default=0, comment="类型(1=番剧,4=国创等)")
    evaluate: Mapped[str] = mapped_column(Text, default="", comment="简介")
    up_name: Mapped[str] = mapped_column(String(100), default="", comment="创作者")
    extra: Mapped[dict | None] = mapped_column(JSON, default=dict, comment="平台专有数据(分享链接/最新集等)")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, onupdate=datetime.now, comment="更新时间",
    )

    def __repr__(self) -> str:
        return f"<Anime platform={self.platform} season_id={self.season_id} title='{self.title}'>"