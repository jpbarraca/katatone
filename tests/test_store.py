"""Tests for the local library store (filesystem, temp dir)."""
from __future__ import annotations

from katatone.library.store import Library
from katatone.liveset.tsl import Tsl
from katatone.model.paramset import ParamSet


def _make(name: str, idx: int = 0) -> Tsl:
    """Build a one-patch Tsl liveset with a named patch and a seeded AMP block.

    Args:
        name: name of the single patch.
        idx: user patch index the liveset is based on.

    Returns:
        a Tsl with one patch carrying a 3-byte PATCH%AMP block.
    """
    ps = ParamSet()
    ps.set_block("PATCH%AMP", bytes([0x01, 0x02, 0x03]))
    return Tsl.from_paramset(name, idx, ps)


def test_library_save_list_load_remove(tmp_path) -> None:
    """Saving a liveset makes it listable and loadable with its blocks intact, and remove deletes it."""
    lib = Library(root=tmp_path)
    lib.save(_make("Sound A"))
    assert "Sound A" in [e.name for e in lib.list()]

    loaded = lib.load("Sound A")
    assert loaded.name == "Sound A"
    assert loaded.to_paramset(0).block("PATCH%AMP") == bytes([1, 2, 3])

    lib.remove("Sound A")
    assert "Sound A" not in [e.name for e in lib.list()]


def test_import_file_roundtrip(tmp_path) -> None:
    """import_file pulls an external .tsl into the library with its patch data intact."""
    src = tmp_path / "imported.tsl"
    _make("Imported", 2).save(src)

    lib = Library(root=tmp_path)
    lib.import_file(src)
    assert "Imported" in [e.name for e in lib.list()]
    assert lib.load("Imported").to_paramset(2).block("PATCH%AMP") == bytes([1, 2, 3])


def test_create_rename_duplicate_unique_names(tmp_path) -> None:
    """create/duplicate/rename keep library names unique, and renaming onto a taken name raises FileExistsError."""
    lib = Library(root=tmp_path)
    a = lib.create("Gig")
    b = lib.create("Gig")                     # name taken -> "Gig (2)"
    assert (a.name, b.name) == ("Gig", "Gig (2)")
    assert len(lib.load("Gig")) == 0

    tsl = lib.load("Gig")
    tsl.add(_make("x").patches[0])
    lib.save(tsl)
    copy = lib.duplicate("Gig")
    assert copy.name == "Gig copy" and len(lib.load("Gig copy")) == 1

    assert lib.rename("Gig (2)", "Rehearsal") == "Rehearsal"
    names = [e.name for e in lib.list()]
    assert "Gig (2)" not in names and "Rehearsal" in names
    import pytest
    with pytest.raises(FileExistsError):
        lib.rename("Rehearsal", "Gig")
    counts = {e.name: e.count for e in lib.list()}
    assert counts == {"Gig": 1, "Gig copy": 1, "Rehearsal": 0}


def test_names_are_sanitized_and_import_does_not_overwrite(tmp_path) -> None:
    """Names are sanitized for the filesystem, and importing a file whose name exists keeps both entries."""
    lib = Library(root=tmp_path / "lib")
    assert lib.create("a/b").name == "a-b"
    src = tmp_path / "x.tsl"
    _make("a-b").save(src)
    lib.import_file(src)
    assert sorted(e.name for e in lib.list()) == ["a-b", "a-b (2)"]
