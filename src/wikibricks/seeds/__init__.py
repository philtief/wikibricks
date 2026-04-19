"""Seed-domain loaders. Each domain under this package exposes a `pages()` function returning seed wiki pages."""

from importlib import import_module


def load(domain: str = "insurance") -> list[dict]:
    """Load seed pages for the given domain. Domains live in subpackages: insurance/, hotpot/, custom/."""
    if domain in ("", "none"):
        return []
    try:
        mod = import_module(f"wikibricks.seeds.{domain}")
    except ModuleNotFoundError as e:
        raise ValueError(f"Unknown seed domain '{domain}'. Expected: insurance, hotpot, custom.") from e
    return mod.pages()
