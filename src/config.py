"""全局配置管理

使用 Pydantic Settings 管理所有可配置项, 支持环境变量覆盖。
环境变量前缀: ANIME_
例如: ANIME_DB_PATH=./data/anime.db
"""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # === 数据库配置 ===
    db_path: Path = _PROJECT_ROOT / "data" / "anime.db"

    # === 追踪表配置 ===
    tracking_file: Path = _PROJECT_ROOT / "data" / "tracking" / "bilibili.csv"
    tracking_dir: Path = _PROJECT_ROOT / "data" / "tracking"

    # === HTTP 客户端配置 ===
    request_delay: float = 0.15
    max_concurrency: int = 10
    max_connections: int = 20
    request_timeout: float = 15.0

    # === 重试配置 ===
    max_retries: int = 3
    retry_wait: float = 2.0

    # === 采集调度配置 ===
    interval_airing_minutes: int = 60
    interval_finished_hours: int = 24
    schedule_check_seconds: int = 60

    # === 日志配置 ===
    log_level: str = "INFO"
    log_dir: Path = _PROJECT_ROOT / "log"
    log_keep_days: int = 7  # 硬编码默认值，可通过 ANIME_LOG_KEEP_DAYS 覆盖

    # === 进度条配置 ===
    show_progress_bar: bool = True  # 硬编码默认值，可通过 ANIME_SHOW_PROGRESS_BAR 覆盖

    # === B站配置(Cookie 从环境变量 ANIME_BILIBILI_COOKIE 读取) ===
    bilibili_cookie: str = ""

    model_config = {"env_prefix": "ANIME_"}


_settings: Settings | None = None


def get_settings() -> Settings:
    """获取全局配置单例"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
