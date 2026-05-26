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


def test_escapes_link_type_in_sql():
    """link_type must be SQL-escaped before interpolation — review
    finding #1 from v0.7.10."""
    txt = NB.read_text()
    # The helper _esc must wrap link_type interpolation in dup_sql
    assert "_esc(link_type)" in txt


def test_validates_source_path_exists():
    """Source page existence check guards against race condition where
    _flush stages edges before the page MERGE becomes visible — review
    finding #2 from v0.7.10."""
    txt = NB.read_text()
    assert "source_missing" in txt
    assert "WHERE path = '" in txt  # source-check SQL


def test_handles_orphan_confirmed_rows():
    """Compensating UPDATE catches any confirmed row whose JOIN at INSERT
    time didn't yield a links row — defense in depth for finding #3."""
    txt = NB.read_text()
    assert "source_join_orphan" in txt
    assert "LEFT JOIN" in txt


def test_uses_counter_for_rejected_reasons():
    """rejected_reasons counts whatever reasons appear, not a hardcoded
    set — review minor #1."""
    txt = NB.read_text()
    assert "from collections import Counter" in txt
    assert "Counter(reason for _, reason in rejected)" in txt
