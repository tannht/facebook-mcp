"""`facebook-mcp-check`: verify the configured Page id and token (read-only)."""

from __future__ import annotations

import argparse
import sys

from .config import load_environment
from .graph import ConfigError, FacebookGraphAPI, GraphAPIError


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="facebook-mcp-check", description="Check Facebook Page credentials (read-only).")
    parser.add_argument("--posts", type=int, default=3, help="number of recent posts to list (default 3)")
    args = parser.parse_args(argv)

    loaded = load_environment()
    print("Facebook MCP connection check")
    print("env files:", ", ".join(str(p) for p in loaded) or "(none, using process environment)")

    try:
        api = FacebookGraphAPI()
        page_id, token = api._resolve_page(None, None)
    except ConfigError as exc:
        print(f"[FAIL] {exc}")
        print("Set FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN (see .env.example).")
        return 1

    print(f"Graph API version: {api.graph_version}")
    print(f"FB_PAGE_ID: {page_id}")
    print(f"FB_PAGE_ACCESS_TOKEN: set ({len(token)} chars)")

    try:
        info = api.get_page_info()
        print(f"[OK] Page: {info.get('name')} (id {info.get('id')})")
        print(f"     likes {info.get('fan_count', 0):,} | followers {info.get('followers_count', 0):,}")
        print(f"     {info.get('link')}")

        data = api.debug_token(token)
        if data:
            expires = data.get("expires_at")
            print(f"[OK] Token type: {data.get('type')} | valid: {data.get('is_valid')} | "
                  f"expires: {'never' if not expires else expires}")
            scopes = set(data.get("scopes", []))
            for scope in ("pages_manage_posts", "pages_read_engagement", "read_insights", "pages_show_list"):
                print(f"     {'[x]' if scope in scopes else '[ ]'} {scope}")

        posts = api.get_recent_posts(limit=args.posts)
        print(f"[OK] {len(posts)} recent post(s):")
        for p in posts:
            text = (p["message"] or "").replace("\n", " ")
            print(f"  - {p['id']}: {text[:60]}{'...' if len(text) > 60 else ''}")
            print(f"    reactions {p['stats']['reactions']} | comments {p['stats']['comments']} | shares {p['stats']['shares']}")
    except GraphAPIError as exc:
        print(f"[FAIL] {exc}")
        print("Hints: the token may be expired or revoked, lack permissions, or the Page id may be wrong.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
