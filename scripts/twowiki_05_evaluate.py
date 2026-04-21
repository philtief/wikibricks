"""2WikiMultiHopQA - step 5: run official evaluator per mode, aggregate metrics.

Invokes vendor/2wikimultihop_evaluate_v1.1.py on each predictions_{mode}.json
with data/twowiki/raw/dev.json + id_aliases.json. Parses the JSON metrics the
eval script prints on stdout and aggregates into data/twowiki/metrics.json.

Env:
  TWOWIKI_MODES=HYBRID,ANN,FULL_TEXT  (subset supported)
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

EVAL_SCRIPT = Path("vendor/2wikimultihop_evaluate_v1.1.py")
GOLD_FILE = Path("data/twowiki/raw/dev.json")
ALIAS_FILE = Path("data/twowiki/raw/id_aliases.json")
IN_DIR = Path("data/twowiki")
OUT_PATH = Path("data/twowiki/metrics.json")

DEFAULT_MODES = ["HYBRID", "ANN", "FULL_TEXT"]
MODES = [m.strip() for m in os.environ.get(
    "TWOWIKI_MODES", ",".join(DEFAULT_MODES)).split(",") if m.strip()]


def parse_metrics(stdout: str) -> dict:
    """The eval script prints a missing-keys list (optional) and finally
    `print(json.dumps(metrics, indent=4))`. Extract the trailing JSON block."""
    m = re.search(r"\{[^{}]*\"em\".*?\}", stdout, re.DOTALL)
    if not m:
        raise RuntimeError(f"could not parse metrics from stdout:\n{stdout[-500:]}")
    return json.loads(m.group(0))


def run_one(mode: str) -> tuple[dict, int]:
    pred_path = IN_DIR / f"predictions_{mode}.json"
    if not pred_path.exists():
        raise FileNotFoundError(pred_path)

    cmd = [sys.executable, str(EVAL_SCRIPT), str(pred_path),
           str(GOLD_FILE), str(ALIAS_FILE)]
    print(f"\n=== eval: {mode} ===")
    print("  " + " ".join(cmd))
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True)
    dt = time.time() - t0
    if r.returncode != 0:
        print(f"  FAIL ({dt:.0f}s) rc={r.returncode}")
        print(f"  stderr: {r.stderr[-500:]}")
        raise RuntimeError(f"eval failed for {mode}")
    missing = r.stdout.count("missing")
    metrics = parse_metrics(r.stdout)
    print(f"  ok ({dt:.0f}s) - missing={missing}")
    print(f"  {json.dumps(metrics)}")
    return metrics, missing


def main() -> None:
    if not EVAL_SCRIPT.exists():
        print(f"missing {EVAL_SCRIPT} - run fetch_twowiki.py first", file=sys.stderr)
        sys.exit(1)
    if not GOLD_FILE.exists() or not ALIAS_FILE.exists():
        print("missing dev.json or id_aliases.json - run fetch_twowiki.py", file=sys.stderr)
        sys.exit(1)

    results: dict[str, dict] = {}
    for mode in MODES:
        try:
            metrics, missing = run_one(mode)
            results[mode] = {**metrics, "missing": missing}
        except FileNotFoundError as e:
            print(f"  skip {mode}: {e}")
            continue
        except RuntimeError as e:
            print(f"  {mode}: {e}")
            results[mode] = {"error": str(e)}

    out = {
        "modes": results,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "eval_script": str(EVAL_SCRIPT),
        "config": {
            "retrieve_k": int(os.environ.get("TWOWIKI_RETRIEVE_K", "20")),
            "context_k": int(os.environ.get("TWOWIKI_CONTEXT_K", "5")),
            "model": os.environ.get("TWOWIKI_MODEL", "databricks-claude-sonnet-4-6"),
        },
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT_PATH, "w") as f:
        json.dump(out, f, indent=2)
    print(f"\n=== wrote {OUT_PATH} ===")


if __name__ == "__main__":
    main()
