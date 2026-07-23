import json
from collections import defaultdict
from datetime import timedelta

from django.conf import settings
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse

from .models import Alert, BandwidthSample, Device, Lab, SystemStatus, TimelineEvent, UsageSample, Whitelist


def dashboard(request):
    return render(request, "monitor/dashboard.html")


def _device_row(d):
    return {
        "id": d.id,
        "ip": d.ip or "",
        "mac": d.mac,
        "hostname": d.hostname or "",
        "student_name": d.student_name or "",
        "device_name": d.student_name or d.hostname or d.mac,
        "connection_type": d.connection_type,
        "connected_through": "Ethernet" if d.connection_type == Device.ETHERNET else f"Wi-Fi ({d.ap_label})" if d.ap_label else "Wi-Fi",
        "lab": d.lab.name if d.lab else "",
        "port": d.port if d.port is not None else "",
        "link_speed_mbps": d.link_speed_mbps,
        "upload_mbps": d.upload_mbps,
        "download_mbps": d.download_mbps,
        "status": d.status,
        "is_authorized": d.is_authorized,
        "is_blocked": d.is_blocked,
        "first_seen": d.first_seen.isoformat(),
        "last_seen": d.last_seen.isoformat(),
    }


def api_kpis(request):
    now = timezone.now()
    devices = Device.objects.all()
    labs = list(Lab.objects.all())
    lab_counts = {
        lab.name: devices.filter(lab=lab).exclude(status=Device.INACTIVE).count()
        for lab in labs
    }
    latest_bw = BandwidthSample.objects.order_by("-at").first()
    status = SystemStatus.current()

    return JsonResponse({
        "total_connected": devices.exclude(status=Device.INACTIVE).count(),
        "wifi_users": devices.filter(connection_type=Device.WIFI).exclude(status=Device.INACTIVE).count(),
        "ethernet_users": devices.filter(connection_type=Device.ETHERNET).exclude(status=Device.INACTIVE).count(),
        "lab_counts": lab_counts,
        "inactive_devices": devices.filter(status=Device.INACTIVE).count(),
        "new_devices_last_hour": Alert.objects.filter(kind=Alert.NEW, at__gte=now - timedelta(hours=1)).count(),
        "unauthorized_devices": devices.filter(is_authorized=False).count(),
        "internet_status": "ONLINE" if status.internet_online else "OFFLINE",
        "bandwidth_mbps": latest_bw.mbps if latest_bw else 0,
    })


def api_bandwidth(request):
    samples = list(BandwidthSample.objects.order_by("-at")[:48])[::-1]
    return JsonResponse({
        "samples": [{"at": s.at.isoformat(), "mbps": s.mbps} for s in samples]
    })


def api_labs(request):
    labs = Lab.objects.all()
    return JsonResponse({
        "labs": [
            {
                "id": lab.id,
                "name": lab.name,
                "room": lab.room,
                "switch_label": lab.switch_label,
                "count": lab.devices.exclude(status=Device.INACTIVE).count(),
            }
            for lab in labs
        ]
    })


def api_lab_devices(request, lab_id):
    rows = Device.objects.filter(lab_id=lab_id).order_by("port")
    return JsonResponse({"devices": [_device_row(d) for d in rows]})


def api_devices(request):
    rows = Device.objects.all()
    return JsonResponse({"devices": [_device_row(d) for d in rows]})


def api_wifi(request):
    rows = Device.objects.filter(connection_type=Device.WIFI)
    return JsonResponse({"devices": [_device_row(d) for d in rows]})


def api_ethernet(request):
    rows = Device.objects.filter(connection_type=Device.ETHERNET)
    return JsonResponse({"devices": [_device_row(d) for d in rows]})


def api_timeline(request):
    events = TimelineEvent.objects.all()[:50]
    return JsonResponse({
        "events": [
            {"at": e.at.isoformat(), "kind": e.kind, "text": e.text, "sub": e.sub}
            for e in events
        ]
    })


def api_alerts(request):
    new_alerts = Alert.objects.filter(kind=Alert.NEW, acknowledged=False).select_related("device")[:20]
    unauth_alerts = Alert.objects.filter(kind=Alert.UNAUTHORIZED, acknowledged=False).select_related("device")[:20]
    inactive = Device.objects.filter(status=Device.INACTIVE)[:30]

    def alert_row(a):
        return {
            "id": a.id,
            "mac": a.device.mac,
            "ip": a.device.ip or "",
            "lab": a.device.lab.name if a.device.lab else "",
            "through": "Ethernet" if a.device.connection_type == Device.ETHERNET else "Wi-Fi",
            "at": a.at.isoformat(),
            "is_blocked": a.device.is_blocked,
        }

    return JsonResponse({
        "new": [alert_row(a) for a in new_alerts],
        "unauthorized": [alert_row(a) for a in unauth_alerts],
        "inactive": [
            {"hostname": d.hostname or d.mac, "mac": d.mac, "lab": d.lab.name if d.lab else "Wi-Fi", "last_seen": d.last_seen.isoformat()}
            for d in inactive
        ],
    })


@csrf_exempt
def api_alert_ack(request, alert_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    Alert.objects.filter(id=alert_id).update(acknowledged=True)
    return JsonResponse({"ok": True})


@csrf_exempt
def api_device_block(request, device_id):
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    Device.objects.filter(id=device_id).update(is_blocked=True)
    Alert.objects.filter(device_id=device_id, kind=Alert.UNAUTHORIZED).update(acknowledged=True)
    # NOTE: this only marks the device as blocked in the dashboard's own
    # database. It does NOT shut the switch port down. Actually cutting a
    # device off needs a vendor CLI push (e.g. via netmiko: "interface
    # gi0/5" / "shutdown" on Cisco, or the MikroTik/RouterOS equivalent) —
    # intentionally left out of this project since it's destructive and
    # hardware-specific. Wire it in here once you're comfortable with it.
    return JsonResponse({"ok": True})


@csrf_exempt
def api_whitelist(request):
    if request.method == "GET":
        rows = Whitelist.objects.order_by("-added_at")
        return JsonResponse({
            "whitelist": [
                {"id": w.id, "mac": w.mac, "student_name": w.student_name, "note": w.note}
                for w in rows
            ]
        })
    if request.method == "POST":
        data = json.loads(request.body or "{}")
        mac = (data.get("mac") or "").strip().upper()
        if not mac:
            return JsonResponse({"error": "mac is required"}, status=400)
        Whitelist.objects.update_or_create(
            mac=mac, defaults={"student_name": data.get("student_name", ""), "note": data.get("note", "")}
        )
        return JsonResponse({"ok": True})
    return JsonResponse({"error": "GET or POST only"}, status=405)


def api_config(request):
    path = settings.NETWORK_CONFIG_PATH
    if not path.exists():
        return JsonResponse({"exists": False})
    with open(path) as f:
        return JsonResponse({"exists": True, "config": json.load(f)})


def api_analysis(request):
    """Everything on the Analysis page — all computed from real collector
    history (UsageSample, BandwidthSample, Alert, TimelineEvent, Device).
    Nothing here is mock data; with an empty database every number below
    is honestly 0 / empty, not a placeholder."""
    now = timezone.now()

    # --- usage over time + busiest lab over time -------------------------
    samples = list(UsageSample.objects.order_by("-at")[:200])[::-1]
    usage_series = [
        {
            "at": s.at.isoformat(),
            "wifi": s.wifi_count,
            "ethernet": s.ethernet_count,
            "total": s.wifi_count + s.ethernet_count,
            "labs": s.lab_counts,
        }
        for s in samples
    ]

    # --- peak usage hour (average total devices per hour-of-day) --------
    hour_totals = defaultdict(list)
    for s in UsageSample.objects.all():
        hour_totals[timezone.localtime(s.at).hour].append(s.wifi_count + s.ethernet_count)
    peak_hours = [
        {"hour": h, "avg_total": round(sum(v) / len(v), 1)}
        for h, v in sorted(hour_totals.items())
    ]
    busiest_hour = max(peak_hours, key=lambda x: x["avg_total"]) if peak_hours else None

    # --- top bandwidth consumers (peak-ever rate; Ethernet only — Wi-Fi --
    # --- devices have no per-device rate without an AP controller) ------
    ranked = sorted(
        Device.objects.filter(connection_type=Device.ETHERNET),
        key=lambda d: (d.peak_upload_mbps or 0) + (d.peak_download_mbps or 0),
        reverse=True,
    )
    top_devices = [
        {
            "mac": d.mac,
            "name": d.student_name or d.hostname or d.mac,
            "lab": d.lab.name if d.lab else "",
            "peak_upload_mbps": d.peak_upload_mbps,
            "peak_download_mbps": d.peak_download_mbps,
        }
        for d in ranked
        if (d.peak_upload_mbps or d.peak_download_mbps)
    ][:10]

    # --- bandwidth summary -------------------------------------------------
    bw_values = list(BandwidthSample.objects.order_by("-at")[:200].values_list("mbps", flat=True))
    bandwidth_stats = {
        "avg": round(sum(bw_values) / len(bw_values), 1) if bw_values else 0,
        "peak": round(max(bw_values), 1) if bw_values else 0,
        "current": bw_values[0] if bw_values else 0,
        "samples": len(bw_values),
    }

    # --- alert frequency trend, last 14 days ------------------------------
    cutoff = now - timedelta(days=14)
    day_counts = defaultdict(lambda: {"new": 0, "unauthorized": 0})
    for a in Alert.objects.filter(at__gte=cutoff):
        day = timezone.localtime(a.at).date().isoformat()
        day_counts[day][a.kind] += 1
    alert_trend = [{"date": d, **counts} for d, counts in sorted(day_counts.items())]

    # --- internet uptime %, over the observed window ----------------------
    events = list(TimelineEvent.objects.filter(text__icontains="internet").order_by("at"))
    if events:
        window_start = events[0].at
        offline_seconds = 0
        offline_start = None
        for e in events:
            if "OFFLINE" in e.text:
                offline_start = e.at
            elif "ONLINE" in e.text and offline_start:
                offline_seconds += (e.at - offline_start).total_seconds()
                offline_start = None
        if offline_start:
            offline_seconds += (now - offline_start).total_seconds()
        window_seconds = max((now - window_start).total_seconds(), 1)
        uptime_pct = round((1 - offline_seconds / window_seconds) * 100, 2)
    else:
        uptime_pct = 100.0

    # --- authorization summary --------------------------------------------
    active = Device.objects.exclude(status=Device.INACTIVE)
    authorization = {
        "authorized": active.filter(is_authorized=True).count(),
        "unauthorized": active.filter(is_authorized=False).count(),
    }

    return JsonResponse({
        "usage_series": usage_series,
        "peak_hours": peak_hours,
        "busiest_hour": busiest_hour,
        "top_devices": top_devices,
        "bandwidth_stats": bandwidth_stats,
        "alert_trend": alert_trend,
        "uptime_pct": uptime_pct,
        "authorization": authorization,
        "sample_count": len(samples),
    })
