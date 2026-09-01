from __future__ import annotations

import getpass
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import quote

import pytest

from wikibricks.postgres_store import PostgresStore


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="module")
def postgres_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    root = tmp_path_factory.mktemp("wikibricks-postgres")
    data = root / "data"
    socket_dir = Path(tempfile.mkdtemp(prefix="wbpg-sock-", dir="/tmp"))
    port = _free_port()
    subprocess.run(
        ["initdb", "-D", str(data), "--auth=trust", "--no-locale", "-E", "UTF8"],
        check=True,
        capture_output=True,
        text=True,
    )
    process = subprocess.Popen(
        ["postgres", "-D", str(data), "-k", str(socket_dir), "-p", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    user = getpass.getuser()
    try:
        for _ in range(100):
            ready = subprocess.run(
                ["pg_isready", "-h", str(socket_dir), "-p", str(port)],
                capture_output=True,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.05)
        else:
            raise RuntimeError("disposable PostgreSQL did not become ready")
        subprocess.run(
            ["createdb", "-h", str(socket_dir), "-p", str(port), "wikibricks"],
            check=True,
            capture_output=True,
            text=True,
        )
        yield f"postgresql://{quote(user)}@/wikibricks?host={quote(str(socket_dir))}&port={port}"
    finally:
        process.terminate()
        process.wait(timeout=10)
        shutil.rmtree(socket_dir)


@pytest.fixture
def store(postgres_url: str) -> PostgresStore:
    result = PostgresStore(postgres_url)
    result.migrate()
    result.clear_all()
    return result
