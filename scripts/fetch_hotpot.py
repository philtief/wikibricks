"""Fetch the HotpotQA dev-distractor file. Idempotent - caches under .cache/hotpot/."""

import sys
import urllib.request
from pathlib import Path

DEV_DISTRACTOR_URL = "http://curtis.ml.cmu.edu/datasets/hotpot/hotpot_dev_distractor_v1.json"
CACHE_DIR = Path(".cache/hotpot")


def fetch(url: str = DEV_DISTRACTOR_URL, cache_dir: Path = CACHE_DIR) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    dest = cache_dir / Path(url).name
    if dest.exists():
        print(f"[fetch_hotpot] cached: {dest} ({dest.stat().st_size:,} bytes)")
        return dest
    print(f"[fetch_hotpot] downloading {url} → {dest}")
    urllib.request.urlretrieve(url, dest)
    print(f"[fetch_hotpot] done: {dest.stat().st_size:,} bytes")
    return dest


if __name__ == "__main__":
    try:
        fetch()
    except Exception as e:
        print(f"[fetch_hotpot] failed: {e}", file=sys.stderr)
        sys.exit(1)
