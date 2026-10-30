"""Turn heterogeneous rule files into a single clean, deterministic domain list.

Rule sources ship wildly different dialects::

    0.0.0.0 ads.example.com          # hosts
    ads.example.com                  # domains
    ||ads.example.com^$third-party   # adblock

Everything is funnelled through one validator so that what finally reaches
Cloudflare is always a plain, lower-cased, de-duplicated and **sorted** domain.

Sorting matters: :func:`src.utils.utils.convert_to_list` returns list(set(...)),
whose order depends on ``PYTHONHASHSEED``. A stable order keeps chunking
reproducible across runs, which in turn keeps the diff trustworthy.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from urllib.parse import urlparse

from loguru import logger

from .config import Settings
from .fetcher import FetchResult

COMMENT_STARTS = "#!;["

#: ``digits.digits.digits`` followed by one or more host names.
_HOSTS_ENTRY = re.compile(r"^(?P<ip>[0-9a-fA-F:.]{1,45})\s+(?P<rest>.+?)\s*$")
#: ``||example.com^`` / ``||example.com^$third-party`` / ``||*.example.com^`` /
#: ``||http(s)://example.com/path^``. Scriptlet rules (``||#%#//...``) must not match.
_ADBLOCK_ENTRY = re.compile(r"^\|\|(?P<host>[^^\s$#!]+)")

#: Suffixes that cannot appear in a DNS blocklist (RFC 2606 / 6761 / 6762).
_RESERVED_TLDS = frozenset({"local", "invalid", "test", "example", "onion", "internal", "localhost", "home"})
#: A syntactically valid DNS name. The TLD must be alphabetic, which also keeps
#: plain IPv4 addresses out.
_VALID_DOMAIN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)

#: Names that must never reach Gateway - inherited from
#: :func:`src.utils.utils.convert_to_list` plus the RFC 6761 reserved set.
RESERVED_NAMES = frozenset(
    {
        "0.0.0.0",
        "::1",
        "localhost",
        "localhost.localdomain",
        "broadcasthost",
        "local",
        "ip6-localhost",
        "ip6-loopback",
        "ip6-localnet",
        "ip6-mcastprefix",
        "ip6-allnodes",
        "ip6-allrouters",
        "ip6-allhosts",
        "example",
        "invalid",
        "test",
        "localhost6",
    }
)


def _looks_like_ip(token: str) -> bool:
    """True when the leading hosts column is an IPv4/IPv6 literal rather than a name."""
    if ":" in token:
        return True
    parts = token.split(".")
    return len(parts) == 4 and all(part.isdigit() and len(part) <= 3 for part in parts)


def clean_token(token: str) -> str:
    """Normalise a raw token into a bare, lower-cased host name."""
    value = token.strip().strip("|").strip()
    value = value.split("$", 1)[0]  # drop adblock rule options
    if "://" in value:  # full URL slipped into the rule - keep only its host
        return (urlparse(value).hostname or "").lower().rstrip(".")
    value = value.split("/", 1)[0]  # drop any accidental URL path
    value = value.strip("^")
    while value.startswith("*.") or value.startswith("."):
        value = value.lstrip("*").lstrip(".")
    value = value.strip("\"'").strip(",;")
    return value.lower().rstrip(".")


def is_valid_domain(host: str) -> bool:
    """True when ``host`` can safely be pushed to a Gateway DOMAIN list."""
    if not host or host in RESERVED_NAMES or _VALID_DOMAIN.match(host) is None:
        return False
    return host.rpartition(".")[2] not in _RESERVED_TLDS


def _candidate_tokens(line: str, fmt: str, *, allow_exception: bool = False) -> list[str] | None:
    """Extract candidate hosts from one rule line.

    Returns ``None`` when the line carries no blocking semantics (comments,
    headers, or adblock exception rules when they are not wanted).
    """
    stripped = line.strip()
    if not stripped or stripped[0] in COMMENT_STARTS:
        return None
    if "#" in stripped:
        stripped = stripped.split("#", 1)[0].strip()
    if not stripped:
        return None

    if stripped.startswith("@@"):
        if not allow_exception:
            return None
        stripped = stripped[2:].strip()
        if not stripped:
            return None

    if fmt == "domains":
        tokens = stripped.split()
        return [clean_token(tokens[0])] if tokens else None

    if fmt == "hosts":
        match = _HOSTS_ENTRY.match(stripped)
        if match and _looks_like_ip(match.group("ip")):
            return [clean_token(token) for token in match.group("rest").split()]
        return None

    if fmt == "adblock":
        match = _ADBLOCK_ENTRY.match(stripped)
        return [clean_token(match.group("host"))] if match else None

    # auto: try the most specific dialect first, fall back to a bare name.
    match = _ADBLOCK_ENTRY.match(stripped)
    if match:
        return [clean_token(match.group("host"))]
    match = _HOSTS_ENTRY.match(stripped)
    if match and _looks_like_ip(match.group("ip")):
        return [clean_token(token) for token in match.group("rest").split()]
    tokens = stripped.split()
    return [clean_token(tokens[0])] if tokens else None


def sniff_format(text: str, sample: int = 400) -> str:
    """Guess the dialect of a rule file by inspecting its first meaningful lines."""
    hosts_hits = domains_hits = adblock_hits = 0
    inspected = 0
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped[0] in COMMENT_STARTS:
            continue
        inspected += 1
        if inspected > sample:
            break
        if stripped.startswith("||"):
            adblock_hits += 1
            continue
        match = _HOSTS_ENTRY.match(stripped)
        if match and _looks_like_ip(match.group("ip")):
            hosts_hits += 1
            continue
        domains_hits += 1

    scores = {"hosts": hosts_hits, "domains": domains_hits, "adblock": adblock_hits}
    winner = max(scores, key=lambda key: scores[key])
    return winner if scores[winner] else "domains"


@dataclass
class SourceStats:
    """Per-source parsing counters, surfaced in the report."""

    name: str
    url: str
    format: str
    detected_format: str | None = None
    ok: bool = True
    error: str | None = None
    bytes: int = 0
    lines: int = 0
    candidates: int = 0
    valid: int = 0
    dropped_invalid: int = 0
    dropped_reserved: int = 0
    unique: int = 0

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "url": self.url,
            "format": self.format,
            "detected_format": self.detected_format,
            "ok": self.ok,
            "error": self.error,
            "bytes": self.bytes,
            "lines": self.lines,
            "candidates": self.candidates,
            "valid": self.valid,
            "dropped_invalid": self.dropped_invalid,
            "dropped_reserved": self.dropped_reserved,
            "unique": self.unique,
        }


@dataclass
class NormalizeResult:
    """The merged, cleaned and sorted domain set plus everything worth reporting."""

    domains: list[str] = field(default_factory=list)
    per_source: list[SourceStats] = field(default_factory=list)
    dropped_whitelist: int = 0
    duplicates_merged: int = 0
    failed_sources: list[str] = field(default_factory=list)
    weak_sources: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.domains)

    def summary_line(self) -> str:
        """One-line recap used by the ``[normalize]`` log entry."""
        return (
            f"命中白名单剔除={self.dropped_whitelist} "
            f"重复合并={self.duplicates_merged} 最终={self.total}"
        )

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "dropped_whitelist": self.dropped_whitelist,
            "duplicates_merged": self.duplicates_merged,
            "failed_sources": self.failed_sources,
            "weak_sources": self.weak_sources,
            "sources": [stats.as_dict() for stats in self.per_source],
        }


def _parse_single(result: FetchResult, settings: Settings, *, allow_exception: bool) -> tuple[set[str], SourceStats]:
    stats = SourceStats(
        name=result.name,
        url=result.url,
        format=result.format,
        ok=result.ok,
        error=result.error,
        bytes=result.content_bytes,
        lines=result.lines,
    )
    if not result.ok or not result.text:
        stats.error = stats.error or "empty response body"
        return set(), stats

    detected = result.format if result.format != "auto" else sniff_format(result.text)
    stats.detected_format = detected

    domains: set[str] = set()
    for raw_line in result.text.splitlines():
        tokens = _candidate_tokens(raw_line, detected, allow_exception=allow_exception)
        if tokens is None:
            continue
        for token in tokens:
            if not token:
                continue
            stats.candidates += 1
            if token in RESERVED_NAMES:
                stats.dropped_reserved += 1
                continue
            if not is_valid_domain(token):
                stats.dropped_invalid += 1
                continue
            domains.add(token)

    stats.valid = stats.candidates - stats.dropped_invalid - stats.dropped_reserved
    stats.unique = len(domains)
    return domains, stats


def _is_whitelisted(domain: str, allowed: set[str]) -> bool:
    """Exact match or any parent suffix match (``b.example.com`` covers ``c.b.example.com``)."""
    if domain in allowed:
        return True
    labels = domain.split(".")
    for index in range(1, len(labels)):
        if ".".join(labels[index:]) in allowed:
            return True
    return False


def normalize(fetches: Sequence[FetchResult], settings: Settings, whitelist: Iterable[str] = ()) -> NormalizeResult:
    """Parse every fetched blocklist, merge, apply the whitelist and sort."""
    allowed = {domain for domain in whitelist if is_valid_domain(domain)}
    result = NormalizeResult()
    union: set[str] = set()
    seen_unique = 0

    for fetch in fetches:
        domains, stats = _parse_single(fetch, settings, allow_exception=False)
        result.per_source.append(stats)
        if not stats.ok:
            result.failed_sources.append(stats.name)
            logger.error(f"[normalize] {stats.name} 拉取失败，已跳过：{stats.error}")
            continue
        if stats.unique < settings.min_domains_per_source:
            result.weak_sources.append(stats.name)
            logger.warning(
                f"[normalize] {stats.name} 仅解析出 {stats.unique} 个域名，"
                f"低于阈值 {settings.min_domains_per_source}，请检查规则源是否异常"
            )
        else:
            logger.info(
                f"[normalize] {stats.name} format={stats.detected_format} "
                f"候选={stats.candidates} 有效={stats.valid} "
                f"丢弃(非法={stats.dropped_invalid} 保留字={stats.dropped_reserved}) "
                f"去重后={stats.unique}"
            )
        union |= domains
        seen_unique += stats.unique

    result.duplicates_merged = seen_unique - len(union)

    if allowed:
        kept = {domain for domain in union if not _is_whitelisted(domain, allowed)}
        result.dropped_whitelist = len(union) - len(kept)
    else:
        kept = union

    result.domains = sorted(kept)
    logger.info(f"[normalize] 汇总 {result.summary_line()}")
    return result


def collect_whitelist(
    fetches: Sequence[FetchResult], inline: Iterable[str] = ()
) -> set[str]:
    """Collect allowed domains from remote exception sources and inline entries."""
    allowed: set[str] = {domain for domain in inline if is_valid_domain(domain)}
    for fetch in fetches:
        if not fetch.ok or not fetch.text:
            logger.warning(f"[normalize] 白名单源 {fetch.name} 不可用：{fetch.error or 'empty body'}")
            continue
        detected = fetch.format if fetch.format != "auto" else sniff_format(fetch.text)
        before = len(allowed)
        for raw_line in fetch.text.splitlines():
            tokens = _candidate_tokens(raw_line, detected, allow_exception=True)
            if tokens is None:
                continue
            for token in tokens:
                if is_valid_domain(token):
                    allowed.add(token)
        logger.info(f"[normalize] 白名单源 {fetch.name} 新增 {len(allowed) - before} 个域名")

    if allowed:
        logger.info(f"[normalize] 白名单合计 {len(allowed)} 个域名")
    return allowed
