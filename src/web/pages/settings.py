"""设置页面: 主题/网络/显示配置"""

from __future__ import annotations

import streamlit as st

from config import get_settings

DARK_CSS = """
<style>
    :root {
        --bg: #1a1a2e;
        --text: #e0e0e0;
        --card: #16213e;
        --border: #0f3460;
    }
    .stApp { background-color: var(--bg) !important; color: var(--text) !important; }
    [data-testid="stDataFrame"] { background-color: var(--card) !important; color: var(--text) !important; }
    .stMetric { background-color: var(--card) !important; }
</style>
"""


def render_settings() -> None:
    st.subheader("⚙ 设置")

    tab_appearance, tab_network, tab_display = st.tabs(["🎨 外观", "🌐 网络", "📊 显示"])

    with tab_appearance:
        st.markdown("#### 主题")
        theme = st.radio(
            "选择主题",
            ["浅色", "深色", "跟随系统"],
            horizontal=True,
            label_visibility="collapsed",
        )

        if theme == "深色":
            st.markdown(DARK_CSS, unsafe_allow_html=True)
            st.session_state["theme"] = "dark"
        elif theme == "跟随系统":
            st.session_state["theme"] = "auto"
        else:
            st.session_state["theme"] = "light"

        st.info("主题选择在当前会话中生效。深色模式为实验性功能，部分组件可能显示不完整。")

    with tab_network:
        settings = get_settings()

        st.markdown("#### 网络配置")
        st.markdown("修改后需重启应用生效")

        request_delay = st.number_input(
            "请求延迟（秒）",
            min_value=0.01,
            max_value=10.0,
            value=float(settings.request_delay),
            step=0.05,
        )
        max_concurrency = st.number_input(
            "最大并发数",
            min_value=1,
            max_value=50,
            value=settings.max_concurrency,
        )
        max_retries = st.number_input(
            "最大重试次数",
            min_value=1,
            max_value=10,
            value=settings.max_retries,
        )
        bilibili_cookie = st.text_input(
            "B站Cookie",
            value=settings.bilibili_cookie or "",
            type="password",
        )

        st.info("修改默认值请通过环境变量 ANIME_REQUEST_DELAY / ANIME_MAX_CONCURRENCY / ANIME_MAX_RETRIES / ANIME_BILIBILI_COOKIE 设置，需重启应用生效")

    with tab_display:
        settings = get_settings()

        st.markdown("#### 显示配置")

        show_progress = st.checkbox("显示进度条", value=settings.show_progress_bar)

        log_keep_days = st.number_input(
            "日志保留天数",
            min_value=1,
            max_value=365,
            value=settings.log_keep_days,
        )

        st.info("修改默认值请通过环境变量 ANIME_SHOW_PROGRESS_BAR / ANIME_LOG_KEEP_DAYS 设置，需重启应用生效")

