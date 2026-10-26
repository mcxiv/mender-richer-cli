"""Terminal tables, inventory formatting, and searchable curses pickers."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

try:
    import curses
except ImportError:
    curses = None

from rich.console import Console
from rich.table import Table
from rich import box


def table(headers: list[str], rows: list[list[Any]]) -> None:
    """Print rows as a monochrome Rich table.

    :param headers: Column labels in display order.
    :param rows: Row values, converted to strings before display.
    """
    output = Table(box=box.SIMPLE, show_lines=False, header_style=None, row_styles=["", ""])
    for header in headers:
        output.add_column(str(header), overflow="ellipsis", no_wrap=False)
    for row in rows:
        output.add_row(*(str(value) for value in row))
    Console(no_color=True, highlight=False).print(output)


def print_json(value: Any) -> None:
    """Print indented JSON while preserving non-ASCII characters.

    :param value: JSON-serializable value to print.
    """
    print(json.dumps(value, indent=2, ensure_ascii=False))


def flatten_inventory(value: Any, prefix: str = "") -> list[list[str]]:
    """Flatten nested inventory values into attribute/value rows.

    :param value: Mapping, list, or scalar to flatten.
    :param prefix: Attribute path accumulated during recursion.
    :returns: Rows with dotted mapping keys, indexed nested lists, and comma-separated scalar lists.
    """
    rows: list[list[str]] = []
    if isinstance(value, dict):
        for key in sorted(value):
            name = f"{prefix}.{key}" if prefix else key
            rows.extend(flatten_inventory(value[key], name))
    elif isinstance(value, list):
        if all(not isinstance(item, (dict, list)) for item in value):
            rows.append([prefix, ", ".join(str(item) for item in value)])
        else:
            for index, item in enumerate(value):
                rows.extend(flatten_inventory(item, f"{prefix}[{index}]"))
    else:
        rows.append([prefix, "—" if value is None else str(value)])
    return rows


def copy_terminal(value: str) -> None:
    """Emit an OSC 52 clipboard sequence for terminals that support it.

    :param value: Text to copy through the terminal clipboard escape sequence.
    """
    import base64
    sys.stdout.write("\033]52;c;" + base64.b64encode(value.encode()).decode() + "\a")
    sys.stdout.flush()


def _plain_picker(title: str, rows: list[list[str]]) -> tuple[list[int], str | None, bool]:
    """Prompt for a numbered row when a curses interface is unavailable.

    :param title: Heading printed above the choices.
    :param rows: Rows displayed in their original order.
    :returns: Selected zero-based indices, an unused copy value of ``None``, and a false
        multi-selection flag.
    """
    print(f"\n{title}")
    for index, row in enumerate(rows, 1):
        print(f" {index:>3}  " + "  │  ".join(row))
    print("Enter a row number; press Enter to go back.")
    value = input("> ").strip()
    try:
        index = int(value) - 1
        return ([index], None, False) if 0 <= index < len(rows) else ([], None, False)
    except ValueError:
        return [], None, False


def pick(title: str, headers: list[str], rows: list[list[str]], *,
         device_mode: bool = False, multi_select: bool = False, viewer: bool = False, favorite: int = -1,
         toggle_favorite: Callable[[int], None] | None = None) -> tuple[list[int], int, str | None, bool]:
    """Display a searchable picker, falling back to numbered input outside a terminal.

    :param title: Heading displayed above the rows.
    :param headers: Column labels.
    :param rows: Selectable rows; returned indices refer to this original list.
    :param device_mode: Whether to enable device marking and favorite shortcuts.
    :param multi_select: Whether to enable row marking without device favorite shortcuts.
    :param viewer: Whether to show inventory viewing and clipboard shortcuts.
    :param favorite: Original row index of the favorite, or -1 when none is selected.
    :param toggle_favorite: Optional callback receiving the original index of the toggled favorite.
    :returns: Selected row indices, the favorite index, copied text or ``None``, and whether
        selection came from marked rows.
    :raises RuntimeError: Curses is unavailable, or the interface cannot be initialized or rendered.
    """
    if not rows:
        return [], favorite, None, False
    if curses is None:
        raise RuntimeError("the terminal UI requires curses; on Windows, install it with "
                           "`python -m pip install windows-curses`")
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        selected, _, multi = _plain_picker(title, rows)
        return selected, favorite, None, multi

    state: dict[str, Any] = {"filter": "", "cursor": 0, "horizontal": 0, "matches": list(range(len(rows))),
                             "checked": set(), "favorite": favorite, "selected": [], "copy": None}
    markable = device_mode or multi_select

    def render(stdscr: Any) -> None:
        """Render picker state and process keys until selection or cancellation.

        :param stdscr: Curses screen supplied by ``curses.wrapper``.
        """
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        stdscr.keypad(True)
        try:
            curses.use_default_colors()
        except curses.error:
            pass
        while True:
            height, width = stdscr.getmaxyx()
            stdscr.erase()
            query = state["filter"]
            matches = [i for i, row in enumerate(rows)
                       if query.casefold() in " ".join(row).casefold()]
            state["matches"] = matches
            if state["cursor"] >= len(matches):
                state["cursor"] = max(0, len(matches) - 1)
            _put(stdscr, 0, 1, "mender-richer-cli  /  " + title, width - 2, curses.A_BOLD)
            _put(stdscr, 1, 1, "─" * max(0, width - 2), width - 2, curses.A_DIM)
            _put(stdscr, 2, 2, "SEARCH  " + (query or "type to filter results"), width - 4)
            summary = f"{len(matches)} results"
            if markable:
                summary += f"   ·   {len(state['checked'])} selected"
            _put(stdscr, 3, 2, summary, width - 4, curses.A_DIM)

            columns = len(headers)
            separators = 3 * max(0, columns - 1)
            usable = max(columns * 4, width - 8 - separators)
            widths = []
            for col, header in enumerate(headers):
                longest = max([len(header)] + [len(row[col]) for row in rows if col < len(row)])
                widths.append(min(longest, max(6, usable // max(1, columns))))
            total = sum(widths)
            if widths and total < usable:
                widths[-1] += usable - total
            while sum(widths) > usable and max(widths, default=0) > 6:
                biggest = max(range(len(widths)), key=widths.__getitem__)
                if widths[biggest] <= 6:
                    break
                widths[biggest] -= 1
            y = 5
            marker = "      "
            _put(stdscr, y, 1, marker + "  │  ".join(_clip(h, widths[i]) for i, h in enumerate(headers)),
                 width - 2, curses.A_BOLD)
            page_size = max(1, height - 8)
            cursor = state["cursor"]
            start = cursor // page_size * page_size
            end = min(len(matches), start + page_size)
            for position in range(start, end):
                row_index = matches[position]
                row = rows[row_index]
                pointer = "›" if position == cursor else " "
                checked = "●" if row_index in state["checked"] else " "
                star = "★" if row_index == state["favorite"] else " "
                content = "  │  ".join(_clip((row[col] if col < len(row) else "")
                                            [state["horizontal"] if viewer and col > 0 else 0:], widths[col])
                                         for col in range(columns))
                attr = curses.A_REVERSE if position == cursor else curses.A_NORMAL
                _put(stdscr, y + 1 + position - start,
                     1, f"{pointer} {checked} {star}  {content}", width - 2, attr)
            if not matches:
                _put(stdscr, y + 1, 2, "No matching results.", width - 4, curses.A_DIM)

            if device_mode:
                hints = "↑/↓ move   PgUp/PgDn page   Home/End jump   type filter   Space mark   Shift+F favorite   Enter open/deploy   Esc back"
            elif multi_select:
                hints = "↑/↓ move   PgUp/PgDn page   type filter   Space mark   Enter select   Esc back"
            elif viewer:
                hints = "↑/↓ move   ←/→ scroll text   PgUp/PgDn page   type filter   Shift+C copy   Esc back"
            else:
                hints = "↑/↓ move   PgUp/PgDn page   Home/End jump   type filter   Enter select   Esc back"
            _put(stdscr, height - 2, 1, hints, width - 2, curses.A_DIM)
            stdscr.refresh()

            key = stdscr.get_wch()
            if key in ("\x1b", "\x03"):
                return
            if key in (curses.KEY_UP,):
                state["cursor"] = max(0, cursor - 1)
            elif key == curses.KEY_DOWN:
                state["cursor"] = min(max(0, len(matches) - 1), cursor + 1)
            elif key == curses.KEY_PPAGE:
                state["cursor"] = max(0, cursor - page_size)
            elif key == curses.KEY_NPAGE:
                state["cursor"] = min(max(0, len(matches) - 1), cursor + page_size)
            elif key == curses.KEY_HOME:
                state["cursor"] = 0
            elif key == curses.KEY_END:
                state["cursor"] = max(0, len(matches) - 1)
            elif key == curses.KEY_LEFT and viewer:
                state["horizontal"] = max(0, state["horizontal"] - 8)
            elif key == curses.KEY_RIGHT and viewer:
                longest = max((len(row[1]) for row in rows if len(row) > 1), default=0)
                state["horizontal"] = min(max(0, longest - 1), state["horizontal"] + 8)
            elif key in (curses.KEY_BACKSPACE, "\b", "\x7f"):
                state["filter"] = state["filter"][:-1]
                state["cursor"] = 0
            elif key == "\x15":
                state["filter"] = ""
                state["cursor"] = 0
            elif key == " " and markable and matches:
                row_index = matches[cursor]
                if row_index in state["checked"]:
                    state["checked"].remove(row_index)
                else:
                    state["checked"].add(row_index)
            elif key in (curses.KEY_SF, "F") and device_mode and matches:
                row_index = matches[cursor]
                if toggle_favorite:
                    toggle_favorite(row_index)
                state["favorite"] = -1 if state["favorite"] == row_index else row_index
            elif key == "C" and viewer and matches:
                state["copy"] = rows[matches[cursor]][1]
                copy_terminal(state["copy"])
            elif key in ("\n", "\r", curses.KEY_ENTER) and matches:
                chosen = sorted(state["checked"])
                state["selected"] = chosen or [matches[cursor]]
                state["multi"] = bool(chosen)
                return
            elif isinstance(key, str) and len(key) == 1 and key.isprintable():
                if not (device_mode and key == "F") and not (viewer and key == "C"):
                    state["filter"] += key
                    state["cursor"] = 0

    try:
        curses.wrapper(render)
    except curses.error as exc:
        raise RuntimeError(f"terminal picker failed: {exc}") from exc
    return state["selected"], state["favorite"], state["copy"], state.get("multi", False)


def show_text(title: str, text: str) -> None:
    """Show searchable text lines in the TUI, or print them when output is redirected.

    :param title: Viewer heading.
    :param text: Plain text to display; individual lines can be copied using Shift+C.
    """
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print(text)
        return
    rows = [[str(number), line.expandtabs(4)] for number, line in enumerate(text.splitlines(), 1)]
    pick(title, ["Line", "Text"], rows or [["1", "(empty)"]], viewer=True)


def _clip(value: str, width: int) -> str:
    """Pad a value to a column width or truncate it with an ellipsis.

    :param value: Cell text to fit.
    :param width: Target character width.
    :returns: The padded or truncated cell text.
    """
    value = " ".join(str(value).split())
    if len(value) <= width:
        return value.ljust(width)
    return value[:max(0, width - 1)] + "…"


def _put(screen: Any, y: int, x: int, value: str, width: int, attr: int = 0) -> None:
    """Write within the screen bounds, ignoring curses drawing errors.

    :param screen: Curses window receiving the text.
    :param y: Zero-based screen row.
    :param x: Zero-based screen column.
    :param value: Text to draw.
    :param width: Maximum number of characters to write.
    :param attr: Curses display attributes.
    """
    if y < 0 or x >= screen.getmaxyx()[1] or width <= 0:
        return
    try:
        screen.addnstr(y, x, value, max(0, min(width, screen.getmaxyx()[1] - x - 1)), attr)
    except curses.error:
        pass


def confirm(prompt: str, yes: bool = False) -> bool:
    """Ask for confirmation unless the caller has already supplied approval.

    :param prompt: Question displayed before the default-no response hint.
    :param yes: Whether to approve immediately without reading input.
    :returns: Whether approval was supplied or the user answered yes.
    :raises RuntimeError: Input is not a terminal and approval was not supplied.
    """
    if yes:
        return True
    if not sys.stdin.isatty():
        raise RuntimeError("cannot request confirmation without a terminal; add --yes")
    return input(f"{prompt} [y/N]: ").strip().casefold() in {"y", "yes"}


def human_size(size: int) -> str:
    """Format a byte count using binary units through TiB.

    :param size: Size in bytes.
    :returns: A byte count or a value with one decimal place in KiB, MiB, GiB, or TiB.
    """
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
