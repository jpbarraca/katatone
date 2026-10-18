# Install and troubleshooting

Needs Linux and Python 3.10+.  With [pipx](https://pipx.pypa.io/):

```bash
pipx install --system-site-packages "katatone[desktop] @ git+https://github.com/jpbarraca/katatone"
```

Upgrade with `pipx upgrade katatone`.

## Extras

Pick the extras you need, so each machine installs only what it uses:

| Extra | For |
|---|---|
| `desktop` | the window (`katatone-gui`) |
| `web` | the browser interface (`katatone-gui --web`), e.g. a headless Raspberry Pi |
| `desktop,web` | both |

The command line (`katatone`) needs neither.

## System packages

- The Analyzer and the Tuner need `arecord` (package `alsa-utils`).
- Tone Central's desktop window (Library tab) needs WebKitGTK and PyGObject from
  your distribution (`webkit2gtk-4.1-0 python3-gi gir1.2-webkit2-4.1` on
  Debian/Ubuntu, `webkit2gtk4.1 python3-gobject` on Fedora).  In the browser
  interface it opens the site in a new tab and needs neither.

## Troubleshooting

- *Amp not listed* — check the cable; `lsusb | grep -i roland` should show it.
- *"Permission denied"* — add yourself to the `audio` group
  (`sudo usermod -aG audio $USER`) and log in again.
- *Port busy* — close any other program using the amp's MIDI port.
