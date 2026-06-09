"""discover 命令: 从B站拉取动画列表, 默认拉取番剧+国创"""

from __future__ import annotations

import asyncio
import logging

from core.discover import fetch_and_save

logger = logging.getLogger(__name__)


def cmd_discover(args) -> None:
    """从B站分类索引拉取动画列表, 写入 data/catalog/bilibili.csv

    用法:
        anime-hub discover           # 拉取番剧+国创(按追番数排序)
        anime-hub discover --type 4  # 仅拉取国创
        anime-hub discover --type 1  # 仅拉取番剧
        anime-hub discover --order 5 # 按评分排序
    """

    season_type = getattr(args, "type", None)
    if season_type is None:
        season_type = (1, 4)

    order = getattr(args, "order", 2) or 2

    output_path = asyncio.run(fetch_and_save(season_type=season_type))

    print(f"\n已生成: {output_path}")

    print("筛选感兴趣的动画后, 将 season_id 加入 data/tracking/bilibili.csv 即可开始采集")
