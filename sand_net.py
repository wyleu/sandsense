# sand_net.py — hostname, mDNS, WiFi list, NTP from time_hosts
import wifi
import rtc

last_ssid = ""

_OK = "abcdefghijklmnopqrstuvwxyz0123456789-"

def hostname_from_cfg(cfg):
    raw = cfg.get_str("mdns.hostname", "") or cfg.device_name or "sandsense"
    raw = str(raw).lower()
    label = "".join(c if c in _OK else "-" for c in raw)
    while "--" in label:
        label = label.replace("--", "-")
    return label.strip("-") or "sandsense"

def set_hostname(cfg):
    name = hostname_from_cfg(cfg)
    try:
        wifi.radio.hostname = name
    except Exception as e:
        print("hostname set failed:", e)
    print("hostname:", getattr(wifi.radio, "hostname", name))
    return name

def start_mdns(hostname):
    try:
        import mdns
        srv = mdns.Server(wifi.radio)
        srv.hostname = hostname
        srv.advertise_service(service_type="_http", protocol="_tcp", port=80)
        print("mDNS: http://%s.local/status" % hostname)
        return srv
    except Exception as e:
        print("mDNS failed:", e)
        return None

def wifi_candidates(cfg):
    nets = cfg.raw.get("wifi", {}).get("networks") or []
    if nets:
        return sorted(nets, key=lambda n: n.get("priority", 99))
    ssid = cfg.get_str("wifi.ssid", "")
    pw = cfg.get_str("wifi.password", "")
    return [{"ssid": ssid, "password": pw}] if ssid else []

def connect_wifi(cfg):
    global last_ssid
    
    set_hostname(cfg)
    last = None
    for n in wifi_candidates(cfg):
        ssid, pw = n.get("ssid", ""), n.get("password", "")
        if not ssid:
            continue
        try:
            print("Trying WiFi:", ssid)
            wifi.radio.connect(ssid, pw)
            last_ssid = ssid
            print("WiFi:", ssid, wifi.radio.ipv4_address)
            return True
        except Exception as e:
            last = e
            print("Fail:", ssid, e)
    print("WiFi failed:", last)
    return False

def time_hosts(cfg):
    hosts = cfg.raw.get("time_hosts") or []
    out = []
    for h in hosts:
        s = str(h).strip()
        if not s or s.startswith("10.42."):
            continue
        out.append(s)
    return out

def sync_time(cfg, pool=None):
    import adafruit_ntp
    import socketpool
    if pool is None or not hasattr(pool, "getaddrinfo"):
        pool = socketpool.SocketPool(wifi.radio)
    last = None
    for host in time_hosts(cfg):
        if host.startswith("10.42."):
            continue
        try:
            print("NTP try:", host)
            ntp = adafruit_ntp.NTP(
                pool, tz_offset=0, server=host, cache_seconds=3600
            )
            rtc.RTC().datetime = ntp.datetime
            print("RTC from", host)
            return True
        except Exception as e:
            last = e
            print("NTP fail:", host, e)
    print("NTP failed:", last)
    return False