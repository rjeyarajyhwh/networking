from django.contrib import admin

from .models import Alert, BandwidthSample, Device, Lab, SystemStatus, TimelineEvent, UsageSample, Whitelist


@admin.register(Lab)
class LabAdmin(admin.ModelAdmin):
    list_display = ("name", "room", "switch_ip", "order")


@admin.register(Whitelist)
class WhitelistAdmin(admin.ModelAdmin):
    list_display = ("mac", "student_name", "note", "added_at")
    search_fields = ("mac", "student_name")


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = (
        "mac", "ip", "hostname", "connection_type", "lab", "port",
        "status", "is_authorized", "is_blocked", "last_seen",
    )
    list_filter = ("connection_type", "status", "is_authorized", "lab")
    search_fields = ("mac", "ip", "hostname", "student_name")


@admin.register(BandwidthSample)
class BandwidthSampleAdmin(admin.ModelAdmin):
    list_display = ("at", "mbps")


@admin.register(TimelineEvent)
class TimelineEventAdmin(admin.ModelAdmin):
    list_display = ("at", "kind", "text", "sub")


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    list_display = ("at", "kind", "device", "acknowledged")
    list_filter = ("kind", "acknowledged")


@admin.register(SystemStatus)
class SystemStatusAdmin(admin.ModelAdmin):
    list_display = ("internet_online", "updated_at")


@admin.register(UsageSample)
class UsageSampleAdmin(admin.ModelAdmin):
    list_display = ("at", "wifi_count", "ethernet_count", "lab_counts")
