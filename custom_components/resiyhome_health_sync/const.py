"""Constants for the Health Sync integration."""

from datetime import timedelta

DOMAIN = "resiyhome_health_sync"

TOKEN_URL = "https://oauth2.googleapis.com/token"
HEALTH_API_BASE_URL = "https://health.googleapis.com/v4"

BASE_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
)
NUTRITION_SCOPE = "https://www.googleapis.com/auth/googlehealth.nutrition.readonly"
SETTINGS_SCOPE = "https://www.googleapis.com/auth/googlehealth.settings.readonly"
SUPPORTED_SCOPES = frozenset((*BASE_SCOPES, NUTRITION_SCOPE, SETTINGS_SCOPE))

# Backward-compatible public constant used by release and documentation tests.
SCOPES = BASE_SCOPES

SCAN_INTERVAL = timedelta(minutes=15)
MANUAL_REFRESH_COOLDOWN = timedelta(minutes=5)
