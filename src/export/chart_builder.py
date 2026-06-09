"""图表生成器

基于 matplotlib 生成4种PNG图表:趋势图、增量图、排行图、增长图。
输出位置: data/{YYYYMMDD}/{platform}/{season_id}/分析/
"""

from __future__ import annotations

import platform as _platform
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import pandas as pd

from core.analyzer import Analyzer
from core.path_manager import ensure_dir, get_analysis_dir, sanitize_title


def _setup_chinese_font() -> None:
    """自动检测系统中文字体, 避免中文乱码"""
    system = _platform.system()
    if system == "Windows":
        font_candidates = ["SimHei", "Microsoft YaHei", "SimSun", "FangSong"]
    elif system == "Darwin":
        font_candidates = ["PingFang SC", "Heiti SC", "STHeiti"]
    else:
        font_candidates = ["WenQuanYi Micro Hei", "Noto Sans CJK SC", "Droid Sans Fallback"]

    from matplotlib.font_manager import fontManager

    available_fonts = {f.name for f in fontManager.ttflist}
    for font_name in font_candidates:
        if font_name in available_fonts:
            matplotlib.rcParams["font.sans-serif"] = [font_name, "DejaVu Sans"]
            break
    matplotlib.rcParams["axes.unicode_minus"] = False


_setup_chinese_font()


class ChartBuilder:
    """图表生成器

    生成4种图表:
    - 趋势图:播放量增长曲线
    - 增量图:每日数据增量柱状图
    - 排行图:单集播放量排行
    - 增长图:播放量+追番数双轴趋势
    """

    def __init__(self) -> None:
        self.analyzer = Analyzer()

    def build_all(self, season_id: int, name: str | None = None, platform: str = "bilibili") -> list[Path]:
        """生成动画的所有图表"""
        overview = self.analyzer.get_anime_overview(season_id)
        if overview.empty:
            print(f"未找到 season_id={season_id} 的数据")
            return []

        title = overview.iloc[0].get("标题", str(season_id)) if name is None else name
        platform = overview.iloc[0].get("平台", platform)
        title_slug = sanitize_title(title)
        out_dir = ensure_dir(get_analysis_dir(title_slug, platform))

        paths = []
        diff = self.analyzer.get_daily_diff(season_id, days=30)
        if not diff.empty:
            p = self._plot_daily_trend(diff, title_slug, out_dir)
            if p:
                paths.append(p)
            p = self._plot_daily_increment(diff, title_slug, out_dir)
            if p:
                paths.append(p)

        rank = self.analyzer.get_episode_rank(season_id)
        if not rank.empty:
            p = self._plot_episode_rank(rank, title_slug, out_dir)
            if p:
                paths.append(p)

        if not diff.empty and "总播放量" in diff.columns:
            p = self._plot_total_growth(diff, title_slug, out_dir)
            if p:
                paths.append(p)

        return paths

    def _plot_daily_trend(self, diff: pd.DataFrame, safe_title: str, out_dir: Path) -> Path | None:
        """播放量增长趋势折线图"""
        if "总播放量" not in diff.columns or diff.empty:
            return None
        fig, ax = plt.subplots(figsize=(12, 6))
        ax.plot(diff["日期"], diff["总播放量"], "b-o", markersize=4, linewidth=2)
        ax.set_title("播放量增长趋势", y=1.02)
        ax.set_xlabel("日期")
        ax.set_ylabel("累计播放量")
        ax.ticklabel_format(axis="y", style="plain")
        ax.yaxis.set_major_formatter(
            lambda x, _: f"{x / 1e8:.2f}亿" if x >= 1e8 else f"{x / 1e4:.1f}万" if x >= 1e4 else str(int(x))
        )
        fig.autofmt_xdate()
        fig.tight_layout()
        path = out_dir / f"{safe_title}_趋势.png"
        fig.savefig(str(path), dpi=150)
        plt.close(fig)
        print(f"图表已保存: {path}")
        return path

    def _plot_daily_increment(self, diff: pd.DataFrame, safe_title: str, out_dir: Path) -> Path | None:
        """每日数据增量分组柱状图"""
        fig, ax = plt.subplots(figsize=(12, 6))
        metrics = {
            "播放量增量": ("播放增量", "#2196F3"),
            "点赞增量": ("点赞增量", "#FF9800"),
            "投币增量": ("投币增量", "#FFC107"),
        }
        x = range(len(diff))
        bar_width = 0.25
        for i, (col, (label, color)) in enumerate(metrics.items()):
            if col in diff.columns:
                ax.bar(
                    [xi + i * bar_width for xi in x],
                    diff[col],
                    bar_width,
                    label=label,
                    color=color,
                )

        ax.set_title("每日数据增量", y=1.02)
        ax.set_xlabel("日期")
        ax.set_ylabel("数量")
        ax.set_xticks([xi + bar_width for xi in x])
        ax.set_xticklabels(diff["日期"], rotation=45, ha="right")
        ax.legend()
        fig.tight_layout()
        path = out_dir / f"{safe_title}_增量.png"
        fig.savefig(str(path), dpi=150)
        plt.close(fig)
        print(f"图表已保存: {path}")
        return path

    def _plot_episode_rank(self, rank: pd.DataFrame, safe_title: str, out_dir: Path) -> Path | None:
        """单集播放量排行水平柱状图"""
        top = rank.head(20)
        if "播放量" not in top.columns or top.empty:
            return None

        labels = [
            f"第{row['集号']}集 {row['副标题']}" if "副标题" in top.columns else f"第{row['集号']}集"
            for _, row in top.iterrows()
        ]

        fig, ax = plt.subplots(figsize=(12, max(6, len(top) * 0.4)))
        y_pos = range(len(top))
        ax.barh(y_pos, top["播放量"], color="#2196F3")
        ax.set_yticks(y_pos)
        ax.set_yticklabels(labels)
        ax.set_xlabel("播放量")
        ax.set_title("单集播放量排行 Top20", y=1.02)
        ax.ticklabel_format(axis="x", style="plain")
        ax.xaxis.set_major_formatter(
            lambda x, _: f"{x / 1e8:.2f}亿" if x >= 1e8 else f"{x / 1e4:.1f}万" if x >= 1e4 else str(int(x))
        )
        ax.invert_yaxis()
        fig.tight_layout()
        path = out_dir / f"{safe_title}_排行.png"
        fig.savefig(str(path), dpi=150)
        plt.close(fig)
        print(f"图表已保存: {path}")
        return path

    def _plot_total_growth(self, diff: pd.DataFrame, safe_title: str, out_dir: Path) -> Path | None:
        """播放量+追番数双轴趋势图"""
        fig, ax1 = plt.subplots(figsize=(12, 6))

        ax1.plot(diff["日期"], diff["总播放量"], "b-o", markersize=4, linewidth=2, label="总播放量")
        ax1.set_ylabel("总播放量", color="b")
        ax1.ticklabel_format(axis="y", style="plain")
        ax1.yaxis.set_major_formatter(lambda x, _: f"{x / 1e8:.2f}亿" if x >= 1e8 else f"{x / 1e4:.1f}万")

        fav_col = "追番/订阅数"
        if fav_col in diff.columns:
            ax2 = ax1.twinx()
            ax2.plot(diff["日期"], diff[fav_col], "r-s", markersize=4, linewidth=2, label="追番/订阅数")
            ax2.set_ylabel("追番/订阅数", color="r")

        ax1.set_title("播放量 & 追番数趋势", y=1.02)
        ax1.set_xlabel("日期")
        fig.autofmt_xdate()
        fig.tight_layout()
        path = out_dir / f"{safe_title}_增长.png"
        fig.savefig(str(path), dpi=150)
        plt.close(fig)
        print(f"图表已保存: {path}")
        return path