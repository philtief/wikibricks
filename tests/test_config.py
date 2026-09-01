from __future__ import annotations

from pathlib import Path

import pytest

from wikibricks.config import WikiBricksConfig, load_config


def test_config_precedence(tmp_path: Path):
    home = tmp_path / "home"
    user = home / ".wikibricks" / "config.yml"
    user.parent.mkdir(parents=True)
    user.write_text("search:\n  default_results: 7\n")
    override = tmp_path / "override.yml"
    override.write_text("sync:\n  batch_size: 25\n")

    config = load_config(
        home=home,
        environ={
            "WIKIBRICKS_CONFIG": str(override),
            "WIKIBRICKS_DATABASE_URL": "postgresql:///explicit",
        },
    )

    assert config.database_url == "postgresql:///explicit"
    assert config.search_default_results == 7
    assert config.sync_batch_size == 25
    assert config.sync_apply_policy == "safe"


@pytest.mark.parametrize(
    ("yaml_text", "message"),
    [
        ("unknown: true\n", "unknown"),
        ("version: 2\n", "version"),
        ("search:\n  default_results: 0\n", "search.default_results"),
        ("sync:\n  apply_policy: unsafe\n", "sync.apply_policy"),
    ],
)
def test_config_rejects_invalid_values(tmp_path, yaml_text, message):
    path = tmp_path / "config.yml"
    path.write_text(yaml_text)

    with pytest.raises(ValueError, match=message):
        load_config(path, home=tmp_path / "missing", environ={})


def test_config_environment_values_and_cli_defaults(tmp_path: Path):
    from wikibricks.cli import build_parser

    config = load_config(
        home=tmp_path,
        environ={
            "WIKIBRICKS_SEARCH_DEFAULT_RESULTS": "8",
            "WIKIBRICKS_SEARCH_MAXIMUM_RESULTS": "12",
            "WIKIBRICKS_PRUNE_ARCHIVED_SESSIONS_AFTER_DAYS": "30",
            "WIKIBRICKS_SYNC_BATCH_SIZE": "40",
            "WIKIBRICKS_SYNC_APPLY_POLICY": "all",
        },
    )
    parser = build_parser(config)

    assert parser.parse_args(["search", "term"]).k == 8
    assert (
        parser.parse_args(["curate"]).prune_archived_sessions_after_days == 30
    )
    lakebase = parser.parse_args(
        [
            "sync",
            "lakebase",
            "--profile",
            "p",
            "--project",
            "project",
            "--drain",
        ]
    )
    assert lakebase.limit == 40
    assert lakebase.drain is True
    assert lakebase.max_batches == 100
    assert parser.parse_args(["sync", "plan", "00000000-0000-0000-0000-000000000001"]).policy == "all"
    assert isinstance(config, WikiBricksConfig)
