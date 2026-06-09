"""数据模型包

导入所有模型类, 确保 Base.metadata.create_all() 能发现所有表。
"""

from .anime import Anime
from .anime_stat import AnimeStat
from .base import Base
from .crawl_task import CrawlTask
from .episode import Episode
from .episode_stat import EpisodeStat
from .fanren_monitor import FanrenMonitorLog

__all__ = [
    "Base",
    "Anime",
    "AnimeStat",
    "Episode",
    "EpisodeStat",
    "CrawlTask",
    "FanrenMonitorLog",
]
