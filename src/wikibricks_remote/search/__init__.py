"""Remote-only search projection and Lakebase retrieval."""

from wikibricks_remote.search.documents import SearchDocument, project_event
from wikibricks_remote.search.embeddings import (
    Embedder,
    EmbeddingUpdate,
    build_embedding_updates,
)
from wikibricks_remote.search.lakebase import LakebaseHybridSearch
from wikibricks_remote.search.ranking import (
    CandidateRank,
    CandidateSelection,
    reciprocal_rank_fusion,
)

__all__ = [
    "Embedder",
    "EmbeddingUpdate",
    "LakebaseHybridSearch",
    "SearchDocument",
    "build_embedding_updates",
    "project_event",
    "CandidateRank",
    "CandidateSelection",
    "reciprocal_rank_fusion",
]
