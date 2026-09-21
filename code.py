# code.py
#
# Farm launcher for Sand* CircuitPython devices
# -----------------------------------------------
# Filename: code.py
#
# Overview
# --------
# Thin boot entry point only. This file must stay small and is
# IDENTICAL on sandsense, sandswing, and sandbells boards.
#
# On reset / power-up CircuitPython runs code.py automatically:
#
#   1. Load settings.json via config_loader (farm schema + defaults)
#   2. Print identity banner (device name, family, program)
#   3. Execute the file named in startup.program
#
# It does not create GPIO, ADS, lasers, or MIDI. Those belong in the
# started program (sandsense.py, bell scan, etc.) and in
# hardware_config / future hardware_profile modules.
#
# Configuration
# -------------
# settings.json on this device, for example:
#
#   Sandsense:
#     "device": { "name": "sandsense-tower", "family": "sandsense", ... }
#     "startup": { "program": "sandsense.py" }
#
#   Sandswing:
#     "device": { "name": "sandswing-bench", "family": "sandswing", ... }
#     "startup": { "program": "pico_circuitp_bell_scan_....py" }
#     (or later "sandswing.py")
#
# If startup.program is missing or null, stop after the banner (REPL).
#
# Related modules
# ---------------
#   config_loader.py   – sole settings.json reader
#   hardware_config.py – IR bell GPIO factory (bell programs only)
#   sandsense.py       – escapement app (this machine when family=sandsense)
#
# Design rules
# ------------
# • One config format: settings.json
# • code.py does not parse JSON itself – always config_loader
# • code.py does not hard-code the application – settings decide
# • Same code.py bytes on every board in the farm
#
# Flow
# ----
#   power on → boot.py (USB) → code.py → load_config → exec(program)
#
# Failures
# --------
# Missing program or exception: print error, return to REPL.

import time
import board
import digitalio
import traceback
import supervisor
from config_loader import load_config

led = digitalio.DigitalInOut(board.LED)
led.direction = digitalio.Direction.OUTPUT


def flash(n, on=0.25, off=0.25):
    for _ in range(n):
        led.value = True
        time.sleep(on)
        led.value = False
        time.sleep(off)

flash(4)  # you already see this



def usb_host_present(wait_s=1.5):
    t0 = time.monotonic()
    while time.monotonic() - t0 < wait_s:
        if getattr(supervisor.runtime, "usb_connected", False):
            return True
        time.sleep(0.05)
    return False

cfg = load_config()
cfg.banner()

on_host = usb_host_present()
print("USB host:", on_host)
time.sleep(1)
flash(1 if on_host else 3)
time.sleep(1)
program = cfg.startup_program
if on_host:
    usb_prog = None
    try:
        usb_prog = (cfg.raw.get("startup") or {}).get("program_usb")
    except Exception:
        usb_prog = None
    if usb_prog:
        program = usb_prog

if not program:
    print("No startup.program in settings.json – REPL ready")
else:
    print("Launching:", program)
    flash(5)
    time.sleep(0.2)
    try:
        with open(program, "r") as f:
            source = f.read()
            exec(compile(source, program, "exec"), globals())
            
    except OSError as e:
        print("Cannot open program:", program, "→", e)
    except Exception as e:
        print("Failed while running:", program)
        traceback.print_exception(e)