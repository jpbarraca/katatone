# Browser interface

To use KataTone in a browser instead of a desktop window, run `katatone-web`
(`katatone-gui` is the desktop window, `katatone` the command line):

```bash
katatone-web
```

It prints the address (`http://127.0.0.1:8550/` by default) and opens it in your
browser.  `--host ADDRESS` and `--port N` change where it listens.

## Behaviour

- **A server, not a tab.**  The amp link, the MIDI controllers and the patch state
  belong to the server: pedals keep working with no browser open, and closing or
  refreshing a tab changes nothing.  Only one browser session shows the app at a
  time; signing in elsewhere takes over.
- **Auto-connect.**  The app connects to the amp by itself when it appears, and
  again after it was unplugged.  The **Auto** switch in the top bar turns that off
  (so does **Disconnect**); `--no-auto-connect` starts with it off.  This applies to
  the desktop window too.
- **Files.**  Import and export work through the browser (uploads and downloads).
- **Analyzer and Tuner.**  They open as pages of the server in a new tab; the
  button's link carries a token, so there is no second login.
- **Tone Central.**  It opens the site in a new tab of your browser: download there,
  then **Import .tsl…** in the Library tab.
- **Offline.**  The page is served with its own CanvasKit and fonts, so it works
  without internet.

## Login

The interface asks for a password.  The first one is `KataTone`, and you are asked to
replace it at the first login (`katatone passwd` sets it from a shell).  Five wrong
passwords in a row lock the login for 30 seconds.  The password and the session
travel over plain HTTP, so keep the default `127.0.0.1` unless you trust the network
you expose it on.

## Options

| Flag | Effect |
|---|---|
| `--host`, `--port` | where it listens |
| `--no-auto-connect` | do not connect to the amp by itself |
| `--wifi` | add the Wi-Fi settings (NetworkManager devices) |
| `--reduce-writes` | the file system is read-only; remount it only while writing |
| `--rw-seconds N` | with `--reduce-writes`, how long it stays writable at start (120) |
| `--log` | keep log files of the helper windows |
| `--no-ble-writes` | over Bluetooth, only read from the amp |
| `-h`, `--help` | list every option with its explanation |

For a headless box next to the amp, with Wi-Fi, a read-only file system and a
systemd unit, see [Raspberry Pi](raspberry-pi.md).
