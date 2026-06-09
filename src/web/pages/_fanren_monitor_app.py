"""凡人修仙传实时监控 — 内联渲染

在数据分析页面的「凡人监控」标签页中直接渲染。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from streamlit_autorefresh import st_autorefresh

from web.pages._fanren_collector import (
    get_logs as get_collector_logs,
    is_running as is_collector_running,
    start_collector,
    stop_collector,
)
from web.pages._fanren_monitor_data import (
    DEFAULT_EPISODE_INTERVAL,
    DEFAULT_SEASON_INTERVAL,
    EPISODE_INTERVAL_OPTIONS,
    SEASON_INTERVAL_OPTIONS,
    TARGET_DATETIME,
    get_available_ep_titles,
    get_episode_records,
    get_latest_ep_online,
    get_latest_season_online,
    get_season_records,
    load_ep_logs,
    load_season_logs,
    read_manual_ep,
    read_monitor_config,
    write_manual_ep,
    write_monitor_config,
)

TIME_RANGE_OPTIONS = {
    "15min": 15,
    "30min": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "8h": 480,
    "24h": 1440,
}


def _k_fmt(n: int) -> str:
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def _build_chart(df: pd.DataFrame, color: str, name: str, dtick_seconds: int | None = None) -> go.Figure | None:
    if df is None or df.empty:
        return None
    d = df.tail(20)
    y_vals = d["在线人数"]
    ymin, ymax = int(y_vals.min()), int(y_vals.max())
    if ymin == ymax:
        ymin = max(0, ymin - 3)
        ymax = ymax + 3

    margin = max(1, int((ymax - ymin) * 0.1))
    y_range = [max(0, ymin - margin), ymax + margin]

    y_step = max(1, (y_range[1] - y_range[0]) // 7)
    y_tick_vals = list(range(y_range[0], y_range[1] + y_step, y_step))
    y_tick_texts = [_k_fmt(v) for v in y_tick_vals]

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=d["_ts"],
            y=y_vals,
            mode="lines",
            name=name,
            line=dict(color=color, width=2),
            fill="tozeroy",
            fillcolor=f"rgba({','.join(str(int(color[i : i + 2], 16)) for i in (1, 3, 5))},0.08)",
            hovertemplate="%{x|%H:%M:%S}<br>%{y}人<extra></extra>",
        )
    )
    fig.update_layout(
        height=300,
        margin=dict(l=0, r=10, t=5, b=5),
        xaxis=dict(
            type="date",
            tickformat="%H:%M:%S",
            dtick=dtick_seconds * 1000 if dtick_seconds else None,
            nticks=None if dtick_seconds else 8,
            showgrid=False,
            zeroline=False,
            tickfont=dict(size=9, color="#888"),
        ),
        yaxis=dict(
            range=y_range,
            tickmode="array",
            tickvals=y_tick_vals,
            ticktext=y_tick_texts,
            showgrid=True,
            gridwidth=1,
            gridcolor="rgba(128,128,128,0.10)",
            zeroline=False,
            tickfont=dict(size=9, color="#888"),
        ),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    return fig


def render_fanren_monitor() -> None:
    title_col, toggle_col = st.columns([4, 1], gap="small")
    with title_col:
        st.markdown("### 凡人修仙传实时在线数据统计")
    with toggle_col:
        st.markdown("""
        <style>
        [data-testid="stToggle"] {
            transform: scale(1.5);
            transform-origin: left center;
        }
        [data-testid="stToggle"] label {
            font-size: 1.2rem;
            display: flex !important;
            align-items: center;
            gap: 4px;
        }
        </style>
        """, unsafe_allow_html=True)
        collector_on = st.toggle("采集", value=True, key="fanren_collector_toggle")
    st.markdown('<hr style="margin:2px 0 3px 0;border-color:rgba(128,128,128,0.2)">', unsafe_allow_html=True)

    if collector_on and not is_collector_running():
        start_collector()
    elif not collector_on and is_collector_running():
        stop_collector()

    now = datetime.now()
    is_auto_177 = now >= TARGET_DATETIME

    card_col1, card_col2 = st.columns(2, gap="medium")

    with card_col1:
        season_online, season_time = get_latest_season_online()
        config = read_monitor_config()
        season_interval_keys = list(SEASON_INTERVAL_OPTIONS.keys())
        season_interval_idx = season_interval_keys.index(next(k for k, v in SEASON_INTERVAL_OPTIONS.items() if v == config.get("season_interval", DEFAULT_SEASON_INTERVAL)))

        num_col, int_col, time_col = st.columns([2.5, 1.2, 1.2], gap="small")
        with num_col:
            st.markdown(
                f"""
            <div style="display:flex;align-items:center;gap:6px;line-height:1;min-height:70px;">
                <span style="font-size:13px;color:#888;">总在线</span>
                <span style="font-size:28px;font-weight:700;color:#00E5FF;">{season_online:,}</span>
                <span style="font-size:14px;color:#888;">人</span>
                <span style="font-size:11px;color:#666;margin-left:4px;">{season_time}</span>
            </div>
            """,
                unsafe_allow_html=True,
            )
        with int_col:
            season_interval_key = st.selectbox(
                "间隔",
                season_interval_keys,
                index=season_interval_idx,
                key="season_interval_sel",
            )
            new_season_val = SEASON_INTERVAL_OPTIONS[season_interval_key]
            if new_season_val != config.get("season_interval"):
                config["season_interval"] = new_season_val
                write_monitor_config(config)
        with time_col:
            time_label = st.selectbox(
                "时间",
                list(TIME_RANGE_OPTIONS.keys()),
                index=1,
                key="season_time_range",
            )

        minutes = TIME_RANGE_OPTIONS[time_label]
        season_df = load_season_logs(minutes_ago=minutes)

        fig = _build_chart(season_df, "#00E5FF", "总在线", dtick_seconds=new_season_val)
        if fig:
            fig.update_layout(height=280)
            st.plotly_chart(fig, width="stretch", config={"scrollZoom": True, "displayModeBar": "hover"})
        else:
            st.info("等待数据...")

    with card_col2:
        manual_ep = read_manual_ep()
        available_titles = get_available_ep_titles()

        if is_auto_177:
            monitor_ep = "177"
        elif available_titles:
            idx = available_titles.index(manual_ep) if manual_ep and manual_ep in available_titles else len(available_titles) - 1
            monitor_ep = available_titles[idx]
        else:
            monitor_ep = manual_ep or "176"

        ep_online, ep_time = get_latest_ep_online(monitor_ep)
        config = read_monitor_config()
        ep_interval_keys = list(EPISODE_INTERVAL_OPTIONS.keys())
        ep_interval_idx = ep_interval_keys.index(next(k for k, v in EPISODE_INTERVAL_OPTIONS.items() if v == config.get("episode_interval", DEFAULT_EPISODE_INTERVAL)))

        num_col, ep_col, int_col, time_col = st.columns([2, 1.2, 1, 1], gap="small")
        with num_col:
            st.markdown(
                f"""
            <div style="display:flex;align-items:center;gap:6px;line-height:1;min-height:70px;">
                <span style="font-size:13px;color:#888;">第{monitor_ep}集在线</span>
                <span style="font-size:28px;font-weight:700;color:#FF6D00;">{ep_online:,}</span>
                <span style="font-size:14px;color:#888;">人</span>
                <span style="font-size:11px;color:#666;margin-left:4px;">{ep_time}</span>
            </div>
            """,
                unsafe_allow_html=True,
            )
        with ep_col:
            if is_auto_177:
                st.markdown("<div style='font-weight:600;font-size:14px;line-height:70px;'>第177集</div>", unsafe_allow_html=True)
            elif available_titles:
                idx = available_titles.index(manual_ep) if manual_ep and manual_ep in available_titles else len(available_titles) - 1
                monitor_ep = st.selectbox(
                    "集号",
                    available_titles,
                    index=idx,
                    key="ep_sel",
                )
                if monitor_ep != manual_ep:
                    write_manual_ep(monitor_ep)
            else:
                default_val = manual_ep or "176"
                monitor_ep = st.text_input(
                    "集号",
                    value=default_val,
                    key="ep_inp",
                )
                if monitor_ep != manual_ep:
                    write_manual_ep(monitor_ep)
        with int_col:
            ep_interval_key = st.selectbox(
                "间隔",
                ep_interval_keys,
                index=ep_interval_idx,
                key="ep_interval_sel",
            )
            new_ep_val = EPISODE_INTERVAL_OPTIONS[ep_interval_key]
            if new_ep_val != config.get("episode_interval"):
                config["episode_interval"] = new_ep_val
                write_monitor_config(config)
        with time_col:
            time_label = st.selectbox(
                "时间",
                list(TIME_RANGE_OPTIONS.keys()),
                index=1,
                key="ep_time_range",
            )

        minutes = TIME_RANGE_OPTIONS[time_label]
        ep_df = load_ep_logs(monitor_ep, minutes_ago=minutes)

        fig = _build_chart(ep_df, "#FF6D00", f"第{monitor_ep}集", dtick_seconds=new_ep_val)
        if fig:
            fig.update_layout(height=280)
            st.plotly_chart(fig, width="stretch", config={"scrollZoom": True, "displayModeBar": "hover"})
        else:
            st.info("等待数据...")

    with st.expander("采样记录"):
        season_records = get_season_records(200)
        ep_records = get_episode_records(monitor_ep, 200)

        col_left, col_right = st.columns(2)
        with col_left:
            st.markdown("**总在线**")
            if season_records:
                st.dataframe(pd.DataFrame(season_records), width="stretch", height=200, hide_index=True)
            else:
                st.info("暂无数据, 等待采集器写入...")
        with col_right:
            st.markdown(f"**分集 (#{monitor_ep})**")
            if ep_records:
                st.dataframe(pd.DataFrame(ep_records), width="stretch", height=200, hide_index=True)
            else:
                st.info("暂无数据, 等待采集器写入...")

    with st.expander("采集日志"):
        col_logs = get_collector_logs(50)
        if col_logs:
            st.markdown("```\n" + "\n".join(reversed(col_logs)) + "\n```")
        else:
            st.info("采集器未启动, 请开启右上角「采集」开关")

    st_autorefresh(interval=min(config.get("season_interval", DEFAULT_SEASON_INTERVAL), config.get("episode_interval", DEFAULT_EPISODE_INTERVAL)) * 1000, limit=86400, key="fanren_refresh")
