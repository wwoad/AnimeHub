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

from config import get_settings


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
    return get_settings().archive_dir / (date_str or get_date_str()) / platform / title_slug


def get_raw_path(
    title_slug: str,
    platform: str = "bilibili",
    date_str: str | None = None,
) -> Path:
    """获取原始数据路径: data/archive/{date}/{platform}/{title_slug}.xlsx"""
    return get_settings().archive_dir / (date_str or get_date_str()) / platform / f"{title_slug}.xlsx"


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


def raw_file_exists_today(title: str, platform: str = "bilibili") -> bool:
    """检查今天的原始数据Excel文件是否已存在

    用于重复爬取检测:如果今天已有导出的Excel文件,
    说明该动画今天已经成功采集过,可跳过重复爬取。
    """
    if not title:
        return False
    title_slug = sanitize_title(title)
    raw_path = get_raw_path(title_slug, platform)
    return raw_path.exists()
