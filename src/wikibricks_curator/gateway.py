"""Databricks AI Gateway chat-completions client."""

from __future__ import annotations

import json
import os
import subprocess
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def resolve_token(profile: str | None) -> str:
    """Resolve a gateway token from the environment or the Databricks CLI."""
    token = os.environ.get("WIKIBRICKS_GATEWAY_TOKEN")
    if token:
        return token
    command = ["databricks", "auth", "token"]
    if profile is not None:
        command.extend(["--profile", profile])
    try:
        result = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as error:
        raise ValueError("Databricks auth token command failed") from error
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise ValueError("Databricks auth token response was not JSON") from error
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not token:
        raise ValueError("Databricks auth token response did not contain access_token")
    return str(token)


def _json_content(response: Any) -> dict[str, Any]:
    text = response.read().decode("utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise RuntimeError("curation model response could not be decoded") from error
    if not isinstance(payload, dict) or not payload.get("choices"):
        raise RuntimeError("curation model returned no content")
    message = payload["choices"][0].get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("curation model returned empty content")
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1])
    try:
        value, _ = json.JSONDecoder().raw_decode(text)
    except json.JSONDecodeError as error:
        raise RuntimeError("curation model output could not be decoded") from error
    if not isinstance(value, dict):
        raise RuntimeError("curation model output must be a JSON object")
    return value


def chat_json(
    system_prompt: str,
    request: dict[str, Any],
    schema: dict[str, Any],
    *,
    base_url: str,
    token: str,
    model: str,
    temperature: float = 0.0,
    max_tokens: int = 8192,
    timeout: float = 180,
    reasoning_effort: str | None = "low",
    opener=urlopen,
) -> dict[str, Any]:
    """Call an OpenAI-compatible chat endpoint and return its JSON object."""
    messages = [
        {
            "role": "system",
            "content": (
                f"{system_prompt}\n\nReturn JSON matching this schema:\n"
                f"{json.dumps(schema, separators=(',', ':'))}"
            ),
        },
        {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
    ]
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if reasoning_effort is not None:
        # GLM 5.3 Flash otherwise spends the whole output budget on reasoning.
        payload["reasoning_effort"] = reasoning_effort
    http_request = Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        response = opener(http_request, timeout=timeout)
    except HTTPError as error:
        raise RuntimeError(f"curation model request failed: HTTP {error.code}") from error
    return _json_content(response)
