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
