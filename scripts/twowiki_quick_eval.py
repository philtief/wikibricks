"""2WikiMultiHopQA — quick per-batch eval.

For a given mode:
  1. Read data/twowiki/predictions_{mode}.json
  2. Subset data/twowiki/raw/dev.json to the qids we have predictions for
  3. Run the official v1.1 evaluator on that subset
  4. Print the six metrics (Answer EM/F1, Sup EM/F1, Evi EM/F1, Joint EM/F1)

Usage:
  .venv/bin/python scripts/twowiki_quick_eval.py HYBRID
"""

import json
import re
import subprocess
import sys
from pathlib import Path

EVAL = Path("vendor/2wikimultihop_evaluate_v1.1.py")
GOLD = Path("data/twowiki/raw/dev.json")
ALIAS = Path("data/twowiki/raw/id_aliases.json")
OUT_DIR = Path("data/twowiki")


def parse_metrics(stdout: str) -> dict:
    m = re.search(r"\{[^{}]*\"em\".*?\}", stdout, re.DOTALL)
    if not m:
        raise RuntimeError(f"could not parse metrics from:\n{stdout[-500:]}")
    return json.loads(m.group(0))


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: twowiki_quick_eval.py <MODE>", file=sys.stderr)
        sys.exit(1)
    mode = sys.argv[1]

    pred_path = OUT_DIR / f"predictions_{mode}.json"
    with open(pred_path) as f:
        pred = json.load(f)
    pred_qids = set(pred.get("answer", {}).keys())
    print(f"predictions for {len(pred_qids):,} queries in {mode}")

    with open(GOLD) as f:
        gold = json.load(f)
    subset = [dp for dp in gold if dp["_id"] in pred_qids]
    print(f"matched {len(subset):,} gold records")

    subset_path = OUT_DIR / f"dev_subset_{mode}.json"
    with open(subset_path, "w") as f:
        json.dump(subset, f)

    r = subprocess.run(
        [sys.executable, str(EVAL), str(pred_path), str(subset_path), str(ALIAS)],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        print("eval failed", file=sys.stderr)
        print(r.stderr[-1000:], file=sys.stderr)
        sys.exit(1)

    metrics = parse_metrics(r.stdout)
    print(f"\n=== {mode} over {len(subset):,} preds ===")
    print(f"  Ans   EM={metrics['em']:6.2f}  F1={metrics['f1']:6.2f}")
    print(f"  Sup   EM={metrics['sp_em']:6.2f}  F1={metrics['sp_f1']:6.2f}")
    print(f"  Evi   EM={metrics['evi_em']:6.2f}  F1={metrics['evi_f1']:6.2f}")
    print(f"  Joint EM={metrics['joint_em']:6.2f}  F1={metrics['joint_f1']:6.2f}")

    summary_path = OUT_DIR / f"quick_eval_{mode}.json"
    with open(summary_path, "w") as f:
        json.dump({"n": len(subset), **metrics}, f, indent=2)
    print(f"  wrote {summary_path}")


if __name__ == "__main__":
    main()
