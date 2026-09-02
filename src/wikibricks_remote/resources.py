"""Load the readable policy, prompt, and schema used by remote maintenance."""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True, slots=True)
class RemotePolicy:
    max_replicas_per_run: int
    max_events_per_replica: int
    max_input_chars: int
    max_current_pages: int
    max_proposals_per_replica: int
    allowed_operations: tuple[str, ...]
    allowed_link_types: tuple[str, ...]
    max_search_chunk_chars: int
    max_index_pages: int
    embedding_dimension: int
    max_embedding_documents: int
    embedding_batch_size: int
    max_query_documents: int
    pages_per_query: int
    temperature: float
    max_output_tokens: int


def _resource_text(name: str) -> str:
    return (
        files("wikibricks_remote")
        .joinpath("resources", name)
        .read_text(encoding="utf-8")
    )


def load_policy(path: str | Path | None = None) -> RemotePolicy:
    text = Path(path).read_text(encoding="utf-8") if path else _resource_text("remote-policy.yml")
    value = yaml.safe_load(text)
    if not isinstance(value, dict) or value.get("version") != 1:
        raise ValueError("remote policy must be a version 1 object")
    selection = value.get("selection")
    publishing = value.get("publishing")
    search = value.get("search")
    model = value.get("model")
    if not all(
        isinstance(item, dict)
        for item in (selection, publishing, search, model)
    ):
        raise ValueError(
            "remote policy requires selection, publishing, search, and model sections"
        )
    policy = RemotePolicy(
        max_replicas_per_run=int(selection["max_replicas_per_run"]),
        max_events_per_replica=int(selection["max_events_per_replica"]),
        max_input_chars=int(selection["max_input_chars"]),
        max_current_pages=int(selection["max_current_pages"]),
        max_proposals_per_replica=int(publishing["max_proposals_per_replica"]),
        allowed_operations=tuple(publishing["allowed_operations"]),
        allowed_link_types=tuple(publishing["allowed_link_types"]),
        max_search_chunk_chars=int(search["max_chunk_chars"]),
        max_index_pages=int(search["max_index_pages"]),
        embedding_dimension=int(search["embedding_dimension"]),
        max_embedding_documents=int(search["max_embedding_documents"]),
        embedding_batch_size=int(search["embedding_batch_size"]),
        max_query_documents=int(search["max_query_documents"]),
        pages_per_query=int(search["pages_per_query"]),
        temperature=float(model["temperature"]),
        max_output_tokens=int(model["max_output_tokens"]),
    )
    numeric_limits = (
        policy.max_replicas_per_run,
        policy.max_events_per_replica,
        policy.max_input_chars,
        policy.max_current_pages,
        policy.max_proposals_per_replica,
        policy.max_search_chunk_chars,
        policy.max_index_pages,
        policy.embedding_dimension,
        policy.max_embedding_documents,
        policy.embedding_batch_size,
        policy.max_query_documents,
        policy.pages_per_query,
        policy.max_output_tokens,
    )
    if any(value < 1 for value in numeric_limits):
        raise ValueError("remote policy limits must be positive")
    if not policy.allowed_operations:
        raise ValueError("remote policy must allow at least one operation")
    if not policy.allowed_link_types:
        raise ValueError("remote policy must allow at least one link type")
    return policy


def load_prompt() -> str:
    return _resource_text("curation.md")


def load_schema() -> dict[str, Any]:
    value = json.loads(_resource_text("curation-proposals.schema.json"))
    if not isinstance(value, dict):
        raise ValueError("curation proposal schema must be an object")
    return value


__all__ = ["RemotePolicy", "load_policy", "load_prompt", "load_schema"]
