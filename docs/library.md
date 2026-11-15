# Library

The **Library** tab keeps your patches in livesets (`.tsl` files, the same format as
BOSS Tone Studio and Tone Central use).  See also [Backup and restore](backup.md).

## Saving a patch to the library

**Save to library** (on the PATCH tab, and on the [TONE AI](tone-ai.md) tab for a suggestion)
asks where the patch goes, with two choices:

- **Add to an existing liveset**: pick it from the list.  If it already has a patch of the same
  name you are asked whether to overwrite it (its notes are kept).
- **Create a new liveset**: type its name.  A name that is already used is refused (add to that
  liveset with the first choice instead of getting a "Name 2").

Picking from the list chooses the first way, and clicking or typing in the name field the
second.  A liveset holds up to 15 patches, and the library up to 50 livesets.

## Notes

A patch in the library can carry **notes**: free text such as the guitar and pickup it is made
for, the song, or what to remember when playing it.  They are the patch's *memo* in the `.tsl`
file, so they travel with the liveset (a memo that Tone Central wrote as an object keeps its
other entries; only its text changes).  The amp does not store notes.

- **In the editor** the last tab of the patch is **Notes**.  Type there; **Save to
  library** keeps the notes with the patch.  Overwriting a patch replaces its notes with the
  editor's, unless the editor has none, in which case the old ones stay.
- **In the library** a patch's menu has **Notes…**, which edits the notes of that patch in
  place.  A patch that has notes shows a note icon; hover it to read them.
- **Load into editor** brings a patch's notes into the Notes block; so does playing the patch
  from the [setlist](setlist.md).  Reading a patch from the amp starts with empty notes.
- Copying or moving a patch to another liveset takes its notes along.

## Search, tags and favourites

- **Search** shows the livesets whose name, tags or any patch name contain all the
  words you type (ignoring case).
- **Tags** are labels you give a liveset: open its menu, choose **Tags…** and type
  them separated by commas (up to 10 of 24 characters).  The **Tag** list filters by
  one.  Clear the field to remove the tags.
- The **star** on a liveset marks a favourite.  Favourites are listed first, and the
  star in the toolbar shows only them.

Tags and favourites belong to a liveset, not to a single patch, and are kept in
`library.json` next to the `.tsl` files.  They follow a liveset when it is renamed
or deleted.  A duplicate keeps the tags but not the star.

## Compare patches

Compare shows which parameters differ between the patch in the editor and another:

- **Compare with amp** (on the PATCH tab) reads the amp's stored copy of the selected
  slot and compares it with the editor, so you can see what you changed.
- **Compare with editor** (in a patch's menu in the library) compares that patch with
  the editor.

Each difference lists both values, the editor's on the left.  The arrow button takes
the other patch's value into the editor: it is sent to the amp when the editor is
live, marks the patch as edited and can be undone (Ctrl+Z).  The patch name is
shown but changed in the name field.  Parameters of effects that are switched off
count too, so two patches with different unused effect settings differ.

## Importing a KATANA MkII liveset

**Import .tsl…** also takes a `.tsl` file made for a **BOSS Katana MkII** (the file says
`KATANA MkII`).  Each patch is converted to the Gen 3 layout and stored as a normal Gen 3
liveset; `katatone library import --file X.tsl` does the same from a shell.

How it works, and what to expect:

- The MkII and the Gen 3 keep their parameters in different places, so every value is
  moved from its MkII position to its Gen 3 one (about 1,380 parameters: the amp, booster,
  mod, FX, delay, reverb, EQ, the pedal and knob assignments, the names).  The Gen 3 has
  three variants (green, red, yellow) of each booster, effect, delay and reverb slot where the
  MkII has one, so the value goes to all three, and the type of each variant comes from the
  MkII's own per-colour setting.
- **Amp and effect types are renumbered**, and the Gen 3 has fewer amp "types" than the MkII
  has amps: *Combo Crunch* and *Stack Crunch* both become the Gen 3 *Crunch*, *Natural Clean*
  becomes *Clean* and so on.  The Gen 3 amp variations are left at their defaults.  The
  patch will sound **close, not identical**: check it against the original by ear and adjust.
- Whatever the Gen 3 has and the MkII does not stays at its default.  A value with no Gen 3
  equivalent is **not guessed**: it keeps the default, and it is noted.
- Each converted patch gets a line in its **memo** ("Converted from KATANA MkII by KataTone"
  plus the points above, such as which amp type became which).  The status line says how many.
- Nothing is sent to the amp by the import; use *Load into editor* or *Write to amp slot* as
  with any library patch.

The conversion has not been tried on a MkII amp.  It was checked against the layouts of both
models: the Gen 3 positions against the map made from BOSS TONE STUDIO (all 80 Gen 3 blocks, their
sizes and the parameter names agree), and every block of a MkII file against the MkII layout.  Tell
us when a converted patch sounds wrong.
