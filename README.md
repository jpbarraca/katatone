# KataTone

A Linux patch editor for the **BOSS Katana 50 Gen 3** amplifier.

Edit every parameter of a patch, keep a local library of patches, download
patches from BOSS Tone Central, and control the amp from MIDI footswitches,
pedals and keyboards.  It talks to the amp over USB-MIDI, and runs as a desktop
window or as a browser interface, for example on a Raspberry Pi next to the amp.

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
- **Analyzer** — level meter and spectrum of the amp's USB audio
  ([details](docs/analyzer.md))
- **Tuner** — a guitar tuner for the amp's audio input ([details](docs/tuner.md))
- **System settings** — edit the amp's global settings
- **MIDI controllers** — switch channels, toggle effects and drive wah / volume
  from pedals and keyboards ([details](docs/midi-controllers.md))
- **Browser interface** — the same app in a browser, with a login
  ([details](docs/web.md))
- **Raspberry Pi** — headless use, Wi-Fi with an access-point fallback, read-only
  file system ([details](docs/raspberry-pi.md))

## Install

Needs Linux and Python 3.10+:

```bash
pipx install --system-site-packages "katatone[desktop] @ git+https://github.com/jpbarraca/katatone"
```

Extras, system packages and troubleshooting: [Install](docs/install.md).

## Use

Connect the amp with a USB cable and run `katatone-gui` for the window or `katatone-web` for
the browser.  It connects to the amp by itself.  Edit a tone in the **PATCH** tab,
then **Save to library** to keep it, or **Write** to store it in an amp slot.

`katatone-gui --help` and `katatone-web --help` list the options.  There is also a command line: `katatone ports`, `katatone read --in KATANA3 --out
KATANA3 --patch 0`, ... — see `katatone --help`.

## Terms of service

KataTone makes no requests of its own to the BOSS TONE EXCHANGE and is built to
stay within its [terms of service](https://bosstoneexchange.com/terms_and_conditions/);
the details are in [Tone Central](docs/tone-central.md#terms-of-service).

## More

- [Development](docs/development.md) — project layout, running the tests
- Developed and tested with a Katana 50 Gen 3 only.
- License: Apache 2.0 — see [`LICENSE`](LICENSE).
