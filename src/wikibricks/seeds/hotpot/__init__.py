"""HotpotQA seed domain. Populated by `scripts/build_hotpot_seed.py` from the dev corpus."""

import json
import os
from pathlib import Path

_PAGES_JSONL = Path(__file__).parent / "pages.jsonl"


def pages() -> list[dict]:
    """Return HotpotQA seed pages, loaded from pages.jsonl if present.

    Set env `WIKIBRICKS_HOTPOT_PAGES` to override the path. Returns [] if the file is absent
    — run `python scripts/fetch_hotpot.py && python scripts/build_hotpot_seed.py` first.
    """
    path = Path(os.environ.get("WIKIBRICKS_HOTPOT_PAGES", _PAGES_JSONL))
    if not path.exists():
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]
