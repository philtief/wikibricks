"""Generic sample seed - pages describing WikiBricks itself. Domain-agnostic by design."""


def pages() -> list[dict]:
    return [
        {
            "path": "topics/getting-started",
            "title": "Getting Started with WikiBricks",
            "page_type": "concept",
            "content": {
                "summary": "Introduction to the WikiBricks wiki memory system.",
                "body": (
                    "WikiBricks provides a structured knowledge store backed by Delta tables "
                    "and Vector Search. Pages are organized in a path hierarchy and support "
                    "full-text, semantic, and hybrid search. Each page has a summary, body, "
                    "tags, and automatic version history."
                ),
            },
            "created_by": "setup",
            "tags": ["getting-started", "overview", "onboarding"],
        },
        {
            "path": "topics/architecture/overview",
            "title": "Architecture Overview",
            "page_type": "concept",
            "content": {
                "summary": "High-level architecture of the WikiBricks storage layer.",
                "body": (
                    "WikiBricks uses three Delta tables: pages (current state), pages_history "
                    "(archived versions), and links (page-to-page relationships). A Vector "
                    "Search index on the pages table enables semantic retrieval. Writes use "
                    "MERGE for upsert semantics and archive the previous version automatically."
                ),
            },
            "created_by": "setup",
            "tags": ["architecture", "delta", "vector-search"],
        },
        {
            "path": "guides/setup",
            "title": "Setup Guide",
            "page_type": "entity",
            "content": {
                "summary": "Step-by-step guide for deploying WikiBricks to a workspace.",
                "body": (
                    "Prerequisites: a Databricks workspace with Unity Catalog enabled and a "
                    "SQL warehouse. Steps: 1) Create the catalog and schema. 2) Run the table "
                    "creation DDL. 3) Create the Vector Search endpoint and index. 4) Seed "
                    "initial pages. 5) Verify with a search query."
                ),
            },
            "created_by": "setup",
            "tags": ["guide", "setup", "deployment"],
        },
        {
            "path": "guides/troubleshooting",
            "title": "Troubleshooting Common Issues",
            "page_type": "synthesis",
            "content": {
                "summary": "Solutions for frequently encountered problems.",
                "body": (
                    "Issue: search returns no results. Check that the Vector Search index has "
                    "synced after writing pages. Issue: PARSE_JSON fails. Ensure content JSON "
                    "does not contain unescaped backslashes or newlines. Issue: permission "
                    "denied. Grant USE CATALOG, USE SCHEMA, and SELECT to the service principal."
                ),
            },
            "created_by": "setup",
            "tags": ["guide", "troubleshooting", "faq"],
        },
        {
            "path": "comparisons/search-modes",
            "title": "Search Modes Comparison",
            "page_type": "comparison",
            "content": {
                "summary": "Comparison of ANN, full-text, and hybrid search modes.",
                "body": (
                    "ANN (approximate nearest neighbor): best for semantic similarity, uses "
                    "embedding vectors. Full-text: best for exact keyword matching and known "
                    "identifiers. Hybrid: combines both approaches and generally provides the "
                    "best results for natural-language queries. Default mode is HYBRID."
                ),
            },
            "created_by": "setup",
            "tags": ["comparison", "search", "vector-search"],
        },
    ]
