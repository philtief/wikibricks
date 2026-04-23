"""Seed loaders. Each sub-package exposes a `pages()` function returning seed wiki pages.

WikiBricks is domain-agnostic. The `sample/` loader provides generic meta-pages describing
WikiBricks itself; real deployments supply their own corpus via `custom/` or an ingestion
script.
"""

from importlib import import_module


def load(domain: str = "sample") -> list[dict]:
    """Load seed pages for the given domain. Built-ins: sample, custom. `none` returns []."""
    if domain in ("", "none"):
        return []
    try:
        mod = import_module(f"wikibricks.seeds.{domain}")
    except ModuleNotFoundError as e:
        raise ValueError(f"Unknown seed domain '{domain}'. Expected: sample, custom.") from e
    return mod.pages()
