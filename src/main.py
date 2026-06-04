"""CLI 入口

只负责命令定义和路由, 实现按业务领域分到 cli/ 子模块。
"""

from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import TimedRotatingFileHandler

from cli.analyzing import cmd_diff, cmd_export, cmd_list
from cli.crawling import cmd_crawl, cmd_schedule, cmd_tasks
from cli.discover_cmd import cmd_discover
from cli.tracking import cmd_add, cmd_import, cmd_pause, cmd_resume, cmd_sync
from config import get_settings


def _dev_run_streamlit(app_path: str, port: int) -> None:
    """watchfiles 的回调：重启 Streamlit 子进程"""
    import subprocess

    subprocess.run(
        [
            sys.executable, "-m", "streamlit", "run",
            app_path,
            "--server.port", str(port),
            "--server.fileWatcherType", "none",
            "--server.headless", "true",
        ],
    )


def cmd_dev(args: any) -> None:
    """启动开发服务器 (全范围热更新: cli/core/db 等变更自动重启)"""
    try:
        import watchfiles
    except ModuleNotFoundError:
        print("❌ 缺少 watchfiles，请安装: pip install -e .[dev]", file=sys.stderr)
        sys.exit(1)

    from pathlib import Path

    app_path = str(Path(__file__).resolve().parent / "web" / "app.py")
    root = Path(app_path).parent.parent.parent
    port = getattr(args, "port", 8501)

    print(f"启动开发服务器: http://localhost:{port}  (热更新已就绪)")
    watchfiles.run_process(
        root / "src",
        root / ".streamlit",
        root / "pyproject.toml",
        root / "data",
        target=_dev_run_streamlit,
        kwargs={"app_path": app_path, "port": port},
    )


def cmd_web(args: any) -> None:
    """启动 Streamlit Web UI (配置由 .streamlit/config.toml 控制)"""
    import subprocess
    from pathlib import Path

    app_path = Path(__file__).resolve().parent / "web" / "app.py"
    port = getattr(args, "port", 8501)

    print(f"启动 Web UI: http://localhost:{port}")
    subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(app_path), "--server.port", str(port)],
    )


def _setup_logging() -> None:
    """配置日志: 控制台+文件双输出, 按天轮转保留7天, 压制httpx刷屏"""
    settings = get_settings()
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)

    log_dir = settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(log_level)

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(log_level)
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = TimedRotatingFileHandler(
        log_dir / "anime-hub.log",
        when="midnight",
        interval=1,
        backupCount=settings.log_keep_days,
        encoding="utf-8",
    )
    file_handler.setLevel(log_level)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    err_handler = TimedRotatingFileHandler(
        log_dir / "error.log",
        when="midnight",
        interval=1,
        backupCount=settings.log_keep_days,
        encoding="utf-8",
    )
    err_handler.setLevel(logging.ERROR)
    err_handler.setFormatter(fmt)
    root.addHandler(err_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器"""
    parser = argparse.ArgumentParser(
        prog="anime-hub",
        description="动画数据追踪与分析工具",
    )
    sub = parser.add_subparsers(dest="command", help="可用命令")

    # — 追踪管理 —
    p = sub.add_parser("add", help="添加动画到跟踪列表")
    p.add_argument("keyword", nargs="?", default="", help="搜索关键词")
    p.add_argument("--season-id", type=int, help="直接指定season_id")

    p = sub.add_parser("import", help="从CSV文件批量导入追踪目标并采集(旧模式)")
    p.add_argument("file", nargs="?", default=None, help="CSV文件路径")
    p.add_argument("--dry-run", action="store_true", help="仅校验预览")
    p.add_argument("--skip-crawl", action="store_true", help="只导入不同步采集")

    p = sub.add_parser("sync", help="对比追踪目录与数据库, 自动同步差异(推荐)")
    p.add_argument("--dry-run", action="store_true", help="仅预览差异")

    p = sub.add_parser("pause", help="暂停指定动画的采集")
    p.add_argument("season_id", type=int, help="动画season_id")

    p = sub.add_parser("resume", help="恢复指定动画的采集")
    p.add_argument("season_id", type=int, help="动画season_id")

    # — 采集执行 —
    p = sub.add_parser("crawl", help="执行数据采集")
    p.add_argument("--season-id", type=int, help="指定动画season_id")

    sub.add_parser("schedule", help="启动定时调度器")

    p = sub.add_parser("tasks", help="查看采集任务状态")

    # — 数据分析 —
    sub.add_parser("list", help="列出所有跟踪中的动画")

    p = sub.add_parser("export", help="导出分析报告")
    p.add_argument("--name", help="动画名称(模糊匹配)")
    p.add_argument("--all", dest="all_tracks", action="store_true", help="导出所有动画")
    p.add_argument("--format", choices=["excel", "charts", "both"], default="excel", help="导出格式")

    p = sub.add_parser("diff", help="查看增量数据")
    p.add_argument("--name", help="动画名称(模糊匹配)")
    p.add_argument("--days", type=int, default=7, help="天数(默认7)")

    # — 发现 —
    p = sub.add_parser("discover", help="从B站拉取国创/番剧列表, 写入CSV")
    p.add_argument("--type", type=int, default=4, help="分类类型(1=番剧, 4=国创)")
    p.add_argument("--order", type=int, default=2, help="排序方式(2=追番数, 3=播放量, 5=评分)")

    # — Web UI —
    p = sub.add_parser("web", help="启动Streamlit Web界面")
    p.add_argument("--port", type=int, default=8501, help="端口号(默认8501)")

    p = sub.add_parser("dev", help="启动开发服务器(全文件热更新)")
    p.add_argument("--port", type=int, default=8501, help="端口号(默认8501)")

    return parser


def main() -> None:
    """CLI 入口"""
    _setup_logging()

    parser = _build_parser()
    args = parser.parse_args()

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    commands = {
        "add": cmd_add,
        "import": cmd_import,
        "sync": cmd_sync,
        "pause": cmd_pause,
        "resume": cmd_resume,
        "crawl": cmd_crawl,
        "schedule": cmd_schedule,
        "tasks": cmd_tasks,
        "list": cmd_list,
        "export": cmd_export,
        "diff": cmd_diff,
        "discover": cmd_discover,
        "web": cmd_web,
        "dev": cmd_dev,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
