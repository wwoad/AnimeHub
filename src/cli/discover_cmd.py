"""discover 命令: 从B站拉取国创动画列表"""

from __future__ import annotations

import asyncio
import logging

from core.discover import fetch_and_save

logger = logging.getLogger(__name__)


def cmd_discover(args) -> None:
    """从B站分类索引拉取国创动画列表, 写入 data/anime_info/bilibili.csv



    用法:

        anime-hub discover           # 拉取国创(按追番数排序)

        anime-hub discover --type 1  # 拉取番剧

        anime-hub discover --order 5 # 按评分排序

    """

    season_type = getattr(args, "type", 4) or 4

    order = getattr(args, "order", 2) or 2

    output_path = asyncio.run(fetch_and_save(season_type=season_type, order=order))

    print(f"\n已生成: {output_path}")

    print("筛选感兴趣的动画后, 将 season_id 加入 data/tracking/bilibili.csv 即可开始采集")
