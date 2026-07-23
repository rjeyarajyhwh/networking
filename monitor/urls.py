from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("api/kpis/", views.api_kpis),
    path("api/bandwidth/", views.api_bandwidth),
    path("api/labs/", views.api_labs),
    path("api/labs/<int:lab_id>/devices/", views.api_lab_devices),
    path("api/devices/", views.api_devices),
    path("api/wifi/", views.api_wifi),
    path("api/ethernet/", views.api_ethernet),
    path("api/timeline/", views.api_timeline),
    path("api/alerts/", views.api_alerts),
    path("api/alerts/<int:alert_id>/ack/", views.api_alert_ack),
    path("api/devices/<int:device_id>/block/", views.api_device_block),
    path("api/whitelist/", views.api_whitelist),
    path("api/config/", views.api_config),
    path("api/analysis/", views.api_analysis),
]
