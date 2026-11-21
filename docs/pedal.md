# Pedal tab: position and range

The **Pedal** tab ends with a **Pedal Calibration** block that shows where the expression pedal
is, to set up its range.

- **The number and the bar** are the position (0 to 100) the amp reports for the selected pedal
  effect (the pedal wah, the pedal bend or the 95E wah), live as the pedal moves.  When the
  tab opens (and when you change the pedal effect) KataTone reads where the pedal is now, so
  it is right before you touch it.  It follows the amp while the editor shows the patch the
  amp is playing (see [live following](midi-controllers.md)); a pedal that sends MIDI to
  KataTone moves it too.
- **Lowest and Highest** are the smallest and the largest position seen since you opened the
  tab or pressed **Reset range**.
- **Effect range** shows the effect's own PEDAL MIN and PEDAL MAX (the pedal wah and the 95E
  have them; the bend has none), for comparison.

## Test mode

Your patch may use the pedal for something else than the Pedal FX (a mod or delay setting, the
volume), so that the position does not move.  The **Test mode** button sets the pedal to move
the Pedal FX for as long as it is on, and puts the setting back when you turn it off:

- It keeps what the amp's own expression pedal was set to do, and sets it to **Pedal FX**.  The
  position above then follows the pedal whatever the patch does with it.  The Pedal FX does not
  have to be switched on.
- Only what the amp plays changes.  The patch in the editor is never touched, so what you save
  to the library, or write to the amp, has the patch's own setting; a write also ends the mode.
- Changing channel (on the amp or from KataTone), loading a patch into the editor, writing,
  reading the patch again and disconnecting end it, and put the setting back when it is still the
  same patch.  Choosing the pedal's function by hand in the **Assign** tab ends it too, and
  your choice stands.
- It needs the amp connected and showing the patch it plays; the button springs back otherwise.
- While it is on, the position moving is not an unsaved edit of the patch.

## Setting the range of a pedal

Some pedals have a knob for their range, usually the minimum.  Press the pedal fully heel
down and then fully toe down.  Heel down should read **0** and toe down **100**:

1. If the heel reads more than 0, or the toe less than 100, turn the pedal's MIN (or MAX)
   knob and sweep again.
2. Press **Reset range** after each change, so that Lowest and Highest start again.

The meter shows what the amp reports.  To be sure that only the pedal is being measured, set
the effect's own PEDAL MIN and PEDAL MAX to 0 and 100 while you calibrate (how they affect the
reading has not been checked).
