"""Tests for current-day Health Sync coordination."""

import asyncio
import logging
from collections import Counter
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed as CoordinatorUpdateFailed

from custom_components.resiyhome_health_sync.api import AuthenticationError, UpdateFailed
from custom_components.resiyhome_health_sync.capabilities import (
    CapabilityId,
    validate_granted_scopes,
)
from custom_components.resiyhome_health_sync.const import (
    BASE_SCOPES,
    NUTRITION_SCOPE,
    SCAN_INTERVAL,
    SETTINGS_SCOPE,
)
from custom_components.resiyhome_health_sync.coordinator import (
    OPTIONAL_PROBE_DATA_TYPES,
    HealthSyncCoordinator,
    _civil_date,
)
from custom_components.resiyhome_health_sync.models import (
    DailySummary,
    ExpandedDailyMetrics,
    SourceKind,
)


def _interval_point(day: date, payload_key: str, value_key: str, value: object) -> dict:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    end = start + timedelta(hours=1)
    return {
        payload_key: {
            "interval": {
                "startTime": start.isoformat().replace("+00:00", "Z"),
                "startUtcOffset": "0s",
                "endTime": end.isoformat().replace("+00:00", "Z"),
                "endUtcOffset": "0s",
            },
            value_key: value,
        }
    }


def _steps(day: date, value: int) -> dict:
    return _interval_point(day, "steps", "count", str(value))


def _distance(day: date, value: int) -> dict:
    return _interval_point(day, "distance", "millimeters", str(value))


def _daily_point(day: date, payload_key: str, **values: object) -> dict:
    return {
        payload_key: {
            "date": {"year": day.year, "month": day.month, "day": day.day},
            **values,
        }
    }


def _daily_rollup(day: date, payload_key: str, **values: object) -> dict:
    next_day = day + timedelta(days=1)
    return {
        "civilStartTime": {
            "date": {"year": day.year, "month": day.month, "day": day.day},
            "time": {"hours": 0, "minutes": 0, "seconds": 0, "nanos": 0},
        },
        "civilEndTime": {
            "date": {
                "year": next_day.year,
                "month": next_day.month,
                "day": next_day.day,
            },
            "time": {"hours": 0, "minutes": 0, "seconds": 0, "nanos": 0},
        },
        payload_key: values,
    }


def _weight(day: date, grams: float, *, hour: int = 8) -> dict:
    measured_at = datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
    return {
        "weight": {
            "sampleTime": {
                "physicalTime": measured_at.isoformat().replace("+00:00", "Z"),
                "utcOffset": "0s",
            },
            "weightGrams": grams,
        }
    }


def _body_fat(day: date, percentage: float, *, hour: int = 8) -> dict:
    measured_at = datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
    return {
        "bodyFat": {
            "sampleTime": {
                "physicalTime": measured_at.isoformat().replace("+00:00", "Z"),
                "utcOffset": "0s",
            },
            "percentage": percentage,
        }
    }


def _height(day: date, millimeters: float, *, hour: int = 8) -> dict:
    measured_at = datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
    return {
        "height": {
            "sampleTime": {
                "physicalTime": measured_at.isoformat().replace("+00:00", "Z"),
                "utcOffset": "0s",
            },
            "heightMillimeters": millimeters,
        }
    }


def _nutrition(day: date, kcal: object, *, hour: int = 8) -> dict:
    start = datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
    end = start + timedelta(minutes=30)
    return {
        "dataPointName": (
            "users/private/dataTypes/nutrition-log/dataPoints/"
            f"reconciled-nutrition-{hour}"
        ),
        "nutritionLog": {
            "interval": {
                "startTime": start.isoformat().replace("+00:00", "Z"),
                "startUtcOffset": "0s",
                "endTime": end.isoformat().replace("+00:00", "Z"),
                "endUtcOffset": "0s",
                "civilStartTime": {
                    "date": {"year": day.year, "month": day.month, "day": day.day},
                    "time": {"hours": hour},
                },
                "civilEndTime": {
                    "date": {"year": day.year, "month": day.month, "day": day.day},
                    "time": {"hours": hour, "minutes": 30},
                },
            },
            "energy": {"kcal": kcal},
        },
    }


def _hydration(day: date, milliliters: object, *, hour: int = 8) -> dict:
    start = datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
    end = start + timedelta(minutes=30)
    return {
        "dataPointName": (
            "users/private/dataTypes/hydration-log/dataPoints/"
            f"reconciled-hydration-{hour}"
        ),
        "hydrationLog": {
            "interval": {
                "startTime": start.isoformat().replace("+00:00", "Z"),
                "startUtcOffset": "0s",
                "endTime": end.isoformat().replace("+00:00", "Z"),
                "endUtcOffset": "0s",
                "civilStartTime": {
                    "date": {"year": day.year, "month": day.month, "day": day.day},
                    "time": {"hours": hour},
                },
                "civilEndTime": {
                    "date": {"year": day.year, "month": day.month, "day": day.day},
                    "time": {"hours": hour, "minutes": 30},
                },
            },
            "amountConsumed": {"milliliters": milliliters},
        },
    }


def _adversarial_raw_nutrition(point: dict) -> dict:
    point.pop("dataPointName")
    point["name"] = "users/private/dataTypes/nutrition-log/dataPoints/private-record"
    point["dataSource"] = {"name": "users/private/dataSources/private-source"}
    point["nutritionLog"]["foodDisplayName"] = "Private meal"
    point["nutritionLog"]["nutrients"] = [
        {"nutrient": "PROTEIN", "quantity": {"grams": 20.0}}
    ]
    return point


def _adversarial_raw_hydration(point: dict) -> dict:
    point.pop("dataPointName")
    point["name"] = "users/private/dataTypes/hydration-log/dataPoints/private-record"
    point["dataSource"] = {"name": "users/private/dataSources/private-source"}
    return point


def _paired_device(**overrides: object) -> dict[str, object]:
    return {
        "name": "users/me/pairedDevices/private-device-123",
        "deviceType": "TRACKER",
        "batteryStatus": "High",
        "batteryLevel": 84,
        "lastSyncTime": "2042-07-13T12:30:00Z",
        "deviceVersion": "Fitbit Charge 7",
        "macAddress": "AA:BB:CC:DD:EE:FF",
        "features": ["HEART_RATE", "GPS"],
        "serialNumber": "private-serial",
        **overrides,
    }


def _timestamped_steps(
    start: datetime,
    offset_seconds: int,
    value: int,
    *,
    platform: str | None = None,
) -> dict:
    end = start + timedelta(minutes=15)
    point = {
        "steps": {
            "interval": {
                "startTime": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "startUtcOffset": f"{offset_seconds}s",
                "endTime": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "endUtcOffset": f"{offset_seconds}s",
            },
            "count": str(value),
        }
    }
    if platform is not None:
        point["dataSource"] = {"platform": platform}
    return point


def _civil_exercise(
    *,
    civil_start: datetime | dict[str, object],
    start: datetime,
    end: datetime,
    offset_seconds: int,
) -> dict:
    if isinstance(civil_start, datetime):
        civil_start = {
            "date": {
                "year": civil_start.year,
                "month": civil_start.month,
                "day": civil_start.day,
            },
            "time": {
                "hours": civil_start.hour,
                "minutes": civil_start.minute,
                "seconds": civil_start.second,
                "nanos": civil_start.microsecond * 1000,
            },
        }
    return {
        "exercise": {
            "interval": {
                "civilStartTime": civil_start,
                "startTime": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "startUtcOffset": f"{offset_seconds}s",
                "endTime": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "endUtcOffset": f"{offset_seconds}s",
            },
            "exerciseType": "WALKING",
            "displayName": "DST evening walk",
            "activeDuration": "1800s",
            "metricsSummary": {"caloriesKcal": 120.0},
        }
    }


def _sleep(
    *,
    start: datetime,
    start_offset_seconds: int,
    end: datetime,
    end_offset_seconds: int,
) -> dict:
    return {
        "sleep": {
            "interval": {
                "startTime": start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "startUtcOffset": f"{start_offset_seconds}s",
                "endTime": end.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "endUtcOffset": f"{end_offset_seconds}s",
            },
            "summary": {"minutesAsleep": "390", "stagesSummary": []},
        }
    }


class FakeClient:
    """Low-level Google client fake with independently failing metric streams."""

    def __init__(self) -> None:
        self.all_sources: dict[str, list[dict]] = {}
        self.wearables: dict[str, list[dict]] = {}
        self.raw: dict[str, list[dict]] = {}
        self.rollups: dict[str, list[dict]] = {}
        self.failures: dict[tuple[str, str], Exception] = {}
        self.calls: list[tuple[str, str, datetime | None, datetime | None]] = []
        self.paired_devices: list[dict[str, object]] = []
        self.paired_device_failure: Exception | None = None
        self.paired_device_calls = 0
        self.backfill_gate: asyncio.Event | None = None
        self.scope_grant = validate_granted_scopes(BASE_SCOPES, {})

    async def async_list_data_points(
        self, data_type: str, *, start: datetime, end: datetime
    ) -> list[dict]:
        self.calls.append(("raw", data_type, start, end))
        failure = self.failures.get(("raw", data_type))
        if failure is not None:
            raise failure
        return self.raw.get(data_type, [])

    async def async_reconcile_data_points(
        self,
        data_type: str,
        *,
        start: datetime,
        end: datetime,
        source_family: str,
    ) -> list[dict]:
        self.calls.append((source_family, data_type, start, end))
        if self.backfill_gate is not None and (end - start).days > 1:
            await self.backfill_gate.wait()
        failure = self.failures.get((source_family, data_type))
        if failure is not None:
            raise failure
        stream = self.all_sources if source_family == "all-sources" else self.wearables
        return stream.get(data_type, [])

    async def async_daily_rollup_data_points(
        self,
        data_type: str,
        *,
        start: datetime,
        end: datetime,
        source_family: str,
    ) -> list[dict]:
        family = f"daily-rollup-{source_family}"
        self.calls.append((family, data_type, start, end))
        if self.backfill_gate is not None and (end - start).days > 1:
            await self.backfill_gate.wait()
        failure = self.failures.get((family, data_type))
        if failure is not None:
            raise failure
        return self.rollups.get(data_type, [])

    async def async_list_paired_devices(self) -> list[dict[str, object]]:
        self.paired_device_calls += 1
        if self.paired_device_failure is not None:
            raise self.paired_device_failure
        return self.paired_devices

    @property
    def current_step_calls(self) -> int:
        return sum(
            family == "all-sources" and data_type == "steps" and end - start == timedelta(days=1)
            for family, data_type, start, end in self.calls
            if start is not None and end is not None
        )


class FakeStore:
    """In-memory implementation of the fail-closed history contract."""

    def __init__(
        self,
        rows: list[DailySummary] | None = None,
        cursor: date | None = None,
        expanded_cursor: date | None = None,
        body_measurements_enabled: bool = False,
    ) -> None:
        self.rows = {row.date: row for row in rows or []}
        self.backfill_cursor = cursor
        self.expanded_backfill_cursor = expanded_cursor
        self.body_measurements_enabled = body_measurements_enabled
        self.checkpoints: list[date | None] = []
        self.expanded_checkpoints: list[date | None] = []
        self.upsert_committed: asyncio.Event | None = None
        self.release_upsert: asyncio.Event | None = None
        self.checkpoint_committed: asyncio.Event | None = None
        self.release_checkpoint: asyncio.Event | None = None

    async def async_load(self) -> list[DailySummary]:
        return [self.rows[day] for day in sorted(self.rows)]

    async def async_query(self, start: date, end: date) -> list[DailySummary]:
        return [self.rows[day] for day in sorted(self.rows) if start <= day <= end]

    async def async_upsert(self, summary: DailySummary) -> None:
        self.rows[summary.date] = summary
        if self.upsert_committed is not None:
            self.upsert_committed.set()
            assert self.release_upsert is not None
            await self.release_upsert.wait()

    async def async_set_backfill_checkpoint(self, cursor: date | None) -> None:
        self.backfill_cursor = cursor
        self.checkpoints.append(cursor)
        if self.checkpoint_committed is not None:
            self.checkpoint_committed.set()
            assert self.release_checkpoint is not None
            await self.release_checkpoint.wait()

    async def async_checkpoint_expanded(
        self, summary: DailySummary, next_cursor: date | None
    ) -> None:
        self.rows[summary.date] = summary
        self.expanded_backfill_cursor = next_cursor
        self.expanded_checkpoints.append(next_cursor)

    async def async_apply_body_measurement_option(
        self, enabled: bool, today: date
    ) -> list[DailySummary]:
        if enabled and not self.body_measurements_enabled:
            self.expanded_backfill_cursor = today
        if not enabled:
            self.rows = {
                day: replace(
                    summary,
                    expanded=replace(
                        summary.expanded,
                        weight_kg=None,
                        body_fat_percentage=None,
                        height_m=None,
                    ),
                )
                for day, summary in self.rows.items()
            }
        self.body_measurements_enabled = enabled
        return await self.async_load()


@pytest.fixture
def now() -> datetime:
    return datetime(2042, 7, 13, 15, 0, tzinfo=UTC)


@pytest.fixture
def client(now: datetime) -> FakeClient:
    result = FakeClient()
    result.all_sources = {
        "steps": [_steps(now.date(), 6000)],
        "distance": [_distance(now.date(), 4_500_000)],
    }
    result.wearables = {"steps": [_steps(now.date(), 5800)]}
    result.raw = {
        "steps": [
            _timestamped_steps(
                datetime(now.year, now.month, now.day, tzinfo=UTC),
                0,
                5800,
                platform="FITBIT",
            )
        ]
    }
    return result


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def coordinator(hass, client: FakeClient, store: FakeStore, now: datetime):
    return HealthSyncCoordinator(hass, client, store, now=lambda: now)


def test_coordinator_uses_fifteen_minute_polling(coordinator) -> None:
    assert coordinator.update_interval == SCAN_INTERVAL


async def test_manual_refresh_has_five_minute_cooldown(
    hass, client: FakeClient, store: FakeStore, now: datetime
) -> None:
    clock = [now]
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: clock[0])

    await coordinator.async_manual_refresh()
    await coordinator.async_manual_refresh()
    assert client.current_step_calls == 1

    clock[0] += timedelta(minutes=5, seconds=1)
    await coordinator.async_manual_refresh()
    assert client.current_step_calls == 2


async def test_failed_manual_refresh_throttles_until_five_minutes_have_elapsed(
    hass, client: FakeClient, store: FakeStore, now: datetime
) -> None:
    clock = [now]
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: clock[0])
    for data_type in coordinator.data_types:
        client.failures[("all-sources", data_type)] = UpdateFailed("temporary")

    with pytest.raises(UpdateFailed):
        await coordinator.async_manual_refresh()
    calls_after_failure = client.current_step_calls

    skipped = await coordinator.async_manual_refresh()
    assert client.current_step_calls == calls_after_failure
    assert skipped.last_success is None

    clock[0] += timedelta(minutes=5, seconds=1)
    client.failures.clear()
    snapshot = await coordinator.async_manual_refresh()

    assert client.current_step_calls == calls_after_failure + 1
    assert snapshot.last_success == clock[0]


async def test_manual_cooldown_starts_when_api_attempt_begins(
    hass, client: FakeClient, now: datetime
) -> None:
    clock = [now]
    store = FakeStore()
    store.upsert_committed = asyncio.Event()
    store.release_upsert = asyncio.Event()
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: clock[0])

    refresh = hass.async_create_task(coordinator.async_manual_refresh())
    await store.upsert_committed.wait()
    clock[0] += timedelta(minutes=4)
    store.release_upsert.set()
    await refresh

    clock[0] += timedelta(minutes=2)
    await coordinator.async_manual_refresh()

    assert client.current_step_calls == 2


async def test_successful_refresh_updates_history_and_last_success(
    coordinator, store: FakeStore, now: datetime
) -> None:
    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day == store.rows[now.date()]
    assert snapshot.current_day.steps == 6000
    assert snapshot.current_day.fitbit_steps == 5800
    assert snapshot.current_day.source is SourceKind.FITBIT
    assert snapshot.last_attempt == now
    assert snapshot.last_success == now
    assert snapshot.authorization_healthy is True


async def test_total_calories_and_sleep_period_current_refresh_use_existing_sleep_request(
    hass, client: FakeClient, store: FakeStore, now: datetime
) -> None:
    """Current refresh merges total calories and detailed timing without another sleep call."""
    client.rollups["total-calories"] = [
        _daily_rollup(now.date(), "totalCalories", kcalSum=0.0)
    ]
    sleep = _sleep(
        start=now.replace(hour=4),
        start_offset_seconds=0,
        end=now.replace(hour=11),
        end_offset_seconds=0,
    )
    sleep["sleep"]["summary"].update(
        {
            "minutesAsleep": "375",
            "minutesInSleepPeriod": "402",
            "minutesToFallAsleep": "6",
            "minutesAfterWakeUp": "12",
        }
    )
    client.all_sources["sleep"] = [sleep]
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.total_energy_kcal == 0.0
    assert snapshot.current_day.sleep_minutes == 375.0
    assert snapshot.current_day.sleep_period_minutes == 402.0
    assert snapshot.current_day.sleep_onset_minutes == 6.0
    assert snapshot.current_day.sleep_after_wake_minutes == 12.0
    assert [
        (family, data_type)
        for family, data_type, _start, _end in client.calls
        if data_type == "sleep"
    ] == [("raw", "sleep"), ("all-sources", "sleep")]


async def test_nutrition_refresh_uses_only_the_current_local_civil_day(
    hass, client: FakeClient
) -> None:
    """An available nutrition capability reconciles exactly one local day."""
    detroit = ZoneInfo("America/Detroit")
    now = datetime(2042, 7, 13, 15, 0, tzinfo=detroit)
    client.scope_grant = validate_granted_scopes(
        (*BASE_SCOPES, NUTRITION_SCOPE),
        {"include_nutrition": True},
    )
    client.all_sources["nutrition-log"] = [
        _adversarial_raw_nutrition(_nutrition(now.date(), 820.0)),
        _nutrition(now.date(), 1000.0, hour=13),
    ]
    client.all_sources["hydration-log"] = [
        _adversarial_raw_hydration(_hydration(now.date(), 900.0)),
        _hydration(now.date(), 1200.0, hour=13),
    ]
    store = FakeStore()
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.nutrition_energy_kcal == 1820.0
    assert snapshot.current_day.hydration_ml == 2100.0
    assert [
        (family, data_type, start, end)
        for family, data_type, start, end in client.calls
        if data_type in {"nutrition-log", "hydration-log"}
    ] == [
        (
            "all-sources",
            "nutrition-log",
            datetime(2042, 7, 13, tzinfo=detroit),
            datetime(2042, 7, 14, tzinfo=detroit),
        ),
        (
            "all-sources",
            "hydration-log",
            datetime(2042, 7, 13, tzinfo=detroit),
            datetime(2042, 7, 14, tzinfo=detroit),
        ),
    ]
    nutrition_state = snapshot.capability_states[CapabilityId.NUTRITION]
    assert nutrition_state.enabled is True
    assert nutrition_state.scope_granted is True
    assert nutrition_state.last_success == now
    assert nutrition_state.error_category is None
    retained = repr((snapshot, store.rows[now.date()]))
    assert "Private meal" not in retained
    assert "PROTEIN" not in retained
    assert "private-source" not in retained
    assert "private-record" not in retained
    assert "reconciled-nutrition-13" not in retained
    assert "reconciled-hydration-13" not in retained


@pytest.mark.parametrize(
    ("scopes", "options", "enabled", "scope_granted"),
    [
        ((*BASE_SCOPES, NUTRITION_SCOPE), {}, False, True),
        (BASE_SCOPES, {"include_nutrition": True}, True, False),
    ],
)
async def test_nutrition_refresh_requires_enabled_option_and_granted_scope(
    hass,
    client: FakeClient,
    now: datetime,
    scopes: tuple[str, ...],
    options: dict[str, bool],
    enabled: bool,
    scope_granted: bool,
) -> None:
    """Option and permission gates are independent and schedule no partial request."""
    client.scope_grant = validate_granted_scopes(scopes, options)
    coordinator = HealthSyncCoordinator(hass, client, FakeStore(), now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.nutrition_energy_kcal is None
    assert snapshot.current_day.hydration_ml is None
    assert not any(
        data_type in {"nutrition-log", "hydration-log"}
        for _family, data_type, _start, _end in client.calls
    )
    state = snapshot.capability_states[CapabilityId.NUTRITION]
    assert state.enabled is enabled
    assert state.scope_granted is scope_granted
    assert state.last_success is None
    assert state.error_category == ("authorization" if enabled else None)
    assert snapshot.authorization_healthy is True


async def test_nutrition_capability_never_runs_during_historical_backfill(
    hass, client: FakeClient, now: datetime
) -> None:
    """Enabling nutrition adds no core or expanded backfill data type."""
    client.scope_grant = validate_granted_scopes(
        (*BASE_SCOPES, NUTRITION_SCOPE),
        {"include_nutrition": True},
    )
    store = FakeStore(cursor=now.date(), expanded_cursor=now.date())
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    await coordinator.async_backfill_step()

    assert not any(
        data_type in {"nutrition-log", "hydration-log"}
        for _family, data_type, _start, _end in client.calls
    )
    assert store.backfill_cursor == now.date() - timedelta(days=7)
    assert store.expanded_backfill_cursor == now.date() - timedelta(days=14)


@pytest.mark.parametrize(
    ("failed_type", "message"),
    [
        ("nutrition-log", "Google Health rejected the data request with 403"),
        ("hydration-log", "Google Health request timed out"),
    ],
)
async def test_nutrition_request_failure_preserves_values_and_baseline_health(
    hass,
    client: FakeClient,
    now: datetime,
    failed_type: str,
    message: str,
) -> None:
    """A nutrition-only 403 or timeout cannot change baseline groups or either cursor."""
    core_cursor = now.date() - timedelta(days=21)
    expanded_cursor = now.date() - timedelta(days=14)
    previous = DailySummary(
        date=now.date(),
        steps=6000,
        fitbit_steps=5800,
        distance_m=4500.0,
        nutrition_energy_kcal=1750.0,
        hydration_ml=1950.0,
        source=SourceKind.FITBIT,
        updated_at=now - timedelta(minutes=15),
    )
    store = FakeStore(
        [previous],
        cursor=core_cursor,
        expanded_cursor=expanded_cursor,
    )
    client.scope_grant = validate_granted_scopes(
        (*BASE_SCOPES, NUTRITION_SCOPE),
        {"include_nutrition": True},
    )
    client.all_sources["nutrition-log"] = [_nutrition(now.date(), 1820.0)]
    client.all_sources["hydration-log"] = [_hydration(now.date(), 2100.0)]
    client.failures[("all-sources", failed_type)] = UpdateFailed(message)
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.nutrition_energy_kcal == 1750.0
    assert snapshot.current_day.hydration_ml == 1950.0
    assert snapshot.current_day.steps == 6000
    assert snapshot.current_day.fitbit_steps == 5800
    assert snapshot.current_day.distance_m == 4500.0
    assert snapshot.current_day.source is SourceKind.FITBIT
    assert snapshot.authorization_healthy is True
    assert snapshot.last_success == now
    assert snapshot.backfill_cursor == core_cursor
    assert snapshot.expanded_backfill_cursor == expanded_cursor
    state = snapshot.capability_states[CapabilityId.NUTRITION]
    assert state.enabled is True
    assert state.scope_granted is True
    assert state.last_success is None
    assert state.error_category == "temporary"
    assert store.rows[now.date()] == snapshot.current_day


async def test_paired_device_refresh_normalizes_current_metadata_without_history_storage(
    hass, client: FakeClient, now: datetime
) -> None:
    """An enabled settings capability stores only sanitized current runtime metadata."""
    client.scope_grant = validate_granted_scopes(
        (*BASE_SCOPES, SETTINGS_SCOPE),
        {"include_paired_devices": True},
    )
    client.paired_devices = [_paired_device()]
    store = FakeStore(
        cursor=now.date() - timedelta(days=21),
        expanded_cursor=now.date() - timedelta(days=14),
    )
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert client.paired_device_calls == 1
    assert len(snapshot.paired_devices) == 1
    device = snapshot.paired_devices[0]
    assert device.identity_digest == "577fa4f7736cb1d1aa4fb6e3b8c9ca28"
    assert device.device_type == "TRACKER"
    assert device.product_name == "Fitbit Charge 7"
    assert device.battery_status == "High"
    assert device.battery_percentage == 84
    assert device.last_sync == datetime(2042, 7, 13, 12, 30, tzinfo=UTC)
    state = snapshot.capability_states[CapabilityId.PAIRED_DEVICES]
    assert state.enabled is True
    assert state.scope_granted is True
    assert state.last_success == now
    assert state.error_category is None
    assert snapshot.authorization_healthy is True

    snapshot_values = repr(snapshot)
    for private_value in (
        "users/me/pairedDevices/private-device-123",
        "AA:BB:CC:DD:EE:FF",
        "HEART_RATE",
        "GPS",
        "private-serial",
    ):
        assert private_value not in snapshot_values
    persisted_history = repr(store.rows)
    assert device.identity_digest not in persisted_history
    assert "Fitbit Charge 7" not in persisted_history


@pytest.mark.parametrize(
    ("scopes", "options", "enabled", "scope_granted"),
    [
        ((*BASE_SCOPES, SETTINGS_SCOPE), {}, False, True),
        (BASE_SCOPES, {"include_paired_devices": True}, True, False),
    ],
)
async def test_paired_device_refresh_requires_option_and_settings_scope(
    hass,
    client: FakeClient,
    now: datetime,
    scopes: tuple[str, ...],
    options: dict[str, bool],
    enabled: bool,
    scope_granted: bool,
) -> None:
    """Partial consent never schedules a settings request or harms baseline health."""
    client.scope_grant = validate_granted_scopes(scopes, options)
    client.paired_devices = [_paired_device()]
    coordinator = HealthSyncCoordinator(hass, client, FakeStore(), now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert client.paired_device_calls == 0
    assert snapshot.paired_devices == ()
    state = snapshot.capability_states[CapabilityId.PAIRED_DEVICES]
    assert state.enabled is enabled
    assert state.scope_granted is scope_granted
    assert state.last_success is None
    assert state.error_category == ("authorization" if enabled else None)
    assert snapshot.authorization_healthy is True


@pytest.mark.parametrize(
    ("failure", "replacement"),
    [
        (UpdateFailed("Google Health rejected the data request with 403"), None),
        (UpdateFailed("Google Health request timed out"), None),
        (None, _paired_device(batteryLevel=101)),
        (None, _paired_device(lastSyncTime="0001-01-01T00:00:00+14:00")),
    ],
)
async def test_paired_device_failure_preserves_tuple_and_only_marks_its_capability(
    hass,
    client: FakeClient,
    now: datetime,
    failure: Exception | None,
    replacement: dict[str, object] | None,
) -> None:
    """Settings failures cannot discard prior devices or poison baseline health."""
    clock = [now]
    core_cursor = now.date() - timedelta(days=21)
    expanded_cursor = now.date() - timedelta(days=14)
    client.scope_grant = validate_granted_scopes(
        (*BASE_SCOPES, SETTINGS_SCOPE),
        {"include_paired_devices": True},
    )
    client.paired_devices = [_paired_device()]
    coordinator = HealthSyncCoordinator(
        hass,
        client,
        FakeStore(cursor=core_cursor, expanded_cursor=expanded_cursor),
        now=lambda: clock[0],
    )
    first = await coordinator.async_refresh_current()
    previous_devices = first.paired_devices
    other_states = {
        capability: state
        for capability, state in first.capability_states.items()
        if capability is not CapabilityId.PAIRED_DEVICES
    }

    clock[0] += timedelta(minutes=15)
    client.paired_device_failure = failure
    if replacement is not None:
        client.paired_devices = [replacement]

    snapshot = await coordinator.async_refresh_current()

    assert client.paired_device_calls == 2
    assert snapshot.paired_devices == previous_devices
    assert snapshot.authorization_healthy is True
    assert snapshot.last_success == clock[0]
    assert snapshot.backfill_cursor == core_cursor
    assert snapshot.expanded_backfill_cursor == expanded_cursor
    assert snapshot.current_day.steps == 6000
    assert snapshot.current_day.fitbit_steps == 5800
    assert snapshot.current_day.distance_m == 4500.0
    assert snapshot.current_day.source is SourceKind.FITBIT
    state = snapshot.capability_states[CapabilityId.PAIRED_DEVICES]
    assert state.enabled is True
    assert state.scope_granted is True
    assert state.last_success == now
    assert state.error_category == "temporary"
    assert {
        capability: capability_state
        for capability, capability_state in snapshot.capability_states.items()
        if capability is not CapabilityId.PAIRED_DEVICES
    } == other_states
    assert "with 403" not in repr(snapshot)
    assert "timed out" not in repr(snapshot)


async def test_disabling_paired_devices_clears_ephemeral_metadata(
    hass, client: FakeClient, now: datetime
) -> None:
    """Removing the opt-in stops requests and removes the current runtime tuple."""
    client.scope_grant = validate_granted_scopes(
        (*BASE_SCOPES, SETTINGS_SCOPE),
        {"include_paired_devices": True},
    )
    client.paired_devices = [_paired_device()]
    coordinator = HealthSyncCoordinator(hass, client, FakeStore(), now=lambda: now)
    await coordinator.async_refresh_current()

    client.scope_grant = validate_granted_scopes(BASE_SCOPES, {})
    snapshot = await coordinator.async_refresh_current()

    assert client.paired_device_calls == 1
    assert snapshot.paired_devices == ()
    state = snapshot.capability_states[CapabilityId.PAIRED_DEVICES]
    assert state.enabled is False
    assert state.scope_granted is False
    assert state.error_category is None
    assert snapshot.authorization_healthy is True


async def test_paired_devices_are_never_requested_during_backfill(
    hass, client: FakeClient, now: datetime
) -> None:
    """Current device metadata cannot enter either persisted history lifecycle."""
    client.scope_grant = validate_granted_scopes(
        (*BASE_SCOPES, SETTINGS_SCOPE),
        {"include_paired_devices": True},
    )
    client.paired_devices = [_paired_device()]
    store = FakeStore(cursor=now.date(), expanded_cursor=now.date())
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    await coordinator.async_backfill_step()

    assert client.paired_device_calls == 0
    assert coordinator.data.paired_devices == ()
    assert "Fitbit Charge 7" not in repr(store.rows)


async def test_total_calories_and_sleep_period_are_normalized_during_backfill(
    hass, client: FakeClient, now: datetime
) -> None:
    """Core history writes total calories and detailed sleep timing for returned days."""
    historical_day = now.date() - timedelta(days=1)
    client.rollups["total-calories"] = [
        _daily_rollup(historical_day, "totalCalories", kcalSum=2345.6)
    ]
    sleep = _sleep(
        start=datetime.combine(
            historical_day - timedelta(days=1),
            datetime.min.time(),
            tzinfo=UTC,
        ).replace(hour=23),
        start_offset_seconds=0,
        end=datetime.combine(historical_day, datetime.min.time(), tzinfo=UTC).replace(hour=7),
        end_offset_seconds=0,
    )
    sleep["sleep"]["summary"].update(
        {
            "minutesAsleep": "375",
            "minutesInSleepPeriod": "402",
            "minutesToFallAsleep": "6",
            "minutesAfterWakeUp": "12",
        }
    )
    client.all_sources["sleep"] = [sleep]
    store = FakeStore(cursor=now.date())
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    await coordinator.async_backfill_step()

    row = store.rows[historical_day]
    assert row.total_energy_kcal == 2345.6
    assert row.sleep_minutes == 375.0
    assert row.sleep_period_minutes == 402.0
    assert row.sleep_onset_minutes == 6.0
    assert row.sleep_after_wake_minutes == 12.0


async def test_total_calories_date_only_rollups_are_partitioned_during_backfill(
    hass, client: FakeClient, now: datetime
) -> None:
    """Date-only rollup boundaries retain the value for each historical day."""
    historical_day = now.date() - timedelta(days=1)
    older_day = historical_day - timedelta(days=1)
    rollups = [
        _daily_rollup(older_day, "totalCalories", kcalSum=2100.0),
        _daily_rollup(historical_day, "totalCalories", kcalSum=2200.0),
    ]
    for rollup in rollups:
        rollup["civilStartTime"].pop("time")
        rollup["civilEndTime"].pop("time")
    client.rollups["total-calories"] = rollups
    store = FakeStore(cursor=now.date())
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    await coordinator.async_backfill_step()

    assert store.rows[older_day].total_energy_kcal == 2100.0
    assert store.rows[historical_day].total_energy_kcal == 2200.0


async def test_undated_total_calories_do_not_repeat_across_backfill_days(
    hass, client: FakeClient, now: datetime
) -> None:
    """A sole undated rollup cannot be reused for every returned historical day."""
    historical_day = now.date() - timedelta(days=1)
    older_day = historical_day - timedelta(days=1)
    client.all_sources["steps"] = [
        _steps(older_day, 4100),
        _steps(historical_day, 4200),
    ]
    client.rollups["total-calories"] = [{"totalCalories": {"kcalSum": 2300.0}}]
    store = FakeStore(cursor=now.date())
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    await coordinator.async_backfill_step()

    assert store.rows[older_day].steps == 4100
    assert store.rows[historical_day].steps == 4200
    assert store.rows[older_day].total_energy_kcal is None
    assert store.rows[historical_day].total_energy_kcal is None


async def test_total_calories_failure_preserves_prior_activity_values(
    hass, client: FakeClient, now: datetime
) -> None:
    """A temporary total-calorie failure preserves its activity group only."""
    previous = DailySummary(
        date=now.date(),
        steps=4500,
        active_energy_kcal=120.0,
        total_energy_kcal=2200.0,
    )
    client.all_sources["active-energy-burned"] = [
        _interval_point(now.date(), "activeEnergyBurned", "kcal", 145.0)
    ]
    client.failures[("daily-rollup-all-sources", "total-calories")] = UpdateFailed(
        "total calories unavailable"
    )
    store = FakeStore([previous])
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.active_energy_kcal == 120.0
    assert snapshot.current_day.total_energy_kcal == 2200.0
    assert snapshot.current_day.steps == 6000


async def test_successful_manual_refresh_notifies_coordinator_listeners(coordinator) -> None:
    updates = 0

    def record_update() -> None:
        nonlocal updates
        updates += 1

    remove_listener = coordinator.async_add_listener(record_update)
    try:
        await coordinator.async_manual_refresh()
    finally:
        remove_listener()

    assert updates == 1


async def test_authentication_failure_marks_authorization_unhealthy(
    coordinator, client: FakeClient
) -> None:
    client.failures[("all-sources", "steps")] = AuthenticationError("reauthorize")

    with pytest.raises(AuthenticationError):
        await coordinator.async_manual_refresh()

    assert coordinator.data.authorization_healthy is False
    assert coordinator.data.last_success is None


async def test_transient_failure_does_not_mark_authorization_unhealthy(
    coordinator, client: FakeClient
) -> None:
    for data_type in coordinator.data_types:
        client.failures[("all-sources", data_type)] = UpdateFailed("temporary")

    with pytest.raises(UpdateFailed):
        await coordinator.async_manual_refresh()

    assert coordinator.data.authorization_healthy is True
    assert coordinator.data.last_success is None


async def test_scheduled_refresh_uses_home_assistant_transient_failure_contract(
    coordinator, client: FakeClient
) -> None:
    for data_type in coordinator.data_types:
        client.failures[("all-sources", data_type)] = UpdateFailed("temporary")

    with pytest.raises(CoordinatorUpdateFailed):
        await coordinator._async_update_data()


async def test_scheduled_refresh_uses_home_assistant_auth_failure_contract(
    coordinator, client: FakeClient
) -> None:
    client.failures[("all-sources", "steps")] = AuthenticationError("reauthorize")

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_partial_metric_failure_preserves_only_failed_group(
    hass, client: FakeClient, now: datetime
) -> None:
    previous = DailySummary(
        date=now.date(),
        steps=4500,
        fitbit_steps=4300,
        distance_m=3900.0,
        source=SourceKind.FITBIT,
        updated_at=now - timedelta(minutes=15),
    )
    store = FakeStore([previous])
    client.failures[("all-sources", "distance")] = UpdateFailed("distance unavailable")
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.steps == 6000
    assert snapshot.current_day.fitbit_steps == 5800
    assert snapshot.current_day.distance_m == 3900.0
    assert store.rows[now.date()] == snapshot.current_day


async def test_current_refresh_uses_reconciled_records_with_daily_rollup_precedence(
    coordinator, client: FakeClient, now: datetime
) -> None:
    client.all_sources["daily-oxygen-saturation"] = [
        _daily_point(
            now.date(),
            "dailyOxygenSaturation",
            averagePercentage=96.5,
            lowerBoundPercentage=92.0,
            upperBoundPercentage=99.0,
            standardDeviationPercentage=1.2,
        )
    ]
    client.rollups["time-in-heart-rate-zone"] = [
        _daily_rollup(
            now.date(),
            "timeInHeartRateZone",
            timeInHeartRateZones=[{"heartRateZone": "VIGOROUS", "duration": "600s"}],
        )
    ]

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.expanded.oxygen_average == 96.5
    assert snapshot.current_day.expanded.heart_zone_minutes == {"vigorous": 10.0}
    expected_direct = (
        "daily-vo2-max",
        "daily-oxygen-saturation",
        "daily-respiratory-rate",
        "respiratory-rate-sleep-summary",
        "daily-heart-rate-zones",
    )
    expected_rollups = (
        "total-calories",
        "active-zone-minutes",
        "floors",
        "sedentary-period",
        "time-in-heart-rate-zone",
        "calories-in-heart-rate-zone",
    )
    expected_current_interval_fallbacks = (
        "active-zone-minutes",
        "floors",
        "sedentary-period",
        "time-in-heart-rate-zone",
    )
    expected_calls = Counter(
        [("raw", data_type) for data_type in coordinator.data_types]
        + [("all-sources", data_type) for data_type in coordinator.data_types]
        + [("google-wearables", "steps")]
        + [("all-sources", data_type) for data_type in expected_direct]
        + [("all-sources", data_type) for data_type in expected_current_interval_fallbacks]
        + [("daily-rollup-all-sources", data_type) for data_type in expected_rollups]
    )
    actual_calls = Counter((family, data_type) for family, data_type, _, _ in client.calls)

    assert actual_calls == expected_calls
    assert len(client.calls) == 36
    assert all(end - start == timedelta(days=1) for _, _, start, end in client.calls)
    assert not any(
        data_type == "oxygen-saturation"
        or (
            data_type == "time-in-heart-rate-zone"
            and family not in {"all-sources", "daily-rollup-all-sources"}
        )
        for family, data_type, _start, _end in client.calls
    )


async def test_failed_expanded_type_preserves_only_its_prior_group(
    hass, client: FakeClient, now: datetime
) -> None:
    previous = DailySummary(
        date=now.date(),
        steps=4500,
        expanded=ExpandedDailyMetrics(
            vo2_max=44.0,
            vo2_estimated=True,
            cardio_fitness_level="good",
            oxygen_average=94.0,
            oxygen_lower_bound=90.0,
            oxygen_upper_bound=98.0,
            oxygen_standard_deviation=2.0,
            floors=2,
        ),
        source=SourceKind.FITBIT,
    )
    client.failures[("all-sources", "daily-vo2-max")] = UpdateFailed("vo2 unavailable")
    client.all_sources["daily-oxygen-saturation"] = [
        _daily_point(
            now.date(),
            "dailyOxygenSaturation",
            averagePercentage=97.0,
            lowerBoundPercentage=94.0,
            upperBoundPercentage=99.0,
            standardDeviationPercentage=1.0,
        )
    ]
    client.rollups["floors"] = [_daily_rollup(now.date(), "floors", countSum="7")]
    coordinator = HealthSyncCoordinator(hass, client, FakeStore([previous]), now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.expanded.vo2_max == 44.0
    assert snapshot.current_day.expanded.vo2_estimated is True
    assert snapshot.current_day.expanded.cardio_fitness_level == "good"
    assert snapshot.current_day.expanded.oxygen_average == 97.0
    assert snapshot.current_day.expanded.floors == 7
    assert snapshot.current_day.source is SourceKind.FITBIT


async def test_failed_expanded_type_does_not_inherit_from_a_different_current_day(
    hass, client: FakeClient, now: datetime
) -> None:
    coordinator = HealthSyncCoordinator(hass, client, FakeStore(), now=lambda: now)
    coordinator.data.current_day = DailySummary(
        date=now.date() - timedelta(days=1),
        expanded=ExpandedDailyMetrics(vo2_max=44.0),
    )
    client.failures[("all-sources", "daily-vo2-max")] = UpdateFailed("vo2 unavailable")

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.date == now.date()
    assert snapshot.current_day.expanded.vo2_max is None


async def test_body_measurements_are_not_requested_without_opt_in(
    hass, client: FakeClient, store: FakeStore, now: datetime
) -> None:
    client.all_sources["weight"] = [_weight(now.date(), 80_500.0)]
    client.all_sources["body-fat"] = [_body_fat(now.date(), 21.4)]
    client.all_sources["height"] = [_height(now.date(), 1778.0)]
    coordinator = HealthSyncCoordinator(
        hass,
        client,
        store,
        now=lambda: now,
        include_body_measurements=False,
    )

    snapshot = await coordinator.async_manual_refresh()

    assert not any(
        data_type in {"weight", "body-fat", "height"}
        for _, data_type, _, _ in client.calls
    )
    assert snapshot.current_day.expanded.weight_kg is None
    assert snapshot.current_day.expanded.body_fat_percentage is None
    assert snapshot.current_day.expanded.height_m is None
    assert snapshot.latest_weight_kg is None
    assert snapshot.latest_weight_at is None
    assert snapshot.latest_body_fat_percentage is None
    assert snapshot.latest_body_fat_at is None
    assert snapshot.latest_height_m is None
    assert snapshot.latest_height_at is None


async def test_disabling_body_measurements_scrubs_and_prevents_resurrection(
    hass, client: FakeClient, store: FakeStore, now: datetime
) -> None:
    client.all_sources["weight"] = [_weight(now.date(), 80_500.0)]
    client.all_sources["body-fat"] = [_body_fat(now.date(), 21.4)]
    client.all_sources["height"] = [_height(now.date(), 1778.0)]
    enabled = HealthSyncCoordinator(
        hass,
        client,
        store,
        now=lambda: now,
        include_body_measurements=True,
    )

    collected = await enabled.async_manual_refresh()

    assert collected.current_day.expanded.weight_kg == 80.5
    assert collected.current_day.expanded.body_fat_percentage == 21.4
    assert collected.current_day.expanded.height_m == 1.778
    assert collected.latest_body_fat_percentage == 21.4
    assert collected.latest_body_fat_at == now.date()
    assert collected.latest_height_m == 1.778
    assert collected.latest_height_at == now.date()

    client.calls.clear()
    disabled = HealthSyncCoordinator(
        hass,
        client,
        store,
        now=lambda: now,
        include_body_measurements=False,
    )

    after_disable = await disabled.async_manual_refresh()

    assert not any(
        data_type in {"weight", "body-fat", "height"}
        for _, data_type, _, _ in client.calls
    )
    assert after_disable.current_day.expanded.weight_kg is None
    assert after_disable.current_day.expanded.body_fat_percentage is None
    assert after_disable.current_day.expanded.height_m is None
    assert after_disable.latest_weight_kg is None
    assert after_disable.latest_weight_at is None
    assert after_disable.latest_body_fat_percentage is None
    assert after_disable.latest_body_fat_at is None
    assert after_disable.latest_height_m is None
    assert after_disable.latest_height_at is None
    persisted = store.rows[now.date()]
    assert persisted.expanded.weight_kg is None
    assert persisted.expanded.body_fat_percentage is None
    assert persisted.expanded.height_m is None
    assert persisted.steps == 6000

    after_next_refresh = await disabled.async_refresh_current()

    assert after_next_refresh.current_day.expanded.weight_kg is None
    assert after_next_refresh.current_day.expanded.body_fat_percentage is None
    assert after_next_refresh.current_day.expanded.height_m is None
    assert after_next_refresh.latest_body_fat_percentage is None
    assert after_next_refresh.latest_body_fat_at is None
    assert after_next_refresh.latest_height_m is None
    assert after_next_refresh.latest_height_at is None


async def test_body_measurements_are_requested_and_surfaced_together(
    hass, client: FakeClient, now: datetime
) -> None:
    client.all_sources["weight"] = [
        _weight(now.date(), 81_000.0, hour=7),
        _weight(now.date(), 80_500.0, hour=10),
    ]
    client.all_sources["body-fat"] = [
        _body_fat(now.date(), 22.0, hour=8),
        _body_fat(now.date(), 21.4, hour=11),
    ]
    client.all_sources["height"] = [
        _height(now.date(), 1777.0, hour=9),
        _height(now.date(), 1778.0, hour=12),
    ]
    coordinator = HealthSyncCoordinator(
        hass,
        client,
        FakeStore(body_measurements_enabled=True),
        now=lambda: now,
        include_body_measurements=True,
    )

    snapshot = await coordinator.async_manual_refresh()

    requested = {
        data_type
        for family, data_type, _, _ in client.calls
        if family == "all-sources"
    }
    assert {"weight", "body-fat", "height"} <= requested
    assert snapshot.current_day.expanded.weight_kg == 80.5
    assert snapshot.current_day.expanded.body_fat_percentage == 21.4
    assert snapshot.current_day.expanded.height_m == 1.778
    assert snapshot.latest_weight_kg == 80.5
    assert snapshot.latest_weight_at == now.date()
    assert snapshot.latest_body_fat_percentage == 21.4
    assert snapshot.latest_body_fat_at == now.date()
    assert snapshot.latest_height_m == 1.778
    assert snapshot.latest_height_at == now.date()


async def test_body_measurements_backfill_requests_stay_within_ninety_days(
    hass, client: FakeClient, now: datetime
) -> None:
    boundary = now.date() - timedelta(days=90)
    coordinator = HealthSyncCoordinator(
        hass,
        client,
        FakeStore(cursor=date.min),
        now=lambda: now,
        include_body_measurements=True,
    )

    for _ in range(7):
        await coordinator.async_backfill_step()

    body_calls = [
        (data_type, start, end)
        for family, data_type, start, end in client.calls
        if family == "all-sources"
        and data_type in {"weight", "body-fat", "height"}
    ]
    assert {data_type for data_type, _, _ in body_calls} == {
        "weight",
        "body-fat",
        "height",
    }
    assert all(
        start.date() >= boundary
        and end.date() <= now.date()
        and end - start <= timedelta(days=14)
        for _, start, end in body_calls
    )
    assert coordinator.data.expanded_backfill_cursor == boundary
    assert coordinator.data.expanded_backfill_complete is True


async def test_latest_body_measurements_are_selected_independently_from_history(
    hass, client: FakeClient, now: datetime
) -> None:
    rows = [
        DailySummary(
            date=now.date() - timedelta(days=3),
            expanded=ExpandedDailyMetrics(
                weight_kg=79.0,
                body_fat_percentage=22.5,
                height_m=1.778,
            ),
        ),
        DailySummary(
            date=now.date() - timedelta(days=2),
            expanded=ExpandedDailyMetrics(weight_kg=80.0),
        ),
        DailySummary(
            date=now.date() - timedelta(days=1),
            expanded=ExpandedDailyMetrics(body_fat_percentage=21.5),
        ),
    ]
    coordinator = HealthSyncCoordinator(
        hass,
        client,
        FakeStore(rows, body_measurements_enabled=True),
        now=lambda: now,
        include_body_measurements=True,
    )

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.latest_weight_kg == 80.0
    assert snapshot.latest_weight_at == now.date() - timedelta(days=2)
    assert snapshot.latest_body_fat_percentage == 21.5
    assert snapshot.latest_body_fat_at == now.date() - timedelta(days=1)
    assert snapshot.latest_height_m == 1.778
    assert snapshot.latest_height_at == now.date() - timedelta(days=3)


@pytest.mark.parametrize(
    ("failed_type", "failed_value_field", "failed_latest_field", "failed_date_field"),
    [
        (
            "weight",
            "weight_kg",
            "latest_weight_kg",
            "latest_weight_at",
        ),
        (
            "body-fat",
            "body_fat_percentage",
            "latest_body_fat_percentage",
            "latest_body_fat_at",
        ),
        (
            "height",
            "height_m",
            "latest_height_m",
            "latest_height_at",
        ),
    ],
)
async def test_body_measurements_preserve_only_the_failed_previous_latest_value(
    hass,
    client: FakeClient,
    now: datetime,
    failed_type: str,
    failed_value_field: str,
    failed_latest_field: str,
    failed_date_field: str,
) -> None:
    previous_day = now.date() - timedelta(days=1)
    previous = DailySummary(
        date=previous_day,
        expanded=ExpandedDailyMetrics(
            weight_kg=79.0,
            body_fat_percentage=22.5,
            height_m=1.777,
        ),
    )
    client.all_sources["weight"] = [_weight(now.date(), 80_500.0)]
    client.all_sources["body-fat"] = [_body_fat(now.date(), 21.4)]
    client.all_sources["height"] = [_height(now.date(), 1778.0)]
    client.failures[("all-sources", failed_type)] = UpdateFailed("temporary")
    coordinator = HealthSyncCoordinator(
        hass,
        client,
        FakeStore([previous], body_measurements_enabled=True),
        now=lambda: now,
        include_body_measurements=True,
    )

    snapshot = await coordinator.async_manual_refresh()

    assert getattr(snapshot.current_day.expanded, failed_value_field) is None
    assert getattr(snapshot, failed_latest_field) == getattr(
        previous.expanded, failed_value_field
    )
    assert getattr(snapshot, failed_date_field) == previous_day
    for successful_type, value_field, latest_field in (
        ("weight", "weight_kg", "latest_weight_kg"),
        ("body-fat", "body_fat_percentage", "latest_body_fat_percentage"),
        ("height", "height_m", "latest_height_m"),
    ):
        if successful_type != failed_type:
            assert getattr(snapshot.current_day.expanded, value_field) is not None
            assert getattr(snapshot, latest_field) == getattr(
                snapshot.current_day.expanded, value_field
            )


@pytest.mark.parametrize(
    ("older_weight", "expected_weight", "expected_date"),
    [
        (79.0, 79.0, date(2042, 7, 12)),
        (None, None, None),
    ],
)
async def test_empty_current_weight_recomputes_latest_stored_measurement(
    hass,
    client: FakeClient,
    now: datetime,
    older_weight: float | None,
    expected_weight: float | None,
    expected_date: date | None,
) -> None:
    rows = [
        DailySummary(
            date=now.date(),
            expanded=ExpandedDailyMetrics(weight_kg=80.5),
        )
    ]
    if older_weight is not None:
        rows.append(
            DailySummary(
                date=now.date() - timedelta(days=1),
                expanded=ExpandedDailyMetrics(weight_kg=older_weight),
            )
        )
    coordinator = HealthSyncCoordinator(
        hass,
        client,
        FakeStore(rows),
        now=lambda: now,
        include_body_measurements=True,
    )

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.expanded.weight_kg is None
    assert snapshot.latest_weight_kg == expected_weight
    assert snapshot.latest_weight_at == expected_date


@pytest.mark.parametrize(
    "failed_stream",
    ["raw", "all-sources", "google-wearables"],
)
async def test_current_partial_steps_preserve_previous_source(
    hass, client: FakeClient, now: datetime, failed_stream: str
) -> None:
    previous = DailySummary(
        date=now.date(),
        steps=4500,
        fitbit_steps=4300,
        distance_m=3900.0,
        source=SourceKind.MIXED,
        updated_at=now - timedelta(minutes=15),
    )
    store = FakeStore([previous])
    client.failures[(failed_stream, "steps")] = UpdateFailed("steps input unavailable")
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.source is SourceKind.MIXED


async def test_first_refresh_raw_failure_marks_source_unavailable(
    hass, client: FakeClient, now: datetime
) -> None:
    client.failures[("raw", "steps")] = UpdateFailed("raw attribution unavailable")
    coordinator = HealthSyncCoordinator(hass, client, FakeStore(), now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.source is SourceKind.UNAVAILABLE
    assert snapshot.current_day.steps == 6000
    assert snapshot.current_day.fitbit_steps == 5800
    assert snapshot.current_day.distance_m == 4500.0


async def test_first_refresh_all_source_steps_failure_marks_source_unavailable(
    hass, client: FakeClient, now: datetime
) -> None:
    client.failures[("all-sources", "steps")] = UpdateFailed("steps unavailable")
    coordinator = HealthSyncCoordinator(hass, client, FakeStore(), now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.source is SourceKind.UNAVAILABLE
    assert snapshot.current_day.steps is None
    assert snapshot.current_day.fitbit_steps is None
    assert snapshot.current_day.distance_m == 4500.0


async def test_first_refresh_wearable_steps_failure_marks_source_unavailable(
    hass, client: FakeClient, now: datetime
) -> None:
    client.failures[("google-wearables", "steps")] = UpdateFailed("wearable unavailable")
    coordinator = HealthSyncCoordinator(hass, client, FakeStore(), now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.source is SourceKind.UNAVAILABLE
    assert snapshot.current_day.steps is None
    assert snapshot.current_day.fitbit_steps is None
    assert snapshot.current_day.distance_m == 4500.0


@pytest.mark.parametrize(
    ("day", "expected_start", "expected_end", "points"),
    [
        (
            date(2026, 3, 8),
            datetime(2026, 3, 8, 5, 0, tzinfo=UTC),
            datetime(2026, 3, 9, 4, 0, tzinfo=UTC),
            [_timestamped_steps(datetime(2026, 3, 9, 3, 30, tzinfo=UTC), -4 * 3600, 125)],
        ),
        (
            date(2026, 11, 1),
            datetime(2026, 11, 1, 4, 0, tzinfo=UTC),
            datetime(2026, 11, 2, 5, 0, tzinfo=UTC),
            [
                _timestamped_steps(datetime(2026, 11, 1, 5, 30, tzinfo=UTC), -4 * 3600, 100),
                _timestamped_steps(datetime(2026, 11, 1, 6, 30, tzinfo=UTC), -5 * 3600, 200),
            ],
        ),
    ],
)
async def test_detroit_dst_windows_and_offsets_partition_into_the_local_day(
    hass,
    day: date,
    expected_start: datetime,
    expected_end: datetime,
    points: list[dict],
) -> None:
    detroit = ZoneInfo("America/Detroit")
    now = datetime(day.year, day.month, day.day, 12, tzinfo=detroit)
    client = FakeClient()
    client.all_sources["steps"] = points
    client.wearables["steps"] = points
    client.raw["steps"] = [point | {"dataSource": {"platform": "FITBIT"}} for point in points]
    store = FakeStore()
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.steps == sum(int(point["steps"]["count"]) for point in points)
    raw_steps_call = next(call for call in client.calls if call[:2] == ("raw", "steps"))
    assert raw_steps_call[2].astimezone(UTC) == expected_start
    assert raw_steps_call[3].astimezone(UTC) == expected_end


async def test_detroit_dst_exercise_uses_civil_start_day(hass) -> None:
    point = _civil_exercise(
        civil_start=datetime(2026, 3, 8, 23, 30),
        start=datetime(2026, 3, 9, 3, 30, tzinfo=UTC),
        end=datetime(2026, 3, 9, 4, 0, tzinfo=UTC),
        offset_seconds=-4 * 3600,
    )
    client = FakeClient()
    client.all_sources["exercise"] = [point]
    coordinator = HealthSyncCoordinator(
        hass,
        client,
        FakeStore(),
        now=lambda: datetime(2026, 3, 8, 12, tzinfo=ZoneInfo("America/Detroit")),
    )

    snapshot = await coordinator.async_manual_refresh()

    assert len(snapshot.current_day.workouts) == 1
    assert snapshot.current_day.workouts[0].activity_type == "WALKING"


def test_nested_civil_date_time_returns_calendar_date() -> None:
    interval = {
        "civilStartTime": {
            "date": {"year": 2026, "month": 3, "day": 8},
            "time": {"hours": 23, "minutes": 30, "seconds": 0, "nanos": 123456789},
        }
    }

    assert _civil_date(interval, "civilStartTime") == date(2026, 3, 8)


@pytest.mark.parametrize(
    "civil_start",
    [
        {
            "date": {"year": 2042, "month": 2, "day": 30},
            "time": {"hours": 12, "minutes": 0, "seconds": 0, "nanos": 0},
        },
        {
            "date": {"year": 2026, "month": 3, "day": 8},
            "time": {"hours": 24, "minutes": 0, "seconds": 0, "nanos": 0},
        },
        {
            "date": {"year": 2026, "month": 3, "day": 8},
            "time": {"hours": 12, "minutes": 0, "seconds": 0, "nanos": 1_000_000_000},
        },
        {
            "date": {"year": 2026, "month": 3, "day": 8},
            "time": {"hours": True, "minutes": 0, "seconds": 0, "nanos": 0},
        },
    ],
)
async def test_malformed_nested_civil_date_time_uses_physical_fallback(
    hass, civil_start: dict[str, object]
) -> None:
    point = _civil_exercise(
        civil_start=civil_start,
        start=datetime(2026, 3, 9, 14, 0, tzinfo=UTC),
        end=datetime(2026, 3, 9, 14, 30, tzinfo=UTC),
        offset_seconds=-4 * 3600,
    )
    client = FakeClient()
    client.all_sources["exercise"] = [point]
    coordinator = HealthSyncCoordinator(
        hass,
        client,
        FakeStore(),
        now=lambda: datetime(2026, 3, 9, 12, tzinfo=ZoneInfo("America/Detroit")),
    )

    snapshot = await coordinator.async_manual_refresh()

    assert len(snapshot.current_day.workouts) == 1


async def test_detroit_dst_sleep_uses_local_end_day(
    hass,
) -> None:
    detroit = ZoneInfo("America/Detroit")
    point = _sleep(
        start=datetime(2026, 11, 1, 3, 30, tzinfo=UTC),
        start_offset_seconds=-4 * 3600,
        end=datetime(2026, 11, 1, 12, 0, tzinfo=UTC),
        end_offset_seconds=-5 * 3600,
    )
    client = FakeClient()
    client.all_sources["sleep"] = [point]
    coordinator = HealthSyncCoordinator(
        hass,
        client,
        FakeStore(),
        now=lambda: datetime(2026, 11, 1, 12, tzinfo=detroit),
    )

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.sleep_minutes == 390


async def test_missing_current_sleep_logs_redacted_diagnostics(
    hass, caplog, client: FakeClient, store: FakeStore, now: datetime
) -> None:
    caplog.set_level(logging.WARNING, logger="custom_components.resiyhome_health_sync.coordinator")
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.sleep_minutes is None
    assert "Sleep diagnostics for current refresh" in caplog.text
    assert "day=2042-07-13" in caplog.text
    assert "raw_count=0" in caplog.text
    assert "all_sources_count=0" in caplog.text
    assert "access_token" not in caplog.text
    assert "refresh_token" not in caplog.text
    assert "data_points" not in caplog.text


async def test_missing_current_source_records_log_redacted_fetch_diagnostics(
    hass, caplog, store: FakeStore, now: datetime
) -> None:
    """Current refresh logs source family counts without raw health payloads."""
    caplog.set_level(logging.WARNING, logger="custom_components.resiyhome_health_sync.coordinator")
    client = FakeClient()
    client.all_sources = {"steps": [_steps(now.date(), 1700)]}
    client.raw = {
        "steps": [
            _timestamped_steps(
                datetime(now.year, now.month, now.day, tzinfo=UTC),
                0,
                1700,
                platform="HEALTH_KIT",
            )
        ]
    }
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.source is SourceKind.APPLE_FALLBACK
    assert "Fetch diagnostics for current refresh" in caplog.text
    assert "source=apple_fallback" in caplog.text
    assert "raw_counts=" in caplog.text
    assert "all_sources_counts=" in caplog.text
    assert "wearables_counts=" in caplog.text
    assert "raw_platforms=(steps=HEALTH_KIT)" in caplog.text
    assert "fitbit_steps=False" in caplog.text
    assert "sleep=False" in caplog.text
    assert "workouts=0" in caplog.text
    assert "1700" not in caplog.text
    assert "access_token" not in caplog.text
    assert "refresh_token" not in caplog.text


async def test_missing_expanded_rollups_log_only_redacted_shape_diagnostics(
    hass, caplog, client: FakeClient, store: FakeStore, now: datetime
) -> None:
    """Expanded diagnostics expose counts and availability, never health values."""
    caplog.set_level(logging.WARNING, logger="custom_components.resiyhome_health_sync.coordinator")
    client.all_sources["active-zone-minutes"] = [
        {
            "activeZoneMinutes": {
                "interval": {
                    "startTime": "2042-07-13T12:00:00Z",
                    "startUtcOffset": "0s",
                    "endTime": "2042-07-13T12:01:00Z",
                    "endUtcOffset": "0s",
                },
                "heartRateZone": "FAT_BURN",
                "activeZoneMinutes": "private_health_value",
            }
        }
    ]
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    await coordinator.async_manual_refresh()

    assert "Expanded diagnostics for current refresh" in caplog.text
    assert "direct_counts=(active-zone-minutes=1" in caplog.text
    assert "rollup_counts=(active-zone-minutes=0" in caplog.text
    assert "private_health_value" not in caplog.text


async def test_optional_probe_logs_only_counts_and_source_labels(
    hass, caplog, store: FakeStore, now: datetime
) -> None:
    """Optional metric probing checks availability without exposing health values."""
    caplog.set_level(logging.WARNING, logger="custom_components.resiyhome_health_sync.coordinator")
    client = FakeClient()
    client.raw = {
        "active-zone-minutes": [{"dataSource": {"platform": "FITBIT"}, "secret": "value"}],
        "daily-vo2-max": [{"dataSource": {"platform": "FITBIT"}, "secret": "value"}],
    }
    client.all_sources = {
        "active-zone-minutes": [{"activeZoneMinutes": {"activeZoneMinutes": "23"}}],
        "daily-vo2-max": [{"dailyVo2Max": {"vo2Max": 42}}],
    }
    client.wearables = {
        "active-zone-minutes": [{"activeZoneMinutes": {"activeZoneMinutes": "23"}}],
    }
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    result = await coordinator.async_probe_optional_data_types(days=7)

    assert result["active-zone-minutes"] == {
        "raw_count": 1,
        "all_sources_count": 1,
        "wearables_count": 1,
        "source_platforms": ("FITBIT",),
        "status": "ok",
    }
    assert result["daily-vo2-max"]["all_sources_count"] == 1
    message_text = "\n".join(caplog.messages)
    assert "Optional data type availability probe:" in message_text
    assert "active-zone-minutes status=ok raw=1 all_sources=1 wearables=1 platforms=FITBIT" in (
        message_text
    )
    assert "secret" not in message_text
    assert "23" not in message_text
    assert "42" not in message_text
    assert (
        "all-sources",
        "active-zone-minutes",
        now - timedelta(days=7),
        now,
    ) in client.calls


async def test_optional_probe_continues_after_one_data_type_fails(
    hass, store: FakeStore, now: datetime
) -> None:
    """One temporarily unavailable optional type cannot abort the probe."""
    first = OPTIONAL_PROBE_DATA_TYPES[0]
    second = OPTIONAL_PROBE_DATA_TYPES[1]
    client = FakeClient()
    client.failures[("all-sources", first)] = UpdateFailed("temporary")
    client.all_sources = {second: [{}]}
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    result = await coordinator.async_probe_optional_data_types(data_types=(first, second), days=3)

    assert result[first]["status"] == "error"
    assert result[second]["status"] == "ok"
    assert result[second]["all_sources_count"] == 1


async def test_optional_probe_respects_operation_capabilities(
    hass, store: FakeStore, now: datetime
) -> None:
    """Reconcile-only and rollup-only types never call unsupported endpoints."""
    client = FakeClient()
    client.all_sources = {"floors": [{"floors": {"count": "4"}}]}
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    result = await coordinator.async_probe_optional_data_types(
        data_types=("floors", "calories-in-heart-rate-zone"), days=14
    )

    assert result["floors"] == {
        "raw_count": 0,
        "all_sources_count": 1,
        "wearables_count": 0,
        "source_platforms": (),
        "status": "ok",
    }
    assert result["calories-in-heart-rate-zone"]["status"] == "requires_rollup"
    assert not any(call[0] == "raw" and call[1] == "floors" for call in client.calls)
    assert not any(call[1] == "calories-in-heart-rate-zone" for call in client.calls)


@pytest.mark.parametrize("days", [0, 15, True])
async def test_optional_probe_rejects_unsafe_ranges(
    hass, store: FakeStore, now: datetime, days: object
) -> None:
    """The probe stays inside Google's shortest documented query limit."""
    coordinator = HealthSyncCoordinator(hass, FakeClient(), store, now=lambda: now)

    with pytest.raises(ValueError, match="between 1 and 14"):
        await coordinator.async_probe_optional_data_types(days=days)  # type: ignore[arg-type]


async def test_invalid_current_sleep_logs_only_counts_and_availability(
    hass, caplog, client: FakeClient, store: FakeStore, now: datetime
) -> None:
    caplog.set_level(logging.WARNING, logger="custom_components.resiyhome_health_sync.coordinator")
    point = _sleep(
        start=now.replace(hour=4),
        start_offset_seconds=0,
        end=now.replace(hour=11),
        end_offset_seconds=0,
    )
    del point["sleep"]["summary"]
    client.all_sources["sleep"] = [point]
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.sleep_minutes is None
    assert "all_sources_count=1" in caplog.text
    assert "all_sources_summary_count=0" in caplog.text
    assert "all_sources_stage_count=0" in caplog.text
    assert "2042-07-13T04:00:00Z" not in caplog.text
    assert "2042-07-13T11:00:00Z" not in caplog.text
    assert "minutesAsleep" not in caplog.text


async def test_missing_sleep_stages_logs_counts_without_stage_shapes(
    hass, caplog, client: FakeClient, store: FakeStore, now: datetime
) -> None:
    caplog.set_level(logging.WARNING, logger="custom_components.resiyhome_health_sync.coordinator")
    point = _sleep(
        start=now.replace(hour=4),
        start_offset_seconds=0,
        end=now.replace(hour=11),
        end_offset_seconds=0,
    )
    point["sleep"]["summary"]["minutesAwake"] = "12"
    point["sleep"]["summary"]["stagesSummary"] = [{"unknownStageKeys": "present"}]
    client.all_sources["sleep"] = [point]
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    snapshot = await coordinator.async_manual_refresh()

    assert snapshot.current_day.sleep_minutes == 390
    assert snapshot.current_day.sleep_stages == {"awake": 12.0}
    assert "reason=missing_stage_breakdown" in caplog.text
    assert "all_sources_summary_count=1" in caplog.text
    assert "all_sources_stage_count=1" in caplog.text
    assert "unknownStageKeys" not in caplog.text
    assert "present" not in caplog.text
    assert "access_token" not in caplog.text
    assert "refresh_token" not in caplog.text


async def test_missing_sleep_stages_never_logs_stage_types_or_values(
    hass, caplog, client: FakeClient, store: FakeStore, now: datetime
) -> None:
    caplog.set_level(logging.WARNING, logger="custom_components.resiyhome_health_sync.coordinator")
    point = _sleep(
        start=now.replace(hour=4),
        start_offset_seconds=0,
        end=now.replace(hour=11),
        end_offset_seconds=0,
    )
    point["sleep"]["summary"]["minutesAwake"] = "12"
    point["sleep"]["summary"]["stagesSummary"] = [
        {"type": "AWAKE", "minutes": "12", "count": "4", "note": "private awake note"},
        {"type": "LIGHT", "minutes": "500", "count": "12"},
        {"type": "DEEP", "minutes": "70", "count": "3"},
        {"type": "REM", "minutes": "95", "count": "6"},
        {"type": "LIGHT", "minutes": "500", "count": "12"},
    ]
    client.all_sources["sleep"] = [point]
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    await coordinator.async_manual_refresh()

    assert "all_sources_stage_count=5" in caplog.text
    for private_value in (
        "AWAKE",
        "LIGHT",
        "DEEP",
        "REM",
        "500",
        "private awake note",
    ):
        assert private_value not in caplog.text
    assert "access_token" not in caplog.text
    assert "refresh_token" not in caplog.text


async def test_late_correction_replaces_same_date_without_duplicate(
    coordinator, client: FakeClient, store: FakeStore, now: datetime
) -> None:
    await coordinator.async_manual_refresh()
    client.all_sources["steps"] = [_steps(now.date(), 6400)]

    await coordinator.async_refresh_current()

    assert list(store.rows) == [now.date()]
    assert store.rows[now.date()].steps == 6400


async def test_staleness_starts_after_forty_five_minutes(
    hass, client: FakeClient, store: FakeStore, now: datetime
) -> None:
    clock = [now]
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: clock[0])
    await coordinator.async_manual_refresh()

    clock[0] += timedelta(minutes=45)
    assert coordinator.is_stale is False
    clock[0] += timedelta(seconds=1)
    assert coordinator.is_stale is True


async def test_current_refresh_runs_before_waiting_backfill(
    hass, client: FakeClient, store: FakeStore, now: datetime
) -> None:
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)
    await coordinator._lock.acquire()
    backfill = hass.async_create_task(coordinator.async_backfill_step())
    await asyncio.sleep(0)
    current = hass.async_create_task(coordinator.async_refresh_current())
    await asyncio.sleep(0)

    coordinator._lock.release()
    await current
    await backfill

    first_reconcile = next(call for call in client.calls if call[0] == "all-sources")
    assert first_reconcile[3] - first_reconcile[2] == timedelta(days=1)


async def test_current_refresh_runs_between_core_and_expanded_backfill_windows(
    hass, client: FakeClient, now: datetime
) -> None:
    store = FakeStore()
    store.checkpoint_committed = asyncio.Event()
    store.release_checkpoint = asyncio.Event()
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    backfill = hass.async_create_task(coordinator.async_backfill_step())
    await store.checkpoint_committed.wait()
    current = hass.async_create_task(coordinator.async_refresh_current())
    while coordinator._current_waiters == 0:
        await asyncio.sleep(0)
    store.release_checkpoint.set()

    await current
    await backfill

    core_index = next(
        index
        for index, call in enumerate(client.calls)
        if call[:2] == ("all-sources", "steps") and call[3] - call[2] == timedelta(days=7)
    )
    current_index = next(
        index
        for index, call in enumerate(client.calls)
        if call[:2] == ("all-sources", "steps") and call[3] - call[2] == timedelta(days=1)
    )
    expanded_index = next(
        index
        for index, call in enumerate(client.calls)
        if call[:2] == ("all-sources", "daily-vo2-max") and call[3] - call[2] == timedelta(days=14)
    )
    assert core_index < current_index < expanded_index
