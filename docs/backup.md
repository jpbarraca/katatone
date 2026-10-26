# Backup and restore

A backup is one file with the amp's ten user patches and its system settings.
Make one before you overwrite slots, and keep it when you try something new.

## In the window

Open the **Library** tab (the amp must be connected).

- **Back up amp…** reads the ten patches and the system settings, then asks where
  to save the file (in a browser it is downloaded).  The status line shows the
  progress.
- **Restore amp…** asks for a backup file, shows the ten patch names and asks
  for confirmation.  It then **overwrites all ten patches and the system
  settings**.  The amp keeps playing what it played before.

Before a restore overwrites anything, KataTone saves what the amp holds now to
`before-restore-<time>.ktbackup` in the `backups` folder of its data directory
(`~/.local/share/katatone/backups`), so a restore can be undone by restoring
that file.

The first time in a session that you write a patch, clear a slot or send a
library patch to the amp, KataTone asks whether to back up first.  **Back up
first** makes the backup and cancels that operation (run it again afterwards);
**Continue without a backup** goes ahead.  It asks once per session.

## On the command line

```bash
katatone backup  --in KATANA3 --out KATANA3 amp.ktbackup
katatone restore --in KATANA3 --out KATANA3 amp.ktbackup        # asks first
katatone restore --in KATANA3 --out KATANA3 amp.ktbackup --yes  # does not ask
```

## The file

`.ktbackup` is JSON.  Each patch and the system settings are stored like the
`paramSet` of a `.tsl` liveset (block name to hex bytes).  A backup is only for
the Katana Gen 3, and a file that is not a complete backup is refused before
anything is written to the amp.
