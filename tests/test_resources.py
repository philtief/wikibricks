from __future__ import annotations

import json
from importlib.resources import files
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

from wikibricks.curation_sync import build_manifest, create_patch
from wikibricks.models import SessionEvent, SessionRecord
from wikibricks.resources import get_server_instructions, get_tool_schemas


def _schema(name: str) -> dict:
    path = files("wikibricks.resources").joinpath("schemas", name)
    return json.loads(path.read_text(encoding="utf-8"))


def _validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(_schema(name), format_checker=FormatChecker())


def _session() -> dict:
    record = SessionRecord(
        harness="codex",
        external_id="session-1",
        user_id="u",
        agent="codex-native-ui",
        events=[SessionEvent("event-1", "user", "Remember PostgreSQL")],
    )
    return {"schema_version": 1, "session": record.to_dict()}


def _manifest() -> dict:
    proposal = {
        "title": "Local memory",
        "page_type": "concept",
        "content": {"summary": "Local memory", "body": "PostgreSQL"},
        "content_text": "Local memory PostgreSQL",
        "tags": ["local"],
        "source_ids": ["session:1"],
        "parent_id": None,
        "chunk_index": None,
    }
    patch = create_patch(
        operation="create_page",
        path="topics/local-memory",
        proposal=proposal,
        evidence_ids=["session:1"],
        reason="Preserve the local memory decision.",
    )
    return build_manifest(
        replica_id=UUID("00000000-0000-0000-0000-000000000001"),
        input_watermark=1,
        patches=[patch],
    )


def test_mcp_resources_drive_the_server_contract():
    instructions = get_server_instructions()
    schemas = get_tool_schemas()

    assert "maintained knowledge layer" in instructions
    assert [item["name"] for item in schemas] == [
        "wiki_search",
        "wiki_read_full",
        "wiki_index",
        "wiki_write_page",
        "wiki_promote_answer",
    ]


def test_interchange_schemas_accept_current_payloads():
    _validator("session-record.schema.json").validate(_session())
    _validator("curation-manifest-v1.schema.json").validate(_manifest())


def test_interchange_schemas_reject_invalid_contract_values():
    session = _session()
    session["session"]["events"][0]["kind"] = "prompt"
    manifest = _manifest()
    manifest["patches"][0]["risk_class"] = "unsafe"

    with pytest.raises(ValidationError):
        _validator("session-record.schema.json").validate(session)
    with pytest.raises(ValidationError):
        _validator("curation-manifest-v1.schema.json").validate(manifest)
