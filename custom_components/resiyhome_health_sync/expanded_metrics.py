"""Pure normalization for aggregate Google Health expanded daily metrics."""

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from math import isfinite
from typing import cast

from .models import ExpandedDailyMetrics

type DataPoint = Mapping[str, object]
type DataPointStreams = Mapping[str, Sequence[DataPoint]]

ACTIVE_ZONES = {"FAT_BURN": "fat_burn", "CARDIO": "cardio", "PEAK": "peak"}
HEART_ZONES = {
    "LIGHT": "light",
    "MODERATE": "moderate",
    "VIGOROUS": "vigorous",
    "PEAK": "peak",
}
SLEEP_PHASES = {
    "deepSleepStats": "deep",
    "lightSleepStats": "light",
    "remSleepStats": "rem",
}

_INT64_MAX = 2**63 - 1
_MAX_PROTOBUF_DURATION_SECONDS = 315_576_000_000
_MAX_PROTOBUF_DURATION_SECONDS_DIGITS = len(str(_MAX_PROTOBUF_DURATION_SECONDS))
_NANOSECONDS_PER_SECOND = 1_000_000_000
_MAX_PROTOBUF_DURATION_NANOSECONDS = (
    _MAX_PROTOBUF_DURATION_SECONDS * _NANOSECONDS_PER_SECOND
)
_MAX_UTC_OFFSET_SECONDS = 18 * 60 * 60
_DURATION_RE = re.compile(
    r"(?P<sign>-?)(?P<seconds>0|[1-9][0-9]*)(?:\.(?P<fraction>[0-9]{1,9}))?s"
)
_RFC3339_RE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]+)?(?:Z|[+-][0-9]{2}:[0-9]{2})"
)


def normalize_expanded_day(
    day: date,
    direct: DataPointStreams,
    rollups: DataPointStreams,
    *,
    include_weight: bool,
) -> ExpandedDailyMetrics:
    """Normalize only complete expanded metric groups for one requested day."""
    vo2_max, vo2_estimated, cardio_fitness_level = _daily_vo2_max(direct, day)
    oxygen_average, oxygen_lower_bound, oxygen_upper_bound, oxygen_standard_deviation = _oxygen(
        direct, day
    )
    (
        sleep_respiratory_rates,
        sleep_respiratory_standard_deviation,
        sleep_respiratory_signal_to_noise,
    ) = _sleep_respiratory_rate(direct, day)
    return ExpandedDailyMetrics(
        active_zone_minutes=_active_zone_minutes(rollups, day),
        vo2_max=vo2_max,
        vo2_estimated=vo2_estimated,
        cardio_fitness_level=cardio_fitness_level,
        oxygen_average=oxygen_average,
        oxygen_lower_bound=oxygen_lower_bound,
        oxygen_upper_bound=oxygen_upper_bound,
        oxygen_standard_deviation=oxygen_standard_deviation,
        daily_respiratory_rate=_daily_respiratory_rate(direct, day),
        sleep_respiratory_rates=sleep_respiratory_rates,
        sleep_respiratory_standard_deviation=sleep_respiratory_standard_deviation,
        sleep_respiratory_signal_to_noise=sleep_respiratory_signal_to_noise,
        floors=_floors(rollups, day),
        sedentary_minutes=_sedentary_minutes(rollups, day),
        heart_zone_minutes=_heart_zone_minutes(rollups, day),
        heart_zone_thresholds=_heart_zone_thresholds(direct, day),
        heart_zone_calories=_heart_zone_calories(rollups, day),
        weight_kg=_weight(direct, day) if include_weight else None,
    )


def _active_zone_minutes(stream: DataPointStreams, day: date) -> Mapping[str, float]:
    """Read the three documented active-zone daily-rollup fields together."""
    payload = _single_rollup_payload(stream, "active-zone-minutes", "activeZoneMinutes", day)
    if payload is None:
        return {}
    fields = {
        "FAT_BURN": "sumInFatBurnHeartZone",
        "CARDIO": "sumInCardioHeartZone",
        "PEAK": "sumInPeakHeartZone",
    }
    values: dict[str, float] = {}
    for source_zone, field in fields.items():
        value = _non_negative_integer(payload.get(field))
        if value is None:
            return {}
        values[ACTIVE_ZONES[source_zone]] = float(value)
    return values


def _daily_vo2_max(
    stream: DataPointStreams, day: date
) -> tuple[float | None, bool | None, str | None]:
    """Normalize the complete daily VO2 summary and its optional classifications."""
    payload = _single_daily_payload(stream, "daily-vo2-max", "dailyVo2Max", day)
    if payload is None:
        return None, None, None
    vo2_max = _non_negative_float(payload.get("vo2Max"))
    estimated = payload.get("estimated")
    fitness = payload.get("cardioFitnessLevel")
    if vo2_max is None:
        return None, None, None
    if estimated is not None and type(estimated) is not bool:
        return None, None, None
    if not isinstance(fitness, str | None) or fitness not in {
        None,
        "POOR",
        "FAIR",
        "AVERAGE",
        "GOOD",
        "VERY_GOOD",
        "EXCELLENT",
    }:
        return None, None, None
    return vo2_max, estimated, fitness


def _oxygen(
    stream: DataPointStreams, day: date
) -> tuple[float | None, float | None, float | None, float | None]:
    """Read daily oxygen statistics only when all required percentages are valid."""
    payload = _single_daily_payload(stream, "daily-oxygen-saturation", "dailyOxygenSaturation", day)
    if payload is None:
        return None, None, None, None
    average = _percentage(payload.get("averagePercentage"))
    lower_bound = _percentage(payload.get("lowerBoundPercentage"))
    upper_bound = _percentage(payload.get("upperBoundPercentage"))
    standard_deviation = _percentage(payload.get("standardDeviationPercentage"))
    if None in (average, lower_bound, upper_bound, standard_deviation):
        return None, None, None, None
    if lower_bound > upper_bound or not lower_bound <= average <= upper_bound:
        return None, None, None, None
    return average, lower_bound, upper_bound, standard_deviation


def _daily_respiratory_rate(stream: DataPointStreams, day: date) -> float | None:
    """Read one complete direct daily respiratory rate summary."""
    payload = _single_daily_payload(stream, "daily-respiratory-rate", "dailyRespiratoryRate", day)
    return _non_negative_float(payload.get("breathsPerMinute")) if payload is not None else None


def _sleep_respiratory_rate(
    stream: DataPointStreams, day: date
) -> tuple[Mapping[str, float], float | None, float | None]:
    """Read the required full-sleep data and any complete optional sleep phases."""
    payload = _single_sample_payload(
        stream, "respiratory-rate-sleep-summary", "respiratoryRateSleepSummary", day
    )
    if payload is None:
        return {}, None, None
    full_stats = _mapping(payload.get("fullSleepStats"))
    full = _sleep_statistic(full_stats, require_metadata=True)
    if full is None:
        return {}, None, None
    rates = {"full": full[0]}
    for field, phase in SLEEP_PHASES.items():
        if field not in payload:
            continue
        value = _sleep_statistic(_mapping(payload.get(field)), require_metadata=False)
        if value is None:
            return {}, None, None
        rates[phase] = value[0]
    return rates, full[1], full[2]


def _sleep_statistic(
    value: Mapping[str, object] | None, *, require_metadata: bool
) -> tuple[float, float | None, float | None] | None:
    """Validate a documented sleep respiratory statistic object."""
    if value is None:
        return None
    breaths = _non_negative_float(value.get("breathsPerMinute"))
    standard_deviation = _non_negative_float(value.get("standardDeviation"))
    signal_to_noise = _non_negative_float(value.get("signalToNoise"))
    if breaths is None:
        return None
    if require_metadata and (standard_deviation is None or signal_to_noise is None):
        return None
    if "standardDeviation" in value and standard_deviation is None:
        return None
    if "signalToNoise" in value and signal_to_noise is None:
        return None
    return breaths, standard_deviation, signal_to_noise


def _floors(stream: DataPointStreams, day: date) -> int | None:
    """Read the integer floors daily rollup."""
    payload = _single_rollup_payload(stream, "floors", "floors", day)
    return _non_negative_integer(payload.get("countSum")) if payload is not None else None


def _sedentary_minutes(stream: DataPointStreams, day: date) -> float | None:
    """Convert the total sedentary protobuf duration to minutes."""
    payload = _single_rollup_payload(stream, "sedentary-period", "sedentaryPeriod", day)
    seconds = _duration_seconds(payload.get("durationSum")) if payload is not None else None
    if seconds is None:
        return None
    minutes = seconds / 60
    return minutes if isfinite(minutes) else None


def _heart_zone_minutes(stream: DataPointStreams, day: date) -> Mapping[str, float]:
    """Convert per-zone daily rollup durations to minutes."""
    payload = _single_rollup_payload(stream, "time-in-heart-rate-zone", "timeInHeartRateZone", day)
    return _heart_zone_values(payload, "timeInHeartRateZones", "duration", _duration_minutes)


def _heart_zone_thresholds(stream: DataPointStreams, day: date) -> Mapping[str, tuple[int, int]]:
    """Read daily zone thresholds only when every returned row is valid and unique."""
    payload = _single_daily_payload(stream, "daily-heart-rate-zones", "dailyHeartRateZones", day)
    if payload is None:
        return {}
    rows = _sequence(payload.get("heartRateZones"))
    if not rows:
        return {}
    result: dict[str, tuple[int, int]] = {}
    for row in rows:
        mapped = _mapping(row)
        zone = mapped.get("heartRateZoneType") if mapped is not None else None
        minimum = _non_negative_integer(mapped.get("minBeatsPerMinute")) if mapped else None
        maximum = _non_negative_integer(mapped.get("maxBeatsPerMinute")) if mapped else None
        if (
            not isinstance(zone, str)
            or zone not in HEART_ZONES
            or minimum is None
            or maximum is None
        ):
            return {}
        normalized_zone = HEART_ZONES[zone]
        if normalized_zone in result or minimum > maximum:
            return {}
        result[normalized_zone] = (minimum, maximum)
    return result


def _heart_zone_calories(stream: DataPointStreams, day: date) -> Mapping[str, float]:
    """Read per-zone calorie daily rollup values."""
    payload = _single_rollup_payload(
        stream, "calories-in-heart-rate-zone", "caloriesInHeartRateZone", day
    )
    return _heart_zone_values(payload, "caloriesInHeartRateZones", "kcal", _non_negative_float)


def _heart_zone_values(
    payload: Mapping[str, object] | None,
    rows_key: str,
    value_key: str,
    parser: Callable[[object], float | None],
) -> Mapping[str, float]:
    """Normalize a non-empty, unique, allowlisted heart-zone row collection."""
    if payload is None:
        return {}
    rows = _sequence(payload.get(rows_key))
    if not rows:
        return {}
    result: dict[str, float] = {}
    for row in rows:
        mapped = _mapping(row)
        zone = mapped.get("heartRateZone") if mapped is not None else None
        value = parser(mapped.get(value_key)) if mapped is not None else None
        if not isinstance(zone, str) or zone not in HEART_ZONES or value is None:
            return {}
        normalized_zone = HEART_ZONES[zone]
        if normalized_zone in result:
            return {}
        result[normalized_zone] = value
    return result


def _duration_minutes(value: object) -> float | None:
    """Convert a valid non-negative protobuf duration to finite minutes."""
    seconds = _duration_seconds(value)
    if seconds is None:
        return None
    minutes = seconds / 60
    return minutes if isfinite(minutes) else None


def _weight(stream: DataPointStreams, day: date) -> float | None:
    """Convert one complete direct weight sample from grams to kilograms."""
    payload = _single_sample_payload(stream, "weight", "weight", day)
    grams = _non_negative_float(payload.get("weightGrams")) if payload is not None else None
    if grams is None:
        return None
    kilograms = grams / 1000
    return kilograms if isfinite(kilograms) else None


def _single_daily_payload(
    stream: DataPointStreams, data_type: str, payload_key: str, day: date
) -> Mapping[str, object] | None:
    """Require exactly one complete daily point for the requested calendar day."""
    payloads: list[Mapping[str, object]] = []
    for point in _sequence(stream.get(data_type)) or ():
        mapped = _mapping(point)
        payload = _mapping(mapped.get(payload_key)) if mapped is not None else None
        if payload is not None and _matches_day(payload.get("date"), day):
            payloads.append(payload)
    return payloads[0] if len(payloads) == 1 else None


def _single_sample_payload(
    stream: DataPointStreams, data_type: str, payload_key: str, day: date
) -> Mapping[str, object] | None:
    """Validate direct samples and retain the latest documented sample timestamp."""
    points = _sequence(stream.get(data_type))
    if not points:
        return None
    latest: tuple[datetime, Mapping[str, object]] | None = None
    for point in points:
        mapped = _mapping(point)
        payload = _mapping(mapped.get(payload_key)) if mapped is not None else None
        sample_time = _sample_timestamp(payload.get("sampleTime")) if payload is not None else None
        if payload is None or sample_time is None:
            return None
        timestamp, local_date = sample_time
        if local_date != day:
            continue
        if latest is None or timestamp > latest[0]:
            latest = timestamp, payload
    return latest[1] if latest is not None else None


def _single_rollup_payload(
    stream: DataPointStreams, data_type: str, payload_key: str, day: date
) -> Mapping[str, object] | None:
    """Require exactly one complete daily rollup window for the requested day."""
    matching = [
        point
        for value in (_sequence(stream.get(data_type)) or ())
        if (point := _mapping(value)) is not None
        and _rollup_window_matches_day(point, day)
    ]
    return _mapping(matching[0].get(payload_key)) if len(matching) == 1 else None


def _rollup_window_matches_day(point: Mapping[str, object], day: date) -> bool:
    """Validate the documented daily civil window before trusting its aggregate."""
    start = _civil_date_time(point.get("civilStartTime"))
    end = _civil_date_time(point.get("civilEndTime"))
    start_of_day = datetime(day.year, day.month, day.day)
    valid_ends = {
        (start_of_day + timedelta(hours=23, minutes=59, seconds=59), 0),
        (start_of_day + timedelta(days=1), 0),
    }
    return start == (start_of_day, 0) and end in valid_ends


def _civil_date_time(value: object) -> tuple[datetime, int] | None:
    """Parse a complete documented civil timestamp."""
    civil = _mapping(value)
    date_value = _mapping(civil.get("date")) if civil is not None else None
    time_value = (
        {}
        if civil is not None and "time" not in civil
        else _mapping(civil.get("time")) if civil is not None else None
    )
    if date_value is None or time_value is None:
        return None
    year, month, day = (date_value.get(part) for part in ("year", "month", "day"))
    hour = time_value.get("hours", 0)
    minute = time_value.get("minutes", 0)
    second = time_value.get("seconds", 0)
    nanos = time_value.get("nanos", 0)
    if not all(
        type(part) is int for part in (year, month, day, hour, minute, second, nanos)
    ):
        return None
    year_int = cast(int, year)
    month_int = cast(int, month)
    day_int = cast(int, day)
    hour_int = cast(int, hour)
    minute_int = cast(int, minute)
    second_int = cast(int, second)
    nanos_int = cast(int, nanos)
    if (
        not 0 <= hour_int <= 23
        or not 0 <= minute_int <= 59
        or not 0 <= second_int <= 59
        or not 0 <= nanos_int <= 999_999_999
    ):
        return None
    try:
        parsed = datetime(year_int, month_int, day_int, hour_int, minute_int, second_int)
    except ValueError:
        return None
    return parsed, nanos_int


def _matches_day(value: object, expected: date) -> bool:
    """Confirm a documented API date exactly matches the normalized day."""
    mapped = _mapping(value)
    if mapped is None:
        return False
    parts = tuple(mapped.get(part) for part in ("year", "month", "day"))
    if not all(type(part) is int for part in parts):
        return False
    try:
        return date(*cast(tuple[int, int, int], parts)) == expected
    except ValueError:
        return False


def _sample_timestamp(value: object) -> tuple[datetime, date] | None:
    """Parse a physical sample time with its bounded documented UTC offset."""
    mapped = _mapping(value)
    if mapped is None:
        return None
    timestamp = mapped.get("physicalTime")
    offset = _duration_seconds(mapped.get("utcOffset"), signed=True)
    if not isinstance(timestamp, str) or _RFC3339_RE.fullmatch(timestamp) is None:
        return None
    try:
        parsed = datetime.fromisoformat(
            f"{timestamp[:-1]}+00:00" if timestamp.endswith("Z") else timestamp
        )
    except ValueError:
        return None
    if parsed.tzinfo is None or offset is None or abs(offset) > _MAX_UTC_OFFSET_SECONDS:
        return None
    try:
        instant = parsed.astimezone(UTC)
        local_date = (instant + timedelta(seconds=offset)).date()
    except OverflowError:
        return None
    return instant, local_date


def _percentage(value: object) -> float | None:
    """Accept finite oxygen percentages in the API's inclusive documented range."""
    parsed = _non_negative_float(value)
    return parsed if parsed is not None and parsed <= 100 else None


def _non_negative_integer(value: object) -> int | None:
    """Accept API int64 JSON values without bools, negatives, or overflow."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str) and value.isascii() and re.fullmatch(r"[0-9]+", value):
        if len(value) > 19:
            return None
        parsed = int(value)
    else:
        return None
    return parsed if 0 <= parsed <= _INT64_MAX else None


def _non_negative_float(value: object) -> float | None:
    """Accept finite non-negative JSON numeric values only."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        parsed = float(value)
    except OverflowError:
        return None
    return parsed if isfinite(parsed) and parsed >= 0 else None


def _duration_seconds(value: object, *, signed: bool = False) -> float | None:
    """Parse a bounded protobuf JSON duration with nanosecond precision."""
    if not isinstance(value, str) or (match := _DURATION_RE.fullmatch(value)) is None:
        return None
    seconds_text = match.group("seconds")
    if len(seconds_text) > _MAX_PROTOBUF_DURATION_SECONDS_DIGITS:
        return None
    whole_seconds = int(seconds_text)
    fraction = match.group("fraction") or ""
    nanoseconds = int(fraction.ljust(9, "0")) if fraction else 0
    total_nanoseconds = whole_seconds * _NANOSECONDS_PER_SECOND + nanoseconds
    if match.group("sign"):
        total_nanoseconds = -total_nanoseconds
    if abs(total_nanoseconds) > _MAX_PROTOBUF_DURATION_NANOSECONDS:
        return None
    if not signed and total_nanoseconds < 0:
        return None
    return total_nanoseconds / _NANOSECONDS_PER_SECOND


def _mapping(value: object) -> Mapping[str, object] | None:
    """Narrow untrusted JSON objects to string-keyed mappings."""
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        return None
    return cast(Mapping[str, object], value)


def _sequence(value: object) -> Sequence[object] | None:
    """Narrow untrusted JSON arrays without accepting strings as rows."""
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    return value
