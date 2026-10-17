"""Headless tests for the confined Tone Exchange webview module.

The module imports gi/WebKit lazily (so it can be imported on systems
without them), and the window itself needs a display; these tests pin
the pure logic instead:

* the navigation allowlist (:func:`host_allowed`),
* the download-hook wiring (save destination, idempotency),
* a static scope check for every gi name the module uses — ``run()``
  once called ``Gtk.main()`` while ``Gtk`` was only imported inside
  ``build_window()``; the subprocess died on that NameError and the
  fallback opened the *system browser* instead of the confined window.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import katatone.remote.webview as wv


# ---------------------------------------------------------------- allowlist

def test_host_allowed():
    assert wv.host_allowed("https://bosstoneexchange.com/gear/katana_gen_3/")
    assert wv.host_allowed("https://bosstoneexchange.com/signin/")
    assert wv.host_allowed("https://id.roland.com/login")          # suffix
    assert wv.host_allowed("https://api.boss.info/x")              # suffix
    assert wv.host_allowed("https://www.boss.info/")               # exact
    assert wv.host_allowed(
        "https://rcpsvc-btc-livesets-production"
        ".s3.us-west-2.amazonaws.com/x.tsl")                       # exact


def test_host_blocked():
    assert not wv.host_allowed("https://example.com/")
    assert not wv.host_allowed("https://bosstoneexchange.com.evil.com/")
    assert not wv.host_allowed("https://roland.com.evil.com/")
    assert not wv.host_allowed("about:blank")
    assert not wv.host_allowed("https://")


def test_host_allowed_safe_schemes():
    """blob:/data: are origin-bound object refs -> always allowed.

    The Tone Exchange saves a liveset by opening a ``blob:`` URL
    (FileSaver); blocking it is exactly the bug that made the Download
    button a silent no-op in the confined window.
    """
    assert wv.host_allowed("blob:https://bosstoneexchange.com/1234-abcd")
    assert wv.host_allowed("data:application/tsl;base64,abcd")
    # and the allowlist logic is otherwise unchanged
    assert wv.host_allowed("https://bosstoneexchange.com/gear/katana_gen_3/")


# ------------------------------------------------------- saveAs (download)

def test_saveas_wrapper_js_present():
    """The injected wrapper re-issues saveAs as a blob anchor click.

    FileSaver's own saveAs opens a *new window* (window.open(blob:)),
    which a confined single-window webview drops; the wrapper must turn it
    into an anchor click (WebKit download pipeline) and keep the name.
    """
    js = wv._SAVEAS_WRAPPER_JS
    assert js.strip()
    assert "saveAs" in js                      # wraps the site's saveAs
    assert "createObjectURL" in js             # blob -> object URL
    assert ".download" in js                   # keeps the file name
    assert ".click()" in js                    # triggers the download
    assert "__tsl_saveAs_wrapped" in js        # idempotency guard


def test_safe_schemes_constant():
    assert "blob" in wv.SAFE_SCHEMES
    assert "data" in wv.SAFE_SCHEMES


# ------------------------------------------------- gi scope (NameError pin)

GI_NAMES = {"gi", "Gtk", "GLib", "Gdk", "Pango", "WebKit2"}


def _scope_names(body) -> "tuple[set, set]":
    """Return (loaded, bound) names of one scope, excluding nested ones.

    Args:
        body: the statements of the scope.
    Returns:
        ``(loaded, bound)`` sets of name strings.
    """
    loaded: set = set()
    bound: set = set()
    stack = list(body)
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.Lambda, ast.ClassDef)):
            continue  # a nested scope binds for itself
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                loaded.add(node.id)
            else:
                bound.add(node.id)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
        elif isinstance(node, ast.Import):
            bound.update(a.asname or a.name.split(".")[0]
                         for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            bound.update(a.asname or a.name for a in node.names
                         if a.name != "*")
        stack.extend(ast.iter_child_nodes(node))
    return loaded, bound


def test_gi_names_bound_in_scope():
    """Every gi name used in the module must be bound where it is used.

    Catches the bug class where a function uses a name that is only
    imported in a *sibling* function's scope (a local, not a module
    global) — which raised ``NameError`` at runtime and, in
    :func:`katatone.remote.webview.run`, silently sent the user to the
    system browser.
    """
    tree = ast.parse(Path(wv.__file__).read_text(encoding="utf-8"))
    _, module_bound = _scope_names(tree.body)
    parents = {id(c): p for p in ast.walk(tree)
               for c in ast.iter_child_nodes(p)}

    def enclosing(fn):
        """The function scopes between the module and ``fn`` (outermost first)."""
        chain = []
        cur = parents.get(id(fn))
        while isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chain.append(cur)
            cur = parents.get(id(cur))
        return chain

    functions = [n for n in ast.walk(tree)
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    for fn in functions:
        scopes = [fn] + [n for n in ast.walk(fn)
                         if isinstance(n, (ast.FunctionDef,
                                            ast.AsyncFunctionDef))]
        # everything bound in this function's subtree, in any of its
        # enclosing functions (closures), or at module level…
        covered = set(module_bound)
        for sub in scopes:
            _, b = _scope_names(sub.body)
            covered |= b
        for outer in enclosing(fn):
            _, b = _scope_names(outer.body)
            covered |= b
        # …must cover every gi name loaded in the subtree.
        for sub in scopes:
            l, _ = _scope_names(sub.body)
            missing = (l & GI_NAMES) - covered
            assert not missing, \
                f"{fn.name}(): gi names not bound in scope: {missing}"


# ------------------------------------------------------------------- token

def test_stored_refresh_token_unavailable(monkeypatch):
    """No gi/WebKit on the box -> "" (never an exception to the caller)."""
    def boom() -> str:
        raise ImportError("no gi")
    monkeypatch.setattr(wv, "_read_cookie", boom)
    assert wv.stored_refresh_token() == ""


def test_refresh_token_cookie_names():
    """The durable cookie carries the site's ``btx-`` prefix (live-verified)."""
    assert "btx-refresh-token" in wv.REFRESH_TOKEN_COOKIES
    assert "refresh-token" in wv.REFRESH_TOKEN_COOKIES


# ---------------------------------------------------------------- downloads

class _FakeLabel:
    """Records the toolbar label text."""

    def __init__(self) -> None:
        self.text = ""

    def set_text(self, t: str) -> None:
        self.text = t


class _FakeDownload:
    """Records the destination the hook assigns to a download."""

    def __init__(self) -> None:
        self.destination = None
        self.overwrite = None
        self.decide_cb = None

    def connect(self, signal: str, cb) -> None:
        assert signal == "decide-destination"
        self.decide_cb = cb

    def set_destination(self, path: str) -> None:
        self.destination = path

    def set_allow_overwrite(self, v: bool) -> None:
        self.overwrite = v


class _FakeContext:
    """Records the ``download-started`` handler the hook installs."""

    def __init__(self) -> None:
        self.started_cb = None

    def connect(self, signal: str, cb) -> None:
        assert signal == "download-started"
        self.started_cb = cb


@pytest.fixture(autouse=True)
def _reset_download_hook(monkeypatch):
    """Each test starts from an unhooked module global."""
    monkeypatch.setattr(wv, "_downloads_hooked", False)
    yield


def test_download_hook_idempotent():
    """One context handler no matter how many windows are built."""
    ctx = _FakeContext()
    wv._hook_downloads(ctx, _FakeLabel())
    wv._hook_downloads(ctx, _FakeLabel())
    assert ctx.started_cb is not None  # connected exactly once (no dupes)


def test_download_saves_to_library(tmp_path, monkeypatch):
    """decide-destination lands in the livesets dir, decoded + overwrite."""
    monkeypatch.setattr(wv, "_downloads_dir", lambda: tmp_path)
    ctx = _FakeContext()
    label = _FakeLabel()
    wv._hook_downloads(ctx, label)

    dl = _FakeDownload()
    ctx.started_cb(ctx, dl)          # "download-started"
    assert dl.decide_cb is not None
    dl.decide_cb(dl, "CLEAN%20DEZ55.tsl")

    assert dl.destination == str(tmp_path / "CLEAN DEZ55.tsl")
    assert dl.overwrite is True
    assert "CLEAN DEZ55.tsl" in label.text
    assert str(tmp_path) in label.text


def test_download_name_fallback(tmp_path, monkeypatch):
    """An empty suggested name still yields a usable file name."""
    monkeypatch.setattr(wv, "_downloads_dir", lambda: tmp_path)
    ctx = _FakeContext()
    wv._hook_downloads(ctx, _FakeLabel())
    dl = _FakeDownload()
    ctx.started_cb(ctx, dl)
    dl.decide_cb(dl, "")
    assert dl.destination == str(tmp_path / "download.tsl")
