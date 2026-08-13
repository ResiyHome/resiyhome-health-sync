import asyncio
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, tzinfo
from math import inf, nan

import pytest

from custom_components.resiyhome_health_sync.capabilities import CapabilityId
from custom_components.resiyhome_health_sync.performance import (
    OPERATIONS,
    OUTCOMES,
    PerformanceOperation,
    PerformanceRecorder,
    record_google_request,
    record_storage_read,
    record_storage_write,
)

NOW = datetime(2042, 7, 13, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_recorder_collects_only_allowlisted_bounded_operation_data() -> None:
    clock = Clock()
    recorder = PerformanceRecorder(record_limit=2, monotonic=clock)
    first = recorder.begin(
        "current_refresh",
        backfill_active=True,
        enabled_capabilities=("sleep", "core_activity"),
        started_at=NOW,
    )
    token = first.activate()
    record_google_request(0.125)
    record_storage_read(0.010)
    record_storage_write(0.020)
    first.add_coordinator_wait(0.005)
    clock.advance(0.250)
    first.finish("success")
    first.deactivate(token)

    assert recorder.snapshot()[0].to_diagnostics() == {
        "operation": "current_refresh",
        "started_at": "2042-07-13T12:00:00+00:00",
        "duration_ms": 250,
        "google_request_count": 1,
        "google_request_duration_ms": 125,
        "storage_read_count": 1,
        "storage_read_duration_ms": 10,
        "storage_write_count": 1,
        "storage_write_duration_ms": 20,
        "queued_wait_duration_ms": 5,
        "backfill_active": True,
        "enabled_capabilities": ["core_activity", "sleep"],
        "outcome": "success",
        "slowest_phase": "google_requests",
    }


def test_recorder_evicts_oldest_record_at_limit() -> None:
    clock = Clock()
    recorder = PerformanceRecorder(record_limit=2, monotonic=clock)

    for operation in ("current_refresh", "backfill_step", "current_refresh"):
        measurement = recorder.begin(
            operation,
            backfill_active=False,
            enabled_capabilities=(),
            started_at=NOW,
        )
        measurement.finish("success")
        clock.advance(1)

    assert [record.operation for record in recorder.snapshot()] == [
        "backfill_step",
        "current_refresh",
    ]


def test_module_helpers_are_no_op_without_active_context() -> None:
    record_google_request(0.125)
    record_storage_read(0.010)
    record_storage_write(0.020)

    assert PerformanceRecorder().snapshot() == ()


async def _run_isolated_operation(
    recorder: PerformanceRecorder, duration_seconds: float
) -> None:
    operation = recorder.begin(
        "current_refresh",
        backfill_active=False,
        enabled_capabilities=(),
        started_at=NOW,
    )
    token = operation.activate()
    record_google_request(duration_seconds)
    await asyncio.sleep(0)
    operation.finish("success")
    operation.deactivate(token)


@pytest.mark.asyncio
async def test_async_tasks_keep_active_operations_context_local() -> None:
    first = PerformanceRecorder()
    second = PerformanceRecorder()

    await asyncio.gather(
        _run_isolated_operation(first, 0.125),
        _run_isolated_operation(second, 0.250),
    )

    assert first.snapshot()[0].google_request_count == 1
    assert first.snapshot()[0].google_request_duration_ms == 125
    assert second.snapshot()[0].google_request_count == 1
    assert second.snapshot()[0].google_request_duration_ms == 250


def test_finish_and_deactivate_are_idempotent() -> None:
    recorder = PerformanceRecorder()
    operation = recorder.begin(
        "current_refresh",
        backfill_active=False,
        enabled_capabilities=(),
        started_at=NOW,
    )
    token = operation.activate()

    operation.finish("success")
    operation.finish("success")
    operation.deactivate(token)
    operation.deactivate(token)

    assert len(recorder.snapshot()) == 1


def test_invalid_inputs_fail_closed_without_raising() -> None:
    clock = Clock()
    recorder = PerformanceRecorder(monotonic=clock)

    assert (
        recorder.begin(
            "not_an_operation",
            backfill_active=False,
            enabled_capabilities=(),
            started_at=NOW,
        )
        is None
    )

    operation = recorder.begin(
        "current_refresh",
        backfill_active=False,
        enabled_capabilities=(
            CapabilityId.SLEEP,
            "sleep",
            CapabilityId.CORE_ACTIVITY,
            object(),
        ),
        started_at=NOW,
    )
    token = operation.activate()
    for duration in (-1, nan, inf, None, "invalid"):
        record_google_request(duration)
        record_storage_read(duration)
        record_storage_write(duration)
        operation.add_coordinator_wait(duration)
    operation.finish("not_an_outcome")
    operation.deactivate(token)

    assert recorder.snapshot() == ()

    valid = recorder.begin(
        "backfill_step",
        backfill_active=True,
        enabled_capabilities=(CapabilityId.SLEEP, "sleep", CapabilityId.CORE_ACTIVITY),
        started_at=NOW,
    )
    valid.finish("success")

    valid_record = recorder.snapshot()[0]
    assert valid_record.enabled_capabilities == (
        CapabilityId.CORE_ACTIVITY,
        CapabilityId.SLEEP,
    )
    assert valid_record.google_request_count == 0
    assert valid_record.storage_read_count == 0
    assert valid_record.storage_write_count == 0
    assert valid_record.queued_wait_duration_ms == 0


def test_records_are_frozen_allowlisted_snapshots() -> None:
    recorder = PerformanceRecorder()
    operation = recorder.begin(
        "current_refresh",
        backfill_active=False,
        enabled_capabilities=(CapabilityId.SLEEP,),
        started_at=NOW,
    )
    operation.finish("success")
    record = recorder.snapshot()[0]

    with pytest.raises(FrozenInstanceError):
        record.duration_ms = 1

    assert isinstance(record.enabled_capabilities, tuple)
    assert record.operation in OPERATIONS
    assert record.outcome in OUTCOMES
    assert isinstance(record.started_at, datetime)
    assert all(isinstance(capability, CapabilityId) for capability in record.enabled_capabilities)


class ExplodingTimezone(tzinfo):
    def utcoffset(self, value: datetime) -> None:
        raise RuntimeError("malformed timezone")


class ExplodingCapabilities:
    def __iter__(self):
        raise RuntimeError("malformed capability iterable")


def _finish_without_raising(operation: PerformanceOperation) -> None:
    token = operation.activate()
    operation.finish("success")
    operation.deactivate(token)


def test_malformed_started_at_returns_inert_operation() -> None:
    recorder = PerformanceRecorder()
    malformed_started_at = datetime(2042, 7, 13, 12, 0, tzinfo=ExplodingTimezone())

    operation = recorder.begin(
        "current_refresh",
        backfill_active=False,
        enabled_capabilities=(),
        started_at=malformed_started_at,
    )

    assert operation is not None
    _finish_without_raising(operation)
    assert recorder.snapshot() == ()


def test_capability_iterator_failure_returns_inert_operation() -> None:
    recorder = PerformanceRecorder()

    operation = recorder.begin(
        "current_refresh",
        backfill_active=False,
        enabled_capabilities=ExplodingCapabilities(),
        started_at=NOW,
    )

    assert operation is not None
    _finish_without_raising(operation)
    assert recorder.snapshot() == ()


def test_monotonic_clock_failure_returns_inert_operation() -> None:
    def failing_monotonic() -> float:
        raise RuntimeError("clock unavailable")

    recorder = PerformanceRecorder(monotonic=failing_monotonic)

    operation = recorder.begin(
        "current_refresh",
        backfill_active=False,
        enabled_capabilities=(),
        started_at=NOW,
    )

    assert operation is not None
    _finish_without_raising(operation)
    assert recorder.snapshot() == ()


def test_operation_construction_failure_returns_inert_operation(monkeypatch) -> None:
    def failing_init(self, *args, **kwargs) -> None:
        raise RuntimeError("construction failed")

    monkeypatch.setattr(PerformanceOperation, "__init__", failing_init)
    recorder = PerformanceRecorder()

    operation = recorder.begin(
        "current_refresh",
        backfill_active=False,
        enabled_capabilities=(),
        started_at=NOW,
    )

    assert operation is not None
    _finish_without_raising(operation)
    assert recorder.snapshot() == ()
