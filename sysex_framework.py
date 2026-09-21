# code.py - MIDI SysEx sender for Raspberry Pi Pico 2 (RP2350) using CircuitPython
# Sends a SysEx message at startup with:
# - Manufacturer ID: 0x7D (non-commercial/experimental)
# - Machine ID (unique CPU UID as hex)
# - Current time (from NTP via WiFi)
# - Temperature from DS18B20 one-wire sensor
# Uses adafruit_midi library

import os
import time
import binascii
import board
import microcontroller
import wifi
import socketpool
import adafruit_ntp
import rtc
import usb_midi
import adafruit_midi
from adafruit_midi.system_exclusive import SystemExclusive

# Optional: OneWire and DS18B20 imports (comment out if not using temperature)
from adafruit_onewire.bus import OneWireBus
from adafruit_ds18x20 import DS18X20

# --- Configuration ---
# WiFi credentials must be in settings.toml on the CIRCUITPY drive:
# CIRCUITPY_WIFI_SSID = "your_ssid"
# CIRCUITPY_WIFI_PASSWORD = "your_password"
# TIMEZONE_OFFSET_HOURS = 0   # adjust for your local time (e.g., -8 for PST)

# DS18B20 connected to GP0 (change if using different pin)
ONEWIRE_PIN = board.GP13

# --- Connect to WiFi ---
print("Connecting to WiFi...")
wifi.radio.connect(os.getenv("CIRCUITPY_WIFI_SSID"), os.getenv("CIRCUITPY_WIFI_PASSWORD"))
print("Connected, IP:", wifi.radio.ipv4_address)

# --- Set RTC from NTP ---
pool = socketpool.SocketPool(wifi.radio)
ntp = adafruit_ntp.NTP(pool, tz_offset=int(os.getenv("TIMEZONE_OFFSET_HOURS", 0)))
rtc.RTC().datetime = ntp.datetime
print("RTC set from NTP")

# --- Read temperature (optional) ---
try:
    ow_bus = OneWireBus(ONEWIRE_PIN)
    devices = ow_bus.scan()
    if devices:
        ds18 = DS18X20(ow_bus, devices[0])
        temperature_c = ds18.temperature
        temp_str = f"{temperature_c:.2f}C"
    else:
        temp_str = "NoSensor"
except Exception as e:
    print("Temperature read failed:", e)
    temp_str = "Error"

# --- Get unique Machine ID ---
machine_id_hex = binascii.hexlify(microcontroller.cpu.uid).decode("ascii").upper()

# --- Format current time ---
current_time_str = "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
    time.localtime().tm_year,
    time.localtime().tm_mon,
    time.localtime().tm_mday,
    time.localtime().tm_hour,
    time.localtime().tm_min,
    time.localtime().tm_sec,
)

# --- Build payload as ASCII bytes (all values 0-127, no 7-bit packing needed) ---
payload_str = f"ID:{machine_id_hex} TIME:{current_time_str} TEMP:{temp_str}"
payload_bytes = [ord(c) for c in payload_str]

# --- Create SysEx message: F0 7D [data...] F7 ---
sysex = SystemExclusive(manufacturer_id=[0x7D], data=payload_bytes)

# --- Initialize USB MIDI ---
midi = adafruit_midi.MIDI(midi_out=usb_midi.ports[1], out_channel=0)

# --- Send the message ---
print("Sending SysEx:", payload_str)
midi.send(sysex)
print("SysEx sent successfully")

# Optional: keep running (e.g., for REPL access)
while True:
    time.sleep(1)