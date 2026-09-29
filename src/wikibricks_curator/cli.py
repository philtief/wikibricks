"""Command-line entry point for the optional local curator."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from wikibricks_curator.gateway import chat_json, resolve_token


def build_parser() -> argparse.ArgumentParser:
    from wikibricks.config import load_config

    config = load_config()
    parser = argparse.ArgumentParser(prog="wikibricks-curator")
    commands = parser.add_subparsers(dest="command", required=True)
    propose = commands.add_parser("propose", help="Propose local curation updates")
    propose.add_argument("--database-path", type=Path, default=config.database_path)
    propose.add_argument("--base-url", required=True)
    propose.add_argument("--profile", default=config.sync_profile)
    propose.add_argument("--model", default="system.ai.glm-5-3-flash")
    propose.add_argument("--projects", type=int, default=3)
    propose.add_argument("--no-apply", action="store_true")
    propose.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from wikibricks_curator.curator import run_curator

    token = resolve_token(args.profile)

    def chat(system_prompt: str, request: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
        return chat_json(
            system_prompt,
            request,
            schema,
            base_url=args.base_url,
            token=token,
            model=args.model,
        )

    result = run_curator(
        args.database_path,
        chat=chat,
        projects=args.projects,
        apply=not args.no_apply,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, default=str, indent=2))
    return 1 if result["errors"] else 0
