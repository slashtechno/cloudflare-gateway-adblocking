"""The single structured contract produced by every synchronisation run.

Everything the pipeline learns ends up here, and everything downstream (job
summary, uploaded artifact, failure issue) is rendered from this object. Keeping
one contract means adding a field never requires touching the workflow.
"""

from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

STATUS_SUCCESS = "success"
STATUS_FAILURE = "failure"
STATUS_ABORTED = "aborted"


def utc_now() -> str:
    """ISO-8601 UTC timestamp used for report boundaries."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def mask_account(account_id: str | None) -> str | None:
    """Only ever expose the last four characters of an account ID."""
    if not account_id:
        return None
    return f"***{account_id[-4:]}" if len(account_id) > 4 else "***"


@dataclass
class SyncReport:
    """Full outcome of one run."""

    started_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    duration_seconds: float = 0.0
    trigger: str = "manual"
    repository: str | None = None
    commit_sha: str | None = None
    run_url: str | None = None
    dry_run: bool = False
    status: str = STATUS_SUCCESS
    failure_reason: str | None = None
    account_masked: str | None = None

    # Source level detail
    sources: list[dict] = field(default_factory=list)
    failed_sources: list[str] = field(default_factory=list)
    weak_sources: list[str] = field(default_factory=list)

    # Domain counts
    domains_total: int = 0
    domains_added: int = 0
    domains_removed: int = 0
    domains_unchanged: int = 0
    cloud_before: int = 0
    dropped_whitelist: int = 0
    duplicates_merged: int = 0

    # Gateway writes
    lists_created: int = 0
    lists_updated: int = 0
    lists_deleted: int = 0
    policy_id: str | None = None
    policy_action: str = "none"

    errors: list[dict] = field(default_factory=list)

    def add_error(self, stage: str, message: str, http_status: int | None = None) -> None:
        """Record a stage-scoped failure without leaking secrets.

        The status is only downgraded while it is still ``success``, so a caller
        that already marked the run as ``aborted`` keeps that distinction.
        """
        self.errors.append({"stage": stage, "message": message[:500], "http_status": http_status})
        if self.status == STATUS_SUCCESS:
            self.status = STATUS_FAILURE
        if not self.failure_reason:
            self.failure_reason = f"[{stage}] {message[:300]}"

    def finish(self, status: str | None = None) -> None:
        """Stamp the closing time and duration."""
        finished = datetime.now(timezone.utc)
        started = datetime.strptime(self.started_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        self.finished_at = finished.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.duration_seconds = round((finished - started).total_seconds(), 3)
        if status:
            self.status = status

    def to_dict(self) -> dict[str, Any]:
        return {
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": self.duration_seconds,
            "trigger": self.trigger,
            "repository": self.repository,
            "commit_sha": self.commit_sha,
            "run_url": self.run_url,
            "dry_run": self.dry_run,
            "status": self.status,
            "failure_reason": self.failure_reason,
            "account_masked": self.account_masked,
            "sources": self.sources,
            "failed_sources": self.failed_sources,
            "weak_sources": self.weak_sources,
            "domains_total": self.domains_total,
            "domains_added": self.domains_added,
            "domains_removed": self.domains_removed,
            "domains_unchanged": self.domains_unchanged,
            "cloud_before": self.cloud_before,
            "dropped_whitelist": self.dropped_whitelist,
            "duplicates_merged": self.duplicates_merged,
            "lists_created": self.lists_created,
            "lists_updated": self.lists_updated,
            "lists_deleted": self.lists_deleted,
            "policy_id": self.policy_id,
            "policy_action": self.policy_action,
            "errors": self.errors,
        }

    def to_json(self, path: str | pathlib.Path) -> pathlib.Path:
        """Persist the report so CI can archive it."""
        target = pathlib.Path(path)
        if target.parent != pathlib.Path(""):
            target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        return target

    def to_markdown(self) -> str:
        """Render the execution summary shown on the GitHub Actions run page."""
        badge = {"success": "✅", "failure": "❌", "aborted": "🛑"}.get(self.status, "❔")
        headline = f"## {badge} Gateway 广告拦截同步报告\n"
        meta = [
            f"- **状态**：`{self.status}`" + ("（试运行，未写入 Cloudflare）" if self.dry_run else ""),
            f"- **触发方式**：`{self.trigger}`",
            f"- **耗时**：{self.duration_seconds}s",
            f"- **账号**：`{self.account_masked or 'n/a'}`",
            f"- **提交**：`{(self.commit_sha or 'n/a')[:7]}`",
        ]
        if self.failure_reason:
            meta.append(f"- **失败原因**：{self.failure_reason}")

        counts = [
            "| 指标 | 数量 |",
            "| --- | ---: |",
            f"| 本次生效域名总数 | {self.domains_total:,} |",
            f"| 同步前云端域名数 | {self.cloud_before:,} |",
            f"| 新增域名 | +{self.domains_added:,} |",
            f"| 移除域名 | -{self.domains_removed:,} |",
            f"| 未变化域名 | {self.domains_unchanged:,} |",
            f"| 白名单剔除 | {self.dropped_whitelist:,} |",
            f"| 跨源重复合并 | {self.duplicates_merged:,} |",
            f"| 新建 / 更新 / 删除列表 | {self.lists_created} / {self.lists_updated} / {self.lists_deleted} |",
            f"| 策略 ID（`{self.policy_action}`） | {self.policy_id or 'n/a'} |",
        ]

        lines = [headline, *meta, "", *counts, "", "### 规则源明细", "| 源 | 格式 | 下载 | 行数 | 有效 | 去重后 | 状态 |", "| --- | --- | ---: | ---: | ---: | ---: | --- |"]
        for source in self.sources:
            rows = [
                source.get("name", "?"),
                source.get("detected_format") or source.get("format") or "-",
                f"{(source.get('bytes') or 0) / 1024:.0f} KB",
                f"{source.get('lines') or 0:,}",
                f"{source.get('valid') or 0:,}",
                f"{source.get('unique') or 0:,}",
                "失败" if not source.get("ok") else ("偏少" if source.get("name") in self.weak_sources else "正常"),
            ]
            lines.append("| " + " | ".join(str(row) for row in rows) + " |")

        if self.errors:
            lines += ["", "### 错误", "```"]
            lines += [f"[{item['stage']}] {item['message']}" for item in self.errors]
            lines += ["```"]
        return "\n".join(lines) + "\n"

    def write_step_summary(self, path: str | None = None) -> bool:
        """Append the Markdown summary to the GitHub job summary, if available."""
        target = path or os.environ.get("GITHUB_STEP_SUMMARY")
        if not target:
            return False
        try:
            with open(target, "a", encoding="utf-8") as handle:
                handle.write(self.to_markdown())
        except OSError as exc:
            print(f"[report] 无法写入 GITHUB_STEP_SUMMARY: {exc}")
            return False
        return True
