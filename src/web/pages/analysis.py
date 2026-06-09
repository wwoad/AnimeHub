"""数据分析: 平台总览/排行榜/聚焦分析"""

from __future__ import annotations

import re

import pandas as pd
import plotly.express as px
import streamlit as st

from web.pages._fanren_monitor_app import render_fanren_monitor
from web.queries import get_anime_detail, get_anime_list, get_anime_stat_history, get_daily_deltas, get_main_ep_total_duration


def _fmt(n: int) -> str:
    if n >= 100_000_000:
        return f"{n / 100_000_000:.1f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return f"{n:,}"


def _fmt_label(n: float) -> str:
    if n == 0:
        return "0"
    if n < 1:
        return f"{n * 100:.1f}%"
    if n >= 100_000_000:
        return f"{n / 100_000_000:.2f}亿"
    if n >= 10_000:
        return f"{n / 10_000:.1f}万"
    return f"{n:,.0f}"


_METRIC_CN = {"views": "播放量", "follow": "追番", "danmaku": "弹幕", "reply": "评论", "likes": "点赞", "coins": "投币"}
_BAR_COLOR = "#448AFF"
_PIE_COLORS = px.colors.qualitative.Dark24

_CHART_DARK = dict(
    template="plotly_dark",
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
)


def _build_rank_table(top: pd.DataFrame, metric: str, rank_mode: str) -> pd.DataFrame:
    show = top[["title"]].copy()
    show.columns = ["标题"]

    if rank_mode == "比率":
        show[metric] = top[metric].apply(lambda x: f"{x:.1f}%")
    elif rank_mode == "特殊统计":
        show[metric] = top[metric].apply(lambda x: f"{x * 100:.1f}%")
    else:
        show[metric] = top[metric].apply(lambda x: _fmt(int(x)))

    return show


@st.cache_data(ttl=300)
def _get_decay_ratios(metric_cn: str) -> pd.DataFrame:
    """查询留存率: 后半集均 ÷ 首集（按集号 title 排序定位首集）"""
    from collections import defaultdict

    from sqlalchemy import func

    from db.session import get_session_factory
    from models.episode import Episode
    from models.episode_stat import EpisodeStat

    col_map = {"播放量": "views", "弹幕": "danmaku", "评论": "reply", "点赞": "likes", "投币": "coins", "收藏": "favorite", "分享": "share"}
    col = col_map.get(metric_cn)
    if col is None:
        return pd.DataFrame()

    Session = get_session_factory()
    with Session() as session:
        eps = session.query(Episode).all()
        if not eps:
            return pd.DataFrame()

        anime_eps: dict[int, list[tuple[int, int]]] = defaultdict(list)
        all_ep_ids = []
        for ep in eps:
            try:
                ep_num = int(ep.title.strip())
            except (ValueError, AttributeError):
                ep_num = 0
            anime_eps[ep.anime_id].append((ep_num, ep.ep_id))
            all_ep_ids.append(ep.ep_id)

        if not all_ep_ids:
            return pd.DataFrame()

        subq = session.query(EpisodeStat.episode_id, func.max(EpisodeStat.id).label("max_id")).filter(EpisodeStat.episode_id.in_(all_ep_ids)).group_by(EpisodeStat.episode_id).subquery()
        stats = session.query(EpisodeStat).join(subq, EpisodeStat.id == subq.c.max_id).all()
        stat_map = {s.episode_id: getattr(s, col, 0) for s in stats}

    rows = []
    for sid, eps_list in anime_eps.items():
        if len(eps_list) < 4:
            continue
        eps_list.sort(key=lambda x: x[0])
        first_ep_id = eps_list[0][1]
        first_val = stat_map.get(first_ep_id, 0)
        if first_val <= 0:
            continue
        rest = [e[1] for e in eps_list[1:]]
        half_start = len(rest) // 2
        half_vals = [stat_map.get(eid, 0) for eid in rest[half_start:] if stat_map.get(eid, 0) > 0]
        if len(half_vals) < 2:
            continue
        half_avg = sum(half_vals) / len(half_vals)
        decay = round(half_avg / first_val, 4)
        rows.append({"season_id": sid, "decay_ratio": decay})

    return pd.DataFrame(rows)


def _merge_series(df: pd.DataFrame) -> pd.DataFrame:
    """合并同系列动画：按根标题（去「第X季」「之XXX」后缀）分组，播放量等求和，追番取最大值"""
    import re

    df = df.copy()

    _season_re = re.compile(r"\s*第[\d零一二三四五六七八九十.]+季$")
    _branch_re = re.compile(r"之[\u4e00-\u9fa5]{3,}$")
    _dot_re = re.compile(r"[·：][\u4e00-\u9fa5]+$")
    _arc_re = re.compile(r"\s[\u4e00-\u9fa5]+篇$")
    _num_re = re.compile(r"\s*\d+$")
    _code_re = re.compile(r"\s*[A-Za-z]+$")

    def _root_title(t: str) -> str:
        return _code_re.sub("", _num_re.sub("", _arc_re.sub("", _dot_re.sub("", _branch_re.sub("", _season_re.sub("", str(t)))))))

    df["_root"] = df["title"].apply(_root_title)
    df["_group_key"] = df["season_type"].astype(str) + "_" + df["_root"]

    _sum_cols = ["播放量", "弹幕", "评论", "点赞", "投币", "收藏", "分享", "集数", "正片集数"]
    _max_cols = ["追番"]
    agg: dict[str, str] = {}
    for c in _sum_cols:
        if c in df.columns:
            agg[c] = "sum"
    for c in _max_cols:
        if c in df.columns:
            agg[c] = "max"
    for c in df.columns:
        if c not in agg and c not in ("_group_key", "_root"):
            agg[c] = "first"

    merged = df.groupby("_group_key", as_index=False).agg(agg)
    for c in _sum_cols + _max_cols:
        if c in merged.columns:
            merged[c] = pd.to_numeric(merged[c], errors="coerce").fillna(0).astype("int64")
    merged["title"] = merged["title"].apply(_root_title)
    merged.drop(columns=["_group_key", "_root"], inplace=True)
    return merged


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

    tab_overview, tab_ranking, tab_focus, tab_fanren = st.tabs(["📊 平台总览", "🏆 排行榜", "🎯 聚焦分析", "📡 凡人监控"])

    with tab_overview:
        _overview(plat_df, platform)

    with tab_ranking:
        _ranking(plat_df, platform)

    with tab_focus:
        _focus(plat_df, platform)

    with tab_fanren:
        render_fanren_monitor()


def _fmt_duration(ms: int) -> str:
    """将毫秒格式化为可读时长"""
    hours = ms // 3_600_000
    if hours >= 10_000:
        return f"{hours / 10_000:.1f}万小时"
    return f"{hours:,}小时"


def _delta_str(delta: int, pct: float) -> str | None:
    """格式化为 st.metric 可用的增量字符串"""
    if delta == 0:
        return None
    return f"{_fmt(abs(delta))} ({abs(pct)}‰)"


def _overview(df: pd.DataFrame, platform: str) -> None:

    st.subheader(f"{platform} 平台总览")

    total = len(df)
    total_views = int(df["views"].sum())
    total_follow = int(df["follow"].sum())
    total_danmaku = int(df["danmaku"].sum())
    total_reply = int(df["reply"].sum()) if "reply" in df.columns else 0
    total_likes = int(df["likes"].sum())
    total_coins = int(df["coins"].sum())
    finished = int((df["is_finish"] == "完结").sum())
    airing = int((df["is_finish"] == "连载中").sum())

    main_ep_count = int(df["正片集数"].sum()) if "正片集数" in df.columns else 0
    avg_rating = round(df["rating_score"].mean(), 1) if "rating_score" in df.columns else 0.0
    success_count = int((df["status"] == "成功").sum())
    health_pct = round(success_count / total * 100, 1) if total > 0 else 0

    season_ids = df["season_id"].tolist()
    deltas = get_daily_deltas(season_ids)
    duration_ms = get_main_ep_total_duration(season_ids)

    flow_metrics = [
        ("▶ 总播放量", _fmt(total_views), "views"),
        ("❤ 总追番", _fmt(total_follow), "follow"),
        ("💬 总弹幕", _fmt(total_danmaku), "danmaku"),
        ("📝 总评论", _fmt(total_reply), "reply"),
        ("👍 总点赞", _fmt(total_likes), "likes"),
        ("🪙 总投币", _fmt(total_coins), "coins"),
    ]

    cols = st.columns(6)
    for col, (label, value, key) in zip(cols, flow_metrics):
        with col:
            delta_val = deltas.get(f"{key}_delta", 0)
            delta_pct = deltas.get(f"{key}_pct", 0)
            d = _delta_str(delta_val, delta_pct)
            st.metric(label, value, delta=d)

    cols2 = st.columns(6)
    asset_metrics = [
        ("🎬 追踪动画", str(total)),
        ("✅ 完结 / 连载", f"{finished}/{airing}"),
        ("📺 正片集数", _fmt(main_ep_count)),
        ("⏱ 视频总时长", _fmt_duration(duration_ms)),
        ("⭐ 平均评分", f"{avg_rating:.1f}"),
        ("🏥 采集健康", f"✅ {health_pct}%"),
    ]
    for col, (label, value) in zip(cols2, asset_metrics):
        with col:
            st.metric(label, value)

    st.markdown("---")

    dist_cols = st.columns(2)
    with dist_cols[0]:
        area_counts = df["area"].value_counts()
        if not area_counts.empty:
            fig = px.pie(values=area_counts.values, names=area_counts.index, title="地区分布", color_discrete_sequence=_PIE_COLORS)
            fig.update_layout(height=250, margin=dict(l=20, r=20, t=30, b=10), **_CHART_DARK)
            st.plotly_chart(fig, width="stretch")

    with dist_cols[1]:
        status_counts = df["is_finish"].value_counts()
        if not status_counts.empty:
            fig = px.pie(values=status_counts.values, names=status_counts.index, title="状态分布", color_discrete_sequence=_PIE_COLORS)
            fig.update_layout(height=250, margin=dict(l=20, r=20, t=30, b=10), **_CHART_DARK)
            st.plotly_chart(fig, width="stretch")

    st.markdown("---")
    st.markdown("**全部动画数据**")
    display_cols = ["title", "area", "rating_score", "total_episodes", "views", "follow", "danmaku", "status"]
    show = df[[c for c in display_cols if c in df.columns]].copy()
    show.columns = ["标题", "地区", "评分", "集数", "播放量", "追番", "弹幕", "状态"]
    for c in ["播放量", "追番", "弹幕"]:
        if c in show.columns:
            show[c] = show[c].apply(lambda x: _fmt(int(x)))
    if "评分" in show.columns:
        show["评分"] = show["评分"].apply(lambda x: f"{float(x):.1f}")
    st.dataframe(show, width="stretch", height=max(200, min(400, 40 + len(show) * 35)), hide_index=True)


def _ranking(df: pd.DataFrame, platform: str) -> None:
    st.subheader(f"{platform} 排行榜")

    df = df.rename(
        columns={
            "views": "播放量",
            "follow": "追番",
            "danmaku": "弹幕",
            "reply": "评论",
            "likes": "点赞",
            "coins": "投币",
            "favorite": "收藏",
            "share": "分享",
            "rating_score": "评分",
            "total_episodes": "集数",
            "main_ep_count": "正片集数",
        }
    )

    _ml_re = re.compile(r"(?:日语|粤配|粤语|英语|英文|国语|中配|日配|国配|原声)版?$")
    df = df[~df["title"].str.contains(_ml_re, na=False, regex=True)]

    df = df[~((df["season_type"] == 4) & (df["集数"] == 0))]

    raw_metrics = ["播放量", "追番", "弹幕", "评论", "点赞", "投币", "收藏", "分享"]
    ratio_metrics = ["播放量", "追番", "弹幕", "评论", "点赞", "投币", "收藏", "分享"]
    rank_modes = ["原始数据", "比率", "特殊统计"]

    col_area, col_type, col_mode, col_metric, col_mode2, col_sort, col_merge, col_search, col_topn = st.columns([0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 1.8])
    with col_area:
        area_opt = st.selectbox("地区", ["全部", "中国", "国外"], key="rank_area")
    with col_type:
        type_opt = st.selectbox("类型", ["全部", "番剧", "电影"], key="rank_type")
    with col_mode:
        rank_mode = st.selectbox("模式", rank_modes, key="rank_mode")
    with col_sort:
        rank_asc = st.selectbox("排序", ["降序", "升序"], key="rank_asc") == "升序"
    with col_merge:
        rank_merge = st.selectbox("系列", ["分开展示", "合并系列"], disabled=rank_mode == "特殊统计", key="rank_merge")
    with col_search:
        search_text = st.text_input("搜索", key="rank_search", placeholder="选中动画名称...")
    with col_topn:
        top_n = st.slider("显示作品数", 5, 50, 20, key="rank_topn")

    if area_opt == "中国":
        df = df[df["area"].str.startswith("中国", na=False)]
    elif area_opt == "国外":
        df = df[df["area"].notna() & (df["area"] != "") & ~df["area"].str.startswith("中国", na=False)]
    if type_opt == "番剧":
        df = df[df["season_type"] == 4]
    elif type_opt == "电影":
        df = df[df["season_type"] == 2]

    if rank_merge == "合并系列" and rank_mode != "特殊统计":
        df = _merge_series(df)

    if type_opt == "全部" and "season_type" in df.columns:
        _mask = df["season_type"] == 2
        df.loc[_mask, "title"] = "*" + df.loc[_mask, "title"]

    if rank_mode == "原始数据":
        with col_metric:
            metric = st.selectbox("指标", raw_metrics, index=0, key="rank_metric")
        with col_mode2:
            submode = st.selectbox("取值", ["总数", "集均"], key="rank_submode")
        if submode == "集均":
            col_name = f"集均{metric}"
            df[col_name] = (df[metric] / df["正片集数"].replace(0, 1)).round(2)
            metric = col_name
    elif rank_mode == "比率":
        with col_metric:
            num = st.selectbox("分子", ratio_metrics, index=3, key="rank_num")
        with col_mode2:
            den = st.selectbox("分母", ratio_metrics, index=0, key="rank_den")
            use_per_ep = st.checkbox("☑集均(分子)", key="rank_ratio_eperp")
        if use_per_ep:
            col_name = f"集均{num}/{den}"
            df[col_name] = ((df[num] / df["正片集数"].replace(0, 1)) / df[den].replace(0, 1) * 100).round(2)
        else:
            col_name = f"{num}/{den}"
            df[col_name] = (df[num] / df[den].replace(0, 1) * 100).round(2)
        metric = col_name
    else:
        with col_metric:
            decay_metric = st.selectbox("指标", ratio_metrics, index=0, key="rank_decay_metric")
        decay_df = _get_decay_ratios(decay_metric)
        if decay_df.empty:
            st.warning("暂无首集衰减数据，需先爬取动画的集数数据")
            return
        metric = f"留存率({decay_metric})"
        df = df.merge(decay_df, on="season_id", how="left")
        df[metric] = df["decay_ratio"].fillna(0)
        top = df.nlargest(top_n, metric)
        if not rank_asc:
            top = top.iloc[::-1]
        top["_label"] = top[metric].apply(_fmt_label)
        max_val = top[metric].max()
        if pd.isna(max_val):
            st.info("当前筛选条件下无数据")
            return
        title_text = f"Top{top_n} {metric}"
        top["_match"] = top["title"].str.contains(search_text, case=False, na=False) if search_text else False
        top["_rank"] = list(range(len(top), 0, -1))
        fig = px.bar(top, x=metric, y="title", orientation="h", title=title_text, text=top["_label"], custom_data=["_rank"], labels={"title": "动画标题"})
        fig.data[0].marker.color = ["#FFB300" if m else _BAR_COLOR for m in top["_match"]]
        fig.update_layout(yaxis={"categoryorder": "trace"}, height=750, bargap=0.15, title_x=0.5, title_font=dict(size=14), margin=dict(t=45, b=10, l=10, r=30), **_CHART_DARK)
        fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor="rgba(128,128,128,0.12)", zeroline=False, tickfont=dict(size=10))
        fig.update_yaxes(showgrid=False, zeroline=False, tickfont=dict(size=10))
        fig.update_traces(textposition="outside", textfont=dict(size=10), marker=dict(cornerradius=6, line=dict(width=1, color="rgba(100,180,255,0.25)")), hovertemplate="<b>#%{customdata[0]}</b> %{y}: %{x}<extra></extra>")
        st.plotly_chart(fig, width="stretch")
        if type_opt == "全部":
            st.caption("💡 * 标记为电影")
        with st.expander("📋 数据明细"):
            st.dataframe(_build_rank_table(top, metric, rank_mode), width="stretch", height=max(200, min(500, 40 + len(top) * 35)), hide_index=True)
        return

    top = df.nlargest(top_n, metric)
    if not rank_asc:
        top = top.iloc[::-1]
    top["_label"] = top[metric].apply(_fmt_label)
    max_val = top[metric].max()
    if pd.isna(max_val):
        st.info("当前筛选条件下无数据")
        return

    title_text = f"Top{top_n} {metric}"

    top["_match"] = top["title"].str.contains(search_text, case=False, na=False) if search_text else False
    top["_rank"] = list(range(len(top), 0, -1))
    fig = px.bar(top, x=metric, y="title", orientation="h", title=title_text, text=top["_label"], custom_data=["_rank"], labels={"title": "动画标题"})
    fig.data[0].marker.color = ["#FFB300" if m else _BAR_COLOR for m in top["_match"]]
    fig.update_layout(yaxis={"categoryorder": "trace"}, height=750, bargap=0.15, title_x=0.5, title_font=dict(size=14), margin=dict(t=45, b=10, l=10, r=30), **_CHART_DARK)
    if rank_mode == "原始数据":
        if metric.startswith("集均"):
            step = 10_000_000
            tick_vals = [i * step for i in range(int(max_val / step) + 2)]
            tick_texts = [f"{v / 100_000_000:.1f}亿" for v in tick_vals]
        else:
            step = 500_000_000
            tick_vals = [i * step for i in range(int(max_val / step) + 2)]
            tick_texts = [f"{v // 100_000_000}亿" for v in tick_vals]
        fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor="rgba(128,128,128,0.12)", zeroline=False, tickfont=dict(size=10), tickvals=tick_vals, ticktext=tick_texts)
    else:
        fig.update_xaxes(showgrid=True, gridwidth=1, gridcolor="rgba(128,128,128,0.12)", zeroline=False, tickfont=dict(size=10))
    fig.update_yaxes(showgrid=False, zeroline=False, tickfont=dict(size=10))
    fig.update_traces(textposition="outside", textfont=dict(size=10), marker=dict(cornerradius=6, line=dict(width=1, color="rgba(100,180,255,0.25)")), hovertemplate="<b>#%{customdata[0]}</b> %{y}: %{x}<extra></extra>")
    st.plotly_chart(fig, width="stretch")

    if type_opt == "全部":
        st.caption("💡 * 标记为电影")

    with st.expander("📋 数据明细"):
        st.dataframe(_build_rank_table(top, metric, rank_mode), width="stretch", height=max(200, min(500, 40 + len(top) * 35)), hide_index=True)


def _focus(df: pd.DataFrame, platform: str) -> None:
    titles = df["title"].tolist()
    if not titles:
        st.info("该平台暂无动画")
        return

    idx = st.selectbox("选择动画", range(len(titles)), format_func=lambda i: titles[i], key="focus_sel")
    season_id = int(df.iloc[idx]["season_id"])
    detail = get_anime_detail(season_id)

    if not detail:
        st.warning("未找到该动画的数据")
        return

    anime, stat, episodes = detail["anime"], detail["stat"], detail["episodes"]

    c1, c2 = st.columns([3, 1])
    with c1:
        st.markdown(f"### {anime.title}")
        st.markdown(f"**地区**: {anime.area or '无'} &nbsp;&nbsp; **风格**: {anime.styles or '无'} &nbsp;&nbsp; **状态**: {'✅ 完结' if anime.is_finish else '🔄 连载中'}")
        st.markdown(f"**简介**: {anime.evaluate or '无'}")

    with c2:
        st.markdown(f"**评分**: {anime.rating_score or '暂无'} ({_fmt(anime.rating_count)}人)")
        st.markdown(f"**集数**: {anime.total_episodes}")
        st.markdown(f"**UP主**: {anime.up_name or '无'}")

    st.markdown("---")

    m_cols = st.columns(7)
    metrics_data = [
        ("播放量", stat.views if stat else 0),
        ("追番", stat.follow if stat else 0),
        ("弹幕", stat.danmaku if stat else 0),
        ("点赞", stat.likes if stat else 0),
        ("投币", stat.coins if stat else 0),
        ("收藏", stat.favorite if stat else 0),
        ("分享", stat.share if stat else 0),
    ]
    for col, (label, val) in zip(m_cols, metrics_data):
        with col:
            st.metric(label, _fmt(int(val)) if val else "暂无")

    st.markdown("---")

    if episodes:
        st.subheader(f"集数数据 ({len(episodes)} 集)")

        ep_cols = st.columns([2, 1])
        with ep_cols[0]:
            rows = [
                {
                    "集号": ep.get("title", ""),
                    "标题": ep.get("long_title", ""),
                    "播放量": _fmt(int(ep.get("views", 0))),
                    "弹幕": _fmt(int(ep.get("danmaku", 0))),
                    "点赞": _fmt(int(ep.get("likes", 0))),
                }
                for ep in episodes
            ]
            ep_df = pd.DataFrame(rows)
            st.dataframe(ep_df, width="stretch", height=max(200, min(400, 40 + len(ep_df) * 35)), hide_index=True)

        with ep_cols[1]:
            ep_metric = st.selectbox("集数指标", ["播放量", "弹幕", "点赞"], key="ep_metric")
            ep_chart_data = pd.DataFrame(
                {
                    "集号": [ep.get("title", f"#{i + 1}") for i, ep in enumerate(episodes)],
                    ep_metric: [ep.get({"播放量": "views", "弹幕": "danmaku", "点赞": "likes"}[ep_metric], 0) for ep in episodes],
                }
            )
            ep_chart_data["_label"] = ep_chart_data[ep_metric].apply(_fmt_label)
            emax = ep_chart_data[ep_metric].max()
            estep = 500_000_000
            etick_vals = [i * estep for i in range(int(emax / estep) + 2)]
            etick_texts = [f"{v // 100_000_000}亿" for v in etick_vals]
            fig = px.bar(ep_chart_data, x="集号", y=ep_metric, title=f"各集{ep_metric}分布", text=ep_chart_data["_label"], color_discrete_sequence=[_BAR_COLOR])
            fig.update_layout(height=400, margin=dict(l=10, r=10, t=40, b=50), bargap=0.15, title_font=dict(size=14), **_CHART_DARK)
            fig.update_xaxes(showgrid=False, zeroline=False, tickfont=dict(size=10))
            fig.update_yaxes(showgrid=True, gridwidth=1, gridcolor="rgba(128,128,128,0.12)", zeroline=False, tickfont=dict(size=10), tickvals=etick_vals, ticktext=etick_texts)
            fig.update_traces(textposition="outside", textfont=dict(size=10), marker=dict(cornerradius=6, line=dict(width=1, color="rgba(100,180,255,0.25)")))
            st.plotly_chart(fig, width="stretch")
    else:
        st.info("暂无集数数据")

    st.markdown("---")
    st.subheader("历史趋势")

    history_df = get_anime_stat_history(season_id, days=30)
    if history_df.empty:
        st.info("暂无历史数据，需多次爬取后才能查看趋势")
    else:
        trend_metric = st.selectbox("趋势指标", ["播放量", "追番", "弹幕", "点赞", "投币"], key="trend_metric")
        if trend_metric in history_df.columns:
            fig = px.line(history_df, x="时间", y=trend_metric, title=f"{anime.title} - {trend_metric}趋势", color_discrete_sequence=["#00E5FF"])
            fig.update_layout(height=400, **_CHART_DARK)
            st.plotly_chart(fig, width="stretch")
            with st.expander("查看历史数据表"):
                st.dataframe(history_df, width="stretch", hide_index=True)
