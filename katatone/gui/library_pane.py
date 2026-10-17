"""Librarian pane: livesets (groups of patches) and the patches inside them.

The library is a list of *livesets*; each
liveset groups up to ``MAX_PATCHES`` patches.  Left: the livesets (New,
Rename, Duplicate, Export, Delete, Import, plus Tone Central: open the
BOSS Tone Exchange window and sign in; downloaded livesets appear here).  Right: the selected liveset's
patches, drag-to-reorder, with per-patch Load into editor, Send to amp slot,
Rename, Duplicate, Copy/Move to another liveset and Delete; patches are added
from the editor or read straight from an amp slot.

Livesets are ``.tsl`` files on disk under ``app.lib.root`` and every change is
saved immediately.  Import / Export use the native file chooser (Flet
``FilePicker``; on Linux desktop it needs zenity or kdialog).
"""
from __future__ import annotations
from typing import TYPE_CHECKING, Callable, Optional

import datetime
import pathlib
import shutil

import flet as ft

from ..liveset.tsl import MAX_PATCHES, LivesetFull, Tsl
from ..model.paramset import ParamSet
from ..remote.webview import DEFAULT_URL, stored_refresh_token
from . import params_view as pv, theme

if TYPE_CHECKING:
    from .app import App



def _safe_update(ctrl):
    """Push one control to the client, ignoring the RuntimeError raised
    while it is not attached to a page yet."""
    try:
        ctrl.update()
    except RuntimeError:
        pass  # not yet attached to page


def build(app: App) -> ft.Control:
    """Instantiate the pane, keep it on ``app.library`` and return its
    root control."""
    app.library = _LibraryPane(app)
    return app.library.root


def refresh(app: App) -> None:
    """Refresh the pane if the app already has one; a no-op otherwise."""
    if hasattr(app, "library"):
        app.library.refresh()


#: Terms of service of the site the Tone Central button opens.
TERMS_URL = "https://bosstoneexchange.com/terms_and_conditions/"


def refresh_tone(app: App) -> None:
    """Refresh the Tone Central status and pick up newly downloaded
    livesets, if the pane is built yet."""
    if hasattr(app, "library"):
        app.library.refresh_tone()


def _menu(items: list[tuple[str, object, Callable]], icon=ft.Icons.MORE_VERT,
          tooltip: str = "More") -> ft.PopupMenuButton:
    """Kebab-style popup menu built from (label, icon, callback) triples.

    Args:
        items: one ``(label, icon, on_click)`` triple per entry; ``icon``
            may be ``None`` for a text-only row.
        icon: the trigger button's icon (default: more-vert).
        tooltip: the trigger button's tooltip.

    Returns:
        The ``ft.PopupMenuButton``.
    """
    return ft.PopupMenuButton(
        icon=icon, icon_color=theme.MUTED, tooltip=tooltip,
        bgcolor=theme.PANEL_TOP,
        items=[ft.PopupMenuItem(
            content=ft.Row([ft.Icon(ic, size=16, color=theme.MUTED),
                            ft.Text(text, size=13)], spacing=10)
            if ic else ft.Text(text, size=13),
            on_click=cb) for text, ic, cb in items])


class _LibraryPane:
    def __init__(self, app: App) -> None:
        """Build the pane's control tree — livesets on the left, patches of
        the selected liveset on the right — and do the first refresh."""
        self.app = app
        self.selected: Optional[str] = None     # selected liveset name
        self._names: list[str] = []
        self.tsl: Optional[Tsl] = None          # ... and its contents
        # Services register with the page on construction; keep a reference
        # so Flet does not drop it as unreferenced after the next event.
        self._picker = ft.FilePicker()

        self.new_field = ft.TextField(label="New liveset name", width=220,
                                      value="", hint_text="My liveset",
                                      on_submit=lambda e: self._create(),
                                      **pv.FIELD_STYLE)
        toolbar = ft.Row([
            self.new_field,
            pv.button("New liveset", ft.Icons.CREATE_NEW_FOLDER,
                      lambda e: self._create(), primary=True),
            pv.button("Import .tsl…", ft.Icons.UPLOAD_FILE, self._import),
            pv.button("Refresh", ft.Icons.REFRESH, lambda e: self.refresh()),
            ft.Container(width=1, height=28, bgcolor=theme.PANEL_EDGE),
            pv.button("Tone Central", ft.Icons.CLOUD_DOWNLOAD,
                      lambda e: self._tone_confirm()),
        ], spacing=8, wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER)

        self.liveset_list = ft.ListView(spacing=4, expand=True)
        self.liveset_card = pv.module_card("Livesets", [self.liveset_list])
        self.liveset_card.width = 380
        self.liveset_card.expand = False

        self.patch_title = ft.Text("", size=13, color=theme.FOREGROUND,
                                   weight=ft.FontWeight.BOLD, expand=True,
                                   no_wrap=True,
                                   overflow=ft.TextOverflow.ELLIPSIS)
        self.add_editor_btn = pv.button(
            "Add editor patch", ft.Icons.PLAYLIST_ADD,
            lambda e: self._add_editor_patch())
        self.add_amp_menu = _menu([], icon=ft.Icons.DOWNLOAD,
                                  tooltip="Add a patch read from the amp")
        self.patch_list = ft.ReorderableListView(
            spacing=4, expand=True, on_reorder=self._on_reorder,
            show_default_drag_handles=True)
        self.patch_body = ft.Column(expand=True, spacing=10)
        self.patch_card = pv.module_card("Patches", [self.patch_body])
        self.patch_card.expand = True

        # Tone Central webview state; downloads land in the library and show
        # up in the liveset list as they arrive.
        self.tone_status = ft.Text("", size=12, color=theme.MUTED)
        self._was_open = False
        self._seen: tuple = ()

        self.root = ft.Column([
            ft.Container(toolbar,   # room for the floating field label
                         padding=ft.Padding(left=0, right=0, top=8, bottom=0)),
            self.tone_status,
            ft.Row([self.liveset_card, self.patch_card], expand=True,
                   spacing=12,
                   vertical_alignment=ft.CrossAxisAlignment.STRETCH),
        ], expand=True, spacing=12)
        self.refresh()

    # ------------------------------------------------------- tone central

    def _tone_confirm(self) -> None:
        """Tell the user they are leaving for a third-party site before the
        Tone Exchange window opens."""
        ft.context.disable_auto_update()
        link = ft.TextSpan(
            TERMS_URL, url=TERMS_URL,
            style=ft.TextStyle(color=theme.ACCENT,
                               decoration=ft.TextDecoration.UNDERLINE))

        def ok(e) -> None:
            """Close the dialog and open the Tone Exchange window."""
            self.app.page.pop_dialog()
            self.app.tonecentral_open(DEFAULT_URL)

        self.app.page.show_dialog(ft.AlertDialog(
            title=ft.Text("Opening the BOSS Tone Exchange"),
            content=ft.Column([
                ft.Text("You will be redirected to the BOSS Tone Exchange "
                        "website (bosstoneexchange.com). By using it you must "
                        "abide by its terms of service:"),
                ft.Text(spans=[link]),
                ft.Text("The website and all of its content belong to Roland "
                        "and BOSS. They are not affiliated with this software "
                        "in any way.", color=theme.MUTED),
                ft.Text("Only download and share livesets you have the right "
                        "to use.", color=theme.MUTED),
            ], tight=True, spacing=10, width=420),
            bgcolor=theme.PANEL_TOP,
            actions=[pv.link_button("Cancel",
                                    lambda e: self.app.page.pop_dialog(),
                                    color=theme.MUTED),
                     pv.button("Continue", ft.Icons.OPEN_IN_NEW, ok,
                               primary=True)]))

    def refresh_tone(self) -> None:
        """Reflect the webview state in the status line and list livesets
        downloaded in the Tone Central window as they arrive."""
        proc = getattr(self.app, "tc_proc", None)
        if proc is not None and proc.poll() is None:
            self._was_open = True
            self.tone_status.value = (
                "Tone Central window open — sign in there with your BOSS ID "
                "to download; a downloaded liveset is added to the list "
                "below.")
        elif self._was_open:
            # The window just closed: say whether a BOSS ID session is kept.
            self._was_open = False
            self.tone_status.value = (
                "BOSS ID session active — liveset downloads work."
                if stored_refresh_token() else "Not signed in to BOSS ID.")
        else:
            self.tone_status.value = ""
        seen = tuple((e.name, e.mtime, e.count) for e in self.app.lib.list())
        if seen != self._seen:
            # A download arrived: show its liveset (also redraws the status).
            known = {n for n, _, _ in self._seen}
            new = [n for n, _, _ in seen if n not in known]
            if new and self._seen:
                self.selected = new[-1]
            self.refresh()
        else:
            _safe_update(self.root)

    # ----------------------------------------------------------- rendering

    def refresh(self) -> None:
        """Re-list the livesets, keep a valid selection, redraw both columns
        and push the result to the client."""
        entries = self.app.lib.list()
        self._seen = tuple((e.name, e.mtime, e.count) for e in entries)
        names = self._names = [e.name for e in entries]
        if self.selected not in names:
            self.selected = names[0] if names else None
        self._load_selected()
        self.liveset_list.controls = (
            [self._liveset_row(e) for e in entries] or
            [ft.Text(f"No livesets yet in {self.app.lib.root}.\n"
                     "Create one above or import a .tsl file.",
                     color=theme.MUTED, size=12)])
        self._render_patches()
        _safe_update(self.root)

    def _load_selected(self) -> None:
        """Load the selected liveset into ``self.tsl``; on a read failure
        ``self.tsl`` stays ``None`` and the error goes to the status bar."""
        self.tsl = None
        if self.selected:
            try:
                self.tsl = self.app.lib.load(self.selected)
            except Exception as e:                    # noqa: BLE001
                self.app.set_status(f"Cannot read {self.selected!r}: {e}")

    def _liveset_row(self, e) -> ft.Control:
        """One liveset row: folder icon, name, patch count and mtime, plus a
        kebab menu (Rename, Duplicate, Export, Delete); clicking selects it.

        Args:
            e: a directory entry from ``app.lib.list()``.

        Returns:
            The row's ``ft.Container``.
        """
        active = e.name == self.selected
        ts = datetime.datetime.fromtimestamp(e.mtime).strftime("%Y-%m-%d %H:%M")
        count = "unreadable" if e.count is None else (
            f"{e.count} patch{'es' if e.count != 1 else ''}")
        return ft.Container(
            content=ft.Row([
                ft.Icon(ft.Icons.FOLDER_OPEN if active else ft.Icons.FOLDER,
                        color=theme.ACCENT if active else theme.MUTED,
                        size=20),
                ft.Column([
                    ft.Text(e.name, size=14, color=theme.FOREGROUND,
                            weight=ft.FontWeight.BOLD if active else None,
                            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(f"{count}  ·  {ts}", size=11, color=theme.MUTED),
                ], spacing=0, expand=True),
                _menu([
                    ("Rename…", ft.Icons.EDIT,
                     lambda ev, n=e.name: self._rename_liveset(n)),
                    ("Duplicate", ft.Icons.COPY_ALL,
                     lambda ev, n=e.name: self._duplicate_liveset(n)),
                    ("Export…", ft.Icons.SAVE_ALT, self._export_handler(e.name)),
                    ("Delete…", ft.Icons.DELETE_OUTLINE,
                     lambda ev, n=e.name: self._delete_liveset(n)),
                ]),
            ], spacing=10, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=theme.TAB_ACTIVE if active else theme.KNOB_LO,
            border=ft.Border(left=ft.BorderSide(3, theme.ACCENT if active
                                                else "transparent")),
            border_radius=6,
            padding=ft.Padding(left=10, right=2, top=6, bottom=6),
            on_click=lambda ev, n=e.name: self._select(n))

    def _render_patches(self) -> None:
        """Redraw the right column for ``self.tsl``: the title with the
        ``n/MAX_PATCHES`` count, the add buttons (disabled when the liveset
        is full) and one row per patch, or an empty-liveset hint."""
        tsl = self.tsl
        if tsl is None:
            self.patch_title.value = "No liveset selected"
            self.patch_body.controls = [self.patch_title]
            return
        n = len(tsl)
        self.patch_title.value = f"{tsl.name}  ·  {n}/{MAX_PATCHES}"
        full = n >= MAX_PATCHES
        self.add_editor_btn.disabled = full
        self.add_amp_menu.disabled = full
        self.add_amp_menu.items = _menu(
            [(f"{s.short}  ·  {s.long}", None,
              lambda e, n=s.index: self._add_amp_patch(n))
             for s in self.app.slots()]).items
        others = [e for e in self._names if e != tsl.name]
        self.patch_list.controls = [self._patch_row(i, others)
                                    for i in range(n)]
        hint = ft.Text("Empty liveset — add the editor's patch or read one "
                       "from the amp.", color=theme.MUTED, size=12)
        self.patch_body.controls = [
            ft.Row([self.patch_title, self.add_editor_btn,
                    ft.Text("From amp", size=12, color=theme.MUTED),
                    self.add_amp_menu],
                   spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            self.patch_list if n else hint,
        ]

    def _patch_row(self, i: int, others: list[str]) -> ft.Control:
        """One patch row: number, name (plus a TONE CENTRAL tag when the
        memo says so), a Load-into-editor button, a "write to amp slot" menu
        and a kebab menu (Rename, Duplicate, Copy/Move to each other
        liveset, Delete).

        Args:
            i: index of the patch within ``self.tsl``.
            others: names of the other livesets (the Copy/Move targets).

        Returns:
            The row's ``ft.Container``.
        """
        tsl = self.tsl
        name = tsl.patch_name(i)
        empty = not tsl.patches[i]
        memo = tsl.memos[i]
        from_tc = isinstance(memo, dict) and memo.get("isToneCentralPatch")
        menu = [
            ("Rename…", ft.Icons.EDIT, lambda e, i=i: self._rename_patch(i)),
            ("Duplicate", ft.Icons.COPY_ALL,
             lambda e, i=i: self._mutate(lambda t: t.duplicate(i))),
            *[(f"Copy to “{o}”", ft.Icons.CONTENT_COPY,
               lambda e, i=i, o=o: self._transfer(i, o, move=False))
              for o in others],
            *[(f"Move to “{o}”", ft.Icons.DRIVE_FILE_MOVE,
               lambda e, i=i, o=o: self._transfer(i, o, move=True))
              for o in others],
            ("Delete", ft.Icons.DELETE_OUTLINE,
             lambda e, i=i: self._mutate(lambda t: t.remove(i))),
        ]
        send = _menu([(f"{s.short}  ·  {s.long}", None,
                       lambda e, i=i, n=s.index: self._send_to_amp(i, n))
                      # never write to PANEL
                      for s in self.app.slots() if s.index != 0],
                     icon=ft.Icons.UPLOAD, tooltip="Write to amp slot…")
        return ft.Container(
            key=f"{tsl.name}:{i}:{len(tsl)}",
            content=ft.Row([
                ft.Text(f"{i + 1:>2}", width=24, size=13,
                        weight=ft.FontWeight.BOLD, color=theme.ACCENT),
                ft.Column([
                    ft.Text(name or ("(empty)" if empty else "(no name)"),
                            size=14, no_wrap=True,
                            color=theme.MUTED if empty else theme.FOREGROUND,
                            overflow=ft.TextOverflow.ELLIPSIS),
                    *([ft.Text("TONE CENTRAL", size=9, color=theme.ACCENT,
                               style=ft.TextStyle(letter_spacing=1.5))]
                      if from_tc else []),
                ], spacing=0, expand=True),
                ft.IconButton(ft.Icons.TUNE, icon_color=theme.ACCENT,
                              tooltip="Load into editor", disabled=empty,
                              on_click=lambda e, i=i: self._load_patch(i)),
                send,
                _menu(menu),
            ], spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            bgcolor=theme.KNOB_LO, border=ft.Border.all(1, theme.PANEL_EDGE),
            border_radius=6,
            padding=ft.Padding(left=10, right=36, top=2, bottom=2))

    # ------------------------------------------------------- liveset ops

    def _select(self, name: str) -> None:
        """Select the liveset named ``name`` and refresh (a no-op if it is
        already selected)."""
        ft.context.disable_auto_update()
        if name != self.selected:
            self.selected = name
            self.refresh()

    def _create(self) -> None:
        """Create a liveset named after the toolbar field (``"New liveset"``
        when it is empty), select it and refresh; a bad or already-taken
        name is reported in the status bar."""
        ft.context.disable_auto_update()
        try:
            tsl = self.app.lib.create(self.new_field.value or "New liveset")
        except ValueError as e:
            self.app.set_status(str(e))
            return
        self.new_field.value = ""
        self.selected = tsl.name
        self.app.set_status(f"Created liveset {tsl.name!r}.")
        self.refresh()

    def _duplicate_liveset(self, name: str) -> None:
        """Duplicate the liveset named ``name`` (the copy gets a new name),
        select the copy and refresh."""
        ft.context.disable_auto_update()
        try:
            tsl = self.app.lib.duplicate(name)
        except ValueError as e:
            self.app.set_status(str(e))
            return
        self.selected = tsl.name
        self.app.set_status(f"Duplicated {name!r} -> {tsl.name!r}.")
        self.refresh()

    def _rename_liveset(self, name: str) -> None:
        """Prompt for a new name (max 64 chars) and rename the liveset; a
        failed rename is reported in the status bar."""
        def done(new: str) -> None:
            """Apply the rename and refresh; failures go to the status bar."""
            try:
                self.selected = self.app.lib.rename(name, new)
            except (OSError, ValueError) as e:
                self.app.set_status(f"Rename failed: {e}")
                return
            self.app.set_status(f"Renamed {name!r} -> {self.selected!r}.")
            self.refresh()
        self._prompt("Rename liveset", name, done, max_length=64)

    def _delete_liveset(self, name: str) -> None:
        """Ask for confirmation, then delete the liveset and all its
        patches.

        Args:
            name: the liveset to delete.
        """
        def done() -> None:
            """Delete the liveset, clear the selection if it was active and
            refresh."""
            self.app.lib.remove(name)
            if self.selected == name:
                self.selected = None
            self.app.set_status(f"Deleted liveset {name!r}.")
            self.refresh()
        self._confirm(f"Delete liveset “{name}” and all its patches?", done)

    # --------------------------------------------------------- patch ops

    def _mutate(self, fn, status: Optional[str] = None) -> bool:
        """Apply ``fn(tsl)`` to the selected liveset, save and redraw.

        Args:
            fn: a callable taking the liveset's ``Tsl`` and mutating it
                (``add``, ``remove``, ``rename_patch``, ``move``,
                ``duplicate``).
            status: optional status-bar message to show on success.

        Returns:
            ``True`` if applied and saved; ``False`` when there is no loaded
            liveset or the mutation hit ``LivesetFull`` (reported in the
            status bar).
        """
        ft.context.disable_auto_update()
        if self.tsl is None:
            return False
        try:
            fn(self.tsl)
        except LivesetFull as e:
            self.app.set_status(str(e))
            return False
        self.app.lib.save(self.tsl)
        if status:
            self.app.set_status(status)
        self.refresh()
        return True

    def _on_reorder(self, e: ft.OnReorderEvent) -> None:
        """Reorder handler: move the dragged patch from ``e.old_index`` to
        ``e.new_index`` within the selected liveset."""
        if e.old_index is None or e.new_index is None:
            return
        self._mutate(lambda t: t.move(e.old_index, e.new_index))

    def _add_editor_patch(self) -> None:
        """Append the editor's current patch (``Tsl.encode`` of the model)
        to the selected liveset."""
        name = self.app.patch_name() or "editor patch"
        self._mutate(lambda t: t.add(Tsl.encode(self.app.ps)),
                     f"Added {name!r} to {self.selected!r}.")

    def _add_amp_patch(self, n: int) -> None:
        """Read the patch of amp slot ``n`` and append it to the selected
        liveset once the read finishes.

        Args:
            n: the amp slot index to read from.
        """
        ft.context.disable_auto_update()
        target = self.selected
        if target is None:
            return

        def done(ps: ParamSet) -> None:
            """Add the just-read patch to the liveset (reloaded first, since
            this runs on the read thread) and refresh."""
            # Runs on the read thread: reload in case the liveset changed.
            tsl = self.app.lib.load(target)
            try:
                tsl.add(Tsl.encode(ps))
            except LivesetFull as e:
                self.app.set_status(str(e))
                return
            self.app.lib.save(tsl)
            name = ParamSet.patch_name(ps.block("PATCH%COM"))
            self.app.set_status(f"Added {self.app.slot_name(n)} {name!r} "
                                f"to {target!r}.")
            self.refresh()
        self.app.read_amp_patch(n, done)

    def _load_patch(self, i: int) -> None:
        """Load patch ``i`` of the selected liveset into the editor."""
        ft.context.disable_auto_update()
        tsl = self.tsl
        label = f"{tsl.patch_name(i) or f'patch {i + 1}'!r} from {tsl.name!r}"
        self.app.load_into_editor(tsl.to_paramset(i), label)

    def _send_to_amp(self, i: int, n: int) -> None:
        """Write patch ``i`` of the selected liveset to amp slot ``n``."""
        ft.context.disable_auto_update()
        tsl = self.tsl
        self.app.write_amp_patch(n, tsl.to_paramset(i), tsl.patch_name(i))

    def _rename_patch(self, i: int) -> None:
        """Prompt for a new name (max 16 chars) and rename patch ``i``."""
        self._prompt("Rename patch", self.tsl.patch_name(i),
                     lambda new: self._mutate(lambda t: t.rename_patch(i, new)),
                     max_length=16)

    def _transfer(self, i: int, other: str, move: bool) -> None:
        """Copy (or move) patch ``i`` of the selected liveset into the
        liveset named ``other``.

        Args:
            i: index of the patch within the selected liveset.
            other: name of the destination liveset.
            move: ``True`` to also remove the patch from the source liveset.
        """
        ft.context.disable_auto_update()
        dest = self.app.lib.load(other)
        try:
            dest.add(self.tsl.patches[i], self.tsl.memos[i])
        except LivesetFull as e:
            self.app.set_status(str(e))
            return
        self.app.lib.save(dest)
        verb = "Moved" if move else "Copied"
        status = f"{verb} {self.tsl.patch_name(i)!r} to {other!r}."
        if move:
            self._mutate(lambda t: t.remove(i), status)
        else:
            self.app.set_status(status)
            self.refresh()

    # ----------------------------------------------------- import/export

    async def _import(self, e) -> None:
        """Pick one or more .tsl files with the native chooser and import them."""
        ft.context.disable_auto_update()
        try:
            files = await self._picker.pick_files(
                dialog_title="Import liveset",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["tsl"], allow_multiple=True)
        except Exception as ex:                       # noqa: BLE001
            # e.g. no zenity/kdialog available for the desktop dialog
            self.app.set_status(f"File chooser unavailable: {ex}")
            return
        paths = [f.path for f in files or [] if f.path]
        if not paths:
            self.app.set_status("Import cancelled.")
            return
        for path in paths:
            self.app.import_liveset(path)

    def _export_handler(self, name: str):
        """Wrap :meth:`_export` in a click handler for the Export… menu
        entry.

        Args:
            name: the liveset to export.

        Returns:
            An async callback for the menu item's ``on_click``.
        """
        async def handler(e) -> None:
            """Export ``name`` through the native save dialog."""
            await self._export(name)
        return handler

    async def _export(self, name: str) -> None:
        """Ask for the destination folder + file name with the native "Save
        as" dialog, then copy the liveset there.

        Args:
            name: the liveset to export (its ``.tsl`` file under the library
                root).
        """
        ft.context.disable_auto_update()
        src = self.app.lib.root / f"{name}.tsl"
        start = pathlib.Path.home() / "Desktop"
        if not start.exists():
            start = pathlib.Path.home()
        try:
            chosen = await self._picker.save_file(
                dialog_title=f"Export liveset {name!r}",
                file_name=f"{name}.tsl", initial_directory=str(start),
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["tsl"])
        except Exception as ex:                       # noqa: BLE001
            self.app.set_status(f"File chooser unavailable: {ex}")
            return
        if not chosen:
            self.app.set_status("Export cancelled.")
            return
        dest = pathlib.Path(chosen)
        if dest.suffix.lower() != ".tsl":
            dest = dest.with_name(dest.name + ".tsl")
        try:
            shutil.copy(src, dest)
            self.app.set_status(f"Exported -> {dest}")
        except Exception as e:                        # noqa: BLE001
            self.app.set_status(f"Export failed: {e}")

    # ----------------------------------------------------------- dialogs

    def _prompt(self, title: str, value: str, on_ok: Callable[[str], None],
                max_length: int) -> None:
        """Show a single-line text dialog pre-filled with ``value``.

        Args:
            title: the dialog title.
            value: initial field content (e.g. the current name).
            on_ok: called with the stripped field text when Save is
                pressed; skipped when the field is left empty.
            max_length: maximum number of characters the field accepts.
        """
        ft.context.disable_auto_update()
        field = ft.TextField(value=value, autofocus=True, max_length=max_length,
                             **pv.FIELD_STYLE)

        def ok(e=None) -> None:
            """Close the dialog and hand the stripped field text to
            ``on_ok``."""
            self.app.page.pop_dialog()
            if (field.value or "").strip():
                on_ok(field.value.strip())

        field.on_submit = ok
        self.app.page.show_dialog(ft.AlertDialog(
            title=ft.Text(title), content=field, bgcolor=theme.PANEL_TOP,
            actions=[pv.link_button("Cancel",
                                    lambda e: self.app.page.pop_dialog(),
                                    color=theme.MUTED),
                     pv.button("Save", ft.Icons.CHECK, ok, primary=True)]))

    def _confirm(self, text: str, on_ok: Callable[[], None]) -> None:
        """Show a confirm dialog with the message ``text``.

        Args:
            text: the question shown to the user.
            on_ok: called when the confirm (Delete) button is pressed.
        """
        ft.context.disable_auto_update()

        def ok(e) -> None:
            """Close the dialog and run the confirmed action."""
            self.app.page.pop_dialog()
            on_ok()

        self.app.page.show_dialog(ft.AlertDialog(
            title=ft.Text("Confirm"), content=ft.Text(text),
            bgcolor=theme.PANEL_TOP,
            actions=[pv.link_button("Cancel",
                                    lambda e: self.app.page.pop_dialog(),
                                    color=theme.MUTED),
                     pv.button("Delete", ft.Icons.DELETE_OUTLINE, ok,
                               primary=True)]))
