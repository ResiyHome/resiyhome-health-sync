"""Pure normalization for reconciled Google Health v4 data points."""

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime
from math import isfinite
from typing import cast

from .models import DailySummary, SourceKind, WorkoutSummary

type DataPoint = Mapping[str, object]
type DataPointStreams = Mapping[str, Sequence[DataPoint]]

_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_MAX_PROTOBUF_DURATION_SECONDS = 315_576_000_000
_MAX_UTC_OFFSET_SECONDS = 18 * 60 * 60
_ACTIVE_MINUTE_LEVELS = frozenset({"LIGHT", "MODERATE", "VIGOROUS"})
_SLEEP_STAGE_TYPES = {
    "AWAKE": "awake",
    "AWAKE_IN_BED": "awake",
    "OUT_OF_BED": "awake",
    "WAKE": "awake",
    "LIGHT": "light",
    "DEEP": "deep",
    "REM": "rem",
    "ASLEEP": "asleep",
    "SLEEPING": "asleep",
    "RESTLESS": "restless",
}
_DURATION_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]{1,9})?s")
_RFC3339_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


def normalize_day(
    day: date,
    raw: DataPointStreams,
    all_sources: DataPointStreams,
    wearables: DataPointStreams,
) -> DailySummary:
    """Normalize one day without combining raw platform records.

    Google's reconciled streams are the only source of metric values. Raw points
    are deliberately examined only for their platform labels, which keeps source
    attribution from becoming arithmetic over overlapping measurements.
    """
    steps = _sum_integer_metric(all_sources, "steps", "steps", "count")
    fitbit_steps = _sum_integer_metric(wearables, "steps", "steps", "count")
    distance_mm = _sum_integer_metric(all_sources, "distance", "distance", "millimeters")
    distance_m = _finite_divide(distance_mm, 1000) if distance_mm is not None else None

    active_energy_kcal = _sum_float_metric(
        all_sources, "active-energy-burned", "activeEnergyBurned", "kcal"
    )
    exercise_minutes = _active_minutes(all_sources)
    resting_heart_rate = _latest_integer_metric(
        all_sources, "daily-resting-heart-rate", "dailyRestingHeartRate", "beatsPerMinute"
    )
    heart_rate = _heart_rate_statistics(all_sources)
    hrv_ms = _hrv(all_sources)
    sleep_minutes, sleep_stages = _sleep(all_sources)
    workouts = _workouts(all_sources)

    has_fitbit, has_healthkit = _platforms(raw)
    has_canonical_data = any(
        value is not None
        for value in (
            steps,
            distance_m,
            active_energy_kcal,
            exercise_minutes,
            sleep_minutes,
            resting_heart_rate,
            heart_rate[0],
            hrv_ms,
        )
    ) or bool(workouts)
    source = _classify_source(
        has_fitbit=has_fitbit,
        has_healthkit=has_healthkit,
        has_canonical_data=has_canonical_data,
        has_wearable_data=fitbit_steps is not None,
    )

    return DailySummary(
        date=day,
        steps=steps,
        fitbit_steps=fitbit_steps,
        distance_m=distance_m,
        active_energy_kcal=active_energy_kcal,
        exercise_minutes=exercise_minutes,
        sleep_minutes=sleep_minutes,
        sleep_stages=sleep_stages,
        resting_heart_rate=_as_float(resting_heart_rate),
        average_heart_rate=heart_rate[0],
        minimum_heart_rate=heart_rate[1],
        maximum_heart_rate=heart_rate[2],
        hrv_ms=hrv_ms,
        workouts=workouts,
        source=source,
    )


def _classify_source(
    *,
    has_fitbit: bool,
    has_healthkit: bool,
    has_canonical_data: bool,
    has_wearable_data: bool,
) -> SourceKind:
    """Classify raw platform evidence without computing any source-derived value."""
    if has_wearable_data:
        if has_canonical_data and has_healthkit:
            return SourceKind.MIXED
        return SourceKind.FITBIT
    if not has_canonical_data:
        return SourceKind.UNAVAILABLE
    if has_healthkit:
        return SourceKind.APPLE_FALLBACK
    if has_fitbit:
        return SourceKind.FITBIT
    return SourceKind.UNAVAILABLE


def _platforms(raw: DataPointStreams) -> tuple[bool, bool]:
    """Return only the recognized platform presence flags from raw metadata."""
    has_fitbit = False
    has_healthkit = False
    for points in raw.values():
        for point in _points_or_empty(points):
            data_source = _mapping(point.get("dataSource"))
            if data_source is None:
                continue
            platform = data_source.get("platform")
            if not isinstance(platform, str):
                continue
            match platform.strip().upper():
                case "FITBIT" | "FITBIT_WEB_API":
                    has_fitbit = True
                case "HEALTH_KIT":
                    has_healthkit = True
    return has_fitbit, has_healthkit


def _sum_integer_metric(
    stream: DataPointStreams, data_type: str, payload_key: str, value_key: str
) -> int | None:
    """Sum a required non-negative integer field or reject the metric entirely."""
    values = _integer_values(stream, data_type, payload_key, value_key, _has_observation_interval)
    if not values:
        return None
    total = sum(values)
    return total if total <= _INT64_MAX else None


def _sum_float_metric(
    stream: DataPointStreams, data_type: str, payload_key: str, value_key: str
) -> float | None:
    """Sum a required non-negative floating-point field or reject the metric entirely."""
    points = _metric_points(stream, data_type)
    if not points:
        return None

    values: list[float] = []
    for point in points:
        payload = _mapping(point.get(payload_key))
        if payload is None or not _has_observation_interval(payload):
            return None
        value = _non_negative_float(payload.get(value_key))
        if value is None:
            return None
        values.append(value)
    total = sum(values)
    return total if isfinite(total) else None


def _integer_values(
    stream: DataPointStreams,
    data_type: str,
    payload_key: str,
    value_key: str,
    shape_validator: Callable[[Mapping[str, object]], bool],
) -> list[int] | None:
    """Read integer values from a complete reconciled metric collection."""
    points = _metric_points(stream, data_type)
    if not points:
        return None

    values: list[int] = []
    for point in points:
        payload = _mapping(point.get(payload_key))
        if payload is None or not shape_validator(payload):
            return None
        value = _non_negative_integer(payload.get(value_key))
        if value is None:
            return None
        values.append(value)
    return values


def _active_minutes(stream: DataPointStreams) -> float | None:
    """Sum all activity-level minutes from reconciled active-minute intervals."""
    points = _metric_points(stream, "active-minutes")
    if not points:
        return None

    total = 0
    for point in points:
        payload = _mapping(point.get("activeMinutes"))
        if payload is None or not _has_observation_interval(payload):
            return None
        levels = payload.get("activeMinutesByActivityLevel")
        if not isinstance(levels, list) or not levels:
            return None
        seen_levels: set[str] = set()
        for level in levels:
            level_data = _mapping(level)
            activity_level = level_data.get("activityLevel") if level_data is not None else None
            minutes = _non_negative_integer(
                level_data.get("activeMinutes") if level_data is not None else None
            )
            if (
                not isinstance(activity_level, str)
                or activity_level not in _ACTIVE_MINUTE_LEVELS
                or activity_level in seen_levels
                or minutes is None
            ):
                return None
            seen_levels.add(activity_level)
            total += minutes
            if total > _INT64_MAX:
                return None
    return _finite_float(total)


def _heart_rate_statistics(
    stream: DataPointStreams,
) -> tuple[float | None, float | None, float | None]:
    """Calculate average, minimum, and maximum BPM from reconciled samples."""
    values = _integer_values(stream, "heart-rate", "heartRate", "beatsPerMinute", _has_sample_time)
    if values is None:
        return None, None, None
    average = _finite_divide(sum(values), len(values))
    minimum = _finite_float(min(values))
    maximum = _finite_float(max(values))
    if average is None or minimum is None or maximum is None:
        return None, None, None
    return average, minimum, maximum


def _latest_integer_metric(
    stream: DataPointStreams, data_type: str, payload_key: str, value_key: str
) -> int | None:
    """Read the first API-ordered value for a single daily metric."""
    values = _integer_values(stream, data_type, payload_key, value_key, _has_daily_date)
    return values[0] if values else None


def _hrv(stream: DataPointStreams) -> float | None:
    """Prefer the daily HRV metric and fall back to the detailed sample value."""
    daily = _latest_float_metric(
        stream,
        "daily-heart-rate-variability",
        "dailyHeartRateVariability",
        "averageHeartRateVariabilityMilliseconds",
        _has_daily_date,
    )
    if daily is not None:
        return daily
    return _latest_float_metric(
        stream,
        "heart-rate-variability",
        "heartRateVariability",
        "rootMeanSquareOfSuccessiveDifferencesMilliseconds",
        _has_sample_time,
    )


def _latest_float_metric(
    stream: DataPointStreams,
    data_type: str,
    payload_key: str,
    value_key: str,
    shape_validator: Callable[[Mapping[str, object]], bool],
) -> float | None:
    """Read the first API-ordered value for a single floating-point metric."""
    points = _metric_points(stream, data_type)
    if not points:
        return None
    values: list[float] = []
    for point in points:
        payload = _mapping(point.get(payload_key))
        if payload is None or not shape_validator(payload):
            return None
        value = _non_negative_float(payload.get(value_key))
        if value is None:
            return None
        values.append(value)
    return values[0]


def _sleep(stream: DataPointStreams) -> tuple[float | None, Mapping[str, float]]:
    """Use the latest valid reconciled sleep session, including midnight crossings."""
    points = _metric_points(stream, "sleep")
    if not points:
        return None, {}

    sessions: list[tuple[datetime, float, Mapping[str, float]]] = []
    for point in points:
        payload = _mapping(point.get("sleep"))
        session = _sleep_session(payload)
        if session is not None:
            sessions.append(session)
    if not sessions:
        return None, {}

    _, minutes, stages = max(sessions, key=lambda session: session[0])
    return minutes, stages


def _sleep_session(
    payload: Mapping[str, object] | None,
) -> tuple[datetime, float, Mapping[str, float]] | None:
    """Validate a complete sleep session without keeping its source payload."""
    if payload is None:
        return None
    nested_summary = _mapping(payload.get("summary"))
    summary = nested_summary or payload
    physical_interval = _physical_interval(payload.get("interval"))
    minutes = _non_negative_integer(summary.get("minutesAsleep") if summary is not None else None)
    if physical_interval is None or minutes is None:
        return None
    start, end = physical_interval
    interval_seconds = (end - start).total_seconds()
    minutes_float = _finite_float(minutes)
    if minutes_float is None or minutes_float * 60 > interval_seconds:
        return None

    fallback_stages = _sleep_summary_fallback_stages(summary, interval_seconds)
    stages_value = summary.get("stagesSummary") if summary is not None else None
    if stages_value is None:
        return end, minutes_float, fallback_stages
    if not isinstance(stages_value, list):
        if nested_summary is not None and not fallback_stages:
            return None
        return end, minutes_float, fallback_stages
    if not stages_value and fallback_stages:
        return end, minutes_float, fallback_stages

    stages: dict[str, float] = {}
    for stage_value in stages_value:
        stage = _mapping(stage_value)
        normalized_type = _sleep_stage_type(stage.get("type") if stage is not None else None)
        stage_minutes = _sleep_stage_minutes(stage, start, end) if stage is not None else None
        if normalized_type is None:
            continue
        if stage_minutes is None:
            if fallback_stages:
                return end, minutes_float, fallback_stages
            return None
        if _sleep_stage_is_summary_total(stage):
            stages[normalized_type] = max(stages.get(normalized_type, 0.0), stage_minutes)
        else:
            stages[normalized_type] = stages.get(normalized_type, 0.0) + stage_minutes
    if not stages:
        return end, minutes_float, fallback_stages
    stage_total_seconds = sum(stages.values()) * 60
    if stage_total_seconds > interval_seconds:
        if fallback_stages:
            return end, minutes_float, fallback_stages
        return None
    for stage_type, stage_minutes in fallback_stages.items():
        stages.setdefault(stage_type, stage_minutes)
    return end, minutes_float, stages


def _sleep_stage_type(value: object) -> str | None:
    """Normalize Google and Health Connect sleep stage labels."""
    if not isinstance(value, str):
        return None
    stage_type = value.strip().upper().replace("-", "_").replace(" ", "_")
    for prefix in ("SLEEP_STAGE_TYPE_", "SLEEP_STAGE_", "STAGE_TYPE_", "STAGE_"):
        stage_type = stage_type.removeprefix(prefix)
    if stage_type.endswith("_SLEEP"):
        stage_type = stage_type.removesuffix("_SLEEP")
    if mapped := _SLEEP_STAGE_TYPES.get(stage_type):
        return mapped

    tokens = frozenset(part for part in stage_type.split("_") if part)
    if "REM" in tokens:
        return "rem"
    if "DEEP" in tokens:
        return "deep"
    if "LIGHT" in tokens:
        return "light"
    if {"AWAKE", "WAKE", "OUT"} & tokens:
        return "awake"
    if "RESTLESS" in tokens:
        return "restless"
    if {"ASLEEP", "SLEEPING"} & tokens:
        return "asleep"
    return None


def _sleep_stage_minutes(
    stage: Mapping[str, object], session_start: datetime, session_end: datetime
) -> float | None:
    """Read a stage summary duration or derive one from a stage interval."""
    explicit_minutes = _non_negative_integer(stage.get("minutes"))
    if explicit_minutes is not None:
        return _finite_float(explicit_minutes)

    interval = _stage_interval(stage)
    if interval is None:
        return None
    start, end = interval
    if start < session_start or end > session_end:
        return None
    return (end - start).total_seconds() / 60


def _sleep_stage_is_summary_total(stage: Mapping[str, object] | None) -> bool:
    """Identify per-stage total rows, which may repeat in reconciled summaries."""
    return (
        stage is not None
        and _non_negative_integer(stage.get("minutes")) is not None
        and _non_negative_integer(stage.get("count")) is not None
    )


def _stage_interval(stage: Mapping[str, object]) -> tuple[datetime, datetime] | None:
    """Accept nested or flat stage intervals without requiring UTC offset fields."""
    interval = _mapping(stage.get("interval")) or stage
    start = _timestamp(interval.get("startTime"))
    end = _timestamp(interval.get("endTime"))
    if start is None or end is None or start >= end:
        return None
    return start, end


def _sleep_summary_fallback_stages(
    summary: Mapping[str, object], interval_seconds: float
) -> Mapping[str, float]:
    """Extract safe stage hints from Google summary fields."""
    awake_minutes = _non_negative_integer(summary.get("minutesAwake"))
    if awake_minutes is None or awake_minutes * 60 > interval_seconds:
        return {}
    normalized_awake = _finite_float(awake_minutes)
    if normalized_awake is None:
        return {}
    return {"awake": normalized_awake}


def _workouts(stream: DataPointStreams) -> tuple[WorkoutSummary, ...]:
    """Normalize complete reconciled workouts, omitting malformed individual records."""
    points = _metric_points(stream, "exercise")
    if not points:
        return ()

    workouts: list[WorkoutSummary] = []
    for point in points:
        payload = _mapping(point.get("exercise"))
        workout = _workout(payload)
        if workout is not None:
            workouts.append(workout)
    return tuple(sorted(workouts, key=lambda workout: workout.start or datetime.min))


def _workout(payload: Mapping[str, object] | None) -> WorkoutSummary | None:
    """Validate one exercise session and convert its duration to minutes."""
    if payload is None:
        return None
    activity_type = payload.get("exerciseType")
    display_name = payload.get("displayName")
    physical_interval = _physical_interval(payload.get("interval"))
    duration = _duration_seconds(payload.get("activeDuration"))
    metrics = _mapping(payload.get("metricsSummary"))
    if (
        not isinstance(activity_type, str)
        or not activity_type
        or not isinstance(display_name, str)
        or not display_name
        or physical_interval is None
        or duration is None
        or metrics is None
    ):
        return None
    start, end = physical_interval
    if duration > (end - start).total_seconds():
        return None

    active_energy = None
    if "caloriesKcal" in metrics:
        active_energy = _non_negative_float(metrics["caloriesKcal"])
        if active_energy is None:
            return None
    duration_minutes = _finite_divide(duration, 60)
    if duration_minutes is None:
        return None
    return WorkoutSummary(
        activity_type=activity_type,
        duration_minutes=duration_minutes,
        start=start,
        end=end,
        active_energy_kcal=active_energy,
    )


def _metric_points(stream: DataPointStreams, data_type: str) -> tuple[DataPoint, ...] | None:
    """Validate a metric collection's outer JSON shape."""
    points = stream.get(data_type)
    if points is None:
        return None
    normalized = _points_or_empty(points)
    return normalized or None


def _points_or_empty(value: object) -> tuple[DataPoint, ...]:
    """Return only a sequence of mapping-shaped JSON data points."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    points: list[DataPoint] = []
    for point in value:
        mapped = _mapping(point)
        if mapped is None:
            return ()
        points.append(mapped)
    return tuple(points)


def _mapping(value: object) -> Mapping[str, object] | None:
    """Narrow untrusted JSON objects to string-keyed mappings."""
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        return None
    return cast(Mapping[str, object], value)


def _has_observation_interval(payload: Mapping[str, object]) -> bool:
    """Validate the required physical interval on an observation record."""
    return _physical_interval(payload.get("interval")) is not None


def _has_sample_time(payload: Mapping[str, object]) -> bool:
    """Validate the required physical timestamp and UTC offset on a sample."""
    sample_time = _mapping(payload.get("sampleTime"))
    if sample_time is None:
        return False
    return (
        _timestamp(sample_time.get("physicalTime")) is not None
        and _utc_offset_seconds(sample_time.get("utcOffset")) is not None
    )


def _has_daily_date(payload: Mapping[str, object]) -> bool:
    """Validate a complete calendar date on a daily summary record."""
    value = _mapping(payload.get("date"))
    if value is None:
        return False
    year = value.get("year")
    month = value.get("month")
    day = value.get("day")
    if not all(type(part) is int for part in (year, month, day)):
        return False
    try:
        date(cast(int, year), cast(int, month), cast(int, day))
    except ValueError:
        return False
    return True


def _physical_interval(value: object) -> tuple[datetime, datetime] | None:
    """Validate a complete v4 observation or session physical interval."""
    interval = _mapping(value)
    if interval is None:
        return None
    start = _timestamp(interval.get("startTime"))
    end = _timestamp(interval.get("endTime"))
    start_offset = _utc_offset_seconds(interval.get("startUtcOffset"))
    end_offset = _utc_offset_seconds(interval.get("endUtcOffset"))
    if start is None or end is None or start_offset is None or end_offset is None or start >= end:
        return None
    return start, end


def _non_negative_integer(value: object) -> int | None:
    """Accept only non-negative JSON int64 values represented by Google REST."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif (
        isinstance(value, str) and value.isascii() and re.fullmatch(r"-?[0-9]+", value) is not None
    ):
        digits = value.removeprefix("-")
        if len(digits) > 19:
            return None
        parsed = int(value)
    else:
        return None
    if _INT64_MIN <= parsed <= _INT64_MAX and parsed >= 0:
        return parsed
    return None


def _non_negative_float(value: object) -> float | None:
    """Accept only finite non-negative JSON double values."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    normalized = _finite_float(value)
    return normalized if normalized is not None and normalized >= 0 else None


def _duration_seconds(value: object) -> float | None:
    """Parse a non-negative protobuf JSON duration with nanosecond precision."""
    seconds = _protobuf_duration_seconds(value)
    return seconds if seconds is not None and seconds >= 0 else None


def _utc_offset_seconds(value: object) -> float | None:
    """Parse a protobuf UTC offset within the platform's sensible range."""
    seconds = _protobuf_duration_seconds(value)
    if seconds is None or abs(seconds) > _MAX_UTC_OFFSET_SECONDS:
        return None
    return seconds


def _protobuf_duration_seconds(value: object) -> float | None:
    """Parse a finite signed protobuf JSON duration bounded by its schema."""
    if not isinstance(value, str) or _DURATION_RE.fullmatch(value) is None:
        return None
    seconds = float(value[:-1])
    if not isfinite(seconds) or abs(seconds) > _MAX_PROTOBUF_DURATION_SECONDS:
        return None
    return seconds


def _timestamp(value: object) -> datetime | None:
    """Parse only timezone-aware RFC 3339 timestamps from Google's JSON responses."""
    if not isinstance(value, str) or _RFC3339_RE.fullmatch(value) is None:
        return None
    try:
        normalized = f"{value[:-1]}+00:00" if value.endswith("Z") else value
        timestamp = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        return None
    return timestamp


def _as_float(value: int | None) -> float | None:
    """Convert an optional integer metric to its native floating-point model field."""
    return _finite_float(value) if value is not None else None


def _finite_float(value: int | float) -> float | None:
    """Convert a numeric value only when the floating-point result is finite."""
    try:
        normalized = float(value)
    except OverflowError:
        return None
    return normalized if isfinite(normalized) else None


def _finite_divide(value: int | float, divisor: int | float) -> float | None:
    """Divide numeric values only when the result is finite and non-negative."""
    try:
        normalized = value / divisor
    except OverflowError:
        return None
    return float(normalized) if isfinite(normalized) and normalized >= 0 else None
