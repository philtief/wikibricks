#!/usr/bin/env bash
# twowiki_batch_loop.sh - run 2Wiki generation in 250-query batches with quick eval between batches.
#
# Usage:
#   scripts/twowiki_batch_loop.sh [MODE] [BATCH_SIZE] [MAX_BATCHES]
#
# Example:
#   scripts/twowiki_batch_loop.sh HYBRID 250 50     # run up to 50 batches of 250 on HYBRID
#
# Resumes from the Delta checkpoint table on each batch - safe to Ctrl-C at any time.
# DO NOT launch this until the current IP is allowlisted on fe-vm-agent-marketplace.

set -euo pipefail

MODE=${1:-HYBRID}
BATCH_SIZE=${2:-250}
MAX_BATCHES=${3:-60}

MODEL=${TWOWIKI_MODEL:-databricks-claude-haiku-4-5}
WORKERS=${TWOWIKI_WORKERS:-20}

cd "$(dirname "$0")/.."

echo "mode=$MODE batch=$BATCH_SIZE max_batches=$MAX_BATCHES model=$MODEL workers=$WORKERS"

for i in $(seq 1 "$MAX_BATCHES"); do
  echo
  echo "=== batch $i/$MAX_BATCHES ($BATCH_SIZE queries) ==="
  TWOWIKI_MODES="$MODE" \
    TWOWIKI_MODEL="$MODEL" \
    TWOWIKI_WORKERS="$WORKERS" \
    TWOWIKI_BATCH_SIZE="$BATCH_SIZE" \
    TWOWIKI_CHECKPOINT_EVERY=50 \
    .venv/bin/python scripts/twowiki_04_generate.py

  echo
  echo "=== quick eval after batch $i ==="
  .venv/bin/python scripts/twowiki_quick_eval.py "$MODE"

  # Stop if nothing left to do (generate prints "0 to do this batch").
  REMAINING=$(.venv/bin/python -c "
import json, sys
from pathlib import Path
p = Path('data/twowiki/predictions_${MODE}.json')
if not p.exists():
    print(12576); sys.exit()
with open(p) as f:
    pred = json.load(f)
done = len(pred.get('answer', {}))
print(max(0, 12576 - done))
")
  echo "remaining after batch $i: $REMAINING"
  if [ "$REMAINING" -eq 0 ]; then
    echo "=== all queries complete for $MODE ==="
    break
  fi
done
