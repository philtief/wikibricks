"""`wiki-target` — list configured wikis or set the active one.

  wiki-target              # list (* marks active)
  wiki-target <name>       # set active
  wiki-target --clear      # clear active

The active target lives at `~/.wikibricks/active-target`. Hooks read it via
`wikibricks_recorder.config.get_active_target()`.
"""

from __future__ import annotations

import argparse
import sys
from typing import TextIO

from wikibricks_recorder import config


def _list(out: TextIO) -> int:
    wikis = config.list_wikis()
    if not wikis:
        print("no wikis configured. run `wiki-init` to set one up.", file=out)
        return 0
    active = config.get_active_target()
    print("Configured wikis:", file=out)
    for name in sorted(wikis):
        marker = "*" if name == active else " "
        cfg = wikis[name]
        coord = f"{cfg.get('catalog', '?')}.{cfg.get('schema', '?')}"
        print(f"  {marker} {name:<20} {coord}", file=out)
    if active is None:
        print("\nNo active target. Set with: wiki-target <name>", file=out)
    return 0


def _set(name: str, out: TextIO) -> int:
    wikis = config.list_wikis()
    if name not in wikis:
        names = ", ".join(sorted(wikis)) or "(none)"
        print(f"error: unknown wiki '{name}'. Configured: {names}", file=out)
        return 2
    config.set_active_target(name)
    cfg = wikis[name]
    print(
        f"Active target: {name} → "
        f"{cfg.get('catalog', '?')}.{cfg.get('schema', '?')}",
        file=out,
    )
    return 0


def run(args: list[str] | None = None, *, out: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wiki-target")
    parser.add_argument("name", nargs="?", help="Wiki name to activate.")
    parser.add_argument(
        "--clear", action="store_true", help="Clear the active target."
    )
    ns = parser.parse_args(args)
    out = out or sys.stdout

    if ns.clear:
        config.clear_active_target()
        print("Active target cleared.", file=out)
        return 0
    if ns.name:
        return _set(ns.name, out)
    return _list(out)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
