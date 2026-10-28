"""Loopback-only device controls sharing the CLI's authenticated Mender client."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
import json
import secrets
from threading import Lock
from urllib.parse import parse_qs, urlsplit

import requests

from api import MenderAPI, artifact_kind, device_name, device_updated
from display import flatten_inventory
import remote
from .sessions import Forward, Shell


def _string(data: dict, key: str, *, empty: bool = False) -> str:
    value = data.get(key)
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f'{key} must be a {"nonempty " if not empty else ""}string')
    return value


def _device_artifact(device: dict) -> str:
    for attribute in device.get('attributes', []):
        if attribute.get('scope') == 'inventory' and attribute.get('name', attribute.get('attribute')) == 'artifact_name':
            return str(attribute.get('value') or '')
    return ''


def _deployment_page(api: MenderAPI, status: str, offset: int, limit: int) -> tuple[list[dict], int | None, bool]:
    if status == 'finished':
        rows, total = api.deployments(offset // limit + 1, limit, status)
        return rows, total, offset + len(rows) < total if total is not None else len(rows) == limit

    rows = []
    totals = []
    target = offset + limit + 1
    for status_filter in ('scheduled', 'pending', 'inprogress'):
        records = []
        page = 1
        total = None
        while len(records) < target:
            batch, total = api.deployments(page, limit, status_filter)
            records.extend(batch)
            if (not batch or (total is not None and page * limit >= total) or len(batch) < limit):
                break
            page += 1
        rows.extend(records[:target])
        totals.append(total)
    rows.sort(key=lambda item: str(item.get('created', '')), reverse=True)
    page_rows = rows[offset:offset + limit]
    total = sum(totals) if all(value is not None for value in totals) else None
    has_more = offset + len(page_rows) < total if total is not None else len(rows) > offset + limit
    return page_rows, total, has_more


class WebServer(ThreadingHTTPServer):
    """Serve one local browser session without exposing Mender credentials."""

    def __init__(self, api: MenderAPI, port: int = 8765):
        self.api = api
        self.api_lock = Lock()
        self.session_token = secrets.token_urlsafe(32)
        self.session_lock = Lock()
        self.terminals: dict[str, Shell] = {}
        self.forwards: dict[str, Forward] = {}
        super().__init__(('127.0.0.1', port), Handler)
        self.origin = f'http://127.0.0.1:{self.server_port}'


class Handler(BaseHTTPRequestHandler):
    """Handle only known local assets and authenticated device operations."""

    def _public_server(self) -> str:
        parts = urlsplit(self.server.api.config.server)
        return parts._replace(netloc=parts.netloc.rsplit('@', 1)[-1], query='', fragment='').geturl()

    def _public_error(self, error: Exception) -> str:
        config = self.server.api.config
        message = str(error).replace(config.server, self._public_server())
        return message.replace(config.token, '[redacted]') if config.token else message

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass

    def _reply(self, status: int, value, content_type: str = 'application/json'):
        body = json.dumps(value, ensure_ascii=False).encode() if content_type == 'application/json' else value
        self.send_response(status)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _allowed(self, api: bool) -> bool:
        allowed_hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        origins = {f'http://{host}' for host in allowed_hosts}
        origin = self.headers.get('Origin')
        token = self.headers.get('X-MRC-Session', '').encode()
        if (self.headers.get('Host') not in allowed_hosts or (origin is not None and origin not in origins)
                or self.headers.get('Sec-Fetch-Site') == 'cross-site'
                or (api and not secrets.compare_digest(token, self.server.session_token.encode()))):
            self._reply(403, {'error': 'This interface only accepts requests from its local page.'})
            return False
        return True

    def do_GET(self):
        path = urlsplit(self.path)
        if not self._allowed(path.path.startswith('/api/')):
            return
        assets = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'),
                  '/style.css': ('style.css', 'text/css')}
        if path.path in assets:
            filename, content_type = assets[path.path]
            body = files('webui').joinpath(filename).read_bytes()
            if filename == 'index.html':
                body = body.replace(b'__SESSION_TOKEN__', self.server.session_token.encode())
            self._reply(200, body, content_type)
            return
        query = {key: values[0] for key, values in parse_qs(path.query).items()}
        api = self.server.api
        try:
            with self.server.api_lock:
                if path.path == '/api/devices':
                    account = {}
                    for key, endpoint, field in (
                        ('user', '/api/management/v1/useradm/users/me', 'email'),
                        ('organization', '/api/management/v2/tenantadm/tenants/me', 'name'),
                    ):
                        try:
                            details, response = api.json('GET', endpoint)
                            response.close()
                            value = details.get(field) if isinstance(details, dict) else None
                            if isinstance(value, str) and value.strip():
                                account[key] = value
                        except (RuntimeError, requests.RequestException):
                            pass
                    value = {'server': self._public_server(), 'devices': [
                        {'id': str(d['id']), 'name': device_name(d), 'updated': device_updated(d),
                         'artifact': _device_artifact(d)}
                        for d in api.devices()], 'account': account}
                elif path.path == '/api/options':
                    value = {'groups': api.groups(), 'artifacts': [
                        {'name': a.get('name', ''), 'type': artifact_kind(a)} for a in api.artifacts()]}
                elif path.path == '/api/group-members':
                    groups = api.groups()
                    value = {'group_members': {group: api.group_devices(group) for group in groups}}
                elif path.path == '/api/deployments':
                    status = _string(query, 'status')
                    if status not in {'active', 'finished'}:
                        raise ValueError('status must be active or finished')
                    try:
                        offset = int(query.get('offset', '0'))
                        limit = int(query.get('limit', '50'))
                    except ValueError as exc:
                        raise ValueError('offset and limit must be integers') from exc
                    if offset < 0 or not 1 <= limit <= 50:
                        raise ValueError('offset must be nonnegative and limit must be 1–50')
                    deployments, total, has_more = _deployment_page(api, status, offset, limit)
                    value = {'deployments': deployments, 'total': total, 'has_more': has_more}
                elif path.path == '/api/deployment':
                    deployment_id = _string(query, 'id')
                    deployment, statistics = api.deployment_details(deployment_id)
                    value = {'deployment': deployment, 'statistics': statistics,
                             'devices': api.deployment_devices(deployment_id)}
                elif path.path == '/api/device':
                    device = _string(query, 'id')
                    value = {'inventory': flatten_inventory(api.inventory(device)), 'tags': api.tags(device),
                             'group': api.device_group(device), 'history': api.device_deployments(device)}
                elif path.path == '/api/log':
                    value = {'text': api.deployment_log(_string(query, 'deployment_id'), _string(query, 'device_id'))}
                elif path.path == '/api/terminal':
                    with self.server.session_lock:
                        terminal = self.server.terminals.get(_string(query, 'id'))
                    if terminal is None:
                        self._reply(404, {'error': 'Terminal session not found.'})
                        return
                    value = terminal.poll()
                elif path.path == '/api/forwards':
                    with self.server.session_lock:
                        value = {'forwards': [forward.status() for forward in self.server.forwards.values()]}
                else:
                    self._reply(404, {'error': 'Unknown page or operation.'})
                    return
                self._reply(200, value)
        except ValueError as exc:
            self._reply(400, {'error': self._public_error(exc)})
        except (RuntimeError, OSError, requests.RequestException) as exc:
            self._reply(502, {'error': self._public_error(exc)})

    def do_POST(self):
        if not self._allowed(True):
            return
        if self.path != '/api/action':
            self._reply(404, {'error': 'Unknown operation.'})
            return
        if self.headers.get_content_type() != 'application/json':
            self._reply(415, {'error': 'Send application/json.'})
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 65536 or self.headers.get('Transfer-Encoding'):
                self._reply(413, {'error': 'Request must contain 1–65536 bytes of JSON.'})
                return
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError('Request must be a JSON object.')
            api = self.server.api
            action = _string(body, 'action')
            device = '' if action == 'group-deploy' else _string(body, 'device_id')
            if action == 'terminal-start':
                width = body.get('width', 80)
                height = body.get('height', 24)
                if type(width) is not int or not 40 <= width <= 200 or type(height) is not int or not 10 <= height <= 80:
                    raise ValueError('Terminal size must be 40–200 columns by 10–80 rows.')
                with self.server.session_lock:
                    if len(self.server.terminals) >= 4:
                        raise ValueError('Stop another terminal before opening a new one.')
                    terminal = Shell(MenderAPI(api.config), device, width, height)
                    self.server.terminals[terminal.id] = terminal
                self._reply(202, {'id': terminal.id, 'status': terminal.status})
                return
            if action in {'terminal-input', 'terminal-stop'}:
                terminal_id = _string(body, 'id')
                with self.server.session_lock:
                    terminal = self.server.terminals.get(terminal_id)
                    if terminal is None:
                        self._reply(404, {'error': 'Terminal session not found.'})
                        return
                    if action == 'terminal-stop':
                        self.server.terminals.pop(terminal_id)
                if action == 'terminal-stop':
                    terminal.stop()
                    terminal.api.session.close()
                    self._reply(200, {'message': 'Remote terminal closed.'})
                    return
                terminal.send(_string(body, 'input'))
                self._reply(202, {'message': 'Input sent.'})
                return
            if action == 'forward-start':
                mappings = body.get('mappings')
                if not isinstance(mappings, list) or not 1 <= len(mappings) <= 4 or any(not isinstance(m, str) for m in mappings):
                    raise ValueError('Provide between 1 and 4 port mappings.')
                parsed = [remote.parse_mapping(mapping) for mapping in mappings]
                ports = {mapping.local_port for mapping in parsed}
                if len(ports) != len(parsed):
                    raise ValueError('Each mapping needs a different local port.')
                with self.server.session_lock:
                    statuses = [forward.status() for forward in self.server.forwards.values()]
                    occupied = {port for status in statuses if status['status'] not in {'error', 'stopped'}
                                for port in status['ports']}
                    if ports & occupied:
                        raise ValueError('A local port is already used by another forwarding session.')
                    if len(self.server.forwards) >= 8:
                        raise ValueError('Stop another forwarding session before starting a new one.')
                    forward = Forward(MenderAPI(api.config), device, mappings)
                    self.server.forwards[forward.id] = forward
                self._reply(202, forward.status())
                return
            if action == 'forward-stop':
                forward_id = _string(body, 'id')
                with self.server.session_lock:
                    forward = self.server.forwards.pop(forward_id, None)
                if forward is None:
                    self._reply(404, {'error': 'Forwarding session not found.'})
                    return
                forward.stop()
                self._reply(200, {'message': 'Port forwarding stopped.'})
                return
            with self.server.api_lock:
                api = self.server.api
                if action == 'rename':
                    api.rename_device(device, _string(body, 'name'))
                    message = 'Device renamed.'
                elif action == 'tag-set':
                    api.set_tags(device, {_string(body, 'name'): _string(body, 'value', empty=True)})
                    message = 'Tag saved.'
                elif action == 'tag-remove':
                    api.remove_tag(device, _string(body, 'name'))
                    message = 'Tag removed.'
                elif action == 'group':
                    name = _string(body, 'name', empty=True)
                    if name:
                        api.assign_group(device, name)
                    else:
                        current = api.device_group(device)
                        if current:
                            api.remove_group_devices(current, [device])
                    message = 'Group membership updated.'
                elif action == 'deploy':
                    api.deploy(_string(body, 'artifact'), [device])
                    message = 'Deployment created.'
                elif action == 'group-deploy':
                    group = _string(body, 'group')
                    expected_count = body.get('expected_count')
                    if type(expected_count) is not int or expected_count < 1:
                        raise ValueError('A positive confirmed device count is required.')
                    accepted_ids = {str(item['id']) for item in api.devices()}
                    devices = list(dict.fromkeys(device_id for device_id in api.group_devices(group)
                                                 if device_id in accepted_ids))
                    if not devices:
                        raise ValueError(f'Group {group!r} has no accepted devices to deploy to.')
                    if len(devices) != expected_count:
                        raise ValueError('Group membership changed; refresh and confirm the deployment again.')
                    api.deploy(_string(body, 'artifact'), devices)
                    message = f'Deployment created for {len(devices)} accepted devices in group {group!r}.'
                else:
                    raise ValueError('Unknown action.')
                self._reply(200, {'message': message})
        except (ValueError, UnicodeDecodeError) as exc:
            self._reply(400, {'error': self._public_error(exc)})
        except (RuntimeError, OSError, requests.RequestException) as exc:
            self._reply(502, {'error': self._public_error(exc)})


def serve(api: MenderAPI, port: int = 8765) -> None:
    """Run a local page until Ctrl+C; keep credentials in the Python process."""
    try:
        with WebServer(api, port) as server:
            print(f'Open {server.origin} in your browser. Press Ctrl+C to stop.', flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
    finally:
        if 'server' in locals():
            for terminal in server.terminals.values():
                terminal.stop()
                terminal.api.session.close()
            for forward in server.forwards.values():
                forward.stop()
        api.session.close()
