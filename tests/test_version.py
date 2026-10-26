import re
import subprocess

import katatone


def test_version_is_a_string():
    assert isinstance(katatone.__version__, str) and katatone.__version__


def test_untagged_checkout_shows_commit_date(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    (root / "katatone").mkdir(parents=True)
    git = lambda *a: subprocess.run(["git", "-C", str(root), *a], check=True,
                                    capture_output=True)
    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
        "--allow-empty", "-m", "x")
    monkeypatch.setattr(katatone, "__file__", str(root / "katatone" / "__init__.py"))
    assert re.search(r"\(\d{4}-\d{2}-\d{2}\)$", katatone._version())
    git("tag", "v9.9.9")
    assert katatone._version() == "9.9.9"
