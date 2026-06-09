"""凡人修仙传监控 — 数据查询层

提供数据库查询辅助函数, 供独立 Streamlit 页面使用。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pandas as pd
from pathlib import Path

from config import get_settings
from db.session import get_session, init_db
from models.fanren_monitor import FanrenMonitorLog

SEASON_ID = 28747
TARGET_DATETIME = datetime(2026, 6, 13, 11, 0, 0)

SEASON_INTERVAL_OPTIONS = {"60s": 60, "10min": 600, "30min": 1800}
EPISODE_INTERVAL_OPTIONS = {"10s": 10, "30s": 30, "60s": 60}

DEFAULT_SEASON_INTERVAL = 60
DEFAULT_EPISODE_INTERVAL = 10


def read_monitor_config() -> dict:
    try:
        if get_settings().monitor_config_file.exists():
            return json.loads(get_settings().monitor_config_file.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"season_interval": DEFAULT_SEASON_INTERVAL, "episode_interval": DEFAULT_EPISODE_INTERVAL}


def write_monitor_config(config: dict) -> None:
    try:
        get_settings().monitor_config_file.parent.mkdir(parents=True, exist_ok=True)
        get_settings().monitor_config_file.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _fmt_online(n: int) -> str:
    if n >= 10000:
        return f"{n / 10000:.1f}万人"
    return f"{n}人"


def read_manual_ep() -> str:
    try:
        if get_settings().monitor_ep_file.exists():
            return get_settings().monitor_ep_file.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def write_manual_ep(ep: str) -> None:
    try:
        get_settings().monitor_ep_file.parent.mkdir(parents=True, exist_ok=True)
        get_settings().monitor_ep_file.write_text(ep.strip(), encoding="utf-8")
    except Exception:
        pass


def load_season_logs(minutes_ago: int | None = None, limit: int = 3000) -> pd.DataFrame:
    init_db()
    with get_session() as session:
        q = (
            session.query(FanrenMonitorLog)
            .filter(
                FanrenMonitorLog.log_type == "season",
                FanrenMonitorLog.season_id == SEASON_ID,
            )
        )
        if minutes_ago is not None:
            cutoff = datetime.now() - timedelta(minutes=minutes_ago)
            q = q.filter(FanrenMonitorLog.captured_at >= cutoff)
        rows = q.order_by(FanrenMonitorLog.captured_at.desc()).limit(limit).all()
        if not rows:
            return pd.DataFrame()
        data = [
            {"时间": r.captured_at.strftime("%H:%M:%S"), "在线人数": r.online_count, "_ts": r.captured_at}
            for r in reversed(rows)
        ]
        return pd.DataFrame(data)


def load_ep_logs(ep_title: str, minutes_ago: int | None = None, limit: int = 3000) -> pd.DataFrame:
    init_db()
    with get_session() as session:
        q = (
            session.query(FanrenMonitorLog)
            .filter(
                FanrenMonitorLog.log_type == "episode",
                FanrenMonitorLog.season_id == SEASON_ID,
                FanrenMonitorLog.ep_title == ep_title,
            )
        )
        if minutes_ago is not None:
            cutoff = datetime.now() - timedelta(minutes=minutes_ago)
            q = q.filter(FanrenMonitorLog.captured_at >= cutoff)
        rows = q.order_by(FanrenMonitorLog.captured_at.desc()).limit(limit).all()
        if not rows:
            return pd.DataFrame()
        data = [
            {"时间": r.captured_at.strftime("%H:%M:%S"), "在线人数": r.online_count, "_ts": r.captured_at}
            for r in reversed(rows)
        ]
        return pd.DataFrame(data)


def get_latest_season_online() -> tuple[int, str]:
    init_db()
    with get_session() as session:
        row = (
            session.query(FanrenMonitorLog)
            .filter(FanrenMonitorLog.log_type == "season", FanrenMonitorLog.season_id == SEASON_ID)
            .order_by(FanrenMonitorLog.captured_at.desc())
            .first()
        )
        if row:
            return row.online_count, row.captured_at.strftime("%H:%M:%S")
        return 0, ""


def get_latest_ep_online(ep_title: str) -> tuple[int, str]:
    init_db()
    with get_session() as session:
        row = (
            session.query(FanrenMonitorLog)
            .filter(FanrenMonitorLog.log_type == "episode", FanrenMonitorLog.season_id == SEASON_ID,
                    FanrenMonitorLog.ep_title == ep_title)
            .order_by(FanrenMonitorLog.captured_at.desc())
            .first()
        )
        if row:
            return row.online_count, row.captured_at.strftime("%H:%M:%S")
        return 0, ""


def get_available_ep_titles() -> list[str]:
    init_db()
    with get_session() as session:
        from sqlalchemy import distinct

        from models.episode import Episode

        rows = (
            session.query(distinct(Episode.title))
            .filter(Episode.anime_id == SEASON_ID, Episode.episode_type == "main")
            .all()
        )
        titles = []
        for (t,) in rows:
            try:
                int(t.strip())
                titles.append(t.strip())
            except (ValueError, AttributeError):
                pass
        titles.sort(key=lambda x: int(x))
        return titles


def get_recent_logs(limit: int = 100) -> list[dict]:
    init_db()
    with get_session() as session:
        rows = (
            session.query(FanrenMonitorLog)
            .filter(FanrenMonitorLog.season_id == SEASON_ID)
            .order_by(FanrenMonitorLog.captured_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "时间": r.captured_at.strftime("%m-%d %H:%M:%S"),
                "类型": "总在线" if r.log_type == "season" else f"第{r.ep_title}集",
                "在线人数": r.online_count,
            }
            for r in rows
        ]


def get_season_records(limit: int = 100) -> list[dict]:
    init_db()
    with get_session() as session:
        rows = (
            session.query(FanrenMonitorLog)
            .filter(FanrenMonitorLog.log_type == "season", FanrenMonitorLog.season_id == SEASON_ID)
            .order_by(FanrenMonitorLog.captured_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {"时间": r.captured_at.strftime("%m-%d %H:%M:%S"), "在线人数": r.online_count}
            for r in rows
        ]


def get_episode_records(ep_title: str, limit: int = 100) -> list[dict]:
    init_db()
    with get_session() as session:
        rows = (
            session.query(FanrenMonitorLog)
            .filter(
                FanrenMonitorLog.log_type == "episode",
                FanrenMonitorLog.season_id == SEASON_ID,
                FanrenMonitorLog.ep_title == ep_title,
            )
            .order_by(FanrenMonitorLog.captured_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {"时间": r.captured_at.strftime("%m-%d %H:%M:%S"), "在线人数": r.online_count}
            for r in rows
        ]
