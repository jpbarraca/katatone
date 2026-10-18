"""Opening and saving files from the GUI, in a desktop window or a browser.

On the desktop the native chooser returns paths.  In a browser a chooser
returns the file's bytes (uploaded by the page), which are written to a
temporary file so callers can treat both the same, and "save" becomes a
download by the browser.
"""
from __future__ import annotations

import atexit
import pathlib
import shutil
import tempfile

import flet as ft

_temp_dirs: list[str] = []


def _temp_dir() -> pathlib.Path:
    """A fresh temporary directory, removed when the program exits."""
    d = tempfile.mkdtemp(prefix="katatone-")
    _temp_dirs.append(d)
    return pathlib.Path(d)


@atexit.register
def _cleanup() -> None:
    """Remove the temporary directories."""
    for d in _temp_dirs:
        shutil.rmtree(d, ignore_errors=True)


async def pick_files(page: ft.Page, picker: ft.FilePicker, title: str,
                     ext: str, multiple: bool = False) -> list[pathlib.Path]:
    """Let the user choose files; return them as local paths.

    Args:
        page: the page (tells desktop from browser).
        picker: the page's ``FilePicker``.
        title: the dialog title.
        ext: the allowed extension without a dot (``"tsl"``).
        multiple: allow choosing several files.
    Returns:
        The chosen files; empty when cancelled.
    Raises:
        Exception from the chooser, e.g. when the desktop has no dialog tool.
    """
    files = await picker.pick_files(
        dialog_title=title, file_type=ft.FilePickerFileType.CUSTOM,
        allowed_extensions=[ext], allow_multiple=multiple, with_data=bool(page.web))
    out: list[pathlib.Path] = []
    for f in files or []:
        if page.web:
            if f.bytes is None:
                continue
            path = _temp_dir() / pathlib.Path(f.name).name
            path.write_bytes(bytes(f.bytes))
            out.append(path)
        elif f.path:
            out.append(pathlib.Path(f.path))
    return out


async def save_bytes(page: ft.Page, picker: ft.FilePicker, title: str,
                     file_name: str, ext: str, data: bytes) -> str | None:
    """Offer ``data`` to the user as a file.

    On the desktop a "Save as" dialog asks where to put it and the file is
    written there; in a browser the browser downloads it.

    Args:
        page: the page (tells desktop from browser).
        picker: the page's ``FilePicker``.
        title: the dialog title.
        file_name: the suggested name, with its extension.
        ext: the extension without a dot, added if the user leaves it out.
        data: the file's content.
    Returns:
        A short description of where it went, or None when cancelled.
    Raises:
        Exception from the chooser; OSError if the file cannot be written.
    """
    if page.web:
        chosen = await picker.save_file(
            dialog_title=title, file_name=file_name, src_bytes=data,
            file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=[ext])
        return "downloaded by the browser" if chosen is not None else None
    start = pathlib.Path.home() / "Desktop"
    if not start.exists():
        start = pathlib.Path.home()
    chosen = await picker.save_file(
        dialog_title=title, file_name=file_name, initial_directory=str(start),
        file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=[ext])
    if not chosen:
        return None
    dest = pathlib.Path(chosen)
    if dest.suffix.lower() != f".{ext}":
        dest = dest.with_name(dest.name + f".{ext}")
    dest.write_bytes(data)
    return str(dest)
