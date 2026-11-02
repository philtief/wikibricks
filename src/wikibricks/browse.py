"""A local, read-only web view of the wiki: what changed, search, pages, and sessions."""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from wikibricks.curation.digest import undigested_pages
from wikibricks.storage.sqlite_store import SQLiteStore

_DIGEST_DAYS = 7
_MAX_SESSION_EVENTS = 500
_MAX_TOOL_CHARS = 5000


class _ReadOnlyStore(SQLiteStore):
    """Every connection opens the database read-only, so the browser cannot write."""

    def _open(self) -> sqlite3.Connection:
        uri = f"{self.database_path.resolve().as_uri()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn


_INLINE = re.compile(
    r"`(?P<code>[^`\n]+)`"
    r"|\*\*(?P<bold>[^*\n]+)\*\*"
    r"|\[\[(?P<wiki>[^\]\n]+)\]\]"
    r"|\[(?P<label>[^\]\n]+)\]\((?P<url>[^)\s]+)\)"
)
_PAGE_PATH = re.compile(r"[\w-]+(?:/[\w.-]+)+")
_HEADING = re.compile(r"(#{1,6})\s+(.*?)\s*#*\s*$")
_LIST_ITEM = re.compile(r"(?P<indent>[ \t]*)(?P<marker>[-*+]|\d+[.)])\s+(?P<text>.*)")
_TABLE_SEPARATOR = re.compile(r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*")
_RULE = re.compile(r"\s*([-*_])(?:\s*\1){2,}\s*")


def _href(url: str) -> str | None:
    if url.startswith(("https://", "http://")):
        return url
    if _PAGE_PATH.fullmatch(url):
        return "/page/" + quote(url)
    return None


def _link(href: str | None, label: str, source: str) -> str:
    return f'<a href="{escape(href)}">{escape(label)}</a>' if href else escape(source)


def _inline(text: str) -> str:
    """Render inline Markdown. All text is escaped; links go only to the web or wiki paths."""
    out, position = [], 0
    for match in _INLINE.finditer(text):
        out.append(escape(text[position : match.start()]))
        if match["code"] is not None:
            out.append(f"<code>{escape(match['code'])}</code>")
        elif match["bold"] is not None:
            out.append(f"<strong>{escape(match['bold'])}</strong>")
        elif match["wiki"] is not None:
            target = match["wiki"].split("|")[0].strip()
            out.append(_link(_href(target), target, match.group(0)))
        else:
            out.append(_link(_href(match["url"]), match["label"], match.group(0)))
        position = match.end()
    out.append(escape(text[position:]))
    return "".join(out)


def _list_html(items: list[tuple[int, bool, str]]) -> str:
    html: list[str] = []
    stack: list[tuple[int, str]] = []
    for depth, ordered, text in items:
        tag = "ol" if ordered else "ul"
        if stack and depth > stack[-1][0]:
            html.append(f"<{tag}>")
            stack.append((depth, tag))
        else:
            while stack and depth < stack[-1][0]:
                html.append(f"</li></{stack.pop()[1]}>")
            if stack and stack[-1][1] != tag:
                html.append(f"</li></{stack.pop()[1]}>")
            if stack:
                html.append("</li>")
            else:
                html.append(f"<{tag}>")
                stack.append((depth, tag))
        html.append(f"<li>{_inline(text)}")
    while stack:
        html.append(f"</li></{stack.pop()[1]}>")
    return "".join(html)


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table_starts(lines: list[str], index: int) -> bool:
    return bool(
        lines[index].lstrip().startswith("|")
        and index + 1 < len(lines)
        and _TABLE_SEPARATOR.fullmatch(lines[index + 1])
    )


def _starts_block(lines: list[str], index: int) -> bool:
    line = lines[index]
    return bool(
        line.lstrip().startswith(("```", ">"))
        or _HEADING.fullmatch(line)
        or _RULE.fullmatch(line)
        or _LIST_ITEM.fullmatch(line)
        or _table_starts(lines, index)
    )


def render_markdown(text: str) -> str:
    """Render the Markdown used in wiki pages: headings, lists, tables, code, and quotes."""
    lines = text.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip()
        if not line.strip():
            index += 1
        elif stripped.startswith("```"):
            end = index + 1
            while end < len(lines) and not lines[end].lstrip().startswith("```"):
                end += 1
            out.append(f"<pre><code>{escape(chr(10).join(lines[index + 1 : end]))}</code></pre>")
            index = end + 1
        elif heading := _HEADING.fullmatch(line):
            level = len(heading[1])
            out.append(f"<h{level}>{_inline(heading[2])}</h{level}>")
            index += 1
        elif _RULE.fullmatch(line):
            out.append("<hr>")
            index += 1
        elif _table_starts(lines, index):
            header = "".join(f"<th>{_inline(cell)}</th>" for cell in _cells(line))
            rows, index = [], index + 2
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                rows.append("".join(f"<td>{_inline(cell)}</td>" for cell in _cells(lines[index])))
                index += 1
            body = "".join(f"<tr>{row}</tr>" for row in rows)
            out.append(f'<div class="table"><table><tr>{header}</tr>{body}</table></div>')
        elif _LIST_ITEM.fullmatch(line):
            items: list[tuple[int, bool, str]] = []
            while index < len(lines) and (item := _LIST_ITEM.fullmatch(lines[index])):
                depth = len(item["indent"].expandtabs(4)) // 2
                items.append((depth, item["marker"][0].isdigit(), item["text"]))
                index += 1
                # An indented line that is not a new item continues the item above.
                while (
                    index < len(lines)
                    and lines[index][:1] in {" ", "\t"}
                    and lines[index].strip()
                    and not _LIST_ITEM.fullmatch(lines[index])
                ):
                    depth, ordered, previous = items[-1]
                    items[-1] = (depth, ordered, f"{previous} {lines[index].strip()}")
                    index += 1
            out.append(_list_html(items))
        elif stripped.startswith(">"):
            quoted = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                quoted.append(lines[index].lstrip()[1:].lstrip())
                index += 1
            out.append(f"<blockquote>{render_markdown(chr(10).join(quoted))}</blockquote>")
        else:
            paragraph = [line.strip()]
            index += 1
            while index < len(lines) and lines[index].strip() and not _starts_block(lines, index):
                paragraph.append(lines[index].strip())
                index += 1
            out.append(f"<p>{_inline(' '.join(paragraph))}</p>")
    return "\n".join(out)


_STYLE = """
:root{--ink:#1d2433;--muted:#5d6678;--line:#e3e6ec;--card:#fff;--page:#f6f7f9;--accent:#c4361d;
--chip:#eef0f4;--code:#f1f3f6}
@media (prefers-color-scheme:dark){:root{--ink:#e6e8ee;--muted:#9aa3b5;--line:#2c3342;--card:#1a1f2b;
--page:#12161f;--accent:#ff7a5c;--chip:#252c3a;--code:#222a38}}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,
"Segoe UI",Helvetica,Arial,sans-serif}
header{display:flex;gap:1.5rem;align-items:center;padding:.8rem 1.5rem;border-bottom:1px solid var(--line);
background:var(--card);position:sticky;top:0}
.brand{font-weight:700;color:var(--ink);text-decoration:none}
header form{flex:1;max-width:32rem}
header input{width:100%;padding:.45rem .7rem;border:1px solid var(--line);border-radius:6px;
background:var(--page);color:var(--ink);font:inherit}
main{max-width:56rem;margin:0 auto;padding:1.5rem}
a{color:var(--accent)}
h1{font-size:1.7rem;line-height:1.25;margin:.2rem 0 .6rem}
h2{font-size:1.15rem;margin:2rem 0 .7rem}
section>h2{display:flex;justify-content:space-between;align-items:baseline}
.count{color:var(--muted);font-weight:400;font-size:.9rem}
.cards{display:grid;gap:.7rem}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.85rem 1rem}
.card h3{margin:0;font-size:1rem}
.card h3 a{color:var(--ink);text-decoration:none}
.card h3 a:hover{color:var(--accent)}
.card p{margin:.35rem 0 0}
.meta{color:var(--muted);font-size:.85rem}
.chip{display:inline-block;background:var(--chip);border-radius:4px;padding:0 .4rem;margin-right:.3rem;
font-size:.78rem}
.chip.new{background:var(--accent);color:#fff}
.summary{font-size:1.08rem;background:var(--card);border-left:4px solid var(--accent);
padding:.8rem 1rem;border-radius:0 8px 8px 0;margin:1rem 0 1.5rem}
.body{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.4rem 1.4rem 1rem}
.body h2:first-child{margin-top:1rem}
code{background:var(--code);padding:.05rem .3rem;border-radius:4px;font-size:.88em}
pre{background:var(--code);padding:.8rem;border-radius:6px;overflow:auto}
pre code{padding:0;background:none}
.table{overflow-x:auto}
table{border-collapse:collapse;margin:.6rem 0}
th,td{border:1px solid var(--line);padding:.35rem .6rem;text-align:left;vertical-align:top}
blockquote{margin:.6rem 0;padding:0 1rem;border-left:3px solid var(--line);color:var(--muted)}
.links{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
.links ul,.history{padding-left:1.1rem}
.event{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:.3rem 1rem;margin:.6rem 0}
.event.user{border-left:4px solid var(--accent)}
details{margin:.4rem 0;color:var(--muted)}
details pre{white-space:pre-wrap}
.command{font-family:ui-monospace,Menlo,monospace;font-size:.85rem}
"""


def _layout(title: str, body: str, *, query: str = "") -> str:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)} · WikiBricks</title><style>{_STYLE}</style></head><body>"
        '<header><a class="brand" href="/">WikiBricks</a><form action="/search">'
        f'<input name="q" value="{escape(query)}" placeholder="Search the wiki" aria-label="Search">'
        f"</form></header><main>{body}</main></body></html>"
    )


def _date(value: str | None) -> str:
    return escape(str(value or "")[:10])


def _page_link(path: str, title: str) -> str:
    return f'<a href="/page/{quote(path)}">{escape(title)}</a>'


def _card(path: str, title: str, summary: str, meta: str) -> str:
    return (
        f'<article class="card"><h3>{_page_link(path, title)}</h3>'
        f'<p class="meta">{meta}</p><p>{escape(summary)}</p></article>'
    )


def _section(title: str, count: int, content: str) -> str:
    return f'<section><h2>{escape(title)} <span class="count">{count}</span></h2>{content}</section>'


def _home(store: _ReadOnlyStore) -> str:
    since = (datetime.now(timezone.utc) - timedelta(days=_DIGEST_DAYS)).isoformat()
    with store.connection() as conn:
        changed = conn.execute(
            "SELECT p.path, v.title, v.page_type, v.content, v.version, v.created_at, v.created_by "
            "FROM pages p JOIN page_versions v ON v.version_id = p.current_version_id "
            "WHERE p.status = 'active' AND p.path NOT LIKE '_meta/%' "
            "AND v.page_type NOT IN ('session', 'archive') AND v.created_at >= ? "
            "ORDER BY v.created_at DESC",
            (since,),
        ).fetchall()
        meetings = conn.execute(
            "SELECT page_path, title, coalesce(started_at, updated_at) FROM sessions "
            "WHERE harness = 'meeting-notes' AND coalesce(started_at, updated_at) >= ? "
            "ORDER BY 3 DESC",
            (since,),
        ).fetchall()
        reviews = conn.execute(
            "SELECT p.run_id, p.group_id, p.operation, p.path, p.reason FROM curation_patches p "
            "LEFT JOIN curation_receipts r ON r.patch_id = p.patch_id "
            "WHERE r.patch_id IS NULL ORDER BY p.run_id, p.group_id, p.position"
        ).fetchall()
        totals = conn.execute(
            "SELECT (SELECT count(*) FROM pages p JOIN page_versions v "
            "ON v.version_id = p.current_version_id WHERE p.status = 'active' "
            "AND p.path NOT LIKE '_meta/%' AND v.page_type NOT IN ('session', 'archive')), "
            "(SELECT count(*) FROM sessions)"
        ).fetchone()
        undigested = len(undigested_pages(conn))
    cards = "".join(
        _card(
            row["path"],
            row["title"],
            str(json.loads(row["content"]).get("summary") or ""),
            ('<span class="chip new">new</span>' if row["version"] == 1 else '<span class="chip">updated</span>')
            + f'<span class="chip">{escape(row["path"].split("/")[0])}</span>'
            + f"{_date(row['created_at'])} · {escape(row['created_by'])}",
        )
        for row in changed
    )
    meeting_items = "".join(
        f"<li>{_page_link(row[0], row[1])} <span class=\"meta\">{_date(row[2])}</span></li>"
        for row in meetings
    )
    groups: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in reviews:
        groups.setdefault((row["run_id"], row["group_id"]), []).append(row)
    review_cards = "".join(
        '<article class="card">'
        f"<p>{escape(patches[0]['reason'])}</p>"
        "<p class=\"meta\">"
        + ", ".join(f"{escape(patch['operation'])} {_page_link(patch['path'], patch['path'])}" for patch in patches)
        + f'</p><p class="command">wikibricks sync plan {escape(run_id)}<br>'
        f"wikibricks sync apply {escape(run_id)} --policy all</p></article>"
        for (run_id, _group), patches in groups.items()
    )
    body = (
        f"<h1>This week</h1><p class=\"meta\">{totals[0]} pages, {totals[1]} sessions, "
        f"{undigested} pages still without a short summary or key points</p>"
        + _section("Changed in the last 7 days", len(changed), f'<div class="cards">{cards}</div>')
        + _section("Meetings imported", len(meetings), f"<ul>{meeting_items}</ul>")
        + _section("Waiting for review", len(groups), f'<div class="cards">{review_cards}</div>')
    )
    return _layout("This week", body)


def _search(store: _ReadOnlyStore, query: dict[str, list[str]]) -> str:
    text = query.get("q", [""])[0]
    with_sessions = query.get("sessions") == ["1"]
    hits = store.search(text, num_results=50) if text.strip() else []
    if not with_sessions:
        hits = [hit for hit in hits if hit["page_type"] != "session"]
    toggle = (
        f'<a href="/search?q={quote(text)}">Pages only</a>'
        if with_sessions
        else f'<a href="/search?q={quote(text)}&amp;sessions=1">Include sessions</a>'
    )
    cards = "".join(
        _card(
            hit["path"],
            hit["title"],
            hit.get("snippet") or "",
            f'<span class="chip">{escape(hit["page_type"])}</span>{escape(hit["path"])}',
        )
        for hit in hits
    )
    body = (
        f"<h1>Search</h1><p class=\"meta\">{len(hits)} results for “{escape(text)}” · {toggle}</p>"
        f'<div class="cards">{cards}</div>'
    )
    return _layout(f"Search: {text}", body, query=text)


def _session(page: dict[str, Any], *, with_tools: bool) -> str:
    events = page["events"]
    messages = [event for event in events if event["kind"] in {"user", "assistant"}]
    shown = events if with_tools else messages
    items = []
    for event in shown[:_MAX_SESSION_EVENTS]:
        if event["kind"] in {"user", "assistant"}:
            items.append(
                f'<div class="event {escape(event["kind"])}"><p class="meta">{escape(event["kind"])}</p>'
                f"{render_markdown(event['content'])}</div>"
            )
        else:
            items.append(
                f"<details><summary>{escape(event['kind'])}</summary>"
                f"<pre>{escape(event['content'][:_MAX_TOOL_CHARS])}</pre></details>"
            )
    hidden = len(events) - len(messages)
    toggle = (
        f'<a href="/page/{quote(page["path"])}">Hide tool events</a>'
        if with_tools
        else f'<a href="/page/{quote(page["path"])}?tools=1">Show {hidden} tool events</a>'
        if hidden
        else ""
    )
    capped = (
        f"<p class=\"meta\">Showing the first {_MAX_SESSION_EVENTS} of {len(shown)} events.</p>"
        if len(shown) > _MAX_SESSION_EVENTS
        else ""
    )
    body = (
        f"<h1>{escape(page['title'])}</h1><p class=\"meta\"><span class=\"chip\">session</span>"
        f"{_date(page['created_at'])} · {len(messages)} messages · {toggle}</p>"
        + capped
        + "".join(items)
    )
    return _layout(page["title"], body)


def _page(store: _ReadOnlyStore, path: str, query: dict[str, list[str]]) -> tuple[int, str]:
    page = store.read_page(path)
    if page is None:
        return 404, _layout("Not found", f"<h1>Not found</h1><p>No page at {escape(path)}.</p>")
    if page["page_type"] == "session":
        return 200, _session(page, with_tools=query.get("tools") == ["1"])
    outgoing = store.graph_neighbors(page["path"])
    with store.connection() as conn:
        backlinks = conn.execute(
            "SELECT source.path, v.title, l.link_type FROM links l "
            "JOIN pages source ON source.page_id = l.source_page_id "
            "JOIN pages target ON target.page_id = l.target_page_id "
            "JOIN page_versions v ON v.version_id = source.current_version_id "
            "WHERE target.path = ? AND source.status = 'active' ORDER BY source.path",
            (page["path"],),
        ).fetchall()
    history = store.history(page["path"])
    content = page["content"]
    outgoing_items = "".join(
        f"<li>{_page_link(link['path'], link['title'])} <span class=\"meta\">{escape(link['link_type'])}</span></li>"
        for link in outgoing
    )
    backlink_items = "".join(
        f"<li>{_page_link(row[0], row[1])} <span class=\"meta\">{escape(row[2])}</span></li>"
        for row in backlinks
    )
    history_items = "".join(
        f"<li>v{version['version']} · {_date(version['created_at'])} · {escape(version['created_by'])}</li>"
        for version in history
    )
    tags = "".join(f'<span class="chip">{escape(tag)}</span>' for tag in page["tags"])
    body = (
        f"<h1>{escape(page['title'])}</h1>"
        f'<p class="meta"><span class="chip">{escape(page["page_type"])}</span>{escape(page["path"])} · '
        f"v{page['version']} · updated {_date(page['updated_at'])} by {escape(page['created_by'])} {tags}</p>"
        f'<p class="summary">{escape(str(content.get("summary") or ""))}</p>'
        f'<div class="body">{render_markdown(str(content.get("body") or ""))}</div>'
        '<div class="links">'
        + _section("Links to", len(outgoing), f"<ul>{outgoing_items}</ul>")
        + _section("Linked from", len(backlinks), f"<ul>{backlink_items}</ul>")
        + "</div>"
        + _section("History", len(history), f'<ul class="history">{history_items}</ul>')
    )
    return 200, _layout(page["title"], body)


def route(database_path: str | Path, path: str, query: dict[str, list[str]]) -> tuple[int, str]:
    """Return the status and HTML for one request."""
    store = _ReadOnlyStore(database_path)
    if path == "/":
        return 200, _home(store)
    if path == "/search":
        return 200, _search(store, query)
    if path.startswith("/page/"):
        return _page(store, path[len("/page/") :], query)
    return 404, _layout("Not found", "<h1>Not found</h1>")


def make_server(database_path: str | Path, *, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - http.server API
            bound = self.server.server_address[1]
            # A page on another site could reach a local port through DNS rebinding.
            if self.headers.get("Host") not in {f"{name}:{bound}" for name in (host, "localhost", "127.0.0.1")}:
                status, html = 403, _layout("Forbidden", "<h1>Forbidden</h1>")
            else:
                url = urlsplit(self.path)
                status, html = route(database_path, unquote(url.path), parse_qs(url.query))
            data = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args: Any) -> None:
            pass

    return ThreadingHTTPServer((host, port), Handler)


def serve(database_path: str | Path, *, host: str = "127.0.0.1", port: int = 8765) -> None:
    server = make_server(database_path, host=host, port=port)
    print(f"WikiBricks is browsable at http://{host}:{port}/ (Ctrl-C stops it)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


__all__ = ["make_server", "render_markdown", "route", "serve"]
