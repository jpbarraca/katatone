# Flatpak

KataTone's desktop GUI can be installed as a Flatpak: the application, its Python
packages, the Flet desktop client, `arecord` and `zenity` are bundled, so nothing is
downloaded when it starts.  The amp is used **over USB**.  Bluetooth (the amp's BLE-MIDI
adapter and Bluetooth MIDI controllers) and the web server are not part of this package.

## Install

You need Flatpak and the GNOME 50 runtime (Flatpak fetches it when it is missing):

```bash
flatpak install --user flathub org.gnome.Platform//50
flatpak install --user katatone-0.5.0-x86_64.flatpak     # the file from the release page
flatpak run io.github.jpbarraca.katatone
```

It also appears as **KataTone** in the application menu.

## What the sandbox can reach

| Permission | Why |
|---|---|
| `--device=all` | the amp's MIDI ports (`/dev/snd/midiC*D*`) and the sound card read by the Tuner and the Analyzer |
| `--socket=wayland`, `--socket=x11` | the window; Flet runs in web mode (and tries to install `flet-web`) when `DISPLAY` is unset, so the X11 socket is needed even on Wayland |
| `--socket=pulseaudio` | the `pulse` and `pipewire` inputs of the sound card list |
| `--share=network` | the Tone Central window |

Its data (the patch library, MIDI controller profiles and the Tone Central login) is kept
in `~/.var/app/io.github.jpbarraca.katatone/data/katatone`, apart from the
`~/.local/share/katatone` of a `pipx` or `pip` install.  To use the same library, copy
or link the two folders.  Files for **Import** and **Export** are chosen with the
desktop's file chooser (through `zenity` and the desktop portal).

`KATATONE_NO_BLUETOOTH=1` is set in the sandbox so that the main window does not report a
missing Bluetooth package.

## Build it yourself

```bash
flatpak install --user flathub org.gnome.Sdk//50
cd packaging/flatpak
flatpak-builder --user --install --force-clean --disable-rofiles-fuse \
    --state-dir=/tmp/katatone-fp /tmp/katatone-fp-build io.github.jpbarraca.katatone.yml
```

To make the file of a release by hand (after building as above, with
`--repo=/tmp/katatone-repo` added to the `flatpak-builder` command):

```bash
flatpak build-bundle /tmp/katatone-repo katatone-0.4.1-x86_64.flatpak io.github.jpbarraca.katatone master
```

A release does not need this: pushing a version tag builds and attaches the file, see
[Releasing](development.md#releasing).

Two parts are pinned and have to be refreshed by hand when they change:

* **The Python packages** are listed with checksums in `python3-deps.json`, made by
  [flatpak-pip-generator](https://github.com/flatpak/flatpak-builder-tools/tree/master/pip):
  `python3 flatpak-pip-generator.py --runtime org.gnome.Sdk//50 --prefer-wheels=numpy,msgpack
  --wheel-arches x86_64 --output python3-deps "flet-desktop==X.Y.Z" "numpy>=1.26"
  "setuptools>=68"`.  The `flet` version must match the Flet client below.
* **The Flet client** (`flet-linux-ubuntu24.04-light-amd64.tar.gz` of the same Flet
  release; the name follows the glibc of the runtime, see `flet_desktop`) and its checksum
  are in the manifest.
* `alsa-utils` has to match the runtime's alsa-lib (1.2.14 for GNOME 50).

## Checks with the amp

On a machine with a Katana connected over USB, run these from the installed Flatpak:
connect and read a patch; open the **Tuner** and the **Analyzer** and pick the amp's card;
import and export a liveset; open **Tone Central** and sign in; unplug the amp and plug it
in again (it should reconnect).  `flatpak run --command=arecord io.github.jpbarraca.katatone -l`
lists the sound cards the sandbox sees, `flatpak run --command=sh io.github.jpbarraca.katatone -c 'ls /dev/snd'`
the devices.
