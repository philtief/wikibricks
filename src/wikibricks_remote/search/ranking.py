"""Deterministic page-level Reciprocal Rank Fusion."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CandidateRank:
    path: str
    vector_rank: int | None
    keyword_rank: int | None
    rrf_score: float


@dataclass(frozen=True, slots=True)
class CandidateSelection:
    search_status: str
    pages: tuple[dict[str, Any], ...]
    similarity_candidates: tuple[dict[str, Any], ...]
    query_count: int
    vector_matches: int
    keyword_matches: int


def reciprocal_rank_fusion(
    vector_paths: Sequence[str],
    keyword_paths: Sequence[str],
    *,
    maximum: int,
    constant: int = 60,
) -> tuple[CandidateRank, ...]:
    if maximum < 1 or constant < 1:
        raise ValueError("RRF bounds must be positive")
    vector_ranks: dict[str, int] = {}
    keyword_ranks: dict[str, int] = {}
    for rank, path in enumerate(vector_paths, start=1):
        vector_ranks.setdefault(path, rank)
    for rank, path in enumerate(keyword_paths, start=1):
        keyword_ranks.setdefault(path, rank)
    candidates = []
    for path in vector_ranks.keys() | keyword_ranks.keys():
        vector_rank = vector_ranks.get(path)
        keyword_rank = keyword_ranks.get(path)
        score = sum(
            1.0 / (constant + rank)
            for rank in (vector_rank, keyword_rank)
            if rank is not None
        )
        candidates.append(
            CandidateRank(
                path=path,
                vector_rank=vector_rank,
                keyword_rank=keyword_rank,
                rrf_score=score,
            )
        )
    candidates.sort(key=lambda item: (-item.rrf_score, item.path))
    return tuple(candidates[:maximum])


__all__ = ["CandidateRank", "CandidateSelection", "reciprocal_rank_fusion"]
