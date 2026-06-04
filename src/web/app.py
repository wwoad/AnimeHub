"""番剧数据中枢 - Web UI 入口

顶栏: 状态信息 | 平台选择（label和selectbox同行）
"""

from __future__ import annotations

import streamlit as st

from web.pages.analysis import render_analysis
from web.pages.settings import render_settings
from web.pages.tracking import render_tracking
from web.platforms import get_platforms
from web.queries import get_anime_list

st.set_page_config(
    page_title="番剧数据中枢",
    page_icon="📊",
    layout="wide",
)

_HIDE_CSS = """
<style>
    section[data-testid="stSidebar"] {
        display: none !important;
        width: 0 !important;
        min-width: 0 !important;
    }
    div[data-testid="stSidebarContainer"] { display: none !important; }
    [data-testid="stSidebarNav"] { display: none !important; }
    [data-testid="stSidebarUserContent"] { display: none !important; }
    [data-testid="stSidebarHeader"] { display: none !important; }
    button[kind="header"] { display: none !important; }
    [data-testid="stHeader"] { display: none !important; }
    .block-container {
        padding-top: 0.5rem !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        max-width: 100% !important;
    }
    h1, h2, h3 { margin-top: 0 !important; }
    .top-bar {
        display: flex;
        align-items: center;
        gap: 24px;
        padding: 6px 12px;
        background-color: rgba(128,128,128,0.1);
        border-radius: 8px;
        font-size: 14px;
        color: #aaa;
    }
    .top-bar-item {
        display: flex;
        align-items: center;
        gap: 4px;
    }
    .top-row {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-bottom: 4px;
    }
    .top-row-left {
        flex: 1;
    }
    .top-row-right {
        width: 140px;
        flex-shrink: 0;
    }
    .top-row-right div[data-testid="stSelectbox"] {
        padding-top: 0 !important;
        margin-top: 0 !important;
    }
    .top-row-right div[data-testid="stSelectbox"] > div:first-child {
        padding: 0 !important;
        margin-bottom: 0 !important;
    }
    .top-row-right div[data-testid="stSelectbox"] > div > div:first-child {
        min-height: 32px !important;
        height: 32px !important;
        padding: 2px 10px !important;
        font-size: 14px !important;
    }
</style>
"""

if "platform" not in st.session_state:
    st.session_state.platform = "bilibili"

platforms = get_platforms()
if not platforms:
    platforms = ["bilibili"]

try:
    df = get_anime_list()
    anime_count = len(df) if not df.empty else 0
    last_crawled = ""
    if not df.empty and "last_crawled" in df.columns:
        valid = df[df["last_crawled"] != "未采集"]
        if not valid.empty:
            last_crawled = valid["last_crawled"].iloc[0]
except Exception:
    anime_count = 0
    last_crawled = ""

st.markdown(_HIDE_CSS, unsafe_allow_html=True)

stats_html = '<div class="top-bar">'
stats_html += f'<div class="top-bar-item">📋 共追踪 {st.session_state.platform} <b>{anime_count}</b> 部动画</div>'
if last_crawled:
    stats_html += f'<div class="top-bar-item">🕐 最近采集 {last_crawled}</div>'
stats_html += "</div>"

left_col, right_col = st.columns([6, 1])

with left_col:
    st.markdown(stats_html, unsafe_allow_html=True)

with right_col:
    platform = st.selectbox("平台", platforms, key="global_platform_select", label_visibility="collapsed")
    st.session_state.platform = platform

tab_track, tab_analysis, tab_settings = st.tabs(["🔍 数据追踪", "📈 数据分析", "⚙ 设置"])

with tab_track:
    try:
        render_tracking()
    except Exception as e:
        import traceback as _tb
        _tb.print_exc()
        st.error(f"追踪页面加载失败: {e}")

with tab_analysis:
    try:
        render_analysis()
    except Exception as e:
        import traceback as _tb
        _tb.print_exc()
        st.error(f"分析页面加载失败: {e}")

with tab_settings:
    try:
        render_settings()
    except Exception as e:
        import traceback as _tb
        _tb.print_exc()
        st.error(f"设置页面加载失败: {e}")
