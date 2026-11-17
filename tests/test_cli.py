import io
import logging
import os
import stat

import pytest

from facebook_mcp import check, config, token_tool
from facebook_mcp.graph import FacebookGraphAPI


def test_update_env_file_keeps_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nFB_PAGE_ID=old\nOTHER=1")
    token_tool.update_env_file(env, {"FB_PAGE_ID": "new", "FB_PAGE_ACCESS_TOKEN": "tok"})
    text = env.read_text()
    assert "# comment\n" in text and "OTHER=1\n" in text
    assert "FB_PAGE_ID=new\n" in text and "FB_PAGE_ACCESS_TOKEN=tok\n" in text and "old" not in text
    assert stat.S_IMODE(os.stat(env).st_mode) == 0o600


def test_check_fails_cleanly_without_config(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FB_MCP_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setattr("facebook_mcp.config.source_root", lambda: None)
    assert check.main([]) == 1
    assert "FB_PAGE_ID" in capsys.readouterr().out


def test_token_tool_requires_user_token(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FB_MCP_ENV_FILE", str(tmp_path / "none.env"))
    monkeypatch.setattr("facebook_mcp.config.source_root", lambda: None)
    assert token_tool.main([]) == 1


PAGES = [
    {"id": "111", "name": "First", "access_token": "PAGETOKEN111", "followers_count": 5},
    {"id": "222", "name": "Second", "access_token": "PAGETOKEN222", "fan_count": 7},
]


@pytest.fixture
def fake_graph(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("facebook_mcp.config.source_root", lambda: None)
    monkeypatch.setenv("FB_MCP_SKIP_DOTENV", "1")
    calls = {}

    def exchange(self, user_token, app_id, app_secret):
        calls["exchange"] = (user_token, app_id, app_secret)
        return {"access_token": "LONGUSER", "expires_in": 5184000}

    def pages(self, user_access_token=None, limit=100):
        calls["pages_token"] = user_access_token
        return [dict(p) for p in calls.get("pages", PAGES)]

    monkeypatch.setattr(FacebookGraphAPI, "exchange_long_lived_user_token", exchange)
    monkeypatch.setattr(FacebookGraphAPI, "get_user_pages", pages)
    monkeypatch.setattr(FacebookGraphAPI, "debug_token", lambda self, t, app_token=None: {"scopes": ["read_insights"]})
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # non-interactive
    return calls


def test_token_tool_auto_save_selected_page(fake_graph, tmp_path, capsys):
    env = tmp_path / "out.env"
    rc = token_tool.main(["--user-token", "SHORT", "--app-id", "1", "--app-secret", "s",
                          "--page-id", "222", "--env-file", str(env), "--auto-save"])
    assert rc == 0
    assert fake_graph["exchange"] == ("SHORT", "1", "s") and fake_graph["pages_token"] == "LONGUSER"
    text = env.read_text()
    assert "FB_PAGE_ID=222\n" in text and "FB_PAGE_ACCESS_TOKEN=PAGETOKEN222\n" in text
    out = capsys.readouterr().out
    assert "PAGETOKEN" not in out  # tokens are saved, never printed


def test_token_tool_single_page_auto_selected(fake_graph, tmp_path):
    fake_graph["pages"] = PAGES[:1]
    env = tmp_path / "out.env"
    assert token_tool.main(["--user-token", "U", "--app-id", "1", "--app-secret", "s",
                            "--env-file", str(env), "--auto-save"]) == 0
    assert "FB_PAGE_ID=111" in env.read_text()


def test_token_tool_several_pages_non_tty_needs_page_id(fake_graph, tmp_path):
    env = tmp_path / "out.env"
    assert token_tool.main(["--user-token", "U", "--app-id", "1", "--app-secret", "s",
                            "--env-file", str(env), "--auto-save"]) == 1
    assert not env.exists()


def test_token_tool_non_tty_without_auto_save_does_not_write(fake_graph, tmp_path):
    env = tmp_path / "out.env"
    assert token_tool.main(["--user-token", "U", "--app-id", "1", "--app-secret", "s",
                            "--page-id", "111", "--env-file", str(env)]) == 1
    assert not env.exists()


def test_token_tool_print_token(fake_graph, capsys):
    assert token_tool.main(["--user-token", "U", "--app-id", "1", "--app-secret", "s",
                            "--page-id", "111", "--print-token"]) == 0
    assert "FB_PAGE_ACCESS_TOKEN=PAGETOKEN111" in capsys.readouterr().out


def test_setup_logging_level_and_urllib3(monkeypatch):
    monkeypatch.setenv("FB_MCP_LOG_LEVEL", "DEBUG")
    logging.getLogger("urllib3").setLevel(logging.DEBUG)
    config.setup_logging()
    config.setup_logging()  # idempotent: one handler
    pkg = logging.getLogger("facebook_mcp")
    assert pkg.level == logging.DEBUG
    assert sum(getattr(h, "_facebook_mcp", False) for h in pkg.handlers) == 1
    assert logging.getLogger("urllib3").level == logging.WARNING
