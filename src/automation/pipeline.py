"""Orchestrate one synchronisation run.

Stage tags are embedded in every log line (``[config]`` ``[fetch]``
``[normalize]`` ``[diff]`` ``[sync]`` ``[report]``) so a run can be diagnosed by
grepping a single Actions log.

The run never raises: it always returns a :class:`SyncReport` so CI can archive
what happened even when things went wrong. Exit codes are decided by the CLI.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from loguru import logger

from .config import DEFAULT_CONFIG_PATH, Settings, load_config
from .differ import diff_domains
from .fetcher import fetch_sources
from .gateway import GatewayClient, chunk_domains
from .normalizer import collect_whitelist, normalize
from .reporter import STATUS_ABORTED, STATUS_FAILURE, STATUS_SUCCESS, SyncReport, mask_account

DEFAULT_REPORT_PATH = "sync-report.json"


class GuardAbort(RuntimeError):
    """Raised when a fail-safe threshold stops the run before any write happens."""


def read_credentials() -> tuple[str | None, str | None]:
    """Read Cloudflare credentials from the environment (never from argv)."""
    return os.environ.get("CLOUDFLARE_ACCOUNT_ID"), os.environ.get("CLOUDFLARE_TOKEN")


def _guard_source_failures(report: SyncReport, settings: Settings, source_count: int) -> None:
    failed = report.failed_sources
    if source_count and len(failed) == source_count:
        raise GuardAbort(f"全部 {source_count} 个规则源均拉取失败，拒绝写入 Cloudflare")
    if failed and settings.fail_on_source_error:
        raise GuardAbort(
            f"规则源 {', '.join(failed)} 拉取失败，且 settings.fail_on_source_error=true"
        )


def _guard_domain_count(total: int, settings: Settings) -> None:
    if total < settings.fail_safe_min_domains:
        raise GuardAbort(
            f"本次仅解析出 {total} 个域名，低于护栏阈值 fail_safe_min_domains="
            f"{settings.fail_safe_min_domains}，疑似规则源异常，已中止"
        )
    if total > settings.max_domains:
        raise GuardAbort(
            f"本次解析出 {total} 个域名，超过护栏阈值 max_domains={settings.max_domains}，已中止"
        )


def _guard_removal(removed: int, baseline_total: int, settings: Settings, force: bool) -> None:
    if baseline_total <= 0:
        return  # first ever run: nothing can be "removed"
    ratio = removed / baseline_total
    if ratio > settings.max_removal_ratio and not force:
        raise GuardAbort(
            f"本次将移除 {removed:,}/{baseline_total:,} 个已生效域名（{ratio:.1%}），"
            f"超过护栏阈值 max_removal_ratio={settings.max_removal_ratio:.1%}，已中止。"
            f"确认无误后可用 --force 强制执行"
        )


def run_sync(
    *,
    config_path: str | Path = DEFAULT_CONFIG_PATH,
    dry_run: bool = False,
    force: bool = False,
    report_path: str | Path = DEFAULT_REPORT_PATH,
    domains_path: str | Path | None = None,
) -> SyncReport:
    """Run the full pipeline and return the resulting report.

    Args:
        config_path: location of the YAML driving this run.
        dry_run: parse and diff everything, but never touch Cloudflare.
        force: bypass the removal-ratio fail-safe.
        report_path: where to persist the JSON report.
        domains_path: optional location for the final sorted domain list,
            useful as an audit artifact.
    """
    report = SyncReport(dry_run=dry_run)
    report.trigger = os.environ.get("GITHUB_EVENT_NAME") or "manual"
    report.repository = os.environ.get("GITHUB_REPOSITORY")
    report.commit_sha = os.environ.get("GITHUB_SHA")
    run_id = os.environ.get("GITHUB_RUN_ID")
    report.run_url = (
        f"https://github.com/{report.repository}/actions/runs/{run_id}"
        if report.repository and run_id
        else None
    )

    try:
        config = load_config(config_path)
        settings = config.settings
        logger.info(f"[config] 已加载 {config.path}：{config.summary()}")

        account_id, token = read_credentials()
        report.account_masked = mask_account(account_id)
        if dry_run:
            logger.info("[config] 试运行模式：解析与差分照常执行，不会写入 Cloudflare")
        elif not (account_id and token):
            raise RuntimeError(
                "缺少凭据：请在环境变量中设置 CLOUDFLARE_ACCOUNT_ID 与 CLOUDFLARE_TOKEN"
            )

        # ---------------------------------------------------------------- fetch
        fetches = fetch_sources(config.sources, settings)
        whitelist_sources = config.active_whitelist_sources()
        whitelist_fetches = (
            fetch_sources(whitelist_sources, settings) if whitelist_sources else []
        )
        allowed = collect_whitelist(whitelist_fetches, config.active_whitelist_domains())

        # ------------------------------------------------------------ normalize
        parsed = normalize(fetches, settings, allowed)
        report.sources = [stats.as_dict() for stats in parsed.per_source]
        report.failed_sources = parsed.failed_sources
        report.weak_sources = parsed.weak_sources
        report.domains_total = parsed.total
        report.dropped_whitelist = parsed.dropped_whitelist
        report.duplicates_merged = parsed.duplicates_merged

        if domains_path:
            # Written before any Cloudflare call so a failed write still leaves
            # an auditable record of what we intended to push.
            target = Path(domains_path)
            if target.parent != Path(""):
                target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(parsed.domains) + "\n", encoding="utf-8")
            logger.info(f"[report] 已写入域名清单 {target}（{len(parsed.domains):,} 条）")

        # ------------------------------------------------------------- fail safe
        _guard_source_failures(report, settings, len(config.sources))
        _guard_domain_count(parsed.total, settings)

        # --------------------------------------------------- baseline + diff
        baseline: dict = {}
        client = GatewayClient(account_id, token, settings) if account_id and token else None
        if client is not None:
            baseline = asyncio.run(client.fetch_baseline())
            diff = diff_domains(parsed.domains, baseline)
            report.cloud_before = diff.baseline_total
            report.domains_added = diff.added_count
            report.domains_removed = diff.removed_count
            report.domains_unchanged = diff.unchanged
            _guard_removal(diff.removed_count, diff.baseline_total, settings, force)
        else:
            logger.info("[diff] 缺少凭据，已跳过云端基线读取（试运行场景）")

        chunks = chunk_domains(parsed.domains, settings.chunk_size)
        logger.info(f"[diff] 本次需写入 {len(chunks)} 个列表分片（每片上限 {settings.chunk_size} 条）")

        # ----------------------------------------------------------------- sync
        if dry_run or client is None:
            report.policy_action = "dry-run"
            logger.info("[sync] 试运行模式，已跳过全部写操作")
        else:
            write_result = asyncio.run(client.sync(chunks, baseline))
            report.lists_created = write_result.created
            report.lists_updated = write_result.updated
            report.lists_deleted = write_result.deleted
            report.policy_id = write_result.policy_id
            report.policy_action = write_result.policy_action

        report.status = STATUS_SUCCESS
    except GuardAbort as exc:
        report.status = STATUS_ABORTED
        report.failure_reason = str(exc)
        report.add_error("guard", str(exc))
        logger.error(f"[guard] {exc}")
    except Exception as exc:  # noqa: BLE001 - every failure must reach the report
        report.status = STATUS_FAILURE
        report.failure_reason = f"{type(exc).__name__}: {exc}"
        report.add_error("sync", f"{type(exc).__name__}: {exc}")
        logger.error(f"[report] 执行失败：{type(exc).__name__}: {exc}")

    report.finish()
    try:
        written_to = report.to_json(report_path)
        report.write_step_summary()
        logger.info(f"[report] 已写入结构化报告 {written_to}")
    except OSError as exc:
        print(f"[report] 报告写入失败: {exc}")

    logger.info(
        f"[report] 完成 status={report.status} total={report.domains_total:,} "
        f"added=+{report.domains_added:,} removed=-{report.domains_removed:,} "
        f"created={report.lists_created} updated={report.lists_updated} "
        f"deleted={report.lists_deleted} 用时={report.duration_seconds}s"
    )
    return report
