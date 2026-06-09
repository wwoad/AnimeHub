"""发现动画: 从B站分类索引拉取动画列表, 生成CSV供筛选"""

from __future__ import annotations

import csv
import logging
import re
from pathlib import Path

from client.factory import ClientFactory
from config import get_settings

logger = logging.getLogger(__name__)

CSV_COLUMNS = ["season_id", "title", "score", "eps", "area", "status", "season_type"]

_ML_RE = re.compile(r"(?:日语|粤配|粤语|英语|英文|国语|中配|日配|国配|原声)版?$")


def _infer_area(title: str, season_type: int) -> str:
    """根据标题和来源类型推断地区: 中国 / 海外"""
    if season_type == 4:
        return "中国"

    if _ML_RE.search(title):
        if any(kw in title for kw in ("日配", "日语")):
            return "中国"
    return "海外"


async def fetch_and_save(season_type: int | tuple[int, ...] = (1, 4), output_file: str = "bilibili.csv") -> Path:
    """从B站分类索引拉取动画列表, 写入CSV
    每种类型按追番数/播放量/评分三轮拉取后合并去重, 最大覆盖。

    Args:
        season_type: 分类类型, 单个int或tuple (1=番剧, 4=国创), 默认(1,4)拉全部
        output_file: 输出文件名
    """
    if isinstance(season_type, int):
        season_types = (season_type,)
    else:
        season_types = season_type

    catalog_dir = get_settings().catalog_dir
    catalog_dir.mkdir(parents=True, exist_ok=True)

    all_results = []
    async with ClientFactory.create("bilibili") as client:
        for st in season_types:
            type_name = "国创" if st == 4 else "番剧" if st == 1 else f"类型{st}"
            seen_in_type: set[int] = set()

            for o in (2, 3, 5):
                results = await client.browse_seasons(season_type=st, order=o)
                added = 0
                for r in results:
                    if r.season_id not in seen_in_type:
                        seen_in_type.add(r.season_id)
                        r.season_type_name = type_name
                        r.area = _infer_area(r.title, st)
                        all_results.append(r)
                        added += 1
                logger.info("  order=%d 新增 %d 条 (累计 %d)", o, added, len(seen_in_type))

            logger.info("%s: 合并三轮后共 %d 部", type_name, len(seen_in_type))

    if not all_results:
        logger.warning("未获取到任何动画数据")
        return catalog_dir / output_file

    output_path = catalog_dir / output_file
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in all_results:
            writer.writerow(
                {
                    "season_id": r.season_id,
                    "title": r.title,
                    "score": r.score,
                    "eps": r.ep_size if r.ep_size else _parse_eps(r.index_show),
                    "area": r.area,
                    "status": r.index_show,
                    "season_type": r.season_type_name,
                }
            )

    logger.info("已写入 %s (%d 条)", output_path, len(all_results))
    return output_path


def _parse_eps(index_show: str) -> int:
    match = re.search(r"(?:全(\d+)|更新至第(\d+))", index_show)
    if match:
        return int(match.group(1) or match.group(2))
    return 0
