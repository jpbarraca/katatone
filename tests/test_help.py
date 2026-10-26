"""--help of katatone and katatone-gui lists every option with an explanation."""
from __future__ import annotations

import pytest

from katatone import cli
from katatone.gui import app as gui_app


def run_help(fn, argv, capsys) -> str:
    with pytest.raises(SystemExit) as e:
        fn(argv)
    assert e.value.code == 0
    return capsys.readouterr().out


def test_web_help_explains_every_flag(capsys) -> None:
    out = run_help(lambda a: gui_app._parse_args(a, web=True), ["--help"], capsys)
    for flag in ("--host", "--port", "--wifi", "--no-auto-connect", "--no-ble-writes",
                 "--reduce-writes", "--rw-seconds", "--log", "--verbose"):
        assert flag in out, flag
    assert "usage: katatone-web" in out and "examples:" in out and "default 8550" in out
    assert "default 120" in out                       # the default is filled in, not "%(default)"
    assert "katatone-gui" in out and "katatone --help" in out


def test_gui_help_lists_the_window_options_only(capsys) -> None:
    out = run_help(gui_app._parse_args, ["--help"], capsys)
    for flag in ("--no-auto-connect", "--no-ble-writes", "--reduce-writes", "--rw-seconds", "--log"):
        assert flag in out, flag
    for flag in ("--host", "--port", "--wifi", "--web"):
        assert flag not in out, flag                  # accepted (hidden), not advertised
    assert "usage: katatone-gui" in out and "katatone-web" in out


def test_katatone_gui_still_accepts_the_browser_flags() -> None:
    o = gui_app._parse_args(["--web", "--host", "0.0.0.0", "--port", "9100", "--wifi"])
    assert (o.web, o.host, o.port, o.wifi, o.prog) == (True, "0.0.0.0", 9100, True, "katatone-gui")


def test_katatone_web_means_the_browser_interface() -> None:
    o = gui_app._parse_args([], web=True)
    assert o.web is True and o.prog == "katatone-web"
    assert gui_app._parse_args([]).web is False


def test_the_entry_points_are_katatone_katatone_gui_and_katatone_web() -> None:
    try:
        import tomllib
    except ImportError:             # Python 3.10
        import tomli as tomllib
    from pathlib import Path
    scripts = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())["project"]["scripts"]
    assert scripts == {"katatone": "katatone.cli:main", "katatone-gui": "katatone.gui.app:main",
                       "katatone-web": "katatone.gui.app:main_web"}


def test_main_web_runs_main_with_the_web_interface_on(monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(gui_app, "main", lambda argv=None, web=False: seen.append((argv, web)))
    gui_app.main_web(["--port", "9"])
    assert seen == [(["--port", "9"], True)]


def test_gui_main_prints_help_and_exits_before_starting_anything(capsys) -> None:
    with pytest.raises(SystemExit) as e:
        gui_app.main(["-h"])
    assert e.value.code == 0 and "usage: katatone-gui" in capsys.readouterr().out


def test_gui_help_flag_does_not_swallow_flet_arguments() -> None:
    opts = gui_app._parse_args(["--web", "--flet-thing", "1"])
    assert opts.web is True


def test_cli_help_lists_every_command_with_a_description(capsys) -> None:
    out = run_help(cli._run, ["--help"], capsys)
    for cmd in ("ports", "connect", "read", "write", "clear", "select", "library", "wifi", "passwd"):
        assert cmd in out
    assert "--reduce-writes" in out and "examples:" in out


@pytest.mark.parametrize("cmd,flags", [
    ("read", ["--in", "--out", "--patch", "--region", "--out-file"]),
    ("write", ["--patch", "--tsl", "--name"]),
    ("library", ["--file", "--dir"]),
    ("wifi", ["--ssid", "--key", "--ap-ssid", "--ap-key", "--dhcp", "--address", "--mask",
              "--gateway", "--dns", "--hostname", "--mdns", "--ap"]),
])
def test_every_command_documents_its_options(cmd, flags, capsys) -> None:
    out = run_help(cli._run, [cmd, "--help"], capsys)
    for f in flags:
        assert f in out, (cmd, f)


def test_every_cli_option_has_help_text() -> None:
    import argparse
    parser_holder = {}
    real = argparse.ArgumentParser.parse_args

    def capture(self, args=None, namespace=None):
        parser_holder["p"] = self
        raise SystemExit(0)
    argparse.ArgumentParser.parse_args = capture
    try:
        with pytest.raises(SystemExit):
            cli._run(["ports"])
    finally:
        argparse.ArgumentParser.parse_args = real
    top = parser_holder["p"]
    subs = next(a for a in top._actions if isinstance(a, argparse._SubParsersAction))
    for name, sp in subs.choices.items():
        for a in sp._actions:
            assert a.help, (name, a.option_strings or a.dest)


def test_wifi_settings_belong_to_katatone_web(monkeypatch) -> None:
    from katatone.net.wifi import Wifi
    monkeypatch.setattr(Wifi, "available", staticmethod(lambda: True))
    with pytest.raises(SystemExit, match="run katatone-web --wifi"):
        gui_app.main(["--wifi"])                       # the desktop window has no such page
    monkeypatch.setattr(Wifi, "available", staticmethod(lambda: False))
    with pytest.raises(SystemExit, match="NetworkManager"):
        gui_app.main_web(["--wifi"])


def test_every_command_reports_the_version(capsys) -> None:
    from katatone import __version__
    for fn, prog in ((cli.main, "katatone"),
                     (gui_app._parse_args, "katatone-gui"),
                     (lambda a: gui_app._parse_args(a, web=True), "katatone-web")):
        out = run_help(fn, ["--version"], capsys)
        assert out.strip() == f"{prog} {__version__}"
