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

Notes and limits:

- Effect switches and expression only change the amp's live **edit buffer**;
  they never store anything. A switch change marks the patch as edited (use
  *Write* to keep it), a wah sweep does not. The router can never write,
  clear or export patches or touch the SYSTEM settings.
- Channel switching works from any controller and the editor follows: the
  "Amp channel" card and the patch are re-read after a short pause.
- A controller is opened exclusively; if another program holds it the tab shows
  "in use by another program" and retries.
- **CV** needs a CV-to-MIDI converter. **Bluetooth MIDI** and software ports
  are not supported (use USB).
- Run `python -m katatone.gui -v` to log every event and action
  (`katatone.midi.*`); `python dev/midi_capture.py "<name>"` prints raw events.
