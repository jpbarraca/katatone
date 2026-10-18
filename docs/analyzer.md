# Analyzer

The **Analyzer** button in the top bar opens a separate window that shows the
level and spectrum of a sound-card input.  It only listens to the sound card; it
does not talk to the amp, and it shares nothing with the editor window, so you
can tweak the sound (an EQ, say) in the editor and watch the result live.

- **Top:** the sound-card input and the channel (1–4).  The amp's USB audio card
  is preselected when the amp is selected in the editor.
- **Left:** a vertical level meter in dBFS (−60 to 0): the bar is the RMS level,
  the white line the held peak and the dot at the top lights on clipping.
  **Clear** resets the peak and the clip light.
- **Options (above the plots):** **Linear frequency scale** (off = logarithmic, the default);
  **Guitar strings** draws a green line at each open string in standard tuning
  (E2 82 Hz, A2 110, D3 147, G3 196, B3 247, E4 330); **Tone zones** shades the
  typical ranges engineers associate with a sound: rumble (20–60 Hz), boomy
  (60–120), warm (120–250), muddy (250–500), boxy (500 Hz–1 kHz), honky (1–2 kHz),
  present (2–4 kHz), harsh / fizzy (4–8 kHz) and airy (8–20 kHz).  The zones are
  rules of thumb; sources differ by a few tens of percent at each edge.
- **Right:** the spectrum of the same channel, 20 Hz–20 kHz, with a
  smoothed trace (amber) and a held-peak trace (dim).

The amp's USB interface offers four 48 kHz channels; which one carries what
(amp output, dry guitar) is easiest to find by trying each.  Audio is read with
`arecord`, so it needs ALSA's `alsa-utils` and `numpy` (installed with
`pip install 'katatone[gui]'`).  If another program holds the card, pick the
`pipewire` or `default` input instead.
