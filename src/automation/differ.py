"""Compare the freshly parsed domain set against what Gateway currently enforces.

The baseline is read from Cloudflare itself, so the diff is self-contained: no
cross-run cache, no artifact download, no branch to maintain. Whatever is live
is exactly what we compare against - which is what makes ``added``/``removed``
trustworthy.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from loguru import logger

from .gateway import GatewayList

#: How many example domains to keep for logging and the report.
SAMPLE_LIMIT = 10


@dataclass
class DiffResult:
    """Outcome of comparing baseline and current domain sets."""

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    unchanged: int = 0
    baseline_total: int = 0
    current_total: int = 0

    @property
    def added_count(self) -> int:
        return len(self.added)

    @property
    def removed_count(self) -> int:
        return len(self.removed)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.removed)

    def summary_line(self) -> str:
        """One-line recap used by the ``[diff]`` log entry."""
        return (
            f"基线={self.baseline_total} 本次={self.current_total} "
            f"新增=+{self.added_count} 移除=-{self.removed_count} 无变化={self.unchanged}"
        )

    def as_dict(self) -> dict:
        return {
            "baseline_total": self.baseline_total,
            "current_total": self.current_total,
            "added": self.added_count,
            "removed": self.removed_count,
            "unchanged": self.unchanged,
            "added_samples": self.added[:SAMPLE_LIMIT],
            "removed_samples": self.removed[:SAMPLE_LIMIT],
        }


def baseline_domains(baseline: dict[str, GatewayList]) -> set[str]:
    """Flatten every existing chunk into one set of enforced domains."""
    domains: set[str] = set()
    for entry in baseline.values():
        domains.update(entry.domains)
    return domains


def diff_domains(current: Iterable[str], baseline: dict[str, GatewayList]) -> DiffResult:
    """Compute added / removed / unchanged between Gateway and the new list."""
    current_set = set(current)
    baseline_set = baseline_domains(baseline)

    result = DiffResult(
        added=sorted(current_set - baseline_set),
        removed=sorted(baseline_set - current_set),
        unchanged=len(current_set & baseline_set),
        baseline_total=len(baseline_set),
        current_total=len(current_set),
    )
    logger.info(f"[diff] {result.summary_line()}")
    for sample in result.added[:SAMPLE_LIMIT:3]:
        logger.debug(f"[diff] 新增样例: {sample}")
    for sample in result.removed[:SAMPLE_LIMIT:3]:
        logger.debug(f"[diff] 移除样例: {sample}")
    return result
