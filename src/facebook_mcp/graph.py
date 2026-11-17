"""Thin, defensive client for the Meta Graph API (Facebook Pages).

Design rules:
- Every HTTP call goes through ``_request`` (timeouts, JSON parsing, Graph error
  mapping, token redaction, retries for idempotent reads).
- Nothing in this module writes to stdout: an MCP stdio server owns stdout for
  JSON-RPC. Progress goes through the ``logging`` module (stderr by default).
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import requests

log = logging.getLogger("facebook_mcp")

DEFAULT_GRAPH_VERSION = "v24.0"
GRAPH_HOST = "https://graph.facebook.com"

# Files above this size use the chunked resumable upload instead of one multipart request.
RESUMABLE_THRESHOLD = 50 * 1024 * 1024
CHUNK_SIZE = 25 * 1024 * 1024

# (connect timeout, read timeout) in seconds.
TIMEOUT_DEFAULT = (10, 60)
TIMEOUT_UPLOAD = (10, 900)

# Graph error codes that are worth retrying for idempotent reads.
TRANSIENT_GRAPH_CODES = {1, 2, 4, 17, 32, 341, 613}

DEFAULT_PAGE_METRICS = [
    "page_post_engagements",
    "page_daily_follows_unique",
    "page_follows",
    "page_views_total",
    "page_media_view",
    "page_total_media_view_unique",
    "page_video_views",
]

DEFAULT_POST_METRICS = [
    "post_reactions_by_type_total",
    "post_clicks",
    "post_media_view",
    "post_total_media_view_unique",
    "post_video_views",
    "post_video_view_time",
]

PERIODS = ("day", "week", "days_28", "month", "lifetime", "total_over_range")

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv", ".mpeg", ".mpg", ".3gp"}

_TOKEN_PATTERNS = [
    re.compile(r"(access_token=)[^&\s'\"]+"),
    re.compile(r"(OAuth\s+)[A-Za-z0-9._\-]+"),
    re.compile(r"(client_secret=)[^&\s'\"]+"),
    re.compile(r"(fb_exchange_token=)[^&\s'\"]+"),
    re.compile(r"(input_token=)[^&\s'\"]+"),
    re.compile(r"\bEAA[A-Za-z0-9]{20,}"),
]


def redact(text: str) -> str:
    """Strip anything that looks like a token or secret from a message."""
    for pattern in _TOKEN_PATTERNS:
        if pattern.groups:
            text = pattern.sub(r"\1<redacted>", text)
        else:
            text = pattern.sub("<redacted>", text)
    return text


class GraphAPIError(RuntimeError):
    """A Graph API call failed. The message never contains access tokens."""

    def __init__(
        self,
        message: str,
        *,
        code: Optional[int] = None,
        subcode: Optional[int] = None,
        error_type: Optional[str] = None,
        status: Optional[int] = None,
        fbtrace_id: Optional[str] = None,
        network: bool = False,
    ) -> None:
        super().__init__(redact(message))
        self.code = code
        self.subcode = subcode
        self.error_type = error_type
        self.status = status
        self.fbtrace_id = fbtrace_id
        self.network = network

    @property
    def transient(self) -> bool:
        return (
            self.network
            or (self.status is not None and self.status >= 500)
            or (self.code in TRANSIENT_GRAPH_CODES)
        )


class ConfigError(ValueError):
    """Missing or invalid input/configuration (page id, token, file path...)."""


def _env(*names: str) -> Optional[str]:
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return None


def _allowed_dirs() -> List[Path]:
    raw = _env("FB_MCP_ALLOWED_DIRS")
    if not raw:
        return []
    return [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p.strip()]


def check_media_file(path: str, kind: str) -> Path:
    """Validate a local media path before it is uploaded to Facebook.

    - must exist, be a regular, non-empty file
    - extension must match the media kind ("image" or "video")
    - when FB_MCP_ALLOWED_DIRS is set, must live under one of those directories
    """
    if not path or not str(path).strip():
        raise ConfigError(f"Empty {kind} path.")
    try:
        p = Path(path).expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError):
        raise ConfigError(f"{kind.capitalize()} file not found: {path}") from None
    if not p.is_file():
        raise ConfigError(f"Not a regular file: {path}")
    allowed_ext = IMAGE_EXTENSIONS if kind == "image" else VIDEO_EXTENSIONS
    if p.suffix.lower() not in allowed_ext:
        raise ConfigError(
            f"Unsupported {kind} extension '{p.suffix}'. Allowed: {', '.join(sorted(allowed_ext))}"
        )
    roots = _allowed_dirs()
    if roots and not any(p == r or r in p.parents for r in roots):
        raise ConfigError(f"{path} is outside FB_MCP_ALLOWED_DIRS.")
    if p.stat().st_size == 0:
        raise ConfigError(f"File is empty: {path}")
    return p


def check_schedule_time(ts: Optional[int], *, min_lead_seconds: int = 600) -> Optional[int]:
    """Validate a UNIX timestamp (seconds) for scheduled publishing: >= now + 10 min."""
    if ts is None:
        return None
    try:
        ts_int = int(ts)
    except (TypeError, ValueError):
        raise ConfigError(f"scheduled time must be a UNIX timestamp in seconds, got {ts!r}") from None
    if ts_int > 10**11:
        raise ConfigError("scheduled time looks like milliseconds; pass seconds.")
    if ts_int < int(time.time()) + min_lead_seconds:
        raise ConfigError(f"scheduled time must be at least {min_lead_seconds // 60} minutes in the future.")
    return ts_int


class FacebookGraphAPI:
    """Client for Facebook Page operations on the Meta Graph API."""

    def __init__(
        self,
        page_access_token: Optional[str] = None,
        default_page_id: Optional[str] = None,
        graph_version: Optional[str] = None,
        session: Optional[requests.Session] = None,
        max_retries: int = 2,
        retry_backoff: float = 1.5,
    ) -> None:
        self.page_access_token = page_access_token or _env("FB_PAGE_ACCESS_TOKEN", "FACEBOOK_PAGE_ACCESS_TOKEN")
        self.default_page_id = default_page_id or _env("FB_PAGE_ID", "FACEBOOK_PAGE_ID")
        self.graph_version = graph_version or _env("META_GRAPH_API_VERSION") or DEFAULT_GRAPH_VERSION
        if not re.fullmatch(r"v\d+\.\d+", self.graph_version):
            raise ConfigError(f"META_GRAPH_API_VERSION must look like 'v24.0', got {self.graph_version!r}")
        self.base_url = f"{GRAPH_HOST}/{self.graph_version}"
        self.session = session or requests.Session()
        self.max_retries = max(0, int(max_retries))
        self.retry_backoff = retry_backoff
        self._sleep: Callable[[float], None] = time.sleep

    # ------------------------------------------------------------------ helpers

    def _resolve_page(self, page_id: Optional[str], access_token: Optional[str]) -> Tuple[str, str]:
        pid = page_id or self.default_page_id
        token = access_token or self.page_access_token
        if not pid:
            raise ConfigError("Missing page_id. Pass page_id or set FB_PAGE_ID.")
        if not token:
            raise ConfigError("Missing access token. Pass page_access_token or set FB_PAGE_ACCESS_TOKEN.")
        return str(pid), token

    def _resolve_token(self, access_token: Optional[str]) -> str:
        token = access_token or self.page_access_token
        if not token:
            raise ConfigError("Missing access token. Pass page_access_token or set FB_PAGE_ACCESS_TOKEN.")
        return token

    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        return f"{self.base_url}/{path_or_url.lstrip('/')}"

    def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        data: Any = None,
        files: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        timeout: Any = TIMEOUT_DEFAULT,
        retry: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Perform one Graph call and return the decoded JSON object.

        Reads (GET) are retried on network errors, HTTP 5xx and transient Graph
        codes. Writes are not retried unless ``retry=True``: a blindly retried
        post or upload could publish twice.
        """
        url = self._url(path_or_url)
        should_retry = (method.upper() == "GET") if retry is None else retry
        attempts = 1 + (self.max_retries if should_retry else 0)
        error: Optional[GraphAPIError] = None

        for attempt in range(attempts):
            try:
                resp = self.session.request(
                    method, url, params=params, data=data, files=files, headers=headers, timeout=timeout
                )
            except requests.RequestException as exc:
                error = GraphAPIError(f"Network error calling Graph API: {exc}", network=True)
            else:
                body, error = self._decode(resp)
                if error is None:
                    return body
            if attempt + 1 < attempts and error.transient:
                delay = self.retry_backoff * (2**attempt)
                log.warning("Graph call failed (%s); retrying in %.1fs", error, delay)
                self._sleep(delay)
                continue
            break

        assert error is not None
        raise error

    @staticmethod
    def _decode(resp: requests.Response) -> Tuple[Dict[str, Any], Optional[GraphAPIError]]:
        try:
            body = resp.json()
        except ValueError:
            return {}, GraphAPIError(
                f"Non-JSON response from Graph API (HTTP {resp.status_code}): {resp.text[:200]}",
                status=resp.status_code,
            )
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            err = body["error"]
            msg = err.get("error_user_msg") or err.get("message") or "Unknown Graph API error"
            code_txt = f"{err.get('code')}" + (f"/{err['error_subcode']}" if err.get("error_subcode") else "")
            return {}, GraphAPIError(
                f"Graph API error {code_txt}: {msg}",
                code=err.get("code"),
                subcode=err.get("error_subcode"),
                error_type=err.get("type"),
                status=resp.status_code,
                fbtrace_id=err.get("fbtrace_id"),
            )
        if resp.status_code >= 400:
            return {}, GraphAPIError(f"HTTP {resp.status_code} from Graph API: {str(body)[:200]}", status=resp.status_code)
        if isinstance(body, bool):
            return {"success": body}, None
        if not isinstance(body, dict):
            return {}, GraphAPIError(f"Unexpected Graph API response type: {type(body).__name__}")
        return body, None

    # -------------------------------------------------------------- pages/info

    def get_user_pages(self, user_access_token: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        """List Pages the user manages (needs a *user* token with pages_show_list).

        The returned dicts include each Page's ``access_token``; callers that
        expose results to a model must strip it.
        """
        token = user_access_token or _env("FB_USER_ACCESS_TOKEN") or self.page_access_token
        if not token:
            raise ConfigError("A user access token is required (pass user_access_token or set FB_USER_ACCESS_TOKEN).")
        res = self._request(
            "GET",
            "me/accounts",
            params={
                "access_token": token,
                "fields": "id,name,category,access_token,tasks,followers_count,fan_count,link",
                "limit": max(1, min(int(limit), 100)),
            },
        )
        return res.get("data", [])

    def get_page_info(self, page_id: Optional[str] = None, access_token: Optional[str] = None) -> Dict[str, Any]:
        pid, token = self._resolve_page(page_id, access_token)
        return self._request(
            "GET",
            pid,
            params={
                "fields": "id,name,fan_count,followers_count,link,about,category,verification_status,picture.type(large)",
                "access_token": token,
            },
        )

    # ---------------------------------------------------------------- posting

    def publish_post(
        self,
        message: str,
        link: Optional[str] = None,
        photo_path: Optional[str] = None,
        photo_url: Optional[str] = None,
        scheduled_publish_time: Optional[int] = None,
        page_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Publish (or schedule) a text, link or photo post."""
        pid, token = self._resolve_page(page_id, access_token)
        if photo_path and photo_url:
            raise ConfigError("Pass either photo_path or photo_url, not both.")
        if not (message and message.strip()) and not (photo_path or photo_url or link):
            raise ConfigError("A post needs a message, a link or a photo.")
        ts = check_schedule_time(scheduled_publish_time)

        def with_schedule(d: Dict[str, Any]) -> Dict[str, Any]:
            if ts:
                d["published"] = "false"
                d["scheduled_publish_time"] = ts
            return d

        if photo_path:
            p = check_media_file(photo_path, "image")
            with open(p, "rb") as fh:
                res = self._request(
                    "POST",
                    f"{pid}/photos",
                    data=with_schedule({"caption": message, "access_token": token}),
                    files={"source": (p.name, fh)},
                    timeout=TIMEOUT_UPLOAD,
                )
            kind = "photo"
        elif photo_url:
            res = self._request(
                "POST", f"{pid}/photos", data=with_schedule({"caption": message, "url": photo_url, "access_token": token})
            )
            kind = "photo"
        else:
            data: Dict[str, Any] = {"message": message, "access_token": token}
            if link:
                data["link"] = link
            res = self._request("POST", f"{pid}/feed", data=with_schedule(data))
            kind = "link" if link else "status"

        post_id = res.get("post_id") or res.get("id")
        if not post_id:
            raise GraphAPIError(f"Publish returned no id: {res}")
        return {
            "success": True,
            "post_id": post_id,
            "page_id": pid,
            "type": kind,
            "scheduled_publish_time": ts,
            "url": f"https://www.facebook.com/{post_id}",
        }

    def publish_video(
        self,
        video_path: str,
        description: str = "",
        title: Optional[str] = None,
        as_reel: bool = True,
        scheduled_publish_time: Optional[int] = None,
        page_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Upload a video as a Reel (3-phase API) or as a regular Page video.

        A Reel falls back to a regular Page video only when the Reels *start*
        phase is rejected, i.e. before any byte was uploaded, so one call can
        never publish the same file twice. Auth, network, rate-limit and 5xx
        errors are raised instead of falling back. ``title`` applies to regular
        Page videos only; Reels use ``description`` as the caption.
        """
        pid, token = self._resolve_page(page_id, access_token)
        path = check_media_file(video_path, "video")
        ts = check_schedule_time(scheduled_publish_time)

        if as_reel:
            try:
                video_id, upload_url = self._reels_start(pid, token)
            except GraphAPIError as exc:
                # Only a definitive "Reels not available here" answer justifies a fallback.
                # Auth, network, rate-limit and server errors would fail (or succeed later)
                # the same way, and must not silently turn a Reel into a regular video.
                if exc.transient or exc.code in (102, 190) or exc.status == 401:
                    raise
                log.warning("Reels start rejected (%s); falling back to Page video upload", exc)
                result = self._upload_page_video(path, description, title, pid, token, ts)
                result["note"] = f"Reels upload unavailable, published as a regular Page video instead: {exc}"
                return result
            return self._reels_transfer_and_finish(path, video_id, upload_url, description, pid, token, ts)
        return self._upload_page_video(path, description, title, pid, token, ts)

    def _reels_start(self, page_id: str, token: str) -> Tuple[str, str]:
        res = self._request("POST", f"{page_id}/video_reels", data={"upload_phase": "start", "access_token": token})
        video_id, upload_url = res.get("video_id"), res.get("upload_url")
        if not video_id or not upload_url:
            raise GraphAPIError(f"Reels start returned no video_id/upload_url: {res}")
        return str(video_id), str(upload_url)

    def _reels_transfer_and_finish(
        self,
        path: Path,
        video_id: str,
        upload_url: str,
        description: str,
        page_id: str,
        token: str,
        ts: Optional[int],
    ) -> Dict[str, Any]:
        size = path.stat().st_size
        headers = {"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)}
        with open(path, "rb") as fh:  # streamed from disk, never fully loaded in memory
            res = self._request("POST", upload_url, headers=headers, data=fh, timeout=TIMEOUT_UPLOAD, retry=False)
        if res.get("success") is False:
            raise GraphAPIError(f"Reels transfer failed: {res}")

        finish: Dict[str, Any] = {
            "upload_phase": "finish",
            "access_token": token,
            "video_id": video_id,
            "description": description,
        }
        if ts:
            finish["video_state"] = "SCHEDULED"
            finish["scheduled_publish_time"] = ts
        else:
            finish["video_state"] = "PUBLISHED"
        res = self._request("POST", f"{page_id}/video_reels", data=finish)
        if not res.get("success"):
            raise GraphAPIError(f"Reels finish failed: {res}")
        return {
            "success": True,
            "video_id": video_id,
            "page_id": page_id,
            "type": "reel",
            "scheduled_publish_time": ts,
            "url": f"https://www.facebook.com/reel/{video_id}",
        }

    def _upload_page_video(
        self,
        path: Path,
        description: str,
        title: Optional[str],
        page_id: str,
        token: str,
        ts: Optional[int],
    ) -> Dict[str, Any]:
        size = path.stat().st_size
        if size > RESUMABLE_THRESHOLD:
            return self._upload_resumable_video(path, size, description, title, page_id, token, ts)

        data: Dict[str, Any] = {"description": description, "access_token": token}
        if title:
            data["title"] = title
        if ts:
            data["published"] = "false"
            data["scheduled_publish_time"] = ts
        with open(path, "rb") as fh:
            res = self._request(
                "POST", f"{page_id}/videos", data=data, files={"source": (path.name, fh)}, timeout=TIMEOUT_UPLOAD
            )
        video_id = res.get("id")
        if not video_id:
            raise GraphAPIError(f"Video upload returned no id: {res}")
        return self._video_result(str(video_id), page_id, ts)

    def _upload_resumable_video(
        self,
        path: Path,
        size: int,
        description: str,
        title: Optional[str],
        page_id: str,
        token: str,
        ts: Optional[int],
        chunk_size: int = CHUNK_SIZE,
    ) -> Dict[str, Any]:
        endpoint = f"{page_id}/videos"
        start = self._request("POST", endpoint, data={"upload_phase": "start", "file_size": size, "access_token": token})
        session_id, video_id = start.get("upload_session_id"), start.get("video_id")
        if not session_id or not video_id:
            raise GraphAPIError(f"Resumable start returned no session/video id: {start}")
        offset = int(start.get("start_offset", 0))

        with open(path, "rb") as fh:
            while offset < size:
                fh.seek(offset)
                chunk = fh.read(chunk_size)
                if not chunk:
                    raise GraphAPIError(f"Local read returned no data at offset {offset} of {size}.")
                log.info("Uploading %s: %.1f/%.1f MB", path.name, offset / 2**20, size / 2**20)
                # Re-sending a chunk for the same start_offset is idempotent, so retries are safe here.
                res = self._request(
                    "POST",
                    endpoint,
                    data={
                        "upload_phase": "transfer",
                        "upload_session_id": session_id,
                        "start_offset": str(offset),
                        "access_token": token,
                    },
                    files={"video_file_chunk": (path.name, chunk)},
                    timeout=TIMEOUT_UPLOAD,
                    retry=True,
                )
                new_offset = int(res.get("start_offset", offset + len(chunk)))
                if new_offset <= offset:
                    raise GraphAPIError(f"Upload stalled: server kept start_offset at {offset} of {size} bytes.")
                offset = new_offset

        finish: Dict[str, Any] = {
            "upload_phase": "finish",
            "upload_session_id": session_id,
            "access_token": token,
            "description": description,
        }
        if title:
            finish["title"] = title
        if ts:
            finish["published"] = "false"
            finish["scheduled_publish_time"] = ts
        res = self._request("POST", endpoint, data=finish)
        if res.get("success") is False:
            raise GraphAPIError(f"Resumable finish failed: {res}")
        return self._video_result(str(video_id), page_id, ts)

    @staticmethod
    def _video_result(video_id: str, page_id: str, ts: Optional[int]) -> Dict[str, Any]:
        return {
            "success": True,
            "video_id": video_id,
            "page_id": page_id,
            "type": "video",
            "scheduled_publish_time": ts,
            "url": f"https://www.facebook.com/{page_id}/videos/{video_id}",
        }

    # ---------------------------------------------------------------- reading

    def get_recent_posts(
        self, limit: int = 10, page_id: Optional[str] = None, access_token: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        pid, token = self._resolve_page(page_id, access_token)
        res = self._request(
            "GET",
            f"{pid}/posts",
            params={
                "fields": "id,message,created_time,permalink_url,shares,"
                "reactions.summary(true).limit(0),comments.summary(true).limit(0)",
                "limit": max(1, min(int(limit), 100)),
                "access_token": token,
            },
        )
        return [self._post_summary(item) for item in res.get("data", [])]

    @staticmethod
    def _post_summary(item: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "id": item.get("id"),
            "message": item.get("message", ""),
            "created_time": item.get("created_time"),
            "url": item.get("permalink_url"),
            "stats": {
                "reactions": item.get("reactions", {}).get("summary", {}).get("total_count", 0),
                "comments": item.get("comments", {}).get("summary", {}).get("total_count", 0),
                "shares": item.get("shares", {}).get("count", 0),
            },
        }

    def get_scheduled_posts(
        self,
        limit: int = 100,
        page_id: Optional[str] = None,
        access_token: Optional[str] = None,
        max_items: int = 500,
    ) -> List[Dict[str, Any]]:
        """List scheduled (not yet published) posts, following pagination up to ``max_items``."""
        pid, token = self._resolve_page(page_id, access_token)
        url: Optional[str] = f"{pid}/scheduled_posts"
        params: Optional[Dict[str, Any]] = {
            "fields": "id,message,created_time,scheduled_publish_time,permalink_url,full_picture",
            "limit": max(1, min(int(limit), 100)),
            "access_token": token,
        }
        items: List[Dict[str, Any]] = []
        while url and len(items) < max_items:
            res = self._request("GET", url, params=params)
            items.extend(res.get("data", []))
            url = res.get("paging", {}).get("next")
            params = None  # the "next" URL already carries every query parameter
        results = [
            {
                "id": it.get("id"),
                "message": it.get("message", ""),
                "created_time": it.get("created_time"),
                "scheduled_publish_time": it.get("scheduled_publish_time"),
                "permalink_url": it.get("permalink_url"),
                "picture": it.get("full_picture"),
            }
            for it in items[:max_items]
        ]
        results.sort(key=lambda x: x["scheduled_publish_time"] or 0)
        return results

    def reschedule_post(self, post_id: str, new_scheduled_time: int, access_token: Optional[str] = None) -> Dict[str, Any]:
        token = self._resolve_token(access_token)
        ts = check_schedule_time(new_scheduled_time)
        self._request("POST", str(post_id), data={"scheduled_publish_time": ts, "access_token": token})
        return {"success": True, "post_id": post_id, "new_scheduled_time": ts}

    def publish_scheduled_post_now(self, post_id: str, access_token: Optional[str] = None) -> Dict[str, Any]:
        token = self._resolve_token(access_token)
        self._request("POST", str(post_id), data={"is_published": "true", "access_token": token})
        return {"success": True, "post_id": post_id}

    def get_page_insights(
        self,
        metrics: Optional[Sequence[str]] = None,
        period: str = "day",
        since: Optional[str] = None,
        until: Optional[str] = None,
        page_id: Optional[str] = None,
        access_token: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Page-level insights. Deprecated/invalid metrics are reported in
        ``metric_errors`` instead of failing the whole call."""
        pid, token = self._resolve_page(page_id, access_token)
        if period not in PERIODS:
            raise ConfigError(f"Unsupported period {period!r}. Use one of: {', '.join(PERIODS)}")
        wanted = [m.strip() for m in (metrics or DEFAULT_PAGE_METRICS) if m and m.strip()]
        params: Dict[str, Any] = {"period": period, "access_token": token}
        if since:
            params["since"] = since
        if until:
            params["until"] = until

        data, errors = self._fetch_metrics(f"{pid}/insights", wanted, params)
        summary: Dict[str, Any] = {}
        for entry in data:
            values = entry.get("values", [])
            summary[entry.get("name")] = {
                "title": entry.get("title", entry.get("name")),
                "description": entry.get("description", ""),
                "period": entry.get("period"),
                "latest_value": values[-1].get("value") if values else None,
                "latest_date": values[-1].get("end_time") if values else None,
                "history": values[-7:],
            }
        return {"page_id": pid, "period": period, "insights": summary, "metric_errors": errors}

    def get_post_insights(self, post_id: str, access_token: Optional[str] = None) -> Dict[str, Any]:
        token = self._resolve_token(access_token)
        basic = self._request(
            "GET",
            str(post_id),
            params={
                "fields": "id,message,created_time,permalink_url,shares,"
                "reactions.summary(true).limit(0),comments.summary(true).limit(0)",
                "access_token": token,
            },
        )
        data, errors = self._fetch_metrics(f"{post_id}/insights", list(DEFAULT_POST_METRICS), {"access_token": token})
        detailed: Dict[str, Any] = {}
        for entry in data:
            values = entry.get("values") or [{}]
            detailed[entry.get("name")] = values[0].get("value")
        summary = self._post_summary(basic)
        return {
            "post_id": summary["id"],
            "message": summary["message"],
            "created_time": summary["created_time"],
            "url": summary["url"],
            "summary_stats": summary["stats"],
            "detailed_insights": detailed,
            "metric_errors": errors,
        }

    def _fetch_metrics(
        self, endpoint: str, metrics: List[str], params: Dict[str, Any]
    ) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
        """Fetch metrics in one call; if Graph rejects the batch as invalid (#100),
        fetch them one by one so a single deprecated metric does not hide the rest."""
        if not metrics:
            raise ConfigError("No metrics requested.")
        try:
            res = self._request("GET", endpoint, params={**params, "metric": ",".join(metrics)})
            return res.get("data", []), {}
        except GraphAPIError as exc:
            if exc.code != 100 or len(metrics) == 1:
                raise
        data: List[Dict[str, Any]] = []
        errors: Dict[str, str] = {}
        for metric in metrics:
            try:
                res = self._request("GET", endpoint, params={**params, "metric": metric})
                data.extend(res.get("data", []))
            except GraphAPIError as exc:
                if exc.code != 100:
                    raise
                errors[metric] = str(exc)
        return data, errors

    def delete_post(self, post_id: str, access_token: Optional[str] = None) -> Dict[str, Any]:
        token = self._resolve_token(access_token)
        res = self._request("DELETE", str(post_id), params={"access_token": token})
        return {"success": bool(res.get("success", True)), "deleted_post_id": post_id}

    # ------------------------------------------------------------------ tokens

    def debug_token(self, token: str, app_token: Optional[str] = None) -> Dict[str, Any]:
        res = self._request("GET", "debug_token", params={"input_token": token, "access_token": app_token or token})
        return res.get("data", {})

    def exchange_long_lived_user_token(self, user_token: str, app_id: str, app_secret: str) -> Dict[str, Any]:
        return self._request(
            "GET",
            "oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": user_token,
            },
        )
