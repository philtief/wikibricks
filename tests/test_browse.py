from __future__ import annotations

import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

import pytest

from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.storage.sqlite_store import SQLiteStore


def test_markdown_renders_structure_and_escapes_markup():
    from wikibricks.browse import render_markdown

    html = render_markdown(
        "# Title\n\n## Key points\n\n- **Lakebase** chosen\n  - owner: data team\n1. first\n\n"
        "| Area | State |\n|---|---|\n| Sync | `done` |\n\n"
        "```\n<b>code</b>\n```\n\n"
        "<script>alert(1)</script> see [docs](https://docs.databricks.com), [[topics/contoso]], "
        "[bad](javascript:alert(1)) and [page](synthesis/plan)."
    )

    assert "<h1>Title</h1>" in html and "<h2>Key points</h2>" in html
    assert "<li><strong>Lakebase</strong> chosen<ul><li>owner: data team</li></ul></li>" in html
    assert "<ol><li>first</li></ol>" in html
    assert "<th>Area</th>" in html and "<td><code>done</code></td>" in html
    assert "<pre><code>&lt;b&gt;code&lt;/b&gt;</code></pre>" in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert '<a href="https://docs.databricks.com">docs</a>' in html
    assert '<a href="/page/topics/contoso">topics/contoso</a>' in html
    assert '<a href="/page/synthesis/plan">page</a>' in html
    assert "javascript:" not in html.replace("[bad](javascript:alert(1))", "")


def _store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "wikibricks.db")
    store.migrate()
    store.write_page(
        "topics/contoso",
        "Contoso",
        {
            "summary": "Contoso chose Lakebase.",
            "body": "# Contoso\n\n## Key points\n\n- Lakebase\n\n<script>x</script>",
        },
        page_type="entity",
    )
    store.write_page("synthesis/plan", "Plan", {"summary": "The plan.", "body": "Builds on [[topics/contoso]]."})
    store.commit_edges(
        [{"source_path": "synthesis/plan", "target_path": "topics/contoso", "link_type": "related"}]
    )
    store.ingest_session(
        SessionRecord(
            harness="meeting-notes",
            external_id="contoso-sync",
            user_id="u",
            metadata={"title": "Contoso weekly sync"},
            events=[
                SessionEvent("0", "user", "Contoso migration status"),
                SessionEvent("1", "tool_result", "raw tool output"),
            ],
        )
    )
    return store


def test_browse_shows_digest_search_pages_and_sessions_read_only(tmp_path: Path):
    from wikibricks.browse import route

    store = _store(tmp_path)
    session_path = store.list_pages("meeting-notes")[0]["path"]
    # Reads succeed while another process holds the write lock.
    with store.connection(write=True):
        status, home = route(store.database_path, "/", {})
        search = route(store.database_path, "/search", {"q": ["Contoso"]})[1]
        with_sessions = route(store.database_path, "/search", {"q": ["Contoso"], "sessions": ["1"]})[1]
        page = route(store.database_path, "/page/topics/contoso", {})[1]
        session = route(store.database_path, f"/page/{session_path}", {})[1]
        missing = route(store.database_path, "/page/topics/none", {})[0]

    assert status == 200
    assert "Contoso chose Lakebase." in home and 'href="/page/topics/contoso"' in home
    assert "Contoso weekly sync" in home
    assert 'href="/page/topics/contoso"' in search and session_path not in search
    assert "Contoso chose Lakebase." in search and "Key points" not in search  # the card, not a snippet
    assert session_path in with_sessions
    assert "<h2>Key points</h2>" in page and "<script>" not in page
    assert page.count("<h1>") == 1  # the body's own title line is not repeated
    assert 'href="/page/synthesis/plan"' in page  # backlink
    assert "Contoso migration status" in session and "raw tool output" not in session
    assert missing == 404


def test_browser_serves_localhost_only_with_a_strict_policy(tmp_path: Path):
    from wikibricks.browse import make_server

    store = _store(tmp_path)
    server = make_server(store.database_path, host="127.0.0.1", port=0)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(f"{base}/page/topics/contoso") as response:
            assert response.status == 200
            assert response.headers["Content-Security-Policy"].startswith("default-src 'none'")
        with pytest.raises(HTTPError) as rejected:
            opener.open(Request(f"{base}/", headers={"Host": "attacker.example"}))
        assert rejected.value.code == 403
    finally:
        server.shutdown()
        server.server_close()


def test_cli_browse_serves_the_local_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from wikibricks import browse, cli

    calls = []
    monkeypatch.setattr(browse, "serve", lambda path, **options: calls.append((path, options)))

    assert cli.main(["--database-path", str(tmp_path / "w.db"), "browse", "--port", "9000"]) == 0
    assert calls == [(tmp_path / "w.db", {"host": "127.0.0.1", "port": 9000})]

