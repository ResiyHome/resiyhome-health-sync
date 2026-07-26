"""Tests for pure expanded Google Health daily metric normalization."""

from datetime import UTC, date, datetime, timedelta
from math import nan
from types import MappingProxyType
from typing import Any

import pytest

from custom_components.resiyhome_health_sync.expanded_metrics import normalize_expanded_day
from custom_components.resiyhome_health_sync.models import DailySummary, ExpandedDailyMetrics

DAY = date(2042, 7, 21)


def _daily_date() -> dict[str, int]:
    return {"year": DAY.year, "month": DAY.month, "day": DAY.day}


def _sample_time(
    physical_time: str = "2042-07-21T07:00:00Z", utc_offset: str = "0s"
) -> dict[str, str]:
    return {"physicalTime": physical_time, "utcOffset": utc_offset}


def _civil_time(value: date, hour: int, *, nanos: int = 0) -> dict[str, object]:
    return {
        "date": {"year": value.year, "month": value.month, "day": value.day},
        "time": {"hours": hour, "nanos": nanos},
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


def test_weight_is_ignored_until_explicitly_enabled() -> None:
    """The pure layer must not retain body measurements when the caller opts out."""
    result = normalize_expanded_day(DAY, _direct(), _rollups(), include_weight=False)

    assert result.weight_kg is None


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
