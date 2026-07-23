# NetWatch NOC — College Network Dashboard

A working Django project: a Python collector polls your real switches/router
over SNMP, stores what it finds in a database, and a live dashboard displays
it. No mock data — once the collector runs, everything on screen is real.

```
Managed switches/router (SNMP) → poll_network.py → SQLite/PostgreSQL → Django views → dashboard.html (polls every 15s)
```

## 0. Requirements

- Python 3.10+
- A PC that can reach your switches'/router's management IPs (run this on a
  machine physically on the college LAN — a lab PC or a small server is fine)
- Managed lab switches with SNMP enabled (you confirmed yours are managed —
  good, this whole pipeline applies directly)
- Your router/firewall's SNMP or admin IP (brand not required by the code —
  standard SNMP OIDs work across vendors — but note this below if it turns
  out your router has SNMP disabled/unavailable)

## 1. Install

First, make sure you're actually standing inside the `netwatch` folder (the
one with `manage.py` in it) — check with `Get-Location`, not by guessing:

```powershell
Get-Location                          # confirm where you are first
cd C:\Users\rjeya\Downloads\network\netwatch    # absolute path — safe no matter where you started
python -m venv venv
venv\Scripts\activate
python -m pip install -r requirements.txt
```

Use `python -m pip install ...`, **not** bare `pip install ...`. On some
Windows setups (college-managed PCs especially) an Application Control
policy blocks freshly-created, unsigned `.exe` stubs like venv's own
`pip.exe` — you'll see an error like *"An Application Control policy has
blocked this file."* Routing through `python -m pip` runs pip as a module
inside the already-trusted `python.exe` instead of launching that separate
blocked binary, which avoids the problem entirely. Use `python -m` in front
of every future pip command too (e.g. `python -m pip install <package>`).

## 2. Set up the database

```powershell
python manage.py migrate
python manage.py createsuperuser   # optional — gives you /admin/ access
```

This creates `db.sqlite3` in this folder. That's the whole database step —
no separate install needed. (To use PostgreSQL instead, see the comment
block in `netwatch/settings.py`.)

## 3. Enable SNMP on your hardware

On each lab switch's web admin page: find SNMP settings → enable **SNMP
v2c** → set a community string (don't leave it as `public` — pick something
like `netwatch_ro2026`) → if there's an "allowed hosts" field, restrict it to
the IP of the PC running this collector.

Do the same on the router if it supports SNMP. If it doesn't, the switch
side (Ethernet devices, per-lab detail) still works fully — you'll just be
missing Wi-Fi device detection and the router bandwidth chart until you find
another way to reach the router's ARP table.

## 4. Point the collector at your hardware

```powershell
copy network_config.example.json network_config.json
notepad network_config.json
```

Fill in the real IPs and community strings for your router and your three
lab switches. This file is the single source of truth — the collector reads
it every time it runs, and the Settings page in the dashboard shows its
contents (read-only) so you can double check without opening a text editor.

## 5. Test the collector once

```powershell
python manage.py poll_network
```

You should see something like:
```
[11:42:03] poll complete — 34 devices seen
```

If you see `no SNMP response from ...`, double-check: the community string
matches, the IP is right, and nothing (Windows Firewall, an ACL on the
switch) is blocking UDP port 161 between this PC and that device.

## 6. Run the dashboard

```powershell
python manage.py runserver 0.0.0.0:8000
```

Open `http://localhost:8000/` on this PC, or `http://<this-PC's-LAN-IP>:8000/`
from any other device on the same network (e.g. the screen in Sir's office).

Right after step 5 the dashboard will already show real numbers. If you
skipped step 5, every page shows a friendly "no data yet" message instead of
breaking — run the collector and refresh.

## 7. Keep it live — run the collector continuously

Two ways:

**A. Simplest — a loop in its own terminal window, left running:**
```powershell
python manage.py poll_network --loop --interval 30
```

**B. Production-ish — Windows Task Scheduler:**
Create a task that runs every minute:
```
Program:  C:\path\to\netwatch\venv\Scripts\python.exe
Arguments: manage.py poll_network
Start in:  C:\path\to\netwatch
```

Either way, leave `python manage.py runserver` running in a second window
(or deploy it properly later with `waitress`/IIS — out of scope for the demo).

## What's real vs. what still needs wiring

| Feature | Status |
|---|---|
| Ethernet device list, per-port speed/traffic | **Real** — SNMP off your managed lab switches |
| Wi-Fi device list | **Real, with a caveat** — classified as "any router-ARP MAC not seen on a lab switch." Works well on a simple network; if you later add a proper Wi-Fi controller, Option 3 on the Architecture page shows where to plug it in |
| Bandwidth chart | **Real** — router WAN interface counters, needs `wan_ifindex` set correctly in `network_config.json` |
| Internet ONLINE/OFFLINE | **Real** — pings `8.8.8.8` (or whatever you set) every poll |
| New device / Unauthorized device alerts | **Real** — driven by the Whitelist table; add entries on the Settings page |
| Student names | **Manual** — there's no RADIUS/AD/DHCP-log integration here, so a name only appears once you add that MAC to the Whitelist yourself (Settings page). This is the identity-mapping limitation explained on the Architecture page |
| Signal strength / AP name for Wi-Fi | **Not implemented** — needs your Wi-Fi controller/AP's own API (Architecture page, Option 3). Tell me the AP/controller brand once you know it and this is a small addition |
| "Block device" button | Marks the device blocked *in this dashboard's database only* — it does not shut down the actual switch port. Real port shutdown needs a vendor CLI push (e.g. `netmiko` sending `interface gi0/5` / `shutdown` on Cisco). Deliberately left out since it's destructive; see the comment in `monitor/views.py::api_device_block` for where to add it |
| **Analysis page** | **Real** — internet uptime %, avg/peak bandwidth, busiest hour of day, busiest lab over time, top bandwidth-consuming devices (peak rate ever seen), 14-day alert frequency trend. All computed from `UsageSample`/`BandwidthSample`/`Alert`/`TimelineEvent` history — needs a few hours of the collector running (or `--loop`) to have enough data to chart |
| AI predict/detect features | Listed on the Tech Stack page as the natural next step beyond the Analysis page — not built. The same history tables would feed it |

## Project layout

```
netwatch/
  manage.py
  netwatch/settings.py, urls.py, wsgi.py      — Django project config
  monitor/
    models.py                                  — Lab, Device, Whitelist, Alert, TimelineEvent, BandwidthSample, SystemStatus
    views.py                                    — JSON API the dashboard calls
    urls.py
    admin.py                                    — browse/edit everything at /admin/
    management/commands/poll_network.py         — the SNMP collector (read this first)
    templates/monitor/dashboard.html            — the whole frontend, one file
  network_config.example.json                   — copy to network_config.json and edit
  requirements.txt
```

## Troubleshooting

- **"can't open file ...manage.py"** — you're not standing in the `netwatch` folder. Run `Get-Location` to check, then `cd C:\Users\rjeya\Downloads\network\netwatch` (adjust if your username/path differs).
- **"An Application Control policy has blocked this file" (on `pip.exe`)** — don't call `pip` directly; use `python -m pip install ...` instead (see step 1). This affects any pip command, not just the first install.
- **"no SNMP response"** — wrong community string, wrong IP, or UDP/161 blocked. Test with a one-off Python `getCmd` for `sysName` (`1.3.6.1.2.1.1.5.0`) against that IP first.
- **Wi-Fi list is empty but Ethernet works** — router SNMP likely isn't enabled, or `wan_ifindex`/router IP is wrong in `network_config.json`.
- **Everything says "no data yet"** — the collector hasn't run yet, or hasn't run successfully. Run `python manage.py poll_network` in the foreground and read its output.
- **Dashboard shows old numbers** — it polls the API every 15s; a hard refresh (Ctrl+F5) also works.
