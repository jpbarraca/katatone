# Tuner

The **Tuner** button in the top bar (next to **Analyzer**) opens a guitar tuner in
its own window, or, when KataTone runs with `--web`, in a new browser tab at
`/tuner` of the server (so it works from another machine, behind the same login, which the button's link carries as a token).  Like the
[Analyzer](analyzer.md) it only listens to a sound card: it does not talk to the
amp and shares nothing with the editor.

- **Top:** the sound-card input and the channel (1–4), as in the Analyzer; the
  amp's USB audio card is preselected.  The amp's USB interface offers several
  channels, and which one carries what is easiest to find by trying each: for tuning
  you want the one with the guitar's own sound.
- **Tuning:** *Standard* (E A D G B E), *Drop D*, *Half step down*, *Full step
  down*, *DADGAD*, *Open G*, *Bass* (E A D G) or *Chromatic* (the nearest note, any
  instrument).
- **Reference:** the pitch of A4, 415–450 Hz (440 by default).
- **Check intonation (12th fret):** a checkbox for setting the saddles.  The tuner then
  recognises both the open string and the same string at the 12th fret (the chips show
  both notes: E2 and *12th E3* for the low E), whichever you play:
  1. Play the open string and tune it as usual.  When it is in tune the hint asks for the
     12th fret.  The tuner remembers the pitch it heard (for 30 seconds).
  2. Fret the same string at the 12th fret (a harmonic would always read right) and play
     it.  It is compared with **twice the open string's pitch**, so a slightly flat open
     string does not matter; if the open string was not heard (or too long ago) it is
     compared with the exact octave and the line under the hint says so.  Sharp: the
     string is too short, move the saddle back; flat: move it forward.
  3. Repeat, playing the open string again after each move (adjusting the saddle changes
     it a little), until both read in tune (±5 cents).

  Where two strings are an octave apart (the low D and the D string in Drop D, DADGAD or
  Open G) the sound could be either, and is taken as the open string: click the string's
  chip first.  Not available for *Chromatic*.
- **Display:** the note it hears, how many cents it is above or below that note,
  and a needle on a −50…+50 cent scale (the green band is ±5 cents).  The note and
  the needle turn green when the string is in tune, and the hint says whether to tune
  up or down.  The reading is held for a moment after the string fades.
- **Mute the amp while tuning:** a checkbox that mutes the amp's output (the same
  preview mute as the top bar's **Preview mute**; nothing stored on the amp is
  changed).  Unchecking it, stopping the tuner or closing the window restores the
  sound, also if the window crashes.  It needs the amp to be connected in the main
  window or page, and is greyed out otherwise.
- **Strings:** one chip per string with its target frequency.  The chip nearest to
  what you play is outlined.  Click a chip to tune to that string whatever is
  played (useful for a very slack string); click it again to go back to automatic.

It finds the pitch with the normalised autocorrelation (McLeod) method on the last
4096 samples (about 85 ms), about 1 ms of work per reading, and it does not
mistake a strong second harmonic for the note.  It listens above −55 dBFS, so
quiet noise gives no reading.  As with the Analyzer, if another program holds the
card pick the `pipewire` or `default` input, and it needs `arecord` (`alsa-utils`).
