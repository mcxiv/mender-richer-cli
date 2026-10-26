"""Headless rendering and keyboard interaction tests for the shared terminal UI."""

import base64
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import display


@pytest.fixture
def screen(monkeypatch):
    class CursesError(Exception):
        pass
    keys = ['KEY_UP', 'KEY_DOWN', 'KEY_PPAGE', 'KEY_NPAGE', 'KEY_HOME', 'KEY_END',
            'KEY_BACKSPACE', 'KEY_SF', 'KEY_ENTER', 'KEY_LEFT', 'KEY_RIGHT']
    fake = SimpleNamespace(error=CursesError, A_NORMAL=0, A_BOLD=1, A_DIM=2, A_REVERSE=4,
                           **{key: 1000 + index for index, key in enumerate(keys)})
    fake.curs_set = Mock()
    fake.use_default_colors = Mock()
    output = Mock()
    output.getmaxyx.return_value = (16, 60)
    def wrapper(callback):
        callback(output)
    fake.wrapper = wrapper
    monkeypatch.setattr(display, 'curses', fake)
    monkeypatch.setattr(display.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr(display.sys.stdout, 'isatty', lambda: True)
    return SimpleNamespace(window=output, curses=fake)


def test_json_and_tables(capsys):
    display.print_json({'value': 'é'})
    display.table(['Name', 'Value'], [['bench', 12]])
    text = capsys.readouterr().out
    assert 'é' in text and 'bench' in text and '12' in text


def test_inventory_flattening():
    value = {'z': None, 'nested': {'count': 2}, 'list': ['a', 'b'], 'records': [{'key': 1}]}
    assert display.flatten_inventory(value) == [
        ['list', 'a, b'], ['nested.count', '2'], ['records[0].key', '1'], ['z', '—']]


def test_clipboard_sequence(capsys):
    display.copy_terminal('é')
    assert capsys.readouterr().out == '\x1b]52;c;' + base64.b64encode('é'.encode()).decode() + '\a'


@pytest.mark.parametrize(('text', 'expected'), [('2', [1]), ('99', []), ('bad', []), ('', [])])
def test_numbered_picker(monkeypatch, text, expected):
    monkeypatch.setattr('builtins.input', lambda _: text)
    assert display._plain_picker('title', [['one'], ['two']]) == (expected, None, False)


def test_empty_and_missing_curses(monkeypatch):
    assert display.pick('empty', [], []) == ([], -1, None, False)
    monkeypatch.setattr(display, 'curses', None)
    with pytest.raises(RuntimeError, match='windows-curses'):
        display.pick('title', ['Name'], [['one']])


def test_redirected_picker_preserves_favorite(monkeypatch):
    monkeypatch.setattr(display.sys.stdin, 'isatty', lambda: False)
    monkeypatch.setattr(display, '_plain_picker', lambda *_: ([1], None, False))
    assert display.pick('title', ['Name'], [['one'], ['two']], favorite=1) == ([1], 1, None, False)


def test_search_backspace_clear_and_navigation(screen):
    c = screen.curses
    screen.window.get_wch.side_effect = ['z', c.KEY_BACKSPACE, 'x', '\x15', c.KEY_DOWN, c.KEY_UP,
                                         c.KEY_NPAGE, c.KEY_PPAGE, c.KEY_END, c.KEY_HOME, '\n']
    result = display.pick('title', ['Name', 'Value'], [[str(i), 'value'] for i in range(20)])
    assert result == ([0], -1, None, False)
    screen.window.refresh.assert_called()


def test_marking_and_favorite_use_original_indices_after_filtering(screen):
    callback = Mock()
    screen.window.get_wch.side_effect = ['t', 'w', 'o', 'F', ' ', ' ', ' ', '\n']
    result = display.pick('title', ['Name'], [['one'], ['two']], device_mode=True, toggle_favorite=callback)
    assert result == ([1], 1, None, True)
    callback.assert_called_once_with(1)


def test_unfavorite_and_implicit_single_selection(screen):
    screen.window.get_wch.side_effect = ['F', '\r']
    assert display.pick('title', ['Name'], [['one']], device_mode=True, favorite=0)[1] == -1


def test_multi_select_does_not_expose_device_favorites(screen):
    c = screen.curses
    screen.window.get_wch.side_effect = [' ', c.KEY_DOWN, ' ', '\n']
    assert display.pick('title', ['Name'], [['one'], ['two']], multi_select=True) == ([0, 1], -1, None, True)


def test_viewer_scroll_and_copy_retain_complete_value(screen, monkeypatch):
    c = screen.curses
    copied = Mock()
    monkeypatch.setattr(display, 'copy_terminal', copied)
    screen.window.get_wch.side_effect = [c.KEY_RIGHT, c.KEY_RIGHT, c.KEY_LEFT, 'C', '\x1b']
    text = 'a very long line ' * 20
    result = display.pick('title', ['Line', 'Text'], [['1', text]], viewer=True)
    assert result[2] == text
    copied.assert_called_once_with(text)
    assert any(text[8:24] in call.args[2] for call in screen.window.addnstr.call_args_list)


def test_narrow_screen_and_empty_search_matches(screen):
    screen.window.getmaxyx.return_value = (10, 20)
    screen.window.get_wch.side_effect = ['missing', '\x03']
    assert display.pick('title', ['Long header', 'Other header'], [['long cell ' * 10, 'other' * 20]])[0] == []
    assert any('No matching' in call.args[2] for call in screen.window.addnstr.call_args_list) is False


def test_no_matches_are_rendered(screen):
    screen.window.get_wch.side_effect = ['z', '\x1b']
    display.pick('title', ['Name'], [['one']])
    assert any('No matching results' in call.args[2] for call in screen.window.addnstr.call_args_list)


def test_curses_failures_become_actionable_errors(screen):
    screen.window.get_wch.side_effect = screen.curses.error('drawing failed')
    screen.curses.curs_set.side_effect = screen.curses.error()
    screen.curses.use_default_colors.side_effect = screen.curses.error()
    with pytest.raises(RuntimeError, match='terminal picker failed'):
        display.pick('title', ['Name'], [['one']])


def test_clip_and_bounded_drawing(screen):
    assert display._clip('ab', 4) == 'ab  '
    assert display._clip('abcdef', 4) == 'abc…'
    for y, x, width in [(-1, 0, 10), (0, 1000, 10), (0, 0, 0)]:
        display._put(screen.window, y, x, 'text', width)
    screen.window.addnstr.assert_not_called()
    screen.window.addnstr.side_effect = screen.curses.error()
    display._put(screen.window, 0, 0, 'text', 4)


@pytest.mark.parametrize(('answer', 'expected'), [('yes', True), (' Y ', True), ('no', False), ('', False)])
def test_confirmation(monkeypatch, answer, expected):
    monkeypatch.setattr(display.sys.stdin, 'isatty', lambda: True)
    monkeypatch.setattr('builtins.input', lambda _: answer)
    assert display.confirm('Proceed?') is expected


def test_confirmation_without_terminal_requires_yes(monkeypatch):
    monkeypatch.setattr(display.sys.stdin, 'isatty', lambda: False)
    assert display.confirm('Proceed?', True)
    with pytest.raises(RuntimeError, match='--yes'):
        display.confirm('Proceed?')


@pytest.mark.parametrize(('size', 'expected'), [(0, '0 B'), (1023, '1023 B'), (1024, '1.0 KiB'),
    (1024**2, '1.0 MiB'), (1024**3, '1.0 GiB'), (1024**4 * 2, '2.0 TiB')])
def test_human_sizes(size, expected):
    assert display.human_size(size) == expected


def test_text_viewer_redirected_and_empty(screen, monkeypatch, capsys):
    monkeypatch.setattr(display.sys.stdin, 'isatty', lambda: False)
    display.show_text('title', 'raw text')
    assert capsys.readouterr().out == 'raw text\n'
    monkeypatch.setattr(display.sys.stdin, 'isatty', lambda: True)
    screen.window.get_wch.side_effect = ['\x1b']
    display.show_text('title', '')
