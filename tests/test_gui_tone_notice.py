"""The notice shown before the Tone Exchange window opens (headless)."""
from __future__ import annotations

import types

from katatone.gui import library_pane as lp
from katatone.remote.webview import DEFAULT_URL


def _pane():
    shown, opened = [], []
    app = types.SimpleNamespace(
        page=types.SimpleNamespace(show_dialog=shown.append,
                                   pop_dialog=lambda: None),
        tonecentral_open=opened.append)
    pane = object.__new__(lp._LibraryPane)
    pane.app = app
    return pane, shown, opened


def test_notice_names_terms_and_disclaims_affiliation() -> None:
    pane, shown, opened = _pane()
    pane._tone_confirm()
    texts = []
    for c in shown[-1].content.controls:
        texts.append(getattr(c, "value", None) or "")
        texts += [sp.url for sp in (getattr(c, "spans", None) or [])]
    text = " ".join(t for t in texts if t)
    assert "https://bosstoneexchange.com/terms_and_conditions/" in text
    assert "Roland" in text and "not affiliated" in text
    assert opened == []                        # nothing opens until Continue


def test_continue_opens_the_window_and_cancel_does_not() -> None:
    pane, shown, opened = _pane()
    pane._tone_confirm()
    shown[-1].actions[0].on_click(None)        # Cancel
    assert opened == []
    shown[-1].actions[1].on_click(None)        # Continue
    assert opened == [DEFAULT_URL]
