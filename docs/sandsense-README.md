# sandsense

Tower-clock **escapement** monitor on a Raspberry Pi Pico 2 W (CircuitPython).

GitHub: https://github.com/wyleu/sandsense  
Sister device: [sandswing](https://github.com/wyleu/sandswing) (optical bell lineup / scan).

The README that shipped with this repo described an 8-channel IR bell detector. That is **sandbells / old sandswing scan**, not this machine. This file matches `sandsense.py` as it exists on `main`.

## What this board does

- Reads the escapement optically via **ADS1115** A0 (I2C).
- Flashes a **front laser** from an I2C encoder board (48 ticks/minute nominal).
- Optional software PLL synthesises ticks if a beat is missed.
- Emits USB MIDI note-on/off for each tick (when a USB host is present).
- Serves **HTTP `/status`** (`sand.status/v1` JSON) and a tick **WebSocket** `/ws`.
- Optional DS18B20 temperature on GP13.
- Encoder menu: enable bells, test bells, laser test, escapement test.
- Eight digital inputs kept for compatibility (not the main clock path).

## What it does not do

- Does not line up sandswing laser heads on GP7–10.
- Does not run the 8-bell IR latch scanner as its primary job (`startup.program` is `sandsense.py`).
- Does not use CIRCUITPY as the master copy of the project.

## Shared farm pieces (same idea as sandswing)

| File | Role |
|---|---|
| `code.py` | Thin launcher. Reads `settings.json`, `exec`s `startup.program`. Intended to be the same idea on every Sand* board. |
| `config_loader.py` | Sole `settings.json` reader. |
| `sand_status.py` | Builds `sand.status/v1` documents (`from_sandsense(...)`). |
| `sand_net.py` | Wi-Fi list, hostname, mDNS, NTP from `time_hosts`. |
| `settings.json` | Device identity, startup program, escapement timing, outputs. |

Sandswing currently talks status through `farm_ws` / `farm_log`. Unification means both devices emit **`sand.status/v1`** and use the same launcher + config loader. Hardware apps stay separate (`sandsense.py` vs `sandswing.py`).

## Hardware (from `sandsense.py`)

| Function | Where |
|---|---|
| I2C | GP4 SDA, GP5 SCL |
| ADS1115 | 0x4A, A0 sense, A1 spare |
| I2C encoder + laser PWM | 0x47; front laser GP2, side GP1 on the encoder |
| DS18B20 | GP13 (optional) |
| Status LED | GP15 |
| Bell inputs (legacy) | GP22, 21, 20, 19, 18, 17, 16, 14 |

Escapement timing lives under `settings.json` → `"escapement"` (`ticks_per_minute`, `hysteresis`, `pll_enabled`, laser flash interval, MIDI note).

## Boot

1. `boot.py` (USB / pause if present)
2. `code.py` loads config, prints banner, runs `startup.program`
3. Default program: **`sandsense.py`**

`device.usb_drive` in settings is `false` on the published config — that is deliberate so the tower unit is less likely to expose a writable CIRCUITPY.

## How you develop (one way: host → Pico)

Master files live on the Pi / GitHub, **not** on the Pico.

```text
sandsense/
  src/                 # only files that go on the Pico
    boot.py
    code.py
    sandsense.py
    config_loader.py
    sand_status.py
    sand_net.py
    settings.json
    lib/
  tools/deploy.sh      # mpremote serial copy + reset
  archive/             # old programs, logs — not deployed
```

```bash
./tools/deploy.sh
```

Never copy from CIRCUITPY back to the host. Never use Thonny/VSC Save onto the volume.

CircuitPython on this device in the field: **10.0.3 Pico 2 W** unless you consciously change it. `lib/` must be the 10.x bundle.

## Status document

`GET /status` returns `sand.status/v1` with sections network / sense / hardware (IP, PLL, ticks, TPM, ADC avg, encoder LED colour, temperature). Farm UI already consumes this schema. Sandswing needs the same envelope for unification (see sandswing README).
