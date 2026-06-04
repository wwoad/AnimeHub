"""API 客户端抽象基类

定义数据采集客户端的通用接口契约。
不同平台的客户端实现此基类, 提供平台特有的数据采集能力。
各平台 Ping 类型由各自的 Client 子类定义, Pipeline 按平台分发处理。

扩展新平台时:
1. 在 client/ 下创建子目录(如 client/iqiyi/)
2. 实现具体的 Client 类, 继承 BaseClient
3. 在 ClientFactory 中注册
4. 在 Pipeline 中添加该平台的 transform 方法
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseClient(ABC):
    """API 客户端抽象基类

    所有平台客户端必须实现以下接口方法。
    各方法返回 dict, 具体字段由各平台的 Pydantic 模型定义。
    """

    @abstractmethod
    async def search(self, keyword: str) -> list[Any]:
        """搜索内容

        Args:
            keyword: 搜索关键词

        Returns:
            搜索结果列表, 每个元素为平台特定的数据对象
        """

    @abstractmethod
    async def get_season_info(self, season_id: int) -> Any | None:
        """获取内容详情

        Args:
            season_id: 内容ID

        Returns:
            详情对象, 获取失败返回 None
        """

    @abstractmethod
    async def get_season_stat(self, season_id: int) -> Any | None:
        """获取内容级统计快照

        Args:
            season_id: 内容ID

        Returns:
            统计对象, 获取失败返回 None
        """

    @abstractmethod
    async def get_video_stat(self, aid: int | None = None, bvid: str | None = None) -> Any | None:
        """获取单集视频统计数据

        Args:
            aid: AV号
            bvid: BV号

        Returns:
            视频统计对象, 获取失败返回 None
        """

    @abstractmethod
    async def get_video_stats_batch(self, aids: list[int]) -> list[Any | None]:
        """批量获取多个视频的统计数据

        Args:
            aids: AV号列表

        Returns:
            统计对象列表, 失败的项为 None
        """

    @abstractmethod
    async def close(self) -> None:
        """关闭客户端连接池"""

    @abstractmethod
    async def __aenter__(self) -> BaseClient: ...

    @abstractmethod
    async def __aexit__(self, *args: Any) -> None: ...
