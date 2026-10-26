"""Configuration precedence, cross-platform paths, and durable private storage."""

import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

import config
from config import Config, Favorites

ORIGINAL_CANDIDATES = config.config_candidates


def resolve(**values):
    return Config.resolve(**dict(server=None, token_value=None, token_path=None,
                                 skip_verify=False, verbose=False) | values)


@pytest.mark.parametrize(('value', 'expected'), [
    ('example.com/', 'https://example.com'),
    ('http://example.com/base///#fragment', 'http://example.com/base'),
    ('https://example.com/base?query=yes#fragment', 'https://example.com/base?query=yes'),
])
def test_normalize_server(value, expected):
    assert config.normalize_server(value) == expected


@pytest.mark.parametrize('value', ['ftp://example.com', 'https://', 'http:///path'])
def test_invalid_server(value):
    with pytest.raises(ValueError, match='HTTP or HTTPS'):
        config.normalize_server(value)


@pytest.mark.parametrize(('values', 'expected'), [((None, '', 'yes', 'later'), 'yes'), ((None, ''), None)])
def test_first_nonempty(values, expected):
    assert config._first(*values) == expected


def test_resolution_precedence_and_first_file_wins(monkeypatch, tmp_path):
    first, second = tmp_path / 'first.json', tmp_path / 'second.json'
    first.write_text(json.dumps({'server': 'file.example', 'token_value': 'file-token',
                                 'username': 'file-user', 'password': 'file-password'}))
    second.write_text('{invalid json')
    monkeypatch.setattr(config, 'config_candidates', lambda: [tmp_path / 'missing', first, second])
    values = resolve()
    assert (values.server, values.token, values.username, values.password) == (
        'https://file.example', 'file-token', 'file-user', 'file-password')
    monkeypatch.setenv('MENDER_SERVER', 'env.example')
    monkeypatch.setenv('MENDER_TOKEN', 'env-token')
    monkeypatch.setenv('MENDER_USERNAME', 'env-user')
    monkeypatch.setenv('MENDER_PASSWORD', 'env-password')
    values = resolve(server='explicit.example', token_value='explicit-token', skip_verify=True, verbose=True)
    assert values.server == 'https://explicit.example'
    assert values.token == 'explicit-token'
    assert (values.username, values.password) == ('env-user', 'env-password')
    assert values.skip_verify and values.verbose
    assert resolve().server == 'https://env.example'
    assert resolve().token == 'env-token'


def test_defaults_and_saved_token(monkeypatch, tmp_path):
    values = resolve()
    assert values.server == config.DEFAULT_SERVER and values.token is None
    path = tmp_path / 'token'
    path.write_text(' saved-token \n')
    assert resolve(token_path=path).token == 'saved-token'
    monkeypatch.setattr(config, 'default_token_path', lambda: path)
    assert resolve().token == 'saved-token'


@pytest.mark.parametrize('field', ['token_value', 'token-value'])
def test_configuration_token_aliases(monkeypatch, tmp_path, field):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps({field: 'configured'}))
    monkeypatch.setattr(config, 'config_candidates', lambda: [path])
    assert resolve().token == 'configured'


def test_invalid_configuration_reports_path(monkeypatch, tmp_path):
    path = tmp_path / 'broken.json'
    path.write_text('{')
    monkeypatch.setattr(config, 'config_candidates', lambda: [path])
    with pytest.raises(ValueError, match='invalid configuration') as error:
        resolve()
    assert str(path) in str(error.value)


@pytest.mark.parametrize('source', ['explicit', 'environment'])
def test_token_file_conflicts(monkeypatch, tmp_path, source):
    if source == 'environment':
        monkeypatch.setenv('MENDER_TOKEN', 'token')
    with pytest.raises(ValueError, match='cannot be used together'):
        resolve(token_path=tmp_path / 'token', token_value='token' if source == 'explicit' else None)


@pytest.mark.parametrize('platform', ['linux', 'win32'])
def test_platform_paths(monkeypatch, tmp_path, platform):
    monkeypatch.setattr(config.sys, 'platform', platform)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.delenv('XDG_CACHE_HOME')
    monkeypatch.delenv('XDG_CONFIG_HOME')
    monkeypatch.delenv('LOCALAPPDATA', raising=False)
    monkeypatch.delenv('APPDATA', raising=False)
    monkeypatch.delenv('PROGRAMDATA', raising=False)
    if platform == 'win32':
        assert config.default_token_path() == tmp_path / 'AppData/Local/mender/authtoken'
        assert config.favorite_path() == tmp_path / 'AppData/Roaming/mender-richer-cli/favorites.json'
        monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
        monkeypatch.setenv('APPDATA', str(tmp_path / 'roaming'))
        monkeypatch.setenv('PROGRAMDATA', str(tmp_path / 'system'))
        assert config.default_token_path() == tmp_path / 'local/mender/authtoken'
        assert config.favorite_path() == tmp_path / 'roaming/mender-richer-cli/favorites.json'
        assert ORIGINAL_CANDIDATES()[-1] == tmp_path / 'system/mender-richer-cli/.mender-richer-clirc'
    else:
        assert config.default_token_path() == tmp_path / '.cache/mender/authtoken'
        assert config.favorite_path() == tmp_path / '.config/mender-richer-cli/favorites.json'
        assert ORIGINAL_CANDIDATES()[-1] == Path('/etc/mender/.mender-clirc')
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'override-cache'))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(tmp_path / 'override-config'))
    assert config.default_token_path() == tmp_path / 'override-cache/mender/authtoken'
    assert config.favorite_path() == tmp_path / 'override-config/mender-richer-cli/favorites.json'


@pytest.mark.parametrize('windows', [False, True])
def test_storage_round_trip_and_private_modes(monkeypatch, tmp_path, windows):
    if windows:
        monkeypatch.setattr(config.sys, 'platform', 'win32')
        monkeypatch.setattr(config.os, 'fchmod', Mock(side_effect=AssertionError('POSIX call')), raising=False)
    path = tmp_path / 'private/token'
    settings = Config('https://example.com', None, path, None, None)
    settings.save_token(b'first')
    settings.save_token(b'second')
    assert path.read_bytes() == b'second'
    favorites = Favorites(path.parent / 'favorites.json')
    assert favorites.get('server') is None
    assert favorites.toggle('server', {'id': 'one', 'name': 'One'})
    assert favorites.toggle('other', {'id': 'two', 'name': 'Two'})
    assert Favorites(favorites.path).get('server')['id'] == 'one'
    assert not favorites.toggle('server', {'id': 'one', 'name': 'One'})
    assert Favorites(favorites.path).get('other')['id'] == 'two'
    assert sorted(p.name for p in path.parent.iterdir()) == ['favorites.json', 'token']
    if os.name != 'nt' and not windows:
        assert path.stat().st_mode & 0o777 == 0o600
        assert favorites.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('kind', ['token', 'favorites'])
def test_failed_replace_preserves_old_file_and_cleans_temporary(monkeypatch, tmp_path, kind):
    path = tmp_path / kind
    if kind == 'token':
        path.write_bytes(b'old-token')
        operation = lambda: Config('server', None, path, None, None).save_token(b'new-token')
    else:
        path.write_text('{"servers": {}}')
        operation = lambda: Favorites(path).toggle('server', {'id': 'id', 'name': 'name'})
    original = path.read_bytes()
    monkeypatch.setattr(config.os, 'replace', Mock(side_effect=OSError('disk failure')))
    with pytest.raises(OSError, match='disk failure'):
        operation()
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_invalid_favorites_are_not_silently_overwritten(tmp_path):
    path = tmp_path / 'favorites.json'
    path.write_text('{')
    with pytest.raises(json.JSONDecodeError):
        Favorites(path)
    assert path.read_text() == '{'
