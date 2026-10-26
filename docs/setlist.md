# Setlist

A setlist plays the patches of a library liveset in order, for a gig.  A liveset
already is an ordered list of patches (up to 15), so there is nothing to convert:
arrange the patches in the [Library](library.md), then open that liveset on the
**SETLIST** tab.

1. Connect the amp.
2. On the **SETLIST** tab choose the liveset and press **Open**.  Nothing is played yet.
3. **Next** plays the first patch, then the following ones; **Previous** goes back.
   Press an entry in the list to jump to it.  The buttons are big, for use on stage.

The ends do not wrap round: Next on the last patch stays there and says so.  Empty
slots of the liveset are skipped.

## How a patch is played

The patch is sent to the amp's **edit buffer**, the same place live edits go.  No slot
is written, so the amp's own patches are untouched and a gig cannot overwrite them.
The sound stays until the amp changes channel.  The editor shows the patch and follows
the amp, so you can adjust it live; use **Write** if you want to keep a change.

## From a footswitch

In the **MIDI** tab add a mapping and choose **Next / previous setlist patch**.  Map
one switch to Next and another to Previous.  See [MIDI controllers](midi-controllers.md).
