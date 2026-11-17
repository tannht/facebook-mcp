"""MCP server exposing Facebook Page tools over stdio."""

from __future__ import annotations

import functools
from typing import Any, Dict, List, Optional

from mcp.types import ToolAnnotations

try:  # mcp >= 2.0 renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server  # type: ignore[no-redef]
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore[no-redef]

from . import __version__
from .config import env_flag, load_environment, setup_logging
from .graph import ConfigError, FacebookGraphAPI, GraphAPIError

load_environment()

READ_ONLY = env_flag("FB_MCP_READ_ONLY")

INSTRUCTIONS = """\
Manage a Facebook Page through the Meta Graph API.
- Read tools: fb_get_page_info, fb_get_recent_posts, fb_get_scheduled_posts,
  fb_get_page_insights, fb_get_post_insights, fb_list_pages.
- Write tools (absent when FB_MCP_READ_ONLY=1): fb_publish_post, fb_publish_video,
  fb_reschedule_post, fb_publish_scheduled_now, fb_delete_post.
Publishing and deleting are public and irreversible: confirm the exact content,
target Page and time with the user before calling a write tool.
Scheduled times are UNIX timestamps in seconds, at least 10 minutes in the future.
"""

mcp = _Server("facebook-mcp", instructions=INSTRUCTIONS)

_api: Optional[FacebookGraphAPI] = None


def api() -> FacebookGraphAPI:
    """Create the client lazily so the server starts (and lists tools) without credentials."""
    global _api
    if _api is None:
        _api = FacebookGraphAPI()
    return _api


READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)


def tool(annotations: ToolAnnotations, *, write: bool = False):
    """Register a tool. Expected failures (bad input, Graph API errors) become
    ToolError so the client sees the reason; write tools are skipped in read-only mode."""

    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except (ConfigError, GraphAPIError) as exc:
                raise ToolError(str(exc)) from exc

        if write and READ_ONLY:
            return fn
        return mcp.tool(annotations=annotations)(wrapper)

    return decorator


# ---------------------------------------------------------------- read tools


@tool(READ)
def fb_list_pages(user_access_token: Optional[str] = None) -> List[Dict[str, Any]]:
    """List the Facebook Pages the user manages: id, name, category, followers, tasks.

    Needs a USER access token with pages_show_list (FB_USER_ACCESS_TOKEN by default).
    Page access tokens are never returned; use the get-token CLI to store one.

    :param user_access_token: Optional user token; defaults to FB_USER_ACCESS_TOKEN.
    """
    pages = api().get_user_pages(user_access_token=user_access_token)
    return [
        {**{k: v for k, v in page.items() if k != "access_token"}, "has_page_token": bool(page.get("access_token"))}
        for page in pages
    ]


@tool(READ)
def fb_get_page_info(page_id: Optional[str] = None, page_access_token: Optional[str] = None) -> Dict[str, Any]:
    """Get Page details: name, likes (fan_count), followers, link, about, category, picture.

    :param page_id: Optional Page id; defaults to FB_PAGE_ID.
    :param page_access_token: Optional Page token; defaults to FB_PAGE_ACCESS_TOKEN.
    """
    return api().get_page_info(page_id=page_id, access_token=page_access_token)


@tool(READ)
def fb_get_recent_posts(
    limit: int = 10, page_id: Optional[str] = None, page_access_token: Optional[str] = None
) -> List[Dict[str, Any]]:
    """List the Page's latest published posts with reactions, comments and shares.

    :param limit: Number of posts (1-100, default 10).
    :param page_id: Optional Page id; defaults to FB_PAGE_ID.
    :param page_access_token: Optional Page token.
    """
    return api().get_recent_posts(limit=limit, page_id=page_id, access_token=page_access_token)


@tool(READ)
def fb_get_scheduled_posts(
    limit: int = 100, page_id: Optional[str] = None, page_access_token: Optional[str] = None
) -> List[Dict[str, Any]]:
    """List posts scheduled but not yet published, sorted by publish time (up to 500).

    :param limit: Page size per Graph request (1-100, default 100).
    :param page_id: Optional Page id; defaults to FB_PAGE_ID.
    :param page_access_token: Optional Page token.
    """
    return api().get_scheduled_posts(limit=limit, page_id=page_id, access_token=page_access_token)


@tool(READ)
def fb_get_page_insights(
    period: str = "day",
    metrics: Optional[List[str]] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    page_id: Optional[str] = None,
    page_access_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Page analytics. Default metrics: page_post_engagements, page_daily_follows_unique,
    page_follows, page_views_total, page_media_view, page_total_media_view_unique,
    page_video_views. Metrics Meta has retired are listed in metric_errors.

    :param period: day, week, days_28, month, lifetime or total_over_range.
    :param metrics: Optional custom metric names.
    :param since: Optional start (YYYY-MM-DD or UNIX seconds).
    :param until: Optional end (YYYY-MM-DD or UNIX seconds).
    :param page_id: Optional Page id; defaults to FB_PAGE_ID.
    :param page_access_token: Optional Page token.
    """
    return api().get_page_insights(
        metrics=metrics, period=period, since=since, until=until, page_id=page_id, access_token=page_access_token
    )


@tool(READ)
def fb_get_post_insights(post_id: str, page_access_token: Optional[str] = None) -> Dict[str, Any]:
    """Stats for one post or video: reactions/comments/shares plus reactions by type,
    clicks, media views, unique viewers, video views and view time.

    :param post_id: Post id ("{page_id}_{post_id}") or video id.
    :param page_access_token: Optional Page token.
    """
    return api().get_post_insights(post_id=post_id, access_token=page_access_token)


# --------------------------------------------------------------- write tools


@tool(WRITE, write=True)
def fb_publish_post(
    message: str,
    link: Optional[str] = None,
    photo_path: Optional[str] = None,
    photo_url: Optional[str] = None,
    scheduled_publish_time: Optional[int] = None,
    page_id: Optional[str] = None,
    page_access_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Publish or schedule a Page post: text, text + link, or photo (local file or URL).
    This is public: confirm content and timing with the user first.

    :param message: Post text / photo caption.
    :param link: Optional URL to attach (text posts only).
    :param photo_path: Optional local image path (jpg, png, gif, webp...).
    :param photo_url: Optional public image URL. Use photo_path or photo_url, not both.
    :param scheduled_publish_time: Optional UNIX seconds, at least 10 minutes ahead.
    :param page_id: Optional Page id; defaults to FB_PAGE_ID.
    :param page_access_token: Optional Page token.
    """
    return api().publish_post(
        message=message,
        link=link,
        photo_path=photo_path,
        photo_url=photo_url,
        scheduled_publish_time=scheduled_publish_time,
        page_id=page_id,
        access_token=page_access_token,
    )


@tool(WRITE, write=True)
def fb_publish_video(
    video_path: str,
    description: str = "",
    title: Optional[str] = None,
    as_reel: bool = True,
    scheduled_publish_time: Optional[int] = None,
    page_id: Optional[str] = None,
    page_access_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Upload a local video to the Page, now or scheduled. This is public: confirm first.

    as_reel=True uses the Reels API (Meta's Reels specs apply: vertical 9:16, short
    duration); if the Page cannot post Reels, it falls back to a regular Page video
    and says so in "note".
    as_reel=False uploads a regular Page video; files over 50 MB use chunked upload.

    :param video_path: Local video path (mp4, mov, ...).
    :param description: Caption.
    :param title: Optional title (regular Page videos only; ignored for Reels).
    :param as_reel: Reel (default) or regular Page video.
    :param scheduled_publish_time: Optional UNIX seconds, at least 10 minutes ahead.
    :param page_id: Optional Page id; defaults to FB_PAGE_ID.
    :param page_access_token: Optional Page token.
    """
    return api().publish_video(
        video_path=video_path,
        description=description,
        title=title,
        as_reel=as_reel,
        scheduled_publish_time=scheduled_publish_time,
        page_id=page_id,
        access_token=page_access_token,
    )


@tool(WRITE, write=True)
def fb_reschedule_post(
    post_id: str, new_scheduled_time_unix: int, page_access_token: Optional[str] = None
) -> Dict[str, Any]:
    """Change the publish time of a scheduled post or video.

    :param post_id: Id of the scheduled post/video.
    :param new_scheduled_time_unix: New time, UNIX seconds, at least 10 minutes ahead.
    :param page_access_token: Optional Page token.
    """
    return api().reschedule_post(
        post_id=post_id, new_scheduled_time=new_scheduled_time_unix, access_token=page_access_token
    )


@tool(WRITE, write=True)
def fb_publish_scheduled_now(post_id: str, page_access_token: Optional[str] = None) -> Dict[str, Any]:
    """Publish a scheduled post immediately. This is public: confirm first.

    :param post_id: Id of the scheduled post.
    :param page_access_token: Optional Page token.
    """
    return api().publish_scheduled_post_now(post_id=post_id, access_token=page_access_token)


@tool(DESTRUCTIVE, write=True)
def fb_delete_post(post_id: str, page_access_token: Optional[str] = None) -> Dict[str, Any]:
    """Permanently delete a post or video from the Page. Irreversible: confirm first.

    :param post_id: Id of the post/video to delete.
    :param page_access_token: Optional Page token.
    """
    return api().delete_post(post_id=post_id, access_token=page_access_token)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="facebook-mcp", description="Facebook Page MCP server (stdio).")
    parser.add_argument("--version", action="version", version=f"facebook-mcp {__version__}")
    parser.parse_args()
    setup_logging()
    mcp.run()


if __name__ == "__main__":
    main()
