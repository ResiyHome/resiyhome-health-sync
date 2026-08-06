"""Tests for pure expanded Google Health daily metric normalization."""

from datetime import UTC, date, datetime, timedelta
from math import inf, nan
from types import MappingProxyType
from typing import Any

import pytest

from custom_components.resiyhome_health_sync.expanded_metrics import (
    normalize_expanded_day,
    normalize_hydration_ml,
    normalize_nutrition_energy,
)
from custom_components.resiyhome_health_sync.models import DailySummary, ExpandedDailyMetrics

DAY = date(2042, 7, 21)


def _daily_date() -> dict[str, int]:
    return {"year": DAY.year, "month": DAY.month, "day": DAY.day}


def _sample_time(
    physical_time: str = "2042-07-21T07:00:00Z", utc_offset: str = "0s"
) -> dict[str, str]:
    return {"physicalTime": physical_time, "utcOffset": utc_offset}


def _nutrition_point(
    kcal: object,
    *,
    day: date = DAY,
    hour: int = 7,
    duration_minutes: int = 30,
    utc_offset_seconds: int = 0,
    data_point_name: str = "users/private/dataTypes/nutrition-log/dataPoints/nutrition-record",
) -> dict[str, object]:
    return {
        "dataPointName": data_point_name,
        "nutritionLog": {
            "interval": _session_interval(
                day,
                hour,
                duration_minutes=duration_minutes,
                utc_offset_seconds=utc_offset_seconds,
            ),
            "energy": {"kcal": kcal, "userProvidedUnit": "KILOCALORIE"},
        },
    }


def _hydration_point(
    milliliters: object,
    *,
    day: date = DAY,
    hour: int = 7,
    duration_minutes: int = 30,
    utc_offset_seconds: int = 0,
    data_point_name: str = "users/private/dataTypes/hydration-log/dataPoints/hydration-record",
) -> dict[str, object]:
    return {
        "dataPointName": data_point_name,
        "hydrationLog": {
            "interval": _session_interval(
                day,
                hour,
                duration_minutes=duration_minutes,
                utc_offset_seconds=utc_offset_seconds,
            ),
            "amountConsumed": {
                "milliliters": milliliters,
                "userProvidedUnit": "MILLILITER",
            },
        },
    }


@pytest.fixture
def reconciled_nutrition_hydration_points() -> tuple[
    dict[str, object], dict[str, object]
]:
    """Provide canonical ReconciledDataPoint nutrition and hydration records."""
    return _nutrition_point(820.0), _hydration_point(900.0)


def _civil_time(value: date, hour: int, *, nanos: int = 0) -> dict[str, object]:
    return {
        "date": {"year": value.year, "month": value.month, "day": value.day},
        "time": {"hours": hour, "nanos": nanos},
    }


def _session_interval(
    value: date,
    hour: int,
    *,
    duration_minutes: int,
    utc_offset_seconds: int,
) -> dict[str, object]:
    local_start = datetime(value.year, value.month, value.day, hour)
    local_end = local_start + timedelta(minutes=duration_minutes)
    start = (local_start - timedelta(seconds=utc_offset_seconds)).replace(tzinfo=UTC)
    end = (local_end - timedelta(seconds=utc_offset_seconds)).replace(tzinfo=UTC)
    return {
        "startTime": start.isoformat().replace("+00:00", "Z"),
        "startUtcOffset": f"{utc_offset_seconds}s",
        "endTime": end.isoformat().replace("+00:00", "Z"),
        "endUtcOffset": f"{utc_offset_seconds}s",
        "civilStartTime": _civil_time(value, hour),
        "civilEndTime": {
            "date": {
                "year": local_end.year,
                "month": local_end.month,
                "day": local_end.day,
            },
            "time": {
                "hours": local_end.hour,
                "minutes": local_end.minute,
            },
        },
    }


def _rollup(value_key: str, value: object) -> dict[str, object]:
    return {
        "civilStartTime": _civil_time(DAY, 0),
        "civilEndTime": _civil_time(DAY + timedelta(days=1), 0),
        value_key: value,
    }


def _interval(
    start_hour: int,
    end_hour: int,
) -> dict[str, object]:
    start = datetime(DAY.year, DAY.month, DAY.day, start_hour, tzinfo=UTC)
    end = datetime(DAY.year, DAY.month, DAY.day, end_hour, tzinfo=UTC)
    return {
        "startTime": start.isoformat().replace("+00:00", "Z"),
        "startUtcOffset": "0s",
        "endTime": end.isoformat().replace("+00:00", "Z"),
        "endUtcOffset": "0s",
        "civilStartTime": _civil_time(DAY, start_hour),
        "civilEndTime": _civil_time(DAY, end_hour),
    }


def _direct() -> dict[str, list[dict[str, Any]]]:
    return {
        "daily-vo2-max": [
            {
                "dailyVo2Max": {
                    "date": _daily_date(),
                    "estimated": False,
                    "cardioFitnessLevel": "GOOD",
                    "vo2Max": 42.5,
                }
            }
        ],
        "daily-oxygen-saturation": [
            {
                "dailyOxygenSaturation": {
                    "date": _daily_date(),
                    "averagePercentage": 96.2,
                    "lowerBoundPercentage": 95.1,
                    "upperBoundPercentage": 97.3,
                    "standardDeviationPercentage": 0.4,
                }
            }
        ],
        "daily-respiratory-rate": [
            {
                "dailyRespiratoryRate": {
                    "date": _daily_date(),
                    "breathsPerMinute": 15.4,
                }
            }
        ],
        "respiratory-rate-sleep-summary": [
            {
                "respiratoryRateSleepSummary": {
                    "sampleTime": _sample_time(),
                    "deepSleepStats": {"breathsPerMinute": 14.1},
                    "lightSleepStats": {"breathsPerMinute": 15.2},
                    "remSleepStats": {"breathsPerMinute": 14.6},
                    "fullSleepStats": {
                        "breathsPerMinute": 14.8,
                        "standardDeviation": 0.7,
                        "signalToNoise": 3.2,
                    },
                }
            }
        ],
        "daily-heart-rate-zones": [
            {
                "dailyHeartRateZones": {
                    "date": _daily_date(),
                    "heartRateZones": [
                        {
                            "heartRateZoneType": "LIGHT",
                            "minBeatsPerMinute": "94",
                            "maxBeatsPerMinute": "112",
                        },
                        {
                            "heartRateZoneType": "MODERATE",
                            "minBeatsPerMinute": "113",
                            "maxBeatsPerMinute": "132",
                        },
                        {
                            "heartRateZoneType": "VIGOROUS",
                            "minBeatsPerMinute": "133",
                            "maxBeatsPerMinute": "159",
                        },
                        {
                            "heartRateZoneType": "PEAK",
                            "minBeatsPerMinute": "160",
                            "maxBeatsPerMinute": "190",
                        },
                    ],
                }
            }
        ],
        "weight": [{"weight": {"sampleTime": _sample_time(), "weightGrams": 80500.0}}],
        "body-fat": [
            {"bodyFat": {"sampleTime": _sample_time(), "percentage": 21.4}}
        ],
        "height": [
            {
                "height": {
                    "sampleTime": _sample_time(),
                    "heightMillimeters": 1778.0,
                }
            }
        ],
    }


def _rollups() -> dict[str, list[dict[str, Any]]]:
    return {
        "active-zone-minutes": [
            _rollup(
                "activeZoneMinutes",
                {
                    "sumInFatBurnHeartZone": "12",
                    "sumInCardioHeartZone": "8",
                    "sumInPeakHeartZone": "4",
                },
            )
        ],
        "floors": [_rollup("floors", {"countSum": "7"})],
        "sedentary-period": [_rollup("sedentaryPeriod", {"durationSum": "28800s"})],
        "time-in-heart-rate-zone": [
            _rollup(
                "timeInHeartRateZone",
                {"timeInHeartRateZones": [{"heartRateZone": "VIGOROUS", "duration": "1410s"}]},
            )
        ],
        "calories-in-heart-rate-zone": [
            _rollup(
                "caloriesInHeartRateZone",
                {"caloriesInHeartRateZones": [{"heartRateZone": "VIGOROUS", "kcal": 184.2}]},
            )
        ],
    }


def test_canonical_reconcile_nutrition_and_hydration_normalize_only_scalars(
    reconciled_nutrition_hydration_points: tuple[
        dict[str, object], dict[str, object]
    ],
) -> None:
    """Canonical reconcile records reduce to scalars without retaining identifiers."""
    nutrition, hydration = reconciled_nutrition_hydration_points

    energy = normalize_nutrition_energy([nutrition], DAY)
    volume = normalize_hydration_ml([hydration], DAY)

    assert energy == 820.0
    assert volume == 900.0
    retained = repr((energy, volume))
    assert "nutrition-record" not in retained
    assert "hydration-record" not in retained


def test_sums_distinct_current_day_nutrition_and_hydration_reconcile_records() -> None:
    """Every returned reconcile record contributes its allowlisted scalar."""
    nutrition = [
        _nutrition_point(
            820.0,
            data_point_name=(
                "users/private/dataTypes/nutrition-log/dataPoints/nutrition-record-1"
            ),
        ),
        _nutrition_point(
            1000.0,
            data_point_name=(
                "users/private/dataTypes/nutrition-log/dataPoints/nutrition-record-2"
            ),
        ),
    ]
    hydration = [
        _hydration_point(
            900.0,
            data_point_name=(
                "users/private/dataTypes/hydration-log/dataPoints/hydration-record-1"
            ),
        ),
        _hydration_point(
            1200.0,
            data_point_name=(
                "users/private/dataTypes/hydration-log/dataPoints/hydration-record-2"
            ),
        ),
    ]

    assert normalize_nutrition_energy(nutrition, DAY) == 1820.0
    assert normalize_hydration_ml(hydration, DAY) == 2100.0


def test_nutrition_aggregates_preserve_true_zero_and_empty_days() -> None:
    """A validated zero is data while a day with no matching rows is unavailable."""
    assert normalize_nutrition_energy([_nutrition_point(0.0)], DAY) == 0.0
    assert normalize_hydration_ml([_hydration_point(0.0)], DAY) == 0.0
    assert normalize_nutrition_energy([], DAY) is None
    assert normalize_hydration_ml([], DAY) is None


def test_nutrition_aggregates_use_session_start_local_dates() -> None:
    """Session civil-start semantics select the day despite adjacent UTC dates."""
    nutrition = [
        _nutrition_point(
            600.0,
            hour=22,
            utc_offset_seconds=-14_400,
        ),
        _nutrition_point(
            "private malformed value",
            day=DAY + timedelta(days=1),
        ),
    ]
    hydration = [
        _hydration_point(
            750.0,
            hour=1,
            utc_offset_seconds=7_200,
        ),
        _hydration_point(
            "private malformed value",
            day=DAY - timedelta(days=1),
        ),
    ]

    assert normalize_nutrition_energy(nutrition, DAY) == 600.0
    assert normalize_hydration_ml(hydration, DAY) == 750.0


@pytest.mark.parametrize(
    "malformed",
    [
        {
            "nutritionLog": {
                "interval": _session_interval(
                    DAY, 7, duration_minutes=30, utc_offset_seconds=0
                ),
                "energy": {"kcal": True},
            }
        },
        {
            "nutritionLog": {
                "interval": _session_interval(
                    DAY, 7, duration_minutes=30, utc_offset_seconds=0
                ),
                "energy": {"kcal": -1.0},
            }
        },
        {
            "nutritionLog": {
                "interval": _session_interval(
                    DAY, 7, duration_minutes=30, utc_offset_seconds=0
                ),
                "energy": {"kcal": nan},
            }
        },
        {
            "nutritionLog": {
                "interval": _session_interval(
                    DAY, 7, duration_minutes=30, utc_offset_seconds=0
                ),
                "energy": {"calories": 100.0},
                "kcal": 100.0,
            }
        },
    ],
)
def test_any_matching_malformed_nutrition_row_rejects_the_entire_aggregate(
    malformed: dict[str, object],
) -> None:
    """A valid row cannot hide a malformed current-day nutrition log."""
    assert (
        normalize_nutrition_energy([_nutrition_point(500.0), malformed], DAY)
        is None
    )


@pytest.mark.parametrize(
    "malformed",
    [
        {
            "hydrationLog": {
                "interval": _session_interval(
                    DAY, 7, duration_minutes=30, utc_offset_seconds=0
                ),
                "amountConsumed": {"milliliters": False},
            }
        },
        {
            "hydrationLog": {
                "interval": _session_interval(
                    DAY, 7, duration_minutes=30, utc_offset_seconds=0
                ),
                "amountConsumed": {"milliliters": -1.0},
            }
        },
        {
            "hydrationLog": {
                "interval": _session_interval(
                    DAY, 7, duration_minutes=30, utc_offset_seconds=0
                ),
                "amountConsumed": {"milliliters": inf},
            }
        },
        {
            "hydrationLog": {
                "interval": _session_interval(
                    DAY, 7, duration_minutes=30, utc_offset_seconds=0
                ),
                "amountConsumed": {"liters": 1.0},
                "milliliters": 1000.0,
            }
        },
    ],
)
def test_any_matching_malformed_hydration_row_rejects_the_entire_aggregate(
    malformed: dict[str, object],
) -> None:
    """Hydration traverses only amountConsumed.milliliters and fails closed."""
    assert normalize_hydration_ml([_hydration_point(500.0), malformed], DAY) is None


@pytest.mark.parametrize(
    ("normalizer", "point_factory", "payload_key"),
    [
        (normalize_nutrition_energy, _nutrition_point, "nutritionLog"),
        (normalize_hydration_ml, _hydration_point, "hydrationLog"),
    ],
)
@pytest.mark.parametrize(
    "case",
    [
        "missing_interval",
        "missing_start",
        "missing_start_offset",
        "missing_end",
        "malformed_end",
        "missing_end_offset",
        "reversed_boundaries",
        "mismatched_civil_start",
        "mismatched_civil_end",
    ],
)
def test_nutrition_session_intervals_fail_closed(
    normalizer, point_factory, payload_key: str, case: str
) -> None:
    """Every candidate row needs coherent required and civil session boundaries."""
    malformed = point_factory(100.0)
    payload = malformed[payload_key]
    interval = payload["interval"]
    if case == "missing_interval":
        payload.pop("interval")
    elif case == "missing_start":
        interval.pop("startTime")
    elif case == "missing_start_offset":
        interval.pop("startUtcOffset")
    elif case == "missing_end":
        interval.pop("endTime")
    elif case == "malformed_end":
        interval["endTime"] = "not-a-timestamp"
    elif case == "missing_end_offset":
        interval.pop("endUtcOffset")
    elif case == "reversed_boundaries":
        interval["endTime"] = "2042-07-21T06:00:00Z"
    elif case == "mismatched_civil_start":
        interval["civilStartTime"] = _civil_time(DAY + timedelta(days=1), 7)
    else:
        interval["civilEndTime"] = _civil_time(DAY + timedelta(days=1), 7)

    assert normalizer([point_factory(500.0), malformed], DAY) is None


@pytest.mark.parametrize(
    ("normalizer", "point_factory"),
    [
        (normalize_nutrition_energy, _nutrition_point),
        (normalize_hydration_ml, _hydration_point),
    ],
)
def test_nutrition_session_inclusion_uses_civil_start_day(
    normalizer, point_factory
) -> None:
    """Cross-midnight sessions follow start day and adjacent start days are ignored."""
    points = [
        point_factory(500.0, hour=23, duration_minutes=90),
        point_factory(
            "private malformed value",
            day=DAY - timedelta(days=1),
            hour=23,
            duration_minutes=90,
        ),
        point_factory("private malformed value", day=DAY + timedelta(days=1)),
    ]

    assert normalizer(points, DAY) == 500.0


@pytest.mark.parametrize(
    ("normalizer", "points"),
    [
        (
            normalize_nutrition_energy,
            [_nutrition_point(1e308), _nutrition_point(1e308)],
        ),
        (
            normalize_hydration_ml,
            [_hydration_point(1e308), _hydration_point(1e308)],
        ),
    ],
)
def test_nutrition_aggregate_overflow_is_unavailable(normalizer, points) -> None:
    """Finite rows cannot produce a retained non-finite aggregate."""
    assert normalizer(points, DAY) is None


def test_normalizes_documented_expanded_daily_metrics() -> None:
    """Each supported direct and daily-rollup shape becomes immutable daily data."""
    result = normalize_expanded_day(DAY, _direct(), _rollups(), include_weight=True)

    assert result.active_zone_minutes == {"fat_burn": 12.0, "cardio": 8.0, "peak": 4.0}
    assert result.vo2_max == 42.5
    assert result.vo2_estimated is False
    assert result.cardio_fitness_level == "GOOD"
    assert result.oxygen_average == 96.2
    assert result.oxygen_lower_bound == 95.1
    assert result.oxygen_upper_bound == 97.3
    assert result.oxygen_standard_deviation == 0.4
    assert result.daily_respiratory_rate == 15.4
    assert result.sleep_respiratory_rates == {
        "deep": 14.1,
        "light": 15.2,
        "rem": 14.6,
        "full": 14.8,
    }
    assert result.sleep_respiratory_standard_deviation == 0.7
    assert result.sleep_respiratory_signal_to_noise == 3.2
    assert result.floors == 7
    assert result.sedentary_minutes == 480.0
    assert result.heart_zone_minutes["vigorous"] == 23.5
    assert result.heart_zone_thresholds["vigorous"] == (133, 159)
    assert result.heart_zone_calories["vigorous"] == 184.2
    assert result.weight_kg == 80.5
    assert result.body_fat_percentage == 21.4
    assert result.height_m == 1.778


def test_current_day_uses_reconciled_intervals_when_daily_rollups_are_empty() -> None:
    """Incomplete current days remain available before Google publishes daily rollups."""
    direct = _direct()
    direct.update(
        {
            "active-zone-minutes": [
                {
                    "activeZoneMinutes": {
                        "interval": _interval(8, 9),
                        "heartRateZone": "FAT_BURN",
                        "activeZoneMinutes": "5",
                    }
                },
                {
                    "activeZoneMinutes": {
                        "interval": _interval(9, 10),
                        "heartRateZone": "CARDIO",
                        "activeZoneMinutes": "4",
                    }
                },
            ],
            "floors": [
                {"floors": {"interval": _interval(10, 11), "count": "3"}},
                {"floors": {"interval": _interval(11, 12), "count": "2"}},
            ],
            "sedentary-period": [
                {"sedentaryPeriod": {"interval": _interval(12, 13)}},
                {"sedentaryPeriod": {"interval": _interval(14, 16)}},
            ],
            "time-in-heart-rate-zone": [
                {
                    "timeInHeartRateZone": {
                        "interval": _interval(16, 17),
                        "heartRateZoneType": "MODERATE",
                    }
                },
                {
                    "timeInHeartRateZone": {
                        "interval": _interval(17, 19),
                        "heartRateZoneType": "VIGOROUS",
                    }
                },
            ],
        }
    )

    result = normalize_expanded_day(DAY, direct, {}, include_weight=False)

    assert result.active_zone_minutes == {
        "fat_burn": 5.0,
        "cardio": 4.0,
    }
    assert result.floors == 5
    assert result.sedentary_minutes == 180.0
    assert result.heart_zone_minutes == {
        "moderate": 60.0,
        "vigorous": 120.0,
    }


def test_daily_rollups_take_precedence_over_reconciled_current_intervals() -> None:
    """Published daily aggregates remain authoritative after Google creates them."""
    direct = {
        "floors": [{"floors": {"interval": _interval(10, 11), "count": "3"}}],
    }

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=False)

    assert result.floors == 7


def test_multi_day_streams_are_filtered_before_single_point_validation() -> None:
    """Direct and rollup collections may contain one point for each requested day."""
    previous_day = DAY - timedelta(days=1)
    direct = {
        "daily-vo2-max": [
            {
                "dailyVo2Max": {
                    "date": {
                        "year": previous_day.year,
                        "month": previous_day.month,
                        "day": previous_day.day,
                    },
                    "vo2Max": 41.0,
                }
            },
            _direct()["daily-vo2-max"][0],
        ]
    }
    rollups = {
        "floors": [
            {
                "civilStartTime": _civil_time(previous_day, 0),
                "civilEndTime": _civil_time(DAY, 0),
                "floors": {"countSum": "6"},
            },
            _rollups()["floors"][0],
        ]
    }

    previous = normalize_expanded_day(
        previous_day, direct, rollups, include_weight=False
    )
    current = normalize_expanded_day(DAY, direct, rollups, include_weight=False)

    assert (previous.vo2_max, previous.floors) == (41.0, 6)
    assert (current.vo2_max, current.floors) == (42.5, 7)


def test_daily_rollup_accepts_documented_end_of_day_boundary() -> None:
    """Google may return 23:59:59 as the civil end of a one-day rollup."""
    rollups = {
        "floors": [
            {
                "civilStartTime": _civil_time(DAY, 0),
                "civilEndTime": {
                    "date": _daily_date(),
                    "time": {"hours": 23, "minutes": 59, "seconds": 59},
                },
                "floors": {"countSum": "7"},
            }
        ]
    }

    result = normalize_expanded_day(DAY, {}, rollups, include_weight=False)

    assert result.floors == 7


def test_expanded_model_is_immutable_and_daily_summary_defaults_to_it() -> None:
    """Expanded contracts cannot share mutable mappings or break old summary construction."""
    metrics = ExpandedDailyMetrics(active_zone_minutes={"fat_burn": 12.0})

    assert isinstance(metrics.active_zone_minutes, MappingProxyType)
    with pytest.raises(TypeError):
        metrics.active_zone_minutes["cardio"] = 8.0  # type: ignore[index]
    with pytest.raises(AttributeError):
        metrics.vo2_max = 42.5  # type: ignore[misc]
    assert DailySummary(date=DAY).expanded == ExpandedDailyMetrics()


def test_explicit_zero_is_preserved_and_missing_groups_are_unavailable() -> None:
    """Returned zeros remain values while absent metric groups retain unavailable state."""
    rollups = _rollups()
    rollups["active-zone-minutes"] = [
        _rollup(
            "activeZoneMinutes",
            {
                "sumInFatBurnHeartZone": "0",
                "sumInCardioHeartZone": "0",
                "sumInPeakHeartZone": "0",
            },
        )
    ]
    rollups["floors"] = [_rollup("floors", {"countSum": "0"})]

    result = normalize_expanded_day(DAY, {}, rollups, include_weight=False)

    assert result.active_zone_minutes == {"fat_burn": 0.0, "cardio": 0.0, "peak": 0.0}
    assert result.floors == 0
    assert result.vo2_max is None
    assert result.oxygen_average is None
    assert result.sleep_respiratory_rates == {}


@pytest.mark.parametrize(
    ("group", "value"),
    [
        ("floors", {"countSum": "-1"}),
        ("sedentary-period", {"durationSum": "not-a-duration"}),
        (
            "calories-in-heart-rate-zone",
            {"caloriesInHeartRateZones": [{"heartRateZone": "VIGOROUS", "kcal": nan}]},
        ),
    ],
)
def test_invalid_rollup_group_does_not_remove_unrelated_valid_data(
    group: str, value: object
) -> None:
    """Negative, non-finite, and malformed rollups fail closed only for their group."""
    rollups = _rollups()
    key = {
        "floors": "floors",
        "sedentary-period": "sedentaryPeriod",
        "calories-in-heart-rate-zone": "caloriesInHeartRateZone",
    }[group]
    rollups[group] = [_rollup(key, value)]

    result = normalize_expanded_day(DAY, _direct(), rollups, include_weight=False)

    assert result.active_zone_minutes["fat_burn"] == 12.0
    assert result.vo2_max == 42.5
    assert getattr(
        result,
        {
            "floors": "floors",
            "sedentary-period": "sedentary_minutes",
            "calories-in-heart-rate-zone": "heart_zone_calories",
        }[group],
    ) in (None, {})


def test_rollup_rejects_nonzero_midnight_nanos() -> None:
    """A daily rollup boundary must be midnight at exact nanosecond precision."""
    rollups = _rollups()
    rollups["active-zone-minutes"][0]["civilStartTime"] = _civil_time(
        DAY, 0, nanos=1
    )

    result = normalize_expanded_day(DAY, _direct(), rollups, include_weight=False)

    assert result.active_zone_minutes == {}
    assert result.vo2_max == 42.5
    assert result.floors == 7


def test_rollup_accepts_civil_boundaries_with_omitted_midnight_time() -> None:
    """Google may omit the optional CivilDateTime time field for midnight."""
    rollups = _rollups()
    rollups["floors"][0]["civilStartTime"] = {"date": _daily_date()}
    rollups["floors"][0]["civilEndTime"] = {
        "date": {
            "year": (DAY + timedelta(days=1)).year,
            "month": (DAY + timedelta(days=1)).month,
            "day": (DAY + timedelta(days=1)).day,
        }
    }

    result = normalize_expanded_day(DAY, _direct(), rollups, include_weight=False)

    assert result.floors == 7


def test_rollup_rejects_window_from_wrong_local_day() -> None:
    """An exact daily window for another civil day cannot populate this day."""
    rollups = _rollups()
    rollups["floors"][0]["civilStartTime"] = _civil_time(DAY - timedelta(days=1), 0)
    rollups["floors"][0]["civilEndTime"] = _civil_time(DAY, 0)

    result = normalize_expanded_day(DAY, _direct(), rollups, include_weight=False)

    assert result.floors is None
    assert result.active_zone_minutes["fat_burn"] == 12.0


def test_protobuf_duration_rejects_one_nanosecond_above_maximum() -> None:
    """Duration bounds are enforced before nanoseconds can be rounded away."""
    rollups = _rollups()
    rollups["sedentary-period"] = [
        _rollup(
            "sedentaryPeriod",
            {"durationSum": "315576000000.000000001s"},
        )
    ]

    result = normalize_expanded_day(DAY, _direct(), rollups, include_weight=False)

    assert result.sedentary_minutes is None
    assert result.floors == 7


def test_oversized_duration_only_makes_its_group_unavailable() -> None:
    """An oversized seconds field cannot abort unrelated metric normalization."""
    rollups = _rollups()
    rollups["sedentary-period"] = [
        _rollup("sedentaryPeriod", {"durationSum": f"{'9' * 5_000}s"})
    ]

    result = normalize_expanded_day(DAY, _direct(), rollups, include_weight=False)

    assert result.sedentary_minutes is None
    assert result.floors == 7
    assert result.vo2_max == 42.5


@pytest.mark.parametrize(
    "duration", ["315576000000s", "315576000000.000000000s"]
)
def test_protobuf_duration_supports_exact_maximum(duration: str) -> None:
    """Both valid JSON encodings of the exact duration maximum remain accepted."""
    rollups = _rollups()
    rollups["sedentary-period"] = [
        _rollup("sedentaryPeriod", {"durationSum": duration})
    ]

    result = normalize_expanded_day(DAY, _direct(), rollups, include_weight=False)

    assert result.sedentary_minutes == 5_259_600_000.0


def test_duplicate_and_unknown_zone_rows_fail_closed_by_group() -> None:
    """Duplicate or unknown zone rows cannot form partial zone mappings."""
    direct = _direct()
    direct["daily-heart-rate-zones"][0]["dailyHeartRateZones"]["heartRateZones"].append(  # type: ignore[index]
        {
            "heartRateZoneType": "VIGOROUS",
            "minBeatsPerMinute": "100",
            "maxBeatsPerMinute": "120",
        }
    )
    rollups = _rollups()
    rollups["time-in-heart-rate-zone"] = [
        _rollup(
            "timeInHeartRateZone",
            {"timeInHeartRateZones": [{"heartRateZone": "UNKNOWN", "duration": "60s"}]},
        )
    ]

    result = normalize_expanded_day(DAY, direct, rollups, include_weight=False)

    assert result.heart_zone_thresholds == {}
    assert result.heart_zone_minutes == {}
    assert result.heart_zone_calories["vigorous"] == 184.2
    assert result.oxygen_average == 96.2


def test_reversed_bpm_thresholds_fail_closed_without_removing_other_groups() -> None:
    """A zone minimum above its maximum invalidates only the threshold group."""
    direct = _direct()
    zones = direct["daily-heart-rate-zones"][0]["dailyHeartRateZones"][
        "heartRateZones"
    ]
    zones[2]["minBeatsPerMinute"] = "160"  # type: ignore[index]

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=False)

    assert result.heart_zone_thresholds == {}
    assert result.heart_zone_minutes["vigorous"] == 23.5
    assert result.oxygen_average == 96.2


def test_out_of_range_oxygen_and_invalid_direct_shapes_fail_closed_by_group() -> None:
    """Daily metric ranges and required direct shapes are independently strict."""
    direct = _direct()
    direct["daily-oxygen-saturation"][0]["dailyOxygenSaturation"]["averagePercentage"] = 100.1  # type: ignore[index]
    direct["daily-respiratory-rate"][0]["dailyRespiratoryRate"].pop("date")  # type: ignore[index]
    direct["respiratory-rate-sleep-summary"][0]["respiratoryRateSleepSummary"]["fullSleepStats"] = {  # type: ignore[index]
        "breathsPerMinute": -1
    }

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=False)

    assert result.oxygen_average is None
    assert result.daily_respiratory_rate is None
    assert result.sleep_respiratory_rates == {}
    assert result.vo2_max == 42.5


def test_body_measurements_are_ignored_until_explicitly_enabled() -> None:
    """The pure layer must not retain body measurements when the caller opts out."""
    result = normalize_expanded_day(DAY, _direct(), _rollups(), include_weight=False)

    assert result.weight_kg is None
    assert result.body_fat_percentage is None
    assert result.height_m is None


@pytest.mark.parametrize("percentage", [0.0, 21.4, 100.0])
def test_body_fat_accepts_documented_percentage_range(percentage: float) -> None:
    """Body-fat endpoints and an ordinary finite percentage remain available."""
    direct = _direct()
    direct["body-fat"][0]["bodyFat"]["percentage"] = percentage

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=True)

    assert result.body_fat_percentage == percentage


@pytest.mark.parametrize(
    "percentage",
    [-0.1, 100.1, nan, inf, -inf, True, "21.4"],
)
def test_body_fat_rejects_out_of_range_or_non_finite_values(
    percentage: object,
) -> None:
    """Malformed body fat cannot displace other valid body measurements."""
    direct = _direct()
    direct["body-fat"][0]["bodyFat"]["percentage"] = percentage

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=True)

    assert result.body_fat_percentage is None
    assert result.weight_kg == 80.5
    assert result.height_m == 1.778


@pytest.mark.parametrize(
    "millimeters",
    [0.0, -0.1, nan, inf, -inf, True, "1778"],
)
def test_height_rejects_non_positive_or_non_finite_values(
    millimeters: object,
) -> None:
    """Height must be a finite positive numeric measurement."""
    direct = _direct()
    direct["height"][0]["height"]["heightMillimeters"] = millimeters

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=True)

    assert result.height_m is None
    assert result.weight_kg == 80.5
    assert result.body_fat_percentage == 21.4


def test_height_converts_millimeters_to_meters_without_rounding() -> None:
    """The normalized stored value retains the source measurement precision."""
    direct = _direct()
    direct["height"][0]["height"]["heightMillimeters"] = 1778.123456

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=True)

    assert result.height_m == 1.778123456


@pytest.mark.parametrize(
    ("data_type", "payload_key", "value_field"),
    [
        ("body-fat", "bodyFat", "body_fat_percentage"),
        ("height", "height", "height_m"),
    ],
)
def test_body_fat_and_height_require_timestamps_independently(
    data_type: str,
    payload_key: str,
    value_field: str,
) -> None:
    """A missing timestamp invalidates only the affected measurement stream."""
    direct = _direct()
    direct[data_type][0][payload_key].pop("sampleTime")

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=True)

    assert getattr(result, value_field) is None
    assert result.weight_kg == 80.5
    if data_type == "body-fat":
        assert result.height_m == 1.778
    else:
        assert result.body_fat_percentage == 21.4


def test_body_fat_and_height_ignore_points_outside_the_requested_day() -> None:
    """Adjacent-day body points cannot populate a requested backfill day."""
    direct = _direct()
    direct["body-fat"][0]["bodyFat"]["sampleTime"] = _sample_time(
        "2042-07-22T07:00:00Z"
    )
    direct["height"][0]["height"]["sampleTime"] = _sample_time(
        "2042-07-20T07:00:00Z"
    )

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=True)

    assert result.body_fat_percentage is None
    assert result.height_m is None
    assert result.weight_kg == 80.5


def test_latest_body_measurements_are_selected_independently() -> None:
    """Each body stream selects its own latest valid timestamp."""
    direct = _direct()
    direct["weight"].append(
        {
            "weight": {
                "sampleTime": _sample_time("2042-07-21T10:00:00Z"),
                "weightGrams": 80000.0,
            }
        }
    )
    direct["body-fat"].append(
        {
            "bodyFat": {
                "sampleTime": _sample_time("2042-07-21T11:00:00Z"),
                "percentage": 20.8,
            }
        }
    )
    direct["height"].append(
        {
            "height": {
                "sampleTime": _sample_time("2042-07-21T12:00:00Z"),
                "heightMillimeters": 1779.5,
            }
        }
    )

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=True)

    assert result.weight_kg == 80.0
    assert result.body_fat_percentage == 20.8
    assert result.height_m == 1.7795


def test_latest_valid_body_measurements_ignore_later_malformed_points() -> None:
    """A malformed newer point cannot displace each stream's earlier valid value."""
    direct = _direct()
    direct["weight"].append(
        {
            "weight": {
                "sampleTime": _sample_time("2042-07-21T10:00:00Z"),
                "weightGrams": nan,
            }
        }
    )
    direct["body-fat"].append(
        {
            "bodyFat": {
                "percentage": 20.8,
            }
        }
    )
    direct["height"].append(
        {
            "height": {
                "sampleTime": _sample_time("2042-07-21T12:00:00Z"),
                "heightMillimeters": 0.0,
            }
        }
    )

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=True)

    assert result.weight_kg == 80.5
    assert result.body_fat_percentage == 21.4
    assert result.height_m == 1.778


def test_latest_valid_weight_sample_is_used_when_multiple_exist() -> None:
    """Sample metric groups retain the latest API timestamp instead of rejecting valid rows."""
    direct = _direct()
    direct["weight"].append(
        {
            "weight": {
                "sampleTime": {"physicalTime": "2042-07-21T12:00:00Z", "utcOffset": "0s"},
                "weightGrams": 80000.0,
            }
        }
    )

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=True)

    assert result.weight_kg == 80.0


def test_wrong_local_day_sleep_respiratory_sample_is_ignored() -> None:
    """A sleep sample from an adjacent local day cannot populate the requested day."""
    direct = _direct()
    summary = direct["respiratory-rate-sleep-summary"][0]["respiratoryRateSleepSummary"]
    summary["sampleTime"] = _sample_time("2042-07-22T04:00:00Z")  # type: ignore[index]

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=False)

    assert result.sleep_respiratory_rates == {}


def test_wrong_local_day_weight_sample_is_ignored() -> None:
    """An enabled weight sample still belongs only to its offset-derived local day."""
    direct = _direct()
    weight = direct["weight"][0]["weight"]
    weight["sampleTime"] = _sample_time("2042-07-22T04:00:00Z")  # type: ignore[index]

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=True)

    assert result.weight_kg is None


def test_sample_day_is_derived_from_timestamp_and_utc_offset() -> None:
    """A next-UTC-day sample remains valid when its documented offset maps to the day."""
    direct = _direct()
    weight = direct["weight"][0]["weight"]
    weight["sampleTime"] = _sample_time(  # type: ignore[index]
        "2042-07-22T02:00:00Z", "-14400s"
    )

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=True)

    assert result.weight_kg == 80.5


def test_sample_local_date_overflow_only_makes_its_group_unavailable() -> None:
    """An overflowing timestamp-offset pair cannot abort unrelated normalization."""
    direct = _direct()
    weight = direct["weight"][0]["weight"]
    weight["sampleTime"] = _sample_time(  # type: ignore[index]
        "9999-12-31T23:59:59Z", "64800s"
    )

    result = normalize_expanded_day(DAY, direct, _rollups(), include_weight=True)

    assert result.weight_kg is None
    assert result.vo2_max == 42.5
    assert result.floors == 7
