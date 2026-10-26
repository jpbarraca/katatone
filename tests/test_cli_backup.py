"""katatone backup / restore on the command line (the amp is faked)."""
from __future__ import annotations

import pytest

from katatone import cli
from katatone.amp import backup as bk
from test_backup import make_backup


@pytest.fixture
def amp(monkeypatch, tmp_path):
    """Fake the link; ``log`` records what is done to the amp."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    log = []
    monkeypatch.setattr(cli, "disconnect", lambda t: log.append("disconnect"))
    monkeypatch.setattr(cli, "read_backup", lambda t, progress=None: log.append("read") or make_backup())
    monkeypatch.setattr(cli, "write_backup", lambda t, b, progress=None: log.append("write"))
    t = type("T", (), {"stop": lambda self: None})()
    monkeypatch.setattr(cli, "_connect", lambda args: log.append("connect") or t)
    return log


def run(*args) -> int:
    return cli._run([args[0], "--in", "A", "--out", "A", *args[1:]])


def test_backup_saves_a_file_that_loads_again(amp, tmp_path, capsys) -> None:
    path = tmp_path / "amp.ktbackup"
    assert run("backup", str(path)) == 0
    assert bk.Backup.load(path).names()[0] == "Tone 1"
    assert amp == ["connect", "read", "disconnect"]


def test_restore_keeps_a_copy_then_writes(amp, tmp_path, capsys) -> None:
    path = tmp_path / "amp.ktbackup"
    make_backup().save(path)
    assert run("restore", str(path), "--yes") == 0
    assert amp == ["connect", "read", "write", "disconnect"]
    kept = list((tmp_path / "data" / "katatone" / "backups").glob("before-restore-*.ktbackup"))
    assert len(kept) == 1 and bk.Backup.load(kept[0]).names()[0] == "Tone 1"
    assert "restored" in capsys.readouterr().out


def test_restore_asks_first_and_stops_on_no(amp, tmp_path, monkeypatch, capsys) -> None:
    path = tmp_path / "amp.ktbackup"
    make_backup().save(path)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert run("restore", str(path)) == 1
    assert amp == []                                    # the amp was never touched
    assert "cancelled" in capsys.readouterr().out


def test_restore_refuses_a_bad_file_before_connecting(amp, tmp_path, capsys) -> None:
    bad = tmp_path / "bad.ktbackup"
    bad.write_text("{}")
    assert run("restore", str(bad), "--yes") == 2
    assert run("restore", str(tmp_path / "missing"), "--yes") == 2
    assert amp == []
