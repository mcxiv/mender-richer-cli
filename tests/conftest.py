"""Shared fixtures isolate credentials, files, and network access from real Mender tenants."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

import config
from api import MenderAPI


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch, tmp_path):
    """Keep every test away from real credentials, configuration, and live HTTP endpoints."""
    for name in list(__import__('os').environ):
        if name.startswith('MENDER_'):
            monkeypatch.delenv(name)
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'cache'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'config'))
    monkeypatch.setattr(config, 'config_candidates', lambda: [tmp_path / '.mender-richer-clirc'])
    monkeypatch.setattr(requests.sessions.Session, 'request', Mock(side_effect=AssertionError('unexpected HTTP request')))


@pytest.fixture
def api_client():
    """Return a real API client whose HTTP transport can be scripted per test."""
    settings = SimpleNamespace(server='https://example.com', token='test-token',
                               skip_verify=False, verbose=False)
    client = MenderAPI(settings)
    client.session.request = Mock()
    return client


@pytest.fixture
def http_response():
    """Build consumed HTTP responses without opening sockets."""
    import json
    def make(value=None, *, status=200, text=None, headers=None):
        result = requests.Response()
        result.status_code = status
        result.headers.update(headers or {})
        result.encoding = 'utf-8'
        result._content = (text.encode() if text is not None else
                           json.dumps(value).encode() if value is not None else b'')
        result._content_consumed = True
        return result
    return make


@pytest.fixture
def cli_app(monkeypatch):
    """Provide an authenticated CLI context with mocked management operations."""
    import main
    client = Mock()
    client.display_name.return_value = 'device'
    settings = SimpleNamespace(server='https://example.com', token='test-token',
                               username=None, password=None, token_path=Path('token'))
    monkeypatch.setattr(main.Config, 'resolve', Mock(return_value=settings))
    monkeypatch.setattr(main, 'MenderAPI', Mock(return_value=client))
    return SimpleNamespace(api=client, config=settings)
