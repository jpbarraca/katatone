"""Confined webview window for the BOSS Tone Exchange (Tone Central).

Flet has no WebView control on Linux, so the GUI's Library tab launches
this module in its own process::

    python3 -m katatone.remote.webview [url]

which draws a GTK/WebKit2 window that may only navigate to the Tone
Exchange site and the Roland/BOSS domains it depends on (login, API,
CDN).  Signing in (BOSS ID), searching the remote library, browsing
and downloading livesets all happen inside the webview, exactly as in a
browser — files the page downloads are saved into the app's livesets
library directory (see :func:`_hook_downloads`).  The app itself never
calls the Tone Exchange API.

The login session is stored in a private cookie database
(``~/.local/share/katatone/boss-id-cookies.sqlite``, mode 0600 —
WebKit 4.1's default context does not persist cookies on its own, so
the cookie manager is explicitly pinned to that file), so it survives
between launches; :func:`stored_refresh_token` only reports whether a
session exists.  ``gi``/WebKit are imported lazily so the module can be
imported (and :data:`DEFAULT_URL` reused) on systems without them.
"""
from __future__ import annotations

import sys
import urllib.parse

#: Page the Katana Gen 3 library opens on by default.
DEFAULT_URL = "https://bosstoneexchange.com/gear/katana_gen_3/"

#: Hosts that the webview may navigate to (exact matches).
ALLOWED_HOSTS = frozenset({
    "bosstoneexchange.com",                                  # the site
    "rcpsvc-btc-livesets-production.s3.us-west-2.amazonaws.com",  # livesets CDN
})

#: Host suffixes the webview may navigate to (login + API + CDN).
ALLOWED_SUFFIXES = (".roland.com", ".boss.info")

#: Origin whose cookie jar holds the BOSS ID session.
SESSION_HOST = "https://bosstoneexchange.com/"

#: Cookie names the Tone Exchange sets on sign-in (its durable
#: credential).  The site's front-end namespaces every cookie through a
#: ``CookieManager`` with a ``btx-`` prefix, so the ``refresh-token``
#: the app code calls it is stored as ``btx-refresh-token`` in the jar
#: (verified against a live sign-in).  The unprefixed name is kept as
#: a fallback for older site revisions.
REFRESH_TOKEN_COOKIES = ("btx-refresh-token", "refresh-token")

#: URI schemes that are always safe inside the sandbox.
#:
#: ``blob:`` and ``data:`` are origin-bound object references: they cannot
#: load an external document or exfiltrate the session, so confining them
#: would only break the site's own download mechanism.  The Tone Exchange
#: saves a liveset by turning the fetched ``.tsl`` bytes into a ``blob:``
#: URL and "opening" it (FileSaver), so ``blob:`` must be allowed.
SAFE_SCHEMES = ("blob", "data")

#: JS injected into the page so that FileSaver's ``saveAs`` — which the
#: Tone Exchange calls for every liveset download, as
#: ``saveAs(blob, filename)`` — issues a plain blob anchor click.
#:
#: FileSaver's own implementation opens a *new window*
#: (``window.open(blob: ...)``); a confined single-window webview has no
#: second window to honour that with, so the window is dropped and the
#: download silently does nothing.  Re-issuing the same blob as an anchor
#: click sends it straight down WebKit's download pipeline (see
#: :func:`_hook_downloads`) and keeps the file name, which the
#: ``window.open`` path would lose.  The wrapper is idempotent and falls
#: back to the original ``saveAs`` if anything goes wrong.
_SAVEAS_WRAPPER_JS = r"""
(function () {
  if (window.__tsl_saveAs_wrapped) { return; }
  if (typeof window.saveAs !== "function") { return; }
  var orig = window.saveAs;
  window.saveAs = function (blob, name) {
    try {
      var url = URL.createObjectURL(blob);
      var a = document.createElement("a");
      a.href = url;
      a.download = (name && String(name)) || "download.tsl";
      document.body.appendChild(a);
      a.click();
      setTimeout(function () {
        a.remove();
        URL.revokeObjectURL(url);
      }, 1000);
    } catch (e) {
      orig(blob, name);
    }
  };
  window.__tsl_saveAs_wrapped = true;
})();
"""


def host_allowed(uri: str) -> bool:
    """Return True when ``uri`` may be loaded in the confined window.

    Args:
        uri: absolute URL (http/https) to check.
    Returns:
        True if the host is on the allowlist (or the URI is a safe
        origin-bound scheme such as ``blob:``), else False.
    """
    parsed = urllib.parse.urlparse(uri or "")
    if parsed.scheme in SAFE_SCHEMES:
        return True
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in ALLOWED_HOSTS:
        return True
    if host == "boss.info":
        return True
    return any(host.endswith(suf) for suf in ALLOWED_SUFFIXES)


def data_dir() -> "object":
    """Return the app's data directory (created if missing).

    Returns:
        A ``pathlib.Path`` to ``~/.local/share/katatone`` (or the
        equivalent platform data dir).
    """
    from ..paths import data_root
    base = data_root()
    base.mkdir(parents=True, exist_ok=True)
    return base


def _cookie_file() -> str:
    """Return the path of the webview's persistent cookie database.

    WebKit's default context does not persist cookies on its own in
    this setup, so the cookie manager is pointed at an explicit
    database file inside the app's data directory (see
    :mod:`katatone.library.store` for the same convention).  The file
    holds the BOSS ID refresh-token, so it is kept mode 0600.

    Returns:
        Absolute path of the cookie database file.
    """
    import os
    f = data_dir() / "boss-id-cookies.sqlite"
    if f.exists():
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
    return str(f)


def _downloads_dir() -> "object":
    """Return the app's livesets library directory (created if missing).

    Files downloaded inside the confined window are saved there, so
    they join the local library (:mod:`katatone.library.store`)
    directly.

    Returns:
        A ``pathlib.Path`` to the ``livesets`` directory.
    """
    d = data_dir() / "livesets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cookie_store() -> "object":
    """Return the cookie manager of WebKit's default context.

    The default context is shared by every confined window, and its
    cookie manager is pinned to an explicit database file
    (:func:`_cookie_file`), so the BOSS ID session persists between
    launches and across processes.

    Returns:
        A ``WebKit2.CookieManager``.
    Raises:
        Exception: if ``gi``/WebKit2GTK 4.1 is unavailable.
    """
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import WebKit2
    cm = WebKit2.WebContext.get_default().get_cookie_manager()
    cm.set_persistent_storage(
        _cookie_file(), WebKit2.CookiePersistentStorage.SQLITE)
    return cm


def _read_cookie(names: tuple[str, ...] = REFRESH_TOKEN_COOKIES,
                 uri: str = SESSION_HOST, timeout: float = 5.0) -> str:
    """Read one cookie from WebKit's default store (no window opened).

    Runs a short private GTK main loop just long enough for the
    asynchronous cookie reply, then quits it.

    Args:
        names: cookie names to look for (first match wins).
        uri: origin whose cookie jar is queried (host + subdomains).
        timeout: max seconds to wait for the reply.
    Returns:
        The cookie value, or ``""`` when absent (not signed in).
    Raises:
        Exception: if ``gi``/WebKit2GTK 4.1 is unavailable.
    """
    _cookie_store()  # pins Gtk 3.0 / WebKit2 4.1 before any gi import
    from gi.repository import GLib, Gtk

    found = {"value": ""}
    ids = []

    # GLib sources belong to the global main context and SURVIVE a
    # Gtk.main()/Gtk.main_quit() pair — an unremoved timeout from this
    # private loop would fire inside the caller's next loop and quit it
    # (this is how the sign-in window used to die ~5 s after opening).
    # Whichever of reply/safety wins marks the state done; the loser
    # becomes a no-op, and the winner removes the other source.
    state = {"done": False}

    def reply(manager, task) -> None:
        if state["done"]:
            return
        state["done"] = True
        for c in manager.get_cookies_finish(task):
            if c.get_name() in names and c.get_value():
                found["value"] = c.get_value()
        for sid in ids:
            GLib.source_remove(sid)
        Gtk.main_quit()

    def safety() -> bool:
        if state["done"]:
            return False
        state["done"] = True
        Gtk.main_quit()
        return False

    ids.append(GLib.timeout_add(int(timeout * 1000), safety))
    _cookie_store().get_cookies(uri, None, reply)
    Gtk.main()
    return found["value"]


def stored_refresh_token() -> str:
    """Return a persisted BOSS ID refresh-token, or ``""``.

    Reads the :data:`REFRESH_TOKEN_COOKIES` cookie from WebKit's default
    cookie store — the same store the confined window signs into — so a
    previous sign-in can be reused without opening any window.

    Returns:
        The token, or ``""`` when not signed in or when ``gi``/WebKit2
        is unavailable.
    """
    try:
        return _read_cookie()
    except Exception:                            # noqa: BLE001
        return ""


#: Every confined window shares WebKit's default context, so its
#: ``download-started`` signal is connected at most once.
_downloads_hooked = False


def _hook_downloads(ctx: "object", url_label: "object") -> None:
    """Save files the page wants to download into the app's library.

    WebKit silently drops a download when nothing handles it — that is
    why clicking a liveset's download button in the window used to do
    nothing.  With WebKit2GTK 2.54's download API the context emits
    ``download-started`` with a ``WebKit2.Download``, which in turn
    emits ``decide-destination`` (carrying the suggested file name);
    answering it with ``set_destination()`` makes the download write to
    that file.

    Args:
        ctx: the WebContext to listen on (the default one).
        url_label: toolbar label updated with the save path.
    """
    global _downloads_hooked
    if _downloads_hooked:
        return
    _downloads_hooked = True
    import os
    from urllib.parse import unquote

    dest_dir = _downloads_dir()

    def on_decide(_download, suggested: str) -> bool:
        """Set the download's destination from its suggested name."""
        name = os.path.basename(unquote(suggested or "")) or "download.tsl"
        path = os.path.join(str(dest_dir), name)
        _download.set_destination(path)
        _download.set_allow_overwrite(True)
        print(f"[webview] saving download: {path}", file=sys.stderr)
        try:
            url_label.set_text(f"Saved {name} → {path}")
        except Exception:                    # noqa: BLE001
            pass
        return True

    def on_download_started(_ctx, download) -> None:
        download.connect("decide-destination", on_decide)

    ctx.connect("download-started", on_download_started)


def build_window(url: str):
    """Create (but do not show) the confined webview window.

    Args:
        url: the page to load first.
    Returns:
        A ``(window, web_view, url_label, home_url)`` tuple.
    Raises:
        Exception: if ``gi``/WebKit2GTK 4.1 is unavailable.
    """
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Pango", "1.0")
    gi.require_version("WebKit2", "4.1")
    from gi.repository import Gtk, Pango, WebKit2

    window = Gtk.Window(title="BOSS Tone Exchange")
    window.set_default_size(1280, 860)
    window.connect("destroy", Gtk.main_quit)

    # ---- toolbar: home button + current-URL label ----
    toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    toolbar.set_margin_top(6)
    toolbar.set_margin_bottom(6)
    toolbar.set_margin_start(8)
    toolbar.set_margin_end(8)
    home = Gtk.Button(label="Tone Exchange")
    url_label = Gtk.Label()
    url_label.set_hexpand(True)
    url_label.set_ellipsize(Pango.EllipsizeMode.END)
    url_label.set_xalign(0.0)
    toolbar.pack_start(home, False, False, 0)
    toolbar.pack_start(url_label, True, True, 0)

    # ---- webview (default context => cookies/login persist) ----
    ctx = WebKit2.WebContext.get_default()
    # Pin the cookie jar to an explicit file: WebKit 4.1's default
    # context does not persist cookies on its own (verified), and the
    # BOSS ID refresh-token must survive between launches.
    ctx.get_cookie_manager().set_persistent_storage(
        _cookie_file(), WebKit2.CookiePersistentStorage.SQLITE)
    web = WebKit2.WebView.new_with_context(ctx)
    # Route the site's downloads (liveset .tsl files) into the library.
    _hook_downloads(ctx, url_label)
    settings = web.get_settings()
    settings.set_property("enable-developer-extras", True)
    settings.set_property("enable-write-console-messages-to-stdout", True)

    def blocked(uri: str) -> None:
        """Report a navigation the window is not allowed to follow."""
        url_label.set_text(f"Blocked (outside Roland/BOSS domains): {uri}")

    def on_policy(_view, decision, decision_type) -> None:
        """Confine navigation to the allowed hosts.

        Popups (``window.open`` / ``target=_blank``) are redirected into
        the same window when allowed, so the session stays in one place.
        """
        if decision_type == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            uri = (decision.get_navigation_action().get_request().get_uri())
            if not host_allowed(uri):
                blocked(uri)
                decision.ignore()
        elif decision_type == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
            uri = decision.get_network_request().get_uri() or ""
            if urllib.parse.urlparse(uri).scheme in SAFE_SCHEMES:
                # A "new window" download (FileSaver's window.open(blob:)).
                # A confined view has no second window, so re-issue it as an
                # in-page blob anchor click: WebKit's download pipeline
                # (see :func:`_hook_downloads`) then handles it without
                # navigating away from the current page.  (The saveAs
                # wrapper normally prevents this path from being taken.)
                decision.ignore()
                _view.run_javascript(
                    "(function(){var a=document.createElement('a');"
                    "a.href='%s';a.download='download';"
                    "document.body.appendChild(a);a.click();"
                    "setTimeout(function(){a.remove();},0);})()" % uri,
                    None, None, None)
            elif host_allowed(uri):
                decision.ignore()
                _view.load_uri(uri)
            else:
                blocked(uri)
                decision.ignore()

    def on_close(_view) -> bool:
        """Keep the window open when the page calls ``window.close()``.

        The Tone Exchange sign-in page is written as a popup: when its
        bootstrap cannot find an identity cookie it closes the window
        (expecting an opener page to show an inline form instead).  A
        top-level confined window has no opener, so the sign-in form
        would vanish before the user can log in.  The window belongs to
        the app, not to the page — cancel the close; the caller closes
        it when it is actually done (or the user can).
        """
        print("[webview] page requested window.close() — keeping the "
              "window open", file=sys.stderr)
        return True

    def on_load_changed(_view, event) -> None:
        """Track the current page for the toolbar label and window title."""
        if event == WebKit2.LoadEvent.COMMITTED:
            url_label.set_text(web.get_uri() or "")
        elif event == WebKit2.LoadEvent.FINISHED:
            title = web.get_title()
            window.set_title(f"{title} — BOSS Tone Exchange" if title
                             else "BOSS Tone Exchange")
            # (Re)install the saveAs wrapper so liveset downloads use a blob
            # anchor click (WebKit download) instead of window.open (which a
            # confined view cannot honour).  The wrapper is idempotent, so
            # re-running it on every full load is safe; an SPA's
            # client-side navigations do not re-fire FINISHED, so the
            # install persists for the whole session.
            web.run_javascript(_SAVEAS_WRAPPER_JS, None, None, None)

    def go_home(_btn) -> None:
        """Reload the start page."""
        web.load_uri(url)
        url_label.set_text(url)

    web.connect("decide-policy", on_policy)
    web.connect("load-changed", on_load_changed)
    web.connect("close", on_close)
    home.connect("clicked", go_home)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    box.pack_start(toolbar, False, False, 0)
    box.pack_start(web, True, True, 0)
    window.add(box)

    web.load_uri(url)
    url_label.set_text(url)
    return window, web, url_label, url


def run(url: str) -> None:
    """Open the confined webview window and run its GTK main loop.

    Args:
        url: the page to load first.
    Raises:
        Exception: if ``gi``/WebKit2GTK 4.1 is unavailable (see
            :func:`main` for the browser fallback).
    """
    _cookie_store()  # pins Gtk 3.0 / WebKit2 4.1 before any gi import
    from gi.repository import Gtk
    window, _web, _label, _home = build_window(url)
    window.show_all()
    Gtk.main()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: ``python3 -m katatone.remote.webview [url]``.

    Runs the confined browser window on ``url`` (default
    :data:`DEFAULT_URL`).

    Args:
        argv: argument list (defaults to ``sys.argv[1:]``).
    Returns:
        0 on success, 1 if the webview could not be created.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    url = argv[0] if argv else DEFAULT_URL
    try:
        run(url)
    except Exception as e:                       # noqa: BLE001
        print(f"webview unavailable ({e}); "
              "install webkit2gtk-4.1 and python3-gi, "
              f"opening {url} in the default browser instead.",
              file=sys.stderr)
        import webbrowser
        webbrowser.open(url)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
