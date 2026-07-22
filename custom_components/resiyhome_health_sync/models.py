"""Typed normalized health data contracts."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import cast


def _empty_mapping[T]() -> Mapping[str, T]:
    return cast(Mapping[str, T], MappingProxyType({}))


class SourceKind(StrEnum):
    """Source classification for a normalized metric."""

    FITBIT = "fitbit"
    APPLE_FALLBACK = "apple_fallback"
    MIXED = "mixed"
    UNAVAILABLE = "unavailable"


@dataclass(slots=True, frozen=True)
class WorkoutSummary:
    """Normalized summary of one completed workout."""

    activity_type: str
    duration_minutes: float
    start: datetime | None = None
    end: datetime | None = None
    active_energy_kcal: float | None = None


@dataclass(slots=True, frozen=True)
class ExpandedDailyMetrics:
    """Normalized aggregate Google Health metrics for one local calendar day."""

    active_zone_minutes: Mapping[str, float] = field(default_factory=_empty_mapping)
    vo2_max: float | None = None
    vo2_estimated: bool | None = None
    cardio_fitness_level: str | None = None
    oxygen_average: float | None = None
    oxygen_lower_bound: float | None = None
    oxygen_upper_bound: float | None = None
    oxygen_standard_deviation: float | None = None
    daily_respiratory_rate: float | None = None
    sleep_respiratory_rates: Mapping[str, float] = field(default_factory=_empty_mapping)
    sleep_respiratory_standard_deviation: float | None = None
    sleep_respiratory_signal_to_noise: float | None = None
    floors: int | None = None
    sedentary_minutes: float | None = None
    heart_zone_minutes: Mapping[str, float] = field(default_factory=_empty_mapping)
    heart_zone_thresholds: Mapping[str, tuple[int, int]] = field(default_factory=_empty_mapping)
    heart_zone_calories: Mapping[str, float] = field(default_factory=_empty_mapping)
    weight_kg: float | None = None

    def __post_init__(self) -> None:
        for name in (
            "active_zone_minutes",
            "sleep_respiratory_rates",
            "heart_zone_minutes",
            "heart_zone_thresholds",
            "heart_zone_calories",
        ):
            object.__setattr__(self, name, MappingProxyType(dict(getattr(self, name))))


@dataclass(slots=True, frozen=True)
class DailySummary:
    """Normalized health metrics for one local calendar day."""

    date: date
    steps: int | None = None
    fitbit_steps: int | None = None
    distance_m: float | None = None
    active_energy_kcal: float | None = None
    exercise_minutes: float | None = None
    sleep_minutes: float | None = None
    sleep_stages: Mapping[str, float] = field(default_factory=_empty_mapping)
    resting_heart_rate: float | None = None
    average_heart_rate: float | None = None
    minimum_heart_rate: float | None = None
    maximum_heart_rate: float | None = None
    hrv_ms: float | None = None
    workouts: tuple[WorkoutSummary, ...] = ()
    expanded: ExpandedDailyMetrics = field(default_factory=ExpandedDailyMetrics)
    source: SourceKind = SourceKind.UNAVAILABLE
    complete: bool = False
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "sleep_stages", MappingProxyType(dict(self.sleep_stages)))


@dataclass(slots=True)
class CoordinatorSnapshot:
    """Latest coordinator state, including synchronization health."""

    current_day: DailySummary | None = None
    last_success: datetime | None = None
    last_attempt: datetime | None = None
    authorization_healthy: bool = True
    backfill_cursor: date | None = None
    backfill_complete: bool = False
    expanded_backfill_cursor: date | None = None
    expanded_backfill_complete: bool = False
    latest_weight_kg: float | None = None
    latest_weight_at: date | None = None
