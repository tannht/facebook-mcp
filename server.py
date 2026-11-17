#!/usr/bin/env python3
"""Run the MCP server straight from a source checkout: `python server.py`.

Equivalent to the installed `facebook-mcp` command.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from facebook_mcp.server import main  # noqa: E402

if __name__ == "__main__":
    main()
