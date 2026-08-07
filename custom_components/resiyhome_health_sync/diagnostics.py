"""Redacted diagnostics for Health Sync."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, cast

from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant

from .capabilities import CAPABILITIES, CapabilityId, ScopeGrant
from .models import CapabilityRefreshState, CoordinatorSnapshot, DailySummary

_SENSITIVE_KEY_TOKENS = frozenset(
    {
        "bearer",
        "code",
        "digest",
        "feature",
        "features",
        "food",
        "id",
        "identifier",
        "identity",
        "mac",
        "model",
        "product",
        "raw",
        "resource",
        "samples",
        "secret",
        "token",
    }
)
_SENSITIVE_CONCATENATED_KEYS = frozenset(
    {
        "accesstoken",
        "authorizationcode",
        "clientid",
        "clientsecret",
        "datapoints",
        "deviceid",
        "deviceidentifier",
        "devicename",
        "featurelist",
        "foodname",
        "googleid",
        "googleuser",
        "googleuserid",
        "identitydigest",
        "macaddress",
        "modelname",
        "nutritionname",
        "productname",
        "rawpayload",
        "refreshtoken",
        "resourceid",
        "resourcename",
        "resourcepath",
    }
)
_SENSITIVE_KEY_SEQUENCES = (
    ("access", "token"),
    ("authorization", "code"),
    ("client", "id"),
    ("client", "secret"),
    ("data", "points"),
    ("device", "id"),
    ("device", "identifier"),
    ("device", "name"),
    ("feature", "list"),
    ("food", "name"),
    ("google", "id"),
    ("google", "user"),
    ("identity", "digest"),
    ("mac", "address"),
    ("model", "name"),
    ("nutrition", "name"),
    ("product", "name"),
    ("raw", "payload"),
    ("refresh", "token"),
    ("resource", "id"),
    ("resource", "name"),
    ("resource", "path"),
)
_SAFE_CONCATENATED_KEY_TOKENS = {
    "authorizationhealthy": ("authorization", "healthy"),
    "devicecount": ("device", "count"),
    "errorcategory": ("error", "category"),
    "featureavailable": ("feature", "available"),
    "lastattempt": ("last", "attempt"),
    "productsupported": ("product", "supported"),
    "resourcecount": ("resource", "count"),
    "statuscode": ("status", "code"),
    "updatedat": ("updated", "at"),
    "validcount": ("valid", "count"),
}
_SAFE_KEY_SUFFIXES = frozenset(
    {
        "at",
        "available",
        "category",
        "complete",
        "count",
        "date",
        "enabled",
        "granted",
        "healthy",
        "sampled",
        "stale",
        "success",
        "supported",
        "timestamp",
    }
)
_SAFE_CATEGORIES = frozenset(
    {
        "apple_fallback",
        "authorization",
        "complete",
        "entry_not_loaded",
        "failure",
        "fitbit",
        "healthy",
        "in_progress",
        "loaded",
        "mixed",
        "not_loaded",
        "ok",
        "success",
        "temporary",
        "unavailable",
        "unknown",
        "unhealthy",
    }
)
_SAFE_CATEGORY_KEY_SUFFIXES = frozenset(
    {"category", "issues", "source", "state", "status"}
)
_SAFE_TIMESTAMP_KEY_SUFFIXES = frozenset(
    {"at", "attempt", "cursor", "date", "end", "start", "success", "timestamp"}
)
_CORE_DAILY_METRICS = (
    "steps",
    "fitbit_steps",
    "distance_m",
    "active_energy_kcal",
    "exercise_minutes",
    "resting_heart_rate",
    "average_heart_rate",
    "minimum_heart_rate",
    "maximum_heart_rate",
    "hrv_ms",
    "total_energy_kcal",
    "workouts",
)
_SLEEP_DAILY_METRICS = (
    "sleep_minutes",
    "sleep_stages",
    "sleep_period_minutes",
    "sleep_onset_minutes",
    "sleep_after_wake_minutes",
)
_NUTRITION_METRICS = ("nutrition_energy_kcal", "hydration_ml")
_METRICS = (*_CORE_DAILY_METRICS, *_SLEEP_DAILY_METRICS, *_NUTRITION_METRICS)
_CORE_EXPANDED_METRICS = (
    "active_zone_minutes",
    "vo2_max",
    "vo2_estimated",
    "cardio_fitness_level",
    "oxygen_average",
    "oxygen_lower_bound",
    "oxygen_upper_bound",
    "oxygen_standard_deviation",
    "daily_respiratory_rate",
    "floors",
    "sedentary_minutes",
    "heart_zone_minutes",
    "heart_zone_thresholds",
    "heart_zone_calories",
)
_SLEEP_EXPANDED_METRICS = (
    "sleep_respiratory_rates",
    "sleep_respiratory_standard_deviation",
    "sleep_respiratory_signal_to_noise",
)
_BODY_METRICS = ("weight_kg", "body_fat_percentage", "height_m")
_EXPANDED_METRICS = (
    *_CORE_EXPANDED_METRICS,
    *_SLEEP_EXPANDED_METRICS,
    "weight_kg",
    "body_fat_percentage",
    "height_m",
)
_LATEST_BODY_METRICS = (
    "latest_weight_kg",
    "latest_body_fat_percentage",
    "latest_height_m",
)
_SAFE_ERROR_CATEGORIES = frozenset({"authorization", "temporary"})


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
                    "capabilities": {},
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
    scope_grant = entry.runtime_data.scope_grant
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
        "capabilities": _summarize_capabilities(coordinator.data, scope_grant),
        "current_day": _summarize_day(
            current,
            include_body_measurements=bool(
                entry.options.get("include_body_measurements", False)
            ),
            include_nutrition=(
                CapabilityId.NUTRITION in scope_grant.available_capabilities
            ),
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
    summary: DailySummary | None,
    *,
    include_body_measurements: bool = False,
    include_nutrition: bool = False,
) -> dict[str, object]:
    metric_availability, expanded_availability = _summary_availability(summary)
    if summary is None:
        return {
            "source": "unavailable",
            "metric_availability": metric_availability,
            "expanded_metric_availability": expanded_availability,
        }
    if not include_body_measurements:
        for metric in _BODY_METRICS:
            expanded_availability[metric] = False
    if not include_nutrition:
        for metric in _NUTRITION_METRICS:
            metric_availability[metric] = False
    return {
        "date": summary.date.isoformat(),
        "source": summary.source.value,
        "complete": summary.complete,
        "updated_at": _iso(summary.updated_at),
        "metric_availability": metric_availability,
        "expanded_metric_availability": expanded_availability,
    }


def _summarize_capabilities(
    snapshot: CoordinatorSnapshot, scope_grant: ScopeGrant
) -> dict[str, object]:
    """Return only bounded health state for every declared capability."""
    return {
        capability_id.value: _summarize_capability(snapshot, scope_grant, capability_id)
        for capability_id in CapabilityId
    }


def _summarize_capability(
    snapshot: CoordinatorSnapshot,
    scope_grant: ScopeGrant,
    capability_id: CapabilityId,
) -> dict[str, object]:
    state: CapabilityRefreshState | None = snapshot.capability_states.get(capability_id)
    enabled = (
        state.enabled
        if state is not None
        else capability_id in scope_grant.enabled_capabilities
    )
    scope_granted = (
        state.scope_granted
        if state is not None
        else CAPABILITIES[capability_id].required_scopes <= scope_grant.granted_scopes
    )
    last_success = (
        state.last_success
        if state is not None
        else snapshot.last_success if enabled and scope_granted else None
    )
    error_category = state.error_category if state is not None else None
    if error_category is None and enabled and not scope_granted:
        error_category = "authorization"
    if (
        error_category is None
        and capability_id
        in {
            CapabilityId.CORE_ACTIVITY,
            CapabilityId.SLEEP,
            CapabilityId.BODY_MEASUREMENTS,
        }
        and not snapshot.authorization_healthy
    ):
        error_category = "authorization"
    return {
        "enabled": enabled,
        "scope_granted": scope_granted,
        "last_refresh_success": last_success is not None,
        "data_available": (
            enabled
            and scope_granted
            and _capability_data_available(snapshot, capability_id)
        ),
        "error_category": _safe_error_category(error_category),
    }


def _capability_data_available(
    snapshot: CoordinatorSnapshot, capability_id: CapabilityId
) -> bool:
    current = snapshot.current_day
    if capability_id is CapabilityId.PAIRED_DEVICES:
        return bool(snapshot.paired_devices)
    metric_availability, expanded_availability = _summary_availability(current)
    if capability_id is CapabilityId.BODY_MEASUREMENTS:
        return any(
            _is_available(getattr(snapshot, metric)) for metric in _LATEST_BODY_METRICS
        ) or any(expanded_availability[metric] for metric in _BODY_METRICS)
    if current is None:
        return False
    if capability_id is CapabilityId.CORE_ACTIVITY:
        return any(
            metric_availability[metric] for metric in _CORE_DAILY_METRICS
        ) or any(
            expanded_availability[metric] for metric in _CORE_EXPANDED_METRICS
        )
    if capability_id is CapabilityId.SLEEP:
        return any(
            metric_availability[metric] for metric in _SLEEP_DAILY_METRICS
        ) or any(
            expanded_availability[metric] for metric in _SLEEP_EXPANDED_METRICS
        )
    if capability_id is CapabilityId.NUTRITION:
        return any(metric_availability[metric] for metric in _NUTRITION_METRICS)
    return False


def _safe_error_category(value: str | None) -> str | None:
    if value is None or value in _SAFE_ERROR_CATEGORIES:
        return value
    return "unknown"


def _is_available(value: object) -> bool:
    if isinstance(value, (Mapping, list, tuple, set, frozenset, str)):
        return bool(value)
    return value is not None


def _summary_availability(
    summary: DailySummary | None,
) -> tuple[dict[str, bool], dict[str, bool]]:
    if summary is None:
        return (
            {metric: False for metric in _METRICS},
            {metric: False for metric in _EXPANDED_METRICS},
        )
    return (
        {metric: _is_available(getattr(summary, metric)) for metric in _METRICS},
        {
            metric: _is_available(getattr(summary.expanded, metric))
            for metric in _EXPANDED_METRICS
        },
    )


def _history_bounds(rows: list[DailySummary]) -> dict[str, str | None]:
    if not rows:
        return {"start": None, "end": None}
    days = sorted(row.date for row in rows)
    return {"start": days[0].isoformat(), "end": days[-1].isoformat()}


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _redact_recursive(value: Any) -> Any:
    return _sanitize_recursive(value)


def _sanitize_recursive(value: Any, key_tokens: tuple[str, ...] = ()) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_recursive(item, _key_tokens(str(key)))
            for key, item in value.items()
            if not _is_sensitive_key(str(key))
        }
    if isinstance(value, list):
        return [_sanitize_recursive(item, key_tokens) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_recursive(item, key_tokens) for item in value)
    return _sanitize_scalar(value, key_tokens)


def _is_sensitive_key(key: str) -> bool:
    tokens = _key_tokens(key)
    if not tokens:
        return False
    if tokens == ("status", "code") or tokens[-1] in _SAFE_KEY_SUFFIXES:
        return False
    if any(token in _SENSITIVE_KEY_TOKENS for token in tokens):
        return True
    concatenated = "".join(tokens)
    if concatenated in _SENSITIVE_CONCATENATED_KEYS:
        return True
    return any(_contains_token_sequence(tokens, sequence) for sequence in _SENSITIVE_KEY_SEQUENCES)


def _key_tokens(key: str) -> tuple[str, ...]:
    separated = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", key)
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", separated)
    tokens = tuple(re.findall(r"[a-z0-9]+", separated.casefold()))
    if len(tokens) == 1:
        return _SAFE_CONCATENATED_KEY_TOKENS.get(tokens[0], tokens)
    return tokens


def _contains_token_sequence(
    tokens: tuple[str, ...], sequence: tuple[str, ...]
) -> bool:
    width = len(sequence)
    return any(tokens[index : index + width] == sequence for index in range(len(tokens)))


def _sanitize_scalar(value: Any, key_tokens: tuple[str, ...]) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat() if _is_timestamp_key(key_tokens) else REDACTED
    if isinstance(value, int) and _is_count_or_status_key(key_tokens):
        return value
    if isinstance(value, str):
        if value == REDACTED:
            return value
        if _is_category_key(key_tokens) and value.casefold() in _SAFE_CATEGORIES:
            return value
        if _is_timestamp_key(key_tokens) and _is_iso_date_or_datetime(value):
            return value
    return REDACTED


def _is_count_or_status_key(key_tokens: tuple[str, ...]) -> bool:
    return bool(key_tokens) and (
        key_tokens[-1] in {"count", "sampled"}
        or key_tokens == ("status", "code")
    )


def _is_category_key(key_tokens: tuple[str, ...]) -> bool:
    return bool(key_tokens) and key_tokens[-1] in _SAFE_CATEGORY_KEY_SUFFIXES


def _is_timestamp_key(key_tokens: tuple[str, ...]) -> bool:
    return bool(key_tokens) and key_tokens[-1] in _SAFE_TIMESTAMP_KEY_SUFFIXES


def _is_iso_date_or_datetime(value: str) -> bool:
    try:
        if "T" in value:
            datetime.fromisoformat(value)
        else:
            date.fromisoformat(value)
    except ValueError:
        return False
    return True
