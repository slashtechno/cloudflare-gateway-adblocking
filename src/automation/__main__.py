"""Command line entry point for the daily Gateway list synchronisation.

Usage::

    python -m src.automation --dry-run          # parse everything, write nothing
    python -m src.automation --force            # bypass the removal-ratio guard
    python -m src.automation -c config/sources.yml --report-path sync-report.json

Credentials are read **only** from ``CLOUDFLARE_ACCOUNT_ID`` and
``CLOUDFLARE_TOKEN`` so they never appear in ``ps`` output or CI command lines.

Exit codes: ``0`` on success, ``1`` on failure or abort, ``2`` on invalid usage.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from loguru import logger

from .config import DEFAULT_CONFIG_PATH
from .pipeline import DEFAULT_REPORT_PATH, run_sync
from .reporter import STATUS_SUCCESS

LOGGER_FORMAT = (
    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> |"
    "<level>{level: ^8}</level>| <level>{message}</level>"
)


def build_parser() -> argparse.ArgumentParser:
    """Declare every knob the workflow and manual operators can turn."""
    parser = argparse.ArgumentParser(
        prog="cloudflare-gateway-adblocking-sync",
        description="Fetch adblock rule sources and sync them to Cloudflare Gateway lists",
    )
    parser.add_argument(
        "--config",
        "-c",
        default=str(DEFAULT_CONFIG_PATH),
        help="规则源配置文件路径（默认 config/sources.yml）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="完整执行抓取与差分，但不对 Cloudflare 做任何写入",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略 max_removal_ratio 护栏，强制执行本次删除",
    )
    parser.add_argument(
        "--log-level",
        "-l",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        help="日志级别：DEBUG / INFO / WARNING / ERROR（默认 INFO）",
    )
    parser.add_argument(
        "--report-path",
        default=DEFAULT_REPORT_PATH,
        help="结构化报告输出路径（默认 sync-report.json）",
    )
    parser.add_argument(
        "--domains-path",
        default=None,
        help="额外输出本次最终域名清单（每行一个），便于审计留档",
    )
    return parser


def main() -> None:
    """Parse arguments, run the pipeline and translate the outcome into an exit code."""
    args = build_parser().parse_args()

    logger.remove()
    logger.add(sys.stderr, format=LOGGER_FORMAT, colorize=True, level=args.log_level.upper())

    report = run_sync(
        config_path=Path(args.config),
        dry_run=args.dry_run,
        force=args.force,
        report_path=args.report_path,
        domains_path=args.domains_path,
    )

    if not os.environ.get("GITHUB_STEP_SUMMARY"):
        # Helpful locally; inside Actions the same text goes to the job summary.
        print(report.to_markdown())

    sys.exit(0 if report.status == STATUS_SUCCESS else 1)


if __name__ == "__main__":
    main()
