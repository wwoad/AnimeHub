"""凡人修仙传监控 — 后台采集器 (Streamlit 内嵌)

在 Streamlit 进程内的独立线程中运行, 受 toggle 开关控制启停。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from datetime import datetime
from pathlib import Path

from config import get_settings

SEASON_ID = 28747
TARGET_DATETIME = datetime(2026, 6, 13, 11, 0, 0)

logger = logging.getLogger("fanren_collector")

_running: bool = False
_thread: threading.Thread | None = None
_stop_event: threading.Event | None = None
_log_buffer: list[str] = []


def _add_log(msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    _log_buffer.append(line)
    if len(_log_buffer) > 200:
        _log_buffer[:] = _log_buffer[-100:]
    logger.info(msg)


def get_logs(n: int = 50) -> list[str]:
    return _log_buffer[-n:]


def is_running() -> bool:
    return _running


def _read_config() -> dict:
    try:
        if get_settings().monitor_config_file.exists():
            return json.loads(get_settings().monitor_config_file.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {"season_interval": 60, "episode_interval": 10}


def _read_manual_ep() -> str | None:
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
        if ep.get("pub_time", 0) > now_ts:
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


def _save_log(log_type: str, online_count: int, ep_title: str | None = None) -> None:
    from db.session import get_session
    from models.fanren_monitor import FanrenMonitorLog

    try:
        with get_session() as session:
            log_entry = FanrenMonitorLog(
                log_type=log_type,
                season_id=SEASON_ID,
                online_count=online_count,
                ep_title=ep_title,
                captured_at=datetime.now(),
            )
            session.add(log_entry)
            session.flush()
    except Exception as e:
        _add_log(f"写入DB失败: {e}")


def _determine_monitor_target(client, eps_data: list[dict], current_ep_title: str | None,
                              current_is_auto: bool) -> tuple[str | None, int, int, str, bool]:
    now = datetime.now()

    if now >= TARGET_DATETIME:
        ep177 = _find_ep_by_title(eps_data, "177")
        if ep177:
            if not current_is_auto or current_ep_title != "177":
                _add_log("[单集] 已到目标时间, 自动切换到第177集")
            return "177", ep177["aid"], ep177["cid"], ep177["bvid"], True

    manual_ep = _read_manual_ep()
    if manual_ep:
        target = _find_ep_by_title(eps_data, manual_ep)
        if target:
            if current_ep_title != manual_ep:
                _add_log(f"[单集] 手动切换到第{manual_ep}集")
            return manual_ep, target["aid"], target["cid"], target["bvid"], False
        else:
            _add_log(f"[单集] 第{manual_ep}集未找到")

    return current_ep_title, 0, 0, "", current_is_auto


async def _collect_loop(stop_event: threading.Event) -> None:
    from client.factory import ClientFactory

    async with ClientFactory.create("bilibili") as client:
        season_info = await client.get_season_info(SEASON_ID)
        all_eps = _extract_ep_data(season_info.episodes or []) if season_info else []
        _add_log(f"采集器启动, 加载 {len(all_eps)} 集正片")

        ep_title: str | None = None
        ep_aid: int = 0
        ep_cid: int = 0
        ep_bvid: str = ""
        ep_is_auto: bool = False

        last_season_collect = 0.0
        last_episode_collect = 0.0

        while not stop_event.is_set():
            config = _read_config()
            season_interval = config.get("season_interval", 60)
            episode_interval = config.get("episode_interval", 10)
            now_ts = datetime.now().timestamp()

            if now_ts >= last_season_collect:
                try:
                    if not all_eps:
                        season_info = await client.get_season_info(SEASON_ID)
                        all_eps = _extract_ep_data(season_info.episodes or []) if season_info else []
                    if all_eps:
                        t0 = datetime.now()
                        online_counts = await client.get_online_counts_batch(all_eps)
                        total_online = sum(online_counts)
                        elapsed = (datetime.now() - t0).total_seconds()
                        _save_log("season", total_online)
                        _add_log(f"[总在线] {total_online}人 | {len(all_eps)}集 | {elapsed:.1f}s")
                except Exception as e:
                    _add_log(f"[总在线] 失败: {e}")
                if last_season_collect == 0:
                    last_season_collect = now_ts
                last_season_collect += season_interval

            if now_ts >= last_episode_collect:
                try:
                    if not all_eps:
                        season_info = await client.get_season_info(SEASON_ID)
                        all_eps = _extract_ep_data(season_info.episodes or []) if season_info else []
                    ep_title, ep_aid, ep_cid, ep_bvid, ep_is_auto = _determine_monitor_target(
                        client, all_eps, ep_title, ep_is_auto,
                    )
                    if ep_title and ep_aid:
                        online = await client.get_online_count(aid=ep_aid, cid=ep_cid, bvid=ep_bvid)
                        _save_log("episode", online, ep_title=ep_title)
                        label = "自动177" if ep_is_auto else "手动"
                        _add_log(f"[{label}] 第{ep_title}集在线: {online}人")
                except Exception as e:
                    _add_log(f"[单集] 失败: {e}")
                if last_episode_collect == 0:
                    last_episode_collect = now_ts
                last_episode_collect += episode_interval

            await asyncio.sleep(1)


def _run_async_loop(stop_event: threading.Event) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_collect_loop(stop_event))
    except Exception as e:
        _add_log(f"采集器异常退出: {e}")
    finally:
        loop.close()


def start_collector() -> None:
    global _running, _thread, _stop_event
    if _running:
        return
    _log_buffer.clear()
    _stop_event = threading.Event()
    _thread = threading.Thread(target=_run_async_loop, args=(_stop_event,), daemon=True)
    _running = True
    _thread.start()
    _add_log("采集器已启动")


def stop_collector() -> None:
    global _running, _thread, _stop_event
    if not _running:
        return
    _add_log("采集器正在停止...")
    if _stop_event:
        _stop_event.set()
    if _thread:
        _thread.join(timeout=5)
    _running = False
    _thread = None
    _stop_event = None
    _add_log("采集器已停止")
