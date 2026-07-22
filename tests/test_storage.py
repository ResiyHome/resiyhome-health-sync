"""Tests for durable, normalized Health Sync daily history."""

import asyncio
import json
import os
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, timedelta, timezone
from math import inf, nan
from pathlib import Path

import pytest
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.helpers.storage import Store

from custom_components.resiyhome_health_sync.models import (
    DailySummary,
    ExpandedDailyMetrics,
    SourceKind,
    WorkoutSummary,
)
from custom_components.resiyhome_health_sync.storage import HealthHistoryStore, HistoryStoreError


def summary_for(day: str, **overrides: object) -> DailySummary:
    """Create a complete immutable summary so persistence covers every field."""
    values: dict[str, object] = {
        "date": date.fromisoformat(day),
        "steps": 6200,
        "fitbit_steps": 5800,
        "distance_m": 4675.25,
        "active_energy_kcal": 512.5,
        "exercise_minutes": 45.0,
        "sleep_minutes": 402.0,
        "sleep_stages": {"deep": 92.0, "light": 240.0, "rem": 70.0},
        "resting_heart_rate": 54.0,
        "average_heart_rate": 76.5,
        "minimum_heart_rate": 48.0,
        "maximum_heart_rate": 146.0,
        "hrv_ms": 38.25,
        "workouts": (
            WorkoutSummary(
                activity_type="WALKING",
                duration_minutes=30.5,
                start=datetime(2042, 7, 12, 14, 30, tzinfo=timezone(timedelta(hours=-4))),
                end=datetime(2042, 7, 12, 15, 1, tzinfo=timezone(timedelta(hours=-4))),
                active_energy_kcal=120.75,
            ),
        ),
        "source": SourceKind.MIXED,
        "complete": True,
        "updated_at": datetime(2042, 7, 13, 1, 15, 30, 123456, tzinfo=UTC),
    }
    values.update(overrides)
    return DailySummary(**values)  # type: ignore[arg-type]


@pytest.fixture
def store(hass) -> HealthHistoryStore:
    """Create an isolated Store-backed history instance."""
    return HealthHistoryStore(hass, "entry-id")


@pytest.fixture(autouse=True)
def mirror_history_store_writes_to_disk(hass, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep the mocked Store writer and this component's direct reader aligned."""
    original_write = Store._async_write_data
    storage_dir = Path(hass.config.path(".storage"))

    def remove_history_files() -> None:
        if storage_dir.exists():
            for path in storage_dir.glob("resiyhome_health_sync.*.history"):
                path.unlink()

    async def mirror_write(store: Store[object], data: dict[str, object]) -> None:
        await original_write(store, data)
        if not store.key.startswith("resiyhome_health_sync."):
            return
        path = Path(store.path)
        serialized = json.dumps(data)

        def write_document() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(serialized)

        await store.hass.async_add_executor_job(write_document)

    remove_history_files()
    monkeypatch.setattr(Store, "_async_write_data", mirror_write)
    yield
    monkeypatch.undo()
    remove_history_files()


def _summary_payload(summary: DailySummary) -> dict[str, object]:
    """Return a deliberately explicit persisted v1 summary fixture."""
    return {
        "date": summary.date.isoformat(),
        "steps": summary.steps,
        "fitbit_steps": summary.fitbit_steps,
        "distance_m": summary.distance_m,
        "active_energy_kcal": summary.active_energy_kcal,
        "exercise_minutes": summary.exercise_minutes,
        "sleep_minutes": summary.sleep_minutes,
        "sleep_stages": dict(summary.sleep_stages),
        "resting_heart_rate": summary.resting_heart_rate,
        "average_heart_rate": summary.average_heart_rate,
        "minimum_heart_rate": summary.minimum_heart_rate,
        "maximum_heart_rate": summary.maximum_heart_rate,
        "hrv_ms": summary.hrv_ms,
        "workouts": [
            {
                "activity_type": workout.activity_type,
                "duration_minutes": workout.duration_minutes,
                "start": workout.start.isoformat() if workout.start else None,
                "end": workout.end.isoformat() if workout.end else None,
                "active_energy_kcal": workout.active_energy_kcal,
            }
            for workout in summary.workouts
        ],
        "source": summary.source.value,
        "complete": summary.complete,
        "updated_at": summary.updated_at.isoformat() if summary.updated_at else None,
    }


def _payload(summary: DailySummary, *, cursor: str | None = None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "summaries": {summary.date.isoformat(): _summary_payload(summary)},
        "backfill_cursor": cursor,
    }


def _expanded_metrics() -> ExpandedDailyMetrics:
    """Create a fully populated reviewed expanded-metrics value."""
    return ExpandedDailyMetrics(
        active_zone_minutes={"fat_burn": 12.0, "cardio": 8.0, "peak": 4.0},
        vo2_max=42.5,
        vo2_estimated=False,
        cardio_fitness_level="GOOD",
        oxygen_average=96.2,
        oxygen_lower_bound=95.1,
        oxygen_upper_bound=97.3,
        oxygen_standard_deviation=0.4,
        daily_respiratory_rate=15.4,
        sleep_respiratory_rates={"deep": 14.1, "light": 15.2, "rem": 14.6, "full": 14.8},
        sleep_respiratory_standard_deviation=0.7,
        sleep_respiratory_signal_to_noise=3.2,
        floors=7,
        sedentary_minutes=480.0,
        heart_zone_minutes={"vigorous": 23.5},
        heart_zone_thresholds={"vigorous": (133, 159)},
        heart_zone_calories={"vigorous": 184.2},
        weight_kg=80.5,
    )


def _v2_payload(
    summary: DailySummary,
    *,
    cursor: str | None = None,
    expanded_cursor: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 2,
        "summaries": {
            summary.date.isoformat(): _summary_payload(summary)
            | {"expanded": _expanded_payload(summary.expanded)}
        },
        "backfill_cursor": cursor,
        "expanded_backfill_cursor": expanded_cursor,
    }


def _v3_payload(
    summary: DailySummary,
    *,
    cursor: str | None = None,
    expanded_cursor: str | None = None,
    body_measurements_enabled: bool = False,
) -> dict[str, object]:
    return {
        **_v2_payload(
            summary,
            cursor=cursor,
            expanded_cursor=expanded_cursor,
        ),
        "schema_version": 3,
        "body_measurements_enabled": body_measurements_enabled,
    }


def _expanded_payload(expanded: ExpandedDailyMetrics) -> dict[str, object]:
    return {
        "active_zone_minutes": dict(expanded.active_zone_minutes),
        "vo2_max": expanded.vo2_max,
        "vo2_estimated": expanded.vo2_estimated,
        "cardio_fitness_level": expanded.cardio_fitness_level,
        "oxygen_average": expanded.oxygen_average,
        "oxygen_lower_bound": expanded.oxygen_lower_bound,
        "oxygen_upper_bound": expanded.oxygen_upper_bound,
        "oxygen_standard_deviation": expanded.oxygen_standard_deviation,
        "daily_respiratory_rate": expanded.daily_respiratory_rate,
        "sleep_respiratory_rates": dict(expanded.sleep_respiratory_rates),
        "sleep_respiratory_standard_deviation": expanded.sleep_respiratory_standard_deviation,
        "sleep_respiratory_signal_to_noise": expanded.sleep_respiratory_signal_to_noise,
        "floors": expanded.floors,
        "sedentary_minutes": expanded.sedentary_minutes,
        "heart_zone_minutes": dict(expanded.heart_zone_minutes),
        "heart_zone_thresholds": {
            zone: list(thresholds) for zone, thresholds in expanded.heart_zone_thresholds.items()
        },
        "heart_zone_calories": dict(expanded.heart_zone_calories),
        "weight_kg": expanded.weight_kg,
    }


async def test_schema_v1_migration_preserves_every_core_field_and_adds_empty_expanded(
    hass,
    store: HealthHistoryStore,
) -> None:
    """A validated v1 document is rewritten once as lossless current-schema data."""
    summary = summary_for("2042-07-12")
    original = _payload(summary, cursor="2042-07-01")
    await Store[dict[str, object]](hass, 1, store.key).async_save(original)

    assert await store.async_load() == [summary]
    assert store.backfill_cursor == date(2042, 7, 1)
    assert store.expanded_backfill_cursor is None

    migrated = await Store[dict[str, object]](hass, 1, store.key).async_load()
    assert migrated == _v3_payload(summary, cursor="2042-07-01")


async def test_expanded_summary_round_trip_preserves_every_reviewed_field(
    hass,
    store: HealthHistoryStore,
) -> None:
    """Expanded immutable mappings and scalars remain lossless across Store reload."""
    summary = summary_for("2042-07-13", expanded=_expanded_metrics())

    await store.async_upsert(summary)
    await store.async_set_backfill_checkpoint(date(2042, 7, 1))

    reloaded = HealthHistoryStore(hass, "entry-id")
    assert await reloaded.async_load() == [summary]


async def test_expanded_checkpoint_commits_summary_and_cursor_without_changing_core_cursor(
    hass,
    store: HealthHistoryStore,
) -> None:
    """Expanded backfill can advance independently while preserving the core checkpoint."""
    await store.async_set_backfill_checkpoint(date(2042, 7, 1))
    summary = summary_for("2042-07-13", expanded=_expanded_metrics())

    await store.async_checkpoint_expanded(summary, date(2042, 7, 12))

    assert store.backfill_cursor == date(2042, 7, 1)
    assert store.expanded_backfill_cursor == date(2042, 7, 12)
    reloaded = HealthHistoryStore(hass, "entry-id")
    assert await reloaded.async_load() == [summary]
    assert reloaded.backfill_cursor == date(2042, 7, 1)
    assert reloaded.expanded_backfill_cursor == date(2042, 7, 12)


async def test_enabling_body_measurements_resets_completed_expanded_cursor_once(
    hass,
    store: HealthHistoryStore,
) -> None:
    """The option transition is durable and a restart cannot restart it again."""
    today = date(2042, 7, 21)
    await store.async_load()
    await store.async_checkpoint_expanded(
        summary_for("2042-04-22"), date(2042, 4, 22)
    )

    await store.async_apply_body_measurement_option(True, today)

    assert store.body_measurements_enabled is True
    assert store.expanded_backfill_cursor == today
    await store.async_checkpoint_expanded(
        summary_for("2042-07-20"), date(2042, 7, 7)
    )
    restarted = HealthHistoryStore(hass, "entry-id")
    await restarted.async_load()
    await restarted.async_apply_body_measurement_option(True, today)
    assert restarted.body_measurements_enabled is True
    assert restarted.expanded_backfill_cursor == date(2042, 7, 7)


async def test_disabling_body_measurements_transactionally_scrubs_all_weight(
    hass,
    store: HealthHistoryStore,
) -> None:
    """Opt-out removes every stored weight while preserving unrelated metrics and cursor."""
    today = date(2042, 7, 21)
    weighted = summary_for("2042-07-15", expanded=_expanded_metrics())
    await store.async_load()
    await store.async_apply_body_measurement_option(True, today)
    await store.async_checkpoint_expanded(weighted, date(2042, 7, 7))

    rows = await store.async_apply_body_measurement_option(False, today)

    assert store.body_measurements_enabled is False
    assert store.expanded_backfill_cursor == date(2042, 7, 7)
    assert rows[0].expanded.weight_kg is None
    assert rows[0].expanded.vo2_max == 42.5
    restarted = HealthHistoryStore(hass, "entry-id")
    reloaded = await restarted.async_load()
    assert restarted.body_measurements_enabled is False
    assert reloaded[0].expanded.weight_kg is None
    assert reloaded[0].expanded.vo2_max == 42.5


async def test_body_option_save_failure_leaves_summaries_and_cursors_unchanged(
    hass,
    store: HealthHistoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed opt-out write cannot partially scrub process memory."""
    today = date(2042, 7, 21)
    weighted = summary_for("2042-07-15", expanded=_expanded_metrics())
    await store.async_load()
    await store.async_apply_body_measurement_option(True, today)
    await store.async_checkpoint_expanded(weighted, date(2042, 7, 7))

    async def fail_save(_document: dict[str, object]) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(store._store, "async_save", fail_save)

    with pytest.raises(HistoryStoreError, match="persist body measurement option"):
        await store.async_apply_body_measurement_option(False, today)

    assert store.body_measurements_enabled is True
    assert store.expanded_backfill_cursor == date(2042, 7, 7)
    assert (await store.async_query(date(2042, 7, 15), date(2042, 7, 15)))[
        0
    ].expanded.weight_kg == 80.5


async def test_expanded_checkpoint_save_failure_keeps_in_memory_summary_and_cursor(
    store: HealthHistoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The durable save must succeed before a checkpoint replaces process state."""
    baseline = summary_for("2042-07-14", expanded=ExpandedDailyMetrics(floors=6))
    replacement = summary_for("2042-07-14", expanded=ExpandedDailyMetrics(floors=7))
    await store.async_load()
    await store.async_checkpoint_expanded(baseline, date(2042, 7, 14))

    async def fail_save(_document: dict[str, object]) -> None:
        raise OSError("disk unavailable")

    monkeypatch.setattr(store._store, "async_save", fail_save)

    with pytest.raises(OSError, match="disk unavailable"):
        await store.async_checkpoint_expanded(replacement, date(2042, 7, 13))

    assert store.expanded_backfill_cursor == date(2042, 7, 14)
    assert (await store.async_query(date(2042, 7, 14), date(2042, 7, 14)))[
        0
    ] == baseline


@pytest.mark.parametrize(
    ("path", "value", "message"),
    [
        (("unexpected",), 1, "expanded has invalid fields"),
        (("active_zone_minutes", "unknown"), 1.0, "active_zone_minutes contains an invalid zone"),
        (("heart_zone_thresholds", "vigorous"), [160, 133], "heart_zone_thresholds"),
        (("oxygen_average",), nan, "oxygen_average"),
    ],
)
async def test_invalid_expanded_history_fails_closed(
    hass,
    store: HealthHistoryStore,
    path: tuple[str, ...],
    value: object,
    message: str,
) -> None:
    """Persisted expanded values must remain within the reviewed normalized contract."""
    payload = _v2_payload(summary_for("2042-07-13", expanded=_expanded_metrics()))
    summaries = payload["summaries"]
    assert isinstance(summaries, dict)
    row = summaries["2042-07-13"]
    assert isinstance(row, dict)
    expanded = row["expanded"]
    assert isinstance(expanded, dict)
    target = expanded
    for key in path[:-1]:
        nested = target[key]
        assert isinstance(nested, dict)
        target = nested
    target[path[-1]] = value

    source = json.dumps(
        {
            "version": 1,
            "minor_version": 1,
            "key": store.key,
            "data": payload,
        }
    )
    store_path = Path(store._store.path)

    def write_document() -> None:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(source)

    await hass.async_add_executor_job(write_document)
    expected_message = "corrupt" if isinstance(value, float) and value != value else message
    with pytest.raises(HistoryStoreError, match=expected_message):
        await store.async_load()
    assert await hass.async_add_executor_job(store_path.read_text) == source


async def test_v2_duplicate_expanded_json_key_and_unsupported_schema_do_not_rewrite_source(
    hass,
    store: HealthHistoryStore,
) -> None:
    """The direct reader leaves malformed or unsupported schema-v2 files untouched."""
    summary = summary_for("2042-07-13", expanded=_expanded_metrics())
    document = json.dumps(
        {
            "version": 1,
            "minor_version": 1,
            "key": store.key,
            "data": _v2_payload(summary),
        },
        separators=(",", ":"),
    )
    duplicate = document.replace('"vo2_max":42.5', '"vo2_max":42.5,"vo2_max":42.5', 1)
    unsupported = document.replace('"schema_version":2', '"schema_version":4', 1)
    path = Path(store._store.path)

    for source, message in ((duplicate, "duplicate"), (unsupported, "unsupported")):
        def write_document() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source)

        await hass.async_add_executor_job(write_document)
        reader = HealthHistoryStore(hass, "entry-id")
        with pytest.raises(HistoryStoreError, match=message):
            await reader.async_load()
        assert await hass.async_add_executor_job(path.read_text) == source


async def test_upsert_replaces_one_date_without_disturbing_other_dates(
    store: HealthHistoryStore,
) -> None:
    """Later normalization for a date replaces just that date's summary."""
    await store.async_upsert(summary_for("2042-07-12", steps=5000))
    await store.async_upsert(summary_for("2042-07-13", steps=6200))
    await store.async_upsert(summary_for("2042-07-13", steps=6300))

    rows = await store.async_query(date(2042, 7, 12), date(2042, 7, 13))

    assert [row.steps for row in rows] == [5000, 6300]


async def test_query_orders_dates_and_uses_inclusive_bounds(store: HealthHistoryStore) -> None:
    """History queries are chronological and include both requested endpoints."""
    await store.async_upsert(summary_for("2042-07-14", steps=7000))
    await store.async_upsert(summary_for("2042-07-12", steps=5000))
    await store.async_upsert(summary_for("2042-07-13", steps=6000))

    rows = await store.async_query(date(2042, 7, 13), date(2042, 7, 14))

    assert [(row.date, row.steps) for row in rows] == [
        (date(2042, 7, 13), 6000),
        (date(2042, 7, 14), 7000),
    ]


async def test_per_entry_history_is_isolated(hass) -> None:
    """Each config entry writes and reads an independent history document."""
    first = HealthHistoryStore(hass, "first-entry")
    second = HealthHistoryStore(hass, "second-entry")

    await first.async_upsert(summary_for("2042-07-12", steps=5000))
    await second.async_upsert(summary_for("2042-07-12", steps=6000))
    await first.async_set_backfill_checkpoint(date(2042, 7, 1))
    await second.async_set_backfill_checkpoint(date(2042, 7, 2))

    first_reloaded = HealthHistoryStore(hass, "first-entry")
    second_reloaded = HealthHistoryStore(hass, "second-entry")
    assert [row.steps for row in await first_reloaded.async_load()] == [5000]
    assert [row.steps for row in await second_reloaded.async_load()] == [6000]
    assert first_reloaded.backfill_cursor == date(2042, 7, 1)
    assert second_reloaded.backfill_cursor == date(2042, 7, 2)


async def test_round_trip_preserves_every_normalized_field_and_uses_exact_store_key(
    hass,
    store: HealthHistoryStore,
) -> None:
    """Only complete normalized summaries survive a durable Store round trip."""
    summary = summary_for("2042-07-13")

    assert store.key == "resiyhome_health_sync.entry-id.history"
    await store.async_upsert(summary)
    await store.async_set_backfill_checkpoint(date(2042, 7, 1))

    reloaded = HealthHistoryStore(hass, "entry-id")
    assert await reloaded.async_load() == [summary]
    assert reloaded.backfill_cursor == date(2042, 7, 1)

    raw = await Store[dict[str, object]](hass, 1, store.key).async_load()
    assert raw is not None
    assert raw["schema_version"] == 3
    assert raw["body_measurements_enabled"] is False
    assert raw["expanded_backfill_cursor"] is None
    assert {"raw", "id", "token", "credential", "google"}.isdisjoint(_all_keys(raw))
    summaries = raw["summaries"]
    assert isinstance(summaries, dict)
    persisted = summaries[summary.date.isoformat()]
    assert isinstance(persisted, dict)
    assert persisted["updated_at"] == summary.updated_at.isoformat()
    workouts = persisted["workouts"]
    assert isinstance(workouts, list)
    assert workouts[0]["start"] == summary.workouts[0].start.isoformat()
    assert workouts[0]["end"] == summary.workouts[0].end.isoformat()


async def test_normal_updates_delay_save_and_checkpoint_is_durable(
    store: HealthHistoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Frequent summary writes are batched while checkpoint writes are durable."""
    delayed: list[tuple[object, float]] = []
    saved: list[dict[str, object]] = []

    def capture_delay(data_func, delay: float) -> None:
        delayed.append((data_func(), delay))

    async def capture_save(data: dict[str, object]) -> None:
        saved.append(data)

    monkeypatch.setattr(store._store, "async_delay_save", capture_delay)
    monkeypatch.setattr(store._store, "async_save", capture_save)

    await store.async_upsert(summary_for("2042-07-13"))
    await store.async_set_backfill_checkpoint(date(2042, 7, 1))

    assert delayed and delayed[0][1] == 1.0
    assert saved == [delayed[0][0] | {"backfill_cursor": "2042-07-01"}]


async def test_delayed_save_uses_the_upsert_snapshot(
    store: HealthHistoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A delayed callback cannot serialize summaries added by later operations."""
    delayed: list[tuple[object, float]] = []

    def capture_delay(data_func, delay: float) -> None:
        delayed.append((data_func, delay))

    monkeypatch.setattr(store._store, "async_delay_save", capture_delay)

    await store.async_upsert(summary_for("2042-07-12", steps=5000))
    await store.async_upsert(summary_for("2042-07-13", steps=6000))

    first_snapshot, first_delay = delayed[0]
    assert first_delay == 1.0
    assert callable(first_snapshot)
    assert list(first_snapshot()["summaries"]) == ["2042-07-12"]


async def test_invalid_store_payload_raises_without_replacing_original_content(
    hass,
    store: HealthHistoryStore,
) -> None:
    """Malformed persisted data remains intact for investigation or restoration."""
    original = _payload(summary_for("2042-07-13"))
    original["summaries"] = {
        "2042-07-13": _summary_payload(summary_for("2042-07-13")) | {"steps": "not-an-integer"}
    }
    await Store[dict[str, object]](hass, 1, store.key).async_save(original)

    with pytest.raises(HistoryStoreError, match="steps"):
        await store.async_load()

    assert await Store[dict[str, object]](hass, 1, store.key).async_load() == original


async def test_corrupt_json_raises_without_renaming_or_resetting_history(
    hass,
    store: HealthHistoryStore,
) -> None:
    """Syntactically corrupt Store content remains intact for manual recovery."""
    original = "{not-valid-json"
    path = Path(store._store.path)

    def write_corrupt_document() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(original)

    await hass.async_add_executor_job(write_corrupt_document)
    try:
        with pytest.raises(HistoryStoreError, match="corrupt"):
            await store.async_load()

        assert await hass.async_add_executor_job(path.read_text) == original
        assert not list(path.parent.glob(f"{path.name}.corrupt.*"))
    finally:
        await hass.async_add_executor_job(path.unlink)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("distance_m", nan),
        ("active_energy_kcal", inf),
        ("source", "unrecognized"),
        ("updated_at", "2042-07-13T01:15:30"),
        ("workouts", [{"activity_type": "WALKING"}]),
    ],
)
async def test_invalid_summary_shapes_are_rejected_on_load(
    store: HealthHistoryStore,
    field: str,
    value: object,
) -> None:
    """Stored fields must remain valid normalized values, not loosely typed JSON."""
    payload = _payload(summary_for("2042-07-13"))
    summary = payload["summaries"]
    assert isinstance(summary, dict)
    row = summary["2042-07-13"]
    assert isinstance(row, dict)
    row[field] = value

    with pytest.raises(HistoryStoreError):
        await store.async_load_payload(payload)


async def test_schema_v0_migration_preserves_complete_summary_and_is_idempotent(
    store: HealthHistoryStore,
) -> None:
    """The sole legacy shape migrates deterministically into the v1 document."""
    summary = summary_for("2042-07-12")
    legacy_payload = {
        "summaries": [_summary_payload(summary)],
        "backfill_cursor": "2042-07-01",
    }

    migrated_rows = await store.async_load_payload(legacy_payload)
    current_payload = _payload(summary, cursor="2042-07-01")
    current_rows = await store.async_load_payload(current_payload)

    assert migrated_rows == {summary.date: summary}
    assert current_rows == migrated_rows


async def test_home_assistant_store_v0_migration_rewrites_the_v1_document(hass) -> None:
    """Outer Store version migration retains every normalized field and checkpoint."""
    summary = summary_for("2042-07-12")
    key = "resiyhome_health_sync.entry-id.history"
    legacy_payload = {
        "summaries": [_summary_payload(summary)],
        "backfill_cursor": "2042-07-01",
    }
    await Store[dict[str, object]](hass, 0, key).async_save(legacy_payload)

    store = HealthHistoryStore(hass, "entry-id")
    assert await store.async_load() == [summary]
    assert store.backfill_cursor == date(2042, 7, 1)

    migrated = await Store[dict[str, object]](hass, 1, key).async_load()
    assert migrated == _v3_payload(summary, cursor="2042-07-01")


async def test_legacy_store_wrapper_without_minor_version_defaults_to_one(
    hass,
    store: HealthHistoryStore,
) -> None:
    """Home Assistant's historic missing minor_version remains readable as version one."""
    summary = summary_for("2042-07-12")
    path = Path(store._store.path)
    legacy_wrapper = {
        "version": 1,
        "key": store.key,
        "data": _payload(summary, cursor="2042-07-01"),
    }

    def write_legacy_wrapper() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(legacy_wrapper))

    await hass.async_add_executor_job(write_legacy_wrapper)
    assert await store.async_load() == [summary]
    assert store.backfill_cursor == date(2042, 7, 1)


async def test_outer_v0_malformed_summary_never_rewrites_the_store(hass) -> None:
    """Outer Store migration validates every nested field before HA auto-saves it."""
    store = HealthHistoryStore(hass, "entry-id")
    legacy = {
        "summaries": [_summary_payload(summary_for("2042-07-12"))],
        "backfill_cursor": "2042-07-01",
    }
    summaries = legacy["summaries"]
    assert isinstance(summaries, list)
    row = summaries[0]
    assert isinstance(row, dict)
    row["workouts"] = [{"activity_type": "WALKING"}]
    await Store[dict[str, object]](hass, 0, store.key).async_save(legacy)
    path = Path(store._store.path)
    original = await hass.async_add_executor_job(path.read_bytes)

    with pytest.raises(HistoryStoreError, match="workout"):
        await store.async_load()

    assert await hass.async_add_executor_job(path.read_bytes) == original


async def test_inner_v0_migration_saves_once_and_clean_current_load_does_not_save(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only validated inner v0 data gets one durable current-schema rewrite."""
    summary = summary_for("2042-07-12")
    legacy = {
        "summaries": [_summary_payload(summary)],
        "backfill_cursor": "2042-07-01",
    }
    key = "resiyhome_health_sync.entry-id.history"
    await Store[dict[str, object]](hass, 1, key).async_save(legacy)
    store = HealthHistoryStore(hass, "entry-id")
    saved: list[dict[str, object]] = []
    original_save = store._store.async_save

    async def capture_save(data: dict[str, object]) -> None:
        saved.append(data)
        await original_save(data)

    monkeypatch.setattr(store._store, "async_save", capture_save)

    assert await store.async_load() == [summary]
    assert saved == [_v3_payload(summary, cursor="2042-07-01")]
    assert await store.async_load() == [summary]
    assert saved == [_v3_payload(summary, cursor="2042-07-01")]

    clean = HealthHistoryStore(hass, "entry-id")
    clean_saves: list[dict[str, object]] = []
    clean_original_save = clean._store.async_save

    async def capture_clean_save(data: dict[str, object]) -> None:
        clean_saves.append(data)
        await clean_original_save(data)

    monkeypatch.setattr(clean._store, "async_save", capture_clean_save)
    assert await clean.async_load() == [summary]
    assert clean_saves == []


async def test_failed_reload_latches_writes_until_an_explicit_successful_repair(
    hass,
    store: HealthHistoryStore,
) -> None:
    """A failed reload cannot let stale state overwrite corrupted history."""
    await store.async_upsert(summary_for("2042-07-12", steps=5000))
    await store.async_set_backfill_checkpoint(date(2042, 7, 1))
    malformed = _payload(summary_for("2042-07-13"))
    summaries = malformed["summaries"]
    assert isinstance(summaries, dict)
    row = summaries["2042-07-13"]
    assert isinstance(row, dict)
    row["steps"] = "bad"
    await Store[dict[str, object]](hass, 1, store.key).async_save(malformed)

    with pytest.raises(HistoryStoreError, match="steps"):
        await store.async_load()
    with pytest.raises(HistoryStoreError, match="failed load"):
        await store.async_upsert(summary_for("2042-07-14", steps=7000))
    with pytest.raises(HistoryStoreError, match="failed load"):
        await store.async_set_backfill_checkpoint(date(2042, 7, 2))
    assert await Store[dict[str, object]](hass, 1, store.key).async_load() == malformed

    repaired = _payload(summary_for("2042-07-13", steps=6300), cursor="2042-07-02")
    await Store[dict[str, object]](hass, 1, store.key).async_save(repaired)
    store._store._data = None

    assert await store.async_load() == [summary_for("2042-07-13", steps=6300)]
    await store.async_upsert(summary_for("2042-07-14", steps=7000))
    await store.async_set_backfill_checkpoint(date(2042, 7, 3))
    assert store.backfill_cursor == date(2042, 7, 3)


async def test_valid_reload_retains_pending_summaries_before_a_checkpoint_flush(
    hass,
    store: HealthHistoryStore,
) -> None:
    """A valid disk read cannot replace a loaded delayed-write snapshot with stale history."""
    baseline = summary_for("2042-07-10", steps=4000)
    pending_first = summary_for("2042-07-11", steps=5000)
    pending_second = summary_for("2042-07-12", steps=6000)
    await Store[dict[str, object]](hass, 1, store.key).async_save(
        _payload(baseline, cursor="2042-07-01")
    )

    assert await store.async_load() == [baseline]
    await store.async_upsert(pending_first)
    assert await store.async_load() == [baseline, pending_first]
    await store.async_upsert(pending_second)
    await store.async_set_backfill_checkpoint(date(2042, 7, 2))

    reloaded = HealthHistoryStore(hass, "entry-id")
    assert await reloaded.async_load() == [baseline, pending_first, pending_second]
    assert reloaded.backfill_cursor == date(2042, 7, 2)


async def test_failed_reload_cancels_pending_delayed_and_final_writes(
    hass,
    store: HealthHistoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed reload cannot let a queued Store write replace corrupt history."""
    path = Path(store._store.path)
    corrupt = "{externally-corrupt-history"

    async def write_to_disk(data: dict[str, object]) -> None:
        if "data_func" in data:
            data["data"] = data.pop("data_func")()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    monkeypatch.setattr(store._store, "_async_write_data", write_to_disk)
    await store.async_upsert(summary_for("2042-07-12", steps=5000))

    def corrupt_store() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(corrupt)

    await hass.async_add_executor_job(corrupt_store)
    try:
        with pytest.raises(HistoryStoreError, match="corrupt"):
            await store.async_load()

        store._store._async_schedule_callback_delayed_write()
        await hass.async_block_till_done()
        hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
        await hass.async_block_till_done()

        assert await hass.async_add_executor_job(path.read_text) == corrupt
        assert not list(path.parent.glob(f"{path.name}.corrupt.*"))
    finally:
        if path.exists():
            await hass.async_add_executor_job(path.unlink)


async def test_single_read_boundary_rejects_atomic_corrupt_replacement_without_store_load(
    hass,
    store: HealthHistoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single-read boundary sees an atomic replacement without Store reset behavior."""
    path = Path(store._store.path)
    replacement = path.with_name(f"{path.name}.replacement")
    initial = {
        "version": 1,
        "minor_version": 1,
        "key": store.key,
        "data": _payload(summary_for("2042-07-13")),
    }
    corrupt = "{atomically-replaced-corrupt-history"

    def write_initial() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(initial))
        replacement.write_text(corrupt)

    await hass.async_add_executor_job(write_initial)
    original_reader = getattr(store._store, "_read_validated_store_document", None)
    read_calls = 0

    def replace_then_read() -> object:
        nonlocal read_calls
        read_calls += 1
        os.replace(replacement, path)
        assert original_reader is not None
        return original_reader()

    async def unexpected_store_load() -> None:
        raise AssertionError("history load must not invoke Store.async_load")

    monkeypatch.setattr(
        store._store, "_read_validated_store_document", replace_then_read, raising=False
    )
    monkeypatch.setattr(store._store, "async_load", unexpected_store_load)
    try:
        with pytest.raises(HistoryStoreError, match="corrupt"):
            await store.async_load()

        assert read_calls == 1
        assert await hass.async_add_executor_job(path.read_text) == corrupt
        assert not list(path.parent.glob(f"{path.name}.corrupt.*"))
    finally:
        for candidate in (path, replacement):
            if candidate.exists():
                await hass.async_add_executor_job(candidate.unlink)


async def test_concurrent_cold_start_upserts_share_one_load_and_preserve_both_dates(
    store: HealthHistoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Concurrent first uses cannot race state replacement after their loads finish."""
    first_load_started = asyncio.Event()
    first_load_release = asyncio.Event()
    second_load_release = asyncio.Event()
    load_calls = 0

    async def blocked_load() -> tuple[
        dict[date, DailySummary], date | None, date | None, bool, None, bool
    ]:
        nonlocal load_calls
        load_calls += 1
        if load_calls == 1:
            first_load_started.set()
            await first_load_release.wait()
        else:
            await second_load_release.wait()
        return {}, None, None, False, None, False

    monkeypatch.setattr(store._store, "async_load_validated_history", blocked_load)
    first = asyncio.create_task(store.async_upsert(summary_for("2042-07-12", steps=5000)))
    await first_load_started.wait()
    second = asyncio.create_task(store.async_upsert(summary_for("2042-07-13", steps=6000)))
    await asyncio.sleep(0)
    second_load_release.set()
    first_load_release.set()
    await asyncio.gather(first, second)

    assert load_calls == 1
    assert [row.steps for row in await store.async_query(date(2042, 7, 12), date(2042, 7, 13))] == [
        5000,
        6000,
    ]


async def test_concurrent_cold_upsert_and_checkpoint_preserve_both_values(
    store: HealthHistoryStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checkpointing cannot race an upsert's first-load state replacement."""
    first_load_started = asyncio.Event()
    first_load_release = asyncio.Event()
    second_load_release = asyncio.Event()
    load_calls = 0

    async def blocked_load() -> tuple[
        dict[date, DailySummary], date | None, date | None, bool, None, bool
    ]:
        nonlocal load_calls
        load_calls += 1
        if load_calls == 1:
            first_load_started.set()
            await first_load_release.wait()
        else:
            await second_load_release.wait()
        return {}, None, None, False, None, False

    monkeypatch.setattr(store._store, "async_load_validated_history", blocked_load)
    upsert = asyncio.create_task(store.async_upsert(summary_for("2042-07-13", steps=6000)))
    await first_load_started.wait()
    checkpoint = asyncio.create_task(store.async_set_backfill_checkpoint(date(2042, 7, 1)))
    await asyncio.sleep(0)
    second_load_release.set()
    first_load_release.set()
    await asyncio.gather(upsert, checkpoint)

    assert load_calls == 1
    assert store.backfill_cursor == date(2042, 7, 1)
    assert [row.steps for row in await store.async_query(date(2042, 7, 13), date(2042, 7, 13))] == [
        6000
    ]


@pytest.mark.parametrize(
    "timestamp",
    [
        "2042-07-13T01:15:30.123456Z",
        "2042-07-13 01:15:30.123456+00:00",
        "2042-07-13T01:15:30.123456",
        "2042-07-13T01:15:30.123456+0000",
    ],
)
async def test_timestamp_strings_must_match_canonical_isoformat_output(
    store: HealthHistoryStore,
    timestamp: str,
) -> None:
    """Accepted timestamps have exactly the serialized datetime.isoformat form."""
    payload = _payload(summary_for("2042-07-13"))
    summaries = payload["summaries"]
    assert isinstance(summaries, dict)
    row = summaries["2042-07-13"]
    assert isinstance(row, dict)
    row["updated_at"] = timestamp

    with pytest.raises(HistoryStoreError):
        await store.async_load_payload(payload)


@pytest.mark.parametrize("duplicate_kind", ["outer", "document", "summary", "workout"])
async def test_duplicate_json_keys_fail_closed_and_latch_writes(
    hass,
    store: HealthHistoryStore,
    duplicate_kind: str,
) -> None:
    """Preflight rejects duplicate keys before Home Assistant can collapse them."""
    original = _duplicate_key_store_json(summary_for("2042-07-13"), store.key, duplicate_kind)
    path = Path(store._store.path)

    def write_document() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(original)

    await hass.async_add_executor_job(write_document)
    try:
        with pytest.raises(HistoryStoreError, match="duplicate"):
            await store.async_load()
        with pytest.raises(HistoryStoreError, match="failed load"):
            await store.async_upsert(summary_for("2042-07-14", steps=7000))

        assert await hass.async_add_executor_job(path.read_text) == original
    finally:
        await hass.async_add_executor_job(path.unlink)


@pytest.mark.parametrize("invalid_cursor", ["2042-07", "2042-07-01T00:00:00+00:00", 1])
async def test_invalid_backfill_cursor_is_rejected(
    store: HealthHistoryStore,
    invalid_cursor: object,
) -> None:
    """A checkpoint is always a single ISO local date or absent."""
    with pytest.raises(HistoryStoreError, match="backfill_cursor"):
        await store.async_load_payload(_payload(summary_for("2042-07-13"), cursor=invalid_cursor))


async def test_query_rejects_invalid_or_reversed_date_bounds(store: HealthHistoryStore) -> None:
    """Date-bound semantics cannot silently broaden a dashboard query."""
    with pytest.raises(ValueError, match="start"):
        await store.async_query(date(2042, 7, 14), date(2042, 7, 13))
    with pytest.raises(TypeError, match="date"):
        await store.async_query(datetime(2042, 7, 13, tzinfo=UTC), date(2042, 7, 14))


def _all_keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        return set(value) | set().union(*(_all_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_all_keys(item) for item in value))
    return set()


def _duplicate_key_store_json(summary: DailySummary, key: str, kind: str) -> str:
    summary_payload = _summary_payload(summary)
    summary_json = json.dumps(summary_payload, separators=(",", ":"))
    if kind == "outer":
        document_json = _document_json(summary.date.isoformat(), summary_json)
        return (
            '{"version":1,"version":1,"minor_version":1,"key":'
            f'{json.dumps(key)},"data":{document_json}}}'
        )
    if kind == "document":
        document_json = (
            '{"schema_version":1,"schema_version":1,"summaries":'
            f'{{{json.dumps(summary.date.isoformat())}:{summary_json}}},"backfill_cursor":null}}'
        )
    elif kind == "summary":
        document_json = (
            '{"schema_version":1,"summaries":'
            f"{{{json.dumps(summary.date.isoformat())}:{summary_json},"
            f'{json.dumps(summary.date.isoformat())}:{summary_json}}},"backfill_cursor":null}}'
        )
    elif kind == "workout":
        workout_json = json.dumps(summary_payload["workouts"][0], separators=(",", ":"))
        duplicated_workout_json = workout_json.replace(
            '"activity_type":"WALKING"',
            '"activity_type":"WALKING","activity_type":"RUNNING"',
            1,
        )
        document_json = _document_json(
            summary.date.isoformat(), summary_json.replace(workout_json, duplicated_workout_json, 1)
        )
    else:
        raise AssertionError(f"unexpected duplicate kind: {kind}")
    return f'{{"version":1,"minor_version":1,"key":{json.dumps(key)},"data":{document_json}}}'


def _document_json(serialized_day: str, summary_json: str) -> str:
    return (
        '{"schema_version":1,"summaries":'
        f'{{{json.dumps(serialized_day)}:{summary_json}}},"backfill_cursor":null}}'
    )
