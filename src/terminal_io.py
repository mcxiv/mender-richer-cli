"""Platform-specific console input and output for remote shell sessions."""

from __future__ import annotations

import codecs
import os
import select
import socket
import sys
from typing import Any


class PosixTerminal:
    """Use raw POSIX terminal input while preserving the original terminal settings."""

    def __enter__(self) -> PosixTerminal:
        """Save terminal settings and enable raw input.

        :returns: The active terminal adapter.
        """
        import termios
        import tty

        self.fd = sys.stdin.fileno()
        self.settings = termios.tcgetattr(self.fd)
        try:
            tty.setraw(self.fd)
        except BaseException:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.settings)
            raise
        return self

    def __exit__(self, *exc: Any) -> None:
        """Restore the terminal settings, including after connection errors.

        :param exc: Exception details supplied by the context manager protocol.
        """
        import termios

        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.settings)

    def poll(self, sock: socket.socket) -> tuple[bytes | None, bool]:
        """Wait briefly for keyboard input or remote output.

        :param sock: Socket carrying the remote shell session.
        :returns: Input bytes (empty on EOF), or None when idle, and socket readability.
        """
        ready, _, _ = select.select([sys.stdin, sock], [], [], 0.2)
        data = os.read(self.fd, 4096) if sys.stdin in ready else None
        return data, sock in ready

    def write(self, data: bytes) -> None:
        """Write remote output without changing its bytes.

        :param data: Remote terminal output.
        """
        while data:
            data = data[os.write(sys.stdout.fileno(), data):]


class WindowsTerminal:
    """Poll Windows console keys and render UTF-8 output with ANSI terminal sequences."""

    _keys = {
        "H": b"\x1b[A", "P": b"\x1b[B", "M": b"\x1b[C", "K": b"\x1b[D",
        "G": b"\x1b[H", "O": b"\x1b[F", "R": b"\x1b[2~", "S": b"\x1b[3~",
        "I": b"\x1b[5~", "Q": b"\x1b[6~", "\x0f": b"\x1b[Z",
        ";": b"\x1bOP", "<": b"\x1bOQ", "=": b"\x1bOR", ">": b"\x1bOS",
        "?": b"\x1b[15~", "@": b"\x1b[17~", "A": b"\x1b[18~", "B": b"\x1b[19~",
        "C": b"\x1b[20~", "D": b"\x1b[21~", "\x85": b"\x1b[23~", "\x86": b"\x1b[24~",
    }

    def __init__(self) -> None:
        """Initialize output decoding and console restoration state."""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self._modes: list[tuple[int, int]] = []
        self._surrogate = ""

    def __enter__(self) -> WindowsTerminal:
        """Enable unbuffered keyboard input and virtual-terminal output.

        :returns: The active terminal adapter.
        :raises OSError: The console modes cannot be read or changed.
        """
        import ctypes
        import msvcrt
        from ctypes import wintypes

        self._keys_api = msvcrt
        self._kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        self._kernel.GetConsoleMode.restype = wintypes.BOOL
        self._kernel.SetConsoleMode.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._kernel.SetConsoleMode.restype = wintypes.BOOL
        try:
            for stream, is_input in ((sys.stdin, True), (sys.stdout, False)):
                handle = msvcrt.get_osfhandle(stream.fileno())
                mode = wintypes.DWORD()
                if not self._kernel.GetConsoleMode(handle, ctypes.byref(mode)):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._modes.append((handle, mode.value))
                # Disable line editing, echo, Ctrl+C processing, Quick Edit,
                # and VT input; getwch handles the traditional console key codes.
                flags = ((mode.value & ~0x277) | 0x80) if is_input else mode.value | 0x5
                if not self._kernel.SetConsoleMode(handle, flags):
                    raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.__exit__()
            raise
        return self

    def __exit__(self, *exc: Any) -> None:
        """Flush pending UTF-8 output and restore both console modes.

        :param exc: Exception details supplied by the context manager protocol.
        """
        try:
            sys.stdout.write(self._decoder.decode(b"", final=True))
            sys.stdout.flush()
        finally:
            for handle, mode in reversed(self._modes):
                self._kernel.SetConsoleMode(handle, mode)
            self._modes.clear()

    def _read_key(self) -> bytes:
        """Translate a console key into the UTF-8 or ANSI bytes expected by a shell.

        :returns: Encoded input, or empty bytes for unsupported keys or an incomplete surrogate.
        """
        key = self._keys_api.getwch()
        if key in {"\x00", "\xe0"}:
            return self._keys.get(self._keys_api.getwch(), b"")
        if "\ud800" <= key <= "\udbff":
            self._surrogate = key
            return b""
        if self._surrogate:
            key = (self._surrogate + key).encode("utf-16-le", errors="surrogatepass").decode(
                "utf-16-le", errors="replace")
            self._surrogate = ""
        return b"\x7f" if key == "\x08" else key.encode("utf-8", errors="replace")

    def poll(self, sock: socket.socket) -> tuple[bytes | None, bool]:
        """Poll console input separately from the Windows socket-only select call.

        :param sock: Socket carrying the remote shell session.
        :returns: Input bytes or None when idle, and whether remote output is ready.
        """
        data = bytearray()
        while len(data) < 4096 and self._keys_api.kbhit():
            data.extend(self._read_key())
        ready, _, _ = select.select([sock], [], [], 0 if data else 0.05)
        return bytes(data) if data else None, sock in ready

    def write(self, data: bytes) -> None:
        """Decode remote UTF-8 incrementally, including across WebSocket frames.

        :param data: Remote terminal output, possibly ending within a UTF-8 character.
        """
        sys.stdout.write(self._decoder.decode(data))
        sys.stdout.flush()


def terminal_io() -> PosixTerminal | WindowsTerminal:
    """Choose the console adapter for the current operating system.

    :returns: A Windows or POSIX terminal context manager.
    """
    return WindowsTerminal() if os.name == "nt" else PosixTerminal()
