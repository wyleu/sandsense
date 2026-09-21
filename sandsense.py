# sandsense.py
#
# CircuitPython Tower-Clock Escapement Sensor (async)
# Raspberry Pi Pico 2W
#
# Boot order (field / battery / brick):
#   hardware → WiFi → mDNS → HTTP/WS → NTP (best effort) → tasks
# NTP failure must not block /status or the laser.
#
import asyncio
import time
import board
import busio
import digitalio
import json

import wifi
import adafruit_connection_manager

try:
    import usb_hid
except Exception:
    usb_hid = None
try:
    import usb_midi
except Exception:
    usb_midi = None
try:
    from adafruit_hid.keyboard import Keyboard
    from adafruit_hid.keycode import Keycode
except Exception:
    Keyboard = None
    Keycode = None

from adafruit_onewire.bus import OneWireBus
from adafruit_ds18x20 import DS18X20
from adafruit_ads1x15 import ADS1115, AnalogIn, ads1x15
import i2cencoderlibv21 as enc_lib
from sand_status import from_sandsense
import sand_net
from sand_net import connect_wifi, start_mdns, sync_time, hostname_from_cfg

from config_loader import load_config

try:
    from adafruit_httpserver import Server, Request, Response, GET, Websocket
    HAS_HTTP = True
except ImportError:
    HAS_HTTP = False
    print("adafruit_httpserver not found – /status disabled")

_status_server = None
_tick_ws = None

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
CONSOLE_MAX = 80
console_lines = []
last_tpm = 0
tick_seq = 0

print("sandsense.py – config from settings.json")
cfg.banner()
print(
    " TICK_NOTE=%s HYSTERESIS=%s REPORT=%ss LASER=%ss PLL=%s"
    % (TICK_NOTE, HYSTERESIS, REPORT_INTERVAL, LASER_FLASH_INTERVAL, PLL_ENABLED)
)

log_file = None
log_queue = []


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


def format_time(struct_time=None):
    if struct_time is None:
        struct_time = time.localtime()
    weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    months = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return (f"{weekdays[struct_time.tm_wday]} {struct_time.tm_mday} "
            f"{months[struct_time.tm_mon]} {struct_time.tm_year} "
            f"{struct_time.tm_hour:02d}:{struct_time.tm_min:02d}:{struct_time.tm_sec:02d}")


def _status_payload():
    ip = None
    ssid = sand_net.last_ssid
    try:
        ip = str(wifi.radio.ipv4_address)
    except Exception:
        pass
    try:
        if wifi.radio.ap_info:
            ssid = wifi.radio.ap_info.ssid
    except Exception:
        pass
    last = console_lines[-1] if console_lines else None
    try:
        ts = int(time.time())
    except Exception:
        ts = 0
    return from_sandsense({
        "id": getattr(cfg, "device_name", "sandsense-clock"),
        "name": getattr(cfg, "device_name", "sandsense-clock"),
        "family": getattr(cfg, "family", "sandsense"),
        "role": getattr(cfg, "role", "sense"),
        "location": getattr(cfg, "location", ""),
        "ip": ip,
        "ssid": ssid,
        "mode": "escapement",
        "pll_enabled": PLL_ENABLED,
        "pll_locked": pll_locked,
        "misses": consecutive_misses,
        "tick_count": tick_count,
        "report_interval_s": REPORT_INTERVAL,
        "last_tpm": last_tpm,
        "tick_seq": tick_seq,
        "adc_avg": adc_avg,
        "temp_c": last_temperature,
        "tick_led_enabled": TICK_LED_ENABLED,
        "tick_led_red": tick_led_red,
        "tick_led_red_hex": TICK_LED_RED,
        "tick_led_green_hex": TICK_LED_GREEN,
        "last_line": last,
        "ts": ts,
    })


def start_status_server():
    global _status_server, _tick_ws
    if not HAS_HTTP:
        return None
    try:
        pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
        server = Server(pool, debug=False)

        @server.route("/status", GET)
        def status_route(request: Request):
            body = json.dumps(_status_payload())
            return Response(
                request,
                body=body,
                content_type="application/json",
                headers={"Access-Control-Allow-Origin": "*"},
            )

        @server.route("/ws", GET)
        def ws_route(request: Request):
            global _tick_ws
            if _tick_ws is not None:
                try:
                    _tick_ws.close()
                except Exception:
                    pass
            _tick_ws = Websocket(request)
            print("Tick WebSocket client connected")
            return _tick_ws

        host = str(wifi.radio.ipv4_address)
        server.start(host=host, port=80)
        print("Status HTTP: http://%s/status" % host)
        print("Tick WS     : ws://%s/ws" % host)
        _status_server = server
        return server
    except Exception as e:
        print("Status/WS server failed:", e)
        _status_server = None
        return None


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
handlers.append(console_handler)


def emit(msg):
    console_lines.append(str(msg))
    if len(console_lines) > CONSOLE_MAX:
        console_lines.pop(0)
    if CONSOLE_ENABLED:
        print(msg)
    if LOG_ENABLED and log_file is not None:
        if len(log_queue) < 100:
            log_queue.append(msg)


print("\n=== Output Handlers ===")
for h in handlers:
    h.on_startup()
print(f" [log     ] {'enabled (queued)' if LOG_ENABLED else 'disabled'}")
print("=======================\n")

summary_tick_count = 0
summary_start_time = time.monotonic()
summary_start_label = "(pending NTP)"
adc_avg = None
adc_state = False
last_adc_read = time.monotonic()
last_edge_time = time.monotonic()
tick_count = 0
period_start_time = time.monotonic()
midi_velocity = TICK_VELOCITY_START
velocity_direction = 1
last_temperature = None
hour_tick_count = 0
hour_start_time = time.monotonic()
hour_start_label = format_time()
next_expected_tick = None
pll_locked = False
consecutive_misses = 0
laser_flash_state = False
last_laser_toggle = time.monotonic()
in_test_mode = False
in_submenu = False
menu_idx = 0
bell_idx = 0
laser_seq_step = 0
button_down = False
press_time = 0
last_counter = 0
tick_led_red = False
escapement_test_active = False
last_tick_was_virtual = False
ds18 = None
led = None
bells = []
enabled = []
kbd = None
midi_out = None
NOTE_ON = 0x90
NOTE_OFF = 0x80
i2c = None
ads = None
chan0 = None
chan1 = None
encoder = None


def init_onewire():
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
    global led
    led = None
    try:
        led = digitalio.DigitalInOut(board.GP15)
        led.direction = digitalio.Direction.OUTPUT
        led.value = False
    except Exception as e:
        print("status LED skipped:", e)


def init_bells():
    global bells, enabled, kbd
    _one = getattr(Keycode, "ONE", 1) if Keycode else 1
    keys = []
    if Keycode:
        keys = [
            Keycode.ONE, Keycode.TWO, Keycode.THREE, Keycode.FOUR,
            Keycode.FIVE, Keycode.SIX, Keycode.SEVEN, Keycode.EIGHT,
        ]
    else:
        keys = [None] * 8
    pins = [board.GP22, board.GP21, board.GP20, board.GP19,
            board.GP18, board.GP17, board.GP16, board.GP14]
    notes = [41, 40, 38, 36, 34, 33, 31, 29]
    bells = []
    for pin, key, note in zip(pins, keys, notes):
        s = digitalio.DigitalInOut(pin)
        s.pull = digitalio.Pull.UP
        bells.append({"pin": pin, "key": key, "note": note, "sensor": s})
    enabled = [True] * 8
    kbd = None
    
    try:
        import supervisor
        if not getattr(supervisor.runtime, "usb_connected", False):
            print("USB MIDI skipped: no host")
            kbd = None
            return
    except Exception:
        kbd = None
        return
    
  
    if Keyboard and usb_hid is not None:
        try:
            kbd = Keyboard(usb_hid.devices)
        except Exception as e:
            print("HID keyboard skipped:", e)


def init_midi():
    global midi_out, NOTE_ON, NOTE_OFF
    midi_out = None
    NOTE_ON = 0x90
    NOTE_OFF = 0x80
    try:
        import supervisor
        if not getattr(supervisor.runtime, "usb_connected", False):
            print("USB MIDI skipped: no host")
            midi_out = None
            return
    except Exception:
        midi_out = None
        return
    
    try:
        if usb_midi and usb_midi.ports and len(usb_midi.ports) > 1:
            midi_out = usb_midi.ports[1]
            print("USB MIDI ready")
        else:
            print("USB MIDI skipped: no ports")
    except Exception as e:
        print("USB MIDI skipped:", e)


def init_i2c_bus():
    global i2c
    i2c = busio.I2C(board.GP5, board.GP4)
    print("I2C bus ready (GP4/GP5)")


def init_ads1115():
    global ads, chan0, chan1
    ads = ADS1115(i2c, address=0x4A)
    ads.data_rate = 860
    chan0 = AnalogIn(ads, ads1x15.Pin.A0)
    chan1 = AnalogIn(ads, ads1x15.Pin.A1)
    print("ADS1115 ready (A0 sensing)")


def init_encoder():
    global encoder
    encoder = enc_lib.I2CEncoderLibV21(i2c, address=0x47)
    if hasattr(encoder, "reset"):
        encoder.reset()
        time.sleep(0.1)
    print("Encoder ID:", int.from_bytes(encoder.readIDCode()),
          "Ver:", int.from_bytes(encoder.readVersion()))
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
    encoder.onIncrement = on_rotate
    encoder.onDecrement = on_rotate
    encoder.onButtonPush = on_button_down
    encoder.onButtonRelease = on_button_up


def init_all_hardware():
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


menu = [
    {"name": "Enable Bells", "rgb": 0xFF8800, "type": "toggle"},
    {"name": "Test Bells", "rgb": 0x00FF00, "type": "trigger"},
    {"name": "All On", "rgb": 0x00FFFF, "type": "all_on"},
    {"name": "Laser Test", "rgb": 0xFF00FF, "type": "laser_seq"},
    {"name": "Escapement Test", "rgb": 0xFFFF00, "type": "escapement"},
]


def _midi_write(buf):
    if midi_out is None:
        return
    try:
        midi_out.write(buf)
    except Exception:
        pass


def _emit_tick(virtual=False):
    global tick_count, summary_tick_count, midi_velocity, velocity_direction
    global tick_led_red, last_tick_was_virtual, tick_seq, _tick_ws

    tick_seq += 1
    tick_count += 1
    summary_tick_count += 1
    last_tick_was_virtual = virtual

    if _tick_ws is not None:
        try:
            payload = {
                "tick": tick_seq,
                "virtual": virtual,
                "t": time.monotonic(),
                "vel": midi_velocity,
            }
            _tick_ws.send_message(json.dumps(payload), fail_silently=True)
        except Exception:
            _tick_ws = None
            print("Tick WebSocket client disconnected")

    if TICK_LED_ENABLED:
        tick_led_red = not tick_led_red
        encoder.write_rgb_code(TICK_LED_RED if tick_led_red else TICK_LED_GREEN)

    _midi_write(bytearray([NOTE_ON, TICK_NOTE, midi_velocity]))
    _midi_write(bytearray([NOTE_OFF, TICK_NOTE, 0]))
    midi_velocity += velocity_direction
    if midi_velocity >= VELOCITY_MAX:
        midi_velocity = VELOCITY_MAX
        velocity_direction = -1
    elif midi_velocity <= VELOCITY_MIN:
        midi_velocity = VELOCITY_MIN
        velocity_direction = 1
    if virtual:
        emit(f"{format_time()}  VIRTUAL tick (PLL)  vel={midi_velocity}")


def _read_adc():
    return chan0.value


def _update_average(value):
    global adc_avg
    if adc_avg is None:
        adc_avg = float(value)
    else:
        adc_avg = (1.0 - ADC_ALPHA) * adc_avg + ADC_ALPHA * value
    return adc_avg


def _thresholds():
    return adc_avg + HYSTERESIS, adc_avg - HYSTERESIS


def _detect_edge(value, high_thresh, low_thresh):
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
    if now < next_expected_tick + 0.15:
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
    global next_expected_tick, pll_locked, consecutive_misses
    if next_expected_tick is None:
        if real_tick:
            _pll_handle_real(now)
        return
    if real_tick:
        _pll_handle_real(now)
    else:
        _pll_handle_miss(now)


def _maybe_report(now, high_thresh, low_thresh):
    global tick_count, period_start_time, last_tpm
    if now - period_start_time < REPORT_INTERVAL:
        return
    elapsed = now - period_start_time
    tpm = tick_count * (60.0 / elapsed) if elapsed > 0 else 0.0
    temp_str = f"{last_temperature:.3f}C" if last_temperature is not None else "n/a"
    last_tpm = tpm
    msg = (
        f"{format_time()} {elapsed:.1f} s {tick_count} tks {tpm:.2f}   "
        f"(aim 48.00) {'ON' if PLL_ENABLED else 'OFF'} {pll_locked}  "
        f"misses={consecutive_misses} {adc_avg:.0f} / {high_thresh:.0f} / "
        f"{low_thresh:.0f} {temp_str}"
    )
    emit(msg)
    tick_count = 0
    period_start_time += REPORT_INTERVAL
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
    from_label = summary_start_label
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
    summary_start_label = to_label


def check_escapement():
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


class AppTask:
    name = "unnamed"

    async def setup(self):
        pass

    async def run(self):
        raise NotImplementedError

    async def shutdown(self):
        pass


class LaserFlashTask(AppTask):
    name = "laser"

    async def run(self):
        global laser_flash_state
        while True:
            now = time.monotonic()
            phase_on = int(now / LASER_FLASH_INTERVAL) % 2 == 0
            encoder.writeGP1(SIDE_OFF)
            desired = FRONT_ON if phase_on else FRONT_OFF
            if laser_flash_state != phase_on:
                laser_flash_state = phase_on
                encoder.writeGP2(desired)
            await asyncio.sleep(0.02)


class EscapementTask(AppTask):
    name = "escapement"

    async def run(self):
        while True:
            check_escapement()
            await asyncio.sleep(ADC_READ_INTERVAL)


class EncoderUITask(AppTask):
    name = "encoder"

    async def run(self):
        while True:
            encoder.update_status()
            encoder.writeGP1(SIDE_OFF)
            await asyncio.sleep(0.01)


class TemperatureTask(AppTask):
    name = "temperature"

    async def run(self):
        global last_temperature
        if ds18 is None:
            return
        while True:
            try:
                last_temperature = ds18.temperature
            except Exception:
                last_temperature = None
            await asyncio.sleep(60)


class StatusLedTask(AppTask):
    name = "status_led"

    async def run(self):
        if led is None:
            return
        
        while True:
            led.value = not led.value
            await asyncio.sleep(0.5 if not in_test_mode else 0.12)


class LogWriterTask(AppTask):
    name = "log_writer"

    async def run(self):
        pending = 0
        while True:
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
                if log_file and pending >= 1:
                    try:
                        log_file.flush()
                    except OSError:
                        pass
                    pending = 0
            await asyncio.sleep(0.05)


class StatusHttpTask(AppTask):
    name = "status_http"

    async def run(self):
        if _status_server is None:
            return
        while True:
            try:
                _status_server.poll()
            except Exception as e:
                print("status poll:", e)
            await asyncio.sleep(0.05)


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
    if duration >= 0.7:
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
        b = bells[bell_idx]
        if kbd is not None and b["key"] is not None:
            try:
                kbd.press(b["key"])
                kbd.release_all()
            except Exception:
                pass
        _midi_write(bytearray([NOTE_ON, b["note"], 100]))
        _midi_write(bytearray([NOTE_OFF, b["note"], 0]))
        emit(f"Manual ring → bell {bell_idx+1}")
    elif item["type"] == "all_on":
        enabled[:] = [True] * 8
        emit("ALL BELLS FORCED ON")
    elif item["type"] == "escapement":
        escapement_test_active = True
        emit("Escapement Test active – laser + PLL running")
        encoder.write_rgb_code(0xFFFF00)


def lasers_all_off():
    encoder.writeGP1(SIDE_OFF)
    encoder.writeGP1(SIDE_OFF)
    encoder.writeGP2(FRONT_OFF)


def _update_laser_seq(step=None):
    global laser_seq_step
    encoder.writeGP1(SIDE_OFF)
    if step is None:
        encoder.writeGP2(FRONT_OFF)
        laser_seq_step = 0
        return
    laser_seq_step = step % 4
    encoder.writeGP2(FRONT_ON if laser_seq_step in (1, 2) else FRONT_OFF)
    
    
def stage(encoder, rgb, n=2):
    for _ in range(n):
        encoder.write_rgb_code(rgb)
        time.sleep(0.15)
        encoder.write_rgb_code(0)
        time.sleep(0.15)

async def main():
    global summary_start_time, summary_start_label, summary_tick_count
    
    init_all_hardware()
    
    stage(encoder, 0x000040)   # blue  — hw ok
    if connect_wifi(cfg):
        stage(encoder, 0x004000)   # green — wifi
        start_mdns(hostname_from_cfg(cfg))
        start_status_server()
        stage(encoder, 0x400000)   # red  — http
        sync_time(cfg)
        
    stage(encoder, 0x404000)       # yellow — armed    
    open_log_file()
    
    summary_tick_count = 0
    summary_start_time = time.monotonic()
    summary_start_label = format_time()

    tasks = [
        LaserFlashTask(),
        EscapementTask(),
        EncoderUITask(),
        TemperatureTask(),
        StatusLedTask(),
        LogWriterTask(),
        StatusHttpTask(),
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
        encoder.writeGP2(FRONT_OFF)
        while log_queue and log_file:
            log_file.write(log_queue.pop(0) + "\n")
        if log_file:
            log_file.flush()
            log_file.close()
        print("All lights OFF – side laser OFF – safe shutdown complete")


try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("\nStopped by user")