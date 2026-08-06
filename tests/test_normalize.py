"""Tests for deterministic Google Health reconciliation normalization."""

import json
import math
import re
import subprocess
import sys
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest

from custom_components.resiyhome_health_sync.models import SourceKind
from custom_components.resiyhome_health_sync.normalize import normalize_day

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "steps_fitbit_healthkit.json"
FIXTURE_GENERATOR = FIXTURE_PATH.with_name("generate_steps_fixture.py")
MISSING = object()
ALLOWED_FIXTURE_KEYS = frozenset(
    {
        "active-energy-burned",
        "active-minutes",
        "activeDuration",
        "activeEnergyBurned",
        "activeMinutes",
        "activeMinutesByActivityLevel",
        "activityLevel",
        "all_sources",
        "averageHeartRateVariabilityMilliseconds",
        "beatsPerMinute",
        "caloriesKcal",
        "count",
        "date",
        "daily-heart-rate-variability",
        "daily-resting-heart-rate",
        "dailyHeartRateVariability",
        "dailyRestingHeartRate",
        "dataSource",
        "day",
        "distance",
        "displayName",
        "endTime",
        "endUtcOffset",
        "exercise",
        "exerciseType",
        "heart-rate",
        "heart-rate-variability",
        "heartRate",
        "heartRateVariability",
        "interval",
        "kcal",
        "metricsSummary",
        "millimeters",
        "minutes",
        "minutesAsleep",
        "month",
        "physicalTime",
        "platform",
        "raw",
        "rootMeanSquareOfSuccessiveDifferencesMilliseconds",
        "sampleTime",
        "sleep",
        "stagesSummary",
        "startTime",
        "startUtcOffset",
        "steps",
        "summary",
        "type",
        "utcOffset",
        "wearables",
        "year",
    }
)
FORBIDDEN_FIXTURE_KEYS = frozenset(
    {
        "application",
        "externalId",
        "googleWebClientId",
        "healthUserId",
        "id",
        "legacyUserId",
        "name",
        "packageName",
        "resourceName",
        "sourceId",
        "userId",
        "webClientId",
    }
)


@pytest.fixture
def reconciled_fixture() -> dict[str, Any]:
    """Load a fully synthetic Google Health v4 reconciliation fixture."""
    return json.loads(FIXTURE_PATH.read_text())


def _nested_keys(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for key, nested in value.items():
            yield key
            yield from _nested_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _nested_keys(nested)


def _nested_values(value: object) -> Iterator[object]:
    if isinstance(value, dict):
        for nested in value.values():
            yield from _nested_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _nested_values(nested)
    else:
        yield value


def _step_point(count: object) -> dict[str, Any]:
    return {"steps": {"interval": _observation_interval(), "count": count}}


def _observation_interval(
    *,
    start: object = "2042-07-13T10:00:00Z",
    start_offset: object = "-14400s",
    end: object = "2042-07-13T11:00:00Z",
    end_offset: object = "-14400s",
) -> dict[str, object]:
    return {
        "startTime": start,
        "startUtcOffset": start_offset,
        "endTime": end,
        "endUtcOffset": end_offset,
    }


def _sample_time(
    *,
    physical_time: object = "2042-07-13T10:00:00Z",
    utc_offset: object = "-14400s",
) -> dict[str, object]:
    return {"physicalTime": physical_time, "utcOffset": utc_offset}


def _daily_date() -> dict[str, int]:
    return {"year": 2042, "month": 7, "day": 13}


def _daily_rollup(payload_key: str, **values: object) -> dict[str, object]:
    return {
        "civilStartTime": {"date": _daily_date()},
        "civilEndTime": {"date": {"year": 2042, "month": 7, "day": 14}},
        payload_key: values,
    }


def _distance_point(millimeters: object) -> dict[str, Any]:
    return {"distance": {"interval": _observation_interval(), "millimeters": millimeters}}


def _energy_point(kcal: object) -> dict[str, Any]:
    return {"activeEnergyBurned": {"interval": _observation_interval(), "kcal": kcal}}


def _heart_point(beats_per_minute: object) -> dict[str, Any]:
    return {"heartRate": {"sampleTime": _sample_time(), "beatsPerMinute": beats_per_minute}}


def _daily_resting_heart_point(beats_per_minute: object) -> dict[str, Any]:
    return {
        "dailyRestingHeartRate": {
            "date": _daily_date(),
            "beatsPerMinute": beats_per_minute,
        }
    }


def _daily_hrv_point(value: object) -> dict[str, Any]:
    return {
        "dailyHeartRateVariability": {
            "date": _daily_date(),
            "averageHeartRateVariabilityMilliseconds": value,
        }
    }


def _raw_platform(platform: object) -> dict[str, Any]:
    return {"dataSource": {"platform": platform}}


def _active_minutes_point(levels: object) -> dict[str, Any]:
    return {
        "activeMinutes": {
            "interval": _observation_interval(),
            "activeMinutesByActivityLevel": levels,
        }
    }


def _sleep_point(
    *,
    start: object = "2042-07-12T23:30:00Z",
    end: object = "2042-07-13T07:00:00Z",
    minutes_asleep: object = "390",
    stages: object | None = None,
) -> dict[str, Any]:
    if stages is None:
        stages = [
            {"type": "LIGHT", "minutes": "240"},
            {"type": "DEEP", "minutes": "90"},
            {"type": "REM", "minutes": "60"},
        ]
    interval: dict[str, object] = {"endTime": end}
    if start is not None:
        interval["startTime"] = start
    interval["startUtcOffset"] = "-14400s"
    interval["endUtcOffset"] = "-14400s"
    return {
        "sleep": {
            "interval": interval,
            "summary": {"minutesAsleep": minutes_asleep, "stagesSummary": stages},
        }
    }


def _exercise_point(duration: object) -> dict[str, Any]:
    return {
        "exercise": {
            "interval": {
                "startTime": "2042-07-13T14:00:00Z",
                "startUtcOffset": "-14400s",
                "endTime": "2042-07-13T14:30:00Z",
                "endUtcOffset": "-14400s",
            },
            "exerciseType": "WALKING",
            "displayName": "Synthetic walk",
            "activeDuration": duration,
            "metricsSummary": {"caloriesKcal": 120.0},
        }
    }


def test_fixture_contains_only_approved_synthetic_shape_fields(
    reconciled_fixture: dict[str, Any],
) -> None:
    """The fixture recursively excludes identity, resource, app, and source identifiers."""
    keys = set(_nested_keys(reconciled_fixture))
    text = FIXTURE_PATH.read_text().lower()

    assert keys <= ALLOWED_FIXTURE_KEYS
    assert keys.isdisjoint(FORBIDDEN_FIXTURE_KEYS)
    assert "users/" not in text
    assert "access_token" not in text
    assert "refresh_token" not in text
    assert "client_secret" not in text


def test_fixture_matches_deterministic_generator() -> None:
    subprocess.run(
        [sys.executable, str(FIXTURE_GENERATOR), "--check"],
        check=True,
        capture_output=True,
        text=True,
    )


def test_fixture_uses_only_fixed_future_synthetic_timestamps(
    reconciled_fixture: dict[str, Any],
) -> None:
    timestamps = [
        value
        for value in _nested_values(reconciled_fixture)
        if isinstance(value, str) and re.match(r"^\d{4}-\d{2}-\d{2}(?:T|$)", value)
    ]

    assert timestamps
    assert all(value.startswith(("2042-07-12", "2042-07-13")) for value in timestamps)


def test_fixture_supported_points_include_required_v4_time_and_date_shapes(
    reconciled_fixture: dict[str, Any],
) -> None:
    """Every supported synthetic v4 record includes its required structural fields."""
    for family in ("raw", "all_sources", "wearables"):
        for point in reconciled_fixture[family]["steps"]:
            interval = point["steps"]["interval"]
            assert set(interval) == {
                "startTime",
                "startUtcOffset",
                "endTime",
                "endUtcOffset",
            }

    for data_type, payload_key in (
        ("distance", "distance"),
        ("active-energy-burned", "activeEnergyBurned"),
        ("active-minutes", "activeMinutes"),
    ):
        interval = reconciled_fixture["all_sources"][data_type][0][payload_key]["interval"]
        assert set(interval) == {
            "startTime",
            "startUtcOffset",
            "endTime",
            "endUtcOffset",
        }

    for data_type, payload_key in (
        ("heart-rate", "heartRate"),
        ("heart-rate-variability", "heartRateVariability"),
    ):
        for point in reconciled_fixture["all_sources"][data_type]:
            assert set(point[payload_key]["sampleTime"]) == {"physicalTime", "utcOffset"}

    for data_type, payload_key in (
        ("daily-resting-heart-rate", "dailyRestingHeartRate"),
        ("daily-heart-rate-variability", "dailyHeartRateVariability"),
    ):
        assert set(reconciled_fixture["all_sources"][data_type][0][payload_key]["date"]) == {
            "year",
            "month",
            "day",
        }

    for data_type in ("exercise", "sleep"):
        interval = reconciled_fixture["all_sources"][data_type][0][data_type]["interval"]
        assert set(interval) == {
            "startTime",
            "startUtcOffset",
            "endTime",
            "endUtcOffset",
        }

    exercise = reconciled_fixture["all_sources"]["exercise"][0]["exercise"]
    assert isinstance(exercise["metricsSummary"], dict)
    assert {"exerciseType", "displayName", "metricsSummary"} <= exercise.keys()


def test_fixture_raw_source_intervals_overlap(reconciled_fixture: dict[str, Any]) -> None:
    """The synthetic raw records represent the overlap reconciliation must resolve."""
    fitbit, healthkit = reconciled_fixture["raw"]["steps"]
    fitbit_interval = fitbit["steps"]["interval"]
    healthkit_interval = healthkit["steps"]["interval"]

    fitbit_start = datetime.fromisoformat(fitbit_interval["startTime"])
    fitbit_end = datetime.fromisoformat(fitbit_interval["endTime"])
    healthkit_start = datetime.fromisoformat(healthkit_interval["startTime"])
    healthkit_end = datetime.fromisoformat(healthkit_interval["endTime"])

    assert fitbit_start < healthkit_end
    assert healthkit_start < fitbit_end


def test_canonical_steps_use_reconciled_total(reconciled_fixture: dict[str, Any]) -> None:
    """Overlapping raw records never change canonical or wearable reconciled totals."""
    result = normalize_day(
        date.fromisoformat(reconciled_fixture["day"]),
        reconciled_fixture["raw"],
        reconciled_fixture["all_sources"],
        reconciled_fixture["wearables"],
    )

    assert result.steps == 3000
    assert result.fitbit_steps == 1111
    assert result.steps != 1111 + 2222
    assert result.source is SourceKind.MIXED


@pytest.mark.parametrize(
    "wearables",
    [
        {"steps": [_step_point("not-a-count")]},
        {"steps": {"steps": {"count": "100"}}},
        {"unexpected": [{"value": "100"}]},
    ],
)
def test_malformed_or_unrelated_wearables_do_not_imply_fitbit(
    wearables: dict[str, Any],
) -> None:
    """Only a successfully normalized wearable metric is Fitbit evidence."""
    result = normalize_day(
        date(2042, 7, 13),
        {"steps": [_raw_platform("FITBIT"), _raw_platform("HEALTH_KIT")]},
        {"steps": [_step_point("4100")]},
        wearables,
    )

    assert result.fitbit_steps is None
    assert result.source is SourceKind.APPLE_FALLBACK


def test_unrelated_wearable_data_does_not_block_apple_fallback() -> None:
    """A valid unrelated wearable-shaped mapping cannot suppress HealthKit attribution."""
    result = normalize_day(
        date(2042, 7, 13),
        {"steps": [_raw_platform("HEALTH_KIT")]},
        {"steps": [_step_point("4100")]},
        {"distance": [_distance_point("1000")]},
    )

    assert result.source is SourceKind.APPLE_FALLBACK


def test_valid_wearable_only_reconciliation_is_fitbit() -> None:
    """The google-wearables family is valid Fitbit evidence without all-sources data."""
    result = normalize_day(
        date(2042, 7, 13),
        {},
        {},
        {"steps": [_step_point("5800")]},
    )

    assert result.steps is None
    assert result.fitbit_steps == 5800
    assert result.source is SourceKind.FITBIT


@pytest.mark.parametrize("platform", ["FITBIT", "FITBIT_WEB_API"])
def test_fitbit_platform_labels_are_recognized(platform: str) -> None:
    """Current and legacy verified Fitbit platform labels classify canonical data."""
    result = normalize_day(
        date(2042, 7, 13),
        {"steps": [_raw_platform(platform)]},
        {"steps": [_step_point("100")]},
        {},
    )

    assert result.source is SourceKind.FITBIT


def test_unknown_platform_remains_unclassified() -> None:
    """Future or unrelated raw platform labels are not guessed into a source family."""
    result = normalize_day(
        date(2042, 7, 13),
        {"steps": [_raw_platform("SOMETHING_NEW")]},
        {"steps": [_step_point("100")]},
        {},
    )

    assert result.steps == 100
    assert result.source is SourceKind.UNAVAILABLE


def test_apple_fallback_requires_healthkit_metadata_and_no_valid_wearable() -> None:
    """Apple fallback is attribution, never arithmetic against a wearable total."""
    result = normalize_day(
        date(2042, 7, 13),
        {"steps": [_raw_platform("HEALTH_KIT")]},
        {"steps": [_step_point("4100")]},
        {"steps": []},
    )

    assert result.steps == 4100
    assert result.fitbit_steps is None
    assert result.source is SourceKind.APPLE_FALLBACK


def test_normalizes_google_v4_units_and_sleep_crossing_midnight(
    reconciled_fixture: dict[str, Any],
) -> None:
    """Canonical reconciled records become Home Assistant's native metric units."""
    result = normalize_day(
        date.fromisoformat(reconciled_fixture["day"]),
        reconciled_fixture["raw"],
        reconciled_fixture["all_sources"],
        reconciled_fixture["wearables"],
    )

    assert result.distance_m == 1234.0
    assert result.active_energy_kcal == 123.0
    assert result.exercise_minutes == 33.0
    assert result.resting_heart_rate == 55.0
    assert result.average_heart_rate == 75.0
    assert result.minimum_heart_rate == 60.0
    assert result.maximum_heart_rate == 90.0
    assert result.hrv_ms == 44.0
    assert result.sleep_minutes == 390.0
    assert result.sleep_stages == {"awake": 30.0, "deep": 90.0, "light": 210.0, "rem": 90.0}
    assert result.workouts[0].activity_type == "WALKING"
    assert result.workouts[0].duration_minutes == 30.0
    assert result.workouts[0].active_energy_kcal == 111.0
    assert result.workouts[0].start == datetime.fromisoformat("2042-07-13T14:00:00+00:00")
    assert result.workouts[0].end == datetime.fromisoformat("2042-07-13T14:30:00+00:00")


@pytest.mark.parametrize(("kcal_sum", "expected"), [(0.0, 0.0), (2345.6, 2345.6)])
def test_total_calories_daily_rollup_preserves_zero_and_positive_values(
    kcal_sum: float, expected: float
) -> None:
    """A valid total-calorie aggregate remains finite, non-negative, and exact."""
    result = normalize_day(
        date(2042, 7, 13),
        {},
        {"total-calories": [_daily_rollup("totalCalories", kcalSum=kcal_sum)]},
        {},
    )

    assert result.total_energy_kcal == expected


@pytest.mark.parametrize("kcal_sum", [math.nan, math.inf, -1.0, "2345.6", True, None, []])
def test_total_calories_rejects_non_finite_negative_and_malformed_values(
    kcal_sum: object,
) -> None:
    """Invalid total-calorie aggregates never become a value or a false zero."""
    result = normalize_day(
        date(2042, 7, 13),
        {},
        {"total-calories": [_daily_rollup("totalCalories", kcalSum=kcal_sum)]},
        {},
    )

    assert result.total_energy_kcal is None


@pytest.mark.parametrize(
    ("boundary", "value"),
    [
        ("civilStartTime", MISSING),
        ("civilEndTime", MISSING),
        ("civilStartTime", {"date": {"year": 2042, "month": "7", "day": 13}}),
        ("civilEndTime", {"date": []}),
        ("civilStartTime", {"date": {"year": 2042, "month": 7, "day": 12}}),
        ("civilEndTime", {"date": {"year": 2042, "month": 7, "day": 15}}),
        (
            "civilStartTime",
            {
                "date": {"year": 2042, "month": 7, "day": 13},
                "time": {"hours": 0, "minutes": 1},
            },
        ),
        (
            "civilEndTime",
            {
                "date": {"year": 2042, "month": 7, "day": 14},
                "time": {"hours": 0, "seconds": 1},
            },
        ),
        (
            "civilEndTime",
            {
                "date": {"year": 2042, "month": 7, "day": 13},
                "time": {"hours": 23, "minutes": 59, "seconds": 59},
            },
        ),
    ],
    ids=[
        "missing-start",
        "missing-end",
        "malformed-start",
        "malformed-end",
        "mismatched-start",
        "mismatched-end",
        "non-midnight-start",
        "non-midnight-end",
        "same-day-last-second-end",
    ],
)
def test_total_calories_rejects_incomplete_or_wrong_civil_window(
    boundary: str, value: object
) -> None:
    """A calorie aggregate is valid only for its complete requested-day window."""
    point = _daily_rollup("totalCalories", kcalSum=2345.6)
    if value is MISSING:
        point.pop(boundary)
    else:
        point[boundary] = value

    result = normalize_day(
        date(2042, 7, 13),
        {},
        {"total-calories": [point]},
        {},
    )

    assert result.total_energy_kcal is None


def test_sleep_period_onset_and_after_wake_use_latest_valid_session() -> None:
    """Detailed timing follows the same latest valid completed sleep as duration."""
    older = _sleep_point(
        start="2042-07-12T20:00:00Z",
        end="2042-07-13T03:00:00Z",
        minutes_asleep="360",
    )
    latest = _sleep_point(
        start="2042-07-13T04:00:00Z",
        end="2042-07-13T11:00:00Z",
        minutes_asleep="375",
        stages=[],
    )
    latest["sleep"]["summary"].update(
        {
            "minutesInSleepPeriod": "402",
            "minutesToFallAsleep": "6",
            "minutesAfterWakeUp": "12",
        }
    )
    invalid_newer = _sleep_point(
        start="2042-07-13T11:00:00Z",
        end="2042-07-13T12:00:00Z",
        minutes_asleep="61",
    )

    result = normalize_day(
        date(2042, 7, 13),
        {},
        {"sleep": [older, invalid_newer, latest]},
        {},
    )

    assert result.sleep_minutes == 375.0
    assert result.sleep_period_minutes == 402.0
    assert result.sleep_onset_minutes == 6.0
    assert result.sleep_after_wake_minutes == 12.0


def test_sleep_onset_and_after_wake_preserve_true_zero() -> None:
    """Documented zero-minute sleep timing remains available as zero."""
    point = _sleep_point()
    point["sleep"]["summary"].update(
        {
            "minutesInSleepPeriod": "420",
            "minutesToFallAsleep": "0",
            "minutesAfterWakeUp": "0",
        }
    )

    result = normalize_day(date(2042, 7, 13), {}, {"sleep": [point]}, {})

    assert result.sleep_period_minutes == 420.0
    assert result.sleep_onset_minutes == 0.0
    assert result.sleep_after_wake_minutes == 0.0


@pytest.mark.parametrize(
    ("field", "attribute", "value"),
    [
        ("minutesInSleepPeriod", "sleep_period_minutes", math.nan),
        ("minutesInSleepPeriod", "sleep_period_minutes", math.inf),
        ("minutesInSleepPeriod", "sleep_period_minutes", "-1"),
        ("minutesInSleepPeriod", "sleep_period_minutes", "malformed"),
        ("minutesInSleepPeriod", "sleep_period_minutes", "451"),
        ("minutesToFallAsleep", "sleep_onset_minutes", math.nan),
        ("minutesToFallAsleep", "sleep_onset_minutes", math.inf),
        ("minutesToFallAsleep", "sleep_onset_minutes", "-1"),
        ("minutesToFallAsleep", "sleep_onset_minutes", "malformed"),
        ("minutesToFallAsleep", "sleep_onset_minutes", "451"),
        ("minutesAfterWakeUp", "sleep_after_wake_minutes", math.nan),
        ("minutesAfterWakeUp", "sleep_after_wake_minutes", math.inf),
        ("minutesAfterWakeUp", "sleep_after_wake_minutes", "-1"),
        ("minutesAfterWakeUp", "sleep_after_wake_minutes", "malformed"),
        ("minutesAfterWakeUp", "sleep_after_wake_minutes", "451"),
    ],
)
def test_sleep_period_onset_and_after_wake_reject_invalid_values(
    field: str, attribute: str, value: object
) -> None:
    """Each optional timing value fails closed without discarding valid sleep."""
    point = _sleep_point()
    point["sleep"]["summary"].update(
        {
            "minutesInSleepPeriod": "420",
            "minutesToFallAsleep": "6",
            "minutesAfterWakeUp": "12",
            field: value,
        }
    )

    result = normalize_day(date(2042, 7, 13), {}, {"sleep": [point]}, {})

    assert result.sleep_minutes == 390.0
    assert getattr(result, attribute) is None


def test_normalizes_google_sleep_summary_payload_without_nested_summary() -> None:
    """Google may return sleep summary fields directly under the sleep payload."""
    result = normalize_day(
        date(2042, 7, 14),
        {},
        {
            "sleep": [
                {
                    "sleep": {
                        "interval": {
                            "startTime": "2042-07-14T04:46:00Z",
                            "endTime": "2042-07-14T11:13:00Z",
                            "startUtcOffset": "-14400s",
                            "endUtcOffset": "-14400s",
                        },
                        "minutesAfterWakeUp": "0",
                        "minutesAsleep": "375",
                        "minutesAwake": "12",
                        "minutesInSleepPeriod": "387",
                        "minutesToFallAsleep": "6",
                        "stagesSummary": [],
                    }
                }
            ]
        },
        {},
    )

    assert result.sleep_minutes == 375.0
    assert result.sleep_stages == {"awake": 12.0}


def test_normalizes_google_sleep_stage_summary_counts() -> None:
    """Google v4 stage summaries include type, minutes, and count."""
    result = normalize_day(
        date(2042, 7, 14),
        {},
        {
            "sleep": [
                {
                    "sleep": {
                        "interval": {
                            "startTime": "2042-07-14T04:46:00Z",
                            "endTime": "2042-07-14T11:13:00Z",
                            "startUtcOffset": "-14400s",
                            "endUtcOffset": "-14400s",
                        },
                        "minutesAsleep": "350",
                        "minutesAwake": "15",
                        "stagesSummary": [
                            {"type": "DEEP", "minutes": "45", "count": "3"},
                            {"type": "LIGHT", "minutes": "210", "count": "12"},
                            {"type": "REM", "minutes": "95", "count": "6"},
                            {"type": "AWAKE", "minutes": "15", "count": "4"},
                        ],
                    }
                }
            ]
        },
        {},
    )

    assert result.sleep_minutes == 350.0
    assert result.sleep_stages == {
        "awake": 15.0,
        "deep": 45.0,
        "light": 210.0,
        "rem": 95.0,
    }


def test_normalizes_prefixed_google_sleep_stage_types() -> None:
    """Google may return protobuf-style enum labels for sleep stage types."""
    result = normalize_day(
        date(2042, 7, 14),
        {},
        {
            "sleep": [
                {
                    "sleep": {
                        "interval": {
                            "startTime": "2042-07-14T04:46:00Z",
                            "endTime": "2042-07-14T11:13:00Z",
                            "startUtcOffset": "-14400s",
                            "endUtcOffset": "-14400s",
                        },
                        "minutesAsleep": "350",
                        "minutesAwake": "15",
                        "stagesSummary": [
                            {
                                "type": "SLEEP_STAGE_TYPE_DEEP",
                                "minutes": "45",
                                "count": "3",
                            },
                            {
                                "type": "sleep_stage_type_light",
                                "minutes": "210",
                                "count": "12",
                            },
                            {"type": "STAGE_TYPE_REM", "minutes": "95", "count": "6"},
                            {
                                "type": "STAGE_TYPE_AWAKE_IN_BED",
                                "minutes": "15",
                                "count": "4",
                            },
                        ],
                    }
                }
            ]
        },
        {},
    )

    assert result.sleep_minutes == 350.0
    assert result.sleep_stages == {
        "awake": 15.0,
        "deep": 45.0,
        "light": 210.0,
        "rem": 95.0,
    }


def test_normalizes_tokenized_google_sleep_stage_types() -> None:
    """Google stage labels may include extra enum words around the real stage."""
    result = normalize_day(
        date(2042, 7, 14),
        {},
        {
            "sleep": [
                {
                    "sleep": {
                        "interval": {
                            "startTime": "2042-07-14T04:46:00Z",
                            "endTime": "2042-07-14T11:13:00Z",
                            "startUtcOffset": "-14400s",
                            "endUtcOffset": "-14400s",
                        },
                        "minutesAsleep": "350",
                        "minutesAwake": "15",
                        "stagesSummary": [
                            {"type": "SLEEP_DEEP", "minutes": "45", "count": "3"},
                            {"type": "LIGHT_SLEEP", "minutes": "210", "count": "12"},
                            {"type": "REM_SLEEP", "minutes": "95", "count": "6"},
                        ],
                    }
                }
            ]
        },
        {},
    )

    assert result.sleep_minutes == 350.0
    assert result.sleep_stages == {
        "awake": 15.0,
        "deep": 45.0,
        "light": 210.0,
        "rem": 95.0,
    }


def test_sleep_stage_parser_ignores_unknown_summary_rows() -> None:
    """Unsupported stage rows must not discard known deep, light, and REM totals."""
    result = normalize_day(
        date(2042, 7, 14),
        {},
        {
            "sleep": [
                {
                    "sleep": {
                        "interval": {
                            "startTime": "2042-07-14T04:46:00Z",
                            "endTime": "2042-07-14T11:13:00Z",
                            "startUtcOffset": "-14400s",
                            "endUtcOffset": "-14400s",
                        },
                        "minutesAsleep": "350",
                        "minutesAwake": "15",
                        "stagesSummary": [
                            {"type": "DEEP", "minutes": "45", "count": "3"},
                            {"type": "LIGHT", "minutes": "210", "count": "12"},
                            {"type": "REM", "minutes": "95", "count": "6"},
                            {"type": "UNRECOGNIZED_STAGE", "minutes": "1", "count": "1"},
                        ],
                    }
                }
            ]
        },
        {},
    )

    assert result.sleep_minutes == 350.0
    assert result.sleep_stages == {
        "awake": 15.0,
        "deep": 45.0,
        "light": 210.0,
        "rem": 95.0,
    }


def test_sleep_stage_summary_duplicate_rows_do_not_double_count() -> None:
    """Repeated summary rows with count are totals, not timeline segments."""
    result = normalize_day(
        date(2042, 7, 14),
        {},
        {
            "sleep": [
                {
                    "sleep": {
                        "interval": {
                            "startTime": "2042-07-14T04:46:00Z",
                            "endTime": "2042-07-14T11:13:00Z",
                            "startUtcOffset": "-14400s",
                            "endUtcOffset": "-14400s",
                        },
                        "minutesAsleep": "375",
                        "minutesAwake": "12",
                        "stagesSummary": [
                            {"type": "AWAKE", "minutes": "12", "count": "4"},
                            {"type": "LIGHT", "minutes": "210", "count": "12"},
                            {"type": "DEEP", "minutes": "70", "count": "3"},
                            {"type": "REM", "minutes": "95", "count": "6"},
                            {"type": "AWAKE", "minutes": "12", "count": "4"},
                            {"type": "LIGHT", "minutes": "210", "count": "12"},
                            {"type": "DEEP", "minutes": "70", "count": "3"},
                            {"type": "REM", "minutes": "95", "count": "6"},
                        ],
                    }
                }
            ]
        },
        {},
    )

    assert result.sleep_minutes == 375.0
    assert result.sleep_stages == {
        "awake": 12.0,
        "deep": 70.0,
        "light": 210.0,
        "rem": 95.0,
    }


def test_sleep_duration_survives_unrecognized_stage_summary_shape() -> None:
    """Google may provide a summary object for stages instead of a stage list."""
    result = normalize_day(
        date(2042, 7, 14),
        {},
        {
            "sleep": [
                {
                    "sleep": {
                        "interval": {
                            "startTime": "2042-07-14T04:46:00Z",
                            "endTime": "2042-07-14T11:13:00Z",
                            "startUtcOffset": "-14400s",
                            "endUtcOffset": "-14400s",
                        },
                        "minutesAsleep": "375",
                        "stagesSummary": {"shape": "summary"},
                    }
                }
            ]
        },
        {},
    )

    assert result.sleep_minutes == 375.0
    assert result.sleep_stages == {}


def test_sleep_duration_survives_unrecognized_top_level_stage_list_shape() -> None:
    """Top-level Google summary minutes are valid even when stage entries are not."""
    result = normalize_day(
        date(2042, 7, 14),
        {},
        {
            "sleep": [
                {
                    "sleep": {
                        "interval": {
                            "startTime": "2042-07-14T04:46:00Z",
                            "endTime": "2042-07-14T11:13:00Z",
                            "startUtcOffset": "-14400s",
                            "endUtcOffset": "-14400s",
                        },
                        "minutesAsleep": "375",
                        "minutesAwake": "12",
                        "stagesSummary": [{"unknownStageKeys": "present"}],
                    }
                }
            ]
        },
        {},
    )

    assert result.sleep_minutes == 375.0
    assert result.sleep_stages == {"awake": 12.0}


def test_sleep_duration_survives_unrecognized_nested_stage_list_shape() -> None:
    """Nested Google summary minutes are valid even when stage entries are not."""
    result = normalize_day(
        date(2042, 7, 14),
        {},
        {
            "sleep": [
                {
                    "sleep": {
                        "interval": {
                            "startTime": "2042-07-14T04:46:00Z",
                            "endTime": "2042-07-14T11:13:00Z",
                            "startUtcOffset": "-14400s",
                            "endUtcOffset": "-14400s",
                        },
                        "summary": {
                            "minutesAfterWakeUp": "0",
                            "minutesAsleep": "375",
                            "minutesAwake": "12",
                            "minutesInSleepPeriod": "387",
                            "minutesToFallAsleep": "6",
                            "stagesSummary": [{"unknownStageKeys": "present"}],
                        },
                    }
                }
            ]
        },
        {},
    )

    assert result.sleep_minutes == 375.0
    assert result.sleep_stages == {"awake": 12.0}


def test_normalizes_google_sleep_stage_timeline_segments() -> None:
    """Detailed sleep stages may arrive as repeated interval segments."""
    result = normalize_day(
        date(2042, 7, 14),
        {},
        {
            "sleep": [
                {
                    "sleep": {
                        "interval": {
                            "startTime": "2042-07-14T04:46:00Z",
                            "endTime": "2042-07-14T11:13:00Z",
                            "startUtcOffset": "-14400s",
                            "endUtcOffset": "-14400s",
                        },
                        "minutesAsleep": "375",
                        "minutesAwake": "12",
                        "stagesSummary": [
                            {
                                "type": "LIGHT",
                                "interval": {
                                    "startTime": "2042-07-14T04:46:00Z",
                                    "endTime": "2042-07-14T05:16:00Z",
                                    "startUtcOffset": "-14400s",
                                    "endUtcOffset": "-14400s",
                                },
                            },
                            {
                                "type": "DEEP",
                                "interval": {
                                    "startTime": "2042-07-14T05:16:00Z",
                                    "endTime": "2042-07-14T06:01:00Z",
                                    "startUtcOffset": "-14400s",
                                    "endUtcOffset": "-14400s",
                                },
                            },
                            {
                                "type": "LIGHT",
                                "interval": {
                                    "startTime": "2042-07-14T06:01:00Z",
                                    "endTime": "2042-07-14T07:01:00Z",
                                    "startUtcOffset": "-14400s",
                                    "endUtcOffset": "-14400s",
                                },
                            },
                            {
                                "type": "REM",
                                "interval": {
                                    "startTime": "2042-07-14T07:01:00Z",
                                    "endTime": "2042-07-14T07:31:00Z",
                                    "startUtcOffset": "-14400s",
                                    "endUtcOffset": "-14400s",
                                },
                            },
                        ],
                    }
                }
            ]
        },
        {},
    )

    assert result.sleep_minutes == 375.0
    assert result.sleep_stages == {
        "awake": 12.0,
        "deep": 45.0,
        "light": 90.0,
        "rem": 30.0,
    }


def test_explicit_zero_is_preserved_while_missing_metric_is_unavailable() -> None:
    """A true Google zero is not conflated with an absent reconciled data type."""
    result = normalize_day(
        date(2042, 7, 13),
        {"steps": [_raw_platform("FITBIT")]},
        {"steps": [_step_point("0")]},
        {"steps": [_step_point("0")]},
    )

    assert result.steps == 0
    assert result.fitbit_steps == 0
    assert result.distance_m is None
    assert result.source is SourceKind.FITBIT


@pytest.mark.parametrize(
    "all_sources",
    [
        {"steps": [_step_point(1.5)]},
        {"steps": [_step_point(True)]},
        {"steps": [_step_point("not-a-count")]},
        {"steps": {"steps": {"count": "12"}}},
        {"steps": [_step_point("-1")]},
        {"steps": [_step_point(str(2**63))]},
        {"steps": [_step_point(str(-(2**63) - 1))]},
        {"steps": [_step_point("9" * 5000)]},
    ],
)
def test_malformed_or_out_of_domain_steps_fail_closed(
    all_sources: dict[str, Any],
) -> None:
    """Invalid Google int64 values cannot become a corrupted or zero total."""
    result = normalize_day(date(2042, 7, 13), {}, all_sources, {})

    assert result.steps is None
    assert result.source is SourceKind.UNAVAILABLE


def test_active_minutes_sum_wholly_valid_level_arrays() -> None:
    """Unique official levels are summed, and may repeat in separate intervals."""
    result = normalize_day(
        date(2042, 7, 13),
        {},
        {
            "active-minutes": [
                _active_minutes_point(
                    [
                        {"activityLevel": "LIGHT", "activeMinutes": "10"},
                        {"activityLevel": "MODERATE", "activeMinutes": "20"},
                    ]
                ),
                _active_minutes_point([{"activityLevel": "LIGHT", "activeMinutes": "5"}]),
            ]
        },
        {},
    )

    assert result.exercise_minutes == 35.0


@pytest.mark.parametrize(
    "levels",
    [
        [],
        [{"activeMinutes": "1"}],
        [{"activityLevel": True, "activeMinutes": "1"}],
        [{"activityLevel": 1, "activeMinutes": "1"}],
        [{"activityLevel": "ACTIVITY_LEVEL_UNSPECIFIED", "activeMinutes": "1"}],
        [{"activityLevel": "UNKNOWN", "activeMinutes": "1"}],
        [
            {"activityLevel": "LIGHT", "activeMinutes": "1"},
            {"activityLevel": "LIGHT", "activeMinutes": "2"},
        ],
        [{"activityLevel": "VIGOROUS", "activeMinutes": 1.5}],
        [{"activityLevel": "VIGOROUS", "activeMinutes": str(2**63)}],
        ({"activityLevel": "LIGHT", "activeMinutes": "1"},),
        {"activityLevel": "LIGHT", "activeMinutes": "1"},
    ],
)
def test_active_minutes_reject_invalid_level_arrays(levels: object) -> None:
    """One invalid active-minute entry makes the complete metric unavailable."""
    result = normalize_day(
        date(2042, 7, 13),
        {},
        {"active-minutes": [_active_minutes_point(levels)]},
        {},
    )

    assert result.exercise_minutes is None


def test_float_sum_overflow_fails_closed() -> None:
    """Finite energy points may not overflow their aggregate to infinity."""
    result = normalize_day(
        date(2042, 7, 13),
        {},
        {
            "active-energy-burned": [
                _energy_point(1e308),
                _energy_point(1e308),
            ]
        },
        {},
    )

    assert result.active_energy_kcal is None


def test_enormous_double_input_fails_closed() -> None:
    """An integer too large for a JSON double cannot escape as an exception."""
    result = normalize_day(
        date(2042, 7, 13),
        {},
        {"active-energy-burned": [_energy_point(10**400)]},
        {},
    )

    assert result.active_energy_kcal is None


def test_distance_and_heart_int64_overflow_fail_closed() -> None:
    """Out-of-range REST int64 strings never reach floating-point conversions."""
    result = normalize_day(
        date(2042, 7, 13),
        {},
        {
            "distance": [_distance_point(str(2**63))],
            "heart-rate": [_heart_point(str(2**63))],
            "daily-resting-heart-rate": [_daily_resting_heart_point(str(2**63))],
        },
        {},
    )

    assert result.distance_m is None
    assert result.average_heart_rate is None
    assert result.minimum_heart_rate is None
    assert result.maximum_heart_rate is None
    assert result.resting_heart_rate is None


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_hrv_is_unavailable(value: float) -> None:
    """HRV output is always finite when available."""
    result = normalize_day(
        date(2042, 7, 13),
        {},
        {"daily-heart-rate-variability": [_daily_hrv_point(value)]},
        {},
    )

    assert result.hrv_ms is None


def test_enormous_workout_duration_is_rejected() -> None:
    """A protobuf duration cannot overflow or exceed its workout interval."""
    result = normalize_day(
        date(2042, 7, 13),
        {},
        {"exercise": [_exercise_point(f"{'9' * 400}s")]},
        {},
    )

    assert result.workouts == ()


def test_sleep_crossing_midnight_uses_valid_physical_instants() -> None:
    """A valid timezone-aware sleep may start before and end on the summary day."""
    result = normalize_day(
        date(2042, 7, 13),
        {},
        {"sleep": [_sleep_point()]},
        {},
    )

    assert result.sleep_minutes == 390.0


@pytest.mark.parametrize(
    "sleep_point",
    [
        _sleep_point(start=None),
        _sleep_point(start="not-a-timestamp"),
        _sleep_point(start="2042-07-12T23:30:00"),
        _sleep_point(start="2042-07-13T08:00:00Z", end="2042-07-13T07:00:00Z"),
    ],
)
def test_sleep_rejects_invalid_physical_intervals(sleep_point: dict[str, Any]) -> None:
    """Missing, malformed, naive, or inverted physical sleep intervals fail closed."""
    result = normalize_day(date(2042, 7, 13), {}, {"sleep": [sleep_point]}, {})

    assert result.sleep_minutes is None
    assert result.sleep_stages == {}


@pytest.mark.parametrize(
    "sleep_point",
    [
        _sleep_point(minutes_asleep="451"),
        _sleep_point(stages={"type": "LIGHT", "minutes": "10"}),
        _sleep_point(stages=[{"type": "LIGHT", "minutes": str(2**63)}]),
    ],
)
def test_sleep_rejects_invalid_stage_or_duration_shapes(sleep_point: dict[str, Any]) -> None:
    """Sleep duration and stage summaries remain wholly fail-closed."""
    result = normalize_day(date(2042, 7, 13), {}, {"sleep": [sleep_point]}, {})

    assert result.sleep_minutes is None
    assert result.sleep_stages == {}


def test_sleep_unknown_only_stage_labels_preserve_duration_without_stage_breakdown() -> None:
    """Unknown stage labels cannot erase an otherwise valid sleep session."""
    result = normalize_day(
        date(2042, 7, 13),
        {},
        {"sleep": [_sleep_point(stages=[{"type": "UNKNOWN", "minutes": "10"}])]},
        {},
    )

    assert result.sleep_minutes == 390.0
    assert result.sleep_stages == {}


@pytest.mark.parametrize(
    "metrics_summary",
    [MISSING, None, True, [], "summary", {"caloriesKcal": "120"}, {"caloriesKcal": math.nan}],
)
def test_exercise_rejects_missing_or_malformed_metrics_summary(
    metrics_summary: object,
) -> None:
    """Required exercise metricsSummary and any used calories field fail closed."""
    point = _exercise_point("1800s")
    exercise = point["exercise"]
    if metrics_summary is MISSING:
        del exercise["metricsSummary"]
    else:
        exercise["metricsSummary"] = metrics_summary

    result = normalize_day(date(2042, 7, 13), {}, {"exercise": [point]}, {})

    assert result.workouts == ()


def test_exercise_allows_empty_metrics_summary_with_unavailable_calories() -> None:
    """The required summary object may omit its optional calorie metric."""
    point = _exercise_point("1800s")
    point["exercise"]["metricsSummary"] = {}

    result = normalize_day(date(2042, 7, 13), {}, {"exercise": [point]}, {})

    assert len(result.workouts) == 1
    assert result.workouts[0].active_energy_kcal is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("exerciseType", None),
        ("exerciseType", True),
        ("exerciseType", ""),
        ("displayName", None),
        ("displayName", True),
        ("displayName", ""),
    ],
)
def test_exercise_rejects_invalid_required_text_fields(field: str, value: object) -> None:
    """Required exercise type and display name must be non-empty strings."""
    point = _exercise_point("1800s")
    if value is None:
        del point["exercise"][field]
    else:
        point["exercise"][field] = value

    result = normalize_day(date(2042, 7, 13), {}, {"exercise": [point]}, {})

    assert result.workouts == ()


@pytest.mark.parametrize(
    "interval",
    [
        MISSING,
        None,
        True,
        [],
        {"startTime": "2042-07-13T00:00:00Z"},
        _observation_interval(start_offset=True),
        _observation_interval(start_offset=[]),
        _observation_interval(start_offset="not-a-duration"),
        _observation_interval(end_offset="64801s"),
        _observation_interval(start_offset="-64801s"),
        _observation_interval(start="2042-07-13T10:00:00"),
        _observation_interval(start="2042-07-13T12:00:00Z"),
    ],
)
def test_sleep_and_exercise_reject_invalid_session_intervals(interval: object) -> None:
    """Both supported session records require complete valid physical intervals."""
    sleep = _sleep_point()
    exercise = _exercise_point("1800s")
    if interval is MISSING:
        del sleep["sleep"]["interval"]
        del exercise["exercise"]["interval"]
    else:
        sleep["sleep"]["interval"] = interval
        exercise["exercise"]["interval"] = interval

    result = normalize_day(
        date(2042, 7, 13),
        {},
        {"sleep": [sleep], "exercise": [exercise]},
        {},
    )

    assert result.sleep_minutes is None
    assert result.workouts == ()


def test_protobuf_duration_accepts_exactly_nine_fractional_digits() -> None:
    """The protobuf JSON nanosecond precision limit is accepted exactly."""
    result = normalize_day(
        date(2042, 7, 13),
        {},
        {"exercise": [_exercise_point("1799.123456789s")]},
        {},
    )

    assert result.workouts[0].duration_minutes == pytest.approx(1799.123456789 / 60)


@pytest.mark.parametrize(
    "duration",
    [
        "1.1234567890s",
        "+1s",
        "--1s",
        "-1s",
        "NaNs",
        "nans",
        "infs",
        "Infinitys",
        f"{'9' * 400}s",
    ],
)
def test_protobuf_duration_rejects_malformed_precision_sign_and_overflow(
    duration: str,
) -> None:
    """Malformed or out-of-domain protobuf durations cannot emit a workout."""
    result = normalize_day(
        date(2042, 7, 13),
        {},
        {"exercise": [_exercise_point(duration)]},
        {},
    )

    assert result.workouts == ()


@pytest.mark.parametrize(
    ("data_type", "point", "attribute"),
    [
        ("steps", _step_point("10"), "steps"),
        ("distance", _distance_point("1000"), "distance_m"),
        ("active-energy-burned", _energy_point(1.0), "active_energy_kcal"),
        (
            "active-minutes",
            _active_minutes_point([{"activityLevel": "LIGHT", "activeMinutes": "1"}]),
            "exercise_minutes",
        ),
    ],
)
def test_interval_metrics_require_complete_observation_intervals(
    data_type: str, point: dict[str, Any], attribute: str
) -> None:
    """Every supported interval metric validates its required interval object."""
    payload = next(iter(point.values()))
    del payload["interval"]

    result = normalize_day(date(2042, 7, 13), {}, {data_type: [point]}, {})

    assert getattr(result, attribute) is None


@pytest.mark.parametrize(
    "interval",
    [
        MISSING,
        None,
        True,
        [],
        {"startTime": "2042-07-13T10:00:00Z"},
        _observation_interval(start_offset=True),
        _observation_interval(end_offset=[]),
        _observation_interval(end_offset="not-a-duration"),
        _observation_interval(start="not-a-timestamp"),
        _observation_interval(start="2042-07-13T11:00:00Z"),
    ],
)
def test_observation_interval_malformed_shapes_fail_closed(interval: object) -> None:
    """Malformed observation intervals make their complete metric unavailable."""
    point = _step_point("10")
    if interval is MISSING:
        del point["steps"]["interval"]
    else:
        point["steps"]["interval"] = interval

    result = normalize_day(date(2042, 7, 13), {}, {"steps": [point]}, {})

    assert result.steps is None


@pytest.mark.parametrize(
    "sample_time",
    [
        MISSING,
        None,
        True,
        [],
        {"physicalTime": "2042-07-13T10:00:00Z"},
        _sample_time(physical_time="not-a-timestamp"),
        _sample_time(physical_time="2042-07-13T10:00:00"),
        _sample_time(utc_offset=True),
        _sample_time(utc_offset=[]),
        _sample_time(utc_offset="64801s"),
    ],
)
def test_heart_sample_requires_complete_sample_time(sample_time: object) -> None:
    """Heart-rate samples require physical time plus a bounded UTC offset."""
    point = _heart_point("70")
    if sample_time is MISSING:
        del point["heartRate"]["sampleTime"]
    else:
        point["heartRate"]["sampleTime"] = sample_time

    result = normalize_day(date(2042, 7, 13), {}, {"heart-rate": [point]}, {})

    assert result.average_heart_rate is None


def test_sample_hrv_requires_complete_sample_time() -> None:
    """The HRV sample fallback uses the same required sample-time validation."""
    result = normalize_day(
        date(2042, 7, 13),
        {},
        {
            "heart-rate-variability": [
                {
                    "heartRateVariability": {
                        "rootMeanSquareOfSuccessiveDifferencesMilliseconds": 42.5
                    }
                }
            ]
        },
        {},
    )

    assert result.hrv_ms is None


@pytest.mark.parametrize(
    "daily_date",
    [
        MISSING,
        None,
        True,
        [],
        {},
        {"year": 2042, "month": 7},
        {"year": True, "month": 7, "day": 13},
        {"year": "2042", "month": 7, "day": 13},
        {"year": 2042, "month": 2, "day": 30},
    ],
)
def test_daily_metrics_require_complete_valid_dates(daily_date: object) -> None:
    """Both supported daily metrics require a real year-month-day object."""
    resting = _daily_resting_heart_point("54")
    hrv = _daily_hrv_point(42.5)
    if daily_date is MISSING:
        del resting["dailyRestingHeartRate"]["date"]
        del hrv["dailyHeartRateVariability"]["date"]
    else:
        resting["dailyRestingHeartRate"]["date"] = daily_date
        hrv["dailyHeartRateVariability"]["date"] = daily_date

    result = normalize_day(
        date(2042, 7, 13),
        {},
        {
            "daily-resting-heart-rate": [resting],
            "daily-heart-rate-variability": [hrv],
        },
        {},
    )

    assert result.resting_heart_rate is None
    assert result.hrv_ms is None
