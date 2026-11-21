# Flatpak

KataTone's desktop GUI can be installed as a Flatpak: the application, its Python
packages, the Flet desktop client, `arecord`, `aplay`, `zenity` and Node.js with npm are bundled, so nothing is
downloaded when it starts.  The amp is used **over USB**.  Bluetooth (the amp's BLE-MIDI
adapter and Bluetooth MIDI controllers) and the web server are not part of this package.

## Install

You need Flatpak and the GNOME 50 runtime (Flatpak fetches it when it is missing):

```bash
flatpak install --user flathub org.gnome.Platform//50
flatpak install --user katatone-0.6.1-x86_64.flatpak     # the file from the release page
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

`ALSA_CONFIG_PATH` points to `/app/share/katatone/alsa.conf` (`packaging/flatpak/alsa.conf`):
the runtime's own ALSA configuration plus a numeric group id for sharing the sound card.
Without it the Tuner and the Analyzer could not capture from the card's `sysdefault` input,
which is the one they preselect: the runtime names the group `audio`, the sandbox has no such
group, and `arecord` stopped with "The field ipc_gid must be a valid group".

Node.js and npm are there for the **Tone AI** tab: **Install the agent** downloads the Claude
agent (a Node program, about 400 MB, into the data directory above) and runs it.  The sandbox's
home directory is private and empty, so the Claude sign-in is kept in
`~/.var/app/io.github.jpbarraca.katatone/data/katatone/claude-config` (`CLAUDE_CONFIG_DIR`,
set by KataTone), and npm's download cache in the app's cache directory.  A Claude sign-in
made on the host with `claude` is not visible in the sandbox; use **Sign in** in the tab.  **Sign in** tries
to open the sign-in page in your browser; the link is shown in the tab in case it does not
open (not verified inside the sandbox yet: please report how it goes).

`KATATONE_NO_BLUETOOTH=1` is set in the sandbox so that the main window does not report a
missing Bluetooth package.

## Build it yourself

You need `flatpak` and `flatpak-builder`.  From the top of the repository:

```bash
make flatpak-deps      # once: the GNOME runtime and SDK the manifest asks for
make flatpak           # build/flatpak/katatone-<version>-x86_64.flatpak and its .sha256
make flatpak-install   # build and install it for this user
make flatpak-run       # start the installed one
make clean-flatpak     # remove build/flatpak
```

`make` alone lists them.  The version comes from `pyproject.toml` and the runtime from the
manifest, and a build stops if the version is not the same in `pyproject.toml` and the
metainfo (`packaging/check_version.py`).  Everything is built under `build/flatpak`; its
`state/` folder keeps the downloaded sources, which is what makes a second build quick.
`STATE=...` puts that cache elsewhere, but it must be on the same file system as `build/`
(`flatpak-builder` links files between them), and `OUT=...` moves the whole build folder.

What the targets run, if you want the commands:

```bash
cd packaging/flatpak
flatpak-builder --user --install --force-clean --disable-rofiles-fuse \
    --state-dir=/tmp/katatone-fp /tmp/katatone-fp-build io.github.jpbarraca.katatone.yml
```

adding `--repo=/tmp/katatone-repo` and then

```bash
flatpak build-bundle /tmp/katatone-repo katatone-0.6.1-x86_64.flatpak io.github.jpbarraca.katatone master
```

makes the file.  A release does not need any of this: pushing a version tag builds and
attaches the file, see [Releasing](development.md#releasing).

Two parts are pinned and have to be refreshed by hand when they change:

* **The Python packages** are listed with checksums in `python3-deps.json`, made by
  [flatpak-pip-generator](https://github.com/flatpak/flatpak-builder-tools/tree/master/pip):
  `python3 flatpak-pip-generator.py --runtime org.gnome.Sdk//50 --prefer-wheels=numpy,msgpack
  --wheel-arches x86_64 --output python3-deps "flet-desktop==X.Y.Z" "numpy>=1.26"
  "setuptools>=68"`.  The `flet` version must match the Flet client below.
* **Node.js** (`node-v24.22.3-linux-x64.tar.xz`, an LTS release, from nodejs.org) and its
  checksum are in the manifest; the Claude agent needs Node 22 or newer.
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
