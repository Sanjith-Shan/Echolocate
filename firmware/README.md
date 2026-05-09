# Echolocate ESP32-S3 Firmware

Custom CSI receiver firmware. Connects to a WiFi AP (your phone hotspot), captures
WiFi Channel State Information from every received packet, streams CSV over USB
serial **and** runs an HTTP test server on the ESP's IP so you can verify the
firmware is alive without needing the Python backend.

## Wire format compatibility

Each CSI sample is printed to UART as one line in the same CSV layout as
[`espressif/esp-csi`](https://github.com/espressif/esp-csi)'s `csi_recv_router`
example. The Python parser in `backend/csi_detector.py` is therefore
drop-in compatible whether you flash this firmware or the upstream example.

## Why the HTTP server?

To make the WiFi side of the device independently testable.

After flashing, the boot log prints something like:

```
I (5273) echolocate: ===========================================
I (5273) echolocate:  WiFi connected. IP: 172.20.10.5
I (5273) echolocate:  Test the firmware:
I (5273) echolocate:    curl http://172.20.10.5/health
I (5273) echolocate:    open http://172.20.10.5/  (or echolocate.local)
I (5273) echolocate: ===========================================
```

| Endpoint        | Returns                                                              |
|-----------------|----------------------------------------------------------------------|
| `GET /`         | HTML status page (auto-refreshing)                                   |
| `GET /health`   | `{ok, firmware, chip, ssid, ip, rssi, uptime_s, free_heap, packets, ping_replies}` |
| `GET /stats`    | `{samples, subcarriers_per_sample, rolling_mean_amplitude, ...}`     |
| `GET /csi/latest` | `{sample_us, rssi, n, amplitudes:[...]}`                           |

`ping_replies` counts how many ICMP echo replies the gateway has answered.
If this number is climbing, your AP is responsive and CSI is being driven
reliably. If `packets_received` grows but `ping_replies` is stuck at 0,
your AP is filtering ICMP — uncommon but possible on enterprise networks.

mDNS advertises `echolocate.local` so you can also try `curl http://echolocate.local/health`.

## Build & flash

You need ESP-IDF 5.1+ installed. From this directory:

```bash
# 1. Set the target
idf.py set-target esp32s3

# 2. Configure your hotspot SSID/password
idf.py menuconfig
#    → "Echolocate Configuration"
#    → WiFi SSID / WiFi Password

# 3. Build
idf.py build

# 4. Flash & monitor (Mac — adjust port for Linux/Windows)
idf.py -p /dev/cu.usbmodem14101 flash monitor
```

## Phone hotspot tips

- iPhone: Settings → Personal Hotspot → **Maximize Compatibility = ON** (forces 2.4 GHz).
- Android: hotspot Band → 2.4 GHz.
- Keep the phone plugged in.
- SSID should be plain ASCII, no emoji, no spaces (the Kconfig string supports them
  but some IDF versions choke on quotes).

## Testing the WiFi side without the Python backend

Once the boot banner prints the IP, on any laptop/phone joined to the same hotspot:

```bash
# Quick health check
curl http://<ip>/health
# {"ok":true,"firmware":"echolocate-csi-1.0",...,"packets_received":1247}

# Live rolling stats — values change as you move people around the room
curl http://<ip>/stats

# Latest CSI sample (subcarrier amplitudes)
curl http://<ip>/csi/latest | jq

# Or just open in a browser:
open http://<ip>/
```

If `packets_received` is going up and `rssi` is reasonable (≥ -75 dBm),
the firmware is healthy. If it stays at zero, see *Troubleshooting* below.

## Troubleshooting

| Symptom                          | Likely cause                                    | Fix                                              |
|----------------------------------|-------------------------------------------------|--------------------------------------------------|
| No serial output at all          | Wrong baud rate                                 | Confirm 921600. Or run `idf.py monitor`.         |
| `WiFi failed to connect within 30s` | Hotspot is 5 GHz only                       | Enable "Maximize Compatibility" on iPhone.       |
| Connects but `packets_received=0` | CSI not enabled in sdkconfig                   | `idf.py menuconfig` → check `CONFIG_ESP_WIFI_CSI_ENABLED`. |
| `packets_received` rising but `ping_replies=0` | AP filters ICMP                       | Try a different hotspot, or rely on beacon-driven CSI (slower). |
| `packets_received` rising but variance always near zero | Empty room, baseline | Walk through the area; variance should jump.     |
| HTTP server unreachable from laptop | Phone hotspot has client isolation on        | iPhone hotspots usually don't isolate; on Android, check.|

## Two-ESP32 fallback (if phone hotspot is unreliable)

This firmware is the *receiver*. If you want the self-contained two-ESP32 mode
(one as AP, one as STA), use the upstream esp-csi `csi_send` example on the
sender board and this firmware (or the upstream `csi_recv`) on the receiver.

## File layout

```
firmware/
├── CMakeLists.txt
├── sdkconfig.defaults       — preset config (SPIRAM, baud, CSI on)
├── README.md                — this file
└── main/
    ├── CMakeLists.txt
    ├── Kconfig.projbuild    — adds "Echolocate Configuration" menu
    ├── idf_component.yml    — pulls in espressif/mdns
    └── csi_recv_router_http.c   — the firmware
```
