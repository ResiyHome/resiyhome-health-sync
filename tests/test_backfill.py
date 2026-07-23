"""Tests for bounded and resumable Health Sync history backfill."""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest
from test_coordinator import (
    FakeClient,
    FakeStore,
    _daily_point,
    _daily_rollup,
    _steps,
    _weight,
)

from custom_components.resiyhome_health_sync.api import AuthenticationError, UpdateFailed
from custom_components.resiyhome_health_sync.coordinator import HealthSyncCoordinator
from custom_components.resiyhome_health_sync.models import DailySummary, ExpandedDailyMetrics
from custom_components.resiyhome_health_sync.storage import HealthHistoryStore


class _HighVolumeWindowClient(FakeClient):
    """Model a stream that exceeds transport pagination beyond seven days."""

    async def async_list_data_points(
        self, data_type: str, *, start: datetime, end: datetime
    ) -> list[dict]:
        if data_type == "heart-rate" and end - start > timedelta(days=7):
            raise UpdateFailed("pagination exceeded the result limit")
        return await super().async_list_data_points(data_type, start=start, end=end)

    async def async_reconcile_data_points(
        self,
        data_type: str,
        *,
        start: datetime,
        end: datetime,
        source_family: str,
    ) -> list[dict]:
        if data_type == "heart-rate" and end - start > timedelta(days=7):
            raise UpdateFailed("pagination exceeded the result limit")
        return await super().async_reconcile_data_points(
            data_type,
            start=start,
            end=end,
            source_family=source_family,
        )


async def test_backfill_bounds_high_volume_core_window_to_seven_days(hass) -> None:
    now = datetime(2042, 7, 13, 15, 0, tzinfo=UTC)
    store = FakeStore()
    coordinator = HealthSyncCoordinator(
        hass,
        _HighVolumeWindowClient(),
        store,
        now=lambda: now,
    )

    snapshot = await coordinator.async_backfill_step()

    assert store.backfill_cursor == date(2042, 7, 6)
    assert snapshot.backfill_cursor == date(2042, 7, 6)


async def test_backfill_uses_7_day_window_and_persists_checkpoint(hass) -> None:
    now = datetime(2042, 7, 13, 15, 0, tzinfo=UTC)
    client = FakeClient()
    client.all_sources["steps"] = [_steps(date(2042, 7, 10), 7200)]
    client.wearables["steps"] = [_steps(date(2042, 7, 10), 7000)]
    store = FakeStore()
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    snapshot = await coordinator.async_backfill_step()

    assert store.backfill_cursor == date(2042, 7, 6)
    assert store.checkpoints == [date(2042, 7, 6)]
    assert store.rows[date(2042, 7, 10)].steps == 7200
    assert snapshot.backfill_cursor == date(2042, 7, 6)
    windows = {
        (call[2], call[3])
        for call in client.calls
        if call[:2] == ("all-sources", "steps")
        and call[2] is not None
        and call[3] is not None
    }
    assert windows == {
        (
            datetime(2042, 7, 6, tzinfo=UTC),
            datetime(2042, 7, 13, tzinfo=UTC),
        )
    }


async def test_empty_window_advances_and_does_not_complete_early(hass) -> None:
    now = datetime(2042, 7, 13, 15, 0, tzinfo=UTC)
    store = FakeStore()
    coordinator = HealthSyncCoordinator(hass, FakeClient(), store, now=lambda: now)

    first = await coordinator.async_backfill_step()
    second = await coordinator.async_backfill_step()

    assert store.checkpoints == [date(2042, 7, 6), date(2042, 6, 29)]
    assert first.backfill_complete is False
    assert second.backfill_complete is False


async def test_recreated_coordinator_resumes_from_durable_checkpoint(hass) -> None:
    now = datetime(2042, 7, 13, 15, 0, tzinfo=UTC)
    store = FakeStore(cursor=date(2042, 6, 12))
    client = FakeClient()
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    await coordinator.async_backfill_step()

    assert store.backfill_cursor == date(2042, 6, 5)
    first_window = next(call for call in client.calls if call[0] == "all-sources")
    assert first_window[2] == datetime(2042, 6, 5, tzinfo=UTC)
    assert first_window[3] == datetime(2042, 6, 12, tzinfo=UTC)


async def test_failed_window_does_not_advance_checkpoint(hass) -> None:
    now = datetime(2042, 7, 13, 15, 0, tzinfo=UTC)
    client = FakeClient()
    store = FakeStore(cursor=date(2042, 6, 12))
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)
    for data_type in coordinator.data_types:
        client.failures[("all-sources", data_type)] = UpdateFailed("temporary")

    with pytest.raises(UpdateFailed):
        await coordinator.async_backfill_step()

    assert store.backfill_cursor == date(2042, 6, 12)
    assert store.checkpoints == []


async def test_raw_failure_with_successful_reconciliation_retries_same_window(hass) -> None:
    now = datetime(2042, 7, 13, 15, 0, tzinfo=UTC)
    returned_day = date(2042, 7, 10)
    client = FakeClient()
    client.raw["steps"] = [_steps(returned_day, 6900)]
    client.all_sources["steps"] = [_steps(returned_day, 7000)]
    client.wearables["steps"] = [_steps(returned_day, 6800)]
    client.failures[("raw", "steps")] = UpdateFailed("raw attribution unavailable")
    store = FakeStore()
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    with pytest.raises(UpdateFailed):
        await coordinator.async_backfill_step()

    assert store.rows == {}
    assert store.backfill_cursor is None
    assert store.checkpoints == []


async def test_backfill_auth_failure_marks_authorization_unhealthy(hass) -> None:
    now = datetime(2042, 7, 13, 15, 0, tzinfo=UTC)
    client = FakeClient()
    client.failures[("all-sources", "steps")] = AuthenticationError("reauthorize")
    store = FakeStore()
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    with pytest.raises(AuthenticationError):
        await coordinator.async_backfill_step()

    assert coordinator.data.authorization_healthy is False
    assert store.checkpoints == []


async def test_cancelled_backfill_releases_lock_without_advancing_checkpoint(hass) -> None:
    now = datetime(2042, 7, 13, 15, 0, tzinfo=UTC)
    client = FakeClient()
    client.backfill_gate = asyncio.Event()
    store = FakeStore()
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    task = hass.async_create_task(coordinator.async_backfill_step())
    while not client.calls:
        await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.checkpoints == []
    assert coordinator._lock.locked() is False


async def test_cancellation_after_row_upsert_leaves_checkpoint_for_idempotent_retry(hass) -> None:
    now = datetime(2042, 7, 13, 15, 0, tzinfo=UTC)
    returned_day = date(2042, 7, 10)
    client = FakeClient()
    client.all_sources["steps"] = [_steps(returned_day, 7000)]
    store = FakeStore()
    store.upsert_committed = asyncio.Event()
    store.release_upsert = asyncio.Event()
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    task = hass.async_create_task(coordinator.async_backfill_step())
    await store.upsert_committed.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.rows[returned_day].steps == 7000
    assert store.backfill_cursor is None
    assert coordinator.data.backfill_cursor is None
    assert store.checkpoints == []

    store.upsert_committed = None
    store.release_upsert = None
    await coordinator.async_backfill_step()
    assert [day for day, row in store.rows.items() if row.steps is not None] == [
        returned_day
    ]
    assert store.backfill_cursor == date(2042, 7, 6)


async def test_cancellation_during_committed_checkpoint_keeps_states_consistent(hass) -> None:
    now = datetime(2042, 7, 13, 15, 0, tzinfo=UTC)
    returned_day = date(2042, 7, 10)
    expected_cursor = date(2042, 7, 6)
    client = FakeClient()
    client.all_sources["steps"] = [_steps(returned_day, 7000)]
    store = FakeStore()
    store.checkpoint_committed = asyncio.Event()
    store.release_checkpoint = asyncio.Event()
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    task = hass.async_create_task(coordinator.async_backfill_step())
    await store.checkpoint_committed.wait()
    task.cancel()
    store.release_checkpoint.set()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert store.backfill_cursor == expected_cursor
    assert store.checkpoints == [expected_cursor]
    assert coordinator.data.backfill_cursor == expected_cursor


async def test_real_history_store_precommit_cancellation_keeps_checkpoint(
    hass, monkeypatch
) -> None:
    history = HealthHistoryStore(hass, "task-5-cancelled-checkpoint")
    await history.async_load()
    save_started = asyncio.Event()

    async def blocked_save(_document: dict[str, object]) -> None:
        save_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(history._store, "async_save", blocked_save)
    task = hass.async_create_task(history.async_set_backfill_checkpoint(date(2042, 6, 12)))
    await save_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert history.backfill_cursor is None


async def test_backfill_stops_at_twenty_year_provider_boundary(hass) -> None:
    now = datetime(2042, 7, 13, 15, 0, tzinfo=UTC)
    store = FakeStore(cursor=date(2022, 7, 14))
    client = FakeClient()
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    snapshot = await coordinator.async_backfill_step()
    completed = await coordinator.async_backfill_step()

    assert store.backfill_cursor == date(2022, 7, 13)
    assert snapshot.backfill_complete is True
    assert completed.backfill_complete is True
    assert len(
        [
            call
            for call in client.calls
            if call[0] == "all-sources" and call[1] in coordinator.data_types
        ]
    ) == len(coordinator.data_types)


async def test_backfill_late_correction_replaces_existing_day(hass) -> None:
    now = datetime(2042, 7, 13, 15, 0, tzinfo=UTC)
    corrected_day = date(2042, 7, 10)
    store = FakeStore()
    client = FakeClient()
    client.all_sources["steps"] = [_steps(corrected_day, 7000)]
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)
    await coordinator.async_backfill_step()

    client.all_sources["steps"] = [_steps(corrected_day, 7100)]
    store.backfill_cursor = date(2042, 7, 13)
    await coordinator.async_backfill_step()

    assert [day for day, row in store.rows.items() if row.steps is not None] == [
        corrected_day
    ]
    assert store.rows[corrected_day].steps == 7100


async def test_core_backfill_preserves_existing_expanded_metrics(hass) -> None:
    now = datetime(2042, 7, 13, 15, 0, tzinfo=UTC)
    returned_day = date(2042, 7, 10)
    store = FakeStore(
        [
            DailySummary(
                date=returned_day,
                expanded=ExpandedDailyMetrics(floors=7),
            )
        ],
        expanded_cursor=date(2042, 4, 14),
    )
    client = FakeClient()
    client.all_sources["steps"] = [_steps(returned_day, 7000)]
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    await coordinator.async_backfill_step()

    assert store.rows[returned_day].steps == 7000
    assert store.rows[returned_day].expanded.floors == 7


async def test_expanded_backfill_is_bounded_checkpointed_and_core_cursor_independent(
    hass,
) -> None:
    now = datetime(2042, 7, 21, 15, 0, tzinfo=UTC)
    core_boundary = date(2022, 7, 21)
    expected_cursors = [
        date(2042, 7, 7),
        date(2042, 6, 23),
        date(2042, 6, 9),
        date(2042, 5, 26),
        date(2042, 5, 12),
        date(2042, 4, 28),
        date(2042, 4, 22),
    ]
    client = FakeClient()
    store = FakeStore(cursor=core_boundary)
    coordinator = HealthSyncCoordinator(
        hass,
        client,
        store,
        now=lambda: now,
        include_body_measurements=True,
    )

    for expected_cursor in expected_cursors:
        snapshot = await coordinator.async_backfill_step()
        assert snapshot.expanded_backfill_cursor == expected_cursor
        assert store.expanded_backfill_cursor == expected_cursor

    assert snapshot.expanded_backfill_complete is True
    assert snapshot.backfill_cursor == core_boundary
    assert store.backfill_cursor == core_boundary
    expanded_calls = [
        call
        for call in client.calls
        if call[1]
        in {
            "daily-vo2-max",
            "daily-oxygen-saturation",
            "daily-respiratory-rate",
            "respiratory-rate-sleep-summary",
            "daily-heart-rate-zones",
            "active-zone-minutes",
            "floors",
            "sedentary-period",
            "time-in-heart-rate-zone",
            "calories-in-heart-rate-zone",
            "weight",
        }
    ]
    assert expanded_calls
    assert all(call[3] - call[2] <= timedelta(days=14) for call in expanded_calls)
    assert min(call[2].date() for call in expanded_calls) == date(2042, 4, 22)
    assert all(call[2].date() >= date(2042, 4, 22) for call in expanded_calls)


async def test_recreated_coordinator_resumes_expanded_backfill_checkpoint(hass) -> None:
    now = datetime(2042, 7, 21, 15, 0, tzinfo=UTC)
    core_cursor = date(2006, 7, 21)
    store = FakeStore(
        cursor=core_cursor,
        expanded_cursor=date(2042, 6, 23),
    )
    client = FakeClient()

    first = HealthSyncCoordinator(hass, client, store, now=lambda: now)
    snapshot = await first.async_backfill_step()
    recreated = HealthSyncCoordinator(hass, FakeClient(), store, now=lambda: now)

    assert snapshot.expanded_backfill_cursor == date(2042, 6, 9)
    assert recreated.data.expanded_backfill_cursor == date(2042, 6, 9)
    assert recreated.data.backfill_cursor == core_cursor


async def test_weight_backfill_stores_only_measurement_day_and_updates_latest_snapshot(
    hass,
) -> None:
    now = datetime(2042, 7, 21, 15, 0, tzinfo=UTC)
    measured_day = date(2042, 7, 15)
    client = FakeClient()
    client.all_sources["weight"] = [_weight(measured_day, 80_500.0)]
    store = FakeStore(cursor=date(2006, 7, 21))
    coordinator = HealthSyncCoordinator(
        hass,
        client,
        store,
        now=lambda: now,
        include_body_measurements=True,
    )

    snapshot = await coordinator.async_backfill_step()

    assert store.rows[measured_day].expanded.weight_kg == 80.5
    assert all(
        summary.expanded.weight_kg is None
        for day, summary in store.rows.items()
        if day != measured_day
    )
    assert snapshot.latest_weight_kg == 80.5
    assert snapshot.latest_weight_at == measured_day


async def test_multi_day_expanded_backfill_persists_each_direct_and_rollup_day(
    hass,
) -> None:
    """Advancing the cursor cannot hide values dropped from a multi-day response."""
    now = datetime(2042, 7, 21, 15, 0, tzinfo=UTC)
    first_day = date(2042, 7, 15)
    second_day = date(2042, 7, 16)
    client = FakeClient()
    client.all_sources["daily-vo2-max"] = [
        _daily_point(first_day, "dailyVo2Max", vo2Max=41.0),
        _daily_point(second_day, "dailyVo2Max", vo2Max=42.0),
    ]
    client.rollups["floors"] = [
        _daily_rollup(first_day, "floors", countSum="6"),
        _daily_rollup(second_day, "floors", countSum="7"),
    ]
    store = FakeStore(cursor=date(2006, 7, 21))
    coordinator = HealthSyncCoordinator(hass, client, store, now=lambda: now)

    snapshot = await coordinator.async_backfill_step()

    assert store.rows[first_day].expanded.vo2_max == 41.0
    assert store.rows[first_day].expanded.floors == 6
    assert store.rows[second_day].expanded.vo2_max == 42.0
    assert store.rows[second_day].expanded.floors == 7
    assert snapshot.expanded_backfill_cursor == date(2042, 7, 7)


async def test_enabling_body_measurements_restarts_completed_expanded_backfill(
    hass,
) -> None:
    """A completed metric-only cursor cannot suppress a newly enabled weight import."""
    now = datetime(2042, 7, 21, 15, 0, tzinfo=UTC)
    boundary = date(2042, 4, 22)
    store = FakeStore(
        cursor=date(2006, 7, 21),
        expanded_cursor=boundary,
        body_measurements_enabled=False,
    )
    client = FakeClient()
    client.all_sources["weight"] = [_weight(date(2042, 7, 15), 80_500.0)]
    coordinator = HealthSyncCoordinator(
        hass,
        client,
        store,
        now=lambda: now,
        include_body_measurements=True,
    )

    snapshot = await coordinator.async_backfill_step()

    weight_calls = [call for call in client.calls if call[1] == "weight"]
    assert weight_calls[0][2:] == (
        datetime(2042, 7, 7, tzinfo=UTC),
        datetime(2042, 7, 21, tzinfo=UTC),
    )
    assert store.rows[date(2042, 7, 15)].expanded.weight_kg == 80.5
    assert snapshot.expanded_backfill_cursor == date(2042, 7, 7)


async def test_enabled_body_backfill_resumes_without_reset_after_restart(hass) -> None:
    """A durable option marker makes a partially imported weight range idempotent."""
    now = datetime(2042, 7, 21, 15, 0, tzinfo=UTC)
    store = FakeStore(
        cursor=date(2006, 7, 21),
        expanded_cursor=date(2042, 4, 22),
        body_measurements_enabled=False,
    )

    first = HealthSyncCoordinator(
        hass,
        FakeClient(),
        store,
        now=lambda: now,
        include_body_measurements=True,
    )
    await first.async_backfill_step()
    restarted_client = FakeClient()
    restarted = HealthSyncCoordinator(
        hass,
        restarted_client,
        store,
        now=lambda: now,
        include_body_measurements=True,
    )

    snapshot = await restarted.async_backfill_step()

    weight_call = next(call for call in restarted_client.calls if call[1] == "weight")
    assert weight_call[2:] == (
        datetime(2042, 6, 23, tzinfo=UTC),
        datetime(2042, 7, 7, tzinfo=UTC),
    )
    assert snapshot.expanded_backfill_cursor == date(2042, 6, 23)


async def test_disabling_body_measurements_scrubs_current_and_latest_weight(
    hass,
) -> None:
    """An opted-out coordinator cannot retain weight in history or its snapshot."""
    now = datetime(2042, 7, 21, 15, 0, tzinfo=UTC)
    stored = DailySummary(
        date=now.date(),
        expanded=ExpandedDailyMetrics(weight_kg=80.5),
    )
    store = FakeStore(
        [stored],
        cursor=date(2006, 7, 21),
        expanded_cursor=date(2042, 4, 22),
        body_measurements_enabled=True,
    )
    coordinator = HealthSyncCoordinator(
        hass,
        FakeClient(),
        store,
        now=lambda: now,
        include_body_measurements=False,
    )

    snapshot = await coordinator.async_refresh_current()

    assert store.body_measurements_enabled is False
    assert store.rows[now.date()].expanded.weight_kg is None
    assert snapshot.current_day is not None
    assert snapshot.current_day.expanded.weight_kg is None
    assert snapshot.latest_weight_kg is None
    assert snapshot.latest_weight_at is None
