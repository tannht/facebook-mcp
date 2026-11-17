"""End-to-end MCP stdio handshake against the real server process (no network)."""

import os
import subprocess
import sys
from pathlib import Path

import anyio

ROOT = Path(__file__).resolve().parents[1]
WRITE_TOOLS = {"fb_publish_post", "fb_publish_video", "fb_reschedule_post", "fb_publish_scheduled_now", "fb_delete_post"}
READ_TOOLS = {"fb_list_pages", "fb_get_page_info", "fb_get_recent_posts", "fb_get_scheduled_posts",
              "fb_get_page_insights", "fb_get_post_insights"}


async def _list_tools(extra_env):
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    env = {k: v for k, v in os.environ.items() if not k.startswith(("FB_", "FACEBOOK_", "META_"))}
    env.update(extra_env)
    env["FB_MCP_SKIP_DOTENV"] = "1"
    params = StdioServerParameters(command=sys.executable, args=[str(ROOT / "server.py")], env=env, cwd=str(ROOT / "tests"))
    async with stdio_client(params) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            result = await session.call_tool("fb_get_page_info", {})
            return tools, result


def field(obj, camel, snake):
    """mcp 1.x exposes camelCase attributes, mcp 2.x snake_case."""
    return getattr(obj, snake) if snake in type(obj).model_fields else getattr(obj, camel)


def hint(tool, camel, snake):
    return field(tool.annotations, camel, snake)


def run(extra_env):
    return anyio.run(_list_tools, extra_env)


def test_server_starts_without_credentials_and_lists_all_tools():
    tools, result = run({})
    names = {t.name for t in tools}
    assert names == READ_TOOLS | WRITE_TOOLS
    by_name = {t.name: t for t in tools}
    assert hint(by_name["fb_delete_post"], "destructiveHint", "destructive_hint") is True
    assert hint(by_name["fb_get_page_info"], "readOnlyHint", "read_only_hint") is True
    assert hint(by_name["fb_publish_post"], "readOnlyHint", "read_only_hint") is False
    # missing credentials -> a tool error, not a crash
    assert field(result, "isError", "is_error") is True
    assert "page_id" in result.content[0].text


def test_read_only_mode_hides_write_tools():
    tools, _ = run({"FB_MCP_READ_ONLY": "1"})
    assert {t.name for t in tools} == READ_TOOLS


def test_version_flag():
    out = subprocess.run([sys.executable, str(ROOT / "server.py"), "--version"], capture_output=True, text=True)
    assert out.returncode == 0 and "facebook-mcp" in out.stdout
