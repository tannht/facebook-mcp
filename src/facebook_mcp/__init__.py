"""Facebook Page MCP server and Graph API client."""

__version__ = "1.0.0"

from .graph import ConfigError, FacebookGraphAPI, GraphAPIError  # noqa: E402

__all__ = ["FacebookGraphAPI", "GraphAPIError", "ConfigError", "__version__"]
