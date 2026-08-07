"""Authenticated WebSocket history command for Health Sync."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .capabilities import CapabilityId
from .const import DOMAIN
from .models import DailySummary

_COMMAND = "resiyhome_health_sync/history"
_MAX_DAYS = 20 * 366
_MAX_EXPANDED_DAYS = 90
_EXPANDED_METRICS = frozenset(
    {
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
        "body_fat_percentage",
        "height_m",
    }
)
_CORE_METRICS = frozenset(
    {
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
        "total_energy_kcal",
        "sleep_period_minutes",
        "sleep_onset_minutes",
        "sleep_after_wake_minutes",
        "source",
        "complete",
        "updated_at",
    }
)
_BODY_METRICS = frozenset({"weight_kg", "body_fat_percentage", "height_m"})
_NUTRITION_METRICS = frozenset({"nutrition_energy_kcal", "hydration_ml"})
_METRICS = _CORE_METRICS | _EXPANDED_METRICS | _NUTRITION_METRICS
_DEFAULT_METRICS = tuple(sorted(_CORE_METRICS - {"updated_at"}))


@callback
def async_register_websocket(hass: HomeAssistant) -> None:
    """Register the normalized history command."""
    websocket_api.async_register_command(hass, _async_history)


@callback
def async_unregister_websocket(hass: HomeAssistant) -> None:
    """Remove the normalized history command when no entries remain loaded."""
    handlers = hass.data.get(websocket_api.DOMAIN)
    if handlers is not None:
        handlers.pop(_COMMAND, None)


@websocket_api.websocket_command(  # type: ignore[attr-defined]
    {
        vol.Required("type"): _COMMAND,
        vol.Required("person"): str,
        vol.Required("start_date"): str,
        vol.Required("end_date"): str,
        vol.Optional("metrics"): [str],
    }
)
@callback
def _async_history(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    """Handle one bounded normalized history query."""
    hass.async_create_task(_async_handle_history(hass, connection, msg))


async def _async_handle_history(hass: HomeAssistant, connection: Any, msg: dict[str, Any]) -> None:
    person = msg["person"].strip()
    entry = _entry_for_person(hass, person)
    if entry is None:
        connection.send_error(msg["id"], "not_found", "person was not found")
        return

    start = _parse_date(msg["start_date"])
    end = _parse_date(msg["end_date"])
    if start is None or end is None:
        connection.send_error(msg["id"], "invalid_format", "dates must be ISO calendar dates")
        return
    if start > end:
        connection.send_error(
            msg["id"], "invalid_range", "start_date must be on or before end_date"
        )
        return
    if (end - start).days > _MAX_DAYS:
        connection.send_error(
            msg["id"], "range_too_large", "history queries are limited to 20 years"
        )
        return

    metrics = tuple(msg.get("metrics") or _DEFAULT_METRICS)
    invalid = sorted(set(metrics) - _METRICS)
    if invalid:
        connection.send_error(msg["id"], "invalid_metric", "unsupported metric requested")
        return
    if (end - start).days >= _MAX_EXPANDED_DAYS and set(metrics) & _EXPANDED_METRICS:
        connection.send_error(
            msg["id"],
            "range_too_large",
            "expanded history queries are limited to 90 days",
        )
        return

    rows = await entry.runtime_data.history.async_query(start, end)
    include_body_measurements = bool(
        entry.options.get("include_body_measurements", False)
    )
    include_nutrition = (
        CapabilityId.NUTRITION
        in entry.runtime_data.scope_grant.available_capabilities
    )
    connection.send_result(
        msg["id"],
        {
            "person": person,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "records": [
                _serialize_summary(
                    row,
                    metrics,
                    include_body_measurements=include_body_measurements,
                    include_nutrition=include_nutrition,
                )
                for row in rows
            ],
        },
    )


def _entry_for_person(hass: HomeAssistant, person_slug: str) -> Any | None:
    matches = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.data.get("person_slug") == person_slug and hasattr(entry, "runtime_data")
    ]
    return matches[0] if len(matches) == 1 else None


def _parse_date(value: str) -> date | None:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if value == parsed.isoformat() else None


def _serialize_summary(
    summary: DailySummary,
    metrics: Iterable[str],
    *,
    include_body_measurements: bool = False,
    include_nutrition: bool = False,
) -> dict[str, object]:
    record: dict[str, object] = {"date": summary.date.isoformat()}
    for metric in metrics:
        value = (
            getattr(summary.expanded, metric)
            if metric in _EXPANDED_METRICS
            else getattr(summary, metric)
        )
        if metric in _BODY_METRICS and not include_body_measurements:
            value = None
        if metric in _NUTRITION_METRICS and not include_nutrition:
            value = None
        if metric == "source":
            record[metric] = value.value
        elif metric == "updated_at":
            record[metric] = value.isoformat() if value is not None else None
        else:
            record[metric] = _json_value(value)
    return record


def _json_value(value: object) -> object:
    """Convert immutable normalized collections to JSON-native containers."""
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_value(item) for item in value]
    return value
