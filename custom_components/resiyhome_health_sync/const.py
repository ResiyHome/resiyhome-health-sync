"""Constants for the Health Sync integration."""

from datetime import timedelta

DOMAIN = "resiyhome_health_sync"

TOKEN_URL = "https://oauth2.googleapis.com/token"
HEALTH_API_BASE_URL = "https://health.googleapis.com/v4"

SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
)

SCAN_INTERVAL = timedelta(minutes=15)
MANUAL_REFRESH_COOLDOWN = timedelta(minutes=5)
