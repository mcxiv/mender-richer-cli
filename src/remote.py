"""Device-connect WebSockets, remote terminal sessions, and port forwarding."""

from __future__ import annotations

import select
import shutil
import socket
import ssl
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import msgpack
import websocket

from api import DEVICE_CONNECT, MenderAPI
from display import TerminalNotification
from terminal_io import terminal_io
from protocol import (PROTO_CONTROL, PROTO_PORT_FORWARD, PROTO_PORT_FORWARD_V2,
                       PROTO_SHELL, body_value, message, pack_message, unpack_message)


def connect(api: MenderAPI, device_id: str) -> websocket.WebSocket:
    """Open an authenticated device-connect WebSocket using the configured TLS policy.

    :param api: API client supplying the server, token, and TLS settings.
    :param device_id: Target device identifier.
    :returns: The connected WebSocket; the caller must close it.
    """
    parts = urlsplit(api.config.server)
    scheme = "wss" if parts.scheme == "https" else "ws"
    path = f"{DEVICE_CONNECT}/{quote(device_id, safe='')}/connect"
    url = urlunsplit((scheme, parts.netloc, path, "", ""))
    sslopt = {"cert_reqs": ssl.CERT_NONE, "check_hostname": False} if api.config.skip_verify else {}
    return websocket.create_connection(url, header={"Authorization": f"Bearer {api.config.token}"},
                                       timeout=15, sslopt=sslopt, enable_multithread=True)


def _send(ws: websocket.WebSocket, value: dict[str, Any]) -> None:
    """Send a protocol envelope as a binary WebSocket frame.

    :param ws: Connected device WebSocket.
    :param value: Protocol envelope to encode.
    """
    ws.send_binary(pack_message(value))


def _recv(ws: websocket.WebSocket) -> dict[str, Any] | None:
    """Receive a binary protocol message and handle WebSocket control frames.

    :param ws: Connected device WebSocket.
    :returns: A decoded message, or ``None`` after a timeout, ping, or nonbinary frame.
    :raises ConnectionError: The server sends a WebSocket close frame.
    """
    try:
        opcode, data = ws.recv_data(control_frame=True)
    except websocket.WebSocketTimeoutException:
        return None
    if opcode == websocket.ABNF.OPCODE_PING:
        ws.pong(data)
        return None
    if opcode == websocket.ABNF.OPCODE_CLOSE:
        raise ConnectionError("the server closed the device connection")
    if opcode != websocket.ABNF.OPCODE_BINARY:
        return None
    return unpack_message(data)


def _terminal_size() -> tuple[int, int]:
    """Read the terminal dimensions, falling back to 80 columns and 40 rows.

    :returns: The terminal width and height in characters.
    """
    return tuple(shutil.get_terminal_size(fallback=(80, 40)))


def terminal(api: MenderAPI, device_id: str) -> None:
    """Run a remote shell, restoring terminal settings when the session ends.

    :param api: Authenticated API client.
    :param device_id: Target device identifier.
    :raises RuntimeError: The terminal is noninteractive, the device is disconnected, or the shell
        session is rejected.
    :raises TimeoutError: No shell session is established within 30 seconds.
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise RuntimeError("remote terminal requires an interactive terminal")
    api.require_connected(device_id)
    name = api.display_name(device_id)
    ws = connect(api, device_id)
    session = ""
    try:
        width, height = _terminal_size()
        _send(ws, message(PROTO_SHELL, "new", properties={"terminal_width": width,
                                                            "terminal_height": height}))
        ws.settimeout(0.25)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            item = _recv(ws)
            if item is None:
                continue
            hdr = item["hdr"]
            if hdr.get("proto") == PROTO_CONTROL and hdr.get("typ") == "ping":
                _send(ws, message(PROTO_CONTROL, "pong", hdr.get("sid", "")))
            elif hdr.get("typ") == "error":
                raise RuntimeError(f"remote terminal rejected session: {item.get('body', b'')!r}")
            elif hdr.get("proto") == PROTO_SHELL and hdr.get("typ") == "new":
                if (hdr.get("props") or {}).get("status") == 2:
                    raise RuntimeError(f"remote terminal rejected session: {item.get('body', b'')!r}")
                session = hdr.get("sid", "")
                break
        if not session:
            raise TimeoutError("timed out creating remote terminal")

        with terminal_io() as console, TerminalNotification(
                "Remote terminal connected", f"{name} ({device_id}) · Ctrl+] to leave", console.write) as notification:
            next_resize = time.monotonic() + 0.4
            while True:
                notification.update()
                data, readable = console.poll(ws.sock)
                if data is not None:
                    if not data or b"\x1d" in data:
                        _send(ws, message(PROTO_SHELL, "stop", session))
                        break
                    _send(ws, message(PROTO_SHELL, "shell", session, data))
                if readable:
                    item = _recv(ws)
                    if item is None:
                        continue
                    hdr = item["hdr"]
                    kind = hdr.get("typ")
                    if hdr.get("proto") == PROTO_CONTROL:
                        if kind == "ping":
                            _send(ws, message(PROTO_CONTROL, "pong", session))
                        elif kind == "close":
                            break
                        elif kind == "error":
                            raise RuntimeError(f"remote terminal error: {item.get('body', b'')!r}")
                    elif hdr.get("proto") == PROTO_SHELL:
                        if kind == "shell":
                            console.write(item.get("body", b""))
                        elif kind == "ping":
                            _send(ws, message(PROTO_SHELL, "pong", session))
                        elif kind == "stop":
                            break
                        elif kind == "error":
                            raise RuntimeError(f"remote terminal error: {item.get('body', b'')!r}")
                if time.monotonic() >= next_resize:
                    new_width, new_height = _terminal_size()
                    if (new_width, new_height) != (width, height):
                        width, height = new_width, new_height
                        _send(ws, message(PROTO_SHELL, "resize", session, properties={
                            "terminal_width": width, "terminal_height": height}))
                    next_resize = time.monotonic() + 0.4
    finally:
        ws.close()


@dataclass
class Mapping:
    """Describe a local port and its remote forwarding destination.

    :param protocol: Transport name: ``tcp`` or ``udp``.
    :param local_port: Port to bind on the local machine.
    :param remote_host: Destination host as seen from the device.
    :param remote_port: Destination port on the remote host.
    """

    protocol: str
    local_port: int
    remote_host: str
    remote_port: int


def parse_mapping(value: str) -> Mapping:
    """Parse ``[tcp/|udp/]LOCAL[:REMOTE_PORT]`` or ``[tcp/|udp/]LOCAL:HOST:PORT``.

    :param value: Forwarding specification; defaults to TCP, loopback, and matching local/remote
        ports.
    :returns: The validated transport, local port, remote host, and remote port.
    :raises ValueError: The protocol, field layout, or port range is invalid.
    """
    protocol, _, value = value.partition("/")
    if not value:
        value, protocol = protocol, "tcp"
    if protocol not in {"tcp", "udp"}:
        raise ValueError(f"unknown protocol {protocol!r}; use tcp or udp")
    fields = value.split(":")
    if len(fields) == 1:
        local, host, remote = fields[0], "127.0.0.1", fields[0]
    elif len(fields) == 2:
        local, host, remote = fields[0], "127.0.0.1", fields[1]
    elif len(fields) == 3 and fields[1]:
        local, host, remote = fields
    else:
        raise ValueError(f"invalid mapping {value!r}")
    try:
        local_port, remote_port = int(local), int(remote)
    except ValueError as exc:
        raise ValueError(f"invalid port in mapping {value!r}") from exc
    if not 1 <= local_port <= 65535 or not 1 <= remote_port <= 65535:
        raise ValueError("ports must be between 1 and 65535")
    return Mapping(protocol, local_port, host, remote_port)


@dataclass
class Route:
    """Track a forwarding connection and its protocol flow-control state.

    :param mapping: Transport and destination settings.
    :param connection_id: Identifier shared with the device forwarding protocol.
    :param local: Local accepted TCP socket or UDP listener.
    :param udp: Whether the route uses a shared UDP listener.
    :param started: Whether the device has acknowledged route creation.
    :param ready: Whether another payload can be sent under protocol flow control.
    :param peer: Latest local UDP peer address, if one has sent a datagram.
    """

    mapping: Mapping
    connection_id: str
    local: socket.socket
    udp: bool = False
    started: bool = False
    ready: bool = True
    peer: Any = None


def port_forward(api: MenderAPI, device_id: str, bind: str, values: list[str]) -> None:
    """Negotiate forwarding and relay local TCP/UDP traffic until interrupted.

    :param api: Authenticated API client.
    :param device_id: Target device identifier.
    :param bind: Local interface address on which to bind listeners.
    :param values: Port specifications accepted by :func:`parse_mapping`.
    :raises ValueError: No mappings are supplied or a mapping is invalid.
    :raises RuntimeError: The device is disconnected or forwarding is rejected.
    :raises TimeoutError: Protocol negotiation does not finish within 30 seconds.
    :raises OSError: A socket cannot be bound or traffic cannot be relayed.
    """
    mappings = [parse_mapping(value) for value in values]
    if not mappings:
        raise ValueError("at least one mapping is required")
    api.require_connected(device_id)
    ws = connect(api, device_id)
    protocol = 0
    session = ""
    try:
        ws.settimeout(0.15)
        body = msgpack.packb({"versions": [1]}, use_bin_type=True)
        _send(ws, message(PROTO_CONTROL, "open", body=body))
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            item = _recv(ws)
            if item is None:
                continue
            hdr = item["hdr"]
            if hdr.get("typ") == "error":
                raise RuntimeError(f"port-forward negotiation rejected: {item.get('body', b'')!r}")
            if hdr.get("typ") != "accept":
                continue
            accepted = body_value(item)
            protocols = accepted.get("protocols", [])
            protocol = PROTO_PORT_FORWARD_V2 if PROTO_PORT_FORWARD_V2 in protocols else PROTO_PORT_FORWARD
            if protocol not in protocols:
                raise RuntimeError("port forwarding is not enabled on this device")
            session = hdr.get("sid", "")
            break
        if not protocol:
            raise TimeoutError("port-forward negotiation timed out")
    except BaseException:
        ws.close()
        raise

    listeners: list[socket.socket] = []
    routes: dict[str, Route] = {}
    udp_routes: dict[int, Route] = {}
    notification = None
    try:
        for mapping in mappings:
            if mapping.protocol == "tcp":
                listener = socket.socket(socket.AF_INET6 if ":" in bind else socket.AF_INET, socket.SOCK_STREAM)
                listeners.append(listener)
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                listener.bind((bind, mapping.local_port))
                listener.listen()
                listener.setblocking(False)
            else:
                listener = socket.socket(socket.AF_INET6 if ":" in bind else socket.AF_INET, socket.SOCK_DGRAM)
                listeners.append(listener)
                listener.bind((bind, mapping.local_port))
                listener.setblocking(False)
                connection_id = str(uuid.uuid4())
                route = Route(mapping, connection_id, listener, udp=True)
                routes[connection_id] = route
                udp_routes[mapping.local_port] = route
                _open_route(ws, route, session, protocol)

        notification = TerminalNotification("Port forwarding ready", "\n".join(
            f"{m.protocol}/{bind}:{m.local_port} → {m.protocol}/{m.remote_host}:{m.remote_port}"
            for m in mappings) + "\nCtrl+C to stop")

        while True:
            notification.update()
            readable, _, _ = select.select([ws.sock, *listeners,
                                             *(r.local for r in routes.values() if not r.udp)], [], [], 0.15)
            if ws.sock in readable:
                item = _recv(ws)
                if item is not None:
                    hdr = item["hdr"]
                    kind = hdr.get("typ")
                    if kind == "ping":
                        _send(ws, message(hdr.get("proto", PROTO_CONTROL), "pong", session))
                    elif kind == "close":
                        raise ConnectionError("device closed port-forward session")
                    elif kind == "error":
                        error = body_value(item)
                        if error.get("err") == "unknown connection" and error.get("msgtype") in {"ack", "forward", "stop"}:
                            continue
                        raise RuntimeError(f"port-forward error: {error}")
                    elif hdr.get("proto") == protocol:
                        props = hdr.get("props") or {}
                        route = routes.get(props.get("connection_id"))
                        if route:
                            if _route_message(ws, route, item, session, protocol):
                                routes.pop(route.connection_id, None)
            for listener in listeners:
                if listener not in readable:
                    continue
                mapping = next(m for m in mappings if m.local_port == listener.getsockname()[1])
                if mapping.protocol == "tcp":
                    local, _ = listener.accept()
                    local.settimeout(0.25)
                    route = Route(mapping, str(uuid.uuid4()), local)
                    routes[route.connection_id] = route
                    _open_route(ws, route, session, protocol)
                else:
                    data, peer = listener.recvfrom(65535)
                    route = udp_routes[listener.getsockname()[1]]
                    route.peer = peer
                    if route.started and route.ready:
                        _send(ws, message(protocol, "forward", session, data,
                                         {"connection_id": route.connection_id}))
                        if protocol == PROTO_PORT_FORWARD:
                            route.ready = False
            for route in list(routes.values()):
                if route.udp or route.local not in readable or not route.started or not route.ready:
                    continue
                try:
                    data = route.local.recv(65535)
                except (BlockingIOError, socket.timeout):
                    continue
                if not data:
                    try:
                        _send(ws, message(protocol, "stop", session,
                                         properties={"connection_id": route.connection_id}))
                    except Exception:
                        pass
                    route.local.close()
                    routes.pop(route.connection_id, None)
                    continue
                _send(ws, message(protocol, "forward", session, data,
                                 {"connection_id": route.connection_id}))
                if protocol == PROTO_PORT_FORWARD:
                    route.ready = False
    except KeyboardInterrupt:
        pass
    finally:
        for local in listeners:
            local.close()
        for route in routes.values():
            if not route.udp:
                route.local.close()
        try:
            _send(ws, message(PROTO_CONTROL, "close", session))
        except Exception:
            pass
        ws.close()
        if notification is not None:
            notification.close()


def _open_route(ws: websocket.WebSocket, route: Route, session: str, protocol: int) -> None:
    """Request a remote forwarding connection for a local route.

    :param ws: Connected device WebSocket.
    :param route: Local route whose remote connection should be opened.
    :param session: Negotiated forwarding session identifier.
    :param protocol: Negotiated port-forward protocol identifier.
    """
    body = msgpack.packb({"remote_host": route.mapping.remote_host,
                          "remote_port": route.mapping.remote_port,
                          "protocol": route.mapping.protocol}, use_bin_type=True)
    _send(ws, message(protocol, "new", session, body,
                      {"connection_id": route.connection_id}))


def _route_message(ws: websocket.WebSocket, route: Route, item: dict[str, Any],
                   session: str, protocol: int) -> bool:
    """Apply a forwarding message to its local route and send required acknowledgments.

    :param ws: Connected device WebSocket.
    :param route: Route associated with the incoming connection ID.
    :param item: Decoded protocol message.
    :param session: Negotiated forwarding session identifier.
    :param protocol: Negotiated port-forward protocol identifier.
    :returns: Whether the route has stopped and should be removed.
    """
    hdr = item["hdr"]
    kind = hdr.get("typ")
    if kind == "new":
        route.started = True
    elif kind == "ack" and protocol == PROTO_PORT_FORWARD:
        route.ready = True
    elif kind == "stop":
        if not route.udp:
            route.local.close()
        return True
    elif kind == "forward":
        data = item.get("body", b"")
        if route.udp:
            if route.peer:
                route.local.sendto(data, route.peer)
        else:
            route.local.sendall(data)
        if protocol == PROTO_PORT_FORWARD:
            _send(ws, message(protocol, "ack", session,
                              properties={"connection_id": route.connection_id}))
    return False

