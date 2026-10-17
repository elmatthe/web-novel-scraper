"""Adapter registry: maps an adapter key to its adapter class.

Registration is centralized here (this module imports the adapter classes and
registers them) so there is exactly one registration point and no import
cycle: adapters depend on ``base`` + ``models`` only, never on the registry.

`get_adapter_for_spec` is the pipeline-layer disabled-adapter guard: it refuses
to hand out an adapter for a disabled catalog row (defense in depth — the GUI
greys disabled sites, the pipeline refuses them, and the stub's
NotImplementedError is the final backstop).
"""

from __future__ import annotations

from .adapters.base import BaseAdapter
from .adapters.empire_novel import EmpireNovelAdapter
from .adapters.freewebnovel import FreeWebNovelAdapter
from .adapters.novel_bin import NovelBinAdapter
from .adapters.telegraph import TelegraphAdapter
from .adapters.webnovel_dynamic import WebNovelDynamicAdapter
from .models import SiteSpec


class AdapterDisabledError(RuntimeError):
    """Raised when an adapter is requested for a disabled catalog row."""


REGISTRY: dict[str, type[BaseAdapter]] = {}


def register(key: str, cls: type[BaseAdapter]) -> None:
    """Add an adapter class under ``key``. Raises on a duplicate key."""
    if key in REGISTRY:
        raise KeyError(f"Adapter key {key!r} is already registered.")
    REGISTRY[key] = cls


def get_adapter(key: str) -> BaseAdapter:
    """Instantiate and return the adapter registered under ``key``.

    Raises KeyError with a clear message if no adapter is registered.
    """
    try:
        cls = REGISTRY[key]
    except KeyError:
        known = ", ".join(sorted(REGISTRY)) or "(none)"
        raise KeyError(
            f"No adapter registered for key {key!r}. Known keys: {known}."
        ) from None
    return cls()


def get_adapter_for_spec(spec: SiteSpec) -> BaseAdapter:
    """Return an adapter for an *enabled* catalog row.

    Raises AdapterDisabledError if ``spec.enabled`` is False — the pipeline must
    never run a disabled site.
    """
    if not spec.enabled:
        raise AdapterDisabledError(
            f"Site {spec.adapter_key!r} for novel {spec.novel_slug!r} is "
            f"disabled (not yet available in this release)."
        )
    return get_adapter(spec.adapter_key)


# ── Registration (the single source of registered adapters) ──────────────────
register("freewebnovel", FreeWebNovelAdapter)
register("webnovel_dynamic", WebNovelDynamicAdapter)
register("empire_novel", EmpireNovelAdapter)
register("novel_bin", NovelBinAdapter)
register("telegraph", TelegraphAdapter)
