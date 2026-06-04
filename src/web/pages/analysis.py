"""数据分析: 总览/排行榜/对比/趋势/详情

内容区第一行右侧放平台选择器。
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


def render_analysis() -> None:
    platform = st.session_state.get("platform", "bilibili")

    df = get_anime_list()
    if df.empty:
        st.info("还没有跟踪的动画，去「数据追踪」页添加吧！")
        return

    plat_df = df[df["platform"] == platform] if "platform" in df.columns else df
    if plat_df.empty:
        st.info(f"{platform} 平台暂无跟踪动画")
        return

    sub1, sub2, sub3, sub4 = st.tabs(["🏆 排行榜", "📊 对比分析", "📈 增长趋势", "🎯 聚焦分析"])

    with sub1:
        _ranking(plat_df, platform)

    with sub2:
        _compare(plat_df, platform)

    with sub3:
        _trend(plat_df, platform)

    with sub4:
        _detail(plat_df, platform)


def _ranking(df: pd.DataFrame, platform: str) -> None:
    st.subheader(f"{platform} 排行榜")

    metric = st.selectbox("排行指标", list(_METRIC_CN.keys()), format_func=lambda x: _METRIC_CN[x], key=f"rank_metric_{platform}")

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


def _compare(df: pd.DataFrame, platform: str) -> None:
    st.subheader(f"{platform} 对比分析")

    all_titles = df["title"].tolist()
    selected = st.multiselect("选择要对比的动画（最多5部）", all_titles, default=all_titles[: min(2, len(all_titles))], key=f"compare_sel_{platform}")

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
    fig = px.bar(melt_df, x="指标", y="数值", color="标题", barmode="group", title="多动画指标对比")
    st.plotly_chart(fig, width="stretch")


def _trend(df: pd.DataFrame, platform: str) -> None:
    st.subheader(f"{platform} 增长趋势")

    titles = df["title"].tolist()
    if not titles:
        st.info("暂无动画数据")
        return

    idx = st.selectbox("选择动画", range(len(titles)), format_func=lambda i: titles[i], key=f"trend_sel_{platform}")
    season_id = int(df.iloc[idx]["season_id"])

    history_df = get_anime_stat_history(season_id, days=30)
    if history_df.empty:
        st.info("暂无历史数据，需多次爬取后才能查看趋势")
        return

    metric = st.selectbox("趋势指标", ["播放量", "追番", "弹幕", "点赞", "投币"], key=f"trend_metric_{platform}")
    if metric not in history_df.columns:
        st.warning(f"数据中没有 {metric} 字段")
        return

    fig = px.line(history_df, x="时间", y=metric, title=f"{titles[idx]} - {metric}趋势")
    st.plotly_chart(fig, width="stretch")
    st.dataframe(history_df.astype(str), width="stretch", hide_index=True)


def _detail(df: pd.DataFrame, platform: str) -> None:
    titles_list = df["title"].tolist()
    if not titles_list:
        st.info("该平台暂无动画")
        return

    cols = st.columns([2, 1, 1, 1, 1, 1, 1, 1, 1])
    with cols[0]:
        idx = st.selectbox("动画", range(len(titles_list)), format_func=lambda i: titles_list[i], key=f"detail_sel_{platform}")

    season_id = int(df.iloc[idx]["season_id"])
    detail = get_anime_detail(season_id)

    if not detail:
        st.warning("未找到该动画的数据")
        return

    anime, stat, episodes = detail["anime"], detail["stat"], detail["episodes"]

    with cols[1]:
        st.metric("评分", f"{anime.rating_score}({anime.rating_count}人)")
    with cols[2]:
        st.metric("集数", anime.total_episodes)
    with cols[3]:
        st.metric("播放量", _fmt(stat.views) if stat else "暂无")
    with cols[4]:
        st.metric("弹幕", _fmt(stat.danmaku) if stat else "暂无")
    with cols[5]:
        st.metric("点赞", _fmt(stat.likes) if stat else "暂无")
    with cols[6]:
        st.metric("收藏", _fmt(stat.favorite) if stat else "暂无")
    with cols[7]:
        st.metric("分享", _fmt(stat.share) if stat else "暂无")
    with cols[8]:
        st.metric("状态", "完结" if anime.is_finish else "连载中")

    st.markdown(f"**风格**: {anime.styles or '无'}")
    st.markdown(f"**简介**: {anime.evaluate or '无'}")

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
                display[col] = display[col].astype(str).apply(lambda v: _fmt(int(v)) if v.isdigit() and int(v) >= 10_000 else v)
        st.dataframe(display, width="stretch", height=max(200, min(400, 40 + len(display) * 35)), hide_index=True)
    else:
        st.info("暂无集数数据")
