#!/bin/bash
# Install katatone (from ./katatone) and its web extra into build/sysroot/app
# as arm64 / CPython 3.14 wheels, using a host pip (a venv with pip, e.g.
# PIP=path/to/venv/bin/pip). No arm64 toolchain or target Python involved:
# pip is told the target platform and only accepts binary wheels.
set -euo pipefail
TOP=$(cd "$(dirname "$0")/.." && pwd)
PIP=${PIP:-pip3}
W=$TOP/build/wheels
APP=$TOP/build/sysroot/app
PLAT="--platform manylinux2014_aarch64 --platform manylinux_2_17_aarch64 \
      --platform manylinux_2_27_aarch64 --platform manylinux_2_28_aarch64 \
      --python-version 3.14 --implementation cp --abi cp314 --abi abi3 --abi none"
# flet-web 1.0.4 still passes openapi_prefix to FastAPI, removed in 0.145.
# dbus-fast: katatone's bluetooth extra (the amp over BlueZ).
REQS=("flet>=1.0" "flet-web>=1.0" "numpy>=1.26" "fastapi<0.145" "dbus-fast>=2.0")

mkdir -p "$W"
$PIP wheel -q --no-deps -w "$W" "$TOP/katatone"
$PIP download -q --only-binary=:all: $PLAT -d "$W" "${REQS[@]}"
rm -rf "$APP"
$PIP install -q --no-compile --target "$APP" --no-index --find-links "$W" --only-binary=:all: $PLAT \
    "${REQS[@]}" katatone
ls "$APP" | /usr/bin/grep -E "^(fastapi|flet_web|numpy|katatone|dbus_fast)-" 
