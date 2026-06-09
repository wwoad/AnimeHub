"""番剧数据中枢 - Web UI 入口

顶栏：Tab 导航(左) + 平台选择(右)，横线分割，条件渲染
"""

from __future__ import annotations

import streamlit as st

from web.pages.analysis import render_analysis
from web.pages.settings import render_settings
from web.pages.tracking import render_tracking
from web.platforms import get_platforms

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
        padding-top: 0.2rem !important;
        padding-left: 1rem !important;
        padding-right: 1rem !important;
        max-width: 100% !important;
    }
    h1, h2, h3 { margin-top: 0 !important; }
</style>
"""

if "platform" not in st.session_state:
    st.session_state.platform = "bilibili"

platforms = get_platforms()
if not platforms:
    platforms = ["bilibili"]

st.markdown(_HIDE_CSS, unsafe_allow_html=True)

tab_col, plat_col = st.columns([4, 0.6])

with tab_col:
    tab = st.segmented_control(
        "导航",
        ["🔍 数据追踪", "📈 数据分析", "⚙ 设置"],
        default="🔍 数据追踪",
        label_visibility="collapsed",
    )

with plat_col:
    platform = st.selectbox("平台", platforms, key="global_platform_select", label_visibility="collapsed")
    st.session_state.platform = platform

st.markdown('<hr style="margin:0;border-color:rgba(128,128,128,0.15)">', unsafe_allow_html=True)

if tab == "🔍 数据追踪":
    try:
        render_tracking()
    except Exception as e:
        import traceback as _tb
        _tb.print_exc()
        st.error(f"追踪页面加载失败: {e}")

elif tab == "📈 数据分析":
    try:
        render_analysis()
    except Exception as e:
        import traceback as _tb
        _tb.print_exc()
        st.error(f"分析页面加载失败: {e}")

elif tab == "⚙ 设置":
    try:
        render_settings()
    except Exception as e:
        import traceback as _tb
        _tb.print_exc()
        st.error(f"设置页面加载失败: {e}")
