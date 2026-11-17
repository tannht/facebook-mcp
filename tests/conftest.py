import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from facebook_mcp.graph import FacebookGraphAPI  # noqa: E402

ENV_VARS = [
    "FB_PAGE_ID", "FB_PAGE_ACCESS_TOKEN", "FB_USER_ACCESS_TOKEN", "FACEBOOK_PAGE_ID",
    "FACEBOOK_PAGE_ACCESS_TOKEN", "META_GRAPH_API_VERSION", "FB_MCP_ALLOWED_DIRS",
    "FB_MCP_READ_ONLY", "FB_MCP_ENV_FILE", "FB_MCP_SKIP_DOTENV", "FB_APP_ID", "FB_APP_SECRET",
]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class FakeResponse:
    def __init__(self, body=None, status=200, text=None):
        self._body = body
        self.status_code = status
        self.text = text if text is not None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


class FakeSession:
    """Replays queued responses (or exceptions) and records every call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        data = kwargs.get("data")
        if hasattr(data, "read"):
            kwargs = {**kwargs, "data": f"<stream {getattr(data, 'name', '')}>"}
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected call {method} {url}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.fixture
def make_api():
    def factory(responses, **kw):
        session = FakeSession(responses)
        api = FacebookGraphAPI(
            page_access_token=kw.pop("token", "EAATESTTOKEN1234567890abcdefXYZ"),
            default_page_id=kw.pop("page_id", "123"),
            session=session,
            **kw,
        )
        api._sleep = lambda s: None
        return api, session

    return factory
