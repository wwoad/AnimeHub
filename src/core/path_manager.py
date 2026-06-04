"""路径管理

定义数据输出的目录结构:
  data/archive/{YYYYMMDD}/{platform}/{title_slug}/
    原始数据.xlsx ←  crawl 时写入的原始 API 数据(3个Sheet)
    分析/         ←  export 生成的分析产物
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

DATA_DIR = Path("data/archive")


def sanitize_title(title: str) -> str:
    """将动画标题转为安全的文件夹名"""
    # 替换非法文件名字符为下划线
    safe = re.sub(r'[\\/:*?"<>|]', "_", title)
    # 去除首尾空白
    safe = safe.strip()
    # 限制长度
    return safe[:80]


def get_date_str(dt: datetime | None = None) -> str:
    """获取当前日期字符串 YYYYMMDD"""
    return (dt or datetime.now()).strftime("%Y%m%d")


def get_anime_dir(
    title_slug: str,
    platform: str = "bilibili",
    date_str: str | None = None,
) -> Path:
    """获取动画目录: data/archive/{date}/{platform}/{title_slug}/"""
    return DATA_DIR / (date_str or get_date_str()) / platform / title_slug


def get_raw_path(
    title_slug: str,
    platform: str = "bilibili",
    date_str: str | None = None,
) -> Path:
    """获取原始数据 Excel 路径: .../{title_slug}/{title_slug}.xlsx"""
    return get_anime_dir(title_slug, platform, date_str) / f"{title_slug}.xlsx"


def get_analysis_dir(
    title_slug: str,
    platform: str = "bilibili",
    date_str: str | None = None,
) -> Path:
    """获取分析产物目录: .../{title_slug}/分析/"""
    return get_anime_dir(title_slug, platform, date_str) / "分析"


def ensure_dir(path: Path) -> Path:
    """确保目录存在, 不存在则创建"""
    path.mkdir(parents=True, exist_ok=True)
    return path
