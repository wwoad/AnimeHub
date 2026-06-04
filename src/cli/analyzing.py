"""分析管理命令: list / export / diff"""

from __future__ import annotations

import logging

from core.analyzer import Analyzer
from db.session import init_db
from export.chart_builder import ChartBuilder
from export.excel_writer import ExcelWriter

logger = logging.getLogger(__name__)


def cmd_list(_: any) -> None:
    """列出所有跟踪中的动画"""
    init_db()
    analyzer = Analyzer()
    df = analyzer.get_anime_list()
    if df.empty:
        print("没有跟踪中的动画, 请先用 add 或 import 命令添加")
        return
    print(df.to_string(index=False))


def cmd_export(args: any) -> None:
    """导出分析报告"""
    format_type = args.format or "excel"

    if format_type in ("excel", "both"):
        writer = ExcelWriter()
        if args.name:
            analyzer = Analyzer()
            df = analyzer.get_anime_list()
            match = df[df["标题"].str.contains(args.name, na=False)]
            if match.empty:
                print(f"未找到包含 '{args.name}' 的动画")
                return
            for _, row in match.iterrows():
                writer.export(row["动画ID"], name=row["标题"], platform=row.get("平台", "bilibili"))
        elif args.all_tracks:
            writer.export_all()
        else:
            print("请指定 --name 或 --all")
            return

    if format_type in ("charts", "both"):
        builder = ChartBuilder()
        analyzer = Analyzer()
        if args.name:
            df = analyzer.get_anime_list()
            match = df[df["标题"].str.contains(args.name, na=False)]
            if match.empty:
                print(f"未找到包含 '{args.name}' 的动画")
                return
            for _, row in match.iterrows():
                builder.build_all(row["动画ID"], name=row["标题"], platform=row.get("平台", "bilibili"))
        elif args.all_tracks:
            df = analyzer.get_anime_list()
            for _, row in df.iterrows():
                builder.build_all(row["动画ID"], name=row["标题"], platform=row.get("平台", "bilibili"))
        else:
            print("请指定 --name 或 --all")
            return


def cmd_diff(args: any) -> None:
    """查看增量数据"""
    analyzer = Analyzer()
    days = args.days or 7

    if args.name:
        df = analyzer.get_anime_list()
        match = df[df["标题"].str.contains(args.name, na=False)]
        if match.empty:
            print(f"未找到包含 '{args.name}' 的动画")
            return
        for _, row in match.iterrows():
            diff = analyzer.get_daily_diff(row["动画ID"], days=days)
            if diff.empty:
                print(f"{row['标题']}: 暂无增量数据(需要至少采集2天)")
                continue
            print(f"\n=== {row['标题']} 最近{days}天增量 ===")
            print(diff.to_string(index=False))
    else:
        df = analyzer.get_anime_list()
        if df.empty:
            print("没有跟踪中的动画")
            return
        for _, row in df.iterrows():
            diff = analyzer.get_daily_diff(row["动画ID"], days=days)
            if diff.empty:
                continue
            print(f"\n=== {row['标题']} 最近{days}天增量 ===")
            print(diff.to_string(index=False))
