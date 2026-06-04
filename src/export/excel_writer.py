"""Excel 报告导出

将 Analyzer 查询结果导出为多 Sheet 的 Excel 文件。
输出位置: data/{YYYYMMDD}/{platform}/{season_id}/分析/report.xlsx
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from core.analyzer import Analyzer
from core.path_manager import ensure_dir, get_analysis_dir, sanitize_title


def _format_large_number(val):
    """格式化大数字为可读字符串"""
    if not isinstance(val, (int, float)):
        return val
    if abs(val) >= 100_000_000:
        return f"{val / 100_000_000:.2f}亿"
    if abs(val) >= 10_000:
        return f"{val / 10_000:.2f}万"
    return str(val)


class ExcelWriter:
    """Excel 报告导出器

    生成包含4个Sheet的Excel报告:
    动画总览、单集明细、每日增量、单集排行。
    """

    def __init__(self) -> None:
        self.analyzer = Analyzer()

    def export(self, season_id: int, name: str | None = None, platform: str = "bilibili") -> Path:
        """导出单个动画的完整报告"""
        overview = self.analyzer.get_anime_overview(season_id)
        if overview.empty:
            print(f"未找到 season_id={season_id} 的数据")
            return Path("")

        title = overview.iloc[0].get("标题", str(season_id)) if name is None else name
        platform = overview.iloc[0].get("平台", platform)
        title_slug = sanitize_title(title)
        output_dir = ensure_dir(get_analysis_dir(title_slug, platform))
        output_path = output_dir / f"{title_slug}_report.xlsx"

        detail = self.analyzer.get_episode_detail(season_id)
        diff = self.analyzer.get_daily_diff(season_id)
        rank = self.analyzer.get_episode_rank(season_id)

        with pd.ExcelWriter(str(output_path), engine="openpyxl") as writer:
            overview.to_excel(writer, sheet_name="动画总览", index=False)
            if not detail.empty:
                detail.to_excel(writer, sheet_name="单集明细", index=False)
            if not diff.empty:
                diff.to_excel(writer, sheet_name="每日增量", index=False)
            if not rank.empty:
                rank.to_excel(writer, sheet_name="单集排行", index=True)

            for sheet_name in writer.sheets:
                ws = writer.sheets[sheet_name]
                for col in ws.columns:
                    max_length = 0
                    for cell in col:
                        if cell.value:
                            max_length = max(max_length, len(str(cell.value)))
                    ws.column_dimensions[col[0].column_letter].width = min(max_length + 4, 40)

        print(f"Excel报告已导出: {output_path}")
        return output_path

    def export_all(self) -> list[Path]:
        """导出所有跟踪动画的报告"""
        anime_list = self.analyzer.get_anime_list()
        if anime_list.empty:
            print("没有跟踪中的动画")
            return []

        paths = []
        for _, row in anime_list.iterrows():
            path = self.export(row["season_id"], name=row["title"], platform=row.get("platform", "bilibili"))
            if path and str(path):
                paths.append(path)
        return paths
