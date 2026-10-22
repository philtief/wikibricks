#!/usr/bin/env python3
"""Open a prefilled meeting note for the calendar event that just ran.

Minutes for a meeting no platform transcribes still have to come from you, but
they should not also cost you the typing of who was there and when. This reads
the event from Google Calendar, writes the note with its frontmatter already
filled in, and opens it so you only dictate or type the substance. The note
lands in the WikiBricks drop folder, so the daily job imports it and the curator
routes it to the right account page.

Authentication is the same as the Meet connector: the token `gcloud` already
holds, plus the Databricks quota project header.

Usage:
    wikibricks-note                 # the most recently started event
    wikibricks-note --list          # show today's events and their indexes
    wikibricks-note --event 2       # pick one from that list
    wikibricks-note --title "Ad-hoc call with the Allianz team"
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CALENDAR = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
DEFAULT_OUT = Path.home() / ".wikibricks" / "inbox" / "meetings"
DEFAULT_QUOTA_PROJECT = os.environ.get("GOOGLE_CLOUD_QUOTA_PROJECT", "gcp-dev-field-eng-aiapiquota")
_SLUG = re.compile(r"[^a-z0-9]+")
TEMPLATE = """## Decisions

## Open questions

## Next steps
"""


def resolve_token() -> str:
    result = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"no Google access token: {result.stderr.strip()}")
    return result.stdout.strip()


def recent_events(*, hours: int) -> list[dict[str, Any]]:
    """List timed events that started within the last `hours`, newest first."""
    parameters = urllib.parse.urlencode(
        {
            "timeMin": (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "timeMax": (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "singleEvents": "true",
            "orderBy": "startTime",
            "maxResults": "25",
        }
    )
    request = urllib.request.Request(
        f"{CALENDAR}?{parameters}",
        headers={
            "Authorization": f"Bearer {resolve_token()}",
            "x-goog-user-project": DEFAULT_QUOTA_PROJECT,
        },
    )
    with urllib.request.urlopen(request) as response:
        payload = json.loads(response.read())
    events = [event for event in payload.get("items", []) if event.get("start", {}).get("dateTime")]
    return list(reversed(events))


def _platform(event: dict[str, Any]) -> str:
    haystack = json.dumps(event).lower()
    if "teams.microsoft.com" in haystack:
        return "teams"
    if event.get("hangoutLink") or "meet.google.com" in haystack:
        return "gmeet"
    return ""


def _source_uri(event: dict[str, Any]) -> str:
    return str(event.get("hangoutLink") or event.get("htmlLink") or "")


def note_body(event: dict[str, Any] | None, title: str, started_at: str) -> str:
    attendees = [
        attendee["email"]
        for attendee in (event or {}).get("attendees", [])
        if attendee.get("email") and not attendee.get("resource")
    ]
    front = {
        "title": title,
        "platform": _platform(event) if event else "",
        "started_at": started_at,
        "attendees": attendees,
        "source_uri": _source_uri(event) if event else "",
    }
    lines = ["---"]
    for key, value in front.items():
        if not value:
            continue
        lines.append(f"{key}: [{', '.join(value)}]" if isinstance(value, list) else f"{key}: {value}")
    lines += ["---", "", TEMPLATE]
    return "\n".join(lines)


def write_note(out: Path, event: dict[str, Any] | None, title: str | None) -> Path:
    started = (event or {}).get("start", {}).get("dateTime") or datetime.now().astimezone().isoformat()
    heading = title or (event or {}).get("summary") or "Untitled meeting"
    slug = _SLUG.sub("-", heading.lower()).strip("-")[:60] or "meeting"
    note = out / f"{started[:10]}--{slug}.md"
    if not note.exists():
        out.mkdir(parents=True, exist_ok=True)
        note.write_text(note_body(event, heading, started), encoding="utf-8")
    return note


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--hours", type=int, default=4)
    parser.add_argument("--event", type=int, help="index from --list")
    parser.add_argument("--title", help="use this title instead of the calendar event")
    parser.add_argument("--list", action="store_true", help="list recent events and exit")
    parser.add_argument("--no-open", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    events = [] if arguments.title and arguments.event is None else recent_events(hours=arguments.hours)
    if arguments.list:
        for index, event in enumerate(events, start=1):
            start = event["start"]["dateTime"][11:16]
            print(f"{index:>2}  {start}  {event.get('summary', '(no title)')}")
        return 0
    index = (arguments.event or 1) - 1
    event = events[index] if 0 <= index < len(events) else None
    if event is None and not arguments.title:
        print(f"no calendar event started in the last {arguments.hours}h; pass --title", file=sys.stderr)
        return 1
    note = write_note(arguments.out, event, arguments.title)
    print(note)
    if not arguments.no_open:
        subprocess.run(["open", str(note)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
