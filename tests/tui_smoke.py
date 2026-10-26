"""Exercise real curses rendering with fixture data on Linux and Windows terminals."""

from collections import deque
import curses
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import display
import main


def queue_keys(keys: list[str | int]) -> None:
    """Queue test input using the platform's native key representation.

    :param keys: Text characters or curses special-key constants in input order.
    """
    if os.name != "nt":
        for key in reversed(keys):
            if isinstance(key, int):
                curses.ungetch(key)
            else:
                curses.unget_wch(key)
        return

    import ctypes
    from ctypes import wintypes
    import msvcrt

    class KeyEvent(ctypes.Structure):
        """Represent a Unicode KEY_EVENT_RECORD for the Windows console."""

        _fields_ = [("down", wintypes.BOOL), ("repeat", wintypes.WORD),
                    ("virtual_key", wintypes.WORD), ("scan", wintypes.WORD),
                    ("char", wintypes.WCHAR), ("state", wintypes.DWORD)]

    class InputRecord(ctypes.Structure):
        """Represent an INPUT_RECORD containing a keyboard event."""

        _fields_ = [("kind", wintypes.WORD), ("key", KeyEvent)]

    mappings = {curses.KEY_DOWN: (0x28, 0x50), curses.KEY_RIGHT: (0x27, 0x4D)}
    records = []
    for key in keys:
        if isinstance(key, int):
            virtual, scan = mappings[key]
            records.append(InputRecord(1, KeyEvent(True, 1, virtual, scan, "\0", 0x100)))
        else:
            character = "\r" if key == "\n" else key
            virtual = 0x0D if key == "\n" else 0x1B if key == "\x1b" else ord(key.upper())
            records.append(InputRecord(1, KeyEvent(True, 1, virtual, 0, character, 0)))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.WriteConsoleInputW.argtypes = [wintypes.HANDLE, ctypes.POINTER(InputRecord),
                                        wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    kernel.WriteConsoleInputW.restype = wintypes.BOOL
    written = wintypes.DWORD()
    events = (InputRecord * len(records))(*records)
    if not kernel.WriteConsoleInputW(msvcrt.get_osfhandle(sys.stdin.fileno()), events,
                                    len(records), ctypes.byref(written)):
        raise ctypes.WinError(ctypes.get_last_error())


def run() -> None:
    """Navigate device edits, groups, deployment results, and log scrolling using queued keys."""
    down = curses.KEY_DOWN
    scenes = deque([
        ("Main menu", ["\n"]),
        ("Select a device", ["\n"]),
        ("Old bench (device-id)", [down] * 6 + ["\n"]),
        ("New bench (device-id)", ["\x1b"]),
        ("Select a device", ["\x1b"]),
        ("Main menu", [down, "\n"]),
        ("Static groups", [down, "\n"]),
        ("Group staging", [down] * 3 + ["\n"]),
        ("Order artifacts", ["\n"]),
        ("Select an artifact", ["\x1b"]),
        ("Static groups", ["\x1b"]),
        ("Main menu", [down, down, "\n"]),
        ("Deployments", ["\n"]),
        ("Test deployment", [down, "\n"]),
        ("Device results", [down, "\n"]),
        ("Select a device to read its log", ["\n"]),
        ("Log for device-id", [curses.KEY_RIGHT, curses.KEY_RIGHT, "\x1b"]),
        ("Select a device to read its log", ["\x1b"]),
        ("Test deployment", ["\x1b"]),
        ("Deployments", ["\x1b"]),
        ("Main menu", ["\x1b"]),
    ])
    api = Mock()
    api.devices.return_value = [{"id": "device-id", "attributes": [
        {"scope": "tags", "name": "name", "value": "Old bench"}]}]
    api.groups.return_value = ["staging"]
    api.group_devices.return_value = ["device-id"]
    api.artifacts.return_value = [{"name": "release-1", "description": "test", "type_info": {"type": "rootfs-image"}}]
    api.all_pages.return_value = [{"id": "deployment-id", "name": "Test deployment", "status": "finished"}]
    api.deployment_devices.return_value = [{"id": "device-id", "status": "failure", "log": True}]
    api.deployment_log.return_value = "Signature verification failed: " + "details " * 30 + "é\n"
    app = SimpleNamespace(api=api, config=SimpleNamespace(server="https://example.com"))
    original_picker = display.pick
    original_wrapper = curses.wrapper
    visited = []

    def scripted_picker(title, headers, rows, **kwargs):
        """Verify the next screen and feed keys through the platform's real curses implementation.

        :param title: Actual screen heading.
        :param headers: Table column headings.
        :param rows: Screen rows.
        :param kwargs: Additional picker options.
        :returns: The actual picker result after processing queued input.
        """
        expected, keys = scenes.popleft()
        assert title == expected, (title, expected)
        visited.append(title)

        def wrapper(callback):
            """Inject screen-specific input after curses initialization.

            :param callback: Picker renderer.
            :returns: The curses wrapper result.
            """
            def render(screen):
                """Queue text and special keys before running the real renderer.

                :param screen: Initialized curses window.
                """
                screen.timeout(5000)
                queue_keys(keys)
                return callback(screen)
            return original_wrapper(render)

        with patch.object(curses, "wrapper", side_effect=wrapper):
            return original_picker(title, headers, rows, **kwargs)

    with tempfile.TemporaryDirectory() as directory, \
         patch.dict(os.environ, {"XDG_CONFIG_HOME": directory}), \
         patch.object(main, "pick", side_effect=scripted_picker), \
         patch.object(display, "pick", side_effect=scripted_picker), \
         patch.object(main.click, "prompt", return_value="New bench"):
        main.interactive(app)
    assert not scenes, list(scenes)
    api.rename_device.assert_called_once_with("device-id", "New bench")
    api.deploy.assert_not_called()
    api.deployment_devices.assert_called_with("deployment-id", "failure")
    api.deployment_log.assert_called_once_with("deployment-id", "device-id")
    print(f"PASS: {len(visited)} native TUI screens; device rename, groups, cancellation, results, and log scrolling")


if __name__ == "__main__":
    run()
