"""数据追踪: 爬取数据+跟踪列表+发现动画"""

from __future__ import annotations

import streamlit as st

from config import get_settings
from db.session import init_db
from web.queries import (
    add_to_tracking,
    delete_tracking,
    get_anime_list,
    load_discover_csv,
    toggle_tracking_paused,
    update_csv_paused,
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


def _read_recent_log(n: int = 80) -> str:

    log_path = get_settings().log_dir / "anime-hub.log"

    if not log_path.exists():
        return "暂无日志"

    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()

        return "\n".join(lines[-n:])

    except Exception:
        return "读取日志失败"


def _do_crawl(season_id: int | None = None) -> None:

    import argparse

    from cli.crawling import cmd_crawl

    args = argparse.Namespace(season_id=season_id)

    init_db()

    cmd_crawl(args)


def _check_tracking_changes(edited, original_paused):

    deleted_ids = []

    to_pause_ids = []

    to_unpause_ids = []

    for i in range(len(edited)):
        sid = int(edited.iloc[i]["编号"])

        if bool(edited.iloc[i]["删除"]):
            deleted_ids.append(sid)

        else:
            new_paused = bool(edited.iloc[i]["暂停"])

            old_paused = bool(original_paused[i])

            if new_paused and not old_paused:
                to_pause_ids.append(sid)

            elif not new_paused and old_paused:
                to_unpause_ids.append(sid)

    return deleted_ids, to_pause_ids, to_unpause_ids


def render_tracking() -> None:
    try:
        tracking_header = st.container()

        _render_tracking_table()

        _render_log_panel()
    except Exception:
        import traceback
        traceback.print_exc()
        st.error(f"渲染追踪页面出错")

    st.markdown("---")

    discover_header = st.container()

    _render_discover_content()

    with tracking_header:
        _render_crawl_header()

    with discover_header:
        _render_discover_header()


def _render_crawl_header() -> None:

    has_changes = st.session_state.get("tracking_has_changes", False)

    title_col, mode_col, crawl_col, apply_col = st.columns([5, 1, 1, 0.5])

    with title_col:
        st.markdown("### 追踪动画列表")

    with mode_col:
        crawl_mode = st.selectbox(
            "模式",
            ["批量爬取", "单个爬取"],
            label_visibility="collapsed",
            key="crawl_mode_sel",
        )

    with crawl_col:
        if crawl_mode == "批量爬取":
            if st.button("爬取", type="primary", use_container_width=True, key="crawl_all"):
                st.session_state["crawling"] = True

                with st.spinner("正在爬取所有动画..."):
                    _do_crawl()

                st.success("爬取完成!")

                st.rerun()

        else:
            sid_col, btn_col = st.columns([1, 1])

            with sid_col:
                sid = st.text_input(
                    "动画编号",
                    placeholder="输入动画编号",
                    label_visibility="collapsed",
                    key="crawl_sid",
                )

            with btn_col:
                if st.button("爬取", type="primary", use_container_width=True, key="crawl_single"):
                    if sid and sid.isdigit():
                        st.session_state["crawling"] = True

                        with st.spinner(f"正在爬取动画编号={sid}..."):
                            _do_crawl(int(sid))

                        st.success("爬取完成!")

                        st.rerun()

                    else:
                        st.warning("请有效的动画编号")

    with apply_col:
        if st.button("应用", disabled=not has_changes, key="apply_btn"):
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
        "area",
        "rating_score",
        "total_episodes",
        "follow",
        "views",
        "is_finish",
        "status",
        "last_crawled",
    ]

    available = [c for c in disp_cols if c in df.columns]

    rename = {
        "season_id": "编号",
        "title": "标题",
        "area": "地区",
        "rating_score": "评分",
        "total_episodes": "集数",
        "follow": "追番量",
        "views": "播放量",
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

    for col in ["播放量", "追番量", "收藏"]:
        if col in show.columns:
            show[col] = show[col].fillna(0).astype("int64").apply(lambda x: f"{x:,}")
    if "评分" in show.columns:
        show["评分"] = show["评分"].fillna(0.0).apply(lambda x: f"{x:.1f}")

    for col in show.columns:
        if col not in ("采集状态", "状态", "播放量", "追番量", "收藏", "评分", "暂停", "删除"):
            show[col] = show[col].astype(str)

    show["暂停"] = df["paused"].values

    show["删除"] = False

    tracking_ver = st.session_state.get("tracking_editor_ver", 0)

    edited = st.data_editor(
        show,
        column_config={
            "暂停": st.column_config.CheckboxColumn("暂停"),
            "删除": st.column_config.CheckboxColumn("删除"),
        },
        disabled=[c for c in show.columns if c not in ("暂停", "删除")],
        width="stretch",
        height=max(150, min(350, 50 + len(show) * 33)),
        hide_index=True,
        key=f"tracking_editor_{tracking_ver}",
    )

    deleted_ids, to_pause_ids, to_unpause_ids = _check_tracking_changes(edited, df["paused"].tolist())

    has_changes = bool(deleted_ids or to_pause_ids or to_unpause_ids)

    st.session_state["tracking_has_changes"] = has_changes

    st.session_state["tracking_deleted"] = deleted_ids

    st.session_state["tracking_to_pause"] = to_pause_ids

    st.session_state["tracking_to_unpause"] = to_unpause_ids

    if st.session_state.get("tracking_apply", False):
        if deleted_ids:
            count = delete_tracking(deleted_ids)

            st.success(f"已删除 {count} 部动画")

            st.session_state["tracking_apply"] = False

            st.session_state["tracking_has_changes"] = False

            st.session_state["tracking_editor_ver"] = tracking_ver + 1

            st.rerun()

        else:
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


def _render_log_panel() -> None:

    if not st.session_state.get("crawling", False):
        return

    st.markdown(_LOG_CSS, unsafe_allow_html=True)

    log_text = _read_recent_log(80)

    st.markdown(f'<div class="log-panel">{log_text}</div>', unsafe_allow_html=True)


def _render_discover_header() -> None:

    platform_name = st.session_state.get("platform", "bilibili")

    display_name = "bilibili" if platform_name == "bilibili" else platform_name.capitalize()

    has_selection = st.session_state.get("discover_has_selection", False)

    title_col, update_col, add_col = st.columns([5, 1, 0.5])

    with title_col:
        st.markdown(f"### {display_name} 动画表")

    with update_col:
        if st.button("更新列表", key="refresh_discover", type="primary", use_container_width=True):
            with st.spinner("正在从B站拉取..."):
                import asyncio

                from core.discover import fetch_and_save

                asyncio.run(fetch_and_save(season_type=4, order=2))

            st.success("拉取完成!")

            st.rerun()

    with add_col:
        if st.button("应用", disabled=not has_selection, key="add_tracking"):
            st.session_state["discover_apply"] = True
            st.rerun()


def _render_discover_content() -> None:

    disc_df = load_discover_csv()

    if disc_df.empty:
        st.info("暂无动画数据, 请点击【更新列表】拉取")

        st.session_state["discover_has_selection"] = False

        return

    display_cols = ["season_id", "title", "score", "eps", "area", "status"]

    available = [c for c in display_cols if c in disc_df.columns]

    rename2 = {
        "season_id": "编号",
        "title": "标题",
        "score": "评分",
        "eps": "集数",
        "area": "地区",
        "status": "状态",
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
