import time

import pytest
import requests
from conftest import FakeResponse as R

from facebook_mcp.graph import (
    RESUMABLE_THRESHOLD,
    ConfigError,
    FacebookGraphAPI,
    GraphAPIError,
    check_media_file,
    check_schedule_time,
    redact,
)

TOKEN = "EAATESTTOKEN1234567890abcdefXYZ"


def graph_error(code, msg="boom", status=400, **extra):
    return R({"error": {"message": msg, "code": code, "type": "OAuthException", **extra}}, status=status)


# ------------------------------------------------------------------ config


def test_missing_page_id_and_token():
    api = FacebookGraphAPI(session=object())
    with pytest.raises(ConfigError, match="page_id"):
        api.get_page_info()
    api = FacebookGraphAPI(default_page_id="1", session=object())
    with pytest.raises(ConfigError, match="access token"):
        api.get_page_info()
    for call in (lambda: api.delete_post("1"), lambda: api.publish_scheduled_post_now("1"),
                 lambda: api.get_post_insights("1")):
        with pytest.raises(ConfigError):
            call()


def test_env_and_version(monkeypatch):
    monkeypatch.setenv("FB_PAGE_ID", "42")
    monkeypatch.setenv("FB_PAGE_ACCESS_TOKEN", "tok")
    monkeypatch.setenv("META_GRAPH_API_VERSION", "v23.0")
    api = FacebookGraphAPI(session=object())
    assert api.default_page_id == "42" and api.base_url.endswith("/v23.0")
    monkeypatch.setenv("META_GRAPH_API_VERSION", "23")
    with pytest.raises(ConfigError):
        FacebookGraphAPI(session=object())


def test_schedule_time_validation():
    now = int(time.time())
    assert check_schedule_time(None) is None
    assert check_schedule_time(now + 3600) == now + 3600
    with pytest.raises(ConfigError, match="future"):
        check_schedule_time(now + 60)
    with pytest.raises(ConfigError, match="milliseconds"):
        check_schedule_time((now + 3600) * 1000)
    with pytest.raises(ConfigError):
        check_schedule_time("tomorrow")


def test_media_file_checks(tmp_path, monkeypatch):
    img = tmp_path / "a.jpg"
    img.write_bytes(b"x")
    assert check_media_file(str(img), "image") == img.resolve()
    with pytest.raises(ConfigError, match="not found"):
        check_media_file(str(tmp_path / "missing.jpg"), "image")
    with pytest.raises(ConfigError, match="extension"):
        check_media_file(str(img), "video")
    empty = tmp_path / "e.png"
    empty.write_bytes(b"")
    with pytest.raises(ConfigError, match="empty"):
        check_media_file(str(empty), "image")
    other = tmp_path / "sub"
    other.mkdir()
    monkeypatch.setenv("FB_MCP_ALLOWED_DIRS", str(other))
    with pytest.raises(ConfigError, match="ALLOWED_DIRS"):
        check_media_file(str(img), "image")


def test_redact():
    s = f"https://graph.facebook.com/v24.0/me?access_token={TOKEN}&x=1 OAuth {TOKEN} client_secret=abc {TOKEN}"
    out = redact(s)
    assert TOKEN not in out and "abc" not in out and "x=1" in out


# ----------------------------------------------------------------- request


def test_graph_error_is_raised_with_code(make_api):
    api, _ = make_api([graph_error(190, "Invalid OAuth access token")])
    with pytest.raises(GraphAPIError) as ei:
        api.get_page_info()
    assert ei.value.code == 190 and "Invalid OAuth" in str(ei.value)


def test_non_json_response(make_api):
    api, _ = make_api([R(None, status=502, text="<html>Bad gateway</html>")] * 3)
    with pytest.raises(GraphAPIError, match="Non-JSON"):
        api.get_page_info()


def test_get_retries_transient_then_succeeds(make_api):
    api, s = make_api([requests.ConnectionError("reset"), graph_error(2, status=500), R({"id": "123", "name": "P"})])
    assert api.get_page_info()["name"] == "P"
    assert len(s.calls) == 3
    assert all(c[2]["timeout"] for c in s.calls)


def test_get_does_not_retry_permanent_errors(make_api):
    api, s = make_api([graph_error(100, "bad field")])
    with pytest.raises(GraphAPIError):
        api.get_page_info()
    assert len(s.calls) == 1


def test_post_is_never_retried(make_api):
    api, s = make_api([requests.ConnectionError("reset")])
    with pytest.raises(GraphAPIError, match="Network error"):
        api.publish_post("hi")
    assert len(s.calls) == 1


def test_network_error_message_is_redacted(make_api):
    api, _ = make_api([requests.ConnectionError(f"failed url ...?access_token={TOKEN}")] * 3)
    with pytest.raises(GraphAPIError) as ei:
        api.get_page_info()
    assert TOKEN not in str(ei.value)


# ---------------------------------------------------------------- publish


def test_publish_text_and_link(make_api):
    api, s = make_api([R({"id": "123_9"})])
    out = api.publish_post("hello", link="https://example.com")
    assert out["post_id"] == "123_9" and out["type"] == "link"
    method, url, kw = s.calls[0]
    assert method == "POST" and url.endswith("/123/feed") and kw["data"]["link"] == "https://example.com"


def test_publish_scheduled_sets_published_false(make_api):
    ts = int(time.time()) + 7200
    api, s = make_api([R({"id": "123_9"})])
    api.publish_post("later", scheduled_publish_time=ts)
    data = s.calls[0][2]["data"]
    assert data["published"] == "false" and data["scheduled_publish_time"] == ts


def test_publish_photo_validation(make_api, tmp_path):
    api, s = make_api([])
    with pytest.raises(ConfigError):
        api.publish_post("x", photo_path="a.jpg", photo_url="https://x/y.jpg")
    with pytest.raises(ConfigError):
        api.publish_post("x", photo_path=str(tmp_path / "missing.jpg"))
    assert s.calls == []


def test_publish_photo_upload(make_api, tmp_path):
    img = tmp_path / "p.png"
    img.write_bytes(b"png")
    api, s = make_api([R({"id": "777", "post_id": "123_777"})])
    out = api.publish_post("cap", photo_path=str(img))
    assert out["post_id"] == "123_777" and out["type"] == "photo"
    assert s.calls[0][1].endswith("/123/photos") and "source" in s.calls[0][2]["files"]


def test_publish_without_id_fails(make_api):
    api, _ = make_api([R({"weird": True})])
    with pytest.raises(GraphAPIError, match="no id"):
        api.publish_post("x")


# ------------------------------------------------------------------ videos


@pytest.fixture
def video(tmp_path):
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"0" * 1000)
    return p


def test_reel_happy_path_streams_file(make_api, video):
    api, s = make_api([
        R({"video_id": "v1", "upload_url": "https://rupload.facebook.com/video-upload/v24.0/v1"}),
        R({"success": True}),
        R({"success": True}),
    ])
    out = api.publish_video(str(video), description="d")
    assert out["type"] == "reel" and out["video_id"] == "v1"
    transfer = s.calls[1]
    assert transfer[2]["data"].startswith("<stream")  # file object, not bytes in memory
    assert transfer[2]["headers"]["file_size"] == "1000"
    assert s.calls[2][2]["data"]["video_state"] == "PUBLISHED"


def test_reel_falls_back_only_when_start_rejected(make_api, video):
    api, s = make_api([graph_error(200, "Reels not available"), R({"id": "pv1"})])
    out = api.publish_video(str(video))
    assert out["type"] == "video" and "note" in out
    assert s.calls[1][1].endswith("/123/videos")


def test_reel_no_fallback_on_bad_token(make_api, video):
    api, s = make_api([graph_error(190, "expired")])
    with pytest.raises(GraphAPIError):
        api.publish_video(str(video))
    assert len(s.calls) == 1


def test_reel_no_fallback_after_transfer_failure(make_api, video):
    api, s = make_api([
        R({"video_id": "v1", "upload_url": "https://rupload.facebook.com/x"}),
        R({"success": False}),
    ])
    with pytest.raises(GraphAPIError, match="transfer"):
        api.publish_video(str(video))
    assert len(s.calls) == 2  # no second upload


def test_reel_finish_failure_raises(make_api, video):
    api, s = make_api([
        R({"video_id": "v1", "upload_url": "https://rupload.facebook.com/x"}),
        R({"success": True}),
        graph_error(100, "bad description"),
    ])
    with pytest.raises(GraphAPIError):
        api.publish_video(str(video))
    assert len(s.calls) == 3


def test_resumable_upload_chunks_and_finish(make_api, tmp_path, monkeypatch):
    import facebook_mcp.graph as g

    p = tmp_path / "big.mp4"
    p.write_bytes(b"1" * 250)
    monkeypatch.setattr(g, "RESUMABLE_THRESHOLD", 100)
    monkeypatch.setattr(g, "CHUNK_SIZE", 100)
    api, s = make_api([
        R({"upload_session_id": "s", "video_id": "v", "start_offset": "0", "end_offset": "100"}),
        R({"start_offset": "100", "end_offset": "200"}),
        R({"start_offset": "200", "end_offset": "250"}),
        R({"start_offset": "250", "end_offset": "250"}),
        R({"success": True}),
    ])
    out = api.publish_video(str(p), as_reel=False)
    assert out["video_id"] == "v"
    phases = [c[2]["data"]["upload_phase"] for c in s.calls]
    assert phases == ["start", "transfer", "transfer", "transfer", "finish"]


def test_resumable_upload_stall_raises(make_api, tmp_path, monkeypatch):
    import facebook_mcp.graph as g

    p = tmp_path / "big.mp4"
    p.write_bytes(b"1" * 250)
    monkeypatch.setattr(g, "RESUMABLE_THRESHOLD", 100)
    monkeypatch.setattr(g, "CHUNK_SIZE", 100)
    api, s = make_api([
        R({"upload_session_id": "s", "video_id": "v", "start_offset": "0"}),
        R({"start_offset": "0"}),
    ])
    with pytest.raises(GraphAPIError, match="stalled"):
        api.publish_video(str(p), as_reel=False)
    assert all(c[2]["data"]["upload_phase"] != "finish" for c in s.calls)


def test_threshold_constant_is_sane():
    assert RESUMABLE_THRESHOLD == 50 * 1024 * 1024


# ------------------------------------------------------------------- reads


def test_recent_posts_summary(make_api):
    api, _ = make_api([R({"data": [{
        "id": "123_1", "message": "m", "permalink_url": "u",
        "reactions": {"summary": {"total_count": 5}}, "comments": {"summary": {"total_count": 2}},
        "shares": {"count": 1},
    }, {"id": "123_2"}]})])
    posts = api.get_recent_posts(limit=500)
    assert posts[0]["stats"] == {"reactions": 5, "comments": 2, "shares": 1}
    assert posts[1]["stats"] == {"reactions": 0, "comments": 0, "shares": 0}


def test_recent_posts_limit_clamped(make_api):
    api, s = make_api([R({"data": []})])
    api.get_recent_posts(limit=500)
    assert s.calls[0][2]["params"]["limit"] == 100


def test_scheduled_posts_paginates_and_sorts(make_api):
    api, s = make_api([
        R({"data": [{"id": "b", "scheduled_publish_time": 200}], "paging": {"next": "https://graph.facebook.com/next"}}),
        R({"data": [{"id": "a", "scheduled_publish_time": 100}]}),
    ])
    out = api.get_scheduled_posts()
    assert [p["id"] for p in out] == ["a", "b"]
    assert s.calls[1][1] == "https://graph.facebook.com/next" and s.calls[1][2]["params"] is None


def test_page_insights_reports_bad_metrics(make_api):
    api, s = make_api([
        graph_error(100, "The value must be a valid insights metric"),
        R({"data": [{"name": "good", "period": "day", "values": [{"value": 3, "end_time": "t"}]}]}),
        graph_error(100, "The value must be a valid insights metric"),
    ])
    out = api.get_page_insights(metrics=["good", "page_impressions"])
    assert out["insights"]["good"]["latest_value"] == 3
    assert "page_impressions" in out["metric_errors"]


def test_page_insights_bad_period(make_api):
    api, _ = make_api([])
    with pytest.raises(ConfigError):
        api.get_page_insights(period="year")


def test_page_insights_auth_error_propagates(make_api):
    api, _ = make_api([graph_error(190, "expired")])
    with pytest.raises(GraphAPIError):
        api.get_page_insights()


def test_post_insights(make_api):
    api, _ = make_api([
        R({"id": "123_1", "message": "m", "reactions": {"summary": {"total_count": 4}}}),
        R({"data": [{"name": "post_clicks", "values": [{"value": 9}]}]}),
    ])
    out = api.get_post_insights("123_1")
    assert out["summary_stats"]["reactions"] == 4 and out["detailed_insights"]["post_clicks"] == 9
    assert out["metric_errors"] == {}


def test_reschedule_validates_and_posts(make_api):
    ts = int(time.time()) + 3600
    api, s = make_api([R({"success": True})])
    assert api.reschedule_post("p1", ts)["new_scheduled_time"] == ts
    with pytest.raises(ConfigError):
        api.reschedule_post("p1", 5)
    assert len(s.calls) == 1


def test_delete_error_propagates(make_api):
    api, _ = make_api([graph_error(100, "Unsupported delete request")])
    with pytest.raises(GraphAPIError):
        api.delete_post("x")


def test_delete_ok(make_api):
    api, s = make_api([R(True)])
    assert api.delete_post("123_1") == {"success": True, "deleted_post_id": "123_1"}
    assert s.calls[0][0] == "DELETE"


@pytest.mark.parametrize("err", [graph_error(4, "rate limited"), graph_error(1, "unknown", status=500),
                                 requests.ConnectionError("reset")])
def test_reel_no_fallback_on_transient_errors(make_api, video, err):
    api, s = make_api([err])
    with pytest.raises(GraphAPIError):
        api.publish_video(str(video))
    assert len(s.calls) == 1  # no regular-video upload


def test_reel_finish_has_no_title(make_api, video):
    api, s = make_api([
        R({"video_id": "v1", "upload_url": "https://rupload.facebook.com/x"}),
        R({"success": True}),
        R({"success": True}),
    ])
    api.publish_video(str(video), description="cap", title="ignored for reels")
    assert "title" not in s.calls[2][2]["data"] and s.calls[2][2]["data"]["description"] == "cap"
