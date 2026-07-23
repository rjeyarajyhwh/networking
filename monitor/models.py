from django.db import models


class Lab(models.Model):
    """One row per lab switch. The collector creates/updates these
    automatically from network_config.json — you don't need to add
    them by hand."""

    name = models.CharField(max_length=50, unique=True)
    room = models.CharField(max_length=100, blank=True)
    switch_ip = models.GenericIPAddressField()
    switch_label = models.CharField(max_length=100, blank=True)
    order = models.IntegerField(default=0)

    class Meta:
        ordering = ["order", "name"]

    def __str__(self):
        return self.name


class Whitelist(models.Model):
    """MAC addresses that are allowed on the network. Leave this table
    empty until you're ready to start flagging unauthorized devices —
    with zero rows, nothing is flagged (see Device.refresh_authorization)."""

    mac = models.CharField(max_length=17, unique=True)
    student_name = models.CharField(max_length=100, blank=True)
    note = models.CharField(max_length=200, blank=True)
    added_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.mac} ({self.student_name})" if self.student_name else self.mac


class Device(models.Model):
    ETHERNET = "ethernet"
    WIFI = "wifi"
    CONNECTION_CHOICES = [(ETHERNET, "Ethernet"), (WIFI, "Wi-Fi")]

    ONLINE = "Online"
    IDLE = "Idle"
    INACTIVE = "Inactive"
    STATUS_CHOICES = [(ONLINE, "Online"), (IDLE, "Idle"), (INACTIVE, "Inactive")]

    mac = models.CharField(max_length=17, unique=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    hostname = models.CharField(max_length=150, blank=True)
    student_name = models.CharField(max_length=100, blank=True)

    connection_type = models.CharField(max_length=10, choices=CONNECTION_CHOICES)
    lab = models.ForeignKey(Lab, null=True, blank=True, on_delete=models.SET_NULL, related_name="devices")
    port = models.IntegerField(null=True, blank=True)
    ap_label = models.CharField(max_length=100, blank=True)  # only meaningful once an AP/controller is wired in

    link_speed_mbps = models.FloatField(null=True, blank=True)
    upload_mbps = models.FloatField(null=True, blank=True)
    download_mbps = models.FloatField(null=True, blank=True)
    peak_upload_mbps = models.FloatField(null=True, blank=True)
    peak_download_mbps = models.FloatField(null=True, blank=True)

    # raw counters kept between polls so we can compute a rate; not shown in the UI
    prev_in_octets = models.BigIntegerField(null=True, blank=True)
    prev_out_octets = models.BigIntegerField(null=True, blank=True)
    prev_sample_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=ONLINE)
    is_authorized = models.BooleanField(default=True)
    is_blocked = models.BooleanField(default=False)

    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-last_seen"]

    def __str__(self):
        return f"{self.mac} ({self.ip or 'no ip'})"


class BandwidthSample(models.Model):
    """One row per collector poll of the router's WAN interface — feeds
    the bandwidth chart on the dashboard. in_octets/out_octets are the raw
    SNMP counters at poll time, kept only so the next poll can compute a
    rate; the UI only ever reads `mbps`."""

    at = models.DateTimeField(auto_now_add=True)
    mbps = models.FloatField()
    in_octets = models.BigIntegerField(null=True, blank=True)
    out_octets = models.BigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-at"]


class UsageSample(models.Model):
    """One row per collector poll — a snapshot of how many devices were
    connected, broken down by connection type and by lab. This is what
    the Analysis page charts over time (busiest lab, peak hour, Wi-Fi vs
    Ethernet trend). Cheap: one row per poll, not one per device."""

    at = models.DateTimeField(auto_now_add=True)
    wifi_count = models.IntegerField(default=0)
    ethernet_count = models.IntegerField(default=0)
    lab_counts = models.JSONField(default=dict)  # {"Lab 1": 12, "Lab 2": 9, ...}

    class Meta:
        ordering = ["-at"]


class SystemStatus(models.Model):
    """Singleton row (always pk=1) holding the latest overall health,
    updated on every collector poll."""

    internet_online = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def current(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class TimelineEvent(models.Model):
    GOOD, WARN, CRIT = "good", "warn", "crit"
    KIND_CHOICES = [(GOOD, "Good"), (WARN, "Warning"), (CRIT, "Critical")]

    at = models.DateTimeField(auto_now_add=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default=GOOD)
    text = models.CharField(max_length=200)
    sub = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["-at"]


class Alert(models.Model):
    NEW = "new"
    UNAUTHORIZED = "unauthorized"
    KIND_CHOICES = [(NEW, "New device"), (UNAUTHORIZED, "Unauthorized device")]

    kind = models.CharField(max_length=15, choices=KIND_CHOICES)
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="alerts")
    at = models.DateTimeField(auto_now_add=True)
    acknowledged = models.BooleanField(default=False)

    class Meta:
        ordering = ["-at"]
