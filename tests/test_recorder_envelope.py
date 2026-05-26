"""Tests for the envelope module — pure helpers (no LLM calls)."""
from __future__ import annotations

import json

from wikibricks_recorder import envelope


def test_parse_envelope_happy_path():
    raw = json.dumps({
        "summary_markdown": "## Intent\n- refactor",
        "entities": [{"name": "Stripe", "type": "library"}],
        "tags": ["customer:az", "topic:payments"],
        "edges": [
            {"target_path": "topics/stripe", "link_type": "cites",
             "evidence": "uses Stripe.construct_event"}
        ],
    })
    e = envelope.parse_envelope(raw)
    assert e is not None
    assert e["summary_markdown"].startswith("## Intent")
    assert len(e["entities"]) == 1
    assert e["entities"][0]["name"] == "Stripe"
    assert "customer:az" in e["tags"]
    assert len(e["edges"]) == 1
    assert e["edges"][0]["target_path"] == "topics/stripe"


def test_parse_envelope_strips_code_fences():
    raw = "```json\n" + json.dumps({
        "summary_markdown": "S", "entities": [], "tags": [], "edges": []
    }) + "\n```"
    e = envelope.parse_envelope(raw)
    assert e is not None
    assert e["summary_markdown"] == "S"


def test_parse_envelope_returns_none_on_garbage():
    assert envelope.parse_envelope("not json") is None
    assert envelope.parse_envelope("") is None
    assert envelope.parse_envelope(None) is None


def test_parse_envelope_handles_missing_optional_keys():
    raw = json.dumps({"summary_markdown": "S"})
    e = envelope.parse_envelope(raw)
    assert e is not None
    assert e["summary_markdown"] == "S"
    assert e["entities"] == []
    assert e["tags"] == []
    assert e["edges"] == []


def test_parse_envelope_coerces_non_list_optional_fields_to_empty():
    """If the LLM returns a string instead of a list for entities/tags/edges,
    parse_envelope must coerce to [] rather than passing the malformed value
    through. Otherwise downstream build_override_text indexes into a string."""
    raw = json.dumps({
        "summary_markdown": "S",
        "entities": "Stripe",         # WRONG type — should be a list
        "tags": {"customer": "az"},   # also wrong — should be a list
        "edges": None,
    })
    e = envelope.parse_envelope(raw)
    assert e is not None
    assert e["entities"] == []
    assert e["tags"] == []
    assert e["edges"] == []


def test_filter_edges_to_candidates_drops_unknown_targets():
    edges = [
        {"target_path": "topics/known", "link_type": "cites", "evidence": "ok"},
        {"target_path": "topics/fabricated", "link_type": "cites", "evidence": "ok"},
    ]
    candidates = ["topics/known", "topics/also-known"]
    kept = envelope.filter_edges_to_candidates(edges, candidates)
    assert len(kept) == 1
    assert kept[0]["target_path"] == "topics/known"


def test_filter_edges_drops_edges_with_empty_evidence():
    edges = [
        {"target_path": "topics/known", "link_type": "cites", "evidence": ""},
        {"target_path": "topics/known", "link_type": "cites", "evidence": "ok"},
    ]
    kept = envelope.filter_edges_to_candidates(edges, ["topics/known"])
    assert len(kept) == 1
    assert kept[0]["evidence"] == "ok"


def test_filter_edges_normalizes_unknown_link_types():
    edges = [
        {"target_path": "t", "link_type": "WRONG_TYPE", "evidence": "ok"},
        {"target_path": "t", "link_type": "cites", "evidence": "ok"},
    ]
    kept = envelope.filter_edges_to_candidates(edges, ["t"])
    # Unknown link_type normalized to 'related' (the safe default)
    assert kept[0]["link_type"] == "related"
    assert kept[1]["link_type"] == "cites"


def test_filter_edges_normalizes_case_and_whitespace():
    """LLM-emitted target_path with case-shift or trailing space must still
    match a candidate (anti-hallucination contract)."""
    edges = [
        {"target_path": "Topics/Foo", "link_type": "cites", "evidence": "ok"},
        {"target_path": "topics/bar ", "link_type": "cites", "evidence": "ok"},
        {"target_path": "topics/UNKNOWN", "link_type": "cites", "evidence": "ok"},
    ]
    kept = envelope.filter_edges_to_candidates(
        edges, ["topics/foo", "topics/bar"]
    )
    assert len(kept) == 2
    # Canonical (candidate) form is stored, not the LLM variant
    assert kept[0]["target_path"] == "topics/foo"
    assert kept[1]["target_path"] == "topics/bar"


def test_build_override_text_includes_all_fields():
    e = {
        "summary_markdown": "## Intent\n- refactor",
        "entities": [{"name": "Stripe"}, {"name": "payments/webhook.py"}],
        "tags": ["customer:az", "topic:payments"],
        "edges": [],
    }
    text = envelope.build_override_text(title="My Session", env=e)
    assert "My Session" in text
    assert "## Intent" in text
    assert "Tags: customer:az topic:payments" in text
    assert "Entities: Stripe, payments/webhook.py" in text


def test_build_override_text_caps_entity_count():
    e = {
        "summary_markdown": "S",
        "entities": [{"name": f"e{i}"} for i in range(50)],
        "tags": [],
        "edges": [],
    }
    text = envelope.build_override_text(title="T", env=e)
    # Cap at 20 entities — the heuristic the spec calls out.
    # Substring checks ("e0" in text) are dangerous: "e0" matches "e10",
    # "e20" matches "e2", etc. Extract the Entities: line and count names
    # exactly so an off-by-one in the cap cannot pass.
    entity_line = next(line for line in text.split("\n") if line.startswith("Entities:"))
    names = [n.strip() for n in entity_line[len("Entities:"):].split(",")]
    assert len(names) == envelope.MAX_ENTITIES_IN_OVERRIDE
    # First 20 entities must be in order
    assert names == [f"e{i}" for i in range(envelope.MAX_ENTITIES_IN_OVERRIDE)]
    # Entities past the cap must not appear anywhere in the text
    assert "e20" not in text
    assert "e49" not in text


def test_build_prompt_includes_candidates_inline():
    candidates = [
        {"path": "topics/foo", "title": "Foo", "summary": "About foo"},
        {"path": "topics/bar", "title": "Bar", "summary": "About bar"},
    ]
    prompt = envelope.build_prompt(
        transcript="refactor payments",
        candidates=candidates,
    )
    assert "topics/foo" in prompt
    assert "topics/bar" in prompt
    assert "refactor payments" in prompt
    # The candidate constraint is explicit so the LLM doesn't invent targets
    assert "MUST come from" in prompt or "must come from" in prompt
