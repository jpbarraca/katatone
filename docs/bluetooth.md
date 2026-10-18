# Bluetooth

## The amp over Bluetooth

KataTone can talk to the Katana through BOSS's Bluetooth adapter instead of the USB
cable.  The adapter is a plain BLE-MIDI device: the amp's data travels in standard
BLE-MIDI packets and needs no initialisation packet.

1. **Install** the extra: `pip install 'katatone[bluetooth]'` (it uses `dbus-fast`
   to talk to BlueZ).
2. **Pair** the adapter once with your system's Bluetooth settings (KDE, GNOME or
   `bluetoothctl`).  KataTone connects to a paired adapter; it does not pair or scan.
3. **Choose it** in the **Amp** dropdown: it appears as `KATANA 3 MIDI (Bluetooth)`
   (USB stays the default when both are present), then **Connect**.

Reading works as over USB: a full patch read is byte-identical to the USB one, in
about 16 s (USB takes a few seconds), because the adapter sends small packets and
long replies arrive in pieces, so KataTone waits longer for them.

**Writes are allowed** like over USB.  `katatone-gui --no-ble-writes` turns them
off: only reads, patch select, editor mode and preview mute are then sent, and
writing patches or system settings is refused with an explanation.  Writing is
less tested over Bluetooth than reading, so save a patch to the library before
overwriting an amp slot.  The connection
is not monitored: if the adapter drops, the next operation fails and you can
reconnect.

Some BlueZ versions list the adapter's characteristic UUIDs mangled; KataTone finds
the MIDI characteristic by its properties, so this does not matter.

## Bluetooth MIDI controllers

Pedals and keyboards that the system has connected appear in the **MIDI** tab; see
[MIDI controllers](midi-controllers.md#bluetooth-and-other-sequencer-devices).
