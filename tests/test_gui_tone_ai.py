"""The TONE AI tab, headless: asking, showing the suggestion, taking part of it, undo."""
from __future__ import annotations

import json
import stat

import pytest

from test_gui_resync import app  # noqa: F401  (fixture)

from katatone.ai import client, setup, store
from katatone.gui import app as app_mod, tone_ai_pane

REPLY = {
    "match": {"song": "Sultans of Swing", "artist": "Dire Straits", "confidence": "high"},
    "original_rig": "Strat into a Vox", "reasoning": "Bright clean.",
    "amp": {"type": "Clean", "gain": 30, "volume": 50},
    "delay": {"on": True, "type": "ANALOG", "time_ms": 380},
    "eq": {"on": True, "type": "PARAMETRIC EQ", "params": {"high_gain": 4}},
}


@pytest.fixture
def pane(app, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(store, "default_path", lambda: tmp_path / "ai.json")
    p = app.tone_ai
    p.settings = store.Settings("https://api.openai.com/v1", "m", "sk-test")
    p.calls = []
    p.replies = [json.dumps(REPLY)]

    def chat(settings, system, user, **kw):
        p.calls.append((system, user))
        r = p.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(tone_ai_pane.backend, "chat", chat)
    p.setup_calls = []
    monkeypatch.setattr(tone_ai_pane.setup, "status",
                        lambda command: p.setup_calls.append(("status", command))
                        or setup.Status("ok", "Signed in as me@x.org (pro)."))
    return p


def _ask(pane, song="sultans of swing"):
    pane.query.value = song
    pane.ask()
    pane.app.page.join()


def _rows(pane, key):
    return {r.key + ("*" if r.role == "switch" else ""): r
            for r in next(s for s in pane.proposal.sections if s.key == key).rows}


def test_the_tab_is_registered(app) -> None:  # noqa: F811
    assert app_mod.TAB_ORDER[-1] == "TONE AI" == app_mod.TAB_TONE_AI
    assert app.tone_ai.root is not None


def test_asking_sends_the_song_and_the_amp_and_shows_the_answer(pane) -> None:
    _ask(pane)
    system, user = pane.calls[0]
    assert user == 'Song: "sultans of swing"\nAmp: KATANA-50 Gen 3'
    assert "Settings reference" in system
    assert [s.key for s in pane.proposal.sections] == ["amp", "delay", "eq"]
    assert pane.result.controls and not pane.spinner.visible and not pane.ask_button.disabled
    assert pane.status.value == ""


def test_nothing_is_changed_until_applied(pane, app) -> None:  # noqa: F811
    before = dict(app.ps.data)
    _ask(pane)
    assert app.ps.data == before and not app.dirty


def test_apply_changes_the_patch_in_one_undoable_step(pane, app) -> None:  # noqa: F811
    region = app.region_for(app.patch_number)
    time_before = app.ps.get_param(region, "DELAY(1)", 1)
    _ask(pane)
    n = pane.proposal.count
    assert n > 5
    pane.apply()
    assert app.ps.get_param(region, "AMP", 0) == 30 and app.ps.get_param(region, "AMP", 1) == 50
    assert app.ps.get_param(region, "SW", 3) == 1 and app.ps.get_param(region, "DELAY(1)", 1) == 380
    assert app.dirty and f"Applied {n} settings" in app.last_status
    assert pane.proposal.count == 0                    # what is applied is no longer a change
    app.undo()
    assert app.ps.get_param(region, "AMP", 0) == 0 and app.ps.get_param(region, "SW", 3) == 0
    assert app.ps.get_param(region, "DELAY(1)", 1) == time_before


def test_apply_only_what_is_taken(pane, app) -> None:  # noqa: F811
    region = app.region_for(app.patch_number)
    kept = [app.ps.get_param(region, b, i) for b, i in (("DELAY(1)", 1), ("EQ_PEQ(1)", 8))]
    _ask(pane)
    amp, delay = _rows(pane, "amp"), _rows(pane, "delay")
    amp["volume"].selected = False                      # drop one setting
    for r in delay.values():                            # and a whole section
        r.selected = False
    next(s for s in pane.proposal.sections if s.key == "eq").set_all(False)
    pane.apply()
    assert app.ps.get_param(region, "AMP", 0) == 30 and app.ps.get_param(region, "AMP", 1) == 0
    assert app.ps.get_param(region, "SW", 3) == 0
    assert [app.ps.get_param(region, b, i)
            for b, i in (("DELAY(1)", 1), ("EQ_PEQ(1)", 8))] == kept
    app.undo()
    assert app.ps.get_param(region, "AMP", 0) == 0


def test_applying_with_nothing_taken_changes_nothing(pane, app) -> None:  # noqa: F811
    _ask(pane)
    pane.proposal.select_all(False)
    pane.apply()
    assert not app.dirty and pane.status.value == "Nothing is taken."


def test_a_reply_that_is_not_json_is_asked_for_again_once(pane) -> None:
    pane.replies = ["Sure! Here are some settings", json.dumps(REPLY)]
    _ask(pane)
    assert len(pane.calls) == 2 and pane.proposal is not None
    pane.replies = ["nope", "still nope"]
    pane.proposal = None
    _ask(pane)
    assert len(pane.calls) == 4 and pane.proposal is None and "not a JSON" in pane.status.value


def test_server_errors_are_shown_and_the_old_suggestion_is_not_left_to_pass_for_the_answer(pane) -> None:
    _ask(pane)
    assert pane.proposal is not None and pane.result.controls
    pane.replies = [client.AiError("The server rejected the API key.")]
    _ask(pane, "another song")
    assert pane.status.value == "The server rejected the API key."
    assert pane.proposal is None and pane.result.controls == []      # nothing of the first song
    assert not pane.spinner.visible and not pane.ask_button.disabled


def test_the_model_not_knowing_the_song_is_shown(pane) -> None:
    pane.replies = [json.dumps({"error": "I do not know that song"})]
    _ask(pane, "asdf qwer")
    assert "I do not know that song" in pane.status.value and pane.proposal is None


def test_an_empty_song_asks_nothing(pane) -> None:
    _ask(pane, "   ")
    assert pane.calls == [] and "Type a song" in pane.status.value


def test_without_a_key_the_settings_open_instead(pane, app) -> None:  # noqa: F811
    pane.settings = store.Settings("https://api.openai.com/v1", "m", "")
    _ask(pane)
    assert pane.calls == [] and len(app.page.dialogs) == 1


def test_an_answer_that_arrives_after_a_newer_question_is_dropped(pane) -> None:
    _ask(pane)
    shown = pane.proposal
    pane._gen += 1                                      # a newer question started meanwhile
    pane.replies = [json.dumps({"amp": {"gain": 99}})]
    pane._work(pane._gen - 1, "old", "KATANA-50 Gen 3", pane.settings)
    assert pane.proposal is shown


def test_a_different_patch_is_read_again_not_applied(pane, app) -> None:  # noqa: F811
    from katatone.model.paramset import ParamSet
    _ask(pane)
    app.ps = ParamSet()                                 # the editor now holds another patch
    pane.apply()
    assert not app.dirty and "changed" in pane.status.value
    assert pane.proposal.count > 0                      # shown again, against the new patch


def _dialog(app):
    dialog = app.page.dialogs[-1]
    return dialog, {c.data: c for c in dialog.content.controls if c.data}


def test_settings_dialog_saves_with_owner_only_permissions(pane, app, tmp_path) -> None:  # noqa: F811
    pane.open_settings()
    dialog, f = _dialog(app)
    f["url"].value, f["model"].value, f["key"].value = "http://localhost:11434/v1", "llama3", "sk-new"
    dialog.actions[1].on_click(None)
    assert (pane.settings.base_url, pane.settings.model, pane.settings.api_key) == (
        "http://localhost:11434/v1", "llama3", "sk-new")
    assert pane.settings.provider == "openai"
    path = tmp_path / "ai.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600 and "sk-new" in path.read_text()
    assert app.page.dialogs == []
    pane.open_settings()                                # an empty key field keeps the saved key
    dialog, f = _dialog(app)
    f["key"].value = ""
    dialog.actions[1].on_click(None)
    assert pane.settings.api_key == "sk-new"


def test_choosing_claude_through_acp(pane, app) -> None:  # noqa: F811
    pane.open_settings()
    dialog, f = _dialog(app)
    assert f["url"].visible and not f["acp_model"].visible          # the server's fields first
    f["provider"].value = "acp"
    f["provider"].on_select(None)
    assert f["acp_model"].visible and f["acp_command"].visible and not f["url"].visible
    f["acp_model"].value = "sonnet"
    dialog.actions[1].on_click(None)
    s = pane.settings
    assert (s.provider, s.acp_model, s.acp_command) == ("acp", "sonnet", "claude-agent-acp")
    assert store.load(store.default_path()).provider == "acp"


def test_asking_through_acp_needs_no_key(pane) -> None:
    pane.settings = store.Settings(provider="acp", api_key="")
    _ask(pane)
    assert len(pane.calls) == 1 and pane.proposal is not None


def test_a_browser_session_cannot_choose_the_program(pane, app) -> None:  # noqa: F811
    app.page.web = True
    pane.settings = store.Settings(provider="acp", acp_command="my-agent --flag")
    pane.open_settings()
    dialog, f = _dialog(app)
    assert not f["acp_command"].visible
    f["acp_command"].value = "rm -rf /"
    dialog.actions[1].on_click(None)
    assert pane.settings.acp_command == "my-agent --flag"


# ---- getting Claude ready (the dialog's Claude block)

def _claude_block(app):
    dialog, f = _dialog(app)
    block = f["claude"]
    row = block.controls[1]
    names = {"Check": row.controls[0], "Install the agent": row.controls[1], "Sign in": row.controls[2]}
    return dialog, f, block, names


def _open_acp(pane, app, web=False):
    app.page.web = web
    pane.settings = store.Settings(provider="acp")
    pane.open_settings()
    app.page.join()
    return _claude_block(app)


def test_opening_the_settings_for_claude_checks_it(pane, app) -> None:  # noqa: F811
    _, f, block, names = _open_acp(pane, app)
    assert pane.setup_calls == [("status", "claude-agent-acp")]
    assert block.controls[0].value == "Signed in as me@x.org (pro)."
    assert not names["Install the agent"].visible and not names["Sign in"].visible


def test_no_agent_offers_to_install_it_and_then_uses_it(pane, app, monkeypatch) -> None:  # noqa: F811
    answers = [setup.Status("no_agent", "The agent is not installed."),
               setup.Status("ok", "Signed in as me@x.org (pro).")]
    monkeypatch.setattr(tone_ai_pane.setup, "status", lambda command: answers.pop(0))

    def install(on_line, cancel=None):
        on_line("added 108 packages")
        return "/data/claude-agent/node_modules/.bin/claude-agent-acp"
    monkeypatch.setattr(tone_ai_pane.setup, "install", install)
    dialog, f, block, names = _open_acp(pane, app)
    assert names["Install the agent"].visible
    names["Install the agent"].on_click(None)
    app.page.join()
    assert f["acp_command"].value == "/data/claude-agent/node_modules/.bin/claude-agent-acp"
    assert block.controls[0].value.startswith("Signed in") and not names["Install the agent"].visible
    assert "added 108 packages" in block.controls[3].value
    dialog.actions[1].on_click(None)                    # saved with the installed agent
    assert pane.settings.acp_command.endswith("claude-agent-acp")


def test_a_failed_install_is_shown(pane, app, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(tone_ai_pane.setup, "status",
                        lambda c: setup.Status("no_agent", "The agent is not installed."))

    def install(on_line, cancel=None):
        raise setup.SetupError("Installing needs Node.js and npm.")
    monkeypatch.setattr(tone_ai_pane.setup, "install", install)
    _, f, block, names = _open_acp(pane, app)
    names["Install the agent"].on_click(None)
    app.page.join()
    assert block.controls[0].value == "Installing needs Node.js and npm."
    assert not names["Check"].disabled                  # the buttons are usable again


def test_signed_out_offers_to_sign_in_then_checks_again(pane, app, monkeypatch) -> None:  # noqa: F811
    answers = [setup.Status("signed_out", "Not signed in to Claude."),
               setup.Status("ok", "Signed in as me@x.org (pro).")]
    monkeypatch.setattr(tone_ai_pane.setup, "status", lambda command: answers.pop(0))
    monkeypatch.setattr(tone_ai_pane.setup, "find_claude", lambda command: "/bin/claude")
    seen = {}

    def login(claude, on_line, on_url, cancel=None):
        seen["claude"] = claude
        on_url("https://claude.ai/oauth/authorize?x=1")
        return True
    monkeypatch.setattr(tone_ai_pane.setup, "login", login)
    _, f, block, names = _open_acp(pane, app)
    assert names["Sign in"].visible and not names["Install the agent"].visible
    names["Sign in"].on_click(None)
    app.page.join()
    assert seen["claude"] == "/bin/claude"
    assert block.controls[0].value.startswith("Signed in") and not names["Sign in"].visible


def test_a_browser_session_can_check_but_not_install_or_sign_in(pane, app, monkeypatch) -> None:  # noqa: F811
    for kind in ("no_agent", "signed_out"):
        monkeypatch.setattr(tone_ai_pane.setup, "status", lambda c, k=kind: setup.Status(k, "x"))
        app.page.dialogs.clear()
        _, f, block, names = _open_acp(pane, app, web=True)
        assert not names["Install the agent"].visible and not names["Sign in"].visible
        assert "npm install -g" in block.controls[4].value          # what to do by hand instead


def test_closing_the_dialog_stops_what_is_running(pane, app, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(tone_ai_pane.setup, "status",
                        lambda c: setup.Status("no_agent", "The agent is not installed."))
    stopped = []

    def install(on_line, cancel=None):
        assert cancel.wait(5), "the install was never told to stop"
        stopped.append(True)
        raise setup.SetupError("The install was stopped.")
    monkeypatch.setattr(tone_ai_pane.setup, "install", install)
    dialog, f, block, names = _open_acp(pane, app)
    names["Install the agent"].on_click(None)
    dialog.actions[0].on_click(None)                    # Cancel, while it runs
    app.page.join()
    assert stopped == [True] and app.page.dialogs == []


def test_a_question_that_fails_for_sign_in_points_to_the_dialog(pane) -> None:
    pane.settings = store.Settings(provider="acp")
    pane.replies = [client.AiError("The agent refused: Authentication required. "
                                   "Sign in with the agent first (for Claude, run `claude`).")]
    _ask(pane)
    assert "press Sign in" in pane.status.value


def test_the_search_row_leaves_room_for_the_floating_label(pane) -> None:
    top = pane.root.controls[0]                         # the "Song" label floats above the field
    assert top.padding.top >= 8 and pane.query in top.content.controls


# ---- Save to library: a copy with the taken settings, the editor left alone

def _save(pane, app, liveset="Mine"):  # noqa: F811
    """Press Save to library, name a new liveset (or, with None, keep the one picked), Save."""
    pane.save_to_library()
    dialog = app.page.dialogs[-1]
    mode = dialog.content.controls[0]
    if liveset is not None:
        new = mode.content.controls[3]
        new.value = liveset
        new.on_change(None)
    dialog.actions[1].on_click(None)
    return dialog


def test_save_to_library_keeps_a_copy_named_after_the_song(pane, app) -> None:  # noqa: F811
    from katatone.liveset.tsl import Tsl  # noqa: F401
    _ask(pane)
    before = dict(app.ps.data)
    dialog = _save(pane, app)
    assert "Sultans of Swing" in dialog.title.value
    tsl = app.lib.load("Mine")
    assert len(tsl) == 1 and tsl.patch_name(0) == "Sultans of Swing"
    saved, region = tsl.to_paramset(0), app.region_for(app.patch_number)
    assert saved.get_param(region, "AMP", 0) == 30 and saved.get_param(region, "SW", 3) == 1
    assert app.ps.data == before and not app.dirty          # the editor (and the amp) untouched
    assert "Saved 'Sultans of Swing'" in app.last_status


def test_save_to_library_saves_only_what_is_taken(pane, app) -> None:  # noqa: F811
    _ask(pane)
    _rows(pane, "amp")["gain"].selected = False
    for r in _rows(pane, "delay").values():
        r.selected = False
    _save(pane, app)
    saved = app.lib.load("Mine").to_paramset(0)
    region = app.region_for(app.patch_number)
    assert saved.get_param(region, "AMP", 0) == 0 and saved.get_param(region, "SW", 3) == 0
    assert saved.get_param(region, "AMP", 1) == 50          # volume was taken


def test_save_to_library_after_apply_saves_what_the_editor_has(pane, app) -> None:  # noqa: F811
    _ask(pane)
    pane.apply()                                            # nothing is left to change
    _save(pane, app)
    saved = app.lib.load("Mine").to_paramset(0)
    assert saved.get_param(app.region_for(app.patch_number), "AMP", 0) == 30


def test_save_to_library_needs_a_suggestion_and_the_same_patch(pane, app) -> None:  # noqa: F811
    from katatone.model.paramset import ParamSet
    pane.save_to_library()
    assert app.page.dialogs == []                           # nothing asked yet
    _ask(pane)
    app.ps = ParamSet()                                     # the editor holds another patch now
    pane.save_to_library()
    assert app.page.dialogs == [] and "changed" in pane.status.value


def test_save_to_library_can_add_to_an_existing_liveset(pane, app) -> None:  # noqa: F811
    app.lib.create("Mine")
    _ask(pane)
    _save(pane, app, liveset=None)                          # the existing liveset is picked
    pane.replies = [json.dumps({**REPLY, "match": {"song": "Another One"}})]
    _ask(pane, "another")
    _save(pane, app, liveset=None)
    tsl = app.lib.load("Mine")
    assert [tsl.patch_name(i) for i in range(len(tsl))] == ["Sultans of Swing", "Another One"]
    assert [e.name for e in app.lib.list()] == ["Mine"]     # no second liveset made


# ---- the guitar

def test_the_guitar_goes_with_the_song(pane) -> None:
    pane.guitar.value = "Fender Stratocaster, single coils"
    _ask(pane)
    assert pane.calls[0][1] == ('Song: "sultans of swing"\nAmp: KATANA-50 Gen 3\n'
                                'Guitar: "Fender Stratocaster, single coils"')
    assert "on Fender Stratocaster" not in pane.status.value            # the answer replaced it


def test_without_a_guitar_none_is_sent(pane) -> None:
    _ask(pane)
    assert "Guitar" not in pane.calls[0][1]


def test_the_guitar_is_remembered_for_the_next_time(pane, app) -> None:  # noqa: F811
    pane.guitar.value = " Gibson Les Paul "
    _ask(pane)
    assert store.load(store.default_path(), env={}).guitar == "Gibson Les Paul"
    again = tone_ai_pane._ToneAiPane(app)
    assert again.guitar.value == "Gibson Les Paul"
    pane.replies = [json.dumps(REPLY)]
    pane.guitar.value = ""                                              # cleared: forgotten too
    _ask(pane)
    assert store.load(store.default_path(), env={}).guitar == ""


def test_saving_the_settings_keeps_the_guitar(pane, app) -> None:  # noqa: F811
    pane.guitar.value = "Telecaster"
    pane.open_settings()
    dialog, f = _dialog(app)
    dialog.actions[1].on_click(None)
    assert pane.settings.guitar == "Telecaster"
    assert store.load(store.default_path(), env={}).guitar == "Telecaster"


def test_the_pickup_advice_is_shown_with_the_suggestion(pane) -> None:
    pane.replies = [json.dumps({**REPLY, "pickup": "neck pickup, tone knob at 8"})]
    _ask(pane, "Sultans of Swing")
    assert pane.proposal.pickup == "neck pickup, tone knob at 8"
    header = pane._header(pane.proposal)
    shown = [s.text for c in header.content.controls for s in getattr(c, "spans", None) or []]
    assert "Pickup: " in shown and "neck pickup, tone knob at 8" in shown


def test_the_search_row_holds_song_guitar_ask_and_settings(pane) -> None:
    row = pane.root.controls[0].content
    assert row.controls[:3] == [pane.query, pane.guitar, pane.ask_button]
    assert pane.guitar.on_submit is not None


# ---- every question stands alone

def test_the_last_suggestion_is_gone_as_soon_as_the_next_question_is_asked(pane, app) -> None:  # noqa: F811
    _ask(pane)
    assert pane.proposal is not None
    seen = {}
    real = pane.app.page.run_thread

    def hold(fn, *args):                                # the answer is on its way: look at the screen
        seen["proposal"], seen["cards"] = pane.proposal, list(pane.result.controls)
    pane.app.page.run_thread = hold
    pane.replies = [json.dumps(REPLY)]
    pane.query.value = "another song"
    pane.ask()
    assert seen == {"proposal": None, "cards": []}
    pane.app.page.run_thread = real


def test_a_question_carries_nothing_of_the_one_before(pane) -> None:
    pane.guitar.value = "Fender Stratocaster"
    _ask(pane, "Wonderwall")
    pane.replies = [json.dumps({**REPLY, "match": {"song": "Back in Black"}, "pickup": "bridge"})]
    pane.guitar.value = ""
    _ask(pane, "Back in Black")
    (system_a, user_a), (system_b, user_b) = pane.calls
    assert system_a == system_b                         # the instructions are the same every time
    assert user_b == 'Song: "Back in Black"\nAmp: KATANA-50 Gen 3'
    assert "Wonderwall" not in user_b and "Stratocaster" not in user_b
    assert "Back in Black" not in system_b and "Wonderwall" not in system_b
    assert pane.proposal.song == "Back in Black" and pane.proposal.pickup == "bridge"


def test_the_same_question_is_asked_the_same_way_every_time(pane) -> None:
    for _ in range(3):
        pane.replies = [json.dumps(REPLY)]
        _ask(pane, "sultans of swing")
    assert len(set(pane.calls)) == 1                    # no history is added to the request
    assert len(pane.calls) == 3
