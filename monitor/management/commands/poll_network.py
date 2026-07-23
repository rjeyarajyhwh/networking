"""
poll_network — the real-world data collector.

Run it once:            python manage.py poll_network
Run it forever (30s):    python manage.py poll_network --loop --interval 30

What it does, per poll:
  1. Reads network_config.json (switch IPs, router IP, SNMP community
     strings — edit that file, not this one, to point at your hardware).
  2. For each lab switch: walks the bridge forwarding table (which MAC is
     on which port) and the interface table (link speed, up/down,
     in/out byte counters) via SNMP. Every MAC found this way is an
     "Ethernet" device.
  3. For the router: walks its ARP table (IP <-> MAC). Any MAC seen here
     that was NOT already found on a lab switch is classified as
     "Wi-Fi" (it reached the router without passing through a managed
     lab switch — on a small college network that almost always means
     it's on Wi-Fi). Also pulls the WAN interface's byte counters for
     the bandwidth chart, and pings an external host to decide
     Internet ONLINE/OFFLINE.
  4. Upserts everything into the Device table, raises Alerts for brand
     new MACs and for MACs not in the Whitelist table (only once you've
     added at least one whitelist entry — see README), and marks
     devices Idle/Inactive if they stop showing up.

This only works against MANAGED switches/routers with SNMP turned on.
See README.md for the 2-minute SNMP setup on your hardware.

Design note: all SNMP I/O happens inside one asyncio.run() call that
returns plain data (no Django ORM calls from inside async code — Django
forbids that unless you go through sync_to_async, and there's no need to
here). Every database write happens afterwards, back in ordinary
synchronous Django code.
"""
import asyncio
import json
import socket
import time
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from monitor.models import Alert, BandwidthSample, Device, Lab, SystemStatus, TimelineEvent, UsageSample, Whitelist

try:
    import ping3
except ImportError:
    ping3 = None

from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    get_cmd,
    walk_cmd,
)

OID_DOT1D_FDB_PORT = "1.3.6.1.2.1.17.4.3.1.2"           # dot1dTpFdbPort: mac -> bridge port
OID_DOT1D_BASE_PORT_IFINDEX = "1.3.6.1.2.1.17.1.4.1.2"  # bridge port -> ifIndex
OID_IF_OPER_STATUS = "1.3.6.1.2.1.2.2.1.8"
OID_IF_SPEED = "1.3.6.1.2.1.2.2.1.5"
OID_IF_IN_OCTETS = "1.3.6.1.2.1.2.2.1.10"
OID_IF_OUT_OCTETS = "1.3.6.1.2.1.2.2.1.16"
OID_ARP_PHYS_ADDRESS = "1.3.6.1.2.1.4.22.1.2"           # ipNetToMediaPhysAddress: (ifIndex.ip) -> mac
SNMP_TIMEOUT = 2
SNMP_RETRIES = 1


def mac_from_bytes(raw_bytes):
    return ":".join(f"{b:02X}" for b in raw_bytes)


# --------------------------------------------------------------------------
# Async collection layer. Pure SNMP I/O only — no Django ORM calls allowed
# in anything below, since it all runs inside asyncio.run().
# --------------------------------------------------------------------------
async def snmp_walk(engine, ip, community, oid):
    target = await UdpTransportTarget.create((ip, 161), timeout=SNMP_TIMEOUT, retries=SNMP_RETRIES)
    rows = []
    async for error_indication, error_status, error_index, var_binds in walk_cmd(
        engine, CommunityData(community, mpModel=1), target, ContextData(),
        ObjectType(ObjectIdentity(oid)), lexicographicMode=False,
    ):
        if error_indication or error_status:
            break
        for name, value in var_binds:
            rows.append((tuple(name.getOid()), value))
    return rows


async def snmp_get(engine, ip, community, oid):
    target = await UdpTransportTarget.create((ip, 161), timeout=SNMP_TIMEOUT, retries=SNMP_RETRIES)
    error_indication, error_status, error_index, var_binds = await get_cmd(
        engine, CommunityData(community, mpModel=1), target, ContextData(),
        ObjectType(ObjectIdentity(oid)),
    )
    if error_indication or error_status or not var_binds:
        return None
    return var_binds[0][1]


async def walk_if_table(engine, ip, community):
    """One dict, keyed by ifIndex, with oper_status/speed_bps/in_octets/out_octets."""
    table = {}

    async def merge(oid, key, cast=int):
        for oid_tuple, value in await snmp_walk(engine, ip, community, oid):
            ifindex = oid_tuple[-1]
            table.setdefault(ifindex, {})[key] = cast(value)

    await merge(OID_IF_OPER_STATUS, "oper_status")
    await merge(OID_IF_SPEED, "speed_bps")
    await merge(OID_IF_IN_OCTETS, "in_octets")
    await merge(OID_IF_OUT_OCTETS, "out_octets")
    return table


async def collect_switch(engine, ip, community):
    fdb = await snmp_walk(engine, ip, community, OID_DOT1D_FDB_PORT)
    bridge_to_ifindex = {
        oid_tuple[-1]: int(value)
        for oid_tuple, value in await snmp_walk(engine, ip, community, OID_DOT1D_BASE_PORT_IFINDEX)
    }
    if_table = await walk_if_table(engine, ip, community)
    return {"fdb": fdb, "bridge_to_ifindex": bridge_to_ifindex, "if_table": if_table}


async def collect_router(engine, ip, community, wan_ifindex):
    arp_rows = await snmp_walk(engine, ip, community, OID_ARP_PHYS_ADDRESS)
    if_table = await walk_if_table(engine, ip, community) if wan_ifindex is not None else {}
    return {"arp_rows": arp_rows, "if_table": if_table}


async def collect_all(config):
    """Polls every switch and the router concurrently — one slow/unreachable
    device only costs its own timeout, not everyone else's turn waiting
    behind it in a queue."""
    engine = SnmpEngine()
    result = {"switches": {}, "router": None}
    try:
        switch_cfgs = config.get("switches", [])
        router_cfg = config.get("router")

        tasks = [collect_switch(engine, sw["ip"], sw.get("community", "public")) for sw in switch_cfgs]
        if router_cfg:
            tasks.append(collect_router(
                engine, router_cfg["ip"], router_cfg.get("community", "public"), router_cfg.get("wan_ifindex")
            ))

        results = await asyncio.gather(*tasks)

        for switch_cfg, res in zip(switch_cfgs, results):
            result["switches"][switch_cfg["lab"]] = res
        if router_cfg:
            result["router"] = results[-1]
    finally:
        try:
            engine.close_dispatcher()
        except Exception:
            pass
    return result


def reverse_dns(ip):
    try:
        socket.setdefaulttimeout(0.4)
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def rate_mbps(prev_octets, cur_octets, prev_at, now):
    if prev_octets is None or prev_at is None or cur_octets is None or cur_octets < prev_octets:
        return None
    elapsed = (now - prev_at).total_seconds()
    if elapsed <= 0:
        return None
    return round((cur_octets - prev_octets) * 8 / elapsed / 1_000_000, 2)


class Command(BaseCommand):
    help = "Poll real switches/router via SNMP and populate the dashboard's database."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Poll repeatedly instead of once.")
        parser.add_argument("--interval", type=int, default=30, help="Seconds between polls when --loop is set.")

    def handle(self, *args, **options):
        if options["loop"]:
            self.stdout.write(self.style.SUCCESS(f"Polling every {options['interval']}s. Ctrl+C to stop."))
            while True:
                self.run_once()
                time.sleep(options["interval"])
        else:
            self.run_once()

    def run_once(self):
        config = self.load_config()
        now = timezone.now()

        # ---- 1. all network I/O happens here, isolated inside asyncio ----
        raw = asyncio.run(collect_all(config))

        # ---- 2. everything below is plain, synchronous Django ORM code ----
        seen_macs = set()
        for switch_cfg in config.get("switches", []):
            data = raw["switches"].get(switch_cfg["lab"])
            seen_macs |= self.apply_switch(switch_cfg, data, now)

        self.apply_router(config.get("router", {}), raw["router"], seen_macs, now)
        self.check_internet(config.get("internet_check_host", "8.8.8.8"))
        self.age_out_devices(seen_macs, config.get("inactive_after_minutes", 10), now)
        self.record_usage_sample()
        self.stdout.write(self.style.SUCCESS(f"[{now:%H:%M:%S}] poll complete — {len(seen_macs)} devices seen"))

    def load_config(self):
        path = settings.NETWORK_CONFIG_PATH
        if not path.exists():
            self.stderr.write(self.style.ERROR(
                f"{path} not found. Copy network_config.example.json to network_config.json and edit it."
            ))
            return {}
        with open(path) as f:
            return json.load(f)

    # ---------------------------------------------------------------- switches
    def apply_switch(self, switch_cfg, data, now):
        name = switch_cfg["lab"]
        ip = switch_cfg["ip"]

        lab, _ = Lab.objects.update_or_create(
            name=name,
            defaults={
                "room": switch_cfg.get("room", ""),
                "switch_ip": ip,
                "switch_label": switch_cfg.get("label", ""),
                "order": switch_cfg.get("order", 0),
            },
        )

        if not data or not data["fdb"]:
            self.stderr.write(self.style.WARNING(f"{name}: no SNMP response from {ip} — skipped this poll."))
            return set()

        bridge_to_ifindex = data["bridge_to_ifindex"]
        if_table = data["if_table"]

        seen = set()
        for oid_tuple, value in data["fdb"]:
            mac = mac_from_bytes(oid_tuple[-6:])
            bridge_port = int(value)
            ifindex = bridge_to_ifindex.get(bridge_port, bridge_port)
            ifrow = if_table.get(ifindex, {})

            device, created = Device.objects.get_or_create(mac=mac, defaults={"connection_type": Device.ETHERNET})
            device.connection_type = Device.ETHERNET
            device.lab = lab
            device.port = bridge_port
            device.link_speed_mbps = (ifrow.get("speed_bps") or 0) / 1_000_000 or None
            device.upload_mbps = rate_mbps(device.prev_out_octets, ifrow.get("out_octets"), device.prev_sample_at, now)
            device.download_mbps = rate_mbps(device.prev_in_octets, ifrow.get("in_octets"), device.prev_sample_at, now)
            device.prev_in_octets = ifrow.get("in_octets")
            device.prev_out_octets = ifrow.get("out_octets")
            device.prev_sample_at = now
            device.status = Device.ONLINE if ifrow.get("oper_status") != 2 else Device.IDLE
            if device.upload_mbps is not None:
                device.peak_upload_mbps = max(device.peak_upload_mbps or 0, device.upload_mbps)
            if device.download_mbps is not None:
                device.peak_download_mbps = max(device.peak_download_mbps or 0, device.download_mbps)
            device.save()

            self.refresh_authorization(device, created)
            seen.add(mac)
        return seen

    # ------------------------------------------------------------------ router
    def apply_router(self, router_cfg, data, ethernet_macs, now):
        if not router_cfg or not data:
            return
        ip = router_cfg["ip"]

        if not data["arp_rows"]:
            self.stderr.write(self.style.WARNING(f"Router: no SNMP response from {ip} — Wi-Fi list not updated."))

        for oid_tuple, value in data["arp_rows"]:
            ip_addr = ".".join(str(b) for b in oid_tuple[-4:])
            mac = mac_from_bytes(value.asOctets()) if hasattr(value, "asOctets") else None
            if not mac or mac in ethernet_macs:
                continue  # already accounted for on a lab switch
            device, created = Device.objects.get_or_create(mac=mac, defaults={"connection_type": Device.WIFI})
            device.connection_type = Device.WIFI
            device.ip = ip_addr
            device.lab = None
            device.port = None
            if not device.hostname:
                device.hostname = reverse_dns(ip_addr)
            device.status = Device.ONLINE
            device.save()
            self.refresh_authorization(device, created)
            ethernet_macs.add(mac)  # reuse the set as "seen this poll" for both types

        wan_ifindex = router_cfg.get("wan_ifindex")
        if wan_ifindex is not None:
            row = data["if_table"].get(int(wan_ifindex), {})
            last = BandwidthSample.objects.order_by("-at").first()
            in_o, out_o = row.get("in_octets"), row.get("out_octets")
            mbps = 0.0
            if last and last.in_octets is not None and in_o is not None:
                elapsed = (now - last.at).total_seconds() or 1
                total_delta = max(0, (in_o - last.in_octets)) + max(0, (out_o - last.out_octets))
                mbps = round(total_delta * 8 / elapsed / 1_000_000, 1)
            BandwidthSample.objects.create(mbps=mbps, in_octets=in_o, out_octets=out_o)

    # -------------------------------------------------------------- housekeeping
    def refresh_authorization(self, device, created):
        has_whitelist = Whitelist.objects.exists()
        entry = Whitelist.objects.filter(mac=device.mac).first()
        device.is_authorized = (not has_whitelist) or entry is not None
        if entry and entry.student_name and not device.student_name:
            device.student_name = entry.student_name
        device.save(update_fields=["is_authorized", "student_name"])

        if created:
            TimelineEvent.objects.create(
                kind=TimelineEvent.GOOD,
                text="New device connected",
                sub=f"{device.get_connection_type_display()} · {device.mac}",
            )
            Alert.objects.create(kind=Alert.NEW, device=device)

        if has_whitelist and not device.is_authorized:
            already_alerted = Alert.objects.filter(kind=Alert.UNAUTHORIZED, device=device, acknowledged=False).exists()
            if not already_alerted:
                TimelineEvent.objects.create(
                    kind=TimelineEvent.CRIT,
                    text="Unauthorized device detected",
                    sub=f"MAC {device.mac} not in whitelist",
                )
                Alert.objects.create(kind=Alert.UNAUTHORIZED, device=device)

    def check_internet(self, host):
        online = True
        if ping3 is not None:
            try:
                online = ping3.ping(host, timeout=2) is not None
            except Exception:
                online = False
        status = SystemStatus.current()
        if status.internet_online != online:
            TimelineEvent.objects.create(
                kind=TimelineEvent.GOOD if online else TimelineEvent.CRIT,
                text="Internet back ONLINE" if online else "Internet went OFFLINE",
                sub=f"Reachability check against {host}",
            )
        status.internet_online = online
        status.save()

    def age_out_devices(self, seen_macs, inactive_after_minutes, now):
        Device.objects.exclude(mac__in=seen_macs).filter(status=Device.ONLINE).update(status=Device.IDLE)
        cutoff = now - timedelta(minutes=inactive_after_minutes)
        Device.objects.exclude(mac__in=seen_macs).filter(last_seen__lt=cutoff).exclude(
            status=Device.INACTIVE
        ).update(status=Device.INACTIVE)

    def record_usage_sample(self):
        active = Device.objects.exclude(status=Device.INACTIVE)
        lab_counts = {
            lab.name: active.filter(lab=lab).count() for lab in Lab.objects.all()
        }
        UsageSample.objects.create(
            wifi_count=active.filter(connection_type=Device.WIFI).count(),
            ethernet_count=active.filter(connection_type=Device.ETHERNET).count(),
            lab_counts=lab_counts,
        )
