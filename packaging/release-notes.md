# KataTone @VERSION@

## Install

The desktop GUI as a **Flatpak** (the amp over USB), x86_64:

```bash
flatpak install --user flathub org.gnome.Platform//50
flatpak install --user katatone-@VERSION@-x86_64.flatpak
flatpak run io.github.jpbarraca.katatone
```

Verify the download with the `.sha256` file: `sha256sum -c katatone-@VERSION@-x86_64.flatpak.sha256`.
Details and the permissions it asks for: [docs/flatpak.md](docs/flatpak.md).

For Bluetooth, the browser interface or a Raspberry Pi, install with `pipx` instead
([docs/install.md](docs/install.md)); the Python package of this release is attached as a
wheel and a source archive.
