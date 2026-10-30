"""Resilient retrieval of remote rule sources.

Every source is downloaded independently: a timeout, a 5xx or a rate limit only
fails that source, never the whole run. Failures come back as a structured
:class:`FetchResult` so the pipeline can decide whether to abort or degrade.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Iterable

import requests
from loguru import logger

from .config import Settings, SourceConfig

#: Upper bound for a single backoff sleep, so a bad backoff_base cannot stall the job.
MAX_RETRY_SLEEP = 30.0

#: Status codes worth retrying: request timeouts, rate limits and server errors.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class FetchResult:
    """Outcome of retrieving one rule source."""

    name: str
    url: str
    format: str
    ok: bool
    text: str = ""
    status_code: int | None = None
    content_bytes: int = 0
    lines: int = 0
    duration_seconds: float = 0.0
    attempts: int = 0
    error: str | None = None

    def __repr__(self) -> str:  # keep rule bodies out of debug logs
        return (
            f"FetchResult(name={self.name!r}, ok={self.ok}, "
            f"status={self.status_code}, bytes={self.content_bytes}, "
            f"lines={self.lines}, attempts={self.attempts}, error={self.error!r})"
        )

    @property
    def size_hint(self) -> str:
        """Human readable payload size."""
        size = float(self.content_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} GB"


def _retry_delay(response: requests.Response | None, attempt: int, settings: Settings) -> float:
    """Honour ``Retry-After`` when present, otherwise use exponential backoff."""
    if response is not None:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return max(0.0, min(float(header), MAX_RETRY_SLEEP))
            except ValueError:
                logger.debug(f"Ignoring unparsable Retry-After header: {header!r}")
    return min(settings.backoff_base ** attempt, MAX_RETRY_SLEEP)


def fetch_source(source: SourceConfig, settings: Settings) -> FetchResult:
    """Download one rule source with timeout, retry and backoff."""
    headers = {"User-Agent": settings.user_agent, "Accept": "text/plain, */*"}
    started = time.monotonic()
    attempts = 0
    last_status: int | None = None
    last_error = "download failed"

    for attempt in range(settings.max_retries + 1):
        attempts = attempt + 1
        try:
            response = requests.get(source.url, headers=headers, timeout=settings.timeout)
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"[:200]
            logger.warning(f"[fetch] {source.name} {last_error} (attempt {attempts})")
            if attempt < settings.max_retries:
                time.sleep(min(settings.backoff_base**attempt, MAX_RETRY_SLEEP))
            continue

        last_status = response.status_code
        if response.status_code == 200:
            text = response.text
            return FetchResult(
                name=source.name,
                url=source.url,
                format=source.format,
                ok=True,
                text=text,
                status_code=200,
                content_bytes=len(response.content),
                lines=text.count("\n") + 1 if text else 0,
                duration_seconds=round(time.monotonic() - started, 3),
                attempts=attempts,
            )

        last_error = f"HTTP {response.status_code}"
        retryable = response.status_code in RETRYABLE_STATUS
        if retryable and attempt < settings.max_retries:
            delay = _retry_delay(response, attempt, settings)
            logger.warning(
                f"[fetch] {source.name} {last_error}, retry {attempt + 1}/{settings.max_retries} "
                f"in {delay:.1f}s"
            )
            time.sleep(delay)
        elif retryable:
            break
        else:
            # 404/403 etc. never recover within a single run - fail fast.
            break

    logger.error(f"[fetch] {source.name} failed after {attempts} attempt(s): {last_error}")
    return FetchResult(
        name=source.name,
        url=source.url,
        format=source.format,
        ok=False,
        status_code=last_status,
        duration_seconds=round(time.monotonic() - started, 3),
        attempts=attempts,
        error=last_error,
    )


def fetch_sources(sources: Iterable[SourceConfig], settings: Settings) -> list[FetchResult]:
    """Download every source concurrently, preserving the configured order."""
    source_list = list(sources)
    if not source_list:
        return []

    workers = max(1, min(len(source_list), settings.max_concurrency))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda src: fetch_source(src, settings), source_list))

    for result in results:
        if result.ok:
            logger.info(
                f"[fetch] {result.name} {result.status_code} OK, {result.size_hint}, "
                f"{result.lines:,} lines in {result.duration_seconds:.2f}s (attempt {result.attempts})"
            )
    return results
