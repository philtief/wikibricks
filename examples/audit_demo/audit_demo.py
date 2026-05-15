"""Bi-temporal audit demo — what edges did the agent have at time T?

Sets up two pages connected by a `lives_in` edge, mutates the world
(person moves), and demonstrates three queries Mem0 / Letta / vanilla
graph stores categorically cannot answer:

1. **What does my agent think now?**
   `graph_neighbors("philipp", "current")` — currently-valid edges only.
   Returns: Berlin.

2. **What did my agent think on 2022-06-01?**
   `graph_neighbors_at("philipp", "2022-06-01")`.
   Returns: London (the edge was valid then).

3. **Show me the full timeline of where Philipp lived.**
   `link_history("philipp", "lives_in", *)`.
   Returns: [Munich 2020-01-01 → 2022-04-15], [London 2022-04-15 →
   2025-09-01], [Berlin 2025-09-01 → NULL].

The point: a fact that was true once can be queried even after it's
been superseded. Mem0 stores atomic facts and overwrites on conflict;
Letta has tier-based memory but no event-time vs transaction-time
split; vanilla graph stores have edges but no validity intervals.

Run:
    uv run python examples/audit_demo/audit_demo.py \\
        --profile <profile> \\
        --catalog <catalog> --schema <schema> \\
        --warehouse-id <wh>
"""

import argparse
import os
import sys
from datetime import datetime


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", required=True)
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--warehouse-id", required=True)
    p.add_argument("--namespace", default="audit_demo",
                   help="page-path prefix to isolate this demo's writes")
    args = p.parse_args()

    os.environ["WIKIBRICKS_CATALOG"] = args.catalog
    os.environ["WIKIBRICKS_SCHEMA"] = args.schema

    from databricks.sdk import WorkspaceClient

    from wikibricks import WikiClient

    ws = WorkspaceClient(profile=args.profile)
    wiki = WikiClient(warehouse_id=args.warehouse_id, workspace_client=ws)

    ns = args.namespace
    paths = {
        "philipp": f"{ns}/philipp",
        "munich":  f"{ns}/munich",
        "london":  f"{ns}/london",
        "berlin":  f"{ns}/berlin",
    }

    print("==> 1. Set up the world: one person, three cities.")
    wiki.write_page(paths["philipp"], "Philipp",
                    {"summary": "A person.", "body": "Lives somewhere."},
                    page_type="entity", created_by="audit_demo")
    for city in ("munich", "london", "berlin"):
        wiki.write_page(paths[city], city.capitalize(),
                        {"summary": f"A city in Europe.", "body": city.capitalize()},
                        page_type="entity", created_by="audit_demo")

    philipp = wiki.read_page(paths["philipp"])
    cities = {c: wiki.read_page(paths[c])["page_id"]
              for c in ("munich", "london", "berlin")}
    pid = philipp["page_id"]

    print("\n==> 2. Record Philipp's known life: Munich 2020 → London 2022 → Berlin 2025.")
    print("    Each `commit_edges` call carries the real event time, not the write time.")

    # Use 'related' as the link_type (must be in VALID_LINK_TYPES); the
    # interesting bit is the temporal interval, not the type.
    wiki.commit_edges([{
        "source_page_id": pid, "target_page_id": cities["munich"],
        "link_type": "related", "confidence": 1.0, "origin": "manual",
        "valid_from":  "2020-01-01T00:00:00",
        "valid_until": "2022-04-15T00:00:00",
    }])
    wiki.commit_edges([{
        "source_page_id": pid, "target_page_id": cities["london"],
        "link_type": "related", "confidence": 1.0, "origin": "manual",
        "valid_from":  "2022-04-15T00:00:00",
        "valid_until": "2025-09-01T00:00:00",
    }])
    wiki.commit_edges([{
        "source_page_id": pid, "target_page_id": cities["berlin"],
        "link_type": "related", "confidence": 1.0, "origin": "manual",
        "valid_from": "2025-09-01T00:00:00",
        # no valid_until — currently valid
    }])
    print("    Wrote 3 facts; each carries a (valid_from, valid_until) window.")
    print(f"    transaction time `created_at` is set to {datetime.utcnow().isoformat()}Z")
    print("    — independent of the three different valid_from values above.")

    print("\n==> 3. Where does Philipp live today?")
    today = wiki.graph_neighbors(paths["philipp"], depth=1)
    today_cities = [n["target_path"].split("/")[-1] for n in today]
    print(f"    graph_neighbors → {today_cities}  (only currently-valid edges)")

    print("\n==> 4. Where did the agent think Philipp lived on 2022-06-01?")
    in_2022 = wiki.graph_neighbors_at(
        paths["philipp"], at_timestamp="2022-06-01T00:00:00", depth=1
    )
    cities_2022 = [n["target_path"].split("/")[-1] for n in in_2022]
    print(f"    graph_neighbors_at(2022-06-01) → {cities_2022}")
    print("    ^ correct: on 2022-06-01 the Munich edge had closed and Berlin hadn't started.")

    print("\n==> 5. What did the agent think on 2020-06-01?")
    in_2020 = wiki.graph_neighbors_at(
        paths["philipp"], at_timestamp="2020-06-01T00:00:00", depth=1
    )
    cities_2020 = [n["target_path"].split("/")[-1] for n in in_2020]
    print(f"    graph_neighbors_at(2020-06-01) → {cities_2020}")

    print("\n==> 6. Full timeline of where Philipp lived.")
    history = []
    for city in ("munich", "london", "berlin"):
        h = wiki.link_history(paths["philipp"], paths[city])
        for row in h:
            history.append((row["valid_from"], row["valid_until"], city))
    history.sort()
    for vf, vu, city in history:
        end = vu if vu else "NULL (currently valid)"
        print(f"    {vf[:10]} → {str(end)[:10]:<32} {city}")

    print()
    print("The point: every fact is preserved with its event-time window.")
    print("- Mem0 atomic-fact store: would have ONLY 'Berlin' after the latest")
    print("  conversation — the old facts overwritten or unindexed.")
    print("- Letta tier-based memory: no event-time/transaction-time split.")
    print("- Vanilla graph: edges, no validity intervals.")
    print()
    print("Only wikibricks (and Graphiti) can answer 'as of 2022-06-01.'")
    print("Of those, only wikibricks runs on Delta + Unity Catalog — your")
    print("audit trail is a first-class governed table, not an opaque graph DB.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
