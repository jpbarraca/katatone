"""File choosing and saving on the desktop and in a browser (fake picker)."""
from __future__ import annotations

import asyncio
import types

from katatone.gui import files


class _Picker:
    def __init__(self, picked=None, chosen="/x/out.tsl"):
        self.picked, self.chosen, self.kw = picked or [], chosen, {}

    async def pick_files(self, **kw):
        self.kw = kw
        return self.picked

    async def save_file(self, **kw):
        self.kw = kw
        return self.chosen


def run(coro):
    return asyncio.run(coro)


def test_desktop_pick_returns_the_paths() -> None:
    picker = _Picker([types.SimpleNamespace(path="/home/a.tsl", name="a.tsl", bytes=None)])
    out = run(files.pick_files(types.SimpleNamespace(web=False), picker, "t", "tsl"))
    assert [str(p) for p in out] == ["/home/a.tsl"] and picker.kw["with_data"] is False


def test_browser_pick_writes_the_uploaded_bytes_to_a_temp_file() -> None:
    picker = _Picker([types.SimpleNamespace(path=None, name="../evil/b.tsl", bytes=b"DATA")])
    (out,) = run(files.pick_files(types.SimpleNamespace(web=True), picker, "t", "tsl"))
    assert out.name == "b.tsl" and out.read_bytes() == b"DATA"     # no path tricks
    assert picker.kw["with_data"] is True


def test_cancelled_pick_is_empty() -> None:
    assert run(files.pick_files(types.SimpleNamespace(web=True), _Picker([]), "t", "tsl")) == []


def test_browser_save_hands_the_bytes_to_the_browser() -> None:
    picker = _Picker(chosen="")
    where = run(files.save_bytes(types.SimpleNamespace(web=True), picker, "t", "m.tsl", "tsl", b"X"))
    assert where == "downloaded by the browser" and picker.kw["src_bytes"] == b"X"


def test_desktop_save_writes_the_file_and_adds_the_extension(tmp_path) -> None:
    picker = _Picker(chosen=str(tmp_path / "out"))
    where = run(files.save_bytes(types.SimpleNamespace(web=False), picker, "t", "m.tsl", "tsl", b"X"))
    assert where == str(tmp_path / "out.tsl") and (tmp_path / "out.tsl").read_bytes() == b"X"


def test_desktop_save_cancelled_returns_none() -> None:
    picker = _Picker(chosen=None)
    assert run(files.save_bytes(types.SimpleNamespace(web=False), picker, "t", "m.tsl", "tsl", b"X")) is None
