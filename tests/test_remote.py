"""Remote shell and TCP/UDP forwarding negotiation, payloads, flow control, and cleanup."""

from types import SimpleNamespace
from unittest.mock import Mock, MagicMock

import msgpack
import pytest
import websocket

import remote
from protocol import PROTO_CONTROL, PROTO_SHELL, PROTO_PORT_FORWARD, PROTO_PORT_FORWARD_V2, message, pack_message, unpack_message


@pytest.fixture
def session(monkeypatch):
    api = Mock()
    api.config = SimpleNamespace(server='https://example.com', token='test-token', skip_verify=False)
    ws = Mock()
    console = MagicMock()
    console.__enter__.return_value = console
    send = Mock()
    receive = Mock()
    monkeypatch.setattr(remote, 'connect', Mock(return_value=ws))
    monkeypatch.setattr(remote, '_send', send)
    monkeypatch.setattr(remote, '_recv', receive)
    monkeypatch.setattr(remote, 'terminal_io', Mock(return_value=console))
    monkeypatch.setattr(remote.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(remote.sys.stdout, 'isatty', lambda: True)
    monkeypatch.setattr(remote, '_terminal_size', Mock(return_value=(80, 24)))
    monkeypatch.setattr(remote.time, 'monotonic', lambda: 0)
    return SimpleNamespace(api=api, ws=ws, console=console, send=send, receive=receive)


@pytest.mark.parametrize(('server','skip','scheme'), [('https://example.com',False,'wss'), ('http://example.com',True,'ws')])
def test_websocket_connection_url_and_tls(monkeypatch, server, skip, scheme):
    api = SimpleNamespace(config=SimpleNamespace(server=server, token='test', skip_verify=skip))
    create = Mock()
    monkeypatch.setattr(remote.websocket, 'create_connection', create)
    remote.connect(api, 'device/id')
    assert create.call_args.args[0] == f'{scheme}://example.com/api/management/v1/deviceconnect/devices/device%2Fid/connect'
    assert create.call_args.kwargs['header'] == {'Authorization': 'Bearer test'}
    assert bool(create.call_args.kwargs['sslopt']) is skip


def test_send_binary_envelope():
    ws = Mock()
    value = message(PROTO_SHELL, 'shell', 'session', b'bytes')
    remote._send(ws, value)
    assert unpack_message(ws.send_binary.call_args.args[0]) == value


@pytest.mark.parametrize('frame', ['timeout','ping','close','text','binary'])
def test_receive_frames(frame):
    ws = Mock()
    if frame == 'timeout':
        ws.recv_data.side_effect = websocket.WebSocketTimeoutException()
        assert remote._recv(ws) is None
    elif frame == 'close':
        ws.recv_data.return_value = (websocket.ABNF.OPCODE_CLOSE, b'')
        with pytest.raises(ConnectionError):
            remote._recv(ws)
    else:
        opcode = {'ping':websocket.ABNF.OPCODE_PING,'text':websocket.ABNF.OPCODE_TEXT,'binary':websocket.ABNF.OPCODE_BINARY}[frame]
        value = message(PROTO_SHELL, 'shell', body=b'data')
        ws.recv_data.return_value = (opcode, pack_message(value))
        assert remote._recv(ws) == (value if frame == 'binary' else None)
        if frame == 'ping':
            ws.pong.assert_called_once()


def test_terminal_size(monkeypatch):
    monkeypatch.setattr(remote.shutil, 'get_terminal_size', Mock(return_value=(123,45)))
    assert remote._terminal_size() == (123,45)


def test_shell_input_output_keepalives_and_ctrl_bracket(session):
    s = session
    s.receive.side_effect = [None, message(PROTO_CONTROL,'ping','control'), message(PROTO_SHELL,'new','session'),
                            message(PROTO_SHELL,'shell','session',b'output'),
                            message(PROTO_SHELL,'ping','session')]
    s.console.poll.side_effect = [(b'ls\r',True),(None,True),(b'\x1d',False)]
    remote.terminal(s.api, 'device')
    s.console.write.assert_any_call(b'output')
    sent = [call.args[1] for call in s.send.call_args_list]
    assert any(v['hdr']['typ']=='shell' and v['body']==b'ls\r' for v in sent)
    assert sent[-1]['hdr']['typ']=='stop'
    assert sum(v['hdr']['typ']=='pong' for v in sent)==2
    s.ws.close.assert_called_once()
    s.console.__exit__.assert_called_once()


@pytest.mark.parametrize(('protocol','kind','error'), [(PROTO_CONTROL,'close',False), (PROTO_SHELL,'stop',False),
    (PROTO_CONTROL,'error',True), (PROTO_SHELL,'error',True), (PROTO_CONTROL,'ping',False)])
def test_shell_remote_end_and_errors(session, protocol, kind, error):
    s=session
    s.receive.side_effect=[message(PROTO_SHELL,'new','session'), message(protocol,kind,'session',b'failure')]
    s.console.poll.side_effect=[(None,True),(b'',False)]
    if error:
        with pytest.raises(RuntimeError, match='remote terminal error'):
            remote.terminal(s.api,'device')
    else:
        remote.terminal(s.api,'device')
    s.ws.close.assert_called_once()
    s.console.__exit__.assert_called_once()


@pytest.mark.parametrize('reply', [message(PROTO_SHELL,'error'), message(PROTO_SHELL,'new',properties={'status':2})])
def test_rejected_shell_never_enters_raw_mode(session, reply):
    session.receive.return_value=reply
    with pytest.raises(RuntimeError,match='rejected'):
        remote.terminal(session.api,'device')
    remote.terminal_io.assert_not_called()
    session.ws.close.assert_called_once()


def test_shell_timeout_and_noninteractive_input(session, monkeypatch):
    monkeypatch.setattr(remote.time,'monotonic',Mock(side_effect=[0,31]))
    with pytest.raises(TimeoutError):
        remote.terminal(session.api,'device')
    session.ws.close.assert_called_once()
    monkeypatch.setattr(remote.sys.stdin,'isatty',lambda:False)
    with pytest.raises(RuntimeError,match='interactive terminal'):
        remote.terminal(session.api,'device')


def test_shell_resize(session, monkeypatch):
    session.receive.return_value=message(PROTO_SHELL,'new','session')
    session.console.poll.side_effect=[(None,False),(b'\x1d',False)]
    monkeypatch.setattr(remote.time,'monotonic',Mock(side_effect=[0,0,0,1,1]))
    monkeypatch.setattr(remote,'_terminal_size',Mock(side_effect=[(80,24),(100,30)]))
    remote.terminal(session.api,'device')
    resized=[c.args[1] for c in session.send.call_args_list if c.args[1]['hdr']['typ']=='resize']
    assert resized[0]['hdr']['props']=={'terminal_width':100,'terminal_height':30}


@pytest.mark.parametrize(('value','expected'), [('80',('tcp',80,'127.0.0.1',80)),
    ('8080:80',('tcp',8080,'127.0.0.1',80)),('udp/5353:10.0.0.1:53',('udp',5353,'10.0.0.1',53))])
def test_forwarding_mappings(value, expected):
    mapping=remote.parse_mapping(value)
    assert (mapping.protocol,mapping.local_port,mapping.remote_host,mapping.remote_port)==expected


@pytest.mark.parametrize('value',['sctp/80','0:80','65536','abc','80::90','1:2:3:4'])
def test_invalid_forwarding_mappings(value):
    with pytest.raises(ValueError):
        remote.parse_mapping(value)


@pytest.mark.parametrize('protocol',[PROTO_PORT_FORWARD,PROTO_PORT_FORWARD_V2])
@pytest.mark.parametrize('udp',[False,True])
def test_route_lifecycle_and_incoming_payload(monkeypatch,protocol,udp):
    ws=Mock()
    local=Mock()
    route=remote.Route(remote.Mapping('udp' if udp else 'tcp',8080,'127.0.0.1',80),'route',local,udp=udp,peer=('127.0.0.1',1234))
    send=Mock()
    monkeypatch.setattr(remote,'_send',send)
    remote._open_route(ws,route,'session',protocol)
    envelope=send.call_args.args[1]
    assert msgpack.unpackb(envelope['body'],raw=False)['remote_port']==80
    assert not remote._route_message(ws,route,message(protocol,'new'),'session',protocol)
    assert route.started
    route.ready=False
    remote._route_message(ws,route,message(protocol,'ack'),'session',protocol)
    assert route.ready is (protocol==PROTO_PORT_FORWARD)
    remote._route_message(ws,route,message(protocol,'forward',body=b'payload'),'session',protocol)
    if udp:
        local.sendto.assert_called_once_with(b'payload',route.peer)
        route.peer=None
        remote._route_message(ws,route,message(protocol,'forward',body=b'dropped'),'session',protocol)
        assert local.sendto.call_count==1
    else:
        local.sendall.assert_called_once_with(b'payload')
    assert remote._route_message(ws,route,message(protocol,'stop'),'session',protocol)
    assert local.close.called is not udp
    assert not remote._route_message(ws,route,message(protocol,'unknown'),'session',protocol)


@pytest.mark.parametrize('kind',['error','unsupported','timeout'])
def test_forwarding_negotiation_failure_closes_socket(session,monkeypatch,kind):
    if kind=='timeout':
        monkeypatch.setattr(remote.time,'monotonic',Mock(side_effect=[0,31]))
    else:
        session.receive.return_value=message(PROTO_CONTROL,'error' if kind=='error' else 'accept',body=msgpack.packb({'protocols':[]}))
    with pytest.raises((RuntimeError,TimeoutError)):
        remote.port_forward(session.api,'device','127.0.0.1',['8080:80'])
    session.ws.close.assert_called_once()


def test_no_mappings_rejected_before_connection(session):
    with pytest.raises(ValueError):
        remote.port_forward(session.api,'device','127.0.0.1',[])
    remote.connect.assert_not_called()


@pytest.mark.parametrize('protocol',[PROTO_PORT_FORWARD,PROTO_PORT_FORWARD_V2])
def test_tcp_forwarding_flow_and_disconnect_cleanup(session,monkeypatch,protocol):
    listener=Mock()
    listener.getsockname.return_value=('127.0.0.1',8080)
    local=Mock()
    local.recv.side_effect=[BlockingIOError(),b'outbound',b'']
    listener.accept.return_value=(local,('127.0.0.1',9999))
    monkeypatch.setattr(remote.socket,'socket',Mock(return_value=listener))
    monkeypatch.setattr(remote.uuid,'uuid4',lambda:'route')
    route_props={'connection_id':'route'}
    session.receive.side_effect=[message(PROTO_CONTROL,'accept','session',msgpack.packb({'protocols':[protocol]})),
                                message(protocol,'new','session',properties=route_props),
                                message(protocol,'ack','session',properties=route_props)]
    ready=[[listener],[session.ws.sock],[local],[local],[session.ws.sock],[local]]
    monkeypatch.setattr(remote.select,'select',Mock(side_effect=[(r,[],[]) for r in ready]+[KeyboardInterrupt()]))
    remote.port_forward(session.api,'device','127.0.0.1',['8080:80'])
    sent=[c.args[1] for c in session.send.call_args_list]
    assert any(v['hdr']['typ']=='forward' and v['body']==b'outbound' for v in sent)
    assert sent[-1]['hdr']['typ']=='close'
    local.close.assert_called_once()
    listener.close.assert_called_once()
    session.ws.close.assert_called_once()


@pytest.mark.parametrize('protocol',[PROTO_PORT_FORWARD,PROTO_PORT_FORWARD_V2])
def test_udp_forwarding_and_control_messages(session,monkeypatch,protocol):
    listener=Mock()
    listener.getsockname.return_value=('127.0.0.1',5353)
    listener.recvfrom.side_effect=[(b'before-open',('127.0.0.1',1000)),(b'one',('127.0.0.1',1000)),(b'two',('127.0.0.1',1000))]
    monkeypatch.setattr(remote.socket,'socket',Mock(return_value=listener))
    monkeypatch.setattr(remote.uuid,'uuid4',lambda:'route')
    props={'connection_id':'route'}
    session.receive.side_effect=[None,message(PROTO_CONTROL,'ping'),
        message(PROTO_CONTROL,'accept','session',msgpack.packb({'protocols':[protocol]})),
        message(protocol,'new','session',properties=props),message(protocol,'forward','session',b'reply',props),
        message(PROTO_CONTROL,'ping','session'),
        message(PROTO_CONTROL,'error',body=msgpack.packb({'err':'unknown connection','msgtype':'ack'})),
        None,message(PROTO_CONTROL,'close','session')]
    ready=[[listener],[session.ws.sock],[listener],[listener],*[ [session.ws.sock] for _ in range(5)]]
    monkeypatch.setattr(remote.select,'select',Mock(side_effect=[(r,[],[]) for r in ready]))
    with pytest.raises(ConnectionError):
        remote.port_forward(session.api,'device','::1',['udp/5353:53'])
    listener.sendto.assert_called_once_with(b'reply',('127.0.0.1',1000))
    forwarded=[c.args[1] for c in session.send.call_args_list if c.args[1]['hdr']['typ']=='forward']
    assert len(forwarded)==(1 if protocol==PROTO_PORT_FORWARD else 2)
    listener.close.assert_called_once()
    session.ws.close.assert_called_once()


@pytest.mark.parametrize('operation',['settimeout','send','interrupt'])
def test_early_forwarding_failures_close_websocket(session,operation):
    if operation=='settimeout':
        session.ws.settimeout.side_effect=OSError('setup failed')
    else:
        session.send.side_effect=KeyboardInterrupt() if operation=='interrupt' else OSError('send failed')
    with pytest.raises((OSError,KeyboardInterrupt)):
        remote.port_forward(session.api,'device','127.0.0.1',['8080:80'])
    session.ws.close.assert_called_once()


@pytest.mark.parametrize('mapping',['8080:80','udp/5353:53'])
def test_bind_failure_closes_new_listener_and_websocket(session,monkeypatch,mapping):
    listener=Mock()
    listener.bind.side_effect=OSError('address in use')
    monkeypatch.setattr(remote.socket,'socket',Mock(return_value=listener))
    session.receive.return_value=message(PROTO_CONTROL,'accept','session',msgpack.packb({'protocols':[PROTO_PORT_FORWARD]}))
    with pytest.raises(OSError,match='address in use'):
        remote.port_forward(session.api,'device','127.0.0.1',[mapping])
    listener.close.assert_called_once()
    session.ws.close.assert_called_once()


def test_remote_route_stop_releases_local_socket(session,monkeypatch):
    listener=Mock()
    listener.getsockname.return_value=('127.0.0.1',8080)
    local=Mock()
    listener.accept.return_value=(local,('127.0.0.1',1234))
    monkeypatch.setattr(remote.socket,'socket',Mock(return_value=listener))
    monkeypatch.setattr(remote.uuid,'uuid4',lambda:'route')
    session.receive.side_effect=[message(PROTO_CONTROL,'accept','session',msgpack.packb({'protocols':[PROTO_PORT_FORWARD]})),
                                message(PROTO_PORT_FORWARD,'stop',properties={'connection_id':'route'})]
    monkeypatch.setattr(remote.select,'select',Mock(side_effect=[([listener],[],[]),([session.ws.sock],[],[]),KeyboardInterrupt()]))
    remote.port_forward(session.api,'device','127.0.0.1',['8080:80'])
    local.close.assert_called_once()
    session.ws.close.assert_called_once()
