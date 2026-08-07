"""Typed capability and OAuth scope policy for Health Sync."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from .const import BASE_SCOPES, NUTRITION_SCOPE, SETTINGS_SCOPE, SUPPORTED_SCOPES


class CapabilityId(StrEnum):
    """Stable identifiers for Health Sync data capabilities."""

    CORE_ACTIVITY = "core_activity"
    SLEEP = "sleep"
    BODY_MEASUREMENTS = "body_measurements"
    NUTRITION = "nutrition"
    PAIRED_DEVICES = "paired_devices"


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    """Describe the authorization and data boundary for one capability."""

    capability_id: CapabilityId
    required_scopes: frozenset[str]
    option_key: str | None
    data_types: tuple[str, ...] = ()
    endpoint: str | None = None
    backfill_days: int = 0


@dataclass(frozen=True, slots=True)
class ScopeGrant:
    """State of a token relative to enabled Health Sync capabilities."""

    granted_scopes: frozenset[str]
    baseline_valid: bool
    enabled_capabilities: frozenset[CapabilityId]
    available_capabilities: frozenset[CapabilityId]
    missing_optional_scopes: frozenset[str]


class UnsupportedScopeError(ValueError):
    """Raised when a token contains a scope Health Sync must not accept."""


CAPABILITIES: dict[CapabilityId, CapabilitySpec] = {
    CapabilityId.CORE_ACTIVITY: CapabilitySpec(
        CapabilityId.CORE_ACTIVITY,
        frozenset({BASE_SCOPES[0], BASE_SCOPES[1]}),
        None,
        ("total-calories",),
        backfill_days=20 * 366,
    ),
    CapabilityId.SLEEP: CapabilitySpec(
        CapabilityId.SLEEP,
        frozenset({BASE_SCOPES[2]}),
        None,
        ("sleep",),
        backfill_days=20 * 366,
    ),
    CapabilityId.BODY_MEASUREMENTS: CapabilitySpec(
        CapabilityId.BODY_MEASUREMENTS,
        frozenset({BASE_SCOPES[1]}),
        "include_body_measurements",
        ("weight", "body-fat", "height"),
        backfill_days=90,
    ),
    CapabilityId.NUTRITION: CapabilitySpec(
        CapabilityId.NUTRITION,
        frozenset({NUTRITION_SCOPE}),
        "include_nutrition",
        ("nutrition-log", "hydration-log"),
    ),
    CapabilityId.PAIRED_DEVICES: CapabilitySpec(
        CapabilityId.PAIRED_DEVICES,
        frozenset({SETTINGS_SCOPE}),
        "include_paired_devices",
        endpoint="/users/me/pairedDevices",
    ),
}


def enabled_capabilities(options: Mapping[str, object]) -> frozenset[CapabilityId]:
    """Return capabilities enabled by the entry's options."""
    return frozenset(
        capability_id
        for capability_id, spec in CAPABILITIES.items()
        if spec.option_key is None or bool(options.get(spec.option_key, False))
    )


def requested_scopes(options: Mapping[str, object]) -> tuple[str, ...]:
    """Return the ordered read-only scopes required by enabled capabilities."""
    scopes = list(BASE_SCOPES)
    enabled = enabled_capabilities(options)
    for capability_id, spec in CAPABILITIES.items():
        if capability_id not in enabled:
            continue
        for scope in spec.required_scopes:
            if scope not in scopes:
                scopes.append(scope)
    return tuple(scopes)


def validate_granted_scopes(
    granted: Iterable[str], options: Mapping[str, object]
) -> ScopeGrant:
    """Validate a token and report enabled capabilities it can provide."""
    granted_scopes = frozenset(granted)
    unsupported_scopes = granted_scopes - SUPPORTED_SCOPES
    if unsupported_scopes:
        raise UnsupportedScopeError("Google OAuth returned an unsupported scope")

    enabled = enabled_capabilities(options)
    available = frozenset(
        capability_id
        for capability_id in enabled
        if CAPABILITIES[capability_id].required_scopes <= granted_scopes
    )
    missing_optional_scopes = frozenset(
        scope
        for capability_id in enabled
        for scope in CAPABILITIES[capability_id].required_scopes - granted_scopes
        if scope not in BASE_SCOPES
    )
    return ScopeGrant(
        granted_scopes=granted_scopes,
        baseline_valid=set(BASE_SCOPES) <= granted_scopes,
        enabled_capabilities=enabled,
        available_capabilities=available,
        missing_optional_scopes=missing_optional_scopes,
    )
