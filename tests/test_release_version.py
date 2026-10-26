"""The version is the same everywhere, and the release workflow is built on that."""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("check_version", ROOT / "packaging" / "check_version.py")
cv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cv)


def fake_repo(tmp_path: Path, project: str, release: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{project}"\n')
    meta = tmp_path / cv.METAINFO
    meta.parent.mkdir(parents=True)
    meta.write_text(f'<component><releases><release version="{release}"/>'
                    '<release version="0.0.1"/></releases></component>')
    return tmp_path


def test_this_repository_writes_its_version_in_step() -> None:
    """A release needs pyproject.toml and the Flatpak's metainfo to say the same."""
    version, problems = cv.check(ROOT)
    assert problems == [], problems
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)


def test_the_tag_of_a_release_must_be_v_plus_the_version() -> None:
    version, _ = cv.check(ROOT)
    assert cv.check(ROOT, f"v{version}")[1] == []
    assert cv.check(ROOT, version)[1] != []                      # a tag without the v
    assert "the tag is v9.9.9" in cv.check(ROOT, "v9.9.9")[1][0]


def test_a_metainfo_that_was_not_updated_is_reported(tmp_path) -> None:
    version, problems = cv.check(fake_repo(tmp_path, "0.5.0", "0.4.1"))
    assert version == "0.5.0" and len(problems) == 1 and "0.4.1" in problems[0]


def test_only_the_newest_release_entry_counts(tmp_path) -> None:
    assert cv.check(fake_repo(tmp_path, "0.5.0", "0.5.0"))[1] == []


def test_the_command_prints_the_version_or_fails(capsys) -> None:
    version, _ = cv.check(ROOT)
    assert cv.main([]) == 0 and capsys.readouterr().out.strip() == version
    assert cv.main([""]) == 0                                     # a manual run has no tag
    assert cv.main(["v0.0.0"]) == 1 and "the tag is v0.0.0" in capsys.readouterr().err


# ---- the workflow ----

WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
TEXT = (ROOT / ".github/workflows/release.yml").read_text()


def test_a_version_tag_starts_the_release_and_it_can_be_tried_by_hand() -> None:
    triggers = WORKFLOW[True]                                     # YAML reads the key "on" as True
    assert triggers["push"] == {"tags": ["v*"]}
    assert "workflow_dispatch" in triggers


def test_only_the_publishing_job_can_write_and_only_for_a_tag() -> None:
    assert WORKFLOW["permissions"] == {"contents": "read"}
    publish = WORKFLOW["jobs"]["publish"]
    assert publish["permissions"] == {"contents": "write"}
    assert publish["if"] == "github.ref_type == 'tag'"
    assert set(publish["needs"]) == {"check", "dist", "flatpak"}
    assert all("permissions" not in job for name, job in WORKFLOW["jobs"].items() if name != "publish")


def test_everything_is_built_after_the_versions_were_checked() -> None:
    for name in ("dist", "flatpak"):
        assert WORKFLOW["jobs"][name]["needs"] == "check"
    assert "packaging/check_version.py" in TEXT


def test_the_workflow_installs_the_runtime_the_manifest_asks_for() -> None:
    manifest = (ROOT / "packaging/flatpak/io.github.jpbarraca.katatone.yml").read_text()
    runtime = re.search(r"^runtime-version: '(\d+)'", manifest, re.MULTILINE).group(1)
    assert f"org.gnome.Platform//{runtime}" in TEXT and f"org.gnome.Sdk//{runtime}" in TEXT


def test_the_files_are_named_as_the_docs_say() -> None:
    assert 'katatone-$VERSION-x86_64.flatpak"' in TEXT
    assert re.search(r"katatone-[\d.]+-x86_64\.flatpak", (ROOT / "docs/flatpak.md").read_text())
    notes = (ROOT / "packaging/release-notes.md").read_text()
    assert notes.count("@VERSION@") >= 3 and "x86_64.flatpak.sha256" in notes
    assert "@VERSION@" in TEXT                                    # the workflow fills the template in
