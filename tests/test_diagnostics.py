"""Tests for redacted Health Sync diagnostics."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.diagnostics import REDACTED

from custom_components.resiyhome_health_sync.capabilities import (
    CapabilityId,
    validate_granted_scopes,
)
from custom_components.resiyhome_health_sync.const import (
    BASE_SCOPES,
    NUTRITION_SCOPE,
    SETTINGS_SCOPE,
)
from custom_components.resiyhome_health_sync.diagnostics import (
    _redact_recursive,
    _summarize_capabilities,
    _summarize_day,
    async_get_config_entry_diagnostics,
)
from custom_components.resiyhome_health_sync.models import (
    CapabilityRefreshState,
    CoordinatorSnapshot,
    DailySummary,
    ExpandedDailyMetrics,
    PairedDeviceSummary,
    SourceKind,
    WorkoutSummary,
)

NOW = datetime(2042, 7, 13, 12, 0, tzinfo=UTC)
FORBIDDEN = (
    "secret",
    "token",
    "client-id",
    "client_secret",
    "google_user",
    "dataPoints",
    "samples",
    "raw",
    "authorization_code",
    "users/me/pairedDevices/private-device-123",
    "AA:BB:CC:DD:EE:FF",
    "HEART_RATE",
    "private-paired-digest",
    "Private Tracker Model",
    "Private breakfast",
    "private-google-error",
)


def _summary() -> DailySummary:
    return DailySummary(
        date=date(2042, 7, 13),
        steps=6200,
        fitbit_steps=None,
        distance_m=4812.5,
        sleep_minutes=None,
        total_energy_kcal=2410.5,
        nutrition_energy_kcal=1830.0,
        hydration_ml=2150.0,
        sleep_period_minutes=402.0,
        sleep_onset_minutes=6.0,
        sleep_after_wake_minutes=12.0,
        expanded=ExpandedDailyMetrics(
            active_zone_minutes={"fat_burn": 12.0},
            vo2_max=42.5,
            vo2_estimated=False,
            cardio_fitness_level="GOOD",
            oxygen_average=96.2,
            oxygen_lower_bound=95.1,
            oxygen_upper_bound=97.3,
            oxygen_standard_deviation=0.4,
            daily_respiratory_rate=15.4,
            sleep_respiratory_rates={"full": 14.8},
            sleep_respiratory_standard_deviation=0.7,
            sleep_respiratory_signal_to_noise=3.2,
            floors=7,
            sedentary_minutes=480.0,
            heart_zone_minutes={"vigorous": 23.5},
            heart_zone_thresholds={"vigorous": (133, 159)},
            heart_zone_calories={"vigorous": 184.2},
            weight_kg=80.5,
            body_fat_percentage=21.4,
            height_m=1.778,
        ),
        source=SourceKind.MIXED,
        complete=False,
        updated_at=NOW,
    )


async def test_diagnostics_exposes_health_and_redacts_recursive_secrets(hass) -> None:
    entry = MagicMock()
    entry.entry_id = "entry-id"
    entry.title = "Sample Alpha"
    entry.data = {
        "person_name": "Sample Alpha",
        "person_slug": "sample_alpha",
        "client_id": "public-client-id",
        "client" + "_secret": "top-secret-client-value",
        "access" + "_token": "secret-access-token",
        "refresh" + "_token": "secret-refresh-token",
        "authorization_code": "secret-code",
        "nested": {
            "google_user_id": "google_user_123",
            "raw_payload": {"dataPoints": [{"secret": "sample"}]},
            "pairedDevices": [
                {
                    "name": "users/me/pairedDevices/private-device-123",
                    "macAddress": "AA:BB:CC:DD:EE:FF",
                    "features": ["HEART_RATE"],
                }
            ],
        },
    }
    entry.options = {
        "include_body_measurements": True,
        "include_nutrition": True,
        "include_paired_devices": True,
    }
    history = MagicMock()
    history.async_query = AsyncMock(return_value=[_summary()])
    history.backfill_cursor = date(2042, 7, 1)
    coordinator = MagicMock()
    coordinator.data = CoordinatorSnapshot(
        current_day=_summary(),
        last_success=NOW,
        last_attempt=NOW,
        authorization_healthy=True,
        backfill_cursor=date(2042, 7, 1),
        backfill_complete=False,
        expanded_backfill_cursor=date(2042, 4, 14),
        expanded_backfill_complete=True,
        paired_devices=(
            PairedDeviceSummary(
                identity_digest="private-paired-digest",
                device_type="TRACKER",
                product_name="Private Tracker Model",
                battery_status="High",
                battery_percentage=84,
                last_sync=NOW,
            ),
        ),
        capability_states={
            CapabilityId.NUTRITION: CapabilityRefreshState(
                enabled=True,
                scope_granted=True,
                last_success=NOW,
            ),
            CapabilityId.PAIRED_DEVICES: CapabilityRefreshState(
                enabled=True,
                scope_granted=True,
                last_success=NOW,
                error_category="private-google-error",
            ),
        },
    )
    coordinator.data_types = ("steps", "sleep")
    coordinator.is_stale = False
    entry.runtime_data.history = history
    entry.runtime_data.coordinator = coordinator
    entry.runtime_data.scope_grant = validate_granted_scopes(
        (*BASE_SCOPES, NUTRITION_SCOPE, SETTINGS_SCOPE),
        entry.options,
    )

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"] == {"loaded": True}
    assert result["coordinator"]["authorization_healthy"] is True
    assert result["coordinator"]["stale"] is False
    assert result["coordinator"]["supported_data_type_count"] == 2
    assert result["current_day"]["source"] == "mixed"
    assert result["current_day"]["metric_availability"]["steps"] is True
    assert result["current_day"]["metric_availability"]["sleep_minutes"] is False
    assert result["current_day"]["metric_availability"]["total_energy_kcal"] is True
    assert result["current_day"]["metric_availability"]["nutrition_energy_kcal"] is True
    assert result["current_day"]["metric_availability"]["hydration_ml"] is True
    assert result["current_day"]["metric_availability"]["sleep_period_minutes"] is True
    assert result["current_day"]["metric_availability"]["sleep_onset_minutes"] is True
    assert result["current_day"]["metric_availability"]["sleep_after_wake_minutes"] is True
    assert result["current_day"]["expanded_metric_availability"] == {
        "active_zone_minutes": True,
        "vo2_max": True,
        "vo2_estimated": True,
        "cardio_fitness_level": True,
        "oxygen_average": True,
        "oxygen_lower_bound": True,
        "oxygen_upper_bound": True,
        "oxygen_standard_deviation": True,
        "daily_respiratory_rate": True,
        "sleep_respiratory_rates": True,
        "sleep_respiratory_standard_deviation": True,
        "sleep_respiratory_signal_to_noise": True,
        "floors": True,
        "sedentary_minutes": True,
        "heart_zone_minutes": True,
        "heart_zone_thresholds": True,
        "heart_zone_calories": True,
        "weight_kg": True,
        "body_fat_percentage": True,
        "height_m": True,
    }
    assert result["capabilities"] == {
        "core_activity": {
            "enabled": True,
            "scope_granted": True,
            "last_refresh_success": True,
            "data_available": True,
            "error_category": None,
        },
        "sleep": {
            "enabled": True,
            "scope_granted": True,
            "last_refresh_success": True,
            "data_available": True,
            "error_category": None,
        },
        "body_measurements": {
            "enabled": True,
            "scope_granted": True,
            "last_refresh_success": True,
            "data_available": True,
            "error_category": None,
        },
        "nutrition": {
            "enabled": True,
            "scope_granted": True,
            "last_refresh_success": True,
            "data_available": True,
            "error_category": None,
        },
        "paired_devices": {
            "enabled": True,
            "scope_granted": True,
            "last_refresh_success": True,
            "data_available": True,
            "error_category": "unknown",
        },
    }
    assert result["history"]["bounds"] == {"start": "2042-07-13", "end": "2042-07-13"}
    assert result["backfill"] == {
        "cursor": "2042-07-01",
        "complete": False,
        "expanded_cursor": "2042-04-14",
        "expanded_complete": True,
    }

    exposed = repr(result)
    assert "Sample Alpha" not in exposed
    assert "sample_alpha" not in exposed.lower()
    for forbidden in FORBIDDEN:
        assert forbidden not in exposed
    for health_value in (
        "2410.5",
        "1830.0",
        "2150.0",
        "402.0",
        "21.4",
        "1.778",
        "42.5",
        "96.2",
        "133",
        "159",
        "184.2",
        "80.5",
        "vigorous",
    ):
        assert health_value not in exposed


def test_recursive_redaction_drops_nested_identifiers_and_raw_values() -> None:
    """Sensitive key fragments remove their nested values at every depth."""
    raw = {
        "outer": [
            {
                "pairedDevices": [
                    {
                        "name": "users/me/pairedDevices/private-device-123",
                        "productName": "Private Tracker Model",
                    }
                ],
                "macAddress": "AA:BB:CC:DD:EE:FF",
                "resource": {"name": "users/me/pairedDevices/private-device-123"},
                "identityDigest": "private-paired-digest",
                "foodName": "Private breakfast",
                "nutrition_name": "Private breakfast",
                "nutritionName": "Private breakfast",
                "nutrition-name": "Private breakfast",
                "accessToken": "secret-access-token",
                "features": ["HEART_RATE"],
                "rawGoogleError": {
                    "message": "private-google-error",
                    "details": [{"person": "Sample Alpha"}],
                },
            }
        ]
    }

    redacted = _redact_recursive(raw)

    exposed = repr(redacted)
    for forbidden in FORBIDDEN:
        assert forbidden not in exposed
    assert redacted == {
        "outer": [{"pairedDevices": [{"name": REDACTED}]}]
    }


def test_recursive_redaction_sanitizes_scalar_leaves_in_all_containers() -> None:
    """Generic keys cannot make an unsafe scalar value diagnostic-safe."""
    raw = {
        "message": "Google Health rejected Sample Alpha's private request",
        "value": 80.5,
        "category_like_value": "fitbit",
        "timestamp_like_value": NOW.isoformat(),
        "details": [
            "USERS/ME/PAIREDDEVICES/PRIVATE-DEVICE-123",
            "aA:bB:cC:dD:eE:fF",
            "bEaReR Private-Access-Token",
            (
                "Private breakfast",
                "Private Tracker Model",
                "device-identifier-123",
            ),
        ],
        "safe_values": [
            True,
            None,
            {"updated_at": NOW.isoformat()},
            {"error_category": "temporary"},
        ],
    }

    redacted = _redact_recursive(raw)

    assert redacted == {
        "message": REDACTED,
        "value": REDACTED,
        "category_like_value": REDACTED,
        "timestamp_like_value": REDACTED,
        "details": [
            REDACTED,
            REDACTED,
            REDACTED,
            (REDACTED, REDACTED, REDACTED),
        ],
        "safe_values": [
            True,
            None,
            {"updated_at": NOW.isoformat()},
            {"error_category": "temporary"},
        ],
    }


def test_redaction_preserves_safe_count_status_and_category_fields() -> None:
    """Boundary-aware classification retains bounded operational diagnostics."""
    safe = {
        "valid_count": 3,
        "ValidCount": 4,
        "VALIDCOUNT": 6,
        "status_code": 200,
        "STATUS-CODE": 204,
        "statuscode": 201,
        "resource_count": 2,
        "Resource.Count": 5,
        "RESOURCECOUNT": 7,
        "product_supported": True,
        "Product-Supported": False,
        "PRODUCTSUPPORTED": True,
        "authorization_healthy": True,
        "last_attempt": NOW.isoformat(),
        "error_category": "temporary",
        "nested": [
            {
                "feature_available": False,
                "device_count": 1,
                "updated_at": NOW.isoformat(),
            }
        ],
    }

    assert _redact_recursive(safe) == safe


def test_redaction_removes_case_and_separator_sensitive_key_variants() -> None:
    """Exact and compound sensitive key tokens are removed in every common style."""
    raw = {
        "AccessToken": "private",
        "access-token": "private",
        "ACCESS.TOKEN": "private",
        "Authorization Code": "private",
        "ClientID": "private",
        "client" + "_secret": "private",
        "DATA_POINTS": ["private"],
        "GoogleUserId": "private",
        "identity-digest": "private",
        "MAC Address": "private",
        "rawPayload": {"message": "private"},
        "".join(("resource", "Name")): "private",
        "food.name": "private",
        "Nutrition Name": "private",
        "ProductName": "private",
        "model-name": "private",
        "".join(("device", "Identifier")): "private",
        "featureList": ["private"],
    }

    assert _redact_recursive(raw) == {}


def test_body_capability_reports_historical_latest_measurement_available() -> None:
    """Body availability follows the latest-measurement state used by entities."""
    snapshot = CoordinatorSnapshot(
        last_success=NOW,
        latest_weight_kg=80.5,
        latest_weight_at=date(2042, 7, 10),
    )
    scope_grant = validate_granted_scopes(
        BASE_SCOPES,
        {"include_body_measurements": True},
    )

    capabilities = _summarize_capabilities(snapshot, scope_grant)

    assert capabilities["body_measurements"] == {
        "enabled": True,
        "scope_granted": True,
        "last_refresh_success": True,
        "data_available": True,
        "error_category": None,
    }


def test_baseline_capability_availability_includes_heart_and_sleep_stage_data() -> None:
    """Capability health recognizes every normalized baseline data family."""
    snapshot = CoordinatorSnapshot(
        current_day=DailySummary(
            date=date(2042, 7, 13),
            resting_heart_rate=54.0,
            sleep_stages={"deep": 60.0},
        ),
        last_success=NOW,
    )
    scope_grant = validate_granted_scopes(BASE_SCOPES, {})

    capabilities = _summarize_capabilities(snapshot, scope_grant)

    assert capabilities["core_activity"]["data_available"] is True
    assert capabilities["sleep"]["data_available"] is True


@pytest.mark.parametrize(
    ("capability_id", "field", "value", "expanded"),
    [
        (CapabilityId.CORE_ACTIVITY, "steps", 0, False),
        (CapabilityId.CORE_ACTIVITY, "fitbit_steps", 1, False),
        (CapabilityId.CORE_ACTIVITY, "distance_m", 1.0, False),
        (CapabilityId.CORE_ACTIVITY, "active_energy_kcal", 1.0, False),
        (CapabilityId.CORE_ACTIVITY, "exercise_minutes", 1.0, False),
        (CapabilityId.CORE_ACTIVITY, "resting_heart_rate", 1.0, False),
        (CapabilityId.CORE_ACTIVITY, "average_heart_rate", 1.0, False),
        (CapabilityId.CORE_ACTIVITY, "minimum_heart_rate", 1.0, False),
        (CapabilityId.CORE_ACTIVITY, "maximum_heart_rate", 1.0, False),
        (CapabilityId.CORE_ACTIVITY, "hrv_ms", 1.0, False),
        (CapabilityId.CORE_ACTIVITY, "total_energy_kcal", 1.0, False),
        (
            CapabilityId.CORE_ACTIVITY,
            "workouts",
            (
                WorkoutSummary(
                    activity_type="PRIVATE_WORKOUT_TYPE",
                    duration_minutes=47.0,
                ),
            ),
            False,
        ),
        (
            CapabilityId.CORE_ACTIVITY,
            "active_zone_minutes",
            {"fat_burn": 1.0},
            True,
        ),
        (CapabilityId.CORE_ACTIVITY, "vo2_max", 1.0, True),
        (CapabilityId.CORE_ACTIVITY, "vo2_estimated", False, True),
        (CapabilityId.CORE_ACTIVITY, "cardio_fitness_level", "GOOD", True),
        (CapabilityId.CORE_ACTIVITY, "oxygen_average", 1.0, True),
        (CapabilityId.CORE_ACTIVITY, "oxygen_lower_bound", 1.0, True),
        (CapabilityId.CORE_ACTIVITY, "oxygen_upper_bound", 1.0, True),
        (CapabilityId.CORE_ACTIVITY, "oxygen_standard_deviation", 1.0, True),
        (CapabilityId.CORE_ACTIVITY, "daily_respiratory_rate", 1.0, True),
        (CapabilityId.CORE_ACTIVITY, "floors", 0, True),
        (CapabilityId.CORE_ACTIVITY, "sedentary_minutes", 1.0, True),
        (
            CapabilityId.CORE_ACTIVITY,
            "heart_zone_minutes",
            {"vigorous": 1.0},
            True,
        ),
        (
            CapabilityId.CORE_ACTIVITY,
            "heart_zone_thresholds",
            {"vigorous": (1, 2)},
            True,
        ),
        (
            CapabilityId.CORE_ACTIVITY,
            "heart_zone_calories",
            {"vigorous": 1.0},
            True,
        ),
        (CapabilityId.SLEEP, "sleep_minutes", 1.0, False),
        (CapabilityId.SLEEP, "sleep_stages", {"deep": 1.0}, False),
        (CapabilityId.SLEEP, "sleep_period_minutes", 0.0, False),
        (CapabilityId.SLEEP, "sleep_onset_minutes", 0.0, False),
        (CapabilityId.SLEEP, "sleep_after_wake_minutes", 0.0, False),
        (
            CapabilityId.SLEEP,
            "sleep_respiratory_rates",
            {"full": 1.0},
            True,
        ),
        (
            CapabilityId.SLEEP,
            "sleep_respiratory_standard_deviation",
            1.0,
            True,
        ),
        (
            CapabilityId.SLEEP,
            "sleep_respiratory_signal_to_noise",
            1.0,
            True,
        ),
    ],
)
def test_capability_availability_matches_every_normalized_family(
    capability_id: CapabilityId,
    field: str,
    value: object,
    expanded: bool,
) -> None:
    """Every exposed field family makes its owning capability available."""
    summary = (
        DailySummary(
            date=date(2042, 7, 13),
            expanded=ExpandedDailyMetrics(**{field: value}),
        )
        if expanded
        else DailySummary(date=date(2042, 7, 13), **{field: value})
    )
    day = _summarize_day(summary)
    capabilities = _summarize_capabilities(
        CoordinatorSnapshot(current_day=summary, last_success=NOW),
        validate_granted_scopes(BASE_SCOPES, {}),
    )
    availability_key = (
        "expanded_metric_availability" if expanded else "metric_availability"
    )

    assert day[availability_key][field] is True
    assert capabilities[capability_id.value]["data_available"] is True
    assert "PRIVATE_WORKOUT_TYPE" not in repr(day)
    assert "47.0" not in repr(day)


def test_empty_summary_reports_all_normalized_families_unavailable() -> None:
    """Missing data stays unavailable without inferred zeroes."""
    summary = DailySummary(date=date(2042, 7, 13))
    day = _summarize_day(summary)
    capabilities = _summarize_capabilities(
        CoordinatorSnapshot(current_day=summary, last_success=NOW),
        validate_granted_scopes(BASE_SCOPES, {}),
    )

    assert all(value is False for value in day["metric_availability"].values())
    assert all(
        value is False for value in day["expanded_metric_availability"].values()
    )
    assert capabilities["core_activity"]["data_available"] is False
    assert capabilities["sleep"]["data_available"] is False


async def test_diagnostics_handles_disabled_entry_without_runtime_data(hass) -> None:
    """Disabled entries should expose safe static diagnostics instead of raising."""
    entry = MagicMock()
    entry.title = "Sample Alpha"
    entry.data = {
        "person_slug": "sample_alpha",
        "client_id": "public-client-id",
        "client" + "_secret": "top-secret-client-value",
        "refresh" + "_token": "secret-refresh-token",
    }
    del entry.runtime_data

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result == {
        "entry": {
            "loaded": False,
        },
        "coordinator": None,
        "capabilities": {},
        "current_day": {
            "source": "unavailable",
            "metric_availability": {
                "steps": False,
                "fitbit_steps": False,
                "distance_m": False,
                "active_energy_kcal": False,
                "exercise_minutes": False,
                "sleep_minutes": False,
                "resting_heart_rate": False,
                "average_heart_rate": False,
                "minimum_heart_rate": False,
                "maximum_heart_rate": False,
                "hrv_ms": False,
                "total_energy_kcal": False,
                "workouts": False,
                "nutrition_energy_kcal": False,
                "hydration_ml": False,
                "sleep_stages": False,
                "sleep_period_minutes": False,
                "sleep_onset_minutes": False,
                "sleep_after_wake_minutes": False,
            },
            "expanded_metric_availability": {
                "active_zone_minutes": False,
                "vo2_max": False,
                "vo2_estimated": False,
                "cardio_fitness_level": False,
                "oxygen_average": False,
                "oxygen_lower_bound": False,
                "oxygen_upper_bound": False,
                "oxygen_standard_deviation": False,
                "daily_respiratory_rate": False,
                "sleep_respiratory_rates": False,
                "sleep_respiratory_standard_deviation": False,
                "sleep_respiratory_signal_to_noise": False,
                "floors": False,
                "sedentary_minutes": False,
                "heart_zone_minutes": False,
                "heart_zone_thresholds": False,
                "heart_zone_calories": False,
                "weight_kg": False,
                "body_fat_percentage": False,
                "height_m": False,
            },
        },
        "history": {"bounds": {"start": None, "end": None}, "loaded_days_sampled": 0},
        "backfill": {
            "cursor": None,
            "complete": False,
            "expanded_cursor": None,
            "expanded_complete": False,
        },
        "issues": ["entry_not_loaded"],
    }

    exposed = repr(result)
    for forbidden in FORBIDDEN:
        assert forbidden not in exposed
