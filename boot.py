# boot.py – Sand* farm (single settings.json)
import sys
import supervisor
import storage

def _usb_from_settings():
    manufacturer, product = "SandFarm", "SandFarm device"
    try:
        import json
        with open("settings.json", "r") as f:
            dev = (json.load(f).get("device") or {})
        name = dev.get("name") or "device"
        manufacturer = dev.get("usb_manufacturer") or "SandFarm"
        product = dev.get("usb_product") or ("SandFarm %s" % name)
    except Exception as e:
        print("boot.py: settings.json fallback:", e)
    return manufacturer, product

manufacturer, product = _usb_from_settings()
try:
    supervisor.set_usb_identification(
        manufacturer=manufacturer,
        product=product,
        vid=0x239A,
        pid=0x80C1,
    )
    print("USB identity:", manufacturer, "|", product)
except Exception as e:
    print("boot.py: USB id failed:", e)

# Field: comment out on the bench while editing files
storage.disable_usb_drive()

if sys.implementation.name == "circuitpython":
    try:
        import usb_cdc
        import usb_hid
        import usb_midi
        usb_cdc.enable(console=True, data=True)
        usb_hid.enable((usb_hid.Device.KEYBOARD,))
        usb_midi.enable()
        print("boot.py: drive off, CDC+HID+MIDI on")
    except Exception as e:
        print("boot.py: USB functions failed:", e)