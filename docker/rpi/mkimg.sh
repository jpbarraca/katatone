#!/bin/bash
# Build a flashable Raspberry Pi 5 SD card image:
#   p1: FAT32 boot  (config.txt, cmdline.txt, Image, DTBs, initramfs.cpio)
#   p2: btrfs home  (mounted at /home by the init script)
# The root filesystem is the initramfs: the firmware loads initramfs.cpio
# next to the kernel, the kernel unpacks it into rootfs (ramfs, see
# rootfstype= in cmdline.txt) and runs busybox init from there. Nothing on
# the card is needed after that except /home.
# No root needed: mtools for FAT, the kernel's gen_initramfs.sh for the cpio
# (all files squashed to uid/gid 0), mkfs.btrfs --rootdir for /home.
#
# Two variants from the same root filesystem, differing only in cmdline.txt:
#   VARIANT=dev  (default)  kernel log and init status lines on the GPIO UART,
#                           then katatone's output; out/rpi5-dev.img
#   VARIANT=prod            quiet loglevel=1: nothing on the UART but
#                           katatone's output (and kernel emergencies);
#                           out/rpi5-prod.img
# The init scripts write their status to /dev/kmsg, so it lands in dmesg (and
# on the console only in dev). The dev build also snapshots dmesg to
# /home/boot-*.txt on the card; prod writes no logs to the SD card at all
# (hostapd/acpid/udhcpd logs live in /tmp, a tmpfs).
#
# BOARD=pi5 (default), pi3 or qemu: each board has its own kernel, built by
# tools/build-kernel.sh into build/linux-<board> from configs/common.config +
# configs/<board>.config (nothing from another board in it), and its own
# initramfs (that kernel's modules). The init scripts are shared.
# This script never touches the source trees: it works from build/stage
# (tools/stage.sh: Image, DTBs, stripped modules, the installed busybox
# tree, gen_init_cpio), build/hostapd, build/sysroot, build/pi3-boot and
# firmware/ - plain files, which is what the base Docker image carries.
#   pi5:  console ttyAMA0 (RP1 uart0 on GPIO 14/15), EEPROM bootloader
#   pi3:  console ttyS1 (mini UART on GPIO 14/15, alias serial1 in the DT;
#         the PL011/ttyAMA0 is the BT UART),
#         bootcode.bin/start.elf/fixup.dat from build/pi3-boot
#   qemu: the test bench (qemu/bench.py, qemu/imgtest.py); no DTBs/blobs
#
# Usage: [BOARD=pi5|pi3] [VARIANT=dev|prod] ./mkimg.sh [output.img]
set -euo pipefail
VARIANT=${VARIANT:-dev}
BOARD=${BOARD:-pi5}
case $VARIANT in dev|prod) ;; *) echo "VARIANT must be dev or prod" >&2; exit 1;; esac
case $BOARD in
    pi5|qemu) CONSOLE=ttyAMA0 ;;
    pi3)      CONSOLE=ttyS1 ;;   # mini UART: alias serial1 in mainline's DT
    *) echo "BOARD must be pi5, pi3 or qemu" >&2; exit 1 ;;
esac

TOP=$(cd "$(dirname "$0")" && pwd)
ST=$TOP/build/stage              # tools/stage.sh: Image, DTBs, modules, busybox tree, gen_init_cpio
case $BOARD in
    qemu) OUT=${1:-$TOP/out/qemu-$VARIANT.img} ;;
    *)    OUT=${1:-$TOP/out/rpi${BOARD#pi}-$VARIANT.img} ;;
esac
WORK=$TOP/build/img-$BOARD
[ -f "$ST/$BOARD/Image" ] || { echo "no kernel for $BOARD: run tools/build-kernel.sh $BOARD (then tools/stage.sh)" >&2; exit 1; }
[ -d "$ST/busybox" ] || { echo "no busybox: run tools/build-busybox.sh (then tools/stage.sh)" >&2; exit 1; }
PATH=$TOP/build/tools:$PATH   # mkfs.btrfs extracted from the btrfs-progs .deb

BOOT_MB=256
BOOT_OFF_MB=1   # partition 1 start
HOME_MB=256     # data only: katatone's library/password, boot logs
DISKID=0x5a5e1d05

for t in mcopy mkfs.vfat mkfs.btrfs mksquashfs sfdisk aarch64-linux-gnu-strip; do
    command -v $t >/dev/null || { echo "missing tool: $t" >&2; exit 1; }
done

rm -rf "$WORK"
mkdir -p "$WORK/boot" "$WORK/rootfs" "$WORK/home" "$(dirname "$OUT")"

# --- boot partition contents ---
cp "$ST/$BOARD/Image" "$WORK/boot/Image"
# DTBs under the downstream names (built by tools/stage.sh with the kernel's
# dtc from mainline's DTs + dts/*.dtsi), so the firmware picks one per SoC.
cp "$ST/$BOARD"/*.dtb "$WORK/boot/" 2>/dev/null || true
if [ "$BOARD" = pi3 ]; then
    # The Pi 3 boots from the card: GPU bootloader + firmware blobs.
    for f in bootcode.bin start.elf fixup.dat; do
        [ -f "$TOP/build/pi3-boot/$f" ] || { echo "missing build/pi3-boot/$f (raspberrypi/firmware boot/)" >&2; exit 1; }
        cp "$TOP/build/pi3-boot/$f" "$WORK/boot/"
    done
fi

cat > "$WORK/boot/config.txt" <<EOF
[$BOARD]
arm_64bit=1
kernel=Image
# Root filesystem: the firmware loads it right after the kernel and passes
# its location in the device tree (chosen/linux,initrd-*).
initramfs initramfs.cpio followkernel
os_check=0
EOF
# Bootloader/firmware messages on the UART (bootcode.bin's "Read File" lines
# and start.elf's MESS log on the Pi 3): dev only; prod shows KataTone alone.
[ "$VARIANT" = dev ] && printf '# Bootloader/firmware messages on the debug UART\nuart_2ndstage=1\n' >> "$WORK/boot/config.txt"
if [ "$BOARD" = pi3 ]; then
    # Mini UART console: pins the core clock so its baud rate holds.
    # No gpu_mem=16: at the minimum, bootcode.bin loads the cut-down
    # start_cd.elf/fixup_cd.dat instead of start.elf, which are not on the
    # card, and stops right after "Read File: config.txt".
    printf 'enable_uart=1\n' >> "$WORK/boot/config.txt"
fi

if [ "${EARLYCON_RP1:-0}" = 1 ]; then
    # Kernel log on GPIO14/15 from the first instruction: the firmware sets up
    # RP1 uart0 and keeps the PCIe link up, earlycon writes to it through the
    # firmware's mapping (address printed by the firmware as "RP1_UART").
    # Output stops once the kernel's PCIe driver resets the link.
    printf 'enable_rp1_uart=1\npciex4_reset=0\n' >> "$WORK/boot/config.txt"
    EARLYCON="earlycon=pl011,mmio32,0x1c00030000,115200n8"
else
    # No earlycon: the kernel does not replay on ttyAMA0 what a bootconsole
    # already printed, so an earlycon on the debug UART would swallow the
    # first ~1.4 s of the log. Without it, ttyAMA0 replays from "Booting Linux".
    EARLYCON=""
fi
# Only one console= for the PL011 driver: it has a single console slot shared
# by all its ports, so a console=ttyAMA10 would take it and ttyAMA0 would
# never get the kernel log. The debug UART still gets a shell from inittab.
# rootfstype=ramfs: keep rootfs as plain ramfs instead of the tmpfs the kernel
# would otherwise pick for an initramfs. No root=: nothing is mounted over it.
# prod: quiet drops the kernel's boot chatter, loglevel=1 leaves only
# emergencies (panics) on the console; the console itself stays ttyAMA0 for
# the app. Everything is still in dmesg and in the /home/boot-*.txt snapshots.
QUIET=""
[ "$VARIANT" = prod ] && QUIET=" quiet loglevel=1"
# Pi 3 dev: earlycon on the DT's stdout-path (the mini UART), so the kernel's
# first lines show before the 8250 driver registers ttyS1.
[ "$BOARD" = pi3 ] && [ "$VARIANT" = dev ] && QUIET=" earlycon$QUIET"
echo "${EARLYCON:+$EARLYCON }console=$CONSOLE,115200 rootfstype=ramfs rdinit=/sbin/init$QUIET" \
    > "$WORK/boot/cmdline.txt"

# --- root filesystem (initramfs) contents ---
R=$WORK/rootfs
cp -a "$ST/busybox/." "$R/"       # busybox + its applet links
mkdir -p "$R"/{dev,proc,sys,tmp,root,home,etc/init.d,lib/firmware,usr/sbin}
ln -s sbin/init "$R/init"            # the kernel's default initramfs entry point

# Device firmware: CYW43455 Wi-Fi (brcmfmac) and Bluetooth (hci_bcm), plus the
# wireless regulatory database. The kernel loads these straight from rootfs.
cp -r "$TOP/firmware/." "$R/lib/firmware/"

# Wi-Fi access point: static hostapd (tools/build-hostapd.sh) + busybox udhcpd.
# /home/hostapd.conf on the card, if present, overrides /etc/hostapd.conf.
[ -x "$TOP/build/hostapd/hostapd" ] || { echo "run tools/build-hostapd.sh first" >&2; exit 1; }
cp "$TOP/build/hostapd/hostapd" "$TOP/build/hostapd/hostapd_cli" "$R/usr/sbin/"
cat > "$R/etc/hostapd.conf" <<'EOF'
# WPA2-PSK access point on the on-board CYW43455 (brcmfmac, nl80211).
# Copy this to /home/hostapd.conf on the SD card to change it without rebuilding.
interface=wlan0
driver=nl80211
ssid=KataTone
country_code=PT
hw_mode=g
channel=6
ieee80211n=1
wmm_enabled=1
auth_algs=1
wpa=2
wpa_key_mgmt=WPA-PSK
wpa_pairwise=CCMP
rsn_pairwise=CCMP
wpa_passphrase=KataTone
ctrl_interface=/tmp/hostapd
EOF
# Power button: busybox acpid maps KEY_POWER to /etc/acpi/PWRF/00000080.
mkdir -p "$R/etc/acpi/PWRF"
cat > "$R/etc/acpi/PWRF/00000080" <<'EOF'
#!/bin/sh
# Power button pressed: clean shutdown (init runs the ::shutdown entry).
exec poweroff
EOF
chmod 755 "$R/etc/acpi/PWRF/00000080"

cat > "$R/etc/udhcpd.conf" <<'EOF'
interface wlan0
start 192.168.4.10
end 192.168.4.50
lease_file /tmp/udhcpd.leases
opt subnet 255.255.255.0
opt router 192.168.4.1
opt lease 86400
EOF

# The Python runtime and katatone (build/sysroot, from tools/fetch-sysroot.py
# and tools/fetch-app.sh) are one zstd squashfs on the boot partition,
# loop-mounted at /opt/katatone by rc.late once the card is up. Not in the
# initramfs: the firmware reads that at ~17 MB/s on a Pi 3 (86 MB = 5 s
# before the kernel even starts), while the kernel reads from the card only
# the blocks the app touches.
[ -d "$TOP/build/sysroot/app/katatone" ] || { echo "run tools/fetch-sysroot.py and tools/fetch-app.sh first" >&2; exit 1; }
SYS=$WORK/sys
cp -a "$TOP/build/sysroot" "$SYS"
# Trim what the app never touches.
rm -rf "$SYS/var" "$SYS/usr/share"/{doc,man,lintian,locale,info,bug}
rm -f "$SYS"/usr/share/dbus-1/system.d/org.freedesktop.*.conf    # systemd's; no systemd here
rm -rf "$SYS/usr/lib/python3.14"/{idlelib,test,pydoc_data,tkinter,turtledemo,ensurepip,lib2to3}
find "$SYS/app" -type d \( -name tests -o -name testing \) -prune -exec rm -rf {} +
rm -rf "$SYS/app/flet_web/web/pyodide"          # Python-in-the-browser, unused
find "$SYS/app/flet_web/web" -name '*.symbols' -delete
find "$SYS/app" -type f -name '*.so*' -exec aarch64-linux-gnu-strip --strip-unneeded {} + 2>/dev/null
rm -rf "$SYS/app/bin"             # pip's launchers (host shebangs); the wrapper runs python3 -c
# Byte-compile once here (same 3.14 bytecode as the target), so first start
# does not compile a hundred modules on the Pi. -d records the path the files
# have on the Pi, not this machine's, in the .pyc files (tracebacks, too).
python3 -m compileall -q -f -j0 -d /opt/katatone/usr/lib/python3.14 "$SYS/usr/lib/python3.14" >/dev/null 2>&1 || true
python3 -m compileall -q -f -j0 -d /opt/katatone/app "$SYS/app" >/dev/null 2>&1 || true
mkdir -p "$R/opt/katatone" "$R/boot"
mksquashfs "$SYS" "$WORK/boot/katatone.sqsh" -comp zstd -Xcompression-level 19 \
    -all-root -no-xattrs -noappend -quiet -no-progress
echo "katatone squashfs: $(du -h "$WORK/boot/katatone.sqsh" | cut -f1) from $(du -sh "$SYS" | cut -f1)"

# Ubuntu's binaries expect a few absolute paths; point them into the mount.
mkdir -p "$R/usr/lib" "$R/usr/share"
ln -s /opt/katatone/usr/lib/aarch64-linux-gnu/ld-linux-aarch64.so.1 "$R/lib/ld-linux-aarch64.so.1"
ln -s /opt/katatone/usr/lib/aarch64-linux-gnu "$R/lib/aarch64-linux-gnu"
ln -s /opt/katatone/usr/lib/aarch64-linux-gnu "$R/usr/lib/aarch64-linux-gnu"
ln -s /opt/katatone/usr/lib/python3.14        "$R/usr/lib/python3.14"
ln -s /opt/katatone/usr/share/alsa            "$R/usr/share/alsa"
for b in python3.14 arecord aplay amidi amixer bluetoothctl dbus-daemon dbus-send btrfs; do
    ln -s /opt/katatone/usr/bin/$b "$R/usr/bin/$b"
done
ln -s python3.14 "$R/usr/bin/python3"
# BlueZ + D-Bus for the amp over Bluetooth (katatone's dbus-fast talks to
# bluetoothd on the system bus). Pairing keys persist on the card.
ln -s /opt/katatone/usr/libexec        "$R/usr/libexec"
ln -s /opt/katatone/usr/share/dbus-1   "$R/usr/share/dbus-1"
mkdir -p "$R/etc/dbus-1" "$R/etc/bluetooth" "$R/var/lib" "$R/run"
ln -s /opt/katatone/etc/dbus-1/system.d "$R/etc/dbus-1/system.d"
ln -s /home/bluetooth "$R/var/lib/bluetooth"
cat > "$R/etc/bluetooth/main.conf" <<'EOF'
# Power the controller as soon as bluetoothd sees it (the app expects an
# "on" adapter with the BOSS adapter paired via bluetoothctl).
[General]
Name = KataTone
[Policy]
AutoEnable=true
EOF

# katatone-web: the app, started by init instead of a shell (see inittab).
cat > "$R/usr/bin/katatone-web" <<'EOF'
#!/bin/sh
# Browser interface of KataTone on port 8550, reachable over the Wi-Fi AP.
# Data (patch library, password) persists in /home/katatone on the SD card.
export HOME=/home/katatone
export PYTHONPATH=/opt/katatone/app
export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
mkdir -p /home/katatone
exec python3 -c 'from katatone.gui.app import main_web; main_web()' \
    --host 0.0.0.0 --port 8550 "$@"
EOF
chmod 755 "$R/usr/bin/katatone-web"

# grow-part DEV N: extend MBR partition N of DEV to the end of the device,
# in place (no tools beyond busybox). Used once, at first boot, on the card:
# the image is 404 MB, the card is whatever it is. The btrfs inside is
# grown afterwards with `btrfs filesystem resize max` once mounted.
cat > "$R/usr/sbin/grow-part" <<'EOF'
#!/bin/sh
# Exit 0 and print "grown" when the entry was changed, 1 when nothing to do.
dev=$1; n=$2
base=$(basename "$dev")
part=$(ls -d /sys/block/"$base"/*"$n" 2>/dev/null | head -1)
[ -n "$part" ] || { echo "grow-part: no partition $n on $dev"; exit 2; }
disk=$(blockdev --getsz "$dev")
start=$(cat "$part/start"); size=$(cat "$part/size")
want=$((disk - start))
# Only worth it above 1 MiB of gain (and never shrink).
[ "$want" -gt $((size + 2048)) ] || exit 1
off=$((446 + 16 * (n - 1) + 12))      # LBA size field of entry N in the MBR
printf "$(printf '\\%03o\\%03o\\%03o\\%03o' $((want & 255)) $(((want >> 8) & 255)) \
        $(((want >> 16) & 255)) $(((want >> 24) & 255)))" \
    | dd of="$dev" bs=1 seek="$off" count=4 conv=notrunc 2>/dev/null
sync
blockdev --rereadpt "$dev"
echo "grown: partition $n of $dev $size -> $want sectors"
EOF
chmod 755 "$R/usr/sbin/grow-part"

cat > "$R/etc/init.d/katatone" <<'EOF'
#!/bin/sh
# Started (and restarted) by init: waits for the app tree (squashfs) and,
# up to 15 s, for /home (SD card: library and password), then runs the app
# with its output on the console. This script's own notes go to the kernel
# log, so the console carries nothing but the app (prod) or the app after the
# kernel log (dev).
exec >/dev/kmsg 2>&1
log() { echo "$*" > /dev/kmsg; }
i=0
while [ ! -x /opt/katatone/usr/bin/python3.14 ]; do
    i=$((i + 1))
    if [ $i -gt 300 ]; then log "katatone: /opt/katatone not mounted, giving up"; exec sleep 2147483647; fi
    sleep 0.1
done
i=0
while [ ! -e /home/README ] && [ $i -lt 150 ]; do i=$((i + 1)); sleep 0.1; done
[ -e /home/README ] || log "katatone: /home not mounted, data will not persist"
log "katatone: starting web GUI at $(cut -d' ' -f1 /proc/uptime) s"
exec katatone-web >/dev/console 2>&1
EOF
chmod 755 "$R/etc/init.d/katatone"

cat > "$R/etc/init.d/console" <<'EOF'
#!/bin/sh
# The console belongs to the app's output. A shell is only given when the
# kernel command line says "shell" or the card has /home/shell. The tty is
# whatever console= named (ttyAMA0 on the Pi 5, ttyS0 on the Pi 3).
tty=/dev/$(awk '{print $NF}' /sys/class/tty/console/active)
shell() { exec cttyhack /bin/sh <"$tty" >"$tty" 2>&1; }   # init made us a session leader
if grep -qw shell /proc/cmdline; then shell; fi
i=0
while [ ! -e /home/README ] && [ $i -lt 150 ]; do i=$((i + 1)); sleep 0.1; done
if [ -e /home/shell ]; then shell; fi
exec sleep 2147483647
EOF
chmod 755 "$R/etc/init.d/console"

# Kernel modules for everything the shell does not need (MMC, USB, Wi-Fi,
# Bluetooth, btrfs, vfat), stripped, with depmod's tables.
cp -a "$ST/$BOARD/modules/lib/modules" "$R/lib/"

# sysinit runs to completion before anything else; once entries start in
# parallel with the respawned shells. So rcS only does what a shell needs
# and rc.late brings up the rest while the prompt is already there.
cat > "$R/etc/inittab" <<'EOF'
::sysinit:/etc/init.d/rcS
::once:/etc/init.d/rc.late
::respawn:/etc/init.d/katatone
::respawn:/etc/init.d/console
::ctrlaltdel:/sbin/reboot
::shutdown:/bin/umount -a -r
EOF

cat > "$R/etc/init.d/rcS" <<'EOF'
#!/bin/sh
# Running from the initramfs: the kernel mounts nothing for us, not even
# devtmpfs (that only happens on the root= path). Keep this to the bare
# minimum: init waits for it before starting the shell.
mount -t devtmpfs devtmpfs /dev
# Status lines go to the kernel log (console in dev, hidden in prod). One
# open of /dev/kmsg per line: the kernel rate-limits writes per open file
# (10 per 5 s), so a shared redirect silently drops lines.
exec >/dev/kmsg 2>&1
log() { echo "$*" > /dev/kmsg; }
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t tmpfs tmpfs /tmp
hostname rpi5
ip link set lo up
log "rpi5 minimal: $(uname -sr) up at $(cut -d' ' -f1 /proc/uptime) s, rootfs in ramfs"
EOF
chmod 755 "$R/etc/init.d/rcS"

cat > "$R/etc/init.d/rc.late" <<'EOF'
#!/bin/sh
# Everything the shell does not need, loaded after init has started it.
# Three independent chains run in parallel; each reports when it is done.
# Module dependencies are resolved by busybox modprobe from /lib/modules.
# Everything printed here (status, modprobe errors, hostapd) goes to the
# kernel log: on the console in the dev build, only in dmesg in prod.
exec >/dev/kmsg 2>&1
up() { cut -d' ' -f1 /proc/uptime; }
log() { echo "$*" > /dev/kmsg; }   # own open per line: see rcS

# busybox modprobe-small builds its dependency cache (modules.dep.bb) on
# first use; three chains doing that at once insert modules before their
# dependencies ("Unknown symbol" noise, retried). Build it once, up front.
depmod

# Keep the kernel log on /home, so a boot can be diagnosed even when no
# console works: read /home/boot-*.txt from the card afterwards.
snap() {
    { cat /proc/uptime; echo; dmesg; echo; ls -l /dev/ttyAMA* 2>&1
      echo; ls /sys/bus/pci/devices /sys/bus/amba/devices 2>&1; } > "$1"
    sync
}

# 1. MMC hosts, then three branches. Pi 5: sdhci-brcmstb (SD on mmc0, Wi-Fi
#    SDIO on mmc1). Pi 3: bcm2835 sdhost (SD) and sdhci-iproc (Wi-Fi). The
#    ones without hardware just sit there.
modprobe loop
modprobe squashfs
modprobe sdhci-brcmstb
modprobe bcm2835
modprobe pwrseq-simple     # Pi 3: the Wi-Fi chip's power sequence; without it
modprobe sdhci-iproc       # the SDIO host's probe is deferred forever

# The card is the only MMC block device (Wi-Fi SDIO has none), but its
# number depends on probe order, so match by pattern.
card() { for d in /dev/mmcblk[0-9]p$1; do [ -b "$d" ] && { echo "$d"; return; }; done; }

# 1c. The app tree: boot partition (vfat, read-only) -> katatone.sqsh ->
#     loop-mounted squashfs at /opt/katatone. The kernel autoloads vfat.
(
    i=0
    while [ -z "$(card 1)" ] && [ $i -lt 200 ]; do i=$((i + 1)); sleep 0.05; done
    p1=$(card 1)
    if [ -z "$p1" ]; then
        log "rc.late: no SD card partition 1 after 10 s, no app tree"
    elif mount -t vfat -o ro "$p1" /boot &&
         mount -t squashfs -o ro,loop /boot/katatone.sqsh /opt/katatone; then
        log "rc.late: /opt/katatone (squashfs on $p1) mounted at $(up) s"
    else
        log "rc.late: app tree mount failed"
    fi
) &

# 1a. Wi-Fi: brcmfmac loads its firmware (~1 s), wlan0 appears, then the AP:
#     hostapd (WPA2-PSK, config overridable from /home) + DHCP for clients.
(
    modprobe brcmfmac
    log "rc.late: wifi driver loaded at $(up) s"
    i=0
    while [ ! -d /sys/class/net/wlan0 ] && [ $i -lt 300 ]; do
        i=$((i + 1))
        sleep 0.05
    done
    if [ -d /sys/class/net/wlan0 ]; then
        ip addr add 192.168.4.1/24 dev wlan0
        ip link set wlan0 up
        cfg=/etc/hostapd.conf
        [ -f /home/hostapd.conf ] && cfg=/home/hostapd.conf
        if hostapd -B -f /tmp/hostapd.log "$cfg"; then
            touch /tmp/udhcpd.leases
            udhcpd /etc/udhcpd.conf
            log "rc.late: wifi AP '$(sed -n 's/^ssid=//p' "$cfg")' 192.168.4.1 up at $(up) s (log: /tmp/hostapd.log)"
        else
            log "rc.late: hostapd failed to start, see /tmp/hostapd.log"
        fi
    else
        log "rc.late: wlan0 never appeared, no AP"
    fi
) &

# 1b. SD card -> /home (btrfs); the dmesg snapshots go there last, their
#     sync to the card takes a while and nothing should wait behind it.
(
    modprobe mmc_block
    modprobe btrfs
    i=0
    while [ -z "$(card 2)" ] && [ $i -lt 200 ]; do
        i=$((i + 1))
        sleep 0.05
    done
    grown=0
    p2=$(card 2)
    if [ -n "$p2" ]; then
        # First boot on a card bigger than the image: take the rest for /home.
        if grow-part "${p2%p2}" 2; then
            grown=1
            i=0
            while [ ! -b "$p2" ] && [ $i -lt 100 ]; do i=$((i + 1)); sleep 0.05; done
        fi
    fi
    if [ ! -b "$p2" ]; then
        log "rc.late: no SD card partition 2 after 10 s, no /home"
    elif mount -t btrfs -o noatime "$p2" /home; then
        log "rc.late: /home (btrfs) mounted at $(up) s"
        if [ $grown = 1 ]; then
            # the btrfs tool lives in the squashfs; wait for it
            i=0
            while [ ! -x /opt/katatone/usr/bin/btrfs ] && [ $i -lt 100 ]; do i=$((i + 1)); sleep 0.05; done
            btrfs filesystem resize max /home && log "rc.late: /home grown to $(df -h /home | awk 'NR==2{print $2}') at $(up) s"
        fi
        # Boot log snapshots on the card: dev build only. prod (quiet on the
        # cmdline) writes no logs to the SD card; only the app's own data.
        if ! grep -qw quiet /proc/cmdline; then
            snap /home/boot-init.txt
            sleep 5
            snap /home/boot-5s.txt
        fi
    else
        log "rc.late: mount of $p2 failed"
    fi
) &

# 2. USB: Pi 5 RP1 dwc3 hosts / Pi 3 dwc2 (+ its USB ethernet), storage,
#    keyboards/mice, the amp (USB audio + MIDI).
(
    modprobe xhci-plat-hcd
    modprobe dwc3
    modprobe phy-generic
    modprobe dwc2
    modprobe smsc95xx
    modprobe lan78xx
    modprobe usb-storage
    modprobe uas
    modprobe sd_mod
    modprobe usbhid
    modprobe evdev
    modprobe snd-usb-audio      # the amp: USB audio + MIDI (/dev/snd/midiC*D*)
    log "rc.late: usb up at $(up) s"
) &

# 4. Power button (gpio-keys on SoC GPIO 20): acpid opens every
#    /dev/input/event* at start-up, so the key must exist before it runs.
(
    modprobe evdev
    modprobe gpio_keys
    i=0
    while [ ! -c /dev/input/event0 ] && [ $i -lt 100 ]; do
        i=$((i + 1))
        sleep 0.05
    done
    if [ -c /dev/input/event0 ]; then
        acpid -l /tmp/acpid.log
        log "rc.late: power button armed at $(up) s"
    else
        log "rc.late: no input device, power button not armed"
    fi
) &

# 3. Bluetooth: HCI over serdev on the BT UART (Pi 5: uarta via 8250_bcm7271;
#    Pi 3: the PL011, built in), then the system D-Bus and bluetoothd
#    (pairing state in /home/bluetooth, so wait for /home; without the card
#    it still runs, pairing then not kept).
(
    modprobe 8250_bcm7271 2>/dev/null
    modprobe hci_uart
    log "rc.late: bluetooth driver loaded at $(up) s"
    # dbus-daemon and bluetoothd live in the app squashfs (chain 1c mounts
    # it at /opt/katatone, after the card appears): wait for it, up to 30 s.
    i=0
    while [ ! -x /opt/katatone/usr/bin/dbus-daemon ] && [ $i -lt 300 ]; do i=$((i + 1)); sleep 0.1; done
    [ -x /opt/katatone/usr/bin/dbus-daemon ] || log "rc.late: no /opt/katatone after 30 s, no D-Bus/bluetoothd"
    mkdir -p /run/dbus && chown messagebus:messagebus /run/dbus
    dbus-daemon --system --fork && log "rc.late: dbus-daemon up at $(up) s"
    i=0
    while [ ! -e /home/README ] && [ $i -lt 150 ]; do i=$((i + 1)); sleep 0.1; done
    mkdir -p /home/bluetooth
    /usr/libexec/bluetooth/bluetoothd -n &   # foreground, as a job: it does not detach here
    log "rc.late: bluetoothd up at $(up) s"
) &
EOF
chmod 755 "$R/etc/init.d/rc.late"

printf 'root:x:0:0:root:/root:/bin/sh\nmessagebus:x:100:100:dbus:/run/dbus:/bin/false\n' > "$R/etc/passwd"
printf 'root:x:0:\nmessagebus:x:100:\n' > "$R/etc/group"

# Device nodes cannot be created without root; list them for gen_init_cpio.
# /dev/console must exist before init runs (the kernel opens it for init's
# stdio); devtmpfs is mounted over /dev by rcS and provides the rest.
cat > "$WORK/initramfs.list" <<'EOF'
nod /dev/console 0600 0 0 c 5 1
nod /dev/null    0666 0 0 c 1 3
nod /dev/kmsg    0600 0 0 c 1 11
EOF
# Uncompressed: the firmware loads it as-is and the kernel unpacks without
# a decompressor pass. gen_initramfs.sh squashes ownership to root.
(cd "$ST/tools" && ./gen_initramfs.sh -o "$WORK/boot/initramfs.cpio" \
    -u squash -g squash "$R" "$WORK/initramfs.list")   # finds usr/gen_init_cpio in cwd

# --- /home contents (btrfs partition) ---
cat > "$WORK/home/README" <<'EOF'
This is the SD card's second partition (btrfs), mounted at /home by rc.late.
The root filesystem, the Python runtime and katatone all live in RAM
(initramfs on the boot partition; the app tree is a squashfs at /opt/katatone).
  katatone/  the app's HOME: library, password, controller profiles
  boot-*.txt kernel log snapshots of the last boot (dev build only; prod
             writes no logs to the card)
  shell      create this file to get a shell on the GPIO UART instead of the app
EOF
mkdir -p "$WORK/home/katatone"

# --- filesystem images ---
rm -f "$WORK/boot.vfat" "$WORK/home.btrfs"
# The boot FAT is read by two different loaders. Pi 5: the EEPROM bootloader,
# fine with mkfs.vfat's default FAT32 (that layout booted). Pi 3: bootcode.bin,
# which with that default (512-byte clusters) read config.txt but never loaded
# start.elf, and with FAT32/4 KiB clusters at 256 MB (~65.4k clusters, the
# FAT16/FAT32 boundary) read nothing at all. Early Raspbian cards were FAT16:
# 8 KiB clusters here (32k clusters, unambiguous), partition type 0x0e.
if [ "$BOARD" = pi3 ]; then
    # Exactly what pi-gen makes for Raspberry Pi OS: 512 MB FAT32 starting at
    # 4 MiB, mkfs.fat's defaults for that size, hidden sectors = partition
    # start (mkfs sets that itself on a real partition). FAT16/8 KiB and
    # FAT32/512 B both stopped bootcode.bin after config.txt.
    BOOT_MB=512; BOOT_OFF_MB=4
    mkfs.vfat -F 32 -h $((BOOT_OFF_MB * 2048)) -n bootfs -C "$WORK/boot.vfat" $((BOOT_MB * 1024)) >/dev/null
else
    mkfs.vfat -F 32 -n BOOT -C "$WORK/boot.vfat" $((BOOT_MB * 1024)) >/dev/null
fi
PTYPE=c
mcopy -i "$WORK/boot.vfat" "$WORK"/boot/* ::/

truncate -s ${HOME_MB}M "$WORK/home.btrfs"
mkfs.btrfs -q -L home --compress zstd -r "$WORK/home" "$WORK/home.btrfs"
# mkfs.btrfs --rootdir grows the file when its (uncompressed) estimate needs
# more than HOME_MB; size the partition from the result, never truncate it.
HOME_MB=$(( ($(stat -c %s "$WORK/home.btrfs") + 1048575) / 1048576 ))

# --- assemble disk image (MBR, 1 MiB aligned) ---
rm -f "$OUT"
truncate -s $((BOOT_OFF_MB + BOOT_MB + HOME_MB))M "$OUT"
sfdisk -q "$OUT" <<EOF
label: dos
label-id: $DISKID
${BOOT_OFF_MB}M,${BOOT_MB}M,$PTYPE,*
,${HOME_MB}M,83
EOF
dd if="$WORK/boot.vfat"  of="$OUT" bs=1M seek=$BOOT_OFF_MB conv=notrunc status=none
dd if="$WORK/home.btrfs" of="$OUT" bs=1M seek=$((BOOT_OFF_MB + BOOT_MB)) conv=notrunc status=none

echo "wrote $OUT ($BOARD $VARIANT: $(cat "$WORK/boot/cmdline.txt"))"
