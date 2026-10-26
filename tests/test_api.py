"""HTTP behavior, artifact validation, transfers, and deployment requests."""

import io
import json
from pathlib import Path
import sys
import tarfile
from unittest.mock import Mock

import pytest
import requests

import api
from api import MenderAPI, artifact_kind, device_name, device_updated, validate_artifact


@pytest.mark.parametrize(('attributes', 'expected'), [
    ([{'scope': 'tags', 'name': 'name', 'value': 'friendly'}], 'friendly'),
    ([{'scope': 'inventory', 'attribute': 'hostname', 'value': 'host'}], 'host'),
    ([{'scope': 'identity', 'name': 'name', 'value': 'identity'}], 'identity'),
    ([{'scope': 'identity', 'name': 'hostname', 'value': 'identity-host'}], 'identity-host'),
    ([{'scope': 'tags', 'name': 'name', 'value': ''}, {'value': 'fallback', 'timestamp': 'today'}], 'fallback'),
    ([{'value': 123, 'timestamp': 'today'}], 'device-abcdefghijkl'),
    ([], 'device-abcdefghijkl'),
])
def test_device_names(attributes, expected):
    assert device_name({'id': 'abcdefghijklmore', 'attributes': attributes}) == expected


@pytest.mark.parametrize(('attributes', 'expected'), [
    ([{'scope': 'system', 'name': 'updated_ts', 'value': 'today'}], 'today'),
    ([{'scope': 'identity', 'name': 'updated_ts'}], 'unknown'),
    ([{'timestamp': '2026-01'}, {'timestamp': '2026-10'}], '2026-10'),
    ([], 'unknown'),
])
def test_last_contact(attributes, expected):
    assert device_updated({'attributes': attributes}) == expected


@pytest.mark.parametrize(('value', 'expected'), [
    ({'type_info': {'type': 'rootfs-image'}}, 'rootfs-image'),
    ({'info': {'type_info': {'type': 'module'}}}, 'module'),
    ({'updates': [{'type_info': {'type': 'docker-compose'}}]}, 'docker-compose'),
    ({}, 'unknown'),
])
def test_artifact_types(value, expected):
    assert artifact_kind(value) == expected


def make_artifact(path, *, entry='version', body=b'{"format":"mender","version":3}'):
    with tarfile.open(path, 'w') as archive:
        info = tarfile.TarInfo(entry)
        info.size = len(body)
        archive.addfile(info, io.BytesIO(body))


def test_valid_artifact(tmp_path):
    path = tmp_path / 'valid.mender'
    make_artifact(path)
    validate_artifact(path)


@pytest.mark.parametrize(('entry', 'body'), [('wrong', b'{}'), ('version', b'{'), ('version', b'{"format":"other"}')])
def test_invalid_artifact_format(tmp_path, entry, body):
    path = tmp_path / 'invalid.mender'
    make_artifact(path, entry=entry, body=body)
    with pytest.raises(ValueError, match='invalid'):
        validate_artifact(path)


def test_unreadable_and_empty_archives(tmp_path):
    with pytest.raises(ValueError):
        validate_artifact(tmp_path / 'missing')
    path = tmp_path / 'empty.mender'
    with tarfile.open(path, 'w'):
        pass
    with pytest.raises(ValueError, match='version.*missing'):
        validate_artifact(path)


def test_directory_version_entry_is_rejected(tmp_path):
    path = tmp_path / 'directory.mender'
    with tarfile.open(path, 'w') as archive:
        info = tarfile.TarInfo('version')
        info.type = tarfile.DIRTYPE
        archive.addfile(info)
    with pytest.raises(ValueError, match='version entry'):
        validate_artifact(path)


def test_request_auth_tls_timeout_and_verbose(api_client, http_response, capsys):
    api_client.session.request.return_value = http_response({'ok': True})
    api_client.config.verbose = True
    api_client.verify = False
    api_client.request('GET', '/path', timeout=3)
    call = api_client.session.request.call_args
    assert call.args == ('GET', 'https://example.com/path')
    assert call.kwargs['timeout'] == 3 and not call.kwargs['verify']
    assert api_client.session.headers['Authorization'] == 'Bearer test-token'
    assert 'GET https://example.com/path' in capsys.readouterr().err
    api_client.request('GET', 'https://storage.example/file')
    assert api_client.session.request.call_args.args[1] == 'https://storage.example/file'


@pytest.mark.parametrize('status', [401, 403, 500])
def test_http_errors_are_closed_and_include_request_id(api_client, http_response, status):
    response = http_response(status=status, text='denied', headers={'X-MEN-RequestID': 'request-id'})
    response.close = Mock()
    api_client.session.request.return_value = response
    with pytest.raises(RuntimeError, match=f'HTTP {status}') as error:
        api_client.request('GET', '/path')
    assert 'request-id' in str(error.value)
    if status == 401:
        assert 'login' in str(error.value)
    response.close.assert_called_once()


@pytest.mark.parametrize('status', [200, 204])
def test_empty_json_response(api_client, http_response, status):
    api_client.session.request.return_value = http_response(status=status)
    assert api_client.json('DELETE', '/path', expected=(status,))[0] is None


def test_invalid_json_response(api_client, http_response):
    api_client.session.request.return_value = http_response(text='not json')
    with pytest.raises(RuntimeError, match='invalid JSON'):
        api_client.json('GET', '/path')


@pytest.mark.parametrize(('status', 'body', 'error'), [(401, 'wrong password', 'login failed'),
    (200, '   ', 'empty token'), (200, 'jwt-token', None)])
def test_login(api_client, http_response, monkeypatch, status, body, error):
    post = Mock(return_value=http_response(status=status, text=body))
    monkeypatch.setattr(api.requests, 'post', post)
    if error:
        with pytest.raises(RuntimeError, match=error):
            api_client.login('user', 'password', '123456')
    else:
        assert api_client.login('user', 'password', '123456') == b'jwt-token'
    assert post.call_args.kwargs['json'] == {'token2fa': '123456'}
    assert post.call_args.kwargs['auth'] == ('user', 'password')


@pytest.mark.parametrize(('page', 'size'), [(0, 1), (1, 0)])
def test_invalid_pagination(api_client, page, size):
    with pytest.raises(ValueError):
        api_client.list_page('/path', page, size)
    api_client.session.request.assert_not_called()


def test_inventory_post_pagination_and_accepted_sort(api_client, http_response):
    data = [{'id': 'z'}, {'id': 'a'}]
    api_client.session.request.return_value = http_response(data, headers={'X-Total-Count': '2'})
    assert [d['id'] for d in api_client.devices()] == ['a', 'z']
    call = api_client.session.request.call_args
    assert call.args[0] == 'POST' and call.kwargs['params'] is None
    assert call.kwargs['json']['filters'][0]['value'] == 'accepted'
    assert call.kwargs['json']['per_page'] == 100


@pytest.mark.parametrize('connected', [True, False])
def test_connectivity_and_name_fallback(api_client, http_response, connected):
    api_client.session.request.return_value = http_response({'status': 'CONNECTED' if connected else 'disconnected'})
    assert api_client.connected('id') is connected
    if connected:
        api_client.require_connected('id')
    else:
        with pytest.raises(RuntimeError, match='not connected'):
            api_client.require_connected('id')
    api_client.session.request.side_effect = RuntimeError('not found')
    assert api_client.display_name('id') == 'id'


def test_display_name_and_artifact_listing(api_client, http_response):
    api_client.session.request.side_effect = [http_response({'attributes': [{'scope':'tags','name':'name','value':'bench'}]}),
                                            http_response([{'id': 'artifact'}])]
    assert api_client.display_name('id') == 'bench'
    assert api_client.artifacts() == [{'id': 'artifact'}]


@pytest.mark.parametrize('ids', [['one'], ['one', 'two']])
def test_deployment_creation_and_default_name(api_client, http_response, ids):
    api_client.session.request.return_value = http_response(status=201)
    api_client.deploy('release', ids)
    body = api_client.session.request.call_args.kwargs['json']
    assert body['devices'] == ids and body['artifact_name'] == 'release'
    assert ('one' if len(ids) == 1 else '2 devices') in body['name']
    with pytest.raises(ValueError):
        api_client.deploy('release', [])


def test_details_cancellation_and_artifact_deletion(api_client, http_response):
    api_client.session.request.side_effect = [http_response({'id': 'deployment'}), http_response({'success': 1}),
                                            http_response(status=204), http_response(status=204)]
    assert api_client.deployment_details('id') == ({'id': 'deployment'}, {'success': 1})
    api_client.cancel_deployment('id')
    assert api_client.session.request.call_args.kwargs['json'] == {'status': 'aborted'}
    api_client.delete_artifact('artifact')
    assert api_client.session.request.call_args.args[0] == 'DELETE'


@pytest.mark.parametrize('progress', [False, True])
def test_multipart_streaming_consumes_file_and_reports_progress(api_client, http_response, tmp_path, capsys, progress):
    path = tmp_path / 'large.bin'
    path.write_bytes(b'x' * (300 * 1024))
    consumed = bytearray()
    def upload(*args, **kwargs):
        stream = kwargs['data']
        while chunk := stream.read(32 * 1024):
            consumed.extend(chunk)
        return http_response(status=201)
    api_client.session.request.side_effect = upload
    api_client._multipart('/upload', 'POST', path, 'file', {'path': '/remote'}, progress)
    assert path.read_bytes() in consumed
    assert b'name="path"' in consumed and b'/remote' in consumed
    assert ('Uploading' in capsys.readouterr().err) is progress


def test_artifact_upload_validates_before_request(api_client, http_response, tmp_path):
    path = tmp_path / 'release.mender'
    make_artifact(path)
    api_client.session.request.return_value = http_response(status=201)
    api_client.upload_artifact(path, 'description', False)
    assert api_client.session.request.call_args.kwargs['headers']['Content-Type'].startswith('multipart/form-data')


def test_missing_upload_dependency_is_actionable(api_client, monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, 'requests_toolbelt', None)
    with pytest.raises(RuntimeError, match='requests-toolbelt'):
        api_client._multipart('/upload', 'POST', tmp_path / 'file', 'file', {}, False)


@pytest.mark.parametrize('authenticated', [False, True])
def test_download_authentication_is_not_leaked_to_storage(api_client, http_response, monkeypatch, tmp_path, authenticated):
    response = http_response()
    response.iter_content = Mock(return_value=iter([b'content']))
    response.close = Mock()
    get = Mock(return_value=response)
    if authenticated:
        monkeypatch.setattr(api_client.session, 'get', get)
    else:
        monkeypatch.setattr(api.requests, 'get', get)
    destination = tmp_path / 'file'
    assert api_client._download('https://storage.example/file', destination, 7, False, authenticated) == destination
    assert destination.read_bytes() == b'content'
    assert ('headers' in get.call_args.kwargs) is authenticated
    response.close.assert_called_once()


def test_download_error_closes_response(api_client, http_response, monkeypatch, tmp_path):
    value = http_response(status=403)
    value.close = Mock()
    monkeypatch.setattr(api_client.session, 'get', Mock(return_value=value))
    with pytest.raises(RuntimeError, match='HTTP 403'):
        api_client._download('https://storage.example/file', tmp_path / 'file', 0, False)
    value.close.assert_called_once()


def test_artifact_download_resolves_link_and_filename(api_client, http_response, monkeypatch, tmp_path):
    api_client.session.request.side_effect = [http_response({'uri': '/signed/file'}),
                                            http_response({'name': 'release', 'size': 7})]
    download = Mock(return_value=tmp_path / 'release.mender')
    monkeypatch.setattr(api_client, '_download', download)
    assert api_client.download_artifact('artifact', tmp_path) == tmp_path / 'release.mender'
    download.assert_called_once_with('https://example.com/signed/file', tmp_path / 'release.mender',
                                     7, True, authenticated=False)


@pytest.mark.parametrize('progress', [False, True])
def test_atomic_stream_download_and_mismatch_cleanup(http_response, tmp_path, capsys, progress):
    destination = tmp_path / 'nested/file'
    value = http_response()
    payload = b'x' * (1024 * 1024)
    value.iter_content = Mock(return_value=iter([b'', payload]))
    assert MenderAPI._stream_download(value, destination, 0, progress) == destination
    assert destination.read_bytes() == payload
    assert ('Downloading' in capsys.readouterr().err) is progress
    value.iter_content = Mock(return_value=iter([b'partial']))
    with pytest.raises(RuntimeError, match='size mismatch'):
        MenderAPI._stream_download(value, destination, 999, progress)
    assert destination.read_bytes() == payload
    assert list(destination.parent.iterdir()) == [destination]


def test_device_file_transfer(api_client, http_response, monkeypatch, tmp_path):
    local = tmp_path / 'file'
    local.write_bytes(b'payload')
    upload = Mock()
    monkeypatch.setattr(api_client, 'require_connected', Mock())
    monkeypatch.setattr(api_client, '_multipart', upload)
    api_client.upload_file('device/id', local, '/etc/file')
    assert upload.call_args.args[4]['path'] == '/etc/file'
    value = http_response(headers={'X-MEN-FILE-SIZE': '7'})
    value.iter_content = Mock(return_value=iter([b'payload']))
    api_client.session.request.return_value = value
    assert api_client.download_file('device/id', '/etc/file', tmp_path) == local
    assert local.read_bytes() == b'payload'
