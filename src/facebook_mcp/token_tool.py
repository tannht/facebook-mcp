"""`facebook-mcp-token`: turn a user access token into a Page access token and save it.

A Page token obtained from a *long-lived* (60-day) user token does not expire.
A Page token obtained from a short-lived (1-2 h) user token expires with it, so
pass --app-id/--app-secret (or set FB_APP_ID/FB_APP_SECRET) to extend the user
token first.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .config import default_env_file, load_environment
from .graph import ConfigError, FacebookGraphAPI, GraphAPIError


def describe_expiry(ts: Optional[int]) -> str:
    if not ts:
        return "never"
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError, OSError):
        return str(ts)


def update_env_file(env_path: Path, values: Dict[str, str]) -> None:
    """Set KEY=value lines in an env file, keeping every other line; file mode 600."""
    lines: List[str] = []
    remaining = dict(values)
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines(keepends=True):
            key = line.split("=", 1)[0].strip()
            if key in remaining:
                lines.append(f"{key}={remaining.pop(key)}\n")
            else:
                lines.append(line if line.endswith("\n") else line + "\n")
    for key, value in remaining.items():
        lines.append(f"{key}={value}\n")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(env_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    os.chmod(env_path, 0o600)


def main(argv=None) -> int:
    load_environment()
    parser = argparse.ArgumentParser(
        prog="facebook-mcp-token",
        description="Exchange a user access token for a Page access token and save it to .env.",
    )
    parser.add_argument("--user-token", help="user access token (default: FB_USER_ACCESS_TOKEN)")
    parser.add_argument("--app-id", help="Meta app id, to extend the user token to 60 days (default: FB_APP_ID)")
    parser.add_argument("--app-secret", help="Meta app secret (default: FB_APP_SECRET)")
    parser.add_argument("--page-id", help="Page to select when the user manages several (default: FB_PAGE_ID)")
    parser.add_argument("--env-file", type=Path, help=f"file to update (default: {default_env_file()})")
    parser.add_argument("--auto-save", action="store_true", help="save without asking")
    parser.add_argument("--print-token", action="store_true", help="print the Page token instead of saving it")
    args = parser.parse_args(argv)

    user_token = args.user_token or os.getenv("FB_USER_ACCESS_TOKEN")
    app_id = args.app_id or os.getenv("FB_APP_ID")
    app_secret = args.app_secret or os.getenv("FB_APP_SECRET")
    wanted_page = args.page_id or os.getenv("FB_PAGE_ID")
    env_path: Path = args.env_file or default_env_file()

    if not user_token:
        print("No user token. Pass --user-token or set FB_USER_ACCESS_TOKEN.", file=sys.stderr)
        return 1

    try:
        api = FacebookGraphAPI(page_access_token=user_token)
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 1

    try:
        if app_id and app_secret:
            res = api.exchange_long_lived_user_token(user_token, app_id, app_secret)
            if res.get("access_token"):
                user_token = res["access_token"]
                days = int(res.get("expires_in", 0)) // 86400
                print(f"[OK] User token extended (~{days} days).")
        else:
            info = api.debug_token(user_token)
            if info.get("expires_at"):
                print("[WARN] Short-lived user token and no app id/secret: the Page token will expire too.")
                print("       Pass --app-id and --app-secret to get a Page token that does not expire.")

        pages = api.get_user_pages(user_access_token=user_token)
    except GraphAPIError as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        print("The user token needs pages_show_list (and pages_read_engagement).", file=sys.stderr)
        return 1

    if not pages:
        print("No Pages found for this user. Did you tick the Page when generating the token?", file=sys.stderr)
        return 1

    print(f"Found {len(pages)} Page(s):")
    selected = None
    for i, page in enumerate(pages, 1):
        token = page.get("access_token") or ""
        info: Dict = {}
        if token:
            try:
                info = api.debug_token(token)
            except GraphAPIError:
                info = {}
        scopes = set(info.get("scopes", []))
        print(f"[{i}] {page.get('name')} (id {page.get('id')})")
        print(f"    followers: {page.get('followers_count', page.get('fan_count', 0)):,}")
        print(f"    token expires: {describe_expiry(info.get('expires_at'))}")
        for scope in ("pages_manage_posts", "pages_read_engagement", "read_insights"):
            print(f"    {'[x]' if scope in scopes else '[ ]'} {scope}")
        if wanted_page and str(page.get("id")) == str(wanted_page):
            selected = page

    if selected is None:
        if wanted_page and len(pages) > 1:
            print(f"Page {wanted_page} is not in the list.", file=sys.stderr)
        if len(pages) == 1:
            selected = pages[0]
        elif not sys.stdin.isatty():
            print("Several Pages found: pass --page-id.", file=sys.stderr)
            return 1
        else:
            try:
                choice = int(input(f"Select a Page (1-{len(pages)}): ").strip())
            except (ValueError, EOFError, KeyboardInterrupt):
                print("Cancelled.")
                return 1
            if not 1 <= choice <= len(pages):
                print("Invalid choice.", file=sys.stderr)
                return 1
            selected = pages[choice - 1]

    page_id, page_token = str(selected["id"]), selected.get("access_token")
    if not page_token:
        print("This Page returned no access token (missing Page role/permissions).", file=sys.stderr)
        return 1
    print(f"Selected: {selected.get('name')} (id {page_id})")

    if args.print_token:
        print(f"FB_PAGE_ID={page_id}")
        print(f"FB_PAGE_ACCESS_TOKEN={page_token}")
        return 0

    if not args.auto_save:
        if not sys.stdin.isatty():
            print("Non-interactive shell: re-run with --auto-save or --print-token.", file=sys.stderr)
            return 1
        answer = input(f"Save FB_PAGE_ID and FB_PAGE_ACCESS_TOKEN to {env_path}? [Y/n] ").strip().lower()
        if answer not in ("", "y", "yes"):
            print("Not saved. Re-run with --print-token to show the values.")
            return 0

    update_env_file(env_path, {"FB_PAGE_ID": page_id, "FB_PAGE_ACCESS_TOKEN": page_token})
    print(f"[OK] Saved to {env_path} (mode 600). Run `facebook-mcp-check` to verify.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
