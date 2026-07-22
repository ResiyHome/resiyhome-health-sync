"""Redacted diagnostics for Health Sync."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, cast

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .models import DailySummary

_REDACT_KEYS = {
    "access_token",
    "authorization_code",
    "client_id",
    "client_secret",
    "code",
    "dataPoints",
    "google_id",
    "google_user",
    "google_user_id",
    "id",
    "raw",
    "raw_payload",
    "refresh_token",
    "samples",
    "secret",
    "token",
}
_METRICS = (
    "steps",
    "fitbit_steps",
    "distance_m",
    "active_energy_kcal",
    "exercise_minutes",
    "sleep_minutes",
    "resting_heart_rate",
    "average_heart_rate",
    "minimum_heart_rate",
    "maximum_heart_rate",
    "hrv_ms",
)
_EXPANDED_METRICS = (
    "active_zone_minutes",
    "vo2_max",
    "vo2_estimated",
    "cardio_fitness_level",
    "oxygen_average",
    "oxygen_lower_bound",
    "oxygen_upper_bound",
    "oxygen_standard_deviation",
    "daily_respiratory_rate",
    "sleep_respiratory_rates",
    "sleep_respiratory_standard_deviation",
    "sleep_respiratory_signal_to_noise",
    "floors",
    "sedentary_minutes",
    "heart_zone_minutes",
    "heart_zone_thresholds",
    "heart_zone_calories",
    "weight_kg",
)


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: Any) -> dict[str, object]:
    """Return secret-safe integration health diagnostics for one person."""
    if not hasattr(entry, "runtime_data"):
        return cast(
            dict[str, object],
            _redact_recursive(
                {
                    "entry": {
                        "loaded": False,
                    },
                    "coordinator": None,
                    "current_day": _summarize_day(None),
                    "history": {
                        "bounds": _history_bounds([]),
                        "loaded_days_sampled": 0,
                    },
                    "backfill": {
                        "cursor": None,
                        "complete": False,
                        "expanded_cursor": None,
                        "expanded_complete": False,
                    },
                    "issues": ["entry_not_loaded"],
                }
            ),
        )

    coordinator = entry.runtime_data.coordinator
    history = entry.runtime_data.history
    current = coordinator.data.current_day
    rows: list[DailySummary] = []
    if current is not None:
        rows = await history.async_query(current.date, current.date)

    result: dict[str, object] = {
        "entry": {"loaded": True},
        "coordinator": {
            "authorization_healthy": coordinator.data.authorization_healthy,
            "stale": coordinator.is_stale,
            "last_success": _iso(coordinator.data.last_success),
            "last_attempt": _iso(coordinator.data.last_attempt),
            "supported_data_type_count": len(coordinator.data_types),
        },
        "current_day": _summarize_day(
            current,
            include_weight=bool(entry.options.get("include_body_measurements", False)),
        ),
        "history": {
            "bounds": _history_bounds(rows),
            "loaded_days_sampled": len(rows),
        },
        "backfill": {
            "cursor": _iso(coordinator.data.backfill_cursor),
            "complete": coordinator.data.backfill_complete,
            "expanded_cursor": _iso(coordinator.data.expanded_backfill_cursor),
            "expanded_complete": coordinator.data.expanded_backfill_complete,
        },
    }
    return cast(dict[str, object], _redact_recursive(result))


def _summarize_day(
    summary: DailySummary | None, *, include_weight: bool = False
) -> dict[str, object]:
    if summary is None:
        return {
            "source": "unavailable",
            "metric_availability": {metric: False for metric in _METRICS},
            "expanded_metric_availability": {metric: False for metric in _EXPANDED_METRICS},
        }
    expanded_availability = {
        metric: _is_available(getattr(summary.expanded, metric)) for metric in _EXPANDED_METRICS
    }
    if not include_weight:
        expanded_availability["weight_kg"] = False
    return {
        "date": summary.date.isoformat(),
        "source": summary.source.value,
        "complete": summary.complete,
        "updated_at": _iso(summary.updated_at),
        "metric_availability": {
            metric: getattr(summary, metric) is not None for metric in _METRICS
        },
        "expanded_metric_availability": expanded_availability,
    }


def _is_available(value: object) -> bool:
    if isinstance(value, Mapping):
        return bool(value)
    return value is not None


def _history_bounds(rows: list[DailySummary]) -> dict[str, str | None]:
    if not rows:
        return {"start": None, "end": None}
    days = sorted(row.date for row in rows)
    return {"start": days[0].isoformat(), "end": days[-1].isoformat()}


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _redact_recursive(value: Any) -> Any:
    redacted = async_redact_data(value, _REDACT_KEYS)
    return _drop_redacted_keys(redacted)


def _drop_redacted_keys(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _drop_redacted_keys(item)
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, list):
        return [_drop_redacted_keys(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_drop_redacted_keys(item) for item in value)
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(fragment.lower() in lowered for fragment in _REDACT_KEYS)
