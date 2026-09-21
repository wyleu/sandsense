#!/bin/bash
# One-way host → Pico. Serial only. Never copies from the device.
set -euo pipefail
SRC="$HOME/Code/Sandbells/sandsense/src"
need=(boot.py code.py sandsense.py config_loader.py
      sand_status.py sand_net.py settings.json)

die() { echo "FAIL: $*"; exit 1; }

[ -d "$SRC" ] || die "no $SRC — mkdir src and copy runtime files into it"
for f in "${need[@]}"; do
  [ -f "$SRC/$f" ] || die "missing $SRC/$f"
done
[ -d "$SRC/lib" ] || die "missing $SRC/lib"
command -v mpremote >/dev/null || die "mpremote not installed"
[ -e /dev/ttyACM0 ] || [ -e /dev/ttyACM1 ] || die "no /dev/ttyACM* — Pico in CircuitPython, not BOOTSEL"

if ! mpremote connect auto exec "print('ping')" >/tmp/mp-ping.txt 2>&1; then
  cat /tmp/mp-ping.txt
  die "serial busy. Close Thonny, screen, other mpremote."
fi

echo "Copying $SRC → Pico"
for f in "${need[@]}"; do
  echo "  $f"
  mpremote connect auto cp "$SRC/$f" ":$f"
done
echo "  lib/"
mpremote connect auto cp -r "$SRC/lib" :
echo "Reset…"
mpremote connect auto reset || true
echo "OK — mpremote connect auto"