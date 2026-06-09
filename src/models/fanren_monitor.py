"""凡人修仙传监控数据模型

存储高频采样的在线人数数据, 与 AnimeStat/EpisodeStat 独立。
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class FanrenMonitorLog(Base):
    __tablename__ = "fanren_monitor_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    log_type: Mapped[str] = mapped_column(String(20), default="season", index=True)
    season_id: Mapped[int] = mapped_column(BigInteger, default=28747)
    ep_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    ep_title: Mapped[str | None] = mapped_column(String(20), nullable=True)
    online_count: Mapped[int] = mapped_column(Integer, default=0)
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, index=True)

    def __repr__(self) -> str:
        return f"<FanrenMonitorLog type={self.log_type} online={self.online_count} at={self.captured_at}>"
