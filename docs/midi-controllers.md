# MIDI controllers (pedals, keyboards)

Any class-compliant **USB MIDI** controller can control the amp from the
**MIDI** tab; nothing is hard-coded per device.

1. Plug the controller in, open the **MIDI** tab and switch it on under
   *Controllers* (press *Rescan* if it is not listed). The *Monitor* shows what
   it sends. The amp itself is never offered as a controller.
2. Under *Mappings* press **Add mapping**, then **Learn** and press the
   footswitch (or move the pedal). The message is captured; pick what it does:
   - **Select a channel** (Panel, A1 … B2), **channel from program number**,
     **switch bank**, **next / previous channel**;
   - **Next / previous setlist patch**: plays the next or previous patch of the
     open [setlist](setlist.md) through the amp's edit buffer (no slot is written);
   - **Effect on / off** (booster, mod, FX, delay, delay 2, reverb,
     send/return, solo, noise suppressor, pedal FX), toggled, forced on or off,
     or on while held;
   - **Expression**: pedal FX position (wah / bend / 95E, chosen by the patch),
     amp volume, or any numeric patch parameter, with min/max, invert and a
     log curve.
   *Test* runs the action once; the fields below *Learn* can be edited by hand.
3. Mappings save as you edit them. Profiles (new, rename, duplicate, delete,
   import, export) live in `~/.local/share/katatone/midi/` as plain JSON.

Messages understood: note on/off, control change, program change, pitch bend and
channel pressure. A switch that only ever sends one value (no release message)
needs *Fire on every message*; Learn sets it for you.
Learn stops at the first message it receives. A CC whose first value is 0 or
127 is taken for a switch, any other value for a pedal; if a pedal happens to
start at an end stop, change *Mode* to Range in the dialog.

## Presets

**From preset...** next to the profile list makes a new profile from a ready-made one:

| Preset | What it maps |
|---|---|
| Program change pedal | any program change selects a channel, as in the amp's own table |
| Channel up / down | CC 80 next channel, CC 81 previous channel |
| Effect switches | CC 80 to 83 toggle booster, mod, delay and reverb |
| Wah / expression pedal | CC 11 moves the pedal FX (wah, bend or 95E) |
| Volume pedal | CC 7 sets the amp volume |
| Setlist switches | CC 80 next, CC 81 previous patch of the open [setlist](setlist.md) |

The numbers are the usual ones for such pedals (CC 80 to 83 are the standard
"general purpose" controllers that foot controllers often send; 11 is expression and
7 volume), so they are a starting point: open a mapping and use **Learn** to match
your own pedal.  A preset names no controller, so enable yours under **Controllers**.
To share a profile with someone else, use **Export...** and **Import...**.

Notes and limits:

- Effect switches and expression only change the amp's live **edit buffer**;
  they never store anything. A switch change marks the patch as edited (use
  *Write* to keep it), a wah sweep does not. The router can never write,
  clear or export patches or touch the SYSTEM settings.
- Channel switching works from any controller and the editor follows: the
  "Amp channel" card and the patch are re-read after a short pause.
- A controller is opened exclusively; if another program holds it the tab shows
  "in use by another program" and retries.
- **CV** needs a CV-to-MIDI converter.
- **Bluetooth MIDI** controllers work once the system has connected them; see
  [Bluetooth and other sequencer devices](#bluetooth-and-other-sequencer-devices).
- Run `python -m katatone.gui -v` to log every event and action
  (`katatone.midi.*`); `python dev/midi_capture.py "<name>"` prints raw events.

## Bluetooth and other sequencer devices

Bluetooth MIDI devices are not sound cards.  The system pairs and connects them
(`bluetoothctl`, once; mark the device trusted), and then presents them to programs:

- **PipeWire (e.g. Fedora, Raspberry Pi OS with PipeWire):** the device is a PipeWire
  MIDI source, `bluez_midi.<address>`.  KataTone lists it in the **MIDI** tab under the
  device's name.  Enabling it starts a `pw-mididump` process, **runs `pw-link` itself**
  to link the device to it, and reads the messages it prints (notes, controls,
  program changes, pitch bend, pressure; not sysex); closing the input (or switching the
  device off) stops it and the link goes.  It needs `pw-link` and `pw-mididump`
  (package `pipewire-utils`).  If the device goes away the input shows as missing and
  reconnects when it returns.  This is deliberately not routed through the ALSA
  sequencer: a link into a sequencer client makes PipeWire's sequencer bridge subscribe to
  every sequencer port, which takes the amp's USB MIDI device away from KataTone.
- **BlueZ's MIDI bridge (no PipeWire):** the device is a port of a sequencer client;
  KataTone lists it and subscribes to it, with no `pw-link` involved.

Either way it appears next to the USB controllers and works the same way: enable it,
learn its controls, map them.  KataTone lists the output ports of user-space
sequencer clients and of kernel clients that are no sound card (such as
`Midi Through`); sound cards' own clients are the USB devices it already offers, so
nothing appears twice.  It needs `libasound` and no extra Python packages.  KataTone
does not scan or pair: do that once with `bluetoothctl`.
