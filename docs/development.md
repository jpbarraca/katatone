# Development

## Setup

```bash
git clone https://github.com/jpbarraca/katatone
cd katatone
python3 -m venv --system-site-packages .venv   # system site packages: Tone Central
. .venv/bin/activate
pip install -e '.[desktop,web,dev]'
python -m katatone.gui -v                      # -v: debug logging
```

KataTone is pure Python: MIDI I/O uses the kernel's ALSA rawmidi device files
(`/dev/snd/midiC*D*`) directly, so there is nothing to compile.

## Project layout

```
katatone/
  protocol/     RC6 sysex framing, nibble addressing, MIDI transport
                (ALSA rawmidi device I/O, sysex reassembly)
  address/      Static address map (15 regions; data in katatone/assets/)
  model/        ParamSet — decode/encode params from raw block bytes
  amp/          Connect handshake, read/write patch & system, whole-amp backup
  liveset/      .tsl liveset file format
  library/      Local patch library (~/.local/share/katatone/)
  remote/       Confined Tone Exchange (Tone Central) window
  midi/         Controller input: parser, mappings, router, learn, profiles
  net/          Wi-Fi client / access-point fallback (NetworkManager)
  auth.py       Web GUI password (hashed, with lockout)
  gui/          Flet desktop application (data-driven editor, undo history, MIDI tab)
  cli.py        Command-line interface
dev/            Live-amp check scripts (need connected hardware)
tests/          pytest suite (protocol, model, amp, MIDI routing, GUI)
```

## Testing

```bash
python -m pytest -q
```

The tests run headlessly (no amp or MIDI hardware needed; the MIDI backend is
tested against fake `/dev/snd` trees, and the GUI tests build Flet controls
without a window).  The `dev/` scripts are the opposite: they talk to a real
amp.

GitHub Actions runs the same suite (`.github/workflows/tests.yml`) on every
push to `master` and every pull request.

## Releasing

A release is made by a tag; GitHub Actions (`.github/workflows/release.yml`) does the
rest.

1. Choose the version `X.Y.Z` and write it in **two places**: `version` in
   `pyproject.toml`, and a new `<release version="X.Y.Z" date="...">` at the *top* of
   `<releases>` in `packaging/flatpak/io.github.jpbarraca.katatone.metainfo.xml` (with a
   line on what changed).  `python3 packaging/check_version.py` says whether they agree, and
   the test suite fails when they do not.
2. Commit, push and wait for the tests to pass.
3. Tag that commit and push the tag:

   ```bash
   git tag vX.Y.Z
   git push origin vX.Y.Z
   ```

The workflow then checks that the tag is `v` plus the version in both files, builds the
Flatpak (`katatone-X.Y.Z-x86_64.flatpak` and its `.sha256`), the wheel and the source
archive, and attaches them to the release of that tag.  If the release does not exist it
is created, with the install text of `packaging/release-notes.md` and the subjects of the
commits since the previous tag; if you made the release first (to write its notes by
hand) the files are added to it.  A failed check or build publishes nothing: fix it, then
delete the tag (`git push origin :refs/tags/vX.Y.Z`) and tag again.

**Run workflow** on the Actions page builds all the files without publishing them (they
are kept as artifacts for a few days), which is the way to try a change to the workflow.
The Flatpak build needs the GNOME runtime named in the manifest; when you move the
manifest to a newer runtime, change the two `flatpak install` lines of the workflow too (a
test compares them).

