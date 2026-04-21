"""Fetch official 2WikiMultiHopQA assets.

Downloads from the Dropbox links published at
https://github.com/Alab-NII/2wikimultihop and vendors the official evaluator
from the GitHub repo. Idempotent - caches under .cache/twowiki/.

Assets:
  - data_ids_april7.zip  (dev/train with IDs + id_aliases.json, latest sent-seg)
  - 2wikimultihop_evaluate_v1.1.py  (vendored to vendor/)

Final layout:
  data/twowiki/raw/
    dev.json
    train.json
    id_aliases.json
  vendor/
    2wikimultihop_evaluate_v1.1.py
"""

import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

CACHE = Path(".cache/twowiki")
RAW = Path("data/twowiki/raw")
VENDOR = Path("vendor")

DATA_IDS_URL = "https://www.dropbox.com/s/ms2m13252h6xubs/data_ids_april7.zip?dl=1"
EVAL_SCRIPT_URL = (
    "https://raw.githubusercontent.com/Alab-NII/2wikimultihop/master/"
    "2wikimultihop_evaluate_v1.1.py"
)


def _download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  cached: {dest} ({dest.stat().st_size:,} bytes)")
        return dest
    print(f"  downloading {url}\n    → {dest}")
    urllib.request.urlretrieve(url, dest)
    print(f"  done: {dest.stat().st_size:,} bytes")
    return dest


def fetch_data_ids() -> None:
    zip_path = CACHE / "data_ids_april7.zip"
    _download(DATA_IDS_URL, zip_path)

    extract_dir = CACHE / "data_ids_april7"
    if not extract_dir.exists() or not any(extract_dir.iterdir()):
        print(f"  extracting → {extract_dir}")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)

    RAW.mkdir(parents=True, exist_ok=True)
    wanted = ["dev.json", "train.json", "id_aliases.json"]
    for name in wanted:
        matches = list(extract_dir.rglob(name))
        if not matches:
            print(f"  WARN: {name} not found in archive", file=sys.stderr)
            continue
        src = matches[0]
        dst = RAW / name
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            print(f"  ok: {dst}")
            continue
        shutil.copy2(src, dst)
        print(f"  copied {src.name} → {dst} ({dst.stat().st_size:,} bytes)")


def fetch_eval_script() -> None:
    VENDOR.mkdir(parents=True, exist_ok=True)
    _download(EVAL_SCRIPT_URL, VENDOR / "2wikimultihop_evaluate_v1.1.py")


def main() -> None:
    print("=== 2WikiMultiHopQA asset fetch ===")
    print("\n[1/2] dataset (data_ids_april7.zip)")
    fetch_data_ids()
    print("\n[2/2] evaluator")
    fetch_eval_script()

    print("\n=== summary ===")
    for p in sorted((RAW).glob("*")) + sorted(VENDOR.glob("2wikimultihop*.py")):
        print(f"  {p}  ({p.stat().st_size:,} bytes)")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"fetch_twowiki failed: {e}", file=sys.stderr)
        sys.exit(1)
