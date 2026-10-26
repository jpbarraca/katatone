"""Tags, favourites and search in the library."""
from __future__ import annotations

import json

import pytest

from katatone.library.store import (MAX_TAGS, TAG_MAX, Library, all_tags, clean_tags,
                                    filter_entries)
from katatone.liveset.tsl import Tsl
from katatone.model.paramset import ParamSet


def liveset(lib: Library, name: str, *patch_names: str) -> None:
    tsl = Tsl(name=name)
    for n in patch_names:
        ps = ParamSet()
        ps.set_block("PATCH%COM", ParamSet.encode_patch_name(n).ljust(16))
        tsl.add(Tsl.encode(ps))
    lib.save(tsl)


@pytest.fixture
def lib(tmp_path):
    lib = Library(tmp_path)
    liveset(lib, "Blues", "Crunch", "Clean Spank")
    liveset(lib, "Metal", "Brutal", "Djent Lead")
    liveset(lib, "Ambient", "Shimmer")
    return lib


def names(entries) -> list[str]:
    return [e.name for e in entries]


def test_entries_carry_their_patch_names(lib) -> None:
    assert {e.name: e.patches for e in lib.list()}["Blues"] == ["Crunch", "Clean Spank"]


def test_tags_and_favourites_are_kept_in_a_file_beside_the_livesets(lib, tmp_path) -> None:
    lib.set_tags("Blues", "gig, Warm ,gig")
    lib.set_favourite("Metal", True)
    again = {e.name: e for e in Library(tmp_path).list()}
    assert again["Blues"].tags == ["gig", "Warm"] and not again["Blues"].favourite
    assert again["Metal"].favourite and again["Metal"].tags == []
    assert set(json.loads((tmp_path / "library.json").read_text())) == {"Blues", "Metal"}


def test_clean_tags_limits_them() -> None:
    assert clean_tags(["a", "A", " b  c ", ""]) == ["a", "b c"]
    assert len(clean_tags([f"t{i}" for i in range(30)])) == MAX_TAGS
    assert len(clean_tags(["x" * 100])[0]) == TAG_MAX


def test_a_damaged_file_means_no_tags(lib, tmp_path) -> None:
    (tmp_path / "library.json").write_text("{not json")
    assert all(not e.tags and not e.favourite for e in lib.list())
    (tmp_path / "library.json").write_text("[1]")
    assert all(not e.tags for e in lib.list())


def test_rename_remove_and_duplicate_follow_the_tags(lib) -> None:
    lib.set_tags("Blues", "gig")
    lib.set_favourite("Blues", True)
    lib.rename("Blues", "Blues 2")
    e = {e.name: e for e in lib.list()}["Blues 2"]
    assert e.tags == ["gig"] and e.favourite
    copy = lib.duplicate("Blues 2")
    c = {e.name: e for e in lib.list()}[copy.name]
    assert c.tags == ["gig"] and not c.favourite
    lib.remove("Blues 2")
    assert "Blues 2" not in lib._read_meta()


def test_search_matches_liveset_tag_and_patch_names(lib) -> None:
    lib.set_tags("Ambient", "pad")
    entries = lib.list()
    assert names(filter_entries(entries)) == ["Ambient", "Blues", "Metal"]
    assert names(filter_entries(entries, "metal")) == ["Metal"]
    assert names(filter_entries(entries, "CRUNCH")) == ["Blues"]       # a patch name
    assert names(filter_entries(entries, "pad")) == ["Ambient"]        # a tag
    assert names(filter_entries(entries, "djent lead")) == ["Metal"]
    assert names(filter_entries(entries, "djent crunch")) == []        # all words must match


def test_filter_by_tag_and_favourites_and_favourites_come_first(lib) -> None:
    lib.set_tags("Blues", "gig")
    lib.set_tags("Metal", "GIG, loud")
    lib.set_favourite("Metal", True)
    entries = lib.list()
    assert names(filter_entries(entries, tag="gig")) == ["Metal", "Blues"]
    assert names(filter_entries(entries, favourites_only=True)) == ["Metal"]
    assert names(filter_entries(entries)) == ["Metal", "Ambient", "Blues"]
    assert all_tags(entries) == ["gig", "loud"]
