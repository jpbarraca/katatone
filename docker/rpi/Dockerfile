# syntax=docker/dockerfile:1
# Production SD-card images for the Raspberry Pi 3 and 5 running KataTone's
# web GUI as the main program on a fast-booting mainline Linux.
# Self-contained: nothing is compiled here and nothing is fetched beyond
# KataTone's Python dependencies (wheels from PyPI). This directory holds
#   bin.tar.xz    the prebuilt binaries (bin.txt lists them): the pi5 and pi3
#                 kernels with DTBs and modules, busybox, hostapd, the Pi 3
#                 bootloader blobs, Wi-Fi/BT firmware, and the Ubuntu arm64
#                 Python 3.14 runtime with alsa-utils, BlueZ, D-Bus, btrfs-progs
#   mkimg.sh      assembles one image (initramfs, boot partition, /home)
#   fetch-app.sh  KataTone + its web/bluetooth extras as arm64 wheels
# (all three are produced by the build system's pack script, not by hand).
#
#   docker build -f docker/rpi/Dockerfile --output type=local,dest=out .
#   -> out/rpi3-prod.img out/rpi5-prod.img
#
# KataTone is this checkout (KATATONE_SOURCE=checkout, the default) or the
# latest KATATONE_REF (branch, tag or sha) of the GitHub repository
# KATATONE_GITHUB, fetched at every build:
#   --build-arg KATATONE_SOURCE=github
# BOARDS="pi5" builds one board. VARIANT=prod: only KataTone's output on the
# GPIO UART, no logs written to the card (dev: the kernel log on the UART and
# boot logs kept in /home). The build runs on x86-64 whatever the host
# (BUILD_PLATFORM; gen_init_cpio in bin.tar.xz is a host binary), so on an
# arm64 machine it is emulated. UBUNTU must match the runtime in bin.txt: the
# build host's python3 byte-compiles the Pi's Python library.
#
# Flash: dd if=out/rpi5-prod.img of=/dev/sdX bs=4M conv=fsync. /home grows
# to the card at the first boot. Wi-Fi AP and login password: KataTone.
ARG UBUNTU=26.04
ARG BUILD_PLATFORM=linux/amd64
ARG BOARDS="pi5 pi3"
ARG VARIANT=prod
ARG KATATONE_SOURCE=checkout
ARG KATATONE_GITHUB=jpbarraca/katatone
ARG KATATONE_REF=master

# --- KataTone's source: this checkout ... ---
FROM scratch AS katatone-src-checkout
COPY . /

# --- ... or the latest commit on GitHub (ADD rechecks the archive every build) ---
FROM ubuntu:${UBUNTU} AS katatone-src-github-fetch
ARG KATATONE_GITHUB
ARG KATATONE_REF
ADD https://github.com/${KATATONE_GITHUB}/archive/${KATATONE_REF}.tar.gz /katatone.tar.gz
RUN mkdir /k && tar -xzf /katatone.tar.gz -C /k --strip-components=1
FROM scratch AS katatone-src-github
COPY --from=katatone-src-github-fetch /k/ /

FROM katatone-src-${KATATONE_SOURCE} AS katatone-src

# --- wheels + images ---
FROM --platform=${BUILD_PLATFORM} ubuntu:${UBUNTU} AS build
RUN apt-get update && apt-get install -y --no-install-recommends \
        mtools dosfstools btrfs-progs squashfs-tools fdisk xz-utils \
        binutils-aarch64-linux-gnu python3 python3-venv ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/pip
ENV PIP=/opt/pip/bin/pip
WORKDIR /src
COPY docker/rpi/mkimg.sh ./
COPY docker/rpi/fetch-app.sh tools/
ADD docker/rpi/bin.tar.xz ./
COPY --from=katatone-src / katatone/
RUN tools/fetch-app.sh
ARG BOARDS
ARG VARIANT
RUN for b in $BOARDS; do BOARD=$b VARIANT=$VARIANT ./mkimg.sh; done && ls -la out/

# --- the images, nothing else: --output type=local,dest=out ---
FROM scratch AS images
ARG VARIANT
COPY --from=build /src/out/*-${VARIANT}.img /
