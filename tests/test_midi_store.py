"""Profile store round trips in a temp directory."""
from __future__ import annotations

import json

import pytest

from katatone.midi.mapping import ChannelSelect, Mapping, Profile, Trigger
from katatone.midi.store import DEFAULT_NAME, ProfileStore


def profile(name: str = "Stage") -> Profile:
    return Profile(name=name, devices=["SINCO MIDI 1"], mappings=[
        Mapping(Trigger("SINCO", 0, "pc", 2), ChannelSelect(5), label="B1")])


def test_default_profile_created_on_first_use(tmp_path) -> None:
    """active() creates an empty Default and makes it active."""
    s = ProfileStore(tmp_path)
    assert s.list() == []
    assert s.active() == DEFAULT_NAME
    assert s.list() == [DEFAULT_NAME]
    assert s.load(DEFAULT_NAME).mappings == []
    assert (tmp_path / "active.txt").read_text().strip() == DEFAULT_NAME


def test_save_load_round_trip_and_atomic(tmp_path) -> None:
    """A saved profile loads back equal, and no .tmp file is left behind."""
    s = ProfileStore(tmp_path)
    p = profile()
    s.save(p)
    assert s.load("Stage") == p
    assert sorted(f.name for f in tmp_path.iterdir()) == ["Stage.json"]
    assert json.loads((tmp_path / "Stage.json").read_text())["version"] == 1


def test_active_survives_a_new_store_instance(tmp_path) -> None:
    """The active name is persisted (an app restart)."""
    s = ProfileStore(tmp_path)
    s.save(profile())
    s.set_active("Stage")
    assert ProfileStore(tmp_path).active() == "Stage"


def test_missing_active_falls_back_to_default(tmp_path) -> None:
    """If the active profile was deleted by hand, Default takes over."""
    s = ProfileStore(tmp_path)
    s.save(profile())
    s.set_active("Stage")
    (tmp_path / "Stage.json").unlink()
    assert s.active() == DEFAULT_NAME


def test_delete_active_resets_to_default(tmp_path) -> None:
    """Deleting the active profile does not leave a dangling active.txt."""
    s = ProfileStore(tmp_path)
    s.save(profile())
    s.set_active("Stage")
    s.delete("Stage")
    assert "Stage" not in s.list()
    assert s.active() == DEFAULT_NAME
    s.delete("never existed")             # not an error


def test_rename_moves_the_file_and_keeps_active(tmp_path) -> None:
    """The old file goes away, the new loads with the new name, active follows."""
    s = ProfileStore(tmp_path)
    s.save(profile())
    s.set_active("Stage")
    s.rename("Stage", "Gig")
    assert s.list() == ["Gig"]
    assert s.load("Gig").name == "Gig" and s.load("Gig").mappings
    assert s.active() == "Gig"
    s.save(profile("Other"))
    with pytest.raises(FileExistsError):
        s.rename("Other", "Gig")
    with pytest.raises(FileNotFoundError):
        s.rename("nope", "x")


def test_duplicate_gives_independent_mapping_ids(tmp_path) -> None:
    """A copy has the same mappings but new ids."""
    s = ProfileStore(tmp_path)
    p = profile()
    s.save(p)
    q = s.duplicate("Stage", "Stage copy")
    assert q.mappings[0].trigger == p.mappings[0].trigger
    assert q.mappings[0].id != p.mappings[0].id
    with pytest.raises(FileExistsError):
        s.duplicate("Stage", "Stage copy")


def test_names_are_made_file_safe(tmp_path) -> None:
    """A name with path separators cannot escape the directory."""
    s = ProfileStore(tmp_path)
    s.save(profile("../evil/name"))
    assert [f.parent for f in tmp_path.glob("**/*.json")] == [tmp_path]
    assert s.list() == ["-evil-name"]


def test_import_export_and_name_clash(tmp_path) -> None:
    """Export writes plain JSON; importing it twice yields 'Stage 2'."""
    s = ProfileStore(tmp_path / "store")
    s.save(profile())
    out = tmp_path / "export.json"
    s.export_file("Stage", out)
    assert json.loads(out.read_text())["name"] == "Stage"
    a = s.import_file(out)
    b = s.import_file(out)
    assert (a.name, b.name) == ("Stage 2", "Stage 3")
    assert s.load("Stage 2").mappings[0].action == ChannelSelect(5)


def test_import_rejects_garbage(tmp_path) -> None:
    """Not JSON, wrong shape and newer versions raise ValueError."""
    s = ProfileStore(tmp_path / "store")
    bad = tmp_path / "bad.json"
    for text in ("not json", "[1, 2]", '{"version": 99}'):
        bad.write_text(text)
        with pytest.raises(ValueError):
            s.import_file(bad)
    assert s.list() == []


def test_corrupt_profile_file_reports_cleanly(tmp_path) -> None:
    """A hand-damaged file is a ValueError naming the file, not a traceback soup."""
    s = ProfileStore(tmp_path)
    (tmp_path / "Broken.json").write_text("{ nope")
    with pytest.raises(ValueError, match="Broken.json"):
        s.load("Broken")
