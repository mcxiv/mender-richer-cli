"""Exercise the local web interface through real loopback HTTP requests."""

import http.client
import json
import socket
from threading import Thread
from unittest.mock import Mock

import pytest
from click.testing import CliRunner

import main
import webui

pytestmark = pytest.mark.integration


@pytest.fixture
def web(cli_app):
    api = cli_app.api
    api.config = cli_app.config
    api.devices.return_value = [{'id': 'one', 'attributes': [
        {'scope': 'tags', 'name': 'name', 'value': '<Bench>'}]}]
    api.inventory.return_value = {'id': 'one', 'attributes': []}
    api.tags.return_value = [{'name': 'site', 'value': 'Paris'}]
    api.device_group.return_value = 'staging'
    api.device_deployments.return_value = []
    api.groups.return_value = ['staging']
    api.artifacts.return_value = [{'name': 'release', 'type_info': {'type': 'rootfs-image'}}]
    api.deployment_log.return_value = 'device log'
    with webui.WebServer(api, 0) as server:
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        def request(path, body=None, *, method=None, headers=None, session=True):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            supplied = {'X-MRC-Session': server.session_token} if session else {}
            if body is not None:
                body = json.dumps(body) if not isinstance(body, str) else body
                supplied['Content-Type'] = 'application/json'
            supplied.update(headers or {})
            connection.request(method or ('POST' if body is not None else 'GET'), path, body, supplied)
            response = connection.getresponse()
            result = response.status, dict(response.getheaders()), response.read()
            connection.close()
            return result
        yield api, server, request
        server.shutdown()
        worker.join(timeout=5)


def test_page_assets_and_session_do_not_expose_mender_token(web):
    api, server, request = web
    for path in ['/', '/app.js', '/style.css']:
        status, headers, body = request(path, session=False)
        assert status == 200
        assert b'test-token' not in body
        assert headers['Cache-Control'] == 'no-store'
        assert "frame-ancestors 'none'" in headers['Content-Security-Policy']
    assert server.session_token.encode() in request('/', session=False)[2]
    assert request('/../../config.py', session=False)[0] == 404
    api.devices.assert_not_called()


@pytest.mark.parametrize('headers,session', [({}, False), ({'X-MRC-Session': 'wrong'}, True),
    ({'Host': 'attacker.test'}, True), ({'Origin': 'https://attacker.test'}, True)])
def test_foreign_requests_cannot_reach_mender(web, headers, session):
    api, _, request = web
    assert request('/api/devices', headers=headers, session=session)[0] == 403
    assert request('/api/action', {'action': 'rename', 'device_id': 'one', 'name': 'New'},
                   headers=headers, session=session)[0] == 403
    api.devices.assert_not_called()
    api.rename_device.assert_not_called()


def test_devices_details_options_and_logs(web):
    api, _, request = web
    devices = json.loads(request('/api/devices')[2])
    assert devices['devices'][0]['name'] == '<Bench>'
    assert devices['server'] == 'https://example.com'
    details = json.loads(request('/api/device?id=one')[2])
    assert details['tags'] == [{'name': 'site', 'value': 'Paris'}]
    assert details['group'] == 'staging'
    options = json.loads(request('/api/options')[2])
    assert options['groups'] == ['staging']
    assert options['artifacts'][0]['name'] == 'release'
    assert json.loads(request('/api/log?device_id=one&deployment_id=d')[2]) == {'text': 'device log'}
    api.deployment_log.assert_called_once_with('d', 'one')


@pytest.mark.parametrize('body,method,args', [
    ({'action': 'rename', 'name': 'New'}, 'rename_device', ('one', 'New')),
    ({'action': 'tag-set', 'name': 'site', 'value': 'Lyon'}, 'set_tags', ('one', {'site': 'Lyon'})),
    ({'action': 'tag-remove', 'name': 'site'}, 'remove_tag', ('one', 'site')),
    ({'action': 'group', 'name': 'new'}, 'assign_group', ('one', 'new')),
    ({'action': 'group', 'name': ''}, 'remove_group_devices', ('staging', ['one'])),
    ({'action': 'deploy', 'artifact': 'release'}, 'deploy', ('release', ['one'])),
])
def test_mutations_reuse_existing_api(web, body, method, args):
    api, server, request = web
    status, _, data = request('/api/action', {**body, 'device_id': 'one'},
                             headers={'Origin': server.origin})
    assert status == 200 and json.loads(data)['message']
    getattr(api, method).assert_called_once_with(*args)


@pytest.mark.parametrize('body', ['{', '[]', '{}', {'action': 'unknown', 'device_id': 'one'},
    {'action': 'rename', 'device_id': 'one', 'name': []},
    {'action': 'tag-set', 'device_id': 'one', 'name': 'site', 'value': None},
    {'action': 'rename', 'device_id': '', 'name': 'New'}])
def test_invalid_mutations_return_validation_errors(web, body):
    api, _, request = web
    assert request('/api/action', body)[0] == 400
    api.rename_device.assert_not_called()
    api.set_tags.assert_not_called()


def test_request_limits_and_failures(web):
    api, _, request = web
    assert request('/api/action', 'x' * 65537)[0] == 413
    assert request('/api/action', '{}', headers={'Content-Type': 'text/plain'})[0] == 415
    assert request('/api/device')[0] == 400
    assert request('/api/unknown')[0] == 404
    api.devices.side_effect = RuntimeError('Mender is unavailable')
    status, _, body = request('/api/devices')
    assert status == 502 and json.loads(body)['error'] == 'Mender is unavailable'


def test_browser_idle_connection_does_not_block_requests(web):
    _, server, request = web
    with socket.create_connection(server.server_address, timeout=2):
        assert request('/api/devices')[0] == 200


def test_server_credentials_are_not_exposed_in_status_or_errors(web):
    api, _, request = web
    api.config.server = 'https://user:password@example.com'
    result = json.loads(request('/api/devices')[2])
    assert result['server'] == 'https://example.com'
    api.devices.side_effect = RuntimeError('Failed https://user:password@example.com: Bearer test-token')
    body = request('/api/devices')[2]
    assert b'user:password' not in body and b'test-token' not in body


def test_web_command_reuses_authenticated_context(cli_app, monkeypatch):
    launch = Mock()
    monkeypatch.setattr(webui, 'serve', launch)
    result = CliRunner().invoke(main.cli, ['web', '--port', '9000'])
    assert result.exit_code == 0, result.output
    launch.assert_called_once_with(cli_app.api, 9000)
    assert CliRunner().invoke(main.cli, ['web', '--port', '0']).exit_code != 0


def test_serve_closes_client_on_shutdown(cli_app, monkeypatch, capsys):
    server = Mock()
    server.origin = 'http://127.0.0.1:8765'
    server.serve_forever.side_effect = KeyboardInterrupt()
    context = Mock()
    context.__enter__ = Mock(return_value=server)
    context.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(webui, 'WebServer', Mock(return_value=context))
    webui.serve(cli_app.api)
    cli_app.api.session.close.assert_called_once()
    assert 'http://127.0.0.1:8765' in capsys.readouterr().out


def test_serve_closes_client_if_port_unavailable(cli_app, monkeypatch):
    monkeypatch.setattr(webui, 'WebServer', Mock(side_effect=OSError('port in use')))
    with pytest.raises(OSError, match='port in use'):
        webui.serve(cli_app.api)
    cli_app.api.session.close.assert_called_once()
