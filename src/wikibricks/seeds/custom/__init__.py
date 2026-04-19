"""Custom seed domain — user-supplied pages via env or file. Empty by default."""

import json
import os
from pathlib import Path


def pages() -> list[dict]:
    """Return user-supplied seed pages from `WIKIBRICKS_CUSTOM_PAGES` (path to JSONL). Empty if unset."""
    path = os.environ.get("WIKIBRICKS_CUSTOM_PAGES")
    if not path or not Path(path).exists():
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]
