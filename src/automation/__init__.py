"""Automated daily synchronisation of Cloudflare Gateway adblock lists.

This package is intentionally additive: nothing under ``src/utils`` or the
existing ``upload``/``delete`` CLI is modified, so the published command line
behaviour stays exactly the same.
"""

__all__ = ["run_sync"]

__version__ = "1.0.0"


def __getattr__(name: str):  # pragma: no cover - lazy import guard
    """Resolve :func:`run_sync` lazily so importing the package stays cheap."""
    if name == "run_sync":
        from .pipeline import run_sync

        return run_sync
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
