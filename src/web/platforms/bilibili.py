"""B站平台专用渲染

包含B站特有的简报、分析、追踪界面逻辑。
"""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from web.queries import get_anime_detail, get_anime_list, get_anime_stat_history


def _fmt(n: int) -> str:
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return f"{n:,}"


_METRIC_CN = {"views": "播放量", "follow": "追番", "danmaku": "弹幕", "likes": "点赞", "coins": "投币"}


def render_briefing() -> None:
    df = get_anime_list()
    plat_df = df[df["platform"] == "bilibili"] if "platform" in df.columns else df

    if plat_df.empty:
        st.info("bilibili 平台暂无跟踪动画，去「数据追踪」页添加吧！")
        return

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("总播放量", _fmt(int(plat_df["views"].sum())))
    with c2:
        st.metric("总追番", _fmt(int(plat_df["follow"].sum())))
    with c3:
        st.metric("连载中", f"{(plat_df['is_finish'] == '连载中').sum()}")
    with c4:
        st.metric("已完结", f"{(plat_df['is_finish'] == '完结').sum()}")

    st.markdown("---")

    col_info = {
        "title": "标题",
        "area": "地区",
        "rating_score": "评分",
        "total_episodes": "集数",
        "views": "播放量",
        "follow": "追番",
        "danmaku": "弹幕",
        "is_finish": "状态",
        "status": "采集状态",
        "last_crawled": "上次采集",
    }
    avail = [c for c in col_info if c in plat_df.columns]
    st.dataframe(plat_df[avail].astype(str).rename(columns=col_info), width="stretch", height=max(200, min(400, 40 + len(plat_df) * 35)), hide_index=True)

    st.markdown("---")
    st.subheader("动画详情")

    titles = plat_df["title"].tolist()
    idx = st.selectbox("选择动画", range(len(titles)), format_func=lambda i: titles[i], key="brief_sel_bilibili")
    season_id = int(plat_df.iloc[idx]["season_id"])
    detail = get_anime_detail(season_id)

    if not detail:
        st.warning("未找到该动画的数据")
        return

    anime, stat, episodes = detail["anime"], detail["stat"], detail["episodes"]

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("评分", f"{anime.rating_score}")
    with c2:
        st.metric("集数", anime.total_episodes)
    with c3:
        st.metric("播放量", _fmt(stat.views) if stat else "暂无")
    with c4:
        st.metric("追番", _fmt(stat.follow) if stat else "暂无")
    with c5:
        st.metric("地区", anime.area or "未知")

    ic1, ic2 = st.columns(2)
    with ic1:
        st.markdown(f"**状态**: {'完结' if anime.is_finish else '连载中'}  |  **风格**: {anime.styles or '未知'}")
    with ic2:
        st.markdown(f"**创作者**: {anime.up_name or '未知'}  |  **简介**: {(anime.evaluate or '无')[:80]}...")

    st.markdown("---")
    st.subheader("统计趋势")

    history_df = get_anime_stat_history(season_id, days=30)
    if not history_df.empty:
        c1, c2 = st.columns(2)
        with c1:
            st.plotly_chart(px.line(history_df, x="时间", y=["播放量", "追番"], title="播放量与追番"), width="stretch")
        with c2:
            st.plotly_chart(px.line(history_df, x="时间", y=["弹幕", "点赞", "投币"], title="互动数据"), width="stretch")
    else:
        st.info("暂无历史统计数据，需多次爬取后才能绘制趋势图")

    st.markdown("---")
    st.subheader(f"正片数据 ({len(episodes)} 集)")

    if episodes:
        rows = [
            {
                "集号": ep.get("title", ""),
                "标题": ep.get("long_title", ""),
                "BV号": ep.get("bvid", ""),
                "播放量": ep.get("views", 0),
                "弹幕": ep.get("danmaku", 0),
                "评论": ep.get("reply", 0),
                "收藏": ep.get("favorite", 0),
                "点赞": ep.get("likes", 0),
                "投币": ep.get("coins", 0),
                "分享": ep.get("share", 0),
            }
            for ep in episodes
        ]
        ep_df = pd.DataFrame(rows)
        display = ep_df.copy()
        for col in ["播放量", "弹幕", "评论", "收藏", "点赞", "投币", "分享"]:
            if col in display.columns:
                display[col] = display[col].apply(lambda v: _fmt(v) if isinstance(v, (int, float)) and v >= 10_000 else v)
        st.dataframe(display, width="stretch", height=max(200, min(400, 40 + len(display) * 35)), hide_index=True)
    else:
        st.info("暂无集数数据")


def render_analysis() -> None:
    df = get_anime_list()
    plat_df = df[df["platform"] == "bilibili"] if "platform" in df.columns else df

    if plat_df.empty:
        st.info("bilibili 平台暂无跟踪动画")
        return

    sub1, sub2, sub3 = st.tabs(["🏆 排行榜", "📊 对比分析", "📈 增长趋势"])

    with sub1:
        _ranking(plat_df)

    with sub2:
        _compare(plat_df)

    with sub3:
        _trend(plat_df)


def _ranking(df: pd.DataFrame) -> None:
    st.subheader("bilibili 排行榜")
    metric = st.selectbox("排行指标", list(_METRIC_CN.keys()), format_func=lambda x: _METRIC_CN[x], key="bilibili_rank_metric")
    top = df.sort_values(metric, ascending=False).head(20)

    rank_data = pd.DataFrame(
        {
            "排名": range(1, len(top) + 1),
            "标题": top["title"].values,
            "地区": top["area"].values if "area" in top.columns else [""] * len(top),
            _METRIC_CN[metric]: top[metric].values,
        }
    )

    fig = px.bar(rank_data, x=_METRIC_CN[metric], y="标题", orientation="h", title=f"TOP {len(top)} {_METRIC_CN[metric]}排行")
    fig.update_layout(yaxis={"categoryorder": "total ascending"})
    st.plotly_chart(fig, width="stretch")
    st.dataframe(rank_data.astype(str), width="stretch", height=max(200, min(500, 40 + len(rank_data) * 35)), hide_index=True)


def _compare(df: pd.DataFrame) -> None:
    st.subheader("bilibili 对比分析")
    all_titles = df["title"].tolist()
    selected = st.multiselect("选择要对比的动画（最多5部）", all_titles, default=all_titles[: min(2, len(all_titles))], key="bilibili_compare_sel")

    if len(selected) < 2:
        st.info("请选择至少2部动画进行对比")
        return

    metrics = ["views", "follow", "danmaku", "likes", "coins"]
    compare_rows = []
    for _, row in df[df["title"].isin(selected)].iterrows():
        entry = {"标题": row["title"]}
        for m in metrics:
            entry[_METRIC_CN[m]] = int(row[m]) if m in row.index and pd.notna(row[m]) else 0
        compare_rows.append(entry)

    compare_df = pd.DataFrame(compare_rows)
    st.dataframe(compare_df.astype(str), width="stretch", hide_index=True)

    melt_df = compare_df.melt(id_vars=["标题"], value_vars=[_METRIC_CN[m] for m in metrics], var_name="指标", value_name="数值")
    st.plotly_chart(px.bar(melt_df, x="指标", y="数值", color="标题", barmode="group", title="多动画指标对比"), width="stretch")


def _trend(df: pd.DataFrame) -> None:
    st.subheader("bilibili 增长趋势")
    titles = df["title"].tolist()
    if not titles:
        st.info("暂无动画数据")
        return

    idx = st.selectbox("选择动画", range(len(titles)), format_func=lambda i: titles[i], key="bilibili_trend_sel")
    season_id = int(df.iloc[idx]["season_id"])

    history_df = get_anime_stat_history(season_id, days=30)
    if history_df.empty:
        st.info("暂无历史数据，需多次爬取后才能查看趋势")
        return

    metric = st.selectbox("趋势指标", ["播放量", "追番", "弹幕", "点赞", "投币"], key="bilibili_trend_metric")
    if metric not in history_df.columns:
        st.warning(f"数据中没有 {metric} 字段")
        return

    fig = px.line(history_df, x="时间", y=metric, title=f"{titles[idx]} - {metric}趋势")
    st.plotly_chart(fig, width="stretch")
    st.dataframe(history_df.astype(str), width="stretch", hide_index=True)


def render_tracking() -> None:
    st.info("B站数据追踪请使用主界面的数据追踪Tab")
