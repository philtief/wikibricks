"""Tests for the Omnigent → WikiBricks session-sync transform.

The sync pulls Omnigent's own conversation store (`~/.omnigent/chat.db`) and
turns each real conversation into a WikiBricks page — the harness-agnostic
counterpart to the Claude Code recorder plugin. Omnigent is NOT modified; we
only read its local store and write via the existing `WikiClient`.

These cover the PURE transform (`build_state`, `conversation_page`,
`is_syncable`, `_decode_item`). No SQLite, no WorkspaceClient — the CLI wrapper
in `scripts/omnigent_sync_cli.py` owns all IO.
"""

from __future__ import annotations

from wikibricks_recorder.omnigent_sync import (
    OMNIGENT_PATH_PREFIX,
    build_state,
    conversation_page,
    is_syncable,
)


# --- fixtures: minimal Omnigent conversation_items rows -----------------------
# Each row mirrors the chat.db shape: (type:int, data:dict). Types verified
# empirically against a live store: 1=message, 2=tool-call, 3=tool-output,
# 5=error, 8/10/11=lifecycle/command/input events.
def _msg(role: str, text: str) -> dict:
    return {"role": role, "content": [{"type": "output_text", "text": text}]}


def _real_conv():
    return {
        "conversation_id": "b65e35694ff7478bb843fc12e1c93750",
        "title": "Are there any size limitations for binary data columns in Delta?",
        "created_at": 1785243000,
        "updated_at": 1785243864,
        "agent_name": "claude-native-ui",
        "workspace": "/Users/philipp.tiefenbacher/code/wikibricks",
        "items": [
            (1, _msg("user", "Are there any size limitations for binary columns in Delta?")),
            (2, {"agent": "claude-native-ui", "name": "web_search",
                 "arguments": '{"query": "Databricks BINARY column max size"}',
                 "call_id": "ws_1"}),
            (3, {"call_id": "ws_1", "output": "BINARY has no declared length; ~2GB practical cap."}),
            (1, _msg("assistant", "For Delta, BINARY has no schema length limit; ~2 GB per value.")),
        ],
    }


class TestIsSyncable:
    def test_real_conversation_is_syncable(self):
        assert is_syncable(_real_conv()) is True

    def test_subagent_general_purpose_skipped(self):
        c = _real_conv()
        c["title"] = "general-purpose:aa69b92111deede66"
        assert is_syncable(c) is False

    def test_native_ui_child_title_skipped(self):
        c = _real_conv()
        c["title"] = "codex-native-ui"
        assert is_syncable(c) is False

    def test_archived_conversation_skipped(self):
        c = _real_conv()
        c["archived"] = True
        assert is_syncable(c) is False

    def test_conversation_with_no_user_message_skipped(self):
        c = _real_conv()
        c["items"] = [(1, _msg("assistant", "hello")), (8, {"event_type": "x"})]
        assert is_syncable(c) is False

    def test_single_prompt_boilerplate_skipped(self):
        # A one-shot summarizer/utility invocation (no real work).
        c = _real_conv()
        c["title"] = "You are summarizing a Claude Code session"
        c["items"] = [(1, _msg("user", "You are summarizing a Claude Code session"))]
        assert is_syncable(c) is False


class TestBuildState:
    def test_maps_conversation_to_recorder_state_shape(self):
        st = build_state(_real_conv())
        # Shape must match what page_builder consumes.
        assert st["session_id"] == "b65e35694ff7478bb843fc12e1c93750"
        assert st["cwd"] == "/Users/philipp.tiefenbacher/code/wikibricks"
        assert st["model"] == "claude-native-ui"
        assert st["first_prompt"].startswith("Are there any size limitations")
        assert isinstance(st["started_at"], str) and "2026" in st["started_at"]

    def test_events_capture_prompts_tools_and_responses(self):
        st = build_state(_real_conv())
        kinds = [e["kind"] for e in st["events"]]
        assert kinds.count("prompt") >= 1
        assert "tool" in kinds
        assert "response" in kinds
        tool = next(e for e in st["events"] if e["kind"] == "tool")
        assert tool["tool_name"] == "web_search"

    def test_first_prompt_is_first_user_message_not_assistant(self):
        c = _real_conv()
        c["items"] = [
            (1, _msg("assistant", "preamble from a resumed session")),
            (1, _msg("user", "the real question")),
        ]
        assert build_state(c)["first_prompt"] == "the real question"

    def test_unknown_item_types_are_ignored_not_crashed(self):
        c = _real_conv()
        c["items"].append((99, {"weird": "shape"}))
        c["items"].append((5, {"code": "RuntimeError", "message": "boom"}))
        st = build_state(c)  # must not raise
        assert st["events"]


class TestConversationPage:
    def test_page_path_uses_omnigent_prefix_and_user(self):
        page = conversation_page(_real_conv(), user_id="philipp.tiefenbacher-at-databricks.com")
        assert page["path"].startswith(OMNIGENT_PATH_PREFIX)
        assert "philipp.tiefenbacher-at-databricks.com" in page["path"]
        # conversation id is the leaf so re-sync overwrites, not duplicates
        assert page["path"].endswith("b65e35694ff7478bb843fc12e1c93750")

    def test_page_title_is_real_first_prompt_not_boilerplate(self):
        page = conversation_page(_real_conv(), user_id="u")
        assert page["title"].startswith("Are there any size limitations")

    def test_page_tags_mark_omnigent_harness_and_agent(self):
        page = conversation_page(_real_conv(), user_id="u")
        assert "session" in page["tags"]
        assert "harness:omnigent" in page["tags"]
        assert "user:u" in page["tags"]
        assert any(t.startswith("agent:") for t in page["tags"])

    def test_page_content_has_summary_and_body(self):
        page = conversation_page(_real_conv(), user_id="u")
        assert set(page["content"]) >= {"summary", "body"}
        assert "# Session" in page["content"]["body"]
        assert page["content"]["summary"]

    def test_page_content_text_override_is_first_prompt_dense(self):
        # content_text feeds the VS index; keep it keyword-rich (the prompt),
        # not the full transcript dump.
        page = conversation_page(_real_conv(), user_id="u")
        assert "content_text_override" in page
        assert "binary" in page["content_text_override"].lower()


class TestLoadConversationsFromSqlite:
    """Covers the CLI's chat.db row-assembly against an in-memory mirror."""

    def _db(self):
        import sqlite3
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.execute(
            "CREATE TABLE conversations (id BLOB, created_at INT, updated_at INT, "
            "title TEXT, archived INT, agent_id BLOB, workspace_id INT)"
        )
        con.execute("CREATE TABLE agents (id BLOB, name TEXT, workspace_id INT)")
        con.execute(
            "CREATE TABLE conversation_items (conversation_id BLOB, workspace_id INT, "
            "position INT, type INT, data TEXT)"
        )
        cid = bytes.fromhex("aa" * 16)
        aid = bytes.fromhex("bb" * 16)
        con.execute("INSERT INTO agents VALUES (?,?,?)", (aid, "claude-native-ui", 0))
        con.execute(
            "INSERT INTO conversations VALUES (?,?,?,?,?,?,?)",
            (cid, 1785243000, 1785243864, "real question about Delta", 0, aid, 0),
        )
        # archived one must be excluded
        con.execute(
            "INSERT INTO conversations VALUES (?,?,?,?,?,?,?)",
            (bytes.fromhex("cc" * 16), 1, 2, "old", 1, aid, 0),
        )
        for pos, (t, d) in enumerate([
            (1, {"role": "user", "content": [{"type": "input_text", "text": "hi Delta?"}]}),
            (2, {"name": "web_search", "arguments": "{}"}),
            (1, {"role": "assistant", "content": [{"type": "output_text", "text": "answer"}]}),
            (3, "NOT JSON — must be skipped, not crash"),
        ]):
            con.execute(
                "INSERT INTO conversation_items VALUES (?,?,?,?,?)",
                (cid, 0, pos, t, d if isinstance(d, str) else __import__("json").dumps(d)),
            )
        con.commit()
        return con

    def test_assembles_metadata_items_and_agent_join(self):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(
            "omnigent_sync_cli",
            Path(__file__).parent.parent / "scripts" / "omnigent_sync_cli.py",
        )
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)

        convs = cli.load_conversations(self._db())
        assert len(convs) == 1  # archived excluded
        c = convs[0]
        assert c["conversation_id"] == "aa" * 16
        assert c["agent_name"] == "claude-native-ui"
        # 4 rows in, 3 parse (1 bad JSON skipped)
        assert len(c["items"]) == 3
        assert is_syncable(c) is True
