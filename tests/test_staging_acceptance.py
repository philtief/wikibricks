from scripts.staging_acceptance import build_corpus, retrieval_metrics, seed_corpus
from wikibricks.storage.sqlite_store import SQLiteStore


def test_staging_acceptance_corpus_and_metrics_are_deterministic(tmp_path):
    corpus = build_corpus(
        "contract",
        page_count=100,
        pair_count=10,
        long_event_chars=24_000,
    )

    assert len(corpus.pages) == 100
    assert len(corpus.expected_partners) == 20
    assert len(corpus.session.events) == 2
    assert all(len(event.content) >= 24_000 for event in corpus.session.events)

    local = SQLiteStore(tmp_path / "acceptance.db")
    local.migrate()
    seed_corpus(local, corpus)
    outbox_kinds = [row["entity_kind"] for row in local.pending_outbox()]
    assert outbox_kinds[:20] == ["page_version"] * 20
    assert outbox_kinds[20:22] == ["session_event_version"] * 2
    assert outbox_kinds[22:] == ["page_version"] * 80

    evidence_to_path = {
        f"archive-event:{index}": path
        for index, path in enumerate(corpus.expected_partners)
    }
    similarity = []
    for index, source in enumerate(corpus.expected_partners):
        target = corpus.expected_partners[source]
        similarity.append(
            {
                "query_evidence_id": f"archive-event:{index}",
                "candidates": [
                    {
                        "path": target,
                        "vector_rank": 1,
                        "keyword_rank": 2,
                    }
                ],
            }
        )

    assert retrieval_metrics(
        similarity,
        evidence_to_path=evidence_to_path,
        expected_partners=corpus.expected_partners,
        maximum_rank=10,
    ) == {
        "evaluated_queries": 20,
        "hybrid_recall_at_10": 1.0,
        "vector_recall_at_10": 1.0,
        "keyword_recall_at_10": 1.0,
    }
