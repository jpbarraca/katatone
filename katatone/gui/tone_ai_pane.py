"""The TONE AI tab: ask an AI for the settings that match a song.

The AI is an OpenAI-style server or Claude through the Agent Client Protocol.  The song is
free text; the AI is told the song and the amp model and answers in JSON (see :mod:`katatone.ai`).  What it suggests is shown section by section, with the amp's
current value next to each suggestion, and the user takes what is good: a whole section, or
single settings.  Taking applies them to the patch in the editor as one undoable step.
"""
from __future__ import annotations
import threading
from typing import TYPE_CHECKING

import flet as ft

from ..ai import backend, client, parse, prompt, setup, store
from ..ai.proposal import Proposal, Row, Section
from ..amp import slots
from . import params_view as pv, theme

if TYPE_CHECKING:
    from .app import App

TAB_TONE_AI = "TONE AI"
#: How many times a reply that is not JSON is asked for again.
RETRIES = 1


def build(app: App) -> ft.Control:
    """Instantiate the pane, keep it on ``app.tone_ai`` and return its root control."""
    app.tone_ai = _ToneAiPane(app)
    return app.tone_ai.root


def refresh(app: App) -> None:
    """Refresh the pane (the amp model shown) if the app already has one."""
    if hasattr(app, "tone_ai"):
        app.tone_ai.refresh()


def _safe_update(ctrl) -> None:
    try:
        ctrl.update()
    except RuntimeError:
        pass            # not on a page yet


class _ClaudeSetup:
    """The dialog's Claude block: whether it is ready, and the steps to get it ready.

    Checking is offered everywhere; installing the agent and signing in run programs on this
    machine, so they are offered only in a desktop window (``web`` is false).
    """

    def __init__(self, page, command: ft.TextField, web: bool) -> None:
        self.page, self.command, self.web = page, command, web
        self.cancel = threading.Event()         # set when the dialog closes
        self._lines: list[str] = []
        self.text = ft.Text("", size=12, color=theme.MUTED)
        self.log = ft.Text("", size=11, color=theme.MUTED, selectable=True, visible=False,
                           font_family="monospace")
        self.link = ft.TextButton("", visible=False, style=ft.ButtonStyle(color=theme.ACCENT))
        self.check_button = pv.button("Check", ft.Icons.REFRESH, lambda e: self.check())
        self.install_button = pv.button("Install the agent", ft.Icons.DOWNLOAD,
                                        lambda e: self.install(), primary=True)
        self.login_button = pv.button("Sign in", ft.Icons.LOGIN, lambda e: self.login(), primary=True)
        self.install_button.visible = self.login_button.visible = False
        self.note = ft.Text(
            "To install the agent or sign in from a browser session, do it on the machine running "
            "KataTone: `npm install -g @agentclientprotocol/claude-agent-acp`, then `claude auth "
            "login`." if web else
            "Installing downloads about 400 MB into the data directory and needs Node.js 18+. "
            "Signing in opens your browser.", size=11, color=theme.MUTED)
        self.controls = [self.text, ft.Row([self.check_button, self.install_button, self.login_button],
                                           spacing=8, wrap=True), self.link, self.log, self.note]
        self._set_buttons = [self.check_button, self.install_button, self.login_button]

    # ---- showing

    def _refresh(self) -> None:
        for c in (self.text, self.log, self.link, *self._set_buttons):
            _safe_update(c)

    def _say(self, text: str, color: str = theme.MUTED) -> None:
        self.text.value, self.text.color = text, color
        _safe_update(self.text)

    def _log(self, line: str) -> None:
        self._lines = (self._lines + [line[:160]])[-6:]
        self.log.value, self.log.visible = "\n".join(self._lines), True
        _safe_update(self.log)

    def _busy(self, busy: bool) -> None:
        for b in self._set_buttons:
            b.disabled = busy
            _safe_update(b)

    def _show(self, st: setup.Status) -> None:
        self._say(st.text, {"ok": theme.OK, "error": theme.DANGER}.get(st.kind, theme.ACCENT))
        self.install_button.visible = not self.web and st.kind == "no_agent"
        self.login_button.visible = not self.web and st.kind == "signed_out"
        self._busy(False)
        self._refresh()

    # ---- the steps (each runs in a worker thread)

    def _run(self, work) -> None:
        self._busy(True)
        self.page.run_thread(work)

    def check(self) -> None:
        """Look at the agent and the sign-in."""
        command = (self.command.value or "").strip()
        self._say("Checking…")

        def work() -> None:
            self._show(setup.status(command))
        self._run(work)

    def install(self) -> None:
        """Install the agent into the data directory and use it."""
        self._say("Installing the agent… (a few minutes on a slow connection)")
        self._lines = []

        def work() -> None:
            try:
                self.command.value = setup.install(self._log, cancel=self.cancel)
                _safe_update(self.command)
            except setup.SetupError as e:
                self._busy(False)
                return self._say(str(e), theme.DANGER)
            self.check()
        self._run(work)

    def login(self) -> None:
        """Sign in to Claude (the browser of this machine opens)."""
        claude = setup.find_claude((self.command.value or "").strip())
        if claude is None:
            return self._say("No `claude` program was found to sign in with.", theme.DANGER)
        self._say("Signing in: finish in the browser that opened…")
        self._lines = []

        def url(address: str) -> None:
            self.link.content, self.link.url, self.link.visible = ft.Text(
                "Open the sign-in page", size=12, color=theme.ACCENT), address, True
            _safe_update(self.link)

        def work() -> None:
            try:
                setup.login(claude, self._log, url, cancel=self.cancel)
            except setup.SetupError as e:
                self._busy(False)
                return self._say(str(e), theme.DANGER)
            self.link.visible = False
            self.check()
        self._run(work)


class _ToneAiPane:
    def __init__(self, app: App) -> None:
        """Build the search row, the status line and the (empty) result area."""
        self.app = app
        self.settings = store.load()
        self.proposal: Proposal | None = None
        self._raw = ""                      # the reply the proposal was parsed from
        self._ps = None                     # the patch it was parsed against
        self._gen = 0                       # bumped by every request: a stale answer is dropped
        self._asking = False
        self.query = ft.TextField(
            label="Song", hint_text="a title, or title and artist: Sultans of Swing, Dire Straits",
            expand=True, on_submit=lambda e: self.ask(), **pv.FIELD_STYLE)
        self.ask_button = pv.button("Ask", ft.Icons.AUTO_AWESOME, lambda e: self.ask(), primary=True)
        self.amp_text = ft.Text("", size=12, color=theme.MUTED)
        self.status = ft.Text("", size=13, color=theme.MUTED)
        self.spinner = ft.ProgressRing(width=16, height=16, stroke_width=2, visible=False,
                                       color=theme.ACCENT)
        self.result = ft.Column(spacing=10, scroll=ft.ScrollMode.AUTO, expand=True)
        search = ft.Row([self.query, self.ask_button,
                         ft.IconButton(ft.Icons.SETTINGS, tooltip="Tone AI settings",
                                       icon_color=theme.MUTED, on_click=lambda e: self.open_settings())],
                        spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)
        self.root = ft.Column([
            ft.Container(search,            # room for the floating field label
                         padding=ft.Padding(left=0, right=0, top=8, bottom=0)),
            ft.Row([self.spinner, self.status, ft.Container(expand=True), self.amp_text],
                   spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            self.result,
        ], spacing=10, expand=True)
        self.refresh()

    # ------------------------------------------------------------------ state

    def refresh(self) -> None:
        """Show the connected amp model and the server the question goes to."""
        self.amp_text.value = (f"Sends the song and “{slots.model_name(self.app.model)}” to "
                               f"{backend.describe(self.settings)}")
        _safe_update(self.amp_text)

    def _say(self, text: str, error: bool = False) -> None:
        self.status.value = text
        self.status.color = theme.DANGER if error else theme.MUTED
        _safe_update(self.status)

    # ------------------------------------------------------------------- ask

    def ask(self) -> None:
        """Send the song in the field to the server (in a background thread)."""
        song = (self.query.value or "").strip()
        if self._asking:
            return
        if not song:
            return self._say("Type a song first.")
        if not self.settings.ready:
            self._say("Set the server's API key first (or use Claude through ACP).", error=True)
            return self.open_settings()
        self._gen += 1
        self._asking = True
        self.ask_button.disabled = True
        self.spinner.visible = True
        self._say(f"Asking {backend.model_label(self.settings)} about “{song}”…")
        _safe_update(self.root)
        self.app.page.run_thread(self._work, self._gen, song, slots.model_name(self.app.model),
                                 self.settings)

    def _work(self, gen: int, song: str, amp: str, settings: store.Settings) -> None:
        """Ask, parse and show the answer (a worker thread)."""
        proposal, raw, ps, error = None, "", None, ""
        try:
            for attempt in range(RETRIES + 1):
                raw = backend.chat(settings, prompt.system_prompt(), prompt.user_prompt(song, amp))
                try:
                    with self.app.state_lock:
                        ps = self.app.ps
                        proposal = parse.parse(raw, ps)
                    break
                except parse.NotJsonError:
                    if attempt == RETRIES:
                        raise
        except (client.AiError, parse.ReplyError) as e:
            error = str(e)
        if gen != self._gen:
            return                          # a newer question replaced this one
        self.app._ui(lambda: self._show(proposal, raw, ps, error))

    def _show(self, proposal: Proposal | None, raw: str, ps, error: str) -> None:
        self._asking = False
        self.ask_button.disabled = False
        self.spinner.visible = False
        if error:
            if self.settings.provider == "acp" and "Sign in with the agent" in error:
                error += " Open the settings (gear) and press Sign in."
            self._say(error, error=True)
        else:
            self.proposal, self._raw, self._ps = proposal, raw, ps
            self._say("")
        self._render()

    # ----------------------------------------------------------------- apply

    def apply(self) -> None:
        """Apply the settings that are taken to the patch in the editor."""
        p = self.proposal
        if p is None:
            return
        if self.app.ps is not self._ps:
            self._reparse("The patch in the editor changed: look at the suggestion again.")
            return
        changes = p.changes()
        if not changes:
            return self._say("Nothing is taken.")
        self.app.apply_group(changes, "the suggested tone",
                             f"Applied {len(changes)} setting{'s' * (len(changes) != 1)} "
                             f"of {p.song or 'the suggestion'}. Ctrl+Z undoes them.")
        self._reparse()

    def _reparse(self, say: str = "") -> None:
        """Read the reply again against the patch as it is now."""
        try:
            with self.app.state_lock:
                self._ps = self.app.ps
                self.proposal = parse.parse(self._raw, self._ps)
        except parse.ReplyError as e:
            self.proposal = None
            say = str(e)
        if say:
            self._say(say)
        self._render()

    # ------------------------------------------------------------ the result

    def _render(self) -> None:
        """Rebuild the result area from the proposal."""
        p = self.proposal
        self.result.controls = self._cards(p) if p else []
        _safe_update(self.root)

    def _cards(self, p: Proposal) -> list[ft.Control]:
        cards = [self._header(p), self._actions(p)]
        cards.append(ft.ResponsiveRow([self._section(p, s) for s in p.sections],
                                      spacing=10, run_spacing=10))
        if p.missing:
            cards.append(self._note(f"The model left out: {', '.join(p.missing)}. "
                                    "Those are unchanged; ask again to try for them."))
        if p.skipped:
            cards.append(ft.ExpansionTile(
                title=ft.Text(f"{len(p.skipped)} suggestion{'s' * (len(p.skipped) != 1)} "
                              "could not be used", size=12, color=theme.MUTED),
                controls=[self._note(s) for s in p.skipped], dense=True,
                icon_color=theme.MUTED, text_color=theme.MUTED, collapsed_text_color=theme.MUTED))
        cards.append(ft.ExpansionTile(
            title=ft.Text("The model's reply", size=12, color=theme.MUTED),
            controls=[ft.Text(self._raw, size=11, selectable=True, color=theme.MUTED,
                              font_family="monospace")],
            dense=True, icon_color=theme.MUTED, text_color=theme.MUTED,
            collapsed_text_color=theme.MUTED))
        return cards

    @staticmethod
    def _note(text: str) -> ft.Control:
        return ft.Text(text, size=12, color=theme.MUTED, selectable=True)

    def _header(self, p: Proposal) -> ft.Control:
        sure = p.confidence in ("", "high")
        title = " — ".join(x for x in (p.song, p.artist) if x) or "The model's suggestion"
        lines: list[ft.Control] = [ft.Row([
            ft.Text(title, size=16, weight=ft.FontWeight.BOLD),
            ft.Text(f"{p.confidence} confidence" if p.confidence else "", size=11,
                    color=theme.OK if sure else theme.ACCENT)], spacing=10)]
        if not sure:
            lines.append(self._note("The model is not sure this is the song you meant: "
                                    "check the title and artist above."))
        for label, text in (("Original rig", p.rig), ("Approach", p.reasoning), ("Notes", p.notes)):
            if text:
                lines.append(ft.Text(spans=[ft.TextSpan(f"{label}: ", ft.TextStyle(
                    color=theme.LABEL, weight=ft.FontWeight.W_600)), ft.TextSpan(text)],
                    size=13, selectable=True))
        return pv.module_card("Suggestion", lines, led=sure)

    def _actions(self, p: Proposal) -> ft.Control:
        n = p.count
        apply = pv.button(f"Apply {n} selected" if n else "Nothing selected",
                          ft.Icons.CHECK, lambda e: self.apply(), primary=True)
        apply.disabled = n == 0
        return ft.Row([
            apply,
            pv.button("Select all", ft.Icons.DONE_ALL, lambda e: self._select(True)),
            pv.button("Select none", ft.Icons.REMOVE_DONE, lambda e: self._select(False)),
            ft.Text("Applies to the patch in the editor; Ctrl+Z takes it back.", size=12,
                    color=theme.MUTED)],
            spacing=8, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    def _select(self, on: bool) -> None:
        if self.proposal is not None:
            self.proposal.select_all(on)
            self._render()

    def _section(self, p: Proposal, s: Section) -> ft.Control:
        def toggle(e) -> None:
            s.set_all(bool(e.control.value))
            self._render()

        usable = any(r.changed and not r.blocked for r in s.rows)
        head = ft.Checkbox(label="Take this section", value=s.state, tristate=True,
                           disabled=not usable, on_change=toggle, active_color=theme.ACCENT)
        return pv.module_card(s.title, [head, *[self._row(r) for r in s.rows]], wide=False,
                              led=any(r.active for r in s.rows))

    def _row(self, r: Row) -> ft.Control:
        def toggle(e) -> None:
            r.selected = bool(e.control.value)
            self._render()

        if not r.changed:
            change = ft.Text(f"{r.new_text} (already set)", size=12, color=theme.MUTED)
        else:
            change = ft.Text(spans=[
                ft.TextSpan(f"{r.old_text}  →  ", ft.TextStyle(color=theme.MUTED)),
                ft.TextSpan(r.new_text, ft.TextStyle(color=theme.FOREGROUND,
                                                     weight=ft.FontWeight.BOLD))], size=12)
        extras = [ft.Text(r.note, size=11, color=theme.ACCENT)] if r.note else []
        if r.blocked:
            extras.append(ft.Text("needs the new type", size=11, color=theme.MUTED))
        return ft.Row([
            ft.Checkbox(value=r.active, disabled=not r.changed or r.blocked, on_change=toggle,
                        active_color=theme.ACCENT),
            ft.Text(r.label, size=13, width=130, no_wrap=True), change, *extras],
            spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER)

    # -------------------------------------------------------------- settings

    def open_settings(self) -> None:
        """Show the dialog for the AI to ask: a server (URL, model, key) or Claude through ACP."""
        ft.context.disable_auto_update()
        page, s = self.app.page, self.settings
        web = bool(getattr(page, "web", False))     # a browser session may not choose a program to run

        def field(name: str, **kw) -> ft.TextField:
            return ft.TextField(data=name, width=420, **pv.FIELD_STYLE, **kw)

        def note(name: str, text: str) -> ft.Text:
            return ft.Text(text, size=12, color=theme.MUTED, data=name)

        provider = ft.Dropdown(
            data="provider", label="Ask", width=420, value=s.provider, **pv.FIELD_STYLE,
            options=[ft.DropdownOption(key="openai", text="An OpenAI-style server"),
                     ft.DropdownOption(key="acp", text="Claude, through ACP")])
        controls = [
            provider,
            note("openai_note", "Any server with an OpenAI-style chat API works (OpenAI, Ollama, "
                 "LM Studio, vLLM). The key is kept in the data directory, readable by you "
                 "only; leave it empty to keep the saved one."),
            field("url", label="Server (base URL)", value=s.base_url),
            field("model", label="Model", value=s.model),
            field("key", label="API key", value="", password=True, can_reveal_password=True,
                  hint_text="saved" if s.api_key else ""),
            note("acp_note", "Starts an agent that speaks the Agent Client Protocol and signs in "
                 "by itself: for Claude, install @agentclientprotocol/claude-agent-acp and sign "
                 "in with `claude` once. The agent is only asked to answer: it gets no files "
                 "and no tools."),
            field("acp_model", label="Model (sonnet, opus, haiku, ...; empty: the agent's own)",
                  value=s.acp_model),
            field("acp_command", label="Agent command", value=s.acp_command, visible=not web)]
        by_name = {c.data: c for c in controls}
        claude = _ClaudeSetup(page, by_name["acp_command"], web)
        claude_box = ft.Column(claude.controls, tight=True, spacing=8, data="claude")
        controls.append(claude_box)
        by_name["claude"] = claude_box
        column = ft.Column(controls, tight=True, spacing=10, width=440, scroll=ft.ScrollMode.AUTO)
        groups = {"openai": ("openai_note", "url", "model", "key"),
                  "acp": ("acp_note", "acp_model", "acp_command", "claude")}
        checked = []

        def show(e=None) -> None:
            for kind, names in groups.items():
                for n in names:
                    by_name[n].visible = provider.value == kind and not (web and n == "acp_command")
            _safe_update(column)
            if provider.value == "acp" and not checked:
                checked.append(True)
                claude.check()                      # cheap: no question is asked

        provider.on_select = show
        show()

        def save(e=None) -> None:
            text = lambda n: (by_name[n].value or "").strip()          # noqa: E731
            new = store.Settings(
                base_url=text("url") or store.DEFAULT_URL, model=text("model") or store.DEFAULT_MODEL,
                api_key=text("key") or s.api_key, provider=provider.value or "openai",
                acp_command=s.acp_command if web else text("acp_command") or store.DEFAULT_ACP_COMMAND,
                acp_model=text("acp_model"))
            try:
                store.save(new)
            except OSError as err:
                return self._say(f"Cannot save the settings: {err}", error=True)
            self.settings = new
            close()
            self.refresh()
            self._say("Settings saved.")

        def close(e=None) -> None:
            claude.cancel.set()                     # stops an install or sign-in under way
            page.pop_dialog()

        page.show_dialog(ft.AlertDialog(
            title=ft.Text("Tone AI settings"), bgcolor=theme.PANEL_TOP, content=column,
            actions=[pv.link_button("Cancel", close, color=theme.MUTED),
                     pv.button("Save", ft.Icons.CHECK, save, primary=True)]))
