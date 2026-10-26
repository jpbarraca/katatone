"""Backing up the whole amp to a file and restoring it."""
from __future__ import annotations

import json

import pytest

from katatone.amp import backup as bk
from katatone.liveset.tsl import NUM_PATCHES
from katatone.model.paramset import ParamSet


def make_backup() -> bk.Backup:
    patches = []
    for n in range(NUM_PATCHES):
        ps = ParamSet()
        ps.set_block("PATCH%COM", ParamSet.encode_patch_name(f"Tone {n + 1}").ljust(16, b" "))
        ps.set_block("PATCH%AMP", bytes([n] * 4))
        patches.append(ps)
    system = ParamSet()
    system.set_block("SYSTEM%GLOBAL", b"\x01\x02\x03")
    return bk.Backup(patches, system)


def test_a_backup_survives_a_file(tmp_path) -> None:
    b = make_backup()
    path = tmp_path / "amp.ktbackup"
    b.save(path)
    again = bk.Backup.load(path)
    assert [ps.data for ps in again.patches] == [ps.data for ps in b.patches]
    assert again.system.data == b.system.data
    assert again.names()[:2] == ["Tone 1", "Tone 2"]
    assert again.created == b.created and again.version == b.version


@pytest.mark.parametrize("change", [
    lambda o: o.update(format="something else"),
    lambda o: o.update(formatRev=99),
    lambda o: o.update(device="KATANA 100"),
    lambda o: o.update(patches=o["patches"][:3]),
    lambda o: o.update(system={}),
    lambda o: o["patches"].__setitem__(4, {}),
    lambda o: o["system"].update({"SYSTEM%GLOBAL": ["zz"]}),
])
def test_a_wrong_file_is_refused(change) -> None:
    obj = json.loads(make_backup().to_json())
    change(obj)
    with pytest.raises(bk.BackupError):
        bk.Backup.from_json(json.dumps(obj))


@pytest.mark.parametrize("text", ["", "not json", "[]", "7"])
def test_text_that_is_not_a_backup_is_refused(text) -> None:
    with pytest.raises(bk.BackupError):
        bk.Backup.from_json(text)


def test_read_backup_reads_every_slot_and_the_system(monkeypatch) -> None:
    calls, steps = [], []
    monkeypatch.setattr(bk, "read_patch", lambda t, n, progress=None: (calls.append(("patch", n)), make_backup().patches[n])[1])
    monkeypatch.setattr(bk, "read_region", lambda t, r, progress=None: (calls.append((r,)), make_backup().system)[1])
    b = bk.read_backup(object(), progress=lambda i, n, text: steps.append((i, n)))
    assert calls == [("patch", n) for n in range(10)] + [("SYSTEM",)]
    assert len(b.patches) == 10 and b.system.block("SYSTEM%GLOBAL")
    assert steps[0] == (0, 11) and steps[-1] == (11, 11)


def test_write_backup_writes_all_then_puts_the_edit_buffer_back(monkeypatch) -> None:
    steps = []
    keep = ParamSet()
    monkeypatch.setattr(bk, "read_region", lambda t, r, progress=None: (steps.append(("read", r)), keep)[1])
    monkeypatch.setattr(bk, "write_patch", lambda t, n, ps, name=None: steps.append(("patch", n)))
    monkeypatch.setattr(bk, "write_system", lambda t, ps: steps.append(("system",)))
    monkeypatch.setattr(bk, "put_back_edit_buffer", lambda t, k: steps.append(("back", k is keep)))
    bk.write_backup(object(), make_backup())
    assert steps == ([("read", "PATCH")] + [("patch", n) for n in range(10)]
                     + [("system",), ("back", True)])


def test_the_edit_buffer_goes_back_when_a_write_fails(monkeypatch) -> None:
    steps = []
    monkeypatch.setattr(bk, "read_region", lambda t, r, progress=None: ParamSet())

    def fail(t, n, ps, name=None):
        if n == 3:
            raise OSError("link lost")
    monkeypatch.setattr(bk, "write_patch", fail)
    monkeypatch.setattr(bk, "put_back_edit_buffer", lambda t, k: steps.append("back"))
    with pytest.raises(OSError):
        bk.write_backup(object(), make_backup())
    assert steps == ["back"]

