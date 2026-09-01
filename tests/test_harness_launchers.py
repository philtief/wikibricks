import json
import os
import shutil
import subprocess
from pathlib import Path
from subprocess import CompletedProcess

import pytest

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
