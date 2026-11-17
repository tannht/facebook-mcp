"""Environment loading shared by the server and the CLI helpers.

Precedence (highest first):
1. Variables already in the process environment (e.g. the ``env`` block of an
   MCP client config). ``.env`` files never override them.
2. The file named by ``FB_MCP_ENV_FILE``.
3. ``.env`` in the current working directory.
4. ``.env`` at the root of a source checkout (next to ``pyproject.toml``), so
   ``python server.py`` works no matter which directory the MCP client starts in.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import List, Optional

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is a declared dependency, but stay usable without it
    load_dotenv = None  # type: ignore[assignment]


def source_root() -> Optional[Path]:
    root = Path(__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").is_file() else None


def env_file_candidates() -> List[Path]:
    paths: List[Path] = []
    explicit = os.getenv("FB_MCP_ENV_FILE")
    if explicit:
        paths.append(Path(explicit).expanduser())
    paths.append(Path.cwd() / ".env")
    root = source_root()
    if root:
        paths.append(root / ".env")
    seen, unique = set(), []
    for p in paths:
        key = str(p.resolve()) if p.exists() else str(p)
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def default_env_file() -> Path:
    """Where helper scripts write credentials when no --env-file is given."""
    explicit = os.getenv("FB_MCP_ENV_FILE")
    if explicit:
        return Path(explicit).expanduser()
    root = source_root()
    return (root / ".env") if root else (Path.cwd() / ".env")


def load_environment() -> List[Path]:
    """Load .env files without overriding existing variables. Returns the files loaded."""
    loaded: List[Path] = []
    if load_dotenv is None or env_flag("FB_MCP_SKIP_DOTENV"):
        return loaded
    for path in env_file_candidates():
        if path.is_file():
            load_dotenv(path, override=False)
            loaded.append(path)
    return loaded


def setup_logging() -> None:
    """Configure this package's logger on stderr (stdout belongs to MCP JSON-RPC).

    The level applies to the ``facebook_mcp`` logger only, so it works even when
    the MCP SDK has already configured the root logger. urllib3 is pinned to
    WARNING because its DEBUG lines contain full URLs, including access tokens.
    """
    level = getattr(logging, os.getenv("FB_MCP_LOG_LEVEL", "INFO").upper(), logging.INFO)
    logger = logging.getLogger("facebook_mcp")
    logger.setLevel(level)
    if not any(getattr(h, "_facebook_mcp", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        handler._facebook_mcp = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger.propagate = False
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")
