"""Phase 0 smoke test: the package imports and exposes a version."""

from __future__ import annotations


def test_package_imports_with_version() -> None:
    import webnovel_scraper

    assert hasattr(webnovel_scraper, "__version__"), "package missing __version__"
    assert webnovel_scraper.__version__ == "0.1.0"
