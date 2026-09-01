"""Databricks serverless wheel entry point for weekly remote maintenance."""

from __future__ import annotations

import argparse
import json
from typing import Any

from psycopg.conninfo import make_conninfo

from wikibricks.postgres_store import PostgresStore
from wikibricks_remote.maintenance import run_maintenance
from wikibricks_remote.resources import RemotePolicy, load_policy


def _database_url(workspace: Any, args: argparse.Namespace) -> str:
    endpoint_name = (
        f"projects/{args.project}/branches/{args.branch}/endpoints/{args.endpoint}"
    )
    endpoint = workspace.postgres.get_endpoint(endpoint_name)
    credential = workspace.postgres.generate_database_credential(endpoint_name)
    user = workspace.current_user.me().user_name
    return make_conninfo(
        host=endpoint.status.hosts.host,
        port=5432,
        dbname=args.database,
        user=user,
        password=credential.token,
        sslmode="require",
    )


def _json_content(response: Any) -> dict[str, Any]:
    if not response.choices or not response.choices[0].message:
        raise RuntimeError("curation model returned no message")
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("curation model returned empty content")
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
    value, _ = json.JSONDecoder().raw_decode(text)
    if not isinstance(value, dict):
        raise RuntimeError("curation model output must be a JSON object")
    return value


def _proposer(workspace: Any, endpoint: str, policy: RemotePolicy):
    def propose(
        system_prompt: str,
        request: dict[str, Any],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

        response = workspace.serving_endpoints.query(
            name=endpoint,
            messages=[
                ChatMessage(
                    role=ChatMessageRole.SYSTEM,
                    content=(
                        f"{system_prompt}\n\nReturn JSON matching this schema:\n"
                        f"{json.dumps(schema, separators=(',', ':'))}"
                    ),
                ),
                ChatMessage(
                    role=ChatMessageRole.USER,
                    content=json.dumps(request, ensure_ascii=False),
                ),
            ],
            temperature=policy.temperature,
            max_tokens=policy.max_output_tokens,
        )
        return _json_content(response)

    return propose


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wikibricks-remote-maintenance")
    parser.add_argument("--project", required=True)
    parser.add_argument("--branch", default="production")
    parser.add_argument("--endpoint", default="primary")
    parser.add_argument("--database", default="wikibricks")
    parser.add_argument("--model-endpoint", required=True)
    parser.add_argument("--policy")
    return parser


def main(argv: list[str] | None = None) -> int:
    from databricks.sdk import WorkspaceClient

    args = build_parser().parse_args(argv)
    policy = load_policy(args.policy)
    workspace = WorkspaceClient()
    store = PostgresStore(_database_url(workspace, args))
    result = run_maintenance(
        store,
        policy=policy,
        proposer=_proposer(workspace, args.model_endpoint, policy),
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
