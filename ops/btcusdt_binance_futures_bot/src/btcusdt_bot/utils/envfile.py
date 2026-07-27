from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path = ".env", *, override: bool = False) -> bool:
    """Load a simple .env file into os.environ.

    The parser is intentionally small and dependency-free:
    - ignores blank lines and leading/trailing whitespace
    - ignores lines starting with '#'
    - accepts KEY=VALUE pairs
    - strips matching single or double quotes around VALUE
    - does not override existing env vars unless override=True
    """
    env_path = Path(path)
    if not env_path.exists() or not env_path.is_file():
        return False

    loaded = False
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if override or key not in os.environ:
            os.environ[key] = value
            loaded = True
    return loaded
