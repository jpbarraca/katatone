"""Default data locations resolve (no explicit directory given)."""
from __future__ import annotations

from katatone import paths
from katatone.library.store import Library
from katatone.midi.store import ProfileStore, default_dir


def _point_at(tmp_path, monkeypatch):
    monkeypatch.setattr("platformdirs.user_data_dir",
                        lambda name: str(tmp_path / name))


def test_midi_profiles_default_to_the_data_dir(tmp_path, monkeypatch) -> None:
    _point_at(tmp_path, monkeypatch)
    assert default_dir() == tmp_path / "katatone" / "midi"
    assert ProfileStore().root == tmp_path / "katatone" / "midi"


def test_library_defaults_to_the_data_dir(tmp_path, monkeypatch) -> None:
    _point_at(tmp_path, monkeypatch)
    assert Library().root == tmp_path / "katatone" / "livesets"


def test_webview_data_dir_is_created(tmp_path, monkeypatch) -> None:
    from katatone.remote import webview
    _point_at(tmp_path, monkeypatch)
    assert webview.data_dir() == paths.data_root() == tmp_path / "katatone"
    assert (tmp_path / "katatone").is_dir()
