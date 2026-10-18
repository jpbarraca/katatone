"""The login page of the web GUI.

Nothing of the editor exists in a browser session until the password has been
accepted: the page holds only this form, so an unauthenticated visitor can
reach no control (and so no amp or library operation).  While the factory
password is still set, a successful login continues with choosing a new one.
"""
from __future__ import annotations

from typing import Callable

import flet as ft

from ..auth import MIN_LENGTH, Auth, AuthError
from . import params_view as pv, theme


class LoginPage:
    """Shows the login form (then the new-password form) on a page."""

    def __init__(self, page: ft.Page, auth: Auth, on_success: Callable[[], None]) -> None:
        """Replace the page's content with the login form.

        Args:
            page: the browser session's page.
            auth: the password store.
            on_success: called once, after the user is in (page content is
                cleared first); it builds the real GUI.
        """
        self.page, self.auth, self.on_success = page, auth, on_success
        page.theme_mode = "dark"
        page.bgcolor = theme.BG
        page.title = "KataTone"
        page.theme = ft.Theme(color_scheme_seed=theme.ACCENT)
        self.error = ft.Text("", color=theme.DANGER, size=12)
        self._show_login()

    def _card(self, title: str, fields: list[ft.Control], button: ft.Control) -> None:
        """Put a centred card on the page."""
        brand = ft.Text("KATATONE", size=26, weight=ft.FontWeight.W_900, italic=True,
                        color=theme.ACCENT, style=ft.TextStyle(letter_spacing=5))
        card = ft.Container(
            width=340, padding=24, border_radius=10,
            gradient=ft.LinearGradient(begin=ft.Alignment.TOP_CENTER,
                                       end=ft.Alignment.BOTTOM_CENTER,
                                       colors=[theme.PANEL_TOP, theme.PANEL_BOTTOM]),
            border=ft.Border(top=ft.BorderSide(2, theme.ACCENT)),
            content=ft.Column([brand, ft.Text(title, size=13, color=theme.MUTED),
                               *fields, self.error, button], spacing=14, tight=True,
                              horizontal_alignment=ft.CrossAxisAlignment.STRETCH))
        self.page.clean()
        self.page.add(ft.Container(card, alignment=ft.Alignment.CENTER, expand=True))
        self.page.update()

    def _password_field(self, label: str) -> ft.TextField:
        """A masked password input."""
        return ft.TextField(label=label, password=True, can_reveal_password=True,
                            **pv.FIELD_STYLE)

    def _show_login(self) -> None:
        """The password form."""
        self.password = self._password_field("Password")
        self.password.autofocus = True
        self.password.on_submit = lambda e: self.submit()
        self._card("Sign in", [self.password],
                   pv.button("Sign in", ft.Icons.LOGIN, lambda e: self.submit(), primary=True))

    def _show_new_password(self) -> None:
        """The form that replaces the factory password."""
        self.error.value = ""
        self.new1 = self._password_field("New password")
        self.new1.autofocus = True
        self.new2 = self._password_field("Repeat the new password")
        self.new2.on_submit = lambda e: self.submit_new()
        self._card(f"Choose a password ({MIN_LENGTH}+ characters) to continue",
                   [self.new1, self.new2],
                   pv.button("Save", ft.Icons.CHECK, lambda e: self.submit_new(), primary=True))

    def _fail(self, msg: str) -> None:
        """Show an error under the form."""
        self.error.value = msg
        self.page.update()

    def submit(self) -> None:
        """Check the typed password; continue, or explain."""
        wait = self.auth.lockout_remaining()
        if wait:
            self._fail(f"Too many attempts. Try again in {wait} s.")
            return
        if not self.auth.check(self.password.value or ""):
            wait = self.auth.lockout_remaining()
            self.password.value = ""
            self._fail(f"Too many attempts. Try again in {wait} s." if wait
                       else "Wrong password.")
            return
        if self.auth.is_default():
            self._show_new_password()
        else:
            self._done()

    def submit_new(self) -> None:
        """Store the new password and continue."""
        if self.new1.value != self.new2.value:
            self._fail("The passwords differ.")
            return
        try:
            self.auth.set_password(self.new1.value or "")
        except (AuthError, OSError) as e:
            self._fail(str(e))
            return
        self._done()

    def _done(self) -> None:
        """Remove the form and hand over to the GUI."""
        self.page.clean()
        self.on_success()
