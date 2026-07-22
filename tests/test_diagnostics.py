"""Tests for redacted Health Sync diagnostics."""

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

from custom_components.resiyhome_health_sync.diagnostics import async_get_config_entry_diagnostics
from custom_components.resiyhome_health_sync.models import (
    CoordinatorSnapshot,
    DailySummary,
    ExpandedDailyMetrics,
    SourceKind,
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
)


def _summary() -> DailySummary:
    return DailySummary(
        date=date(2042, 7, 13),
        steps=6200,
        fitbit_steps=None,
        distance_m=4812.5,
        sleep_minutes=None,
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
        },
    }
    entry.options = {"include_body_measurements": True}
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
    )
    coordinator.data_types = ("steps", "sleep")
    coordinator.is_stale = False
    entry.runtime_data.history = history
    entry.runtime_data.coordinator = coordinator

    result = await async_get_config_entry_diagnostics(hass, entry)

    assert result["entry"] == {"loaded": True}
    assert result["coordinator"]["authorization_healthy"] is True
    assert result["coordinator"]["stale"] is False
    assert result["coordinator"]["supported_data_type_count"] == 2
    assert result["current_day"]["source"] == "mixed"
    assert result["current_day"]["metric_availability"]["steps"] is True
    assert result["current_day"]["metric_availability"]["sleep_minutes"] is False
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
    for health_value in ("42.5", "96.2", "133", "159", "184.2", "80.5", "vigorous"):
        assert health_value not in exposed


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
