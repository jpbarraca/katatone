"""Editor dialog for one MIDI mapping: trigger (Learn or manual) + action.

The dialog edits a *copy* of the mapping and hands it to `on_save` only when
the user presses Save, so Cancel leaves the profile untouched.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Callable

import flet as ft

from ..amp import slots
from ..midi import mapping as mp
from ..midi.learn import LearnResult
from ..midi.mapping import (AmpNative, BankAction, ChannelFromPc, ChannelSelect, ChannelStep,
                            EffectAction, ExprTarget, ExpressionAction, Mapping,
                            SetlistStep, Trigger)
from ..midi.router import AMP_NATIVE_ENABLED
from . import model, params_view as pv, theme

if TYPE_CHECKING:
    from .app import App
    from ..midi.manager import MidiManager

KIND_LABELS = {"note": "Note", "cc": "Control change (CC)", "pc": "Program change",
               "pitchbend": "Pitch bend", "pressure": "Channel pressure"}
MODE_LABELS = {"press": "Press (once per press)", "toggle": "Toggle (on/off state)",
               "range": "Range (continuous)"}
ACTION_LABELS = {"channel_select": "Select a channel",
                 "channel_from_pc": "Channel from program number",
                 "bank": "Switch bank", "channel_step": "Next / previous channel",
                 "setlist_step": "Next / previous setlist patch",
                 "effect": "Effect on / off", "expression": "Expression (continuous)",
                 "amp_native": "Forward to the amp's own MIDI map"}
BANK_OPS = {"toggle": "Toggle A/B", "next": "Next bank", "prev": "Previous bank"}
STEP_OPS = {"next": "Next channel", "prev": "Previous channel"}
SETLIST_OPS = {"next": "Next patch", "prev": "Previous patch"}
EFFECT_OPS = {"toggle": "Toggle", "on": "Switch on", "off": "Switch off",
              "momentary": "On while held"}
TARGET_LABELS = {"pedal_fx": "Pedal FX position (wah / bend)", "volume": "Amp volume",
                 "param": "Other parameter..."}
CURVES = {"linear": "Linear", "log": "Logarithmic"}


def _opt(key: str, text: str):
    """A dropdown option whose value is `key` and whose label is `text`."""
    return ft.DropdownOption(key=key, text=text)


def _dropdown(label: str, options: dict[str, str], value: str, on_select,
              width: int = 200) -> ft.Dropdown:
    """A dense dropdown over a {key: text} table.

    Args:
        label: the field label.
        options: option keys and their display text.
        value: the selected key.
        on_select: called with the chosen key.
        width: field width.
    """
    def changed(e) -> None:
        ft.context.disable_auto_update()
        on_select(e.control.value)

    return ft.Dropdown(label=label, value=value, width=width, on_select=changed,
                       options=[_opt(k, t) for k, t in options.items()],
                       **pv.FIELD_STYLE)


def _int_or_none(text: str | None, what: str) -> int | None:
    """Parse an optional integer field.

    Args:
        text: the field text; blank means "none".
        what: the field name for the error message.

    Returns:
        The integer, or None for blank.

    Raises:
        ValueError with a message naming the field.
    """
    text = (text or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        raise ValueError(f"{what} must be a whole number") from None


class MappingDialog:
    """Builds and shows the editor for one mapping."""

    def __init__(self, app: App, midi: MidiManager, mapping: Mapping | None,
                 on_save: Callable[[Mapping, bool], None]) -> None:
        """Prepare the dialog.

        Args:
            app: the application (slots, model, page, status line).
            midi: the MIDI manager (Learn, controllers, test).
            mapping: the mapping to edit, or None to create one.
            on_save: called as `on_save(mapping, is_new)` on Save.
        """
        self.app, self.midi, self.on_save = app, midi, on_save
        self.is_new = mapping is None
        self.draft = (Mapping.from_dict(mapping.to_dict()) if mapping else
                      Mapping(Trigger(), ChannelSelect(self._default_slot())))
        self._action_touched = not self.is_new
        # A new channel mapping starts with no channel picked: preselecting the
        # amp's current one made it easy to save "Panel" by accident.
        self._slot_chosen = not self.is_new
        self._warned_dupe = False
        self._learning = False

        self.error = ft.Text("", color=theme.DANGER, size=12)
        self.learn_text = ft.Text("Press a footswitch or move a pedal, or fill in the fields below.",
                                  size=12, color=theme.MUTED, expand=True)
        self.learn_btn = pv.button("Learn", ft.Icons.FIBER_MANUAL_RECORD, self._toggle_learn,
                                   primary=True)
        self._build_trigger_fields()
        self.action_box = ft.Column(spacing=10)
        t = self.draft.action.type
        self.action_dd = _dropdown("Action", self._action_options(), t,
                                   self._on_action_type, width=300)
        self._build_action()
        self.label_field = ft.TextField(label="Label (optional)", value=self.draft.label,
                                        width=300, **pv.FIELD_STYLE)
        body = ft.Column([
            ft.Row([self.learn_btn, self.learn_text], spacing=10,
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(pv.caption("Trigger"), padding=ft.Padding(0, 6, 0, 0)),
            ft.Row([self.device_dd, self.channel_dd], spacing=10, wrap=True),
            ft.Row([self.kind_dd, self.number_field], spacing=10, wrap=True),
            ft.Row([self.mode_dd, self.threshold_field, self.repeat_cb], spacing=10,
                   wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Container(pv.caption("Action"), padding=ft.Padding(0, 6, 0, 0)),
            self.action_dd, self.action_box, self.label_field, self.error,
        ], spacing=10, width=620, height=560, scroll=ft.ScrollMode.AUTO)
        self.dialog = ft.AlertDialog(
            title=ft.Text("New mapping" if self.is_new else "Edit mapping"),
            content=body, bgcolor=theme.PANEL_TOP, modal=True,
            actions=[pv.link_button("Test", self._test, color=theme.ACCENT),
                     pv.link_button("Cancel", self._cancel, color=theme.MUTED),
                     pv.button("Save", ft.Icons.CHECK, self._save, primary=True)])

    # -- public --------------------------------------------------------------

    def show(self) -> None:
        """Open the dialog."""
        ft.context.disable_auto_update()
        self.app.page.show_dialog(self.dialog)

    # -- trigger -------------------------------------------------------------

    def _new_channel_select(self) -> ChannelSelect:
        """A channel action with nothing picked yet (the user must choose)."""
        self._slot_chosen = False
        return ChannelSelect(self._default_slot())

    def _default_slot(self) -> int:
        """A sensible first channel: the amp's current one, else the first bank slot."""
        cur = self.app.amp_patch
        return cur if cur is not None else self.app.slots()[1].index

    def _device_options(self) -> dict[str, str]:
        """Device choices: any, the enabled controllers, those plugged in, the draft's."""
        labels = list(self.midi.enabled()) + [p.label for p in self.midi.ports()]
        if self.draft.trigger.device:
            labels.append(self.draft.trigger.device)
        opts = {"": "Any device"}
        for label in labels:
            opts.setdefault(label, label)
        return opts

    def _build_trigger_fields(self) -> None:
        """Create the manual trigger controls from the draft."""
        t = self.draft.trigger
        self.device_dd = _dropdown("Device", self._device_options(), t.device or "",
                                   lambda k: setattr(self.draft.trigger, "device", k or None),
                                   width=300)
        chans = {"": "Any channel", **{str(i): f"Channel {i + 1}" for i in range(16)}}
        self.channel_dd = _dropdown("MIDI channel", chans,
                                    "" if t.channel is None else str(t.channel),
                                    lambda k: setattr(self.draft.trigger, "channel",
                                                      None if k == "" else int(k)), width=200)
        self.kind_dd = _dropdown("Message", KIND_LABELS, t.kind, self._on_kind, width=300)
        self.number_field = ft.TextField(
            label="Number (blank = any)", value="" if t.number is None else str(t.number),
            width=200, input_filter=ft.NumbersOnlyInputFilter(), **pv.FIELD_STYLE)
        self.mode_dd = _dropdown("Mode", MODE_LABELS, t.mode,
                                 lambda k: setattr(self.draft.trigger, "mode", k), width=300)
        self.threshold_field = ft.TextField(
            label="Threshold", value=str(t.threshold), width=110,
            input_filter=ft.NumbersOnlyInputFilter(), **pv.FIELD_STYLE)
        self.repeat_cb = ft.Checkbox(
            label="Fire on every message (pedal sends no release)", value=t.repeat,
            on_change=lambda e: setattr(self.draft.trigger, "repeat", bool(e.control.value)))

    def _on_kind(self, kind: str) -> None:
        """Message type changed: continuous ones default to Range mode."""
        self.draft.trigger.kind = kind
        if kind in ("pitchbend", "pressure"):
            self.draft.trigger.mode = "range"
            self.mode_dd.value = "range"
            self.mode_dd.update()

    def _show_trigger(self) -> None:
        """Push the draft's trigger into the manual fields (after Learn)."""
        t = self.draft.trigger
        self.device_dd.options = [_opt(k, v) for k, v in self._device_options().items()]
        self.device_dd.value = t.device or ""
        self.channel_dd.value = "" if t.channel is None else str(t.channel)
        self.kind_dd.value = t.kind
        self.number_field.value = "" if t.number is None else str(t.number)
        self.mode_dd.value = t.mode
        self.threshold_field.value = str(t.threshold)
        self.repeat_cb.value = t.repeat
        for c in (self.device_dd, self.channel_dd, self.kind_dd, self.number_field,
                  self.mode_dd, self.threshold_field, self.repeat_cb):
            _push(c)

    # -- learn ---------------------------------------------------------------

    def _toggle_learn(self, e=None) -> None:
        """Start or cancel Learn."""
        ft.context.disable_auto_update()
        if self._learning:
            self.midi.cancel_learn()
            self._set_learning(False, "Learn cancelled.")
            return
        if not self.midi.enabled():
            self.learn_text.value = ("No controller is enabled: switch one on in the "
                                     "Controllers card first.")
            _push(self.learn_text)
            return
        self._set_learning(True, "Waiting for a controller message (10 s)...")
        self.midi.start_learn(self._learned)

    def _set_learning(self, on: bool, text: str) -> None:
        """Update the Learn button and hint.

        Args:
            on: whether Learn is running.
            text: the hint to show.
        """
        self._learning = on
        self.learn_btn.content = ft.Text("Cancel learn" if on else "Learn", no_wrap=True, size=13)
        self.learn_text.value = text
        _push(self.learn_btn)
        _push(self.learn_text)

    def _learned(self, result: LearnResult | None) -> None:
        """Learn finished (background thread).

        Args:
            result: what was captured, or None on timeout.
        """
        if result is None:
            self._set_learning(False, "Nothing received. Try again, or fill in the fields.")
            return
        keep = self.draft.trigger
        self.draft.trigger = result.trigger
        self.draft.trigger.threshold = keep.threshold if result.trigger.kind == "cc" else 64
        self._set_learning(False, f"Learned: {result.description}")
        self._show_trigger()
        if not self._action_touched:
            self._on_action_type(result.suggested_action, touched=False)
            self.action_dd.value = result.suggested_action
            _push(self.action_dd)

    # -- action --------------------------------------------------------------

    def _action_options(self) -> dict[str, str]:
        """Action types offered (amp-native only when the amp honours it)."""
        return {k: v for k, v in ACTION_LABELS.items()
                if k != "amp_native" or AMP_NATIVE_ENABLED}

    def _on_action_type(self, kind: str, touched: bool = True) -> None:
        """Action type changed: start from that action's defaults.

        Args:
            kind: the action type key.
            touched: False when Learn picked it (the user may still change it).
        """
        if touched:
            self._action_touched = True
        if kind == self.draft.action.type:
            return
        self.draft.action = {
            "channel_select": self._new_channel_select,
            "channel_from_pc": ChannelFromPc, "bank": BankAction,
            "channel_step": ChannelStep, "setlist_step": SetlistStep,
            "effect": EffectAction,
            "expression": ExpressionAction, "amp_native": AmpNative,
        }[kind]()
        self._build_action()
        _push(self.action_box)

    def _build_action(self) -> None:
        """Rebuild the controls below the action dropdown for the draft's action."""
        a = self.draft.action
        box: list[ft.Control] = []
        if isinstance(a, ChannelSelect):
            box.append(self._slot_buttons(a))
        elif isinstance(a, ChannelFromPc):
            box += self._pc_table_controls(a)
        elif isinstance(a, BankAction):
            box.append(_dropdown("Bank change", BANK_OPS, a.op,
                                 lambda k: setattr(a, "op", k)))
        elif isinstance(a, ChannelStep):
            box.append(_dropdown("Step", STEP_OPS, a.op, lambda k: setattr(a, "op", k)))
        elif isinstance(a, SetlistStep):
            box.append(_dropdown("Step", SETLIST_OPS, a.op, lambda k: setattr(a, "op", k)))
            box.append(ft.Text("Plays the patch through the amp's edit buffer; open the setlist "
                               "on the SETLIST tab first. No slot is written.",
                               size=11, color=theme.MUTED))
        elif isinstance(a, EffectAction):
            effects = {k: d.label for k, d in mp.EFFECTS.items()}
            box.append(ft.Row([
                _dropdown("Effect", effects, a.effect, lambda k: setattr(a, "effect", k)),
                _dropdown("Do", EFFECT_OPS, a.op, lambda k: setattr(a, "op", k))],
                spacing=10, wrap=True))
            box.append(ft.Text("Toggle flips the amp's current state. A switch change marks the "
                               "patch as edited; store it with Write.", size=11, color=theme.MUTED))
        elif isinstance(a, ExpressionAction):
            box += self._expression_controls(a)
        elif isinstance(a, AmpNative):
            box += self._amp_native_controls(a)
        self.action_box.controls = box

    def _amp_native_controls(self, a: AmpNative) -> list[ft.Control]:
        """Kind and number for a message forwarded to the amp's own MIDI map."""
        self._native_number = ft.TextField(
            label="Number (blank = default)", width=200, value="" if a.number is None else str(a.number),
            input_filter=ft.NumbersOnlyInputFilter(), **pv.FIELD_STYLE)
        return [ft.Row([_dropdown("Send", {"cc": "Control change", "pc": "Program change"},
                                  a.kind, lambda k: setattr(a, "kind", k)), self._native_number],
                       spacing=10, wrap=True),
                ft.Text("Sent on the amp's MIDI channel and handled by the amp's own settings "
                        "(SYSTEM > MIDI). Blank: a CC uses the amp's expression-pedal CC (47), "
                        "a PC uses the program number received. Switch-type CCs do not behave "
                        "like on/off switches on the amp; use Effect on/off for those.",
                        size=11, color=theme.MUTED)]

    def _slot_buttons(self, a: ChannelSelect) -> ft.Control:
        """One pill per channel of the connected model; the chosen one is lit."""
        def pick(n: int):
            def click(e) -> None:
                ft.context.disable_auto_update()
                a.slot = n
                self._slot_chosen = True
                self._build_action()
                _push(self.action_box)
            return click

        pills = []
        for s in self.app.slots():
            on = self._slot_chosen and s.index == a.slot
            pills.append(ft.Container(
                ft.Text(s.short, size=12, weight=ft.FontWeight.BOLD,
                        color=theme.BG if on else theme.ACCENT),
                width=52, height=30, border_radius=4, alignment=ft.Alignment.CENTER,
                bgcolor=theme.ACCENT if on else None, tooltip=s.long,
                border=ft.Border.all(1, theme.ACCENT_DIM), on_click=pick(s.index)))
        return ft.Row(pills, spacing=6, wrap=True)

    def _pc_table_controls(self, a: ChannelFromPc) -> list[ft.Control]:
        """'Use the amp's table' checkbox and an editable table."""
        field = ft.TextField(
            label="Program = channel, e.g. 0=1A, 2=2A, 4=PN", width=420,
            value=mp.format_pc_table(a.table or {}, self.app.model),
            visible=a.table is not None, **pv.FIELD_STYLE)
        self._pc_field = field

        def use_default(e) -> None:
            ft.context.disable_auto_update()
            if e.control.value:
                a.table = None
            else:
                a.table = dict(slots.AMP_PC_TABLE)
                field.value = mp.format_pc_table(a.table, self.app.model)
            field.visible = a.table is not None
            _push(field)

        return [ft.Checkbox(label="Use the amp's own program-change table", value=a.table is None,
                            on_change=use_default), field,
                ft.Text("A program whose channel this amp lacks is skipped with a message.",
                        size=11, color=theme.MUTED)]

    def _expression_controls(self, a: ExpressionAction) -> list[ft.Control]:
        """Target, range, invert and curve for an expression action."""
        params = mp.numeric_params()
        blocks: dict[str, str] = {}
        for b, _, _ in params:
            blocks.setdefault(b, model.block_title(b))
        param_dd = ft.Dropdown(label="Parameter", width=300, **pv.FIELD_STYLE)
        block_dd = ft.Dropdown(label="Block", width=300, **pv.FIELD_STYLE)

        def fill_params(block: str) -> None:
            opts = [_opt(n, model.display_label(p.name, block))
                    for b, n, p in params if b == block]
            param_dd.options = opts
            if a.target.name not in {o.key for o in opts}:
                a.target.name = opts[0].key if opts else None
            param_dd.value = a.target.name

        def on_target(k: str) -> None:
            if k == "param" and not (a.target.block and a.target.name):
                a.target = ExprTarget("param", "AMP", "AMP_GAIN")
            else:
                a.target = ExprTarget(k, a.target.block, a.target.name) if k == "param" \
                    else ExprTarget(k)
            show_param_row()

        def show_param_row() -> None:
            on = a.target.kind == "param"
            block_dd.visible = param_dd.visible = on
            if on:
                block_dd.value = a.target.block
                fill_params(a.target.block)
            for c in (block_dd, param_dd):
                _push(c)

        def on_block(k: str) -> None:
            a.target.block = k
            fill_params(k)
            _push(param_dd)

        def on_param(e) -> None:
            ft.context.disable_auto_update()
            a.target.name = e.control.value

        block_dd.options = [_opt(k, v) for k, v in blocks.items()]
        block_dd.on_select = lambda e: (ft.context.disable_auto_update(), on_block(e.control.value))
        param_dd.on_select = on_param
        self._lo = ft.TextField(label="Min", value="" if a.lo is None else str(a.lo), width=110,
                                hint_text="auto", input_filter=ft.InputFilter(r"^-?\d*$"),
                                **pv.FIELD_STYLE)
        self._hi = ft.TextField(label="Max", value="" if a.hi is None else str(a.hi), width=110,
                                hint_text="auto", input_filter=ft.InputFilter(r"^-?\d*$"),
                                **pv.FIELD_STYLE)
        target_dd = _dropdown("Drive", TARGET_LABELS, a.target.kind, on_target, width=300)
        block_dd.visible = param_dd.visible = a.target.kind == "param"
        if a.target.kind == "param":
            block_dd.value = a.target.block
            fill_params(a.target.block)
        return [target_dd, block_dd, param_dd,
                ft.Row([self._lo, self._hi,
                        ft.Checkbox(label="Invert", value=a.invert,
                                    on_change=lambda e: setattr(a, "invert", bool(e.control.value))),
                        _dropdown("Curve", CURVES, a.curve, lambda k: setattr(a, "curve", k),
                                  width=160)],
                       spacing=10, wrap=True, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Text("Expression changes the sound only; it does not mark the patch as edited.",
                        size=11, color=theme.MUTED)]

    # -- buttons -------------------------------------------------------------

    def _collect(self) -> Mapping:
        """Read the text fields into the draft.

        Returns:
            The draft mapping.

        Raises:
            ValueError with a message for the first invalid field.
        """
        t = self.draft.trigger
        t.number = _int_or_none(self.number_field.value, "Number")
        thr = _int_or_none(self.threshold_field.value, "Threshold")
        t.threshold = 64 if thr is None else max(0, min(127, thr))
        if t.kind in ("pitchbend", "pressure"):
            t.number = None
        a = self.draft.action
        if isinstance(a, ChannelFromPc) and a.table is not None:
            a.table = mp.parse_pc_table(self._pc_field.value or "", self.app.model)
        if isinstance(a, AmpNative):
            a.number = _int_or_none(self._native_number.value, "Number")
            if a.number is not None and not 0 <= a.number <= 127:
                raise ValueError("Number must be 0-127")
        if isinstance(a, ExpressionAction):
            a.lo = _int_or_none(self._lo.value, "Min")
            a.hi = _int_or_none(self._hi.value, "Max")
            if a.target.kind == "param":
                try:
                    mp.find_param(a.target.block or "", a.target.name or "")
                except KeyError as e:
                    raise ValueError(str(e.args[0])) from None
        if isinstance(a, ChannelSelect) and not self._slot_chosen:
            raise ValueError("Pick a channel (click one of the buttons).")
        self.draft.label = (self.label_field.value or "").strip()
        return self.draft

    def _clashes(self, m: Mapping) -> list[Mapping]:
        """Other enabled mappings that fire on the same message.

        Args:
            m: the mapping being saved.

        Returns:
            The existing mappings with the same device, channel, message,
            number and mode (an edit does not clash with itself).
        """
        t = m.trigger
        return [x for x in self.midi.profile.mappings
                if x.id != m.id and x.enabled and x.trigger.device == t.device
                and x.trigger.channel == t.channel and x.trigger.kind == t.kind
                and x.trigger.number == t.number and x.trigger.mode == t.mode]

    def _set_error(self, text: str) -> None:
        """Show or clear the error line."""
        self.error.value = text
        _push(self.error)

    def _test(self, e=None) -> None:
        """Run the action once (no need to save first)."""
        ft.context.disable_auto_update()
        try:
            m = self._collect()
        except ValueError as ex:
            self._set_error(str(ex))
            return
        self._set_error("")
        self.midi.router.test(Mapping.from_dict(m.to_dict()))

    def _cancel(self, e=None) -> None:
        """Close without saving."""
        ft.context.disable_auto_update()
        self.midi.cancel_learn()
        self.app.page.pop_dialog()

    def _save(self, e=None) -> None:
        """Validate, hand the mapping over and close."""
        ft.context.disable_auto_update()
        try:
            m = self._collect()
        except ValueError as ex:
            self._set_error(str(ex))
            return
        clash = self._clashes(m)
        if clash and not self._warned_dupe:
            self._warned_dupe = True
            what = "; ".join(x.label or x.action.summary(self.app.slot_name) for x in clash)
            self._set_error(f"This switch already does: {what}. Both would fire, one after "
                            f"the other. Delete the old mapping, or press Save again to keep both.")
            return
        self.midi.cancel_learn()
        self.app.page.pop_dialog()
        self.on_save(m, self.is_new)


def _push(ctrl: ft.Control) -> None:
    """Update one control, ignoring the RuntimeError raised while it is not mounted."""
    try:
        ctrl.update()
    except RuntimeError:
        pass
