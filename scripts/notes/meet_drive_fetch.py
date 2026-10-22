#!/usr/bin/env python3
"""Copy Google Meet notes and transcripts from Drive into the WikiBricks note drop folder.

Meet saves its artifacts as Google Docs in the organizer's Drive: a "Notes by Gemini"
document when note taking ran, and a "Transcript" document when transcription ran. This
script exports the ones it has not seen as plain text and writes the note plus sidecar
pair that `wikibricks import notes` reads. It never touches the WikiBricks database.

Authentication uses the Application Default Credentials that `gcloud` already holds. The
credential needs a Drive scope, and every call needs the Databricks quota project header.
Set both up with the `google-auth` skill's login command rather than by hand.

Usage:
    python3 scripts/notes/meet_drive_fetch.py --dry-run
    python3 scripts/notes/meet_drive_fetch.py --days 14
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DRIVE_FILES = "https://www.googleapis.com/drive/v3/files"
DOCUMENT_MIME = "application/vnd.google-apps.document"
DEFAULT_OUT = Path.home() / ".wikibricks" / "inbox" / "meetings"
# Application Default Credentials need a quota project; the field engineering one is shared.
DEFAULT_QUOTA_PROJECT = os.environ.get("GOOGLE_CLOUD_QUOTA_PROJECT", "gcp-dev-field-eng-aiapiquota")
# Meet names its artifacts "<meeting> - Notes by Gemini" and "<meeting> - Transcript".
ARTIFACTS = ("Notes by Gemini", "Transcript")
_SLUG = re.compile(r"[^a-z0-9]+")
# Drive puts the Docs UI header of the notes tab at the top of the export.
BOILERPLATE = ("\u270d\ufe0f Quick notes", "Please rate the new Quick notes tab by taking a short survey.")
_TRAILING_DATE = re.compile(r"\s*-\s*\d{4}[/-]\d{2}[/-]\d{2}.*$")


def resolve_token() -> str:
    """Take a bearer token from the credentials gcloud already holds."""
    result = subprocess.run(
        ["gcloud", "auth", "application-default", "print-access-token"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"no Google access token: {result.stderr.strip()}")
    return result.stdout.strip()


def _get(url: str, token: str, quota_project: str, *, raw: bool = False) -> Any:
    request = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {token}", "x-goog-user-project": quota_project},
    )
    try:
        with urllib.request.urlopen(request) as response:
            payload = response.read()
    except urllib.error.HTTPError as error:
        # Google explains scope and quota-project problems in the body, so keep it.
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise urllib.error.HTTPError(
            error.url, error.code, f"{error.reason}: {detail}", error.headers, None
        ) from None
    return payload.decode("utf-8", errors="replace") if raw else json.loads(payload)


def list_documents(token: str, quota_project: str, *, days: int) -> list[dict[str, Any]]:
    """List recent Meet artifact documents, whichever folder Drive keeps them in."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    names = " or ".join(f"name contains '{artifact}'" for artifact in ARTIFACTS)
    query = f"mimeType = '{DOCUMENT_MIME}' and trashed = false and modifiedTime > '{cutoff}' and ({names})"
    documents: list[dict[str, Any]] = []
    page_token = None
    while True:
        parameters = {
            "q": query,
            "fields": "nextPageToken, files(id, name, createdTime, webViewLink)",
            "pageSize": "100",
            "orderBy": "createdTime desc",
        }
        if page_token:
            parameters["pageToken"] = page_token
        payload = _get(f"{DRIVE_FILES}?{urllib.parse.urlencode(parameters)}", token, quota_project)
        documents.extend(payload.get("files", []))
        page_token = payload.get("nextPageToken")
        if not page_token:
            return documents


def export_text(token: str, quota_project: str, file_id: str) -> str:
    parameters = urllib.parse.urlencode({"mimeType": "text/plain"})
    exported = _get(f"{DRIVE_FILES}/{file_id}/export?{parameters}", token, quota_project, raw=True)
    lines = exported.lstrip("\ufeff").splitlines(keepends=True)
    while lines and lines[0].strip() in BOILERPLATE:
        lines.pop(0)
    return "".join(lines)


def note_name(document: dict[str, Any]) -> str:
    """Build a stable `YYYY-MM-DD--slug` name from the document."""
    date = str(document.get("createdTime", ""))[:10] or "0000-00-00"
    title = _TRAILING_DATE.sub("", document["name"])
    slug = _SLUG.sub("-", title.lower()).strip("-") or "meeting"
    return f"{date}--{slug[:60]}"


def fetch(out: Path, *, days: int, dry_run: bool, quota_project: str) -> dict[str, int]:
    token = resolve_token()
    try:
        documents = list_documents(token, quota_project, days=days)
    except urllib.error.HTTPError as error:
        if error.code != 401:
            raise
        token = resolve_token()
        documents = list_documents(token, quota_project, days=days)
    report = {"found": len(documents), "written": 0, "skipped": 0}
    for document in documents:
        name = note_name(document)
        note = out / f"{name}.md"
        if note.exists():
            report["skipped"] += 1
            continue
        if dry_run:
            print(f"would write {note.name}  <-  {document['name']}")
            report["written"] += 1
            continue
        out.mkdir(parents=True, exist_ok=True)
        note.write_text(export_text(token, quota_project, document["id"]), encoding="utf-8")
        note.with_suffix(".json").write_text(
            json.dumps(
                {
                    "title": _TRAILING_DATE.sub("", document["name"]),
                    "platform": "gmeet",
                    "started_at": document.get("createdTime"),
                    "source_uri": document.get("webViewLink"),
                    "external_id": f"gmeet-{document['id']}",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        report["written"] += 1
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quota-project", default=DEFAULT_QUOTA_PROJECT)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    report = fetch(
        arguments.out,
        days=arguments.days,
        dry_run=arguments.dry_run,
        quota_project=arguments.quota_project,
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
