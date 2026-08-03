"""Tests for the noise-page classifier used by scripts/purge_noise.py.

The classifier decides which persisted session pages are recorder noise
(programmatic /tmp sub-agent + consolidation runs) versus real work. The
reliable signal is an *ephemeral CWD* in the page body's session-metadata
block — the same signal the recorder's ``page_builder.is_ephemeral`` uses
at write time. Title text is a weak signal (real sessions can fall back to
a ``[stub]`` title) so it must never, on its own, condemn a page whose body
is a genuine summary.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from wikibricks.title_repair import (
    body_has_ephemeral_cwd,
    is_noise_page,
)

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import purge_noise  # noqa: E402


def _body(cwd: str, events: int = 1) -> str:
    return (
        "# Session abc123\n\n"
        f"- Started: 2026-05-15T13:09:26Z\n"
        f"- CWD: {cwd}\n"
        f"- Model: ?\n"
        f"- Events: {events}\n\n"
        "## Timeline\n"
        "### prompt @ 2026-05-15T13:09:26Z\n"
        "> You are a memory consolidation agent.\n"
    )


@pytest.mark.parametrize("cwd", [
    "/tmp",
    "/tmp/smoke",
    "/private/tmp",
    "/private/tmp/xyz",
    "/var/tmp/thing",
    "/var/folders/1q/abc",
    "/private/var/folders/1q/g5whdn6s6j94w914ljghgj_00000gp/T",
])
def test_body_has_ephemeral_cwd_true(cwd):
    assert body_has_ephemeral_cwd(_body(cwd)) is True


@pytest.mark.parametrize("cwd", [
    "/Users/philipp.tiefenbacher/code/wikibricks",
    "/Users/philipp.tiefenbacher/emails",
    "/home/me/project",
    "?",
])
def test_body_has_ephemeral_cwd_false_for_real_cwd(cwd):
    assert body_has_ephemeral_cwd(_body(cwd)) is False


def test_body_has_ephemeral_cwd_false_when_no_metadata_block():
    # Real summarized sessions store a dense summary + ToC in content_text
    # with NO "# Session / - CWD:" block. These must never be flagged.
    summary = (
        "A PERF review session where the user cross-referenced their Q1 2026 "
        "work against the promo rubric and drafted self-assessment bullets.\n\n"
        "## Contents\n- [chunk 01](sessions/.../chunks/01)\n"
    )
    assert body_has_ephemeral_cwd(summary) is False


def test_body_has_ephemeral_cwd_false_on_empty():
    assert body_has_ephemeral_cwd("") is False
    assert body_has_ephemeral_cwd(None) is False  # type: ignore[arg-type]


def test_body_has_ephemeral_cwd_ignores_ephemeral_path_in_prose():
    # A /tmp path mentioned in prose (not on the CWD metadata line) is not a
    # CWD signal — only the "- CWD: <path>" line counts.
    body = (
        "Session debugging a script that writes to /tmp/output.json and then "
        "uploads it.\n\n## Contents\n- [chunk 01](.../chunks/01)\n"
    )
    assert body_has_ephemeral_cwd(body) is False


def test_is_noise_page_true_for_ephemeral_body():
    # [stub] title + ephemeral cwd → noise (the 05/15 /tmp stubs).
    assert is_noise_page("[stub] Session 3dd6426e", _body("/private/tmp")) is True


def test_is_noise_page_true_for_system_prompt_title():
    # Legacy title-based path still works even without a body CWD line.
    assert is_noise_page("You are a memory consolidation agent.", "") is True
    assert is_noise_page("Apply maximum compression. Rules:", "") is True


def test_is_noise_page_false_for_real_summarized_stub():
    # CRITICAL false-positive guard: the 9 real 05/16 sessions carry a
    # "[stub]" title but a genuine summary body with no ephemeral CWD.
    real_body = (
        "Session focused on preparing communications for Allianz AGCS Genie "
        "MVP status, researching internal roadmap.\n\n## Contents\n"
        "- [chunk 01](sessions/.../chunks/01)\n"
    )
    assert is_noise_page("[stub] Session 0a4d237b", real_body) is False


def test_is_noise_page_false_for_real_work_title_and_body():
    body = _body("/Users/philipp.tiefenbacher/code/wikibricks", events=42)
    assert is_noise_page("Fix the Lakebase connection pool bug", body) is False


class TestFindCandidates:
    def _wiki_with_rows(self, rows):
        wiki = MagicMock()
        resp = MagicMock()
        resp.result.data_array = rows
        wiki._exec.return_value = resp
        return wiki

    def test_scan_covers_sessions_and_promoted(self):
        wiki = self._wiki_with_rows([])
        purge_noise.find_candidates(wiki)
        sql = wiki._exec.call_args.args[0]
        assert "sessions/%" in sql
        assert "promoted/%" in sql
        # never scans chunk children as candidates
        assert "NOT LIKE '%/chunks/%'" in sql

    def test_flags_promoted_noise_and_keeps_real(self):
        eph_body = "# Session x\n- CWD: /private/tmp\n- Events: 1\n"
        rows = [
            ["promoted/you-are-summarizing-a-claude-code-session",
             "You are summarizing a Claude Code session for a daily memory log.", eph_body],
            ["sessions/u/2026/05/16/real",
             "A Claude Code session drafting AGCS Genie comms",
             "A Claude Code session focused on drafting communications for AGCS Genie."],
        ]
        wiki = self._wiki_with_rows(rows)
        out = purge_noise.find_candidates(wiki)
        paths = {c["path"] for c in out}
        assert "promoted/you-are-summarizing-a-claude-code-session" in paths
        assert "sessions/u/2026/05/16/real" not in paths

    def test_limit_caps_results(self):
        rows = [
            [f"promoted/noise-{i}", "You are a memory consolidation agent.", ""]
            for i in range(5)
        ]
        wiki = self._wiki_with_rows(rows)
        assert len(purge_noise.find_candidates(wiki, limit=2)) == 2
