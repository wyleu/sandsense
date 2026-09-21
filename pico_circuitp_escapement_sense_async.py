# pico_circuitp_escapement_sense_async.py
#
# CircuitPython Tower-Clock Escapement Sensor (async)
# ----------------------------------------------------
# Raspberry Pi Pico 2W
#
# Purpose
#   Optical sensing of a tower-clock escapement using an ADS1115
#   (channel A0) illuminated by a front laser that flashes at
#   exactly 48 times per minute.  A simple software PLL generates
#   “virtual” ticks when the sensor occasionally misses a beat so
#   the output stream stays continuous and phase-locked.
#
#   Detected (or synthesised) ticks:
#     • produce USB-MIDI Note On/Off (velocity sweeps 50↔90)
#     • are counted and summarised once per minute
#     • can be sent to console and/or a log file
#       (both controlled purely from settings.toml)
#
#   The laser-flash / tick-detection test is also available from the
#   existing rotary-encoder test menu (long-press → Test Mode →
#   “Escapement Test”).
#
# Hardware
#   • ADS1115 on I2C (GP4/GP5) address 0x4A – A0 used for sensing
#   • I2CEncoderLibV21 (address 0x47) – front laser on GP2, side on GP1
#   • DS18B20 on GP13 (optional temperature)
#   • Status LED on GP15
#   • 8 digital bell inputs kept for compatibility (GP14,16-22)
#
# Configuration (settings.toml)
#   All timing, thresholds and feature flags live in settings.toml.
#   No code change is required to enable/disable console or log output.
#
#   Example settings.toml entries:
#
#     CIRCUITPY_WIFI_SSID = "yourssid"
#     CIRCUITPY_WIFI_PASSWORD = "yourpassword"
#
#     TICK_NOTE = "76"
#     TICK_VELOCITY = "110"
#     ADC_ALPHA = "0.002"
#     HYSTERESIS = "2500"
#     ADC_READ_INTERVAL = "0.015"
#     REPORT_INTERVAL = "60.0"
#     LASER_FLASH_INTERVAL = "0.625"
#
#     CONSOLE_ENABLED = "1"
#     LOG_ENABLED = "1"
#     LOG_PREFIX = "escapement_"
#
# Filename: pico_circuitp_escapement_sense_async.py

import asyncio
import time
import board
import busio
import digitalio
import usb_hid
import usb_midi
import rtc
import wifi
import adafruit_connection_manager
import adafruit_ntp
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode
from adafruit_onewire.bus import OneWireBus
from adafruit_ds18x20 import DS18X20
from adafruit_ads1x15 import ADS1115, AnalogIn, ads1x15
import i2cencoderlibv21 as enc_lib

from config_loader import load_config

cfg = load_config()

TICK_NOTE = cfg.get_int("escapement.tick_note", 76)
TICK_VELOCITY_START = cfg.get_int("escapement.tick_velocity", 70)
ADC_ALPHA = cfg.get_float("escapement.adc_alpha", 0.002)
HYSTERESIS = cfg.get_int("escapement.hysteresis", 2500)
ADC_READ_INTERVAL = cfg.get_float("escapement.adc_read_interval", 0.015)
REPORT_INTERVAL = cfg.get_float("escapement.report_interval", 60.0)
LASER_FLASH_INTERVAL = cfg.get_float("escapement.laser_flash_interval", 0.625)
NOMINAL_PERIOD = 60.0 / cfg.get_float("escapement.ticks_per_minute", 48.0)
CAPTURE_WINDOW = cfg.get_float("escapement.capture_window", 0.25)
MAX_MISSES = cfg.get_int("escapement.max_misses", 3)
PLL_ENABLED = cfg.get_bool("escapement.pll_enabled", False)
CONSOLE_ENABLED = cfg.console_enabled
LOG_ENABLED = cfg.log_enabled
LOG_PREFIX = cfg.log_prefix
LASER_ON_LEVEL = cfg.get_int("escapement.laser_on_level", 80)
TICK_LED_ENABLED = cfg.get_bool("escapement.tick_led_enabled", True)
TICK_LED_RED = cfg.get_hex("escapement.tick_led_red", 0x400000)
TICK_LED_GREEN = cfg.get_hex("escapement.tick_led_green", 0x004000)
SIDE_OFF = 255
SIDE_ON = 0
FRONT_OFF = 255
FRONT_ON = LASER_ON_LEVEL
VELOCITY_MIN = 50
VELOCITY_MAX = 90
SUMMARY_INTERVAL = cfg.get_float("escapement.summary_interval", 900.0)

print("sandsense.py – config from settings.json")
cfg.banner()
print(
    " TICK_NOTE=%s HYSTERESIS=%s REPORT=%ss LASER=%ss PLL=%s"
    % (TICK_NOTE, HYSTERESIS, REPORT_INTERVAL, LASER_FLASH_INTERVAL, PLL_ENABLED)
)

log_file = None

def open_log_file():
    global log_file
    if not LOG_ENABLED:
        return
    try:
        log_file = open("%ssession.txt" % LOG_PREFIX, "a")
        log_file.write("Log started %s\n" % format_time())
        log_file.flush()
        print("Logging to", "%ssession.txt" % LOG_PREFIX)
    except OSError as e:
        print("Log open failed:", e)
        log_file = None

# ------------------------------------------------------------------
# time helpers
# ------------------------------------------------------------------
def format_time(struct_time=None):
    if struct_time is None:
        struct_time = time.localtime()
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    months   = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return (f"{weekdays[struct_time.tm_wday]} {struct_time.tm_mday} "
            f"{months[struct_time.tm_mon]} {struct_time.tm_year} "
            f"{struct_time.tm_hour:02d}:{struct_time.tm_min:02d}:{struct_time.tm_sec:02d}")

# ------------------------------------------------------------------
# output handlers (console + log) – enabled purely from settings.toml
# ------------------------------------------------------------------
class OutputHandler:
    def __init__(self, name, enabled):
        self.name = name
        self.enabled = enabled

    def emit(self, msg):
        if self.enabled:
            self._emit(msg)

    def _emit(self, msg):
        pass

    def on_startup(self):
        status = "enabled" if self.enabled else "disabled"
        print(f"  [{self.name:8}] {status}")

class ConsoleHandler(OutputHandler):
    def __init__(self):
        super().__init__("console", CONSOLE_ENABLED)

    def _emit(self, msg):
        print(msg)


handlers = []
console_handler = ConsoleHandler()
# log_handler     = LogHandler()
handlers.append(console_handler)
# handlers.append(log_handler)

log_queue = []
log_file = None

def emit(msg):
    if CONSOLE_ENABLED:
        print(msg)
    if LOG_ENABLED and log_file is not None:
        if len(log_queue) < 100:
            log_queue.append(msg)

async def log_writer_task():
    while True:
        if log_queue and log_file:
            line = log_queue.pop(0)
            log_file.write(line + "\n")
            log_file.flush()     # still slow, but only this task blocks
        await asyncio.sleep_ms(50)

print("\n=== Output Handlers ===")
for h in handlers:
    h.on_startup()
print(f" [log     ] {'enabled (queued)' if LOG_ENABLED else 'disabled'}")
print("=======================\n")

# ------------------------------------------------------------------
# shared runtime state
# ------------------------------------------------------------------
summary_tick_count   = 0
summary_start_time   = time.monotonic()
summary_start_label  = "(pending NTP)"

adc_avg              = None
adc_state            = False
last_adc_read        = time.monotonic()
last_edge_time       = time.monotonic()
tick_count           = 0
period_start_time    = time.monotonic()
midi_velocity        = TICK_VELOCITY_START
velocity_direction   = 1

last_temperature     = None

hour_tick_count      = 0
hour_start_time      = time.monotonic()
hour_start_label     = format_time()          # wall-clock label when hour bucket opened

next_expected_tick   = None
pll_locked           = False
consecutive_misses   = 0

laser_flash_state    = False
last_laser_toggle    = time.monotonic()

in_test_mode         = False
in_submenu           = False
menu_idx             = 0
bell_idx             = 0
laser_seq_step       = 0
button_down          = False
press_time           = 0
last_counter         = 0

tick_led_red         = False     # False → next tick shows green; True → red

escapement_test_active = False   # set True when “Escapement Test” is selected

last_tick_was_virtual = False    # global

# ------------------------------------------------------------------
# Hardware initialisation helpers
# ------------------------------------------------------------------

def init_onewire():
    """DS18B20 on GP13 (optional)."""
    global ds18
    try:
        ow_bus = OneWireBus(board.GP13)
        devices = ow_bus.scan()
        if devices:
            ds18 = DS18X20(ow_bus, devices[0])
            print(f"DS18B20 found – {ds18.temperature:0.2f} C")
        else:
            ds18 = None
            print("No DS18B20 found")
    except Exception as e:
        ds18 = None
        print("OneWire init failed:", e)


def init_status_led():
    """On-board status LED on GP15."""
    global led
    led = digitalio.DigitalInOut(board.GP15)
    led.direction = digitalio.Direction.OUTPUT
    led.value = False


def init_bells():
    """Eight digital bell inputs + HID keyboard."""
    global bells, enabled, kbd
    bells = [
        {"pin": board.GP22, "key": Keycode.ONE,   "note": 41},
        {"pin": board.GP21, "key": Keycode.TWO,   "note": 40},
        {"pin": board.GP20, "key": Keycode.THREE, "note": 38},
        {"pin": board.GP19, "key": Keycode.FOUR,  "note": 36},
        {"pin": board.GP18, "key": Keycode.FIVE,  "note": 34},
        {"pin": board.GP17, "key": Keycode.SIX,   "note": 33},
        {"pin": board.GP16, "key": Keycode.SEVEN, "note": 31},
        {"pin": board.GP14, "key": Keycode.EIGHT, "note": 29},
    ]
    for b in bells:
        s = digitalio.DigitalInOut(b["pin"])
        s.pull = digitalio.Pull.UP
        b["sensor"] = s
    enabled = [True] * 8
    kbd = Keyboard(usb_hid.devices)


def init_midi():
    """USB MIDI output port and status bytes."""
    global midi_out, NOTE_ON, NOTE_OFF
    midi_out = usb_midi.ports[1]
    channel  = 0
    NOTE_ON  = 0x90 | channel
    NOTE_OFF = 0x80 | channel


def init_i2c_bus():
    """Shared I2C bus on GP4 / GP5."""
    global i2c
    i2c = busio.I2C(board.GP5, board.GP4)
    print("I2C bus ready (GP4/GP5)")


def init_ads1115():
    """ADS1115 at 0x4A – A0 used for escapement sensing."""
    global ads, chan0, chan1
    ads = ADS1115(i2c, address=0x4A)
    ads.data_rate = 860
    chan0 = AnalogIn(ads, ads1x15.Pin.A0)
    chan1 = AnalogIn(ads, ads1x15.Pin.A1)
    print("ADS1115 ready (A0 sensing)")

def init_encoder():
    """I2CEncoderLibV21 + laser PWM outputs."""
    global encoder
    
    encoder = enc_lib.I2CEncoderLibV21(i2c, address=0x47)
    
    # Force external PIC back to defaults (helps Thonny soft-restart)
    if hasattr(encoder, "reset"):
        encoder.reset()
        time.sleep(0.1)
    
    print("Encoder ID:", int.from_bytes(encoder.readIDCode()),
          "Ver:", int.from_bytes(encoder.readVersion()))

    # clear any previous callbacks
    for n in ("onChange", "onButtonPush", "onButtonRelease",
              "onIncrement", "onDecrement", "onButtonLongPush",
              "onButtonDoublePush", "onButtonLongPress"):
        if hasattr(encoder, n):
            setattr(encoder, n, None)

    config = (enc_lib.INT_DATA | enc_lib.WRAP_ENABLE | enc_lib.DIRE_LEFT |
              enc_lib.IPUP_ENABLE | enc_lib.RMOD_X1 | enc_lib.RGB_ENCODER)
    
    encoder.begin(config)
    encoder.write_antibounce_period(8)
    encoder.write_double_push_period(30)
    encoder.write_fade_rgb(0)
    encoder.setInterrupts(enc_lib.RINC | enc_lib.RDEC)
    encoder.write_counter(0)
    encoder.write_max(35)
    encoder.write_min(-20)
    encoder.write_step_size(1)

    gp_conf = enc_lib.GP_PWM | enc_lib.GP_PULL_DI | enc_lib.GP_INT_DI
    
    encoder.writeGP1conf(gp_conf)
    encoder.writeGP2conf(gp_conf)
    
    encoder.writeGP1(SIDE_OFF)
    encoder.writeGP2(FRONT_OFF)
    
    time.sleep(0.05)
    
    encoder.writeGP1(SIDE_OFF)
    encoder.writeGP2(FRONT_OFF)

    # attach callbacks
    encoder.onIncrement     = on_rotate
    encoder.onDecrement     = on_rotate
    encoder.onButtonPush    = on_button_down
    encoder.onButtonRelease = on_button_up

def init_all_hardware():
    """Call everything in the correct order."""
    print("Initialising hardware...")
    init_status_led()
    init_onewire()
    init_bells()
    init_midi()
    init_i2c_bus()
    init_ads1115()
    init_encoder()
    print("Hardware initialisation complete\n")
    lasers_all_off()


# ------------------------------------------------------------------
# menu definition (Escapement Test added)
# ------------------------------------------------------------------
menu = [
    {"name": "Enable Bells",    "rgb": 0xFF8800, "type": "toggle"},
    {"name": "Test Bells",      "rgb": 0x00FF00, "type": "trigger"},
    {"name": "All On",          "rgb": 0x00FFFF, "type": "all_on"},
    {"name": "Laser Test",      "rgb": 0xFF00FF, "type": "laser_seq"},
    {"name": "Escapement Test", "rgb": 0xFFFF00, "type": "escapement"},  # ← new
]

# ------------------------------------------------------------------
# MIDI + tick emission
# ------------------------------------------------------------------
def _emit_tick(virtual=False):
    global tick_count, summary_tick_count, midi_velocity, velocity_direction
    global tick_led_red, last_tick_was_virtual

    tick_count += 1
    summary_tick_count += 1
    last_tick_was_virtual = virtual   # True for PLL fill, False for real
    
    # RGB encoder: alternate green / red on each tick
    
    if TICK_LED_ENABLED:
        tick_led_red = not tick_led_red
        encoder.write_rgb_code(TICK_LED_RED if tick_led_red else TICK_LED_GREEN)
    
    midi_out.write(bytearray([NOTE_ON,  TICK_NOTE, midi_velocity]))
    midi_out.write(bytearray([NOTE_OFF, TICK_NOTE, 0]))

    midi_velocity += velocity_direction
    if midi_velocity >= VELOCITY_MAX:
        midi_velocity = VELOCITY_MAX
        velocity_direction = -1
    elif midi_velocity <= VELOCITY_MIN:
        midi_velocity = VELOCITY_MIN
        velocity_direction = 1

    if virtual:
        emit(f"{format_time()}  VIRTUAL tick (PLL)  vel={midi_velocity}")

# ------------------------------------------------------------------
# Escapement sensing – broken into clear steps
# ------------------------------------------------------------------

def _read_adc():
    """Return current raw ADC value from A0."""
    return chan0.value


def _update_average(value):
    """Slow adaptive average for day/night tracking."""
    global adc_avg
    if adc_avg is None:
        adc_avg = float(value)
    else:
        adc_avg = (1.0 - ADC_ALPHA) * adc_avg + ADC_ALPHA * value
    return adc_avg


def _thresholds():
    """Return (high, low) thresholds from the current average."""
    return adc_avg + HYSTERESIS, adc_avg - HYSTERESIS


def _detect_edge(value, high_thresh, low_thresh):
    """
    Update adc_state and return True if a rising edge (real tick) occurred.
    """
    global adc_state, last_edge_time
    now = time.monotonic()

    if not adc_state and value > high_thresh:
        adc_state = True
        last_edge_time = now
        return True

    if adc_state and value < low_thresh:
        adc_state = False

    return False

def _pll_handle_miss(now):
    global next_expected_tick, pll_locked, consecutive_misses
    if not pll_locked or next_expected_tick is None:
        return
    # Wait a bit past the expected time before declaring a miss
    if now < next_expected_tick + 0.15:   # 150 ms grace
        return
    consecutive_misses += 1
    if consecutive_misses <= MAX_MISSES:
        next_expected_tick += NOMINAL_PERIOD
        _emit_tick(virtual=True)
    else:
        pll_locked = False
        next_expected_tick = None
        emit("PLL unlocked – waiting for real tick")
        
def _pll_handle_real(now):
    global next_expected_tick, pll_locked, consecutive_misses
    global last_tick_was_virtual

    if last_tick_was_virtual:
        # Already counted this beat as virtual – retune only
        next_expected_tick = now + NOMINAL_PERIOD
        consecutive_misses = 0
        pll_locked = True
        last_tick_was_virtual = False
        return

    next_expected_tick = now + NOMINAL_PERIOD
    pll_locked = True
    consecutive_misses = 0
    _emit_tick(virtual=False)


def _pll_process(now, real_tick):
    """Full PLL path (only used when PLL_ENABLED is True)."""
    global next_expected_tick, pll_locked, consecutive_misses

    if next_expected_tick is None:
        # Waiting for first real tick to acquire lock
        if real_tick:
            _pll_handle_real(now)
        return

    if real_tick:
        error = now - next_expected_tick
        if abs(error) < CAPTURE_WINDOW or not pll_locked:
            _pll_handle_real(now)
        else:
            # Out-of-window – treat as new acquisition
            _pll_handle_real(now)
    else:
        _pll_handle_miss(now)

def _maybe_report(now, high_thresh, low_thresh):
    """Print the minute summary when REPORT_INTERVAL has elapsed."""
    global tick_count, period_start_time

    if now - period_start_time < REPORT_INTERVAL:
        return

    elapsed = now - period_start_time
    tpm = tick_count * (60.0 / elapsed) if elapsed > 0 else 0.0
    temp_str = f"{last_temperature:.3f}C" if last_temperature is not None else "n/a"
    
#     msg = (
#         f"{format_time()} {elapsed:.1f} s {tick_count} ticks {tpm:.2f} "
#         f"(target 48.00) {'ON' if PLL_ENABLED else 'OFF'} "
#         f"locked={pll_locked} misses={consecutive_misses} "
#         f"{adc_avg:.0f} / {high_thresh:.0f} / {low_thresh:.0f} {temp_str}"
#     )
    
    msg = (f"{format_time()} {elapsed:.1f} s {tick_count} ticks {tpm:.2f}   (target 48.00) {'ON' if PLL_ENABLED else 'OFF'} locked={pll_locked}  misses={consecutive_misses} {adc_avg:.0f} / {high_thresh:.0f} / {low_thresh:.0f} {temp_str}")
    
    emit(msg)
    
    tick_count = 0
    # Keep grid aligned even if this report was late:
    period_start_time += REPORT_INTERVAL
    # If we were very late, catch up so we don't burst reports:
    if now - period_start_time >= REPORT_INTERVAL:
        period_start_time = now
        
def _maybe_summary_report(now):
    global summary_tick_count, summary_start_time, summary_start_label

    if now - summary_start_time < SUMMARY_INTERVAL:
        return

    elapsed = now - summary_start_time
    ticks = summary_tick_count
    tpm = ticks * (60.0 / elapsed) if elapsed > 0 else 0.0
    target_ticks = SUMMARY_INTERVAL * (48.0 / 60.0)

    from_label = summary_start_label   # capture first
    to_label = format_time()

    msg = (
        f"\n********** SUMMARY ({SUMMARY_INTERVAL:.0f}s) **********\n"
        f"From : {from_label}\n"
        f"To   : {to_label}\n"
        f"Ticks : {ticks}\n"
        f"Rate : {tpm:.2f} / min "
        f"(target 48.00 → {target_ticks:.0f} / period)\n"
        "********************************"
    )
    emit(msg)

    summary_tick_count = 0
    summary_start_time += SUMMARY_INTERVAL
    if now - summary_start_time >= SUMMARY_INTERVAL:
        summary_start_time = now
    summary_start_label = to_label      # next period's From

def _maybe_hour_report(now):
    global hour_tick_count, hour_start_time, hour_start_label

    if now - hour_start_time < 3600.0:
        return

    elapsed = now - hour_start_time
    tph = hour_tick_count                     # ticks in this hour
    tpm = hour_tick_count * (60.0 / elapsed) if elapsed > 0 else 0.0

    msg = (
        "\n********** HOUR TOTAL **********\n"
        f"From  : {hour_start_label}\n"
        f"To    : {format_time()}\n"
        f"Ticks : {tph}\n"
        f"Rate  : {tpm:.2f} / min   (target 48.00 → 2880 / hour)\n"
        "********************************"
    )
    emit(msg)

    hour_tick_count  = 0
    hour_start_time  = now
    if now - hour_start_time >= 3600.0:
        hour_start_time = now
    hour_start_label = format_time()

def check_escapement():
    """
    Top-level sensing step – called frequently by EscapementTask.
    Keeps the high-level flow obvious.
    """
    global last_adc_read

    now = time.monotonic()
    if now - last_adc_read < ADC_READ_INTERVAL:
        return
    last_adc_read = now

    value = _read_adc()
    _update_average(value)
    high_thresh, low_thresh = _thresholds()
    real_tick = _detect_edge(value, high_thresh, low_thresh)

    if PLL_ENABLED:
        _pll_process(now, real_tick)
    elif real_tick:
        _emit_tick(virtual=False)

    _maybe_report(now, high_thresh, low_thresh)
    _maybe_summary_report(now)
    # _maybe_hour_report(now)
# ------------------------------------------------------------------
# AppTask base – every long-running job looks the same
# ------------------------------------------------------------------
class AppTask:
    name = "unnamed"

    async def setup(self):
        pass

    async def run(self):
        raise NotImplementedError

    async def shutdown(self):
        pass

# ------------------------------------------------------------------
# concrete tasks
# ------------------------------------------------------------------
class LaserFlashTask(AppTask):
    """Flashes the front laser at 48 times per minute."""
    name = "laser"

    async def run(self):
        global laser_flash_state
        while True:
            encoder.writeGP1(SIDE_OFF)  # side always off
            now = time.monotonic()
            # deterministic on/off phase from the clock
            phase_on = int(now / LASER_FLASH_INTERVAL) % 2 == 0
            
            encoder.writeGP1(SIDE_OFF)
            desired = FRONT_ON if phase_on else FRONT_OFF
            if laser_flash_state != phase_on:
                laser_flash_state = phase_on
                encoder.writeGP2(desired)
                
            await asyncio.sleep(0.02)

class EscapementTask(AppTask):
    """ADC sampling + adaptive threshold + PLL."""
    name = "escapement"

    async def run(self):
        while True:
            # always run the sensor; the test menu just makes it obvious
            check_escapement()
            await asyncio.sleep(ADC_READ_INTERVAL)

class EncoderUITask(AppTask):
    name = "encoder"

    async def run(self):
        while True:
            encoder.update_status()
            encoder.writeGP1(SIDE_OFF)   # side laser always off
            if not in_test_mode:
                pass   # leave RGB to the tick toggle
            # or only clear RGB when entering/leaving test mode
            await asyncio.sleep(0.01)

class TemperatureTask(AppTask):
    name = "temperature"

    async def run(self):
        global last_temperature
        if ds18 is None:
            return
        while True:
            try:
                # temp = ds18.temperature
                last_temperature = ds18.temperature
                #emit(f"temp: {format_time()}  {temp:.3f} C")
            except Exception as e:
                last_temperature = None
                #emit(f"temp read error: {e}")
            await asyncio.sleep(60)

class StatusLedTask(AppTask):
    name = "status_led"

    async def run(self):
        while True:
            led.value = not led.value
            await asyncio.sleep(0.5 if not in_test_mode else 0.12)
            
class LogWriterTask(AppTask):
    """Drains log_queue to flash without blocking sensing/laser tasks."""
    name = "log_writer"

    async def run(self):
        pending = 0
        while True:
            # Write at most a few lines per turn, then yield
            written = 0
            while log_queue and log_file and written < 3:
                line = log_queue.pop(0)
                try:
                    log_file.write(line + "\n")
                    pending += 1
                    written += 1
                except OSError as e:
                    print("Log write failed:", e)
                    break

            # Flush occasionally (flush is the slow part on CIRCUITPY)
                if log_file and pending >= 1:
                    try:
                        log_file.flush()
                    except OSError:
                        pass
                    pending = 0

            await asyncio.sleep(0.05)

# ------------------------------------------------------------------
# encoder callbacks (kept simple)
# ------------------------------------------------------------------
def on_rotate():
    global menu_idx, bell_idx, last_counter, laser_seq_step, escapement_test_active
    if not in_test_mode:
        return
    now = int.from_bytes(encoder.readCounter32(), "big")
    delta = now - last_counter
    last_counter = now
    if delta == 0:
        return
    steps = abs(delta)
    direction = 1 if delta > 0 else -1

    if in_submenu:
        bell_idx = (bell_idx + direction * steps) % 8
    else:
        old_type = menu[menu_idx]["type"]
        if old_type == "laser_seq":
            laser_seq_step = (laser_seq_step + direction * steps) % 4
            _update_laser_seq(laser_seq_step)
        else:
            menu_idx = (menu_idx + direction * steps) % len(menu)
            encoder.write_rgb_code(menu[menu_idx]["rgb"])
            escapement_test_active = (menu[menu_idx]["type"] == "escapement")
            if old_type == "laser_seq":
                _update_laser_seq(None)

def on_button_down():
    global button_down, press_time
    button_down = True
    press_time = time.monotonic()

def on_button_up():
    global button_down, in_test_mode, in_submenu, menu_idx, bell_idx, escapement_test_active
    if not button_down:
        return
    duration = time.monotonic() - press_time
    button_down = False

    if duration >= 0.7:                     # long press – enter / exit test mode
        if in_test_mode:
            if menu[menu_idx]["type"] == "laser_seq":
                _update_laser_seq(None)
            in_test_mode = False
            in_submenu = False
            escapement_test_active = False
            menu_idx = bell_idx = 0
            encoder.write_rgb_code(0x000000)
            emit("EXIT TEST MODE")
        else:
            in_test_mode = True
            in_submenu = False
            menu_idx = bell_idx = 0
            encoder.write_rgb_code(0xFFFF00)
            emit("ENTER TEST MODE")
    elif in_test_mode:
        if not in_submenu:
            in_submenu = True
            encoder.write_rgb_code(menu[menu_idx]["rgb"])
        else:
            _execute_menu_action()
            in_submenu = False
            encoder.write_rgb_code(menu[menu_idx]["rgb"] // 2)

def _execute_menu_action():
    global enabled, escapement_test_active
    item = menu[menu_idx]
    if item["type"] == "toggle":
        enabled[bell_idx] = not enabled[bell_idx]
        encoder.write_rgb_code(0x00FF00 if enabled[bell_idx] else 0xFF0000)
        emit(f"Bell {bell_idx+1} {'ENABLED' if enabled[bell_idx] else 'DISABLED'}")
    elif item["type"] == "trigger":
        # simple manual ring
        b = bells[bell_idx]
        kbd.press(b["key"])
        kbd.release_all()
        midi_out.write(bytearray([NOTE_ON, b["note"], 100]))
        midi_out.write(bytearray([NOTE_OFF, b["note"], 0]))
        emit(f"Manual ring → bell {bell_idx+1}")
    elif item["type"] == "all_on":
        enabled[:] = [True] * 8
        emit("ALL BELLS FORCED ON")
    elif item["type"] == "escapement":
        escapement_test_active = True
        emit("Escapement Test active – laser + PLL running")
        encoder.write_rgb_code(0xFFFF00)

def lasers_all_off():
    """Emphatic laser shutdown."""
    encoder.writeGP1(SIDE_OFF)
    encoder.writeGP1(SIDE_OFF)
    encoder.writeGP2(FRONT_OFF)


def _update_laser_seq(step=None):
    global laser_seq_step
    encoder.writeGP1(SIDE_OFF)   # side never on
    if step is None:
        encoder.writeGP2(FRONT_OFF)
        laser_seq_step = 0
        return
    laser_seq_step = step % 4
    encoder.writeGP2(FRONT_ON if laser_seq_step in (1, 2) else FRONT_OFF)

# ------------------------------------------------------------------
# WiFi / NTP (optional)
# ------------------------------------------------------------------
def setup_wifi():
    ssid = cfg.get_str("wifi.ssid", "")
    password = cfg.get_str("wifi.password", "")
    if not ssid or not password:
        print("No wifi in settings.json – skipping NTP")
        return
    try:
        wifi.radio.connect(ssid, password)
        print("Connected to WiFi:", ssid)
        pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
        ntp  = adafruit_ntp.NTP(pool, tz_offset=0, server="pool.ntp.org", cache_seconds=3600)
        the_rtc = rtc.RTC()
        the_rtc.datetime = ntp.datetime
        print("RTC synced:", format_time())
    except Exception as e:
        print("WiFi / NTP failed:", e)

# ------------------------------------------------------------------
# main
# ------------------------------------------------------------------
async def main():
    init_all_hardware()
    setup_wifi()
    open_log_file()

    global summary_start_time, summary_start_label, summary_tick_count
    summary_tick_count = 0
    summary_start_time = time.monotonic()
    summary_start_label = format_time()   # true "From" for first period
    

    tasks = [
        LaserFlashTask(),
        EscapementTask(),
        EncoderUITask(),
        TemperatureTask(),
        StatusLedTask(),
        LogWriterTask(),
    ]

    print("\n=== Starting tasks ===")
    for t in tasks:
        print(f"  [{t.name}] starting")
        await t.setup()

    runners = [asyncio.create_task(t.run()) for t in tasks]

    print("\nSYSTEM ARMED – long-press encoder for Test Mode")
    print("Escapement sensing + 48/min laser + PLL running\n")

    try:
        await asyncio.gather(*runners)
    finally:
        print("=== Shutting down ===")
        for t in tasks:
            await t.shutdown()
        encoder.write_rgb_code(0x000000)
        encoder.writeGP1(SIDE_OFF)
        encoder.writeGP1(SIDE_OFF)   # second write in case of I2C glitch
        encoder.writeGP2(FRONT_OFF)
        # log_handler.close()
        # Drain any remaining lines
        while log_queue and log_file:
            log_file.write(log_queue.pop(0) + "\n")
        if log_file:
            log_file.flush()
            log_file.close()
        print("All lights OFF – side laser OFF – safe shutdown complete")

# ------------------------------------------------------------------
try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("\nStopped by user")