"""Load and validate ``config/sources.yml``.

The automation pipeline is entirely driven by one YAML file. This module turns
that file into strongly typed dataclasses and reports every problem with the
exact ``settings.chunk_size``-style path that caused it, so a typo surfaces as a
clear failure instead of a silently wrong sync.
"""

from __future__ import annotations

import pathlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlparse

import yaml

DEFAULT_CONFIG_PATH = pathlib.Path("config/sources.yml")

#: Rule file layouts understood by :mod:`src.automation.normalizer`.
SUPPORTED_FORMATS: tuple[str, ...] = ("auto", "hosts", "domains", "adblock")

_TOP_LEVEL_KEYS = frozenset({"settings", "sources", "whitelist"})
_SOURCE_KEYS = frozenset({"name", "url", "format", "enabled"})
_WHITELIST_KEYS = frozenset({"enabled", "sources", "inline"})
_SETTINGS_KEYS = frozenset(
    {
        "list_name_prefix",
        "list_type",
        "list_description",
        "policy_name",
        "chunk_size",
        "timeout",
        "max_retries",
        "backoff_base",
        "max_concurrency",
        "user_agent",
        "min_domains_per_source",
        "fail_safe_min_domains",
        "max_domains",
        "max_removal_ratio",
        "fail_on_source_error",
    }
)
_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


class ConfigError(ValueError):
    """Raised when the configuration file is malformed or holds invalid values."""


def _as_mapping(value: object, ctx: str) -> dict:
    if not isinstance(value, Mapping):
        raise ConfigError(f"{ctx} must be a mapping, got {type(value).__name__}")
    return dict(value)


def _reject_unknown(mapping: Mapping, allowed: frozenset, ctx: str) -> None:
    unknown = sorted(str(key) for key in mapping if key not in allowed)
    if unknown:
        raise ConfigError(
            f"{ctx} contains unknown field(s): {', '.join(unknown)} "
            f"(allowed: {', '.join(sorted(allowed))})"
        )


def _string(mapping: Mapping, key: str, ctx: str, *, required: bool, default: str | None = None) -> str | None:
    field_path = f"{ctx}.{key}"
    if key not in mapping or mapping[key] is None:
        if required:
            raise ConfigError(f"{field_path} is required")
        return default
    raw = mapping[key]
    if not isinstance(raw, str) or not raw.strip():
        raise ConfigError(f"{field_path} must be a non-empty string, got {raw!r}")
    return raw.strip()


def _number(mapping: Mapping, key: str, ctx: str, *, converter, default, minimum=None, maximum=None):
    field_path = f"{ctx}.{key}"
    if key not in mapping or mapping[key] is None:
        return converter(default)
    raw = mapping[key]
    # bool is a subclass of int - never accept True/False where a number is due.
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ConfigError(f"{field_path} must be a number, got {raw!r}")
    value = converter(raw)
    if minimum is not None and value < minimum:
        raise ConfigError(f"{field_path} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{field_path} must be <= {maximum}, got {value}")
    return value


def _bool(mapping: Mapping, key: str, ctx: str, *, default: bool) -> bool:
    field_path = f"{ctx}.{key}"
    if key not in mapping or mapping[key] is None:
        return default
    raw = mapping[key]
    if not isinstance(raw, bool):
        raise ConfigError(f"{field_path} must be true or false, got {raw!r}")
    return raw


def _validate_url(url: str, ctx: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_URL_SCHEMES or not parsed.netloc:
        raise ConfigError(
            f"{ctx} must be an absolute http(s) URL pointing at a rule file, got {url!r}"
        )
    return url


@dataclass(frozen=True)
class SourceConfig:
    """A single remote blocklist/whitelist source."""

    name: str
    url: str
    format: str = "auto"
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.format not in SUPPORTED_FORMATS:
            raise ConfigError(
                f"source '{self.name}': format must be one of "
                f"{', '.join(SUPPORTED_FORMATS)}, got {self.format!r}"
            )
        _validate_url(self.url, f"source '{self.name}'.url")

    @classmethod
    def from_dict(cls, raw: object, ctx: str) -> "SourceConfig":
        mapping = _as_mapping(raw, ctx)
        _reject_unknown(mapping, _SOURCE_KEYS, ctx)
        name = _string(mapping, "name", ctx, required=True)
        url = _validate_url(_string(mapping, "url", ctx, required=True) or "", f"{ctx}.url")
        return cls(
            name=name,
            url=url,
            format=_string(mapping, "format", ctx, required=False, default="auto"),
            enabled=_bool(mapping, "enabled", ctx, default=True),
        )


@dataclass(frozen=True)
class WhitelistConfig:
    """Domains that must never end up blocked."""

    enabled: bool = True
    sources: tuple[SourceConfig, ...] = ()
    inline: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: object, ctx: str = "whitelist") -> "WhitelistConfig":
        mapping = _as_mapping(raw, ctx)
        _reject_unknown(mapping, _WHITELIST_KEYS, ctx)

        raw_sources = mapping.get("sources") or []
        if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, (str, bytes)):
            raise ConfigError(f"{ctx}.sources must be a list, got {type(raw_sources).__name__}")
        sources = tuple(
            SourceConfig.from_dict(item, f"{ctx}.sources[{index}]")
            for index, item in enumerate(raw_sources)
        )

        raw_inline = mapping.get("inline") or []
        if not isinstance(raw_inline, Sequence) or isinstance(raw_inline, (str, bytes)):
            raise ConfigError(f"{ctx}.inline must be a list, got {type(raw_inline).__name__}")
        inline: list[str] = []
        for index, item in enumerate(raw_inline):
            value = _string({"value": item}, "value", f"{ctx}.inline[{index}]", required=True)
            inline.append(value.lower().rstrip("."))

        return cls(
            enabled=_bool(mapping, "enabled", ctx, default=True),
            sources=tuple(source for source in sources if source.enabled),
            inline=tuple(dict.fromkeys(inline)),
        )


@dataclass(frozen=True)
class Settings:
    """Gateway resource naming, networking and fail-safe thresholds."""

    # Gateway resource naming (must match existing objects, rarely changed)
    list_name_prefix: str = "adblock-list"
    list_type: str = "DOMAIN"
    list_description: str = "A blocklist of ad domains"
    policy_name: str = "Block Ads"
    chunk_size: int = 1000

    # Networking
    timeout: int = 30
    max_retries: int = 3
    backoff_base: float = 1.5
    max_concurrency: int = 5
    user_agent: str = "cloudflare-gateway-adblocking-sync/1.0"

    # Fail-safe thresholds
    min_domains_per_source: int = 100
    fail_safe_min_domains: int = 1000
    max_domains: int = 400_000
    max_removal_ratio: float = 0.5
    fail_on_source_error: bool = False

    def __post_init__(self) -> None:
        if not 0 < self.max_removal_ratio <= 1:
            raise ConfigError(
                f"settings.max_removal_ratio must be in (0, 1], got {self.max_removal_ratio}"
            )
        if self.fail_safe_min_domains > self.max_domains:
            raise ConfigError(
                "settings.fail_safe_min_domains must not exceed settings.max_domains "
                f"({self.fail_safe_min_domains} > {self.max_domains})"
            )

    @classmethod
    def from_dict(cls, raw: object, ctx: str = "settings") -> "Settings":
        mapping = _as_mapping(raw, ctx)
        _reject_unknown(mapping, _SETTINGS_KEYS, ctx)
        return cls(
            list_name_prefix=_string(mapping, "list_name_prefix", ctx, required=False, default="adblock-list"),
            list_type=_string(mapping, "list_type", ctx, required=False, default="DOMAIN"),
            list_description=_string(
                mapping, "list_description", ctx, required=False, default="A blocklist of ad domains"
            ),
            policy_name=_string(mapping, "policy_name", ctx, required=False, default="Block Ads"),
            chunk_size=_number(
                mapping, "chunk_size", ctx, converter=int, default=1000, minimum=1, maximum=1000
            ),
            timeout=_number(mapping, "timeout", ctx, converter=int, default=30, minimum=1, maximum=600),
            max_retries=_number(
                mapping, "max_retries", ctx, converter=int, default=3, minimum=0, maximum=10
            ),
            backoff_base=_number(
                mapping, "backoff_base", ctx, converter=float, default=1.5, minimum=1.0, maximum=30.0
            ),
            max_concurrency=_number(
                mapping, "max_concurrency", ctx, converter=int, default=5, minimum=1, maximum=20
            ),
            user_agent=_string(
                mapping,
                "user_agent",
                ctx,
                required=False,
                default="cloudflare-gateway-adblocking-sync/1.0",
            ),
            min_domains_per_source=_number(
                mapping, "min_domains_per_source", ctx, converter=int, default=100, minimum=0
            ),
            fail_safe_min_domains=_number(
                mapping, "fail_safe_min_domains", ctx, converter=int, default=1000, minimum=0
            ),
            max_domains=_number(
                mapping, "max_domains", ctx, converter=int, default=400_000, minimum=1
            ),
            max_removal_ratio=_number(
                mapping, "max_removal_ratio", ctx, converter=float, default=0.5, minimum=0.0, maximum=1.0
            ),
            fail_on_source_error=_bool(mapping, "fail_on_source_error", ctx, default=False),
        )


@dataclass(frozen=True)
class SyncConfig:
    """Validated, immutable view of the whole configuration file."""

    settings: Settings
    sources: tuple[SourceConfig, ...]
    whitelist: WhitelistConfig
    path: pathlib.Path = DEFAULT_CONFIG_PATH

    @classmethod
    def from_dict(cls, raw: object, path: pathlib.Path = DEFAULT_CONFIG_PATH) -> "SyncConfig":
        mapping = _as_mapping(raw, str(path))
        _reject_unknown(mapping, _TOP_LEVEL_KEYS, str(path))

        settings = Settings.from_dict(mapping.get("settings") or {})

        raw_sources = mapping.get("sources")
        if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, (str, bytes)):
            raise ConfigError(
                f"{path}: 'sources' must be a list, got {type(raw_sources).__name__}"
            )
        sources: list[SourceConfig] = []
        seen_names: set[str] = set()
        for index, item in enumerate(raw_sources):
            source = SourceConfig.from_dict(item, f"sources[{index}]")
            if source.name in seen_names:
                raise ConfigError(f"sources[{index}].name duplicates another source: {source.name!r}")
            seen_names.add(source.name)
            sources.append(source)

        enabled_sources = tuple(source for source in sources if source.enabled)
        if not enabled_sources:
            raise ConfigError(
                f"{path}: no enabled sources found - set enabled: true on at least one source"
            )

        whitelist_raw = mapping.get("whitelist") or {}
        whitelist = WhitelistConfig.from_dict(whitelist_raw)

        return cls(settings=settings, sources=enabled_sources, whitelist=whitelist, path=path)

    @property
    def enabled_sources(self) -> tuple[SourceConfig, ...]:
        """Sources taking part in this run (already filtered at load time)."""
        return self.sources

    def active_whitelist_sources(self) -> tuple[SourceConfig, ...]:
        """Remote whitelist sources, honouring the whitelist master switch."""
        return self.whitelist.sources if self.whitelist.enabled else ()

    def active_whitelist_domains(self) -> tuple[str, ...]:
        """Inline whitelist domains, honouring the whitelist master switch."""
        return self.whitelist.inline if self.whitelist.enabled else ()

    def summary(self) -> str:
        """One-line description used by the ``[config]`` log line."""
        return (
            f"{len(self.sources)} source(s), "
            f"chunk={self.settings.chunk_size}, timeout={self.settings.timeout}s, "
            f"retries={self.settings.max_retries}, "
            f"whitelist={'on' if self.whitelist.enabled else 'off'}"
            f"({len(self.whitelist.sources)} remote / {len(self.whitelist.inline)} inline)"
        )


def load_config(path: str | pathlib.Path = DEFAULT_CONFIG_PATH) -> SyncConfig:
    """Read, parse and validate the YAML configuration file.

    Raises:
        ConfigError: if the file is missing, unparsable or semantically invalid.
    """
    config_path = pathlib.Path(path)
    if not config_path.is_file():
        raise ConfigError(f"Configuration file not found: {config_path}")
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse {config_path} as YAML: {exc}") from exc
    if raw is None:
        raise ConfigError(f"{config_path} is empty")
    return SyncConfig.from_dict(raw, config_path)
