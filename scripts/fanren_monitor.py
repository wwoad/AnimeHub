"""凡人修仙传实时在线监控 — 后台采集器

独立进程运行, 持续采集在线人数写入数据库。
Streamlit 面板从数据库读取展示。

单集监控逻辑:
  - 读取 data/monitor/ep.txt 获取手动指定的集号
  - 如未手动指定, 则等待 6月13日11:00 自动切换到第177集
  - 到达目标时间后自动覆盖手动选择, 锁定177集

使用方式:
    python scripts/fanren_monitor.py

Ctrl+C 优雅退出。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import signal
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from client.factory import ClientFactory  # noqa: E402
from config import get_settings  # noqa: E402
from db.session import get_session, init_db  # noqa: E402
from models.fanren_monitor import FanrenMonitorLog  # noqa: E402

SEASON_ID = 28747
EP177_TARGET_DATETIME = datetime(2026, 6, 13, 11, 0, 0)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("fanren_monitor")

_running = True

_all_main_ep_refs: list[dict] = []

_current_ep_title: str | None = None
_current_ep_aid: int = 0
_current_ep_cid: int = 0
_current_ep_bvid: str = ""
_current_ep_is_auto_177: bool = False


def _read_manual_ep() -> str | None:
    """读取手动指定的监控集号"""
    try:
        if get_settings().monitor_ep_file.exists():
            content = get_settings().monitor_ep_file.read_text(encoding="utf-8").strip()
            if content and content.isdigit():
                return content
    except Exception:
        pass
    return None


def _is_main_episode(ep: dict) -> bool:
    title = str(ep.get("title") or "")
    if "预告" in title:
        return False
    duration = ep.get("duration", 0)
    return not (duration and duration < 600000)


def _extract_ep_data(eps: list[dict]) -> list[dict]:
    result = []
    now_ts = datetime.now().timestamp()
    for ep in eps:
        pub_time = ep.get("pub_time", 0)
        if pub_time > now_ts:
            continue
        if not _is_main_episode(ep):
            continue
        result.append({
            "ep_id": ep.get("id", 0),
            "aid": ep.get("aid", 0),
            "cid": ep.get("cid", 0),
            "bvid": ep.get("bvid", ""),
            "title": str(ep.get("title", "")),
        })
    return result


def _find_ep_by_title(eps_data: list[dict], title: str) -> dict | None:
    for ep in eps_data:
        if ep["title"] == title:
            return ep
    return None


def _save_log(log_type: str, online_count: int, ep_id: int | None = None, ep_title: str | None = None) -> None:
    try:
        with get_session() as session:
            log_entry = FanrenMonitorLog(
                log_type=log_type,
                season_id=SEASON_ID,
                ep_id=ep_id,
                ep_title=ep_title,
                online_count=online_count,
                captured_at=datetime.now(),
            )
            session.add(log_entry)
            session.flush()
    except Exception as e:
        logger.error("写入日志失败: %s", e)


def _read_monitor_config() -> dict:
    try:
        if get_settings().monitor_config_file.exists():
            return json.loads(get_settings().monitor_config_file.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"season_interval": 60, "episode_interval": 10}


async def _get_season_eps(client) -> list[dict]:
    season_info = await client.get_season_info(SEASON_ID)
    if season_info:
        return _extract_ep_data(season_info.episodes or [])
    return []


async def _determine_monitor_target(client) -> None:
    """决定单集监控目标: 手动指定 > 自动177 > 无"""
    global _current_ep_title, _current_ep_aid, _current_ep_cid, _current_ep_bvid
    global _current_ep_is_auto_177, _all_main_ep_refs

    now = datetime.now()
    eps_data = await _get_season_eps(client)
    if eps_data:
        _all_main_ep_refs = eps_data

    manual_ep = _read_manual_ep()

    if now >= EP177_TARGET_DATETIME:
        ep177 = _find_ep_by_title(eps_data, "177")
        if ep177:
            if not _current_ep_is_auto_177 or _current_ep_title != "177":
                _current_ep_title = "177"
                _current_ep_aid = ep177["aid"]
                _current_ep_cid = ep177["cid"]
                _current_ep_bvid = ep177["bvid"]
                _current_ep_is_auto_177 = True
                logger.info("[单集] 已到6月13日11:00, 自动切换到第177集")
            return

    if manual_ep:
        target = _find_ep_by_title(eps_data, manual_ep)
        if target:
            if _current_ep_title != manual_ep or _current_ep_aid != target["aid"]:
                _current_ep_title = manual_ep
                _current_ep_aid = target["aid"]
                _current_ep_cid = target["cid"]
                _current_ep_bvid = target["bvid"]
                _current_ep_is_auto_177 = False
                logger.info("[单集] 手动切换到第%s集 aid=%s cid=%s", manual_ep, target["aid"], target["cid"])
        else:
            remaining = (EP177_TARGET_DATETIME - now).total_seconds()
            if remaining > 0:
                h = int(remaining // 3600)
                m = int((remaining % 3600) // 60)
                logger.info("[单集] 第%s集未找到 (距离177集目标 %d时%d分)", manual_ep, h, m)
    elif _current_ep_title:
        _current_ep_title = None
        _current_ep_aid = 0
        _current_ep_cid = 0
        _current_ep_bvid = ""
        _current_ep_is_auto_177 = False
        logger.info("[单集] 清除手动目标, 回到自动等待模式")


async def monitor_season_online(client) -> None:
    """协程A: 每60秒采集总在线人数"""
    global _all_main_ep_refs, _running

    if not _all_main_ep_refs:
        _all_main_ep_refs = await _get_season_eps(client)
        logger.info("总在线监控: 已加载 %d 集正片", len(_all_main_ep_refs))

    while _running:
        try:
            if not _all_main_ep_refs:
                _all_main_ep_refs = await _get_season_eps(client)

            t0 = datetime.now()
            online_counts = await client.get_online_counts_batch(_all_main_ep_refs)
            total_online = sum(online_counts)
            elapsed = (datetime.now() - t0).total_seconds()

            _save_log("season", total_online)
            logger.info("[总在线] %d人 | 统计 %d集 | 耗时 %.1fs",
                        total_online, len(_all_main_ep_refs), elapsed)
        except Exception as e:
            logger.error("[总在线] 采集失败: %s", e)

        config = _read_monitor_config()
        await asyncio.sleep(config.get("season_interval", 60))


async def monitor_single_ep(client) -> None:
    """协程B: 每10秒检测/监控指定单集在线人数"""
    global _current_ep_title, _current_ep_aid, _current_ep_cid, _current_ep_bvid, _running

    while _running:
        try:
            await _determine_monitor_target(client)

            if _current_ep_title:
                online = await client.get_online_count(
                    aid=_current_ep_aid,
                    cid=_current_ep_cid,
                    bvid=_current_ep_bvid,
                )
                _save_log("episode", online, ep_id=None, ep_title=_current_ep_title)
                label = "自动177" if _current_ep_is_auto_177 else "手动"
                logger.info("[%s集] %s 在线: %d人", _current_ep_title, label, online)
        except Exception as e:
            logger.error("[单集] 采集失败: %s", e)

        config = _read_monitor_config()
        await asyncio.sleep(config.get("episode_interval", 10))


async def main() -> None:
    global _running

    init_db()
    logger.info("凡人修仙传实时监控启动 (season_id=%d)", SEASON_ID)
    logger.info("目标时间: 2026-06-13 11:00 (自动切177集)")
    logger.info("总在线: 每60s | 单集: 每10s")
    logger.info("手动指定单集: 编辑 %s (内容为集号数字)", get_settings().monitor_ep_file)

    loop = asyncio.get_running_loop()

    def _handle_signal(sig):
        global _running
        logger.info("收到信号 %s, 准备退出...", sig.name)
        _running = False

    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _handle_signal, sig)

    try:
        async with ClientFactory.create("bilibili") as client:
            await asyncio.gather(
                monitor_season_online(client),
                monitor_single_ep(client),
            )
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("凡人修仙传监控已停止")


if __name__ == "__main__":
    asyncio.run(main())
