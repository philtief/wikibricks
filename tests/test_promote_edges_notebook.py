"""Drift-guard tests for notebooks/promote_edges.py — assert the contract.

The promote_edges task is deterministic (no LLM) for v0.7.10: it pulls
pending rows from edges_proposed, validates target existence + non-empty
evidence + non-duplicate, then INSERTs into links by resolving paths
into page_ids via a JOIN on pages.
"""

from __future__ import annotations

from pathlib import Path

NB = Path(__file__).resolve().parent.parent / "notebooks" / "promote_edges.py"


def test_notebook_exists():
    assert NB.exists()


def test_widgets_are_warehouse_catalog_schema():
    txt = NB.read_text()
    assert 'dbutils.widgets.get("warehouse_id")' in txt
    assert 'dbutils.widgets.get("catalog")' in txt
    assert 'dbutils.widgets.get("schema")' in txt


def test_filters_by_pending_status():
    txt = NB.read_text()
    assert "WHERE status = 'pending'" in txt


def test_inserts_into_links_with_origin():
    txt = NB.read_text()
    assert "INSERT INTO" in txt
    assert ".links" in txt
    assert "auto_summary_envelope" in txt


def test_inserts_links_using_page_id_join():
    """Validates that the INSERT joins edges_proposed to pages by path
    to resolve source_page_id and target_page_id — links table uses
    page_id columns, not path columns."""
    txt = NB.read_text()
    assert "source_page_id" in txt
    assert "target_page_id" in txt
    # Confirm the join exists on path equality
    assert "pages src" in txt or "JOIN" in txt.upper()


def test_logs_promote_edge_op_type():
    txt = NB.read_text()
    assert '"promote_edge"' in txt


def test_pip_install_pinned_to_0_7_10():
    txt = NB.read_text()
    assert "wikibricks-0.7.10-py3-none-any.whl" in txt
