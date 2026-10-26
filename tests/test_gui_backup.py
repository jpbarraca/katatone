"""Backing up and restoring the amp from the GUI (the amp is faked)."""
from __future__ import annotations

import asyncio

import flet.controls.base_control as bc
import pytest

from katatone.amp import backup as bk
from katatone.gui import app as app_mod
from katatone.gui import files, library_pane
from test_backup import make_backup
from test_gui_resync import StubPage


@pytest.fixture
def app(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("KATATONE_NO_BLUETOOTH", "1")
    monkeypatch.setattr(bc.BaseControl, "update", lambda self: None)
    page = StubPage()
    page.tasks = []
    page.run_task = lambda fn, *a: page.tasks.append((fn, a))
    a = app_mod.App(page, start_midi=False)
    a.connected, a.transport = True, object()
    a.log = []
    monkeypatch.setattr(bk, "read_backup", lambda t, progress=None: a.log.append("read") or make_backup())
    monkeypatch.setattr(bk, "write_backup", lambda t, b, progress=None: a.log.append("write"))
    monkeypatch.setattr(a, "read_patch", lambda n: a.log.append(("reread", n)))
    return a


def test_backup_hands_the_read_backup_to_the_page(app) -> None:
    async def offer(b):
        pass
    app.backup_amp(offer)
    app.page.join()
    assert app.log == ["read"] and not app.busy
    (fn, (backup,)), = app.page.tasks
    assert fn is offer and backup.names()[0] == "Tone 1"


def test_restore_keeps_a_copy_writes_and_rereads_the_slot(app, tmp_path) -> None:
    backup = make_backup()
    app.restore_amp(backup)
    app.page.join()
    assert app.log == ["read", "write", ("reread", app.patch_number)]
    assert app.system_ps is backup.system
    assert list((tmp_path / "katatone" / "backups").glob("before-restore-*"))
    assert app.last_status.startswith("Restored the amp")
    assert not app.busy_writing


def test_a_failed_restore_says_so_and_does_not_reread(app, monkeypatch) -> None:
    def boom(t, b, progress=None):
        raise OSError("link lost")
    monkeypatch.setattr(bk, "write_backup", boom)
    app.restore_amp(make_backup())
    app.page.join()
    assert app.last_status == "Restore failed: link lost"
    assert ("reread", app.patch_number) not in app.log and not app.busy_writing


def test_nothing_happens_when_not_connected(app) -> None:
    app.connected = False
    app.restore_amp(make_backup())
    app.backup_amp(lambda b: None)
    app.page.join()
    assert app.log == [] and app.last_status == "Not connected."


def pane(app):
    return library_pane._LibraryPane(app)


def test_restore_asks_before_overwriting(app, monkeypatch, tmp_path) -> None:
    path = tmp_path / "amp.ktbackup"
    make_backup().save(path)

    async def pick(page, picker, title, ext, multiple=False):
        return [path]
    asked = []
    monkeypatch.setattr(files, "pick_files", pick)
    monkeypatch.setattr(library_pane.dialogs, "confirm",
                        lambda page, text, on_ok, **kw: asked.append((text, on_ok)))
    asyncio.run(pane(app)._restore(None))
    (text, on_ok), = asked
    assert "Tone 1" in text and "Tone 10" in text and app.log == []     # nothing written yet
    on_ok()
    app.page.join()
    assert "write" in app.log


def test_restore_refuses_a_file_that_is_not_a_backup(app, monkeypatch, tmp_path) -> None:
    bad = tmp_path / "bad.ktbackup"
    bad.write_text("{}")

    async def pick(page, picker, title, ext, multiple=False):
        return [bad]
    monkeypatch.setattr(files, "pick_files", pick)
    monkeypatch.setattr(library_pane.dialogs, "confirm", lambda *a, **k: pytest.fail("asked"))
    asyncio.run(pane(app)._restore(None))
    assert app.last_status.startswith("Cannot restore bad.ktbackup")
    assert app.log == []


def test_the_backup_is_offered_as_a_file(app, monkeypatch) -> None:
    saved = []

    async def save(page, picker, title, file_name, ext, data):
        saved.append((file_name, ext, data))
        return "/tmp/x"
    monkeypatch.setattr(files, "save_bytes", save)
    asyncio.run(pane(app)._offer_backup(make_backup()))
    (name, ext, data), = saved
    assert name.endswith(".ktbackup") and ext == "ktbackup"
    assert bk.Backup.from_json(data.decode()).names()[0] == "Tone 1"
    assert app.last_status == "Backed up the amp -> /tmp/x"
