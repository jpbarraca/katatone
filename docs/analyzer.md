# Analyzer

The **Analyzer** button in the top bar opens a separate window (in a browser session, a
new tab at `/analyzer` of the server, behind the same login, passed as a token by the button) that shows the
level and spectrum of a sound-card input.  It only listens to the sound card; it
does not talk to the amp, and it shares nothing with the editor window, so you
can tweak the sound (an EQ, say) in the editor and watch the result live.

- **Top:** the sound-card input and the channel (1–4).  The amp's USB audio card
  is preselected when the amp is selected in the editor.
- **Left:** a vertical level meter in dBFS (−60 to 0): the bar is the RMS level,
  the white line the held peak and the dot at the top lights on clipping.
  **Clear** resets the peak and the clip light.
- **Options (above the plots):** **Linear frequency scale** (off = logarithmic, 20 Hz–20 kHz, the default; on = linear, 20 Hz–10 kHz,
  which spreads out the range where the guitar plays);
  **Guitar strings** draws a green line for each string in standard tuning,
  sloping from the string's open note at the bottom of the plot (E2 82 Hz, A2 110,
  D3 147, G3 196, B3 247, E4 330) up to its highest note at the top (fret 22: D4
  294 Hz, G4 392, C5 523, F5 698, A5 880, D6 1175), so each line's slope and two
  ends show the range of frequencies that string can play; **Tone zones** shades the
  typical ranges engineers associate with a sound: rumble (20–60 Hz), boomy
  (60–120), warm (120–250), muddy (250–500), boxy (500 Hz–1 kHz), honky (1–2 kHz),
  present (2–4 kHz), harsh / fizzy (4–8 kHz) and airy (8–20 kHz).  The zones are
  rules of thumb; sources differ by a few tens of percent at each edge.
- **Right:** the spectrum of the same channel, 20 Hz–20 kHz (20 Hz–10 kHz when linear), with a
  smoothed trace (amber) and a held-peak trace (dim).

## Matching a tone to a recording

The row under the options compares your sound with something else.  It is a guide
for where to start turning the amp's tone controls, not a measurement of the original
gear.

- **Reference…** loads the part you want to sound like (WAV, or MP3, FLAC, OGG, M4A,
  AAC and Opus when `ffmpeg` is installed; the first four minutes are read).  Pauses
  are left out of the average.  The reference's average spectrum is drawn in blue
  behind the live one.  **✕** forgets it.
- **Set A** / **Set B** keep the current average spectrum of the live input as a take.
  Capture one take, change the amp, capture the other: the take is drawn in green (A) or
  purple (B).  Nothing is captured until the analyzer has heard sound.
- **Compare** chooses what the difference panel shows: *Live vs reference*, *Live vs
  take A*, *Live vs take B* or *Take A vs take B*.
- **Match level** (on by default) lines the two spectra up over 100 Hz–5 kHz first, so
  the panel shows the tone and not how loud each was.
- **Difference panel** (below the spectrum): the first minus the second in dB, ±12 dB
  full scale; it is filled amber where the first has more and blue where it has less.
- **Hints** (the box at the bottom): what the reference is like (brightness as a
  centre frequency and slope in dB per octave, a guess at the gain from the crest
  factor: *Clean*, *Edge of breakup*, *Crunch* or *High gain*, and the tempo when a clear
  pulse is found, with delay times that fit it) and, once you are playing, what to turn: the mean
  difference in the amp's Bass (80–250 Hz), Middle (250 Hz–2 kHz), Treble (2–5 kHz) and
  Presence (5–10 kHz) ranges, the biggest first; bands within 1.5 dB are left out.
- **EQ preview** and the *EQ settings that fit* (in the hints): the difference is fitted to
  the amp's own EQ blocks, so you get values to dial in rather than "less middle".  The
  *Graphic EQ (GE-10)* proposal is a gain in whole dB (within ±12) for each of the ten
  bands, 31 Hz to 16 kHz; the *Parametric EQ (PEQ)* proposal is a low shelf, two mid bands
  (frequency, Q and gain, from the amp's own steps) and a high shelf; the low and high cuts
  are left alone.  Each line shows how far off the tones are before and after (RMS dB).
  The fit is **on top of the EQ the amp already uses**, and it is made for the guitar's
  range (80 Hz–8 kHz counts fully).  The band widths of the GE-10 and the shelf corners
  of the PEQ are assumed, so treat the numbers as a first setting to listen to and trim.
  Choose a proposal in **EQ preview** to draw your spectrum as it would be with it
  (orange) and to see the difference that would be left.  Not offered for *Take A vs
  take B* (take A is not what the amp is doing now) or while the input is stopped.
- **Notes** (in the hints, once at least three clear notes were played): from the last
  12 seconds of playing and from the reference, the typical note's attack time, how fast it
  decays (dB/s), how much level is left 300 ms after the peak and the spectral centre
  of the first 40 ms against the sustained part (the pick).  The hints say when your notes
  die away faster or slower than the reference's (more or less gain, compression or reverb
  tail), when the attack is softer or snappier, and when the pick sounds brighter or duller.
- **Divider** (the grip bar between the spectrum and the lower panel): drag it up or down to
  give the spectrum or the lower panel more height (60–600 px; the spectrum keeps at least 120 px);
  double-click it to go back to the default.
- **Lower panel** chooses what is under the spectrum: the *Difference* or a *Spectrogram*
  of the last 15 seconds (time to the right, frequency up, brighter is louder), which shows
  attacks, sustain and decay, and the reverb or delay tail, as they happen.
- **Check first: the playing differs.**  A spectrum depends on the notes as well as on the
  tone, so before blaming the amp the analyzer compares how the pitch classes (C, C♯ … B)
  are used and the register (where the energy lies between 75 Hz and 1 kHz) in your playing
  and in the other side of the comparison (the reference, or a take, which remembers what
  was played when it was set).  If they differ (a correlation under 0.5, or more than
  0.7 octaves apart) the hints name the notes of both sides and warn that the bass and
  treble hints may come from the notes, not the tone: play the same part.
- **Tap tempo:** tap along with the music; from the second tap it shows the BPM and
  the matching delay times (quarter, dotted eighth, eighth …).

Compare like with like: play the same part, at a similar picking strength, on the
channel that carries the amp's output.  A reference mix includes the other
instruments, so read the hints as direction, not as exact dB.  Messages such as an
unreadable file stay on the status line for a few seconds.

The amp's USB interface offers four 48 kHz channels; which one carries what
(amp output, dry guitar) is easiest to find by trying each.  Audio is read with
`arecord`, so it needs ALSA's `alsa-utils` and `numpy` (installed with
`pip install 'katatone[gui]'`).  If another program holds the card, pick the
`pipewire` or `default` input instead.
