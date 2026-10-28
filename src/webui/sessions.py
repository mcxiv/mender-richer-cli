"""Background Mender sessions owned by one local browser server."""

from codecs import getincrementaldecoder
from queue import Empty, Queue
import secrets
from threading import Event, Lock, Thread
from urllib.parse import urlsplit

from api import MenderAPI
import remote


def _safe_error(api: MenderAPI, exc: Exception) -> str:
    server = api.config.server
    parts = urlsplit(server)
    safe_server = parts._replace(netloc=parts.netloc.rsplit('@', 1)[-1], query='', fragment='').geturl()
    error = str(exc).replace(server, safe_server)
    return error.replace(api.config.token, '[redacted]') if api.config.token else error


class Shell:
    """Bridge a remote shell to bounded browser input and output queues."""

    def __init__(self, api: MenderAPI, device_id: str, width: int, height: int):
        self.api, self.device_id, self.width, self.height = api, device_id, width, height
        self.id = secrets.token_urlsafe(24)
        self.inputs: Queue = Queue()
        self.output: list[str] = []
        self.output_size = 0
        self.decoder = getincrementaldecoder('utf-8')('replace')
        self.lock = Lock()
        self.stop_event = Event()
        self.status = 'connecting'
        self.error = None
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        ws = None
        session = ''
        try:
            if self.stop_event.is_set():
                return
            self.api.require_connected(self.device_id)
            ws, session = remote.open_terminal_session(self.api, self.device_id, self.width, self.height,
                                                       stop_event=self.stop_event)
            self.status = 'connected'
            while not self.stop_event.is_set():
                try:
                    data = self.inputs.get_nowait()
                except Empty:
                    data = None
                if data:
                    remote.send_terminal_input(ws, session, data)
                output = remote.terminal_output(ws, session)
                if output:
                    text = self.decoder.decode(output)
                    with self.lock:
                        self.output.append(text)
                        self.output_size += len(text)
                        if self.output_size > 1_000_000:
                            raise RuntimeError('terminal output exceeded 1 MB; close and reconnect')
        except Exception as exc:
            if not self.stop_event.is_set():
                self.error = _safe_error(self.api, exc)
        finally:
            if ws:
                remote.close_terminal_session(ws, session)
            self.status = 'closed'
            self.api.session.close()

    def send(self, value: str) -> None:
        if self.status != 'connected':
            raise ValueError('terminal is not connected')
        data = value.encode('utf-8')
        if len(data) > 4096:
            raise ValueError('terminal input must be at most 4096 bytes')
        if not data:
            raise ValueError('terminal input is empty')
        self.inputs.put(data)

    def poll(self) -> dict:
        with self.lock:
            output = ''.join(self.output)
            self.output.clear()
            self.output_size = 0
        return {'id': self.id, 'status': self.status, 'output': output, 'error': self.error}

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=1)


class Forward:
    """Run a cancellable loopback-only TCP or UDP tunnel."""

    def __init__(self, api: MenderAPI, device_id: str, mappings: list[str]):
        self.api, self.device_id, self.mappings = api, device_id, mappings
        self.id = secrets.token_urlsafe(24)
        self.stop_event = Event()
        self.ready_event = Event()
        self.error = None
        self.thread = Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        try:
            remote.port_forward(self.api, self.device_id, '127.0.0.1', self.mappings,
                                stop_event=self.stop_event, ready_event=self.ready_event, notify=False)
        except Exception as exc:
            if not self.stop_event.is_set():
                self.error = _safe_error(self.api, exc)
        finally:
            self.api.session.close()

    def status(self) -> dict:
        return {'id': self.id, 'device_id': self.device_id,
                'mappings': self.mappings,
                'ports': [remote.parse_mapping(value).local_port for value in self.mappings],
                'status': 'error' if self.error else 'ready' if self.ready_event.is_set() and
                self.thread.is_alive() else 'stopped' if not self.thread.is_alive() else 'starting',
                'error': self.error}

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=1)
