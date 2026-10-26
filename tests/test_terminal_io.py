"""POSIX and Windows console behavior, including cleanup and Unicode boundaries."""

import io
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import terminal_io


def test_backend_selection(monkeypatch):
    with monkeypatch.context() as patcher:
        patcher.setattr(terminal_io.os, 'name', 'nt')
        assert isinstance(terminal_io.terminal_io(), terminal_io.WindowsTerminal)
    with monkeypatch.context() as patcher:
        patcher.setattr(terminal_io.os, 'name', 'posix')
        assert isinstance(terminal_io.terminal_io(), terminal_io.PosixTerminal)


@pytest.mark.parametrize('fail', [False, True])
def test_posix_modes_restore_after_setup_or_session_failure(monkeypatch, fail):
    termios = SimpleNamespace(TCSADRAIN=1, tcgetattr=Mock(return_value=['original']), tcsetattr=Mock())
    tty = SimpleNamespace(setraw=Mock(side_effect=OSError('setup') if fail else None))
    monkeypatch.setitem(sys.modules, 'termios', termios)
    monkeypatch.setitem(sys.modules, 'tty', tty)
    monkeypatch.setattr(terminal_io.sys, 'stdin', Mock(fileno=Mock(return_value=9)))
    with pytest.raises(OSError):
        with terminal_io.PosixTerminal():
            raise OSError('disconnected')
    termios.tcsetattr.assert_called_once_with(9, 1, ['original'])


def test_posix_poll_and_partial_writes(monkeypatch):
    console = terminal_io.PosixTerminal()
    console.fd = 9
    sock = Mock()
    select = Mock(side_effect=[([terminal_io.sys.stdin, sock], [], []), ([], [], [])])
    monkeypatch.setattr(terminal_io.select, 'select', select)
    monkeypatch.setattr(terminal_io.os, 'read', Mock(return_value=b'input'))
    assert console.poll(sock) == (b'input', True)
    assert console.poll(sock) == (None, False)
    write = Mock(side_effect=[2, 3])
    monkeypatch.setattr(terminal_io.os, 'write', write)
    console.write(b'hello')
    assert [call.args[1] for call in write.call_args_list] == [b'hello', b'llo']


@pytest.mark.parametrize('keys,expected', [(['\xe0','H'], b'\x1b[A'), (['\x00','S'], b'\x1b[3~'),
    (['\xe0','unknown'], b''), (['é'], 'é'.encode()), (['\x08'], b'\x7f'), (['\x03'], b'\x03'),
    (['\x1d'], b'\x1d')])
def test_windows_keys(keys, expected):
    console = terminal_io.WindowsTerminal()
    console._keys_api = SimpleNamespace(getwch=Mock(side_effect=keys))
    assert console._read_key() == expected


def test_windows_surrogates_and_utf8_output(monkeypatch):
    console = terminal_io.WindowsTerminal()
    console._keys_api = SimpleNamespace(getwch=Mock(side_effect=['\ud83d','\ude80']))
    assert console._read_key() == b''
    assert console._read_key() == '🚀'.encode()
    output = io.StringIO()
    monkeypatch.setattr(terminal_io.sys, 'stdout', output)
    console.write(b'\x1b[31m\xe2')
    console.write(b'\x82\xac\x1b[0m')
    assert output.getvalue() == '\x1b[31m€\x1b[0m'


@pytest.mark.parametrize('ready', [True, False])
def test_windows_poll_selects_sockets_only(monkeypatch, ready):
    console = terminal_io.WindowsTerminal()
    console._keys_api = SimpleNamespace(kbhit=Mock(side_effect=[True, False] if ready else [False]),
                                       getwch=Mock(return_value='x'))
    sock = Mock()
    select = Mock(return_value=([sock], [], []))
    monkeypatch.setattr(terminal_io.select, 'select', select)
    assert console.poll(sock) == (b'x' if ready else None, True)
    assert select.call_args.args[0] == [sock]


@pytest.mark.parametrize('failure', [None, 'get', 'set'])
def test_windows_console_modes_restore_on_success_and_failure(monkeypatch, failure):
    import ctypes
    kernel = Mock()
    def get_mode(handle, pointer):
        pointer._obj.value = 0x277 if handle == 101 else 3
        return not (failure == 'get' and handle == 202)
    kernel.GetConsoleMode.side_effect = get_mode
    kernel.SetConsoleMode.side_effect = lambda handle, flags: not (failure == 'set' and handle == 202 and flags == 7)
    monkeypatch.setitem(sys.modules, 'msvcrt', SimpleNamespace(get_osfhandle=Mock(side_effect=[101,202])))
    monkeypatch.setattr(ctypes, 'WinDLL', Mock(return_value=kernel), raising=False)
    monkeypatch.setattr(ctypes, 'get_last_error', lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, 'WinError', lambda *_: OSError('console setup'), raising=False)
    monkeypatch.setattr(terminal_io.sys, 'stdin', Mock())
    monkeypatch.setattr(terminal_io.sys, 'stdout', Mock(fileno=Mock(return_value=1)))
    console = terminal_io.WindowsTerminal()
    if failure:
        with pytest.raises(OSError, match='console setup'):
            console.__enter__()
    else:
        with console:
            assert kernel.SetConsoleMode.call_args_list[0].args == (101, 0x80)
    assert kernel.SetConsoleMode.call_args_list[-1].args == (101, 0x277)
    assert not console._modes
