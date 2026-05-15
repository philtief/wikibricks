"""Behavioral tests for `wikibricks.tag_logic`."""

from wikibricks.tag_logic import (
    LLM_TAG_PREFIX,
    MAX_SLUG_LEN,
    build_tag_event,
    dedupe_against_vocabulary,
    normalize_slug,
    parse_tag_response,
    prefix_llm,
    should_approve,
)


class TestNormalizeSlug:
    def test_spaces_become_hyphens(self):
        assert normalize_slug("row level security") == "row-level-security"

    def test_lowercases(self):
        assert normalize_slug("Delta Lake") == "delta-lake"

    def test_strips_punctuation(self):
        assert normalize_slug("Foo & Bar! (v2)") == "foo-bar-v2"

    def test_strips_leading_trailing_hyphens(self):
        assert normalize_slug("--foo--bar--") == "foo-bar"

    def test_empty_returns_empty(self):
        assert normalize_slug("") == ""
        assert normalize_slug("   ") == ""

    def test_truncates_to_max_length(self):
        long = "a" * (MAX_SLUG_LEN + 50)
        assert len(normalize_slug(long)) == MAX_SLUG_LEN


class TestParseTagResponse:
    def test_well_formed_dict(self):
        raw = '{"tags": ["row-level-security", "delta-lake-acl"]}'
        assert parse_tag_response(raw) == ["row-level-security", "delta-lake-acl"]

    def test_unwraps_markdown_fence_with_lang(self):
        raw = '```json\n{"tags": ["foo"]}\n```'
        assert parse_tag_response(raw) == ["foo"]

    def test_unwraps_markdown_fence_no_lang(self):
        raw = '```\n{"tags": ["foo"]}\n```'
        assert parse_tag_response(raw) == ["foo"]

    def test_bare_list_accepted(self):
        raw = '["row-level-security", "delta-lake"]'
        assert parse_tag_response(raw) == ["row-level-security", "delta-lake"]

    def test_malformed_returns_empty(self):
        assert parse_tag_response("not json at all") == []
        assert parse_tag_response("{tags: missing-quotes}") == []

    def test_empty_input_returns_empty(self):
        assert parse_tag_response("") == []

    def test_non_string_items_dropped(self):
        raw = '{"tags": ["valid", 42, null, "another"]}'
        assert parse_tag_response(raw) == ["valid", "another"]

    def test_normalizes_inside_parse(self):
        raw = '{"tags": ["Row Level Security", "DELTA LAKE"]}'
        assert parse_tag_response(raw) == ["row-level-security", "delta-lake"]

    def test_order_preserved(self):
        raw = '{"tags": ["z", "a", "m"]}'
        assert parse_tag_response(raw) == ["z", "a", "m"]

    def test_scalar_root_returns_empty(self):
        assert parse_tag_response("42") == []
        assert parse_tag_response('"a-string"') == []


class TestDedupeAgainstVocabulary:
    def test_drops_matching_slugs(self):
        result = dedupe_against_vocabulary(
            ["foo", "bar", "baz"], existing=["bar"]
        )
        assert result == ["foo", "baz"]

    def test_case_insensitive(self):
        result = dedupe_against_vocabulary(["Foo"], existing=["foo"])
        assert result == []

    def test_preserves_proposed_order(self):
        result = dedupe_against_vocabulary(
            ["z", "a", "m", "x"], existing=["a"]
        )
        assert result == ["z", "m", "x"]

    def test_empty_existing_passes_everything(self):
        result = dedupe_against_vocabulary(["foo", "bar"], existing=[])
        assert result == ["foo", "bar"]

    def test_empty_proposed_returns_empty(self):
        assert dedupe_against_vocabulary([], existing=["foo"]) == []

    def test_empty_strings_in_existing_ignored(self):
        result = dedupe_against_vocabulary(["foo"], existing=["", None])  # type: ignore[list-item]
        assert result == ["foo"]


class TestShouldApprove:
    def test_at_threshold_approves(self):
        assert should_approve(3, threshold=3) is True

    def test_above_threshold_approves(self):
        assert should_approve(10, threshold=3) is True

    def test_below_threshold_rejects(self):
        assert should_approve(2, threshold=3) is False

    def test_zero_count_rejects(self):
        assert should_approve(0) is False

    def test_default_threshold_is_three(self):
        assert should_approve(3) is True
        assert should_approve(2) is False


class TestPrefixLlm:
    def test_prefixes_each_tag(self):
        assert prefix_llm(["a", "b"]) == [f"{LLM_TAG_PREFIX}a", f"{LLM_TAG_PREFIX}b"]

    def test_empty_returns_empty(self):
        assert prefix_llm([]) == []

    def test_drops_empty_strings(self):
        assert prefix_llm(["a", "", "b"]) == [f"{LLM_TAG_PREFIX}a", f"{LLM_TAG_PREFIX}b"]

    def test_prefix_value(self):
        # Lock the prefix string so a recorder change can't silently drift it.
        assert LLM_TAG_PREFIX == "llm:"


class TestBuildTagEvent:
    def test_includes_all_fields(self):
        event = build_tag_event(
            path="topics/foo",
            proposed=["a", "b", "c"],
            committed=["a"],
            deduped=["b"],
            model="databricks-meta-llama-3-3-70b",
            raw='{"tags": ["a"]}',
        )
        assert event["path"] == "topics/foo"
        assert event["model"] == "databricks-meta-llama-3-3-70b"
        assert event["proposed"] == ["a", "b", "c"]
        assert event["committed"] == ["a"]
        assert event["deduped_against_vocab"] == ["b"]
        assert "tags" in event["raw_truncated"]

    def test_truncates_raw(self):
        long = "x" * 1000
        event = build_tag_event("p", [], [], [], "m", long)
        assert len(event["raw_truncated"]) == 300

    def test_none_raw_safe(self):
        event = build_tag_event("p", [], [], [], "m", "")
        assert event["raw_truncated"] == ""
