import os
import time
import rtc
import wifi
import adafruit_connection_manager
import adafruit_ntp

# WiFi credentials from settings.toml (create this on root if not already)
wifi_ssid = os.getenv("CIRCUITPY_WIFI_SSID")
wifi_password = os.getenv("CIRCUITPY_WIFI_PASSWORD")

if not wifi_ssid or not wifi_password:
    raise RuntimeError("Add WiFi credentials to settings.toml!")

# Connect to WiFi
wifi.radio.connect(wifi_ssid, wifi_password)
print("Connected to WiFi:", wifi_ssid)

# Socket pool via connection manager
pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)

# NTP (adjust tz_offset for your timezone, e.g., -8 for PST; cache to avoid frequent queries)
ntp = adafruit_ntp.NTP(pool, tz_offset=0, server="pool.ntp.org", cache_seconds=3600)

print("Before sync:", time.localtime())

# Sync the onboard RTC
the_rtc = rtc.RTC()
the_rtc.datetime = ntp.datetime

print("After sync:", time.localtime())

# Loop to display current time
while True:
    print(time.localtime())
    time.sleep(1)