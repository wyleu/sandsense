# pico_circuitp_8bell_scan_rs_long_midi_latch.py
# CircuitPython IR Bell Detector – Latch Mode with MIDI + Log

import asyncio
import time
import sys
import supervisor
from adafruit_ticks import ticks_diff, ticks_ms
import json
import os

from hardware_config import load_config, create_hardware

print("running on", sys.implementation.name)

if sys.implementation.name == "circuitpython":
    import board
    import digitalio
    import pwmio
    import usb_midi
    import adafruit_midi
    from adafruit_midi.note_on import NoteOn
    from adafruit_midi.note_off import NoteOff

# ------------------- Config -------------------
MODE = "latch_detect_with_middle"
print("IR Bell Detector v2 - Latch Mode")

LATCH_TIMEOUT_MS = 100          # > max middle off; tune from logs
MIN_MIDDLE_MS    = 10
MAX_MIDDLE_MS    = 80
SCAN_INTERVAL_MS = 10

DUTY_33        = 21845
CARRIER_FREQ   = 38000

# Create all hardware from settings.json (or defaults)

# Load full config once
config = load_config()

hw = create_hardware(config)

inputs     = hw["inputs"]
leds       = hw["leds"]
ir_pwm     = hw["ir_pwm"]
tx_led     = hw["tx_led"]
status_led = hw["status_led"]

# Detection parameters (now from settings.json)
det = hw["detection"]
MODE              = det["mode"]
LATCH_TIMEOUT_MS  = det["latch_timeout_ms"]
MIN_MIDDLE_MS     = det["min_middle_ms"]
MAX_MIDDLE_MS     = det["max_middle_ms"]
SCAN_INTERVAL_MS  = det["scan_interval_ms"]

print("IR Bell Detector v2 - Latch Mode")
print(f"Mode: {MODE}")
print(f"Latch timeout: {LATCH_TIMEOUT_MS} ms")
print(f"Middle off range: {MIN_MIDDLE_MS}-{MAX_MIDDLE_MS} ms")
print(f"Scan interval: {SCAN_INTERVAL_MS} ms")

pins = hw["pins"]
print(f"Pins loaded → inputs base GP{pins['input_base']}, "
      f"outputs base GP{pins['output_base']}, "
      f"IR GP{pins['ir_led']}, TX GP{pins['tx_led']}, "
      f"Status GP{pins['status_led']}")

tx_led.value = True
status_led.value = True

# ------------------- Load Config -------------------
CONFIG_FILE = "settings.json"
config = {}

try:
    with open(CONFIG_FILE, "r") as f:
        config = json.load(f)
    print("Loaded:", CONFIG_FILE)
except Exception as e:
    print("Config load error:", e)
    config = {
        "wifi": {"ssid": ""},
        "output": {
            "enabled_formats": ["console"],
            "midi": {"enabled": False},
            "log": {"enabled": False}
        },
        "startup": {"program": None}
    }

wifi_ssid       = config.get("wifi", {}).get("ssid", "")
enabled_formats = config.get("output", {}).get("enabled_formats", ["console"])

# ------------------- Output Setup -------------------
midi = None
midi_channel   = 0
midi_note_base = 60
midi_velocity  = 100          # default / fallback

def timestamp():
    return time.monotonic()

def duration_to_velocity(ms: int) -> int:
    """Shorter duration → higher velocity (harder hit)"""
    MAX_VEL  = 127
    MIN_VEL  = 35
    SHORTEST = 30    # ms → full velocity
    LONGEST  = 450   # ms → minimum velocity

    if ms <= SHORTEST:
        return MAX_VEL
    if ms >= LONGEST:
        return MIN_VEL

    ratio = (ms - SHORTEST) / (LONGEST - SHORTEST)
    vel = MAX_VEL - int(ratio * (MAX_VEL - MIN_VEL))
    return max(MIN_VEL, vel)

# MIDI
if "midi" in enabled_formats:
    midi_conf = config.get("output", {}).get("midi", {})
    if midi_conf.get("enabled", False):
        try:
            midi_channel   = midi_conf.get("channel", 0)
            midi_note_base = midi_conf.get("note_base", 60)
            midi_velocity  = midi_conf.get("velocity", 100)
            midi = adafruit_midi.MIDI(
                midi_out=usb_midi.ports[1],
                out_channel=midi_channel
            )
            print(f"USB MIDI initialized (ch {midi_channel+1}, base note {midi_note_base})")
        except Exception as e:
            print("MIDI init failed (skipping MIDI):", e)
            midi = None

# Logging (RTC-safe)
log_file = None
if "log" in enabled_formats:
    log_conf = config.get("output", {}).get("log", {})
    if log_conf.get("enabled", False):
        log_prefix = log_conf.get("filename_prefix", "bell_log_")
        try:
            # Prefer a simple session name if no reliable RTC
            filename = f"{log_prefix}session.txt"
            log_file = open(filename, "a")
            log_file.write(f"Log started (monotonic {time.monotonic():.1f})\n")
            log_file.flush()
            print(f"Logging to: {filename}")
        except OSError as e:
            print("Log open failed:", e)
            log_file = None

# ------------------- Handlers -------------------
class OutputHandler:
    def __init__(self, name, enabled):
        self.name = name
        self.enabled = enabled

    def on_start(self, bell_num, extra_info=None):
        pass

    def on_end(self, bell_num, count, extra_info=None, velocity=None):
        pass

class ConsoleHandler(OutputHandler):
    def __init__(self):
        super().__init__("console", True)

    def on_start(self, bell_num, extra_info=None):
        print(f"{timestamp():.3f} Bell {bell_num} started (latched)")

    def on_end(self, bell_num, count, extra_info=None, velocity=None):
        msg = f"{timestamp():.3f} Bell {bell_num} DETECTED (count: {count})"
        if extra_info:
            msg += f"  {extra_info}"
        if velocity is not None:
            msg += f"  vel={velocity}"
        print(msg)

class LogHandler(OutputHandler):
    def __init__(self, log_file):
        super().__init__("log", log_file is not None)
        self.log_file = log_file

    def on_end(self, bell_num, count, extra_info=None, velocity=None):
        if not self.enabled or not self.log_file:
            return
        # Safe monotonic-based timestamp (no RTC required)
        msg = f"{time.monotonic():.3f} Bell {bell_num} DETECTED (count {count})"
        if extra_info:
            msg += f" - {extra_info}"
        if velocity is not None:
            msg += f" vel={velocity}"
        self.log_file.write(msg + "\n")
        self.log_file.flush()

class MIDIHandler(OutputHandler):
    def __init__(self, midi, channel, note_base, default_velocity=100):
        super().__init__("midi", midi is not None)
        self.midi = midi
        self.channel = channel
        self.note_base = note_base
        self.default_velocity = default_velocity

    def on_start(self, bell_num, extra_info=None):
        if not self.enabled:
            return
        note = self.note_base + (bell_num - 1)
        # We don't know the final duration yet, so use a moderate velocity
        # (or you can move NoteOn to on_end if you prefer)
        vel = self.default_velocity
        print(f"{timestamp():.3f} MIDI NoteOn  ch{self.channel+1} note {note} vel {vel}")
        self.midi.send(NoteOn(note, vel))

    def on_end(self, bell_num, count, extra_info=None, velocity=None):
        if not self.enabled:
            return
        note = self.note_base + (bell_num - 1)
        use_vel = velocity if velocity is not None else self.default_velocity
        print(f"{timestamp():.3f} MIDI NoteOff ch{self.channel+1} note {note} (vel was {use_vel})")
        self.midi.send(NoteOff(note, 0))

handlers = [ConsoleHandler()]
if log_file is not None:
    handlers.append(LogHandler(log_file))
if midi is not None:
    handlers.append(MIDIHandler(midi, midi_channel, midi_note_base, midi_velocity))
    print("MIDI handler added")

print(f"Active handlers: {len(handlers)}")

# ------------------- State -------------------
last_states      = [False] * 8
last_raw         = [False] * 8
last_pulse_times = [0] * 8
off_starts       = [0] * 8
start_times      = [0] * 8
saw_middles      = [False] * 8
hit_counts       = [0] * 8

# ------------------- Coroutines -------------------
async def input_scanner():
    while True:
        now = supervisor.ticks_ms()

        for i in range(8):
            raw = not inputs[i].value          # active low → True when pressed

            if raw:
                last_pulse_times[i] = now

                if off_starts[i] != 0:
                    off_length = ticks_diff(now, off_starts[i])
                    if last_states[i] and MIN_MIDDLE_MS <= off_length <= MAX_MIDDLE_MS:
                        saw_middles[i] = True

                off_starts[i] = 0

                # Rising edge → start of event
                if not last_states[i]:
                    last_states[i] = True
                    start_times[i] = now
                    leds[i].value = False          # LED ON (inverted)
                    bell_num = i + 1
                    for handler in handlers:
                        handler.on_start(bell_num)

            else:
                # Falling edge of raw signal
                if last_raw[i]:
                    off_starts[i] = now

            # Latch timeout → end of event
            if last_states[i]:
                elapsed = ticks_diff(now, last_pulse_times[i])
                if elapsed > LATCH_TIMEOUT_MS:
                    overall_length = ticks_diff(now, start_times[i])
                    bell_num = i + 1
                    velocity = duration_to_velocity(overall_length)

                    extra = f"overall: {overall_length} ms"

                    if saw_middles[i]:
                        # Multi-hit sequence
                        hit_counts[i] += 1
                        for handler in handlers:
                            handler.on_end(bell_num, hit_counts[i], extra, velocity=velocity)
                    else:
                        # Single hit
                        hit_counts[i] += 1
                        extra = f"single, overall: {overall_length} ms"
                        for handler in handlers:
                            handler.on_end(bell_num, hit_counts[i], extra, velocity=velocity)

                    # Reset
                    last_states[i] = False
                    saw_middles[i] = False
                    off_starts[i] = 0
                    leds[i].value = True           # LED OFF

            last_raw[i] = raw

        await asyncio.sleep_ms(SCAN_INTERVAL_MS)

async def blink_status_led():
    while True:
        status_led.value = True
        await asyncio.sleep_ms(490)
        status_led.value = False
        await asyncio.sleep_ms(10)

async def main():
    for led in leds:
        led.value = True
    print("Starting tasks...")
    scanner_task = asyncio.create_task(input_scanner())
    status_task  = asyncio.create_task(blink_status_led())
    await asyncio.gather(scanner_task, status_task)

# ------------------- Run -------------------
try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("\nStopped by user")
finally:
    ir_pwm.duty_cycle = 0
    tx_led.value = False
    status_led.value = True
    for led in leds:
        led.value = True
    if log_file:
        log_file.close()
        print("Log closed")
    print("Cleanup complete")