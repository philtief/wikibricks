import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest
import yaml

from wikibricks.omnigent_install import install_integrations


def _runner(command: list[str], **_: object) -> CompletedProcess[str]:
    return CompletedProcess(command, 0, stdout="", stderr="")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is required to run the Pi extension")
def test_pi_extension_forwards_all_tools_over_mcp(tmp_path: Path):
    fake_mcp = tmp_path / "fake-mcp"
    fake_mcp.write_text(
        """#!/usr/bin/env python3
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    method = request.get("method")
    if method == "initialize":
        result = {
            "protocolVersion": request["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "1"},
        }
    elif method == "tools/call":
        arguments = request["params"]["arguments"]
        result = {
            "content": [{"type": "text", "text": f"fake:{arguments['query']}"}],
            "isError": False,
        }
    else:
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
"""
    )
    fake_mcp.chmod(0o755)

    install_integrations(
        home=tmp_path,
        run=_runner,
        which=lambda name: {
            "wikibricks-mcp": str(fake_mcp),
            "pi": "/tools/pi",
        }.get(name),
    )
    extension = tmp_path / ".pi" / "agent" / "extensions" / "wikibricks-mcp.js"
    node_runner = tmp_path / "run-extension.js"
    node_runner.write_text(
        """const extension = require(process.argv[2]);
const tools = [];
const events = {};
extension({
  registerTool(tool) { tools.push(tool); },
  on(name, callback) { events[name] = callback; },
});

(async () => {
  const search = tools.find((tool) => tool.name === "wiki_search");
  const result = await search.execute("call-1", {query: "needle", k: 2});
  if (events.session_shutdown) await events.session_shutdown();
  process.stdout.write(JSON.stringify({names: tools.map((tool) => tool.name), result}));
})().catch((error) => {
  process.stderr.write(String(error));
  process.exit(1);
});
"""
    )

    completed = subprocess.run(
        [shutil.which("node") or "node", str(node_runner), str(extension)],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "HOME": str(tmp_path)},
        timeout=10,
    )
    output = json.loads(completed.stdout)
    assert output["names"] == [
        "wiki_search",
        "wiki_read_full",
        "wiki_index",
        "wiki_write_page",
        "wiki_promote_answer",
    ]
    assert output["result"] == {
        "content": [{"type": "text", "text": "fake:needle"}],
        "isError": False,
    }


def test_opencode_environment_preserves_existing_inline_configuration():
    from wikibricks.harness_launchers import prepare_opencode_environment

    original = {
        "OPENCODE_CONFIG_CONTENT": json.dumps(
            {
                "theme": "dark",
                "mcp": {
                    "existing": {
                        "type": "remote",
                        "url": "https://example.test/mcp",
                    }
                },
            }
        ),
        "UNCHANGED": "value",
    }

    prepared = prepare_opencode_environment(original, "/bin/wikibricks-mcp")

    assert original["OPENCODE_CONFIG_CONTENT"] != prepared["OPENCODE_CONFIG_CONTENT"]
    assert prepared["UNCHANGED"] == "value"
    assert json.loads(prepared["OPENCODE_CONFIG_CONTENT"]) == {
        "theme": "dark",
        "mcp": {
            "existing": {
                "type": "remote",
                "url": "https://example.test/mcp",
            },
            "wikibricks": {
                "type": "local",
                "command": ["/bin/wikibricks-mcp"],
                "enabled": True,
            },
        },
    }


def test_hermes_home_preserves_omnigent_session_configuration(tmp_path: Path):
    from wikibricks.harness_launchers import prepare_hermes_home

    hermes_home = tmp_path / "hermes-session"
    config_path = hermes_home / "config.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        yaml.safe_dump(
            {
                "policy_hook": "/session/policy.py",
                "mcp_servers": {"omnigent": {"command": "/session/mcp"}},
            }
        )
    )

    updated = prepare_hermes_home(
        {"HERMES_HOME": str(hermes_home)}, "/bin/wikibricks-mcp"
    )

    assert updated == config_path
    assert yaml.safe_load(config_path.read_text()) == {
        "policy_hook": "/session/policy.py",
        "mcp_servers": {
            "omnigent": {"command": "/session/mcp"},
            "wikibricks": {"command": "/bin/wikibricks-mcp", "args": []},
        },
    }


def test_opencode_launcher_preserves_arguments_environment_and_exit_code(tmp_path: Path):
    result_path = tmp_path / "result.json"
    downstream = tmp_path / "downstream.py"
    downstream.write_text(
        """import json
import os
import sys
from pathlib import Path

Path(os.environ["RESULT_PATH"]).write_text(json.dumps({
    "arguments": sys.argv[1:],
    "config": json.loads(os.environ["OPENCODE_CONFIG_CONTENT"]),
}))
raise SystemExit(23)
"""
    )
    manifest = tmp_path / ".wikibricks" / "omnigent-install.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "mcp_command": "/bin/wikibricks-mcp",
                "launchers": {"opencode": sys.executable},
            }
        )
    )
    existing = {
        "mcp": {"existing": {"type": "remote", "url": "https://example.test"}}
    }
    source_root = Path(__file__).parents[1] / "src"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "wikibricks.harness_launchers",
            "opencode",
            str(downstream),
            "--model",
            "test-model",
        ],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HOME": str(tmp_path),
            "PYTHONPATH": str(source_root),
            "RESULT_PATH": str(result_path),
            "OPENCODE_CONFIG_CONTENT": json.dumps(existing),
        },
    )

    assert completed.returncode == 23
    result = json.loads(result_path.read_text())
    assert result["arguments"] == ["--model", "test-model"]
    assert result["config"]["mcp"] == {
        "existing": {"type": "remote", "url": "https://example.test"},
        "wikibricks": {
            "type": "local",
            "command": ["/bin/wikibricks-mcp"],
            "enabled": True,
        },
    }


def test_installer_preserves_harness_args_and_original_downstream_paths(tmp_path: Path):
    omnigent_config = tmp_path / ".omnigent" / "config.yaml"
    omnigent_config.parent.mkdir(parents=True)
    omnigent_config.write_text(
        yaml.safe_dump(
            {
                "harness": {
                    "opencode-native": {
                        "command": "/custom/opencode",
                        "args": ["serve"],
                    },
                    "hermes-native": {
                        "command": "/custom/hermes",
                        "args": ["--config", "session.yaml"],
                    },
                }
            }
        )
    )

    def run(command: list[str], **_: object) -> CompletedProcess[str]:
        if command == ["omnigent", "--version"]:
            return CompletedProcess(command, 0, stdout="omnigent 0.11.0\n", stderr="")
        return CompletedProcess(command, 0, stdout="", stderr="")

    binaries = {
        "wikibricks-mcp": "/tools/wikibricks-mcp",
        "omnigent": "/tools/omnigent",
        "opencode": "/tools/opencode",
        "hermes": "/tools/hermes",
    }
    install_integrations(home=tmp_path, run=run, which=binaries.get)

    wrappers = {
        name: str(tmp_path / ".wikibricks" / "bin" / name)
        for name in ("opencode", "hermes")
    }
    updated = yaml.safe_load(omnigent_config.read_text())
    assert updated["harness"]["opencode-native"] == {
        "command": wrappers["opencode"],
        "args": ["serve"],
    }
    assert updated["harness"]["hermes-native"] == {
        "command": wrappers["hermes"],
        "args": ["--config", "session.yaml"],
    }

    manifest_path = tmp_path / ".wikibricks" / "omnigent-install.json"
    first_manifest = json.loads(manifest_path.read_text())
    assert first_manifest["launchers"] == {
        "opencode": "/tools/opencode",
        "hermes": "/tools/hermes",
    }
    assert first_manifest["previous_harness_commands"] == {
        "opencode-native": "/custom/opencode",
        "hermes-native": "/custom/hermes",
    }

    binaries.update({"opencode": wrappers["opencode"], "hermes": wrappers["hermes"]})
    install_integrations(home=tmp_path, run=run, which=binaries.get)

    second_manifest = json.loads(manifest_path.read_text())
    assert second_manifest["launchers"] == first_manifest["launchers"]
    assert second_manifest["previous_harness_commands"] == first_manifest[
        "previous_harness_commands"
    ]
