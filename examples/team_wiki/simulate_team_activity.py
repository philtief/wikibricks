"""Simulate multi-user activity on a team wiki.

Writes ~9 sample pages under three fake user paths and runs a few
searches so the wiki_log reflects realistic shared-team load. Useful
for screenshots, demos, and the "look how the same wiki is touched by
three users" walkthrough.

Usage:
    uv run python examples/team_wiki/simulate_team_activity.py \\
        --profile <databricks-profile> \\
        --catalog <catalog> --schema <schema> \\
        --warehouse-id <wh>
"""

import argparse
import os
import sys

FAKE_USERS = ["alice@example.com", "bob@example.com", "carol@example.com"]

SAMPLE_PAGES = {
    "alice@example.com": [
        ("sessions/alice@example.com/2026/05/15/aaa111",
         "Setting up a Lakeflow Designer pipeline",
         "Notes on configuring source connectors and sink Delta tables."),
        ("sessions/alice@example.com/2026/05/15/aaa222",
         "Debugging a slow Vector Search index",
         "Index sync was queued; bumped warehouse to L2 and resynced."),
        ("sessions/alice@example.com/2026/05/14/aaa333",
         "Onboarding the data quality monitor",
         "Profiled the table and configured anomaly thresholds."),
    ],
    "bob@example.com": [
        ("sessions/bob@example.com/2026/05/15/bbb111",
         "Wiring up the Genie space for sales analytics",
         "Created the Genie space, attached metric views, tested questions."),
        ("sessions/bob@example.com/2026/05/15/bbb222",
         "MLflow run tagging for the recommendation model",
         "Used custom tags to slice by model version and serving deployment."),
        ("sessions/bob@example.com/2026/05/14/bbb333",
         "Notebook timeout in the nightly job",
         "Found the long-running cell; refactored into Spark UDF."),
    ],
    "carol@example.com": [
        ("sessions/carol@example.com/2026/05/15/ccc111",
         "Adding a column-level lineage check",
         "Used UC system tables to trace a sensitive column end to end."),
        ("sessions/carol@example.com/2026/05/15/ccc222",
         "Setting up role-based access via UC ABAC",
         "Created the policy, attached it to the schema, tested with two users."),
        ("sessions/carol@example.com/2026/05/14/ccc333",
         "Investigating a Lakebase connection pool exhaustion",
         "Bumped max_connections, audited callers via Postgres extensions."),
    ],
}

SAMPLE_QUERIES = [
    "How do I debug a Vector Search sync issue?",
    "What's the right way to add column-level lineage?",
    "How are tags structured in MLflow runs?",
]


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profile", required=True, help="Databricks CLI profile")
    p.add_argument("--catalog", required=True)
    p.add_argument("--schema", required=True)
    p.add_argument("--warehouse-id", required=True)
    args = p.parse_args()

    os.environ["WIKIBRICKS_CATALOG"] = args.catalog
    os.environ["WIKIBRICKS_SCHEMA"] = args.schema

    from databricks.sdk import WorkspaceClient

    from wikibricks import WikiClient

    ws = WorkspaceClient(profile=args.profile)
    wiki = WikiClient(warehouse_id=args.warehouse_id, workspace_client=ws)

    print(f"Writing sample pages to {args.catalog}.{args.schema}")
    print(f"Three fake users: {', '.join(FAKE_USERS)}")

    n_written = 0
    for user, pages in SAMPLE_PAGES.items():
        for path, title, body in pages:
            wiki.write_page(
                path=path,
                title=title,
                content={"summary": title, "body": body},
                page_type="session",
                created_by=user,
                tags=["session", f"user:{user}"],
            )
            n_written += 1
    print(f"Wrote {n_written} pages")

    print()
    print("Running sample searches (each one logs a citation row):")
    for q in SAMPLE_QUERIES:
        results = wiki.search(q, num_results=3)
        print(f"  q='{q[:50]}' → {len(results)} hits")

    print()
    print("Done. Check wiki_log grouped by created_by:")
    print(f'  SELECT created_by, COUNT(*) FROM {args.catalog}.{args.schema}.wiki_log')
    print( '  WHERE created_at > current_timestamp() - INTERVAL 1 HOUR')
    print( '  GROUP BY created_by')
    return 0


if __name__ == "__main__":
    sys.exit(main())
