"""数据追踪: 爬取数据+跟踪列表+发现动画"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque

import streamlit as st

from config import get_settings
from db.session import init_db
from web.queries import (
    add_to_tracking,
    get_anime_list,
    load_discover_csv,
    toggle_tracking_paused,
    update_csv_paused,
    update_csv_priority,
    update_tracking_priority,
)

_LOG_CSS = """

<style>

    .log-panel {

        background-color: #1a1a2e;

        border: 1px solid #333;

        border-radius: 6px;

        padding: 10px;

        height: 200px;

        overflow-y: auto;

        font-family: 'Consolas', 'Courier New', monospace;

        font-size: 12px;

        color: #0f0;

        line-height: 1.5;

        white-space: pre-wrap;

        word-break: break-all;

    }

</style>

"""


class StreamlitLogHandler(logging.Handler):
    """将日志写入内存循环缓冲区，供 Streamlit 实时读取"""

    def __init__(self, max_lines: int = 200) -> None:
        super().__init__()
        self.buffer: deque[str] = deque(maxlen=max_lines)
        fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self.setFormatter(fmt)

    def emit(self, record: logging.LogRecord) -> None:
        self.buffer.append(self.format(record))

    @property
    def text(self) -> str:
        return "\n".join(self.buffer)


class WebLogFilter(logging.Filter):
    """只允许业务核心模块的日志进入 Web UI 面板，屏蔽库噪音"""

    BLOCKED_PREFIXES = ("httpx", "httpcore", "urllib3", "urllib3.")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.ERROR:
            return True
        if record.name.startswith(self.BLOCKED_PREFIXES):
            return False
        if record.name in ("cli.crawling", "core.orchestrator", "core.pipeline", "web.pages.tracking"):
            return True
        return False


def _read_recent_log(n: int = 80) -> str:

    log_path = get_settings().log_dir / "anime-hub.log"

    if not log_path.exists():
        return "暂无日志"

    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()

        return "\n".join(lines[-n:])

    except Exception:
        return "读取日志失败"


def _crawl_worker(season_id: int | None, handler: StreamlitLogHandler, tracker: dict[str, int], mode: str = "fast") -> None:
    """在后台线程中执行爬取，日志写入 handler"""
    from core.pipeline import add_progress_listener, remove_progress_listener

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    handler.addFilter(WebLogFilter())
    root.addHandler(handler)

    def _on_progress(season_id: int, title: str, current: int, total: int) -> None:
        if current >= total and total > 0:
            tracker.pop(season_id, None)
        elif total > 0:
            tracker[season_id] = {"title": title, "current": current, "total": total}

    add_progress_listener(_on_progress)
    try:
        import argparse

        from cli.crawling import cmd_crawl

        logging.getLogger(__name__).info("爬取线程已启动")
        args = argparse.Namespace(season_id=season_id, mode=mode)
        cmd_crawl(args)
    except Exception:
        logger = logging.getLogger(__name__)
        logger.exception("爬取线程异常")
    finally:
        remove_progress_listener(_on_progress)
        root.removeHandler(handler)


def _do_crawl(season_id: int | None = None, mode: str = "fast") -> None:
    """启动后台爬取线程，不阻塞 Streamlit"""
    init_db()
    handler = StreamlitLogHandler()
    tracker: dict[int, dict[str, object]] = {}
    thread = threading.Thread(
        target=_crawl_worker,
        args=(season_id, handler, tracker, mode),
        daemon=True,
    )
    st.session_state["crawl_handler"] = handler
    st.session_state["crawl_thread"] = thread
    st.session_state["crawling"] = True
    st.session_state["crawl_progress"] = tracker
    thread.start()


def _check_tracking_changes(edited, original_paused, original_priority):

    to_pause_ids = []

    to_unpause_ids = []

    to_quick_ids = []

    to_full_ids = []

    for i in range(len(edited)):
        sid = int(edited.iloc[i]["编号"])

        new_paused = bool(edited.iloc[i]["暂停"])

        old_paused = bool(original_paused[i])

        if new_paused and not old_paused:
            to_pause_ids.append(sid)

        elif not new_paused and old_paused:
            to_unpause_ids.append(sid)

        new_mode = str(edited.iloc[i]["采集模式"])
        new_priority = 0 if "快速" in new_mode else 1
        old_priority = original_priority[i]

        if new_priority != old_priority:
            if new_priority == 0:
                to_quick_ids.append(sid)
            else:
                to_full_ids.append(sid)

    return to_pause_ids, to_unpause_ids, to_quick_ids, to_full_ids


def render_tracking() -> None:
    st.markdown(
        """
<style>
    /* ===== 爬取 / 更新列表 (Primary) Disabled ===== */
    div[data-testid="stButton"] button[kind="primary"]:disabled {
        color: #888 !important;
    }
    /* ===== 应用 (Secondary) Disabled ===== */
    div[data-testid="stButton"] button[kind="secondary"]:disabled {
        color: #555 !important;
    }
</style>
""",
        unsafe_allow_html=True,
    )
    try:
        tracking_header = st.container()

        _render_tracking_table()

        _render_log_panel()
    except Exception:
        import traceback

        traceback.print_exc()
        st.error("渲染追踪页面出错")

    st.markdown("---")

    discover_header = st.container()

    _render_discover_content()

    with tracking_header:
        _render_crawl_header()

    with discover_header:
        _render_discover_header()


def _render_crawl_header() -> None:

    is_crawling = st.session_state.get("crawling", False)

    has_changes = st.session_state.get("tracking_has_changes", False)

    title_col, scope_col, mode_col, crawl_col, apply_col = st.columns([4, 1, 1, 1.5, 0.5])

    with title_col:
        df = get_anime_list()
        count = len(df) if not df.empty else 0
        last = ""
        if not df.empty and "last_crawled" in df.columns:
            v = df[df["last_crawled"] != "未采集"]
            if not v.empty:
                last = v["last_crawled"].iloc[0]
        header = f"### 追踪动画列表 - 📋 {count} 部"
        if last:
            header += f" - 🕐 {last}"
        st.markdown(header)

    with scope_col:
        crawl_scope = st.selectbox(
            "采集范围",
            ["快速模式", "全量模式"],
            label_visibility="collapsed",
            key="crawl_scope_sel",
            disabled=is_crawling,
        )
        st.session_state["crawl_scope"] = crawl_scope

    with mode_col:
        crawl_mode = st.selectbox(
            "模式",
            ["批量爬取", "单个爬取"],
            label_visibility="collapsed",
            key="crawl_mode_sel",
            disabled=is_crawling,
        )

    with crawl_col:
        if crawl_mode == "批量爬取":
            if st.button("爬取", type="primary", use_container_width=True, key="crawl_all", disabled=is_crawling):
                scope = st.session_state.get("crawl_scope", "快速模式")
                mode = "fast" if scope == "快速模式" else "full"
                _do_crawl(mode=mode)

                st.rerun()

        else:
            sid_col, btn_col = st.columns([1.2, 0.8])

            with sid_col:
                sid = st.text_input(
                    "动画编号",
                    placeholder="输入动画编号...",
                    label_visibility="collapsed",
                    key="crawl_sid",
                    disabled=is_crawling,
                )

            with btn_col:
                if st.button("爬取", type="primary", use_container_width=True, key="crawl_single", disabled=is_crawling):
                    if sid and sid.isdigit():
                        _do_crawl(season_id=int(sid))

                        st.rerun()

                    else:
                        st.warning("请输入有效的动画编号")

    with apply_col:
        if st.button("应用", disabled=not has_changes or is_crawling, key="apply_btn"):
            st.session_state["tracking_apply"] = True
            st.rerun()


def _render_tracking_table() -> None:

    df = get_anime_list()

    if df.empty:
        st.info("暂无追踪动画, 请从下方动画表添加")

        return

    disp_cols = [
        "season_id",
        "title",
        "rating_score",
        "total_episodes",
        "follow",
        "views",
        "danmaku",
        "reply",
        "is_finish",
        "status",
        "last_crawled",
    ]

    available = [c for c in disp_cols if c in df.columns]

    rename = {
        "season_id": "编号",
        "title": "标题",
        "rating_score": "评分",
        "total_episodes": "集数",
        "follow": "追番量",
        "views": "播放量",
        "danmaku": "弹幕",
        "reply": "评论",
        "is_finish": "状态",
        "status": "采集状态",
        "last_crawled": "上次采集",
    }

    show = df[available].copy()

    show = show.rename(columns={k: v for k, v in rename.items() if k in available})

    _status_emoji = {"成功": "✅  成功", "失败": "❌  失败", "待采集": "⏳  待采集", "采集中": "🔄  采集中"}

    show["采集状态"] = show["采集状态"].fillna("待采集").astype(str).map(lambda x: _status_emoji.get(x, x))

    _finish_emoji = {"完结": "✅  完结", "连载中": "🔄  连载中"}

    show["状态"] = show["状态"].astype(str).map(lambda x: _finish_emoji.get(x, x))

    for col in ["播放量", "追番量", "弹幕", "评论"]:
        if col in show.columns:
            show[col] = show[col].fillna(0).astype("int64").apply(lambda x: f"{x:,}")
    if "评分" in show.columns:
        show["评分"] = show["评分"].fillna(0.0).apply(lambda x: f"{x:.1f}")

    for col in show.columns:
        if col not in ("采集状态", "状态", "播放量", "追番量", "弹幕", "评论", "评分", "暂停"):
            show[col] = show[col].astype(str)

    show["暂停"] = df["paused"].values
    show["采集模式"] = df["priority"].map({0: "⚡ 快速", 1: "📦 全量"}).astype(str)

    tracking_ver = st.session_state.get("tracking_editor_ver", 0)

    edited = st.data_editor(
        show,
        column_config={
            "采集模式": st.column_config.SelectboxColumn(
                "采集模式",
                options=["⚡ 快速", "📦 全量"],
            ),
            "暂停": st.column_config.CheckboxColumn("暂停"),
        },
        disabled=[c for c in show.columns if c not in ("采集模式", "暂停")],
        width="stretch",
        height=max(150, min(350, 50 + len(show) * 33)),
        hide_index=True,
        key=f"tracking_editor_{tracking_ver}",
    )

    to_pause_ids, to_unpause_ids, to_quick_ids, to_full_ids = _check_tracking_changes(edited, df["paused"].tolist(), df["priority"].tolist())

    has_changes = bool(to_pause_ids or to_unpause_ids or to_quick_ids or to_full_ids)

    st.session_state["tracking_has_changes"] = has_changes

    st.session_state["tracking_to_pause"] = to_pause_ids

    st.session_state["tracking_to_unpause"] = to_unpause_ids

    st.session_state["tracking_to_quick"] = to_quick_ids

    st.session_state["tracking_to_full"] = to_full_ids

    if st.session_state.get("tracking_apply", False):
        if to_quick_ids:
            for sid in to_quick_ids:
                update_tracking_priority(sid, 0)
            update_csv_priority(to_quick_ids, 0)
        if to_full_ids:
            for sid in to_full_ids:
                update_tracking_priority(sid, 1)
            update_csv_priority(to_full_ids, 1)

        if to_quick_ids or to_full_ids:
            msg_parts = []
            if to_quick_ids:
                msg_parts.append(f"{len(to_quick_ids)} 部 → 快速模式")
            if to_full_ids:
                msg_parts.append(f"{len(to_full_ids)} 部 → 全量模式")
            st.success(f"已更新采集模式: {', '.join(msg_parts)}")

        for sid in to_pause_ids:
            toggle_tracking_paused(sid, True)

        if to_pause_ids:
            update_csv_paused(to_pause_ids, True)

            st.success(f"已暂停 {len(to_pause_ids)} 部动画")

        for sid in to_unpause_ids:
            toggle_tracking_paused(sid, False)

        if to_unpause_ids:
            update_csv_paused(to_unpause_ids, False)

            st.success(f"已恢复 {len(to_unpause_ids)} 部动画")

        st.session_state["tracking_apply"] = False

        st.session_state["tracking_has_changes"] = False


def _clear_crawl_state() -> None:
    for k in ("crawling", "crawl_completed", "crawl_handler", "crawl_thread", "crawl_progress", "crawl_dismiss_at"):
        st.session_state.pop(k, None)


def _render_log_panel() -> None:
    """爬取进度面板 — fragment 局部刷新，不阻塞主页面"""
    crawling = st.session_state.get("crawling", False)
    completed = st.session_state.get("crawl_completed", False)
    if not crawling and not completed:
        return

    @st.fragment(run_every=3)
    def _log_body() -> None:
        _crawling = st.session_state.get("crawling", False)
        _completed = st.session_state.get("crawl_completed", False)

        st.markdown(_LOG_CSS, unsafe_allow_html=True)

        # ========== 标题行 + ✕ 按钮 ==========
        _, close_col = st.columns([30, 1])
        with close_col:
            if st.button("✕", key="close_log", help="关闭日志面板", type="secondary"):
                _clear_crawl_state()
                st.rerun(scope="app")
                return

        # ========== 日志面板 ==========
        handler: StreamlitLogHandler | None = st.session_state.get("crawl_handler")
        log_text = handler.text if handler else _read_recent_log(80)
        st.markdown(f'<div class="log-panel">{log_text}</div>', unsafe_allow_html=True)

        # ========== 多进度条 ==========
        thread: threading.Thread | None = st.session_state.get("crawl_thread")
        progress_map: dict = st.session_state.get("crawl_progress", {})

        if _crawling and thread is not None and thread.is_alive():
            status_label = "正在爬取..."
            status_color = "#aaa"
        else:
            status_label = "爬取完成!"
            status_color = "#0f0"

        st.markdown(f'<div style="color:{status_color};font-size:14px;font-weight:600;margin-bottom:6px;">{status_label} ({len(progress_map)} 部)</div>', unsafe_allow_html=True)

        if progress_map:
            items = list(progress_map.items())
            prog_cols = st.columns(len(items))
            for col, (sid, prog) in zip(prog_cols, items):
                title = prog.get("title", f"#{sid}")
                cur = prog.get("current", 0)
                tot = prog.get("total", 0)
                pct = (cur / tot * 100) if tot > 0 else 0
                with col:
                    st.markdown(
                        f"""<div style="position:relative;height:28px;background:#1a1a2e;border:1px solid #333;border-radius:4px;overflow:hidden;">
                             <div style="background:#FF9800;width:{pct}%;height:100%;border-radius:4px;transition:width .3s;"></div>
                             <div style="position:absolute;top:0;left:0;right:0;bottom:0;display:flex;align-items:center;justify-content:center;gap:4px;color:#fff;font-size:12px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;padding:0 6px;text-shadow:0 1px 3px rgba(0,0,0,.8),0 0 2px rgba(0,0,0,.5);">
                                 <span style="overflow:hidden;text-overflow:ellipsis;">{title}</span>
                                 <span>{cur}/{tot}</span>
                            </div>
                        </div>""",
                        unsafe_allow_html=True,
                    )

        # ========== 状态转换 ==========
        if _crawling and thread is not None and not thread.is_alive():
            st.session_state["crawling"] = False
            st.session_state["crawl_completed"] = True
            st.session_state["crawl_dismiss_at"] = time.time() + 5

        if _completed and time.time() > st.session_state.get("crawl_dismiss_at", 0):
            _clear_crawl_state()
            st.rerun(scope="app")
            return

    _log_body()


def _render_discover_header() -> None:

    is_crawling = st.session_state.get("crawling", False)

    platform_name = st.session_state.get("platform", "bilibili")

    display_name = "bilibili" if platform_name == "bilibili" else platform_name.capitalize()

    has_selection = st.session_state.get("discover_has_selection", False)

    title_col, update_col, add_col = st.columns([5, 1, 0.5])

    with title_col:
        st.markdown(f"### {display_name} 动画表")

    with update_col:
        if st.button("更新列表", key="refresh_discover", type="primary", use_container_width=True, disabled=is_crawling):
            with st.spinner("正在从B站拉取..."):
                import asyncio

                from core.discover import fetch_and_save

                asyncio.run(fetch_and_save())

            st.success("拉取完成!")

            st.rerun()

    with add_col:
        if st.button("应用", disabled=not has_selection or is_crawling, key="add_tracking"):
            st.session_state["discover_apply"] = True
            st.rerun()


def _render_discover_content() -> None:

    disc_df = load_discover_csv()

    if disc_df.empty:
        st.info("暂无动画数据, 请点击【更新列表】拉取")

        st.session_state["discover_has_selection"] = False

        return

    display_cols = ["season_id", "title", "score", "eps", "area", "status", "season_type"]

    available = [c for c in display_cols if c in disc_df.columns]

    rename2 = {
        "season_id": "编号",
        "title": "标题",
        "score": "评分",
        "eps": "集数",
        "area": "地区",
        "status": "状态",
        "season_type": "类型",
    }

    show = disc_df[available].copy()

    show = show.rename(columns={k: v for k, v in rename2.items() if k in available})

    show = show.fillna("无")

    for col in show.columns:
        if col not in ("评分", "追踪"):
            show[col] = show[col].astype(str)

    if "评分" in show.columns:
        show["评分"] = show["评分"].apply(lambda x: f"{float(x):.1f}" if x != "无" else x)

    for col in show.columns:
        if col not in ("评分", "追踪"):
            show[col] = show[col].astype(str)

    show["追踪"] = False

    discover_ver = st.session_state.get("discover_editor_ver", 0)

    edited_disc = st.data_editor(
        show,
        column_config={
            "追踪": st.column_config.CheckboxColumn("追踪"),
        },
        disabled=[c for c in show.columns if c != "追踪"],
        width="stretch",
        height=400,
        hide_index=True,
        key=f"discover_editor_{discover_ver}",
    )

    to_track_df = edited_disc[edited_disc["追踪"] == True]

    st.session_state["discover_has_selection"] = len(to_track_df) > 0

    if st.session_state.get("discover_apply", False):
        if len(to_track_df) > 0:
            selected_ids = [int(sid) for sid in to_track_df["编号"].tolist()]

            title_map = {}

            if "标题" in to_track_df.columns:
                for _, row in to_track_df.iterrows():
                    title_map[int(row["编号"])] = row["标题"]

            added = add_to_tracking(selected_ids, title_map)

            if added > 0:
                st.success(f"已添加 {added} 部动画到追踪列表")

            else:
                st.info("所选动画已在追踪列表中")

        st.session_state["discover_apply"] = False

        st.session_state["discover_has_selection"] = False

        st.session_state["discover_editor_ver"] = discover_ver + 1

        st.rerun()
