# KataTone

A Linux patch editor for the **BOSS Katana 50 Gen 3** amplifier.

Edit every parameter of a patch, keep a local library of patches, download
patches from BOSS Tone Central, and control the amp from MIDI footswitches,
pedals and keyboards.  It talks to the amp over USB-MIDI; a command-line
interface covers the same operations without the GUI.

![KataTone: the patch editor](docs/screenshot.png)

> **Unofficial.**  Not affiliated with or endorsed by Roland or BOSS.  BOSS,
> Katana, BOSS TONE EXCHANGE and BOSS TONE CENTRAL are trademarks of Roland.
> Writing to an amp is at your own risk: save your patches to the library
> before overwriting amp slots.

## Features

- **Patch editor** — all parameters of a patch, with live edits sent to the amp
- **Library** — save patches into livesets (`.tsl` files) and write them back to the amp
- **Tone Central** — browse and download patches from the official BOSS site
  ([details](docs/tone-central.md))
- **Analyzer** — a second window with a level meter and spectrum of the amp's
  USB audio, to watch the result of a tweak ([details](docs/analyzer.md))
- **System settings** — edit the amp's global settings
- **MIDI controllers** — switch channels, toggle effects and drive wah / volume
  from pedals and keyboards ([details](docs/midi-controllers.md))

## Install

Needs Linux and Python 3.10+.  With [pipx](https://pipx.pypa.io/):

```bash
pipx install --system-site-packages "katatone[gui] @ git+https://github.com/jpbarraca/katatone"
```

The Analyzer needs `arecord` (package `alsa-utils`).  Tone Central (in the
Library tab) also needs WebKitGTK and PyGObject from your distribution
(`webkit2gtk-4.1-0 python3-gi gir1.2-webkit2-4.1` on Debian/Ubuntu,
`webkit2gtk4.1 python3-gobject` on Fedora).  Upgrade with
`pipx upgrade katatone`.

## Use

Connect the amp with a USB cable and run:

```bash
katatone-gui
```

Pick the amp in the **Amp** dropdown (it shows as `KATANA3`) and click
**Connect**.  Edit a tone in the **PATCH** tab, then **Save to library** to
keep it, or **Write** to store it in an amp slot.

Command line: `katatone ports`, `katatone read --in KATANA3 --out KATANA3
--patch 0`, ... — see `katatone --help`.

**Troubleshooting**

- *Amp not listed* — check the cable; `lsusb | grep -i roland` should show it.
- *"Permission denied"* — add yourself to the `audio` group
  (`sudo usermod -aG audio $USER`) and log in again.
- *Port busy* — close any other program using the amp's MIDI port.

## Terms of service

KataTone does not aim to violate the BOSS TONE EXCHANGE
[terms of service](https://bosstoneexchange.com/terms_and_conditions/), and it is
built to stay within them:

- It does not scrape, crawl or use bots, and it makes no requests of its own to the
  site. Tone Central is simply the official website shown in a window, and you
  browse and download by hand, as in any browser.
- You sign in with your own BOSS ID on the site itself. KataTone never sees your
  password; it only keeps the site's session cookie on your machine like a standard 
  browser.
- It does not copy, mirror or resell the site or its content. Downloads go to your
  own library, and sharing livesets you do not have the right to share is up to you.
- The only change to the page is a small script that lets its Download button save
  a file in a single-window app to the software library location.

The site and everything on it belong to Roland and BOSS. This project is
independent and has no affiliation with them. If you are a rights holder and see a
problem, please open an issue.

## More

- [Development](docs/development.md) — project layout, running the tests
- Developed and tested with a Katana 50 Gen 3 only.
- License: Apache 2.0 — see [`LICENSE`](LICENSE).
