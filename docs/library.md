# Library

The **Library** tab keeps your patches in livesets (`.tsl` files, the same format as
BOSS Tone Studio and Tone Central use).  See also [Backup and restore](backup.md).

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
