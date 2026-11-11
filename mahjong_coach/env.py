"""Load settings from a ``.env`` file without extra dependencies.

Lines look like ``NAME=value``. Blank lines and ``#`` comments are skipped, and
surrounding quotes are removed. Anything already set in the real environment
wins, so ``$env:JEV_API_KEY = ...`` still overrides the file.
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_dotenv(path: str | os.PathLike | None = None) -> bool:
    """Read ``path`` (default: ``.env`` in the project root) into ``os.environ``.

    Returns True if a file was read.
    """
    p = Path(path) if path else PROJECT_ROOT / ".env"
    if not p.is_file():
        return False
    for raw in p.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        name, _, value = line.partition("=")
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if name and name not in os.environ:
            os.environ[name] = value
    return True
