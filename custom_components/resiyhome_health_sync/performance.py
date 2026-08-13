"""Bounded, in-memory performance measurements for Health Sync operations."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Iterable
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite

from .capabilities import CapabilityId

OPERATIONS = frozenset({"current_refresh", "backfill_step"})
OUTCOMES = frozenset(
    {"success", "temporary_error", "authorization_error", "storage_error", "cancelled"}
)
PHASES = frozenset(
    {"google_requests", "storage_reads", "storage_writes", "coordinator_wait", "other"}
)
RECORD_LIMIT = 12
_PHASE_ORDER = (
    "google_requests",
    "storage_reads",
    "storage_writes",
    "coordinator_wait",
    "other",
)


@dataclass(frozen=True, slots=True)
class PerformanceRecord:
    """Immutable snapshot of one completed allowlisted operation."""

    operation: str
    started_at: datetime
    duration_ms: int
    google_request_count: int
    google_request_duration_ms: int
    storage_read_count: int
    storage_read_duration_ms: int
    storage_write_count: int
    storage_write_duration_ms: int
    queued_wait_duration_ms: int
    backfill_active: bool
    enabled_capabilities: tuple[CapabilityId, ...]
    outcome: str
    slowest_phase: str
    other_duration_ms: int

    def to_diagnostics(self) -> dict[str, object]:
        """Serialize this snapshot into the bounded diagnostics shape."""
        return {
            "operation": self.operation,
            "started_at": self.started_at.isoformat(),
            "duration_ms": self.duration_ms,
            "google_request_count": self.google_request_count,
            "google_request_duration_ms": self.google_request_duration_ms,
            "storage_read_count": self.storage_read_count,
            "storage_read_duration_ms": self.storage_read_duration_ms,
            "storage_write_count": self.storage_write_count,
            "storage_write_duration_ms": self.storage_write_duration_ms,
            "queued_wait_duration_ms": self.queued_wait_duration_ms,
            "backfill_active": self.backfill_active,
            "enabled_capabilities": [
                capability.value for capability in self.enabled_capabilities
            ],
            "outcome": self.outcome,
            "slowest_phase": self.slowest_phase,
        }


@dataclass(slots=True)
class _MeasurementState:
    google_request_count: int = 0
    google_request_duration_ms: int = 0
    storage_read_count: int = 0
    storage_read_duration_ms: int = 0
    storage_write_count: int = 0
    storage_write_duration_ms: int = 0
    coordinator_wait_duration_ms: int = 0


_ACTIVE_OPERATION: ContextVar[PerformanceOperation | None] = ContextVar(
    "resiyhome_health_sync_active_performance_operation", default=None
)


def _duration_ms(duration_seconds: object) -> int | None:
    """Return rounded milliseconds for a finite, non-negative duration."""
    try:
        seconds = float(duration_seconds)
        if not isfinite(seconds) or seconds < 0:
            return None
        return max(0, round(seconds * 1000))
    except (OverflowError, TypeError, ValueError):
        return None


def _capabilities(values: Iterable[object]) -> tuple[CapabilityId, ...]:
    normalized: set[CapabilityId] = set()
    for value in values:
        if isinstance(value, CapabilityId):
            normalized.add(value)
        elif isinstance(value, str):
            try:
                normalized.add(CapabilityId(value))
            except ValueError:
                continue
    return tuple(sorted(normalized, key=lambda capability: capability.value))


def _started_at(value: datetime | None) -> datetime:
    if not isinstance(value, datetime):
        return datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class PerformanceOperation:
    """Mutable operation state that becomes one immutable record on finish."""

    def __init__(
        self,
        recorder: PerformanceRecorder,
        operation: str,
        *,
        backfill_active: bool,
        enabled_capabilities: tuple[CapabilityId, ...],
        started_at: datetime,
        started_monotonic: float,
    ) -> None:
        self._recorder = recorder
        self._operation = operation
        self._backfill_active = backfill_active
        self._enabled_capabilities = enabled_capabilities
        self._started_at = started_at
        self._started_monotonic = started_monotonic
        self._state = _MeasurementState()
        self._finished = False

    def activate(self) -> Token[PerformanceOperation | None]:
        """Make this operation active in the current execution context."""
        return _ACTIVE_OPERATION.set(self)

    def deactivate(self, token: Token[PerformanceOperation | None]) -> None:
        """Restore the previous active operation, ignoring repeated cleanup."""
        try:
            _ACTIVE_OPERATION.reset(token)
        except (RuntimeError, ValueError):
            pass

    def add_google_request(self, duration_seconds: object) -> None:
        self._add("google_request", duration_seconds)

    def add_storage_read(self, duration_seconds: object) -> None:
        self._add("storage_read", duration_seconds)

    def add_storage_write(self, duration_seconds: object) -> None:
        self._add("storage_write", duration_seconds)

    def add_coordinator_wait(self, duration_seconds: object) -> None:
        self._add("coordinator_wait", duration_seconds)

    def _add(self, measurement: str, duration_seconds: object) -> None:
        if self._finished:
            return
        duration = _duration_ms(duration_seconds)
        if duration is None:
            return
        if measurement == "google_request":
            self._state.google_request_count += 1
            self._state.google_request_duration_ms += duration
        elif measurement == "storage_read":
            self._state.storage_read_count += 1
            self._state.storage_read_duration_ms += duration
        elif measurement == "storage_write":
            self._state.storage_write_count += 1
            self._state.storage_write_duration_ms += duration
        else:
            self._state.coordinator_wait_duration_ms += duration

    def finish(self, outcome: object) -> None:
        if self._finished:
            return
        self._finished = True
        if not isinstance(outcome, str) or outcome not in OUTCOMES:
            return

        try:
            total_duration = _duration_ms(self._recorder._monotonic() - self._started_monotonic)
        except (TypeError, ValueError, OverflowError):
            total_duration = None
        total_duration = total_duration or 0
        phase_durations = {
            "google_requests": self._state.google_request_duration_ms,
            "storage_reads": self._state.storage_read_duration_ms,
            "storage_writes": self._state.storage_write_duration_ms,
            "coordinator_wait": self._state.coordinator_wait_duration_ms,
        }
        other_duration = max(0, total_duration - sum(phase_durations.values()))
        phase_durations["other"] = other_duration
        slowest_phase = max(_PHASE_ORDER, key=phase_durations.__getitem__)
        self._recorder._append(
            PerformanceRecord(
                operation=self._operation,
                started_at=self._started_at,
                duration_ms=total_duration,
                google_request_count=self._state.google_request_count,
                google_request_duration_ms=self._state.google_request_duration_ms,
                storage_read_count=self._state.storage_read_count,
                storage_read_duration_ms=self._state.storage_read_duration_ms,
                storage_write_count=self._state.storage_write_count,
                storage_write_duration_ms=self._state.storage_write_duration_ms,
                queued_wait_duration_ms=self._state.coordinator_wait_duration_ms,
                backfill_active=self._backfill_active,
                enabled_capabilities=self._enabled_capabilities,
                outcome=outcome,
                slowest_phase=slowest_phase,
                other_duration_ms=other_duration,
            )
        )


class PerformanceRecorder:
    """Bounded recorder for completed Health Sync performance operations."""

    def __init__(
        self, record_limit: int = RECORD_LIMIT, *, monotonic: Callable[[], float] = time.monotonic
    ) -> None:
        self._records: deque[PerformanceRecord] = deque(maxlen=record_limit)
        self._monotonic = monotonic

    def begin(
        self,
        operation: object,
        *,
        backfill_active: object,
        enabled_capabilities: Iterable[object],
        started_at: datetime | None = None,
    ) -> PerformanceOperation | None:
        """Start an allowlisted operation, or return None for invalid telemetry."""
        if not isinstance(operation, str) or operation not in OPERATIONS:
            return None
        try:
            started_monotonic = float(self._monotonic())
            active = bool(backfill_active)
            capabilities = _capabilities(enabled_capabilities)
            normalized_started_at = _started_at(started_at)
            return PerformanceOperation(
                self,
                operation,
                backfill_active=active,
                enabled_capabilities=capabilities,
                started_at=normalized_started_at,
                started_monotonic=started_monotonic,
            )
        except Exception:
            return _NoOpPerformanceOperation()

    def _append(self, record: PerformanceRecord) -> None:
        self._records.append(record)

    def snapshot(self) -> tuple[PerformanceRecord, ...]:
        """Return immutable references to the bounded record snapshots."""
        return tuple(self._records)


class _NoOpPerformanceOperation(PerformanceOperation):
    """Inert operation returned when telemetry setup itself fails."""

    def __init__(self) -> None:
        pass

    def activate(self) -> Token[PerformanceOperation | None]:
        return _ACTIVE_OPERATION.set(None)

    def deactivate(self, token: Token[PerformanceOperation | None]) -> None:
        try:
            _ACTIVE_OPERATION.reset(token)
        except (RuntimeError, ValueError):
            pass

    def add_google_request(self, duration_seconds: object) -> None:
        pass

    def add_storage_read(self, duration_seconds: object) -> None:
        pass

    def add_storage_write(self, duration_seconds: object) -> None:
        pass

    def add_coordinator_wait(self, duration_seconds: object) -> None:
        pass

    def finish(self, outcome: object) -> None:
        pass


def _record_active(method: str, duration_seconds: object) -> None:
    operation = _ACTIVE_OPERATION.get()
    if operation is None:
        return
    try:
        getattr(operation, method)(duration_seconds)
    except Exception:
        pass


def record_google_request(duration_seconds: object) -> None:
    """Record a Google request on the current context-local operation."""
    _record_active("add_google_request", duration_seconds)


def record_storage_read(duration_seconds: object) -> None:
    """Record a storage read on the current context-local operation."""
    _record_active("add_storage_read", duration_seconds)


def record_storage_write(duration_seconds: object) -> None:
    """Record a storage write on the current context-local operation."""
    _record_active("add_storage_write", duration_seconds)
