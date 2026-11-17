# facebook-mcp

[![CI](https://github.com/tannht/facebook-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/tannht/facebook-mcp/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)

An [MCP](https://modelcontextprotocol.io) server that lets AI assistants (Claude Code, Claude Desktop, Cursor, Gemini CLI and any other MCP client) manage a **Facebook Page** through the official Meta Graph API.

[Tiếng Việt](README.vi.md)

- Publish text, link and photo posts (local file or URL), now or scheduled
- Upload videos as **Reels** (3-phase Reels API) or as regular Page videos, with chunked resumable upload for large files
- List, reschedule or immediately publish scheduled posts
- Read Page insights and per-post insights; retired metrics are reported instead of breaking the call
- Delete posts
- Read-only mode, upload folder allow-list, token redaction in every error message

It talks only to `graph.facebook.com` with your own Page token. No third-party service, no scraping, no browser automation.

---

## Contents

- [Tools](#tools)
- [Requirements](#requirements)
- [Installation](#installation)
- [Getting a Page access token](#getting-a-page-access-token)
- [Configuration](#configuration)
- [Connecting an MCP client](#connecting-an-mcp-client)
- [Example prompts](#example-prompts)
- [Behaviour details](#behaviour-details)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [Using the Python client directly](#using-the-python-client-directly)
- [Development](#development)
- [License](#license)

---

## Tools

| Tool | Type | What it does | Main parameters |
|---|---|---|---|
| `fb_get_page_info` | read | Page name, likes, followers, link, about, category, picture | `page_id`? |
| `fb_get_recent_posts` | read | Latest posts with reactions, comments, shares | `limit` (1-100, default 10) |
| `fb_get_scheduled_posts` | read | Scheduled, unpublished posts sorted by time (up to 500) | `limit` |
| `fb_get_page_insights` | read | Page analytics | `period`, `metrics`?, `since`?, `until`? |
| `fb_get_post_insights` | read | Stats for one post or video | `post_id` |
| `fb_list_pages` | read | Pages the user manages (Page tokens are never returned) | `user_access_token`? |
| `fb_publish_post` | write | Text, link or photo post, now or scheduled | `message`, `link`?, `photo_path`?, `photo_url`?, `scheduled_publish_time`? |
| `fb_publish_video` | write | Reel or regular Page video, now or scheduled | `video_path`, `description`, `title`?, `as_reel` (default `true`), `scheduled_publish_time`? |
| `fb_reschedule_post` | write | Change the time of a scheduled post | `post_id`, `new_scheduled_time_unix` |
| `fb_publish_scheduled_now` | write | Publish a scheduled post immediately | `post_id` |
| `fb_delete_post` | destructive | Delete a post or video | `post_id` |

Every tool also accepts optional `page_id` / `page_access_token` overrides (where they apply), so one server can work with several Pages. Write tools carry MCP annotations (`readOnlyHint`, `destructiveHint`) so clients can ask for confirmation, and they are not registered at all when `FB_MCP_READ_ONLY=1`.

## Requirements

- Python 3.10+
- A Facebook **Page** (personal profiles cannot be automated through the Graph API)
- A Meta app (any type that offers the Pages permissions, e.g. *Business*) and a Page access token with:
  - `pages_manage_posts` (publish, schedule, delete)
  - `pages_read_engagement` (posts, reactions, comments)
  - `read_insights` (analytics)
  - `pages_show_list` (only for `fb_list_pages` and the token helper)

Tested with the `mcp` Python SDK 1.21.2+ (`FastMCP`) and 2.x (`MCPServer`). Older 1.x releases break with current pydantic.

## Installation

**Option A: run with `uvx` (no install, recommended for MCP clients)**

```bash
uvx --from git+https://github.com/tannht/facebook-mcp facebook-mcp --version
```

**Option B: pip**

```bash
pip install git+https://github.com/tannht/facebook-mcp
```

**Option C: from a clone**

```bash
git clone https://github.com/tannht/facebook-mcp.git
cd facebook-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # then fill it in
```

All options install three commands:

| Command | Purpose |
|---|---|
| `facebook-mcp` | The MCP server (stdio). `python server.py` in a clone does the same. |
| `facebook-mcp-check` | Read-only check of your Page id, token validity, expiry and permissions |
| `facebook-mcp-token` | Exchanges a user token for a Page token and saves it to `.env` |

## Getting a Page access token

Facebook gives you a short-lived **user** token first. The server needs a **Page** token. A Page token derived from a *long-lived* user token does not expire, which is what you want for an always-on server.

### 1. Create a Meta app

1. Go to [developers.facebook.com/apps](https://developers.facebook.com/apps) → **Create app**.
2. Pick a use case/type that offers Page management (for example *Business*), name it and create it.
3. Note the **App ID** and **App Secret** (*App settings → Basic*).

### 2. Generate a user token

1. Open the [Graph API Explorer](https://developers.facebook.com/tools/explorer/) and select your app.
2. Under **User or Page**, choose **User Token**.
3. Add the permissions `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`, `read_insights`.
4. Click **Generate Access Token**, approve, and **tick the Page(s)** you want to manage in the dialog.
5. Copy the token.

### 3. Exchange it for a Page token (automatic)

```bash
facebook-mcp-token --user-token "EAAB..." --app-id 123 --app-secret abc --auto-save
```

The helper:

1. extends the user token to ~60 days (needs app id + secret; without them the Page token expires in about an hour, and the tool warns you),
2. lists your Pages with follower count, token expiry and granted permissions,
3. picks the Page (`--page-id` when you manage several, or an interactive prompt),
4. writes `FB_PAGE_ID` and `FB_PAGE_ACCESS_TOKEN` to `.env` with file mode `600`.

| Flag | Meaning |
|---|---|
| `--user-token` | User token (default: `FB_USER_ACCESS_TOKEN`) |
| `--app-id`, `--app-secret` | Extend the user token first (default: `FB_APP_ID`, `FB_APP_SECRET`) |
| `--page-id` | Page to select when you manage several (default: `FB_PAGE_ID`) |
| `--env-file` | File to update (default: `FB_MCP_ENV_FILE`, else `.env` in the clone or current directory) |
| `--auto-save` | Save without asking |
| `--print-token` | Print the values instead of saving them |

Tip: passing secrets as flags leaves them in your shell history. You can put `FB_USER_ACCESS_TOKEN`, `FB_APP_ID` and `FB_APP_SECRET` in `.env` instead and run `facebook-mcp-token --auto-save`.

### 3b. Or do it by hand

1. Paste the user token into the [Access Token Debugger](https://developers.facebook.com/tools/debug/accesstoken/) and click **Extend Access Token**.
2. In the Graph API Explorer, call `GET /me/accounts` with the extended token.
3. Each entry has the Page `id` (→ `FB_PAGE_ID`) and `access_token` (→ `FB_PAGE_ACCESS_TOKEN`).

### 4. Verify

```bash
facebook-mcp-check
```

```text
[OK] Page: My Page (id 1234567890)
[OK] Token type: PAGE | valid: True | expires: never
     [x] pages_manage_posts
     [x] pages_read_engagement
     [x] read_insights
[OK] 3 recent post(s): ...
```

## Configuration

| Variable | Required | Description |
|---|---|---|
| `FB_PAGE_ID` | yes | Default Page id |
| `FB_PAGE_ACCESS_TOKEN` | yes | Page access token |
| `FB_USER_ACCESS_TOKEN` | no | User token for `fb_list_pages` and `facebook-mcp-token` |
| `FB_APP_ID`, `FB_APP_SECRET` | no | Used by `facebook-mcp-token` to extend user tokens |
| `META_GRAPH_API_VERSION` | no | Graph API version, default `v24.0` |
| `FB_MCP_READ_ONLY` | no | `1` hides every publish/schedule/delete tool |
| `FB_MCP_ALLOWED_DIRS` | no | Folders (separated by `:`; `;` on Windows) that local photos/videos must live in |
| `FB_MCP_ENV_FILE` | no | Explicit path to a `.env` file |
| `FB_MCP_SKIP_DOTENV` | no | `1` ignores all `.env` files |
| `FB_MCP_LOG_LEVEL` | no | `DEBUG`, `INFO` (default), `WARNING`... Logs go to stderr |

`FACEBOOK_PAGE_ID` / `FACEBOOK_PAGE_ACCESS_TOKEN` are accepted as aliases.

**Where values come from (highest priority first):** the process environment (e.g. the `env` block of your MCP client) → `FB_MCP_ENV_FILE` → `.env` in the working directory → `.env` at the root of a source clone. A `.env` file never overrides a variable that is already set.

## Connecting an MCP client

Replace the placeholder values. If you keep credentials in `.env` inside a clone, you can drop the `env` blocks.

### Claude Code

```bash
claude mcp add facebook \
  -e FB_PAGE_ID=YOUR_PAGE_ID -e FB_PAGE_ACCESS_TOKEN=YOUR_PAGE_TOKEN \
  -- uvx --from git+https://github.com/tannht/facebook-mcp facebook-mcp
```

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "facebook": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/tannht/facebook-mcp", "facebook-mcp"],
      "env": {
        "FB_PAGE_ID": "YOUR_PAGE_ID",
        "FB_PAGE_ACCESS_TOKEN": "YOUR_PAGE_TOKEN"
      }
    }
  }
}
```

### Cursor

`.cursor/mcp.json` in a project, or `~/.cursor/mcp.json` globally: same `mcpServers` block as Claude Desktop.

### Gemini CLI

`~/.gemini/settings.json`: same `mcpServers` block as Claude Desktop.

### Running from a clone instead of uvx

```json
{
  "mcpServers": {
    "facebook": {
      "command": "/path/to/facebook-mcp/.venv/bin/python",
      "args": ["/path/to/facebook-mcp/server.py"]
    }
  }
}
```

With this layout the server reads `/path/to/facebook-mcp/.env`, whatever directory the client starts it from.

## Example prompts

- "Show my Page info and the last 5 posts with their reactions, comments and shares."
- "Which of my last 20 posts got the most engagement? Compare with the Page insights for the past week."
- "Post 'We are live!' with the link https://example.com to my Page."
- "Upload `/videos/teaser.mp4` as a Reel with the caption 'Teaser #launch', scheduled for tomorrow 19:00 Ho Chi Minh time."
- "List my scheduled posts and move the 21:00 one to 20:00."
- "Delete post 1234567890_987654321." (the assistant should confirm first)

## Behaviour details

**Scheduling.** `scheduled_publish_time` and `new_scheduled_time_unix` are UNIX timestamps in **seconds** and must be at least 10 minutes in the future; values in milliseconds are rejected. Meta also enforces an upper limit (around 75 days for posts).

**Photos.** Either `photo_path` (local file: jpg, jpeg, png, gif, webp, bmp, tif, tiff, heic) or `photo_url` (public URL), not both. `link` applies to text posts.

**Videos and Reels.**
- `as_reel=true` (default) uses the Reels API: start → upload (streamed from disk) → finish. The video must meet [Meta's Reels publishing specs](https://developers.facebook.com/docs/video-api/guides/reels-publishing) (vertical 9:16, short duration, minimum resolution). `title` is only used for regular Page videos; a Reel's caption is `description`.
- If the Page cannot publish Reels, the *start* step is rejected and the server uploads a regular Page video instead; the result contains a `note`. The fallback only happens before any byte is sent, so one call never publishes the same video twice. Token, network, rate-limit and 5xx errors never trigger the fallback.
- `as_reel=false` uploads a regular Page video. Files over 50 MB use resumable upload in 25 MB chunks; each chunk is retried on network errors and the upload fails loudly if the server stops advancing.
- Accepted extensions: mp4, mov, m4v, avi, mkv, webm, wmv, mpeg, mpg, 3gp.

**Insights.** Meta retires metrics regularly (for example `page_impressions` and `post_impressions` are gone). Defaults use metrics that work today:

- Page: `page_post_engagements`, `page_daily_follows_unique`, `page_follows`, `page_views_total`, `page_media_view`, `page_total_media_view_unique`, `page_video_views`
- Post: `post_reactions_by_type_total`, `post_clicks`, `post_media_view`, `post_total_media_view_unique`, `post_video_views`, `post_video_view_time`

If one requested metric is invalid, the server fetches the others one by one and lists the bad ones under `metric_errors`.

**Reliability.**
- Every request has a timeout (60 s for normal calls, 15 min for uploads).
- Reads are retried up to 2 times with exponential backoff on network errors, HTTP 5xx and rate-limit codes (4, 17, 32, 341, 613). Writes are never retried automatically, so a timeout cannot create a duplicate post.
- Non-JSON responses (proxies, outages) become clear errors instead of crashes.
- Nothing is written to stdout except the MCP protocol; logs go to stderr.
- The server starts and lists its tools even without credentials; calls then return a clear "missing page_id/token" error.

## Security

- **Never commit `.env`.** It is in `.gitignore`; `facebook-mcp-token` writes it with mode `600`.
- **Tokens never reach the model.** `fb_list_pages` strips Page tokens, and every error message is scrubbed of `access_token=`, `OAuth …` headers, app secrets and `EAA…` strings.
- **Limit what can be uploaded.** A model can be tricked into "uploading" a sensitive local file. Set `FB_MCP_ALLOWED_DIRS` to your media folders; extensions are also checked.
- **Read-only mode.** Use `FB_MCP_READ_ONLY=1` for analytics-only setups.
- **Confirm before publishing.** Publishing and deleting are public and irreversible. The server instructions ask the assistant to confirm, and write tools carry MCP hints, but keep your client's tool-approval prompts enabled for write tools.
- A Page token is as powerful as a Page admin for the granted permissions. If it leaks, revoke it (remove the app under *Facebook Settings → Business integrations*, or change your Facebook password) and generate a new one.

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `Missing page_id` / `Missing access token` | Variables not set. Run `facebook-mcp-check`; check the client's `env` block or `.env` location. |
| `Graph API error 190` | Token expired, revoked or malformed. Generate a new one (steps 2-3 above). |
| `Graph API error 10` / `200` | Missing permission or Page role. Re-generate with all four permissions and tick the Page. |
| `Graph API error 100` | Invalid parameter or retired metric. Check the id; for insights see `metric_errors`. |
| `Graph API error 4`, `17`, `32`, `613` | Rate limited. Reads retry automatically; otherwise wait and try again later. |
| `Graph API error 368` | Blocked by Facebook's spam/abuse systems. Slow down; review the content. |
| Reel published as a regular video (`note` in result) | The Page cannot use the Reels API, or the video does not meet Reels specs. |
| `scheduled time must be at least 10 minutes in the future` | Pass UNIX **seconds**, ≥ 10 min ahead, computed in the right timezone. |
| `is outside FB_MCP_ALLOWED_DIRS` | Move the file into an allowed folder or extend the variable. |
| Client shows the server as failed / no tools | Run `facebook-mcp --version` (or `python server.py --version`) in the same environment and read the client's MCP log. Note: code that imports `mcp.server.fastmcp` breaks on `mcp` 2.x; this project supports both. |

## Using the Python client directly

```python
from facebook_mcp import FacebookGraphAPI, GraphAPIError

api = FacebookGraphAPI()  # reads FB_PAGE_ID / FB_PAGE_ACCESS_TOKEN
print(api.get_page_info()["name"])
for post in api.get_recent_posts(limit=5):
    print(post["id"], post["stats"])

try:
    api.publish_video("/videos/clip.mp4", description="Hello", as_reel=True)
except GraphAPIError as exc:
    print("failed:", exc, exc.code)
```

The client does not load `.env` by itself; call `facebook_mcp.config.load_environment()` first if you rely on a `.env` file.

## Development

```bash
pip install -e ".[dev]"
ruff check .
pytest -q
```

The test suite uses mocked HTTP for the Graph client (errors, retries, redaction, Reels fallback rules, chunked upload, pagination, insights) and starts the real server over stdio for an MCP handshake. It never calls Facebook. CI runs on Python 3.10, 3.12 and 3.13 against `mcp==1.21.2` (minimum), latest `mcp` 1.x and `mcp>=2`.

Project layout:

```
facebook-mcp/
├── server.py                  # run from a clone: python server.py
├── src/facebook_mcp/
│   ├── server.py              # MCP tools
│   ├── graph.py               # Graph API client
│   ├── config.py              # .env loading, logging
│   ├── check.py               # facebook-mcp-check
│   └── token_tool.py          # facebook-mcp-token
├── tests/
├── .env.example
└── pyproject.toml
```

Issues and pull requests are welcome.

## License

[MIT](LICENSE). This project is not affiliated with or endorsed by Meta.
