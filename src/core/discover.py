"""发现动画: 从B站分类索引拉取动画列表, 生成CSV供筛选"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

from client.factory import ClientFactory

logger = logging.getLogger(__name__)

OUTPUT_DIR = Path("data") / "anime_info"

CSV_COLUMNS = ["season_id", "title", "score", "eps", "area", "status"]


async def fetch_and_save(season_type: int = 4, order: int = 2, output_file: str = "bilibili.csv") -> Path:
    """从B站分类索引拉取动画列表, 写入CSV

    Args:
        season_type: 分类类型 (1=番剧, 4=国创)
        order: 排序方式 (2=追番数, 3=播放量, 5=评分)
        output_file: 输出文件名
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    async with ClientFactory.create("bilibili") as client:
        results = await client.browse_seasons(season_type=season_type, order=order)

    if not results:
        logger.warning("未获取到任何动画数据")
        return OUTPUT_DIR / output_file

    type_name = "国创" if season_type == 4 else f"类型{season_type}"
    logger.info("获取到 %d 部%s动画, 写入CSV", len(results), type_name)

    output_path = OUTPUT_DIR / output_file
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in results:
            writer.writerow(
                {
                    "season_id": r.season_id,
                    "title": r.title,
                    "score": r.score,
                    "eps": r.ep_size if r.ep_size else _parse_eps(r.index_show),
                    "area": r.area or "中国",
                    "status": r.index_show,
                }
            )

    logger.info("已写入 %s (%d 条)", output_path, len(results))
    return output_path


def _parse_eps(index_show: str) -> int:
    match = re.search(r"(?:全(\d+)|更新至第(\d+))", index_show)
    if match:
        return int(match.group(1) or match.group(2))
    return 0
