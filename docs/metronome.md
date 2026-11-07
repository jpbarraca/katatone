# Metronome

The **METRONOME** tab plays a click track to a sound card.  It does not talk to the
amp: it only needs ALSA's `alsa-utils` (the click goes out through `aplay`) and a
sound card output.

- **Tempo:** 20 to 300 BPM, set with the slider, the **-5 / -1 / +1 / +5** buttons or
  **Tap tempo** (tap in time; the median of the last few taps is used, and the taps
  start over after two seconds without one).  A change is heard from the next click.
- **Beats per bar:** 1 to 12.  The first beat of a bar is higher and louder; with one
  beat per bar every click is the same.  The lamps show the current beat.
- **Subdivision:** beats only, eighths, triplets or sixteenths; the extra clicks are softer.
- **Volume** of the click.
- **Output** and **Click on:** the sound card output and the channel the click goes to
  (both channels of a pair, or one of channels 1 to 4).  Choose the amp's USB card to hear
  the click in the amp's headphones or speaker, or another output to keep it out of the amp.
  Changing the output while it plays restarts the click on the new one.

The click starts on a first beat each time you press **Start**.  Settings are not
remembered between runs.

## From a footswitch

In the **MIDI** tab add a mapping, pick **Metronome** and choose what it does: **Start /
stop**, **Start**, **Stop**, **Tap tempo**, **Tempo up** or **Tempo down** (by a number of
BPM, 1 to 50, default 5).  It needs no amp connection.  The **Metronome switches** preset
maps CC 80 to 83 to start / stop, tap, tempo down and tempo up; press **Learn** in a
mapping to match your own pedal (see [MIDI controllers](midi-controllers.md)).

## Limits

- The click and the editor run on separate clocks of the sound card and the computer, so
  the click is steady but is not locked to anything else, such as a backing track.
- The lamps are lit when a click is handed to the sound card, a few tens of milliseconds
  before it is heard.
- If another program holds the sound card, pick the `pipewire` or `default` output instead.
