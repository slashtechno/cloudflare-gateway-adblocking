"""Idempotent Cloudflare Zero Trust Gateway client.

Why this module exists instead of reusing ``src.utils.upload``: that code only
``POST``\\ s new lists, so running it every day would pile up ``adblock-list-1``
... ``adblock-list-N`` until Cloudflare rejects the duplicates. It also prints
errors instead of raising them, which makes failures invisible to CI.

Everything here is idempotent: existing chunks keep their list ID (so the
``Block Ads`` policy stays valid and DNS filtering never blinks), only the
contents are replaced. Stale chunks are dropped *after* the policy is rewritten
so we never delete a list that is still referenced by a policy.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import httpx
from loguru import logger

from .config import Settings

BASE_URL = "https://api.cloudflare.com/client/v4"  # keep in sync with src/utils/upload.py

#: Status codes worth retrying: rate limits and transient server errors.
RETRY_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

#: Used when updating a ruleset - the API keeps accepting the historical shape.
POLICY_TRAFFIC_TEMPLATE = "any(dns.domains[*] in ${list_id})"


class GatewayError(RuntimeError):
    """Raised for any unsuccessful interaction with the Cloudflare API."""


def _describe_errors(payload: dict) -> str:
    """Flatten Cloudflare's ``errors`` array into one readable line (never dumps bodies)."""
    errors = payload.get("errors") or []
    messages = [str(item.get("message") or item.get("code") or item) for item in errors]
    return "; ".join(messages) if messages else "unspecified error"


@dataclass(frozen=True)
class GatewayList:
    """A single Gateway list already known to the account."""

    id: str
    name: str
    count: int
    domains: tuple[str, ...] = ()


@dataclass
class SyncResult:
    """What one synchronisation actually changed."""

    created: int = 0
    updated: int = 0
    unchanged: int = 0
    deleted: int = 0
    list_ids: list[str] = field(default_factory=list)
    policy_id: str | None = None
    policy_action: str = "none"

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "deleted": self.deleted,
            "policy_id": self.policy_id,
            "policy_action": self.policy_action,
        }


def build_traffic(list_ids: list[str]) -> str:
    """Assemble the Gateway rule expression exactly like ``create_dns_policy`` does."""
    return " or ".join(POLICY_TRAFFIC_TEMPLATE.format(list_id=list_id) for list_id in list_ids)


def chunk_domains(domains: list[str], chunk_size: int) -> list[list[str]]:
    """Split the sorted domain list into Gateway-sized chunks."""
    return [domains[index : index + chunk_size] for index in range(0, len(domains), chunk_size)]


class GatewayClient:
    """Strict async wrapper around the Gateway lists / rules endpoints."""

    def __init__(self, account_id: str, token: str, settings: Settings | None = None) -> None:
        if not account_id or not token:
            raise GatewayError("Cloudflare account ID and API token are both required")
        self._account_id = account_id
        self._token = token
        self._settings = settings or Settings()
        self._timeout = httpx.Timeout(self._settings.timeout)

    # ------------------------------------------------------------------ plumbing

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _url(self, suffix: str = "") -> str:
        """Absolute endpoint.

        Built by hand rather than relying on httpx ``base_url`` joining: a leading
        slash would be treated as root-relative and silently drop ``/client/v4``.
        """
        return f"{BASE_URL}/accounts/{self._account_id}/gateway/{suffix}"

    def _retry_delay(self, response: httpx.Response | None, attempt: int) -> float:
        header = response.headers.get("Retry-After") if response is not None else None
        if header:
            try:
                return max(0.0, min(float(header), 30.0))
            except ValueError:
                pass
        return min(self._settings.backoff_base**attempt, 30.0)

    async def _request_raw(
        self,
        client: httpx.AsyncClient,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        params: dict | None = None,
    ) -> dict:
        """Issue one request with retry/backoff and return the full API envelope."""
        for attempt in range(self._settings.max_retries + 1):
            try:
                response = await client.request(
                    method, path, headers=self._headers(), json=json_body, params=params
                )
            except httpx.HTTPError as exc:
                if attempt >= self._settings.max_retries:
                    raise GatewayError(f"{method} {path} failed: {type(exc).__name__}: {exc}") from exc
                delay = self._retry_delay(None, attempt)
                logger.warning(f"[sync] {method} {path} transport error, retry in {delay:.1f}s: {exc}")
                await asyncio.sleep(delay)
                continue

            if response.status_code in RETRY_STATUS and attempt < self._settings.max_retries:
                delay = self._retry_delay(response, attempt)
                logger.warning(
                    f"[sync] {method} {path} HTTP {response.status_code}, retry in {delay:.1f}s"
                )
                await asyncio.sleep(delay)
                continue
            break

        try:
            payload = response.json()
        except ValueError as exc:
            raise GatewayError(
                f"{method} {path} returned a non-JSON body (HTTP {response.status_code})"
            ) from exc

        if not payload.get("success"):
            raise GatewayError(
                f"{method} {path} failed: HTTP {response.status_code} {_describe_errors(payload)}"
            )
        return payload

    async def _request(self, client, method, path, **kwargs):
        return (await self._request_raw(client, method, path, **kwargs)).get("result")

    async def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=BASE_URL, timeout=self._timeout)

    # ------------------------------------------------------------------- reads

    async def list_adblock_lists(self) -> list[GatewayList]:
        """Return every existing ``adblock-list-*`` DOMAIN list (without its items)."""
        async with await self._client() as client:
            result = await self._request(client, "GET", self._url("lists"))
        prefix = self._settings.list_name_prefix
        matches = [
            GatewayList(id=item["id"], name=item["name"], count=item.get("count", 0))
            for item in result or []
            if item.get("name", "").startswith(prefix) and item.get("type") == self._settings.list_type
        ]
        matches.sort(key=lambda entry: entry.name)
        return matches

    async def list_items(self, client, list_id: str) -> tuple[str, ...]:
        """Enumerate all items of one list, following the API cursor."""
        domains: list[str] = []
        cursor = None
        while True:
            params = {"cursor": cursor} if cursor else None
            payload = await self._request_raw(
                client, "GET", self._url(f"lists/{list_id}/items"), params=params
            )
            for item in payload.get("result") or []:
                value = (item or {}).get("value")
                if value:
                    domains.append(value)
            cursor = ((payload.get("result_info") or {}).get("cursors") or {}).get("after")
            if not cursor:
                break
        return tuple(domains)

    async def fetch_baseline(self) -> dict[str, GatewayList]:
        """Snapshot the currently enforced lists and their domains.

        This **must** run before any write: it is the diff baseline and the source
        of truth for which list IDs can be reused.
        """
        lists = await self.list_adblock_lists()
        if not lists:
            logger.info("[sync] 云端尚无已存在的拦截列表，本次为首次写入")
            return {}

        async with await self._client() as client:
            semaphore = asyncio.Semaphore(self._settings.max_concurrency)

            async def load(entry: GatewayList) -> GatewayList:
                async with semaphore:
                    items = await self.list_items(client, entry.id)
                return GatewayList(entry.id, entry.name, len(items), items)

            loaded = await asyncio.gather(*(load(entry) for entry in lists))

        baseline = {entry.name: entry for entry in loaded}
        total = sum(len(entry.domains) for entry in loaded)
        logger.info(
            f"[sync] 云端基线：{len(baseline)} 个列表 / {total:,} 个域名"
        )
        return baseline

    # ------------------------------------------------------------------ writes

    async def create_list(self, client, name: str, items: list[str]) -> dict:
        body = {
            "name": name,
            "type": self._settings.list_type,
            "description": self._settings.list_description,
            "items": [{"value": value} for value in items],
        }
        return await self._request(client, "POST", self._url("lists"), json_body=body)

    async def update_list(self, client, list_id: str, name: str, items: list[str]) -> dict:
        body = {
            "name": name,
            "type": self._settings.list_type,
            "description": self._settings.list_description,
            "items": [{"value": value} for value in items],
        }
        return await self._request(client, "PUT", self._url(f"lists/{list_id}"), json_body=body)

    async def delete_list(self, client, list_id: str) -> None:
        await self._request(client, "DELETE", self._url(f"lists/{list_id}"))

    async def upsert_chunks(
        self, chunks: list[list[str]], baseline: dict[str, GatewayList]
    ) -> tuple[SyncResult, list[GatewayList]]:
        """Create/update every chunk in place, reusing existing list IDs.

        Returns the write counters plus the leftover lists that should be
        removed once the policy no longer references them.
        """
        result = SyncResult()
        prefix = self._settings.list_name_prefix
        expected_names = {f"{prefix}-{index + 1}" for index in range(len(chunks))}

        # Index -> outcome, so list_ids always follow chunk order regardless of
        # which chunks were skipped or how fast the writes completed.
        outcomes: dict[int, dict] = {}
        pending: list[tuple[int, str, list[str], GatewayList | None]] = []
        for index, chunk in enumerate(chunks):
            name = f"{prefix}-{index + 1}"
            existing = baseline.get(name)
            if existing is not None and set(existing.domains) == set(chunk):
                outcomes[index] = {"action": "unchanged", "id": existing.id}
                continue
            pending.append((index, name, chunk, existing))

        semaphore = asyncio.Semaphore(self._settings.max_concurrency)
        started = time.monotonic()
        async with await self._client() as client:
            # asyncio.gather (not TaskGroup) keeps Python 3.10 compatibility.
            written = await asyncio.gather(
                *(
                    self._write_chunk(client, semaphore, index, name, chunk, existing)
                    for index, name, chunk, existing in pending
                )
            )
        for outcome in written:
            outcomes[outcome["index"]] = outcome

        for index in range(len(chunks)):
            outcome = outcomes[index]
            action = outcome["action"]
            if action == "created":
                result.created += 1
            elif action == "updated":
                result.updated += 1
            else:
                result.unchanged += 1
            result.list_ids.append(outcome["id"])

        stale = [entry for name, entry in baseline.items() if name not in expected_names]
        logger.info(
            f"[sync] 列表写入完成 "
            f"新建={result.created} 更新={result.updated} 未变化={result.unchanged} "
            f"用时={time.monotonic() - started:.1f}s"
        )
        return result, sorted(stale, key=lambda entry: entry.name)

    async def _write_chunk(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        index: int,
        name: str,
        chunk: list[str],
        existing: GatewayList | None,
    ) -> dict:
        async with semaphore:
            if existing is None:
                created = await self.create_list(client, name, chunk)
                logger.debug(f"[sync] 新建列表 {name}（{len(chunk)} 条）")
                return {"action": "created", "id": created["id"], "index": index}
            await self.update_list(client, existing.id, name, chunk)
            logger.debug(f"[sync] 更新列表 {name}（{len(chunk)} 条）")
        return {"action": "updated", "id": existing.id, "index": index}

    async def upsert_policy(self, list_ids: list[str]) -> tuple[str, str]:
        """Create or refresh the ``Block Ads`` policy so it covers ``list_ids``."""
        if not list_ids:
            raise GatewayError("拒绝写入空的 DNS 策略：本次没有可用的列表")
        traffic = build_traffic(list_ids)
        body = {
            "name": self._settings.policy_name,
            "description": "Block ad domains",
            "action": "block",
            "traffic": traffic,
            "enabled": True,
        }
        async with await self._client() as client:
            result = await self._request(client, "GET", self._url("rules"))
            target = next(
                (rule for rule in result or [] if rule.get("name") == self._settings.policy_name),
                None,
            )
            if target is None:
                created = await self._request(client, "POST", self._url("rules"), json_body=body)
                logger.info(f"[sync] 策略 {self._settings.policy_name} 已创建，覆盖 {len(list_ids)} 个列表")
                return created["id"], "created"

            try:
                await self._request(
                    client, "PUT", self._url(f"rules/{target['id']}"), json_body=body
                )
            except GatewayError as exc:
                # Older deployments reject PUT; fall back to the shape the
                # original CLI uses so the run still converges.
                logger.warning(f"[sync] 策略原地更新失败，改用删除重建：{exc}")
                await self._request(client, "DELETE", self._url(f"rules/{target['id']}"))
                created = await self._request(client, "POST", self._url("rules"), json_body=body)
                logger.info(f"[sync] 策略 {self._settings.policy_name} 已重建，覆盖 {len(list_ids)} 个列表")
                return created["id"], "recreated"

            logger.info(f"[sync] 策略 {self._settings.policy_name} 已更新，覆盖 {len(list_ids)} 个列表")
            return target["id"], "updated"

    async def delete_stale_lists(self, stale: list[GatewayList]) -> int:
        """Remove leftover chunks. Call **after** the policy has been rewritten."""
        if not stale:
            return 0
        semaphore = asyncio.Semaphore(self._settings.max_concurrency)
        async with await self._client() as client:

            async def drop(entry: GatewayList) -> None:
                async with semaphore:
                    await self.delete_list(client, entry.id)
                    logger.info(f"[sync] 已删除多余列表 {entry.name}（{entry.count:,} 条）")

            await asyncio.gather(*(drop(entry) for entry in stale))
        logger.info(f"[sync] 共清理 {len(stale)} 个多余列表")
        return len(stale)

    async def sync(
        self, chunks: list[list[str]], baseline: dict[str, GatewayList]
    ) -> SyncResult:
        """Write chunks, refresh the policy, then prune leftover lists."""
        result, stale = await self.upsert_chunks(chunks, baseline)
        policy_id, action = await self.upsert_policy(result.list_ids)
        result.policy_id = policy_id
        result.policy_action = action
        result.deleted = await self.delete_stale_lists(stale)
        return result
