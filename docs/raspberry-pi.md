# Running KataTone on a Raspberry Pi

KataTone can run headless on a Raspberry Pi (or any Armbian board) connected to
the amp by USB, and be used from a browser on a phone or laptop.

## Install

Install only the pieces you need (see [Install](install.md)): on the Pi,
`pipx install --system-site-packages "katatone[web] @ git+https://github.com/jpbarraca/katatone"`.
The `web` extra brings `flet-web`; `katatone-web` stops with an explanation
if it is missing (Flet would otherwise try to download it at start, which fails
offline or on a read-only file system).  Add `desktop` for a window on a machine
with a screen.  The page is served with its own CanvasKit and fonts (no CDN), so
it renders on the Pi's access point with no internet.

## The server keeps working without a browser

`katatone-web` is a server: the amp link, the MIDI controllers and the
patch state belong to it, not to a browser tab.  Pedals and footswitches keep
switching channels and driving effects with no browser open; a tab that signs in
shows the current state, and closing it (or a refresh, or a phone going to sleep)
changes nothing.  Only one browser session shows the app at a time: signing in
elsewhere takes over, and the other tab says so.

**Auto-connect.**  The server looks for the amp (a MIDI card with `katana` in its
name) every two seconds and connects to it, reads its current patch, and follows
it if it is unplugged or switched off and comes back.  The **Auto** switch in the
top bar turns this off; pressing **Disconnect** turns it off too, and **Connect**
turns it back on.  `--no-auto-connect` starts with it off.  This works in the
desktop window as well.

## Start the web GUI

```bash
katatone-web --wifi --host 0.0.0.0 --port 8550
```

`--wifi` adds the Wi-Fi settings described below.  Without it the web GUI is the
same on any system (a laptop, say) and offers nothing that touches the network;
with it, KataTone refuses to start unless NetworkManager's `nmcli` is installed.

Open `http://<address>:8550/` and sign in.  The password is `KataTone` until you
change it, and the first login makes you choose a new one.  Five wrong
passwords in a row lock the login for 30 seconds.  `katatone passwd` sets the
password from a shell (it is stored hashed in `~/.local/share/katatone/auth.json`).

Traffic is plain HTTP: the login protects the controls, not the network.

To start it at boot, a systemd unit such as `/etc/systemd/system/katatone.service`
(adjust the user and path; the user needs the `audio` group for the amp, and
rights over NetworkManager for the Wi-Fi page: `netdev` group, or run as root):

```ini
[Unit]
Description=KataTone web GUI
After=NetworkManager.service sound.target

[Service]
User=katatone
ExecStart=/home/katatone/.local/bin/katatone-web --wifi --host 0.0.0.0
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Wi-Fi and the fallback access point

With `--wifi`, a **Wi-Fi** button appears and sets:

- the **network** to join (SSID and WPA-PSK key), and
- the device's **own access point** (name and key), `KataTone` / `KataTone` by default.

If the network is not in range, the device starts its access point instead.
Join it and browse to `http://10.42.0.1:8550/` (NetworkManager's default
address for a shared connection).  Leave a key blank to keep the saved one;
clear the network name to use the access point only.  The access point can be
switched off in the dialog (only with a network to join: if that network is then
out of range the device stays offline until you retry from a shell or console).
While the access point is up, **Connect to the network now** tries the saved
network and returns to the access point if it is not found.  Saving reconnects at
once, so the browser may lose the page: join whichever network is up and reload.

### Address and host name

On the network it joins, the device gets its address by DHCP, or you can switch
DHCP off and give an **IP address**, **subnet mask** (`255.255.255.0` or `24`),
**gateway** and up to three **DNS servers**.  This applies to the saved network
only; the access point always uses `10.42.0.1`.  Addresses are checked before
anything is changed, but a wrong one that is valid (the wrong subnet, say) leaves
the device unreachable on that network until the access point takes over, so keep
the access point enabled while you experiment.  The **host name** is set on the
device at once (it needs the same NetworkManager rights as the rest).

This relies on NetworkManager (the Armbian default; `nmcli` must exist) and on
`dnsmasq-base` (`sudo apt install dnsmasq-base`), which NetworkManager runs as the
access point's DHCP and DNS server (clients get addresses in `10.42.0.0/24`);
saving settings with the access point enabled fails with a message if it is missing.  KataTone keeps
two NetworkManager profiles:

| Profile | Mode | Autoconnect priority |
|---|---|---|
| `katatone-client` | joins your network (WPA-PSK) | 100 |
| `katatone-ap` | the device's access point (WPA2-PSK, shared) | 0 |

NetworkManager only tries the client profile when its network is visible, so at
boot it picks the network, else the access point.  It does not go back to the
network on its own while the access point is up; use **Connect to the network now**
in the dialog, or `katatone wifi retry`, to try.  The same is available from a shell:

```bash
katatone wifi status
katatone wifi set --ssid home --key 'my wifi key' --ap-ssid KataTone --ap-key KataTone
katatone wifi retry            # try the network now; the access point returns if not found
katatone wifi set --no-ap      # access point off (--ap: on again); other options keep their value
katatone wifi set --address 192.168.1.50 --mask 255.255.255.0 --gateway 192.168.1.1 --dns '1.1.1.1 8.8.8.8'
katatone wifi set --dhcp
katatone wifi set --hostname katatone     # only the name; nothing reconnects
katatone wifi ensure
```

`wifi set` keeps whatever you leave out (use `--ssid ''` to drop the network).

### Finding the device: `<host name>.local`

With the **Reachable as <host name>.local (avahi)** switch (or
`katatone wifi set --mdns`) KataTone enables and starts `avahi-daemon`, so that
`http://katatone.local:8550/` works on your home network without looking up an
address (phones and laptops resolve `.local` names).  It is greyed out until
`avahi-daemon` is installed (`sudo apt install avahi-daemon`), and changing it needs
the same rights as the rest (root, or `sudo -n systemctl`).

## Tone Central, the Analyzer and the Tuner

Tone Central opens the BOSS Tone Exchange **in a new tab of your browser**: sign in
there with your own BOSS ID, download the liveset, and add it to the library with
**Import .tsl…** (the browser uploads it to the Pi).  KataTone cannot show the
site inside its own page, and does not proxy it.  The desktop window version
needs a screen and WebKitGTK on the machine.

The **Analyzer** and **Tuner** buttons open `/analyzer` and `/tuner` of the same
server in a new tab, so a phone or laptop on the Pi's network can use them.  They
run inside the server and listen to the Pi's sound card.  The button's link carries a
token that the signed-in page was given, so the new tab opens without a second login:
the token opens only these two tools (never the app), is revoked when that page
is closed or replaced by another sign-in, expires after 8 hours, and is lost on a
restart.  Opening `/analyzer` or `/tuner` by hand asks for the password.  Closing the tab stops the capture.  Pick the amp's
USB input, or `pipewire` / `default` if another program holds the card; two tabs on
one `hw:` device cannot both listen.

## Read-only file system (`--reduce-writes`)

To spare an SD card, run with the file system that holds KataTone's data mounted
read-only and add `--reduce-writes` (to `katatone-gui` and to the `katatone` command):

```bash
katatone-web --wifi --reduce-writes --host 0.0.0.0
```

**Which file systems.**  At start KataTone works out which mount holds its data
directory (`~/.local/share/katatone`: the patch library, the controller
profiles and the password) and which holds `/etc` (the Wi-Fi profiles, host name
and avahi settings).  Those are the **only** file systems it ever remounts, and
often they are one and the same.  Everything else, including RAM-backed and
layered ones (`tmpfs`, `ramfs`, `overlay`, `devtmpfs`) and `squashfs`, is yours to
manage.  A place whose mount cannot be determined is skipped with a warning.

**Start-up.**  Starting up writes in places that cannot be listed in advance, so
those file systems are kept writable while KataTone boots and for **120 seconds**
after it starts (`--rw-seconds N` changes that), then put back to read-only.  It
is a timer, so it happens whether or not anyone opens the page.  This applies even if they were writable
before.  If one cannot be remounted at start, KataTone stops with the reason; a
clean exit also leaves them read-only.

**After that,** whenever KataTone has to write to them (saving a liveset or controller
profile, importing, renaming or deleting them, changing the password, saving Wi-Fi,
host name or avahi settings) it remounts the one involved read-write (`mount -o remount,rw`), writes, syncs and remounts it read-only
again.  Writers that overlap share one remount.  Reads never remount.  With
`--log` the helper-process logs go to the temporary directory instead of the data
directory.

- Remounting needs root.  Run the service as root, or let its user run
  `mount` through sudo without a password (`sudo -n` is used otherwise), e.g.
  `katatone ALL=(root) NOPASSWD: /usr/bin/mount -o remount\,rw *, /usr/bin/mount -o remount\,ro *`
  in `/etc/sudoers.d/katatone`.
- If a file is still open for writing, the final remount to read-only fails: it
  is retried once and then logged as a warning, leaving the file system writable.
- Not covered: files written by other programs (NetworkManager's own state under
  `/var/lib`, system logs) and by the Tone Central window, which is a separate
  process (cookies, downloads); use a desktop session for that.

## Logs

KataTone keeps no log files by default: the Analyzer and Tone Central windows
run with their error output discarded.  `--log` (on `katatone-gui`) keeps it as
`analyzer.log` and `webview.log` in the data directory (`~/.local/share/katatone`,
or the temporary directory with `--reduce-writes`).  `-v` is separate: it traces to
the terminal (stderr, so the journal under systemd) and writes no file.  Log files
left by earlier versions are not deleted.
