"""agent/__init__._load_dotenv: .env loading rules."""
import os
from unittest import mock

import pytest

import agent as agent_init




def _run_load(monkeypatch, content, existing=None):
    """Write `content` to a fake .env and run _load_dotenv on it."""
    existing = existing or {}
    for k in list(existing):
        monkeypatch.setenv(k, existing[k])
    fake_path = os.path.join("fake", "path", ".env")

    def fake_exists(path):
        return path == fake_path

    with mock.patch.object(agent_init, "_BASE_DIR",
                           os.path.dirname(fake_path)), \
            mock.patch.object(os.path, "exists", fake_exists), \
            mock.patch("builtins.open",
                       mock.mock_open(read_data=content)) as m:
        agent_init._load_dotenv()
    return m


def test_missing_env_file_does_nothing(monkeypatch):
    with mock.patch.object(os.path, "exists", return_value=False):
        # Should return silently without touching env.
        agent_init._load_dotenv()


@pytest.mark.parametrize("content", [
    "KEY=value\n",
    "KEY=\"value\"\n",
    "KEY='value'\n",
    "  KEY  =  value  \n",
])
def test_loads_keys_without_quotes(content):
    with mock.patch.object(agent_init, "_BASE_DIR", "/fake/base"), \
            mock.patch.object(os.path, "exists", return_value=True), \
            mock.patch("builtins.open",
                       mock.mock_open(read_data=content)):
        agent_init._load_dotenv()
    assert os.environ.get("KEY") == "value"


def test_skips_comments_empty_and_no_equals():
    content = "# comment\n\nKEY=value\ninvalid line without equals\n"
    with mock.patch.object(agent_init, "_BASE_DIR", "/fake/base"), \
            mock.patch.object(os.path, "exists", return_value=True), \
            mock.patch("builtins.open",
                       mock.mock_open(read_data=content)) as m:
        agent_init._load_dotenv()
    assert os.environ.get("KEY") == "value"
    assert os.environ.get("invalid line without equals") is None


def test_empty_key_ignored():
    content = " =value\n"
    with mock.patch.object(agent_init, "_BASE_DIR", "/fake/base"), \
            mock.patch.object(os.path, "exists", return_value=True), \
            mock.patch("builtins.open",
                       mock.mock_open(read_data=content)) as m:
        agent_init._load_dotenv()
    assert os.environ.get("") is None


def test_existing_env_wins_over_dotenv(monkeypatch):
    """A real environment variable must never be overwritten by .env."""
    monkeypatch.setenv("KEY", "from-shell")
    content = "KEY=from-dotenv\n"
    with mock.patch.object(agent_init, "_BASE_DIR", "/fake/base"), \
            mock.patch.object(os.path, "exists", return_value=True), \
            mock.patch("builtins.open",
                       mock.mock_open(read_data=content)) as m:
        agent_init._load_dotenv()
    assert os.environ["KEY"] == "from-shell"


def test_partition_handles_multiple_equals():
    content = "URL=https://x.com/a=b?c=d\n"
    with mock.patch.object(agent_init, "_BASE_DIR", "/fake/base"), \
            mock.patch.object(os.path, "exists", return_value=True), \
            mock.patch("builtins.open",
                       mock.mock_open(read_data=content)) as m:
        agent_init._load_dotenv()
    assert os.environ["URL"] == "https://x.com/a=b?c=d"
