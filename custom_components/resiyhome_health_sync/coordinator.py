"""Refresh coordination and resumable history backfill for Health Sync."""

import asyncio
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, fields, replace
from datetime import UTC, date, datetime, time, timedelta
from typing import cast

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
)
from homeassistant.helpers.update_coordinator import (
    UpdateFailed as CoordinatorUpdateFailed,
)
from homeassistant.util import dt as dt_util

from .api import (
    AuthenticationError,
    GoogleHealthClient,
    UpdateFailed,
    get_data_type_operations,
)
from .capabilities import CAPABILITIES, CapabilityId
from .const import MANUAL_REFRESH_COOLDOWN, SCAN_INTERVAL
from .expanded_metrics import (
    normalize_expanded_day,
    normalize_hydration_ml,
    normalize_nutrition_energy,
)
from .models import (
    CapabilityRefreshState,
    CoordinatorSnapshot,
    DailySummary,
    ExpandedDailyMetrics,
    PairedDeviceSummary,
    SourceKind,
)
from .normalize import DataPoint, DataPointStreams, normalize_day
from .paired_devices import normalize_paired_devices
from .storage import HealthHistoryStore

_LOGGER = logging.getLogger(__name__)

_STALE_AFTER = timedelta(minutes=45)
_BACKFILL_WINDOW_DAYS = 7
_PROVIDER_HISTORY_YEARS = 20
_EXPANDED_BACKFILL_DAYS = 90
_EXPANDED_BACKFILL_WINDOW_DAYS = 14
_DURATION_RE = re.compile(r"(-?)([0-9]+)(?:\.([0-9]{1,9}))?s")

_DATA_TYPES: tuple[str, ...] = (
    "active-energy-burned",
    "active-minutes",
    "daily-heart-rate-variability",
    "daily-resting-heart-rate",
    "distance",
    "exercise",
    "heart-rate",
    "heart-rate-variability",
    "sleep",
    "steps",
)

_CORE_ACTIVITY_ROLLUP_TYPES: tuple[str, ...] = ("total-calories",)
_CORE_DATA_TYPES = frozenset((*_DATA_TYPES, *_CORE_ACTIVITY_ROLLUP_TYPES))

_EXPANDED_DIRECT_TYPES: tuple[str, ...] = (
    "daily-vo2-max",
    "daily-oxygen-saturation",
    "daily-respiratory-rate",
    "respiratory-rate-sleep-summary",
    "daily-heart-rate-zones",
)

_EXPANDED_ROLLUP_TYPES: tuple[str, ...] = (
    "active-zone-minutes",
    "floors",
    "sedentary-period",
    "time-in-heart-rate-zone",
    "calories-in-heart-rate-zone",
)

_EXPANDED_CURRENT_INTERVAL_TYPES: tuple[str, ...] = (
    "active-zone-minutes",
    "floors",
    "sedentary-period",
    "time-in-heart-rate-zone",
)

_BODY_MEASUREMENT_TYPES: tuple[str, ...] = ("weight", "body-fat", "height")
_NUTRITION_DATA_TYPES = CAPABILITIES[CapabilityId.NUTRITION].data_types

_EXPANDED_GROUP_FIELDS: Mapping[str, tuple[str, ...]] = {
    "daily-vo2-max": ("vo2_max", "vo2_estimated", "cardio_fitness_level"),
    "daily-oxygen-saturation": (
        "oxygen_average",
        "oxygen_lower_bound",
        "oxygen_upper_bound",
        "oxygen_standard_deviation",
    ),
    "daily-respiratory-rate": ("daily_respiratory_rate",),
    "respiratory-rate-sleep-summary": (
        "sleep_respiratory_rates",
        "sleep_respiratory_standard_deviation",
        "sleep_respiratory_signal_to_noise",
    ),
    "daily-heart-rate-zones": ("heart_zone_thresholds",),
    "active-zone-minutes": ("active_zone_minutes",),
    "floors": ("floors",),
    "sedentary-period": ("sedentary_minutes",),
    "time-in-heart-rate-zone": ("heart_zone_minutes",),
    "calories-in-heart-rate-zone": ("heart_zone_calories",),
    "weight": ("weight_kg",),
    "body-fat": ("body_fat_percentage",),
    "height": ("height_m",),
}

_BODY_MEASUREMENT_FIELDS: Mapping[str, tuple[str, str, str]] = {
    "weight": ("weight_kg", "latest_weight_kg", "latest_weight_at"),
    "body-fat": (
        "body_fat_percentage",
        "latest_body_fat_percentage",
        "latest_body_fat_at",
    ),
    "height": ("height_m", "latest_height_m", "latest_height_at"),
}

OPTIONAL_PROBE_DATA_TYPES: tuple[str, ...] = (
    "active-zone-minutes",
    "daily-vo2-max",
    "vo2-max",
    "run-vo2-max",
    "daily-oxygen-saturation",
    "oxygen-saturation",
    "daily-respiratory-rate",
    "respiratory-rate-sleep-summary",
    "daily-heart-rate-zones",
    "time-in-heart-rate-zone",
    "floors",
    "altitude",
    "sedentary-period",
    "weight",
    "body-fat",
    "height",
    "calories-in-heart-rate-zone",
)

_GROUP_TYPES: Mapping[str, frozenset[str]] = {
    "active_energy": frozenset({"active-energy-burned", "total-calories"}),
    "exercise_minutes": frozenset({"active-minutes"}),
    "hrv": frozenset({"daily-heart-rate-variability", "heart-rate-variability"}),
    "resting_heart_rate": frozenset({"daily-resting-heart-rate"}),
    "distance": frozenset({"distance"}),
    "workouts": frozenset({"exercise"}),
    "heart_rate": frozenset({"heart-rate"}),
    "sleep": frozenset({"sleep"}),
    "steps": frozenset({"steps"}),
}

_GROUP_FIELDS: Mapping[str, tuple[str, ...]] = {
    "active_energy": ("active_energy_kcal", "total_energy_kcal"),
    "exercise_minutes": ("exercise_minutes",),
    "hrv": ("hrv_ms",),
    "resting_heart_rate": ("resting_heart_rate",),
    "distance": ("distance_m",),
    "workouts": ("workouts",),
    "heart_rate": ("average_heart_rate", "minimum_heart_rate", "maximum_heart_rate"),
    "sleep": (
        "sleep_minutes",
        "sleep_stages",
        "sleep_period_minutes",
        "sleep_onset_minutes",
        "sleep_after_wake_minutes",
    ),
    "steps": ("steps", "fitbit_steps"),
}

_PAYLOAD_KEYS: Mapping[str, str] = {
    "active-energy-burned": "activeEnergyBurned",
    "active-minutes": "activeMinutes",
    "daily-heart-rate-variability": "dailyHeartRateVariability",
    "daily-resting-heart-rate": "dailyRestingHeartRate",
    "distance": "distance",
    "exercise": "exercise",
    "heart-rate": "heartRate",
    "heart-rate-variability": "heartRateVariability",
    "sleep": "sleep",
    "steps": "steps",
    "total-calories": "totalCalories",
}

type _NowProvider = Callable[[], datetime]


@dataclass(slots=True, frozen=True)
class _ExpandedWindowResult:
    """Aggregate-only expanded data fetched for one bounded window."""

    direct: DataPointStreams
    rollups: DataPointStreams
    successful_types: frozenset[str]


@dataclass(slots=True, frozen=True)
class _NutritionRefreshResult:
    """Normalized optional nutrition values and their independent refresh state."""

    values: tuple[float | None, float | None] | None
    state: CapabilityRefreshState


@dataclass(slots=True, frozen=True)
class _PairedDeviceRefreshResult:
    """Sanitized paired-device values and their independent refresh state."""

    values: tuple[PairedDeviceSummary, ...] | None
    state: CapabilityRefreshState


class HealthSyncCoordinator(DataUpdateCoordinator[CoordinatorSnapshot]):
    """Coordinate current health data and one bounded history window at a time."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: GoogleHealthClient,
        history: HealthHistoryStore,
        *,
        now: _NowProvider = dt_util.now,
        include_body_measurements: bool = False,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Health Sync",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.history = history
        self.data = CoordinatorSnapshot(
            backfill_cursor=history.backfill_cursor,
            expanded_backfill_cursor=history.expanded_backfill_cursor,
        )
        self._now = now
        self._include_body_measurements = include_body_measurements
        self._lock = asyncio.Lock()
        self._current_waiters = 0
        self._last_manual_refresh: datetime | None = None
        self._history_loaded = False
        self._last_sleep_diagnostic: tuple[object, ...] | None = None
        self._last_fetch_diagnostic: tuple[object, ...] | None = None
        self._last_expanded_diagnostic: tuple[object, ...] | None = None

    @property
    def data_types(self) -> tuple[str, ...]:
        """Expose the immutable supported metric set for diagnostics and tests."""
        return _DATA_TYPES

    @property
    def is_stale(self) -> bool:
        """Return whether no successful current refresh occurred in 45 minutes."""
        last_success = self.data.last_success
        return last_success is None or self._now() - last_success > _STALE_AFTER

    async def _async_update_data(self) -> CoordinatorSnapshot:
        """Run the scheduled current-day refresh."""
        try:
            return await self.async_refresh_current()
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed("Google Health authorization is unhealthy") from err
        except UpdateFailed as err:
            raise CoordinatorUpdateFailed(
                "Google Health refresh is temporarily unavailable"
            ) from err

    async def async_manual_refresh(self) -> CoordinatorSnapshot:
        """Refresh current data unless the dashboard cooldown is still active."""
        await self._async_acquire_current_lock()
        try:
            now = self._now()
            if (
                self._last_manual_refresh is not None
                and now - self._last_manual_refresh < MANUAL_REFRESH_COOLDOWN
            ):
                return self.data
            self._last_manual_refresh = now
            snapshot = await self._async_refresh_current_locked(now)
            self.async_set_updated_data(snapshot)
            return snapshot
        finally:
            self._lock.release()

    async def async_refresh_current(self) -> CoordinatorSnapshot:
        """Refresh current data without applying the dashboard cooldown."""
        await self._async_acquire_current_lock()
        try:
            return await self._async_refresh_current_locked(self._now())
        finally:
            self._lock.release()

    async def async_probe_optional_data_types(
        self,
        *,
        data_types: Sequence[str] = OPTIONAL_PROBE_DATA_TYPES,
        days: int = 7,
    ) -> dict[str, dict[str, object]]:
        """Return value-free availability counts for optional Google Health types."""
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 14:
            raise ValueError("days must be between 1 and 14")

        requested = tuple(dict.fromkeys(data_types))
        if not requested:
            raise ValueError("at least one optional data type is required")
        invalid = sorted(set(requested) - set(OPTIONAL_PROBE_DATA_TYPES))
        if invalid:
            raise ValueError("unsupported optional Google Health data type")

        await self._async_acquire_current_lock()
        try:
            end = self._now()
            start = end - timedelta(days=days)
            results: dict[str, dict[str, object]] = {}
            for data_type in requested:
                operations = get_data_type_operations(data_type)
                raw_points: Sequence[DataPoint] = ()
                all_sources_points: Sequence[DataPoint] = ()
                wearable_points: Sequence[DataPoint] = ()
                failed = False

                if "list" in operations:
                    try:
                        raw_points = await self.client.async_list_data_points(
                            data_type, start=start, end=end
                        )
                    except AuthenticationError:
                        raise
                    except UpdateFailed:
                        failed = True

                if "reconcile" in operations:
                    try:
                        all_sources_points = await self.client.async_reconcile_data_points(
                            data_type,
                            start=start,
                            end=end,
                            source_family="all-sources",
                        )
                    except AuthenticationError:
                        raise
                    except UpdateFailed:
                        failed = True
                    try:
                        wearable_points = await self.client.async_reconcile_data_points(
                            data_type,
                            start=start,
                            end=end,
                            source_family="google-wearables",
                        )
                    except AuthenticationError:
                        raise
                    except UpdateFailed:
                        failed = True

                has_probe_operation = bool(operations & {"list", "reconcile"})
                status = "error" if failed else "ok" if has_probe_operation else "requires_rollup"
                results[data_type] = {
                    "raw_count": len(raw_points),
                    "all_sources_count": len(all_sources_points),
                    "wearables_count": len(wearable_points),
                    "source_platforms": _platform_labels(
                        (*raw_points, *all_sources_points, *wearable_points)
                    ),
                    "status": status,
                }

            _log_optional_probe(results)
            return results
        finally:
            self._lock.release()

    async def async_backfill_step(self) -> CoordinatorSnapshot:
        """Import one core and one expanded window with current refresh priority."""
        await asyncio.sleep(0)
        await self._async_acquire_backfill_lock()
        try:
            await self._async_backfill_core_step_locked()
        finally:
            self._lock.release()

        await asyncio.sleep(0)
        await self._async_acquire_backfill_lock()
        try:
            await self._async_backfill_expanded_step_locked()
            return self.data
        finally:
            self._lock.release()

    async def _async_acquire_backfill_lock(self) -> None:
        while self._lock.locked() or self._current_waiters:
            await asyncio.sleep(0)
        await self._lock.acquire()

    async def _async_backfill_core_step_locked(self) -> None:
        await self._async_ensure_history_loaded()
        now = self._now()
        today = now.date()
        boundary = _years_before(today, _PROVIDER_HISTORY_YEARS)
        cursor = self.history.backfill_cursor or today
        if cursor <= boundary:
            self.data.backfill_cursor = boundary
            self.data.backfill_complete = True
            return

        window_start = max(boundary, cursor - timedelta(days=_BACKFILL_WINDOW_DAYS))
        start = _start_of_day(window_start, now)
        end = _start_of_day(cursor, now)
        try:
            streams, successful_types, raw_complete = await self._async_fetch_window(start, end)
        except AuthenticationError:
            self.data.authorization_healthy = False
            raise
        if not raw_complete or successful_types != _CORE_DATA_TYPES:
            raise UpdateFailed("Google Health history window was incomplete")

        returned_days = _returned_days(streams, window_start, cursor)
        for day in sorted(returned_days):
            previous_rows = await self.history.async_query(day, day)
            previous = previous_rows[0] if previous_rows else None
            normalized = normalize_day(
                day,
                _streams_for_day(streams.raw, day),
                _streams_for_day(streams.all_sources, day),
                _streams_for_day(streams.wearables, day),
            )
            await self.history.async_upsert(
                replace(
                    normalized,
                    expanded=(
                        previous.expanded
                        if previous is not None
                        else normalized.expanded
                    ),
                    complete=True,
                    updated_at=now,
                )
            )

        await self._async_commit_backfill_checkpoint(window_start, boundary)

    async def _async_backfill_expanded_step_locked(self) -> None:
        now = self._now()
        today = now.date()
        boundary = today - timedelta(days=_EXPANDED_BACKFILL_DAYS)
        cursor = self.history.expanded_backfill_cursor or today
        if cursor <= boundary:
            self.data.expanded_backfill_cursor = boundary
            self.data.expanded_backfill_complete = True
            return

        window_start = max(
            boundary,
            cursor - timedelta(days=_EXPANDED_BACKFILL_WINDOW_DAYS),
        )
        start = _start_of_day(window_start, now)
        end = _start_of_day(cursor, now)
        try:
            expanded = await self._async_fetch_expanded_window(start, end)
        except AuthenticationError:
            self.data.authorization_healthy = False
            raise
        expected_types = frozenset(self._expanded_data_types)
        if expanded.successful_types != expected_types:
            raise UpdateFailed("Google Health expanded history window was incomplete")

        for offset in range((cursor - window_start).days):
            day = cursor - timedelta(days=offset + 1)
            previous_rows = await self.history.async_query(day, day)
            previous = previous_rows[0] if previous_rows else None
            normalized = normalize_expanded_day(
                day,
                expanded.direct,
                expanded.rollups,
                include_weight=self._include_body_measurements,
            )
            merged = _merge_partial_expanded(
                previous.expanded if previous is not None else None,
                normalized,
                expanded.successful_types,
            )
            summary = (
                replace(previous, expanded=merged, updated_at=now)
                if previous
                else DailySummary(
                    date=day,
                    expanded=merged,
                    updated_at=now,
                )
            )
            await self._async_commit_expanded_checkpoint(summary, day, boundary)

    @property
    def _expanded_data_types(self) -> tuple[str, ...]:
        if self._include_body_measurements:
            return (
                *_EXPANDED_DIRECT_TYPES,
                *_BODY_MEASUREMENT_TYPES,
                *_EXPANDED_ROLLUP_TYPES,
            )
        return (*_EXPANDED_DIRECT_TYPES, *_EXPANDED_ROLLUP_TYPES)

    async def _async_acquire_current_lock(self) -> None:
        self._current_waiters += 1
        try:
            await self._lock.acquire()
        finally:
            self._current_waiters -= 1

    async def _async_commit_backfill_checkpoint(self, cursor: date, boundary: date) -> None:
        """Keep durable and in-memory cursor state atomic across cancellation."""
        checkpoint_task = self.hass.async_create_task(
            self.history.async_set_backfill_checkpoint(cursor)
        )
        try:
            await asyncio.shield(checkpoint_task)
        except asyncio.CancelledError:
            # The Store write can already be committed when cancellation arrives.
            # Finish the atomic operation and mirror it in memory before propagating.
            await checkpoint_task
            self._apply_backfill_checkpoint(cursor, boundary)
            raise
        self._apply_backfill_checkpoint(cursor, boundary)

    def _apply_backfill_checkpoint(self, cursor: date, boundary: date) -> None:
        self.data.authorization_healthy = True
        self.data.backfill_cursor = cursor
        self.data.backfill_complete = cursor <= boundary

    async def _async_commit_expanded_checkpoint(
        self,
        summary: DailySummary,
        cursor: date,
        boundary: date,
    ) -> None:
        """Keep each expanded day and its independent cursor atomic."""
        checkpoint_task = self.hass.async_create_task(
            self.history.async_checkpoint_expanded(summary, cursor)
        )
        try:
            await asyncio.shield(checkpoint_task)
        except asyncio.CancelledError:
            await checkpoint_task
            self._apply_expanded_checkpoint(summary, cursor, boundary)
            raise
        self._apply_expanded_checkpoint(summary, cursor, boundary)

    def _apply_expanded_checkpoint(
        self,
        summary: DailySummary,
        cursor: date,
        boundary: date,
    ) -> None:
        self.data.authorization_healthy = True
        self.data.expanded_backfill_cursor = cursor
        self.data.expanded_backfill_complete = cursor <= boundary
        self._apply_latest_body_measurements(summary)

    async def _async_refresh_current_locked(self, now: datetime) -> CoordinatorSnapshot:
        await self._async_ensure_history_loaded()
        day = now.date()
        self.data.last_attempt = now
        start = _start_of_day(day, now)
        end = _start_of_day(day + timedelta(days=1), now)

        try:
            streams, successful_types, raw_complete = await self._async_fetch_window(start, end)
            expanded = await self._async_fetch_expanded_window(
                start,
                end,
                include_current_intervals=True,
            )
            nutrition = await self._async_fetch_nutrition_current(
                day,
                start,
                end,
                now,
            )
            paired_devices = await self._async_fetch_paired_devices(now)
        except AuthenticationError:
            self.data.authorization_healthy = False
            raise

        if not successful_types.intersection(_DATA_TYPES):
            raise UpdateFailed("Google Health current refresh failed")

        previous_rows = await self.history.async_query(day, day)
        cached_current = self.data.current_day
        previous = previous_rows[0] if previous_rows else None
        if previous is None and cached_current is not None and cached_current.date == day:
            previous = cached_current
        normalized = normalize_day(
            day,
            _streams_for_day(streams.raw, day),
            _streams_for_day(streams.all_sources, day),
            _streams_for_day(streams.wearables, day),
        )
        self._log_fetch_diagnostics(day, start, end, streams, successful_types, normalized)
        if "sleep" in successful_types:
            missing_sleep = normalized.sleep_minutes is None
            missing_stages = normalized.sleep_minutes is not None and not {
                "deep",
                "light",
                "rem",
            } <= set(normalized.sleep_stages)
            if missing_sleep or missing_stages:
                reason = "missing_sleep" if missing_sleep else "missing_stage_breakdown"
                self._log_sleep_diagnostics(reason, day, start, end, streams)
        current = _merge_partial_summary(
            previous,
            normalized,
            successful_types,
            preserve_source=not raw_complete or "steps" not in successful_types,
            updated_at=now,
        )
        normalized_expanded = normalize_expanded_day(
            day,
            expanded.direct,
            expanded.rollups,
            include_weight=self._include_body_measurements,
        )
        self._log_expanded_diagnostics(day, expanded, normalized_expanded)
        current = replace(
            current,
            expanded=_merge_partial_expanded(
                previous.expanded if previous is not None else None,
                normalized_expanded,
                expanded.successful_types,
            ),
        )
        if nutrition.values is None:
            nutrition_energy_kcal = (
                previous.nutrition_energy_kcal if previous is not None else None
            )
            hydration_ml = previous.hydration_ml if previous is not None else None
        else:
            nutrition_energy_kcal, hydration_ml = nutrition.values
        current = replace(
            current,
            nutrition_energy_kcal=nutrition_energy_kcal,
            hydration_ml=hydration_ml,
        )
        await self.history.async_upsert(current)
        self.data.current_day = current
        self.data.last_success = now
        self.data.authorization_healthy = True
        self.data.backfill_cursor = self.history.backfill_cursor
        self.data.expanded_backfill_cursor = self.history.expanded_backfill_cursor
        if paired_devices.values is not None:
            self.data.paired_devices = paired_devices.values
        self.data.capability_states = {
            **self.data.capability_states,
            CapabilityId.NUTRITION: nutrition.state,
            CapabilityId.PAIRED_DEVICES: paired_devices.state,
        }
        successful_body_types = frozenset(_BODY_MEASUREMENT_TYPES).intersection(
            expanded.successful_types
        )
        await self._async_recompute_latest_body_measurements(
            day, successful_body_types
        )
        self._apply_latest_body_measurements(
            current,
            frozenset(_BODY_MEASUREMENT_TYPES).difference(successful_body_types),
        )
        return self.data

    async def _async_fetch_nutrition_current(
        self,
        day: date,
        start: datetime,
        end: datetime,
        now: datetime,
    ) -> _NutritionRefreshResult:
        """Fetch and immediately reduce the independently authorized nutrition group."""
        capability = CAPABILITIES[CapabilityId.NUTRITION]
        grant = self.client.scope_grant
        enabled = CapabilityId.NUTRITION in grant.enabled_capabilities
        scope_granted = capability.required_scopes <= grant.granted_scopes
        previous_state = self.data.capability_states.get(CapabilityId.NUTRITION)
        last_success = previous_state.last_success if previous_state is not None else None

        if not enabled or not scope_granted:
            return _NutritionRefreshResult(
                values=None,
                state=CapabilityRefreshState(
                    enabled=enabled,
                    scope_granted=scope_granted,
                    last_success=last_success,
                    error_category="authorization" if enabled else None,
                ),
            )

        streams: dict[str, Sequence[DataPoint]] = {}
        for data_type in _NUTRITION_DATA_TYPES:
            try:
                streams[data_type] = await self.client.async_reconcile_data_points(
                    data_type,
                    start=start,
                    end=end,
                    source_family="all-sources",
                )
            except AuthenticationError:
                raise
            except UpdateFailed:
                return _NutritionRefreshResult(
                    values=None,
                    state=CapabilityRefreshState(
                        enabled=True,
                        scope_granted=True,
                        last_success=last_success,
                        error_category="temporary",
                    ),
                )

        return _NutritionRefreshResult(
            values=(
                normalize_nutrition_energy(streams["nutrition-log"], day),
                normalize_hydration_ml(streams["hydration-log"], day),
            ),
            state=CapabilityRefreshState(
                enabled=True,
                scope_granted=True,
                last_success=now,
            ),
        )

    async def _async_fetch_paired_devices(self, now: datetime) -> _PairedDeviceRefreshResult:
        """Fetch paired metadata only with explicit settings authorization."""
        capability = CAPABILITIES[CapabilityId.PAIRED_DEVICES]
        grant = self.client.scope_grant
        enabled = CapabilityId.PAIRED_DEVICES in grant.enabled_capabilities
        scope_granted = capability.required_scopes <= grant.granted_scopes
        previous_state = self.data.capability_states.get(CapabilityId.PAIRED_DEVICES)
        last_success = previous_state.last_success if previous_state is not None else None

        if not enabled or not scope_granted:
            return _PairedDeviceRefreshResult(
                values=(),
                state=CapabilityRefreshState(
                    enabled=enabled,
                    scope_granted=scope_granted,
                    last_success=last_success,
                    error_category="authorization" if enabled else None,
                ),
            )

        try:
            payloads = await self.client.async_list_paired_devices()
            devices = normalize_paired_devices(payloads)
        except AuthenticationError:
            raise
        except UpdateFailed, ValueError:
            return _PairedDeviceRefreshResult(
                values=None,
                state=CapabilityRefreshState(
                    enabled=True,
                    scope_granted=True,
                    last_success=last_success,
                    error_category="temporary",
                ),
            )

        return _PairedDeviceRefreshResult(
            values=devices,
            state=CapabilityRefreshState(
                enabled=True,
                scope_granted=True,
                last_success=now,
            ),
        )

    def _log_sleep_diagnostics(
        self, reason: str, day: date, start: datetime, end: datetime, streams: _WindowStreams
    ) -> None:
        """Log redacted sleep fetch shape when current sleep details are unavailable."""
        raw_sleep = tuple(streams.raw.get("sleep", ()))
        all_sources_sleep = tuple(streams.all_sources.get("sleep", ()))
        raw_summary_count, raw_stage_count = _sleep_shape_counts(raw_sleep)
        all_sources_summary_count, all_sources_stage_count = _sleep_shape_counts(
            all_sources_sleep
        )
        signature = (
            reason,
            day,
            len(raw_sleep),
            len(all_sources_sleep),
            raw_summary_count,
            all_sources_summary_count,
            raw_stage_count,
            all_sources_stage_count,
        )
        if signature == self._last_sleep_diagnostic:
            return
        self._last_sleep_diagnostic = signature
        _LOGGER.debug(
            "Sleep diagnostics for current refresh: "
            "reason=%s day=%s window_start=%s window_end=%s raw_count=%d "
            "all_sources_count=%d raw_summary_count=%d all_sources_summary_count=%d "
            "raw_stage_count=%d all_sources_stage_count=%d",
            reason,
            day.isoformat(),
            start.isoformat(),
            end.isoformat(),
            len(raw_sleep),
            len(all_sources_sleep),
            raw_summary_count,
            all_sources_summary_count,
            raw_stage_count,
            all_sources_stage_count,
        )

    def _log_fetch_diagnostics(
        self,
        day: date,
        start: datetime,
        end: datetime,
        streams: _WindowStreams,
        successful_types: frozenset[str],
        normalized: DailySummary,
    ) -> None:
        """Log redacted current fetch shape when source-specific data is unavailable."""
        if (
            normalized.source not in {SourceKind.APPLE_FALLBACK, SourceKind.UNAVAILABLE}
            and normalized.fitbit_steps is not None
            and normalized.sleep_minutes is not None
            and normalized.workouts
        ):
            return

        raw_counts = _stream_count_diagnostics(streams.raw)
        all_sources_counts = _stream_count_diagnostics(streams.all_sources)
        wearables_counts = _stream_count_diagnostics(streams.wearables)
        raw_platforms = _platform_label_diagnostics(streams.raw)
        signature = (
            day,
            raw_counts,
            all_sources_counts,
            wearables_counts,
            raw_platforms,
            tuple(sorted(successful_types)),
            normalized.source.value,
            normalized.fitbit_steps is not None,
            normalized.sleep_minutes is not None,
            bool(normalized.workouts),
        )
        if signature == self._last_fetch_diagnostic:
            return
        self._last_fetch_diagnostic = signature
        _LOGGER.debug(
            "Fetch diagnostics for current refresh: "
            "day=%s window_start=%s window_end=%s successful_types=%s "
            "raw_counts=%s all_sources_counts=%s wearables_counts=%s raw_platforms=%s "
            "source=%s fitbit_steps=%s sleep=%s workouts=%d exercise_minutes=%s",
            day.isoformat(),
            start.isoformat(),
            end.isoformat(),
            _format_sequence(tuple(sorted(successful_types))),
            _format_sequence(raw_counts),
            _format_sequence(all_sources_counts),
            _format_sequence(wearables_counts),
            _format_sequence(raw_platforms),
            normalized.source.value,
            normalized.fitbit_steps is not None,
            normalized.sleep_minutes is not None,
            len(normalized.workouts),
            normalized.exercise_minutes is not None,
        )

    def _log_expanded_diagnostics(
        self,
        day: date,
        expanded: _ExpandedWindowResult,
        normalized: ExpandedDailyMetrics,
    ) -> None:
        """Log value-free fetch shape when current expanded metrics are missing."""
        availability = (
            f"active_zone_minutes={bool(normalized.active_zone_minutes)}",
            f"floors={normalized.floors is not None}",
            f"sedentary_minutes={normalized.sedentary_minutes is not None}",
            f"heart_zone_minutes={bool(normalized.heart_zone_minutes)}",
        )
        if all(item.endswith("=True") for item in availability):
            return
        direct_counts = tuple(
            f"{data_type}={len(expanded.direct.get(data_type, ()))}"
            for data_type in _EXPANDED_CURRENT_INTERVAL_TYPES
        )
        rollup_counts = tuple(
            f"{data_type}={len(expanded.rollups.get(data_type, ()))}"
            for data_type in _EXPANDED_ROLLUP_TYPES
        )
        signature = (day, direct_counts, rollup_counts, availability)
        if signature == self._last_expanded_diagnostic:
            return
        self._last_expanded_diagnostic = signature
        _LOGGER.debug(
            "Expanded diagnostics for current refresh: "
            "day=%s successful_types=%s direct_counts=%s rollup_counts=%s availability=%s",
            day.isoformat(),
            _format_sequence(tuple(sorted(expanded.successful_types))),
            _format_sequence(direct_counts),
            _format_sequence(rollup_counts),
            _format_sequence(availability),
        )

    async def _async_ensure_history_loaded(self) -> None:
        if self._history_loaded:
            return
        rows = await self.history.async_load()
        rows = await self.history.async_apply_body_measurement_option(
            self._include_body_measurements,
            self._now().date(),
        )
        self._history_loaded = True
        self.data.backfill_cursor = self.history.backfill_cursor
        self.data.expanded_backfill_cursor = self.history.expanded_backfill_cursor
        if self._include_body_measurements:
            missing_types = set(_BODY_MEASUREMENT_TYPES)
            for summary in reversed(rows):
                for data_type in tuple(missing_types):
                    value_field, _, _ = _BODY_MEASUREMENT_FIELDS[data_type]
                    if getattr(summary.expanded, value_field) is not None:
                        self._apply_latest_body_measurements(
                            summary, frozenset({data_type})
                        )
                        missing_types.remove(data_type)
                if not missing_types:
                    break
        else:
            for _, latest_value_field, latest_date_field in (
                _BODY_MEASUREMENT_FIELDS.values()
            ):
                setattr(self.data, latest_value_field, None)
                setattr(self.data, latest_date_field, None)
            if self.data.current_day is not None:
                self.data.current_day = replace(
                    self.data.current_day,
                    expanded=replace(
                        self.data.current_day.expanded,
                        weight_kg=None,
                        body_fat_percentage=None,
                        height_m=None,
                    ),
                )

    def _apply_latest_body_measurements(
        self,
        summary: DailySummary,
        data_types: frozenset[str] = frozenset(_BODY_MEASUREMENT_TYPES),
    ) -> None:
        """Apply independently dated latest values from one normalized day."""
        if not self._include_body_measurements:
            return
        for data_type in data_types:
            value_field, latest_value_field, latest_date_field = (
                _BODY_MEASUREMENT_FIELDS[data_type]
            )
            value = getattr(summary.expanded, value_field)
            latest_date = getattr(self.data, latest_date_field)
            if value is not None and (
                latest_date is None or summary.date >= latest_date
            ):
                setattr(self.data, latest_value_field, value)
                setattr(self.data, latest_date_field, summary.date)

    async def _async_recompute_latest_body_measurements(
        self,
        end: date,
        data_types: frozenset[str],
    ) -> None:
        """Rebuild only body streams replaced by a successful current fetch."""
        if not self._include_body_measurements or not data_types:
            return
        missing_types = set(data_types)
        for data_type in missing_types:
            _, latest_value_field, latest_date_field = _BODY_MEASUREMENT_FIELDS[
                data_type
            ]
            setattr(self.data, latest_value_field, None)
            setattr(self.data, latest_date_field, None)
        rows = await self.history.async_query(date.min, end)
        for summary in reversed(rows):
            for data_type in tuple(missing_types):
                value_field, _, _ = _BODY_MEASUREMENT_FIELDS[data_type]
                if getattr(summary.expanded, value_field) is not None:
                    self._apply_latest_body_measurements(
                        summary, frozenset({data_type})
                    )
                    missing_types.remove(data_type)
            if not missing_types:
                return

    async def _async_fetch_window(
        self, start: datetime, end: datetime
    ) -> tuple[_WindowStreams, frozenset[str], bool]:
        raw: dict[str, Sequence[DataPoint]] = {}
        all_sources: dict[str, Sequence[DataPoint]] = {}
        wearables: dict[str, Sequence[DataPoint]] = {}
        successful_types: set[str] = set()
        raw_complete = True

        for data_type in _DATA_TYPES:
            raw_points: Sequence[DataPoint] = ()
            try:
                raw_points = await self.client.async_list_data_points(
                    data_type, start=start, end=end
                )
            except AuthenticationError:
                raise
            except UpdateFailed:
                raw_complete = False
            raw[data_type] = raw_points

            try:
                canonical = await self.client.async_reconcile_data_points(
                    data_type,
                    start=start,
                    end=end,
                    source_family="all-sources",
                )
                wearable_points: Sequence[DataPoint] = ()
                if data_type == "steps":
                    wearable_points = await self.client.async_reconcile_data_points(
                        data_type,
                        start=start,
                        end=end,
                        source_family="google-wearables",
                    )
            except AuthenticationError:
                raise
            except UpdateFailed:
                continue

            all_sources[data_type] = canonical
            wearables[data_type] = wearable_points
            successful_types.add(data_type)

        for data_type in _CORE_ACTIVITY_ROLLUP_TYPES:
            try:
                all_sources[data_type] = await self.client.async_daily_rollup_data_points(
                    data_type,
                    start=start,
                    end=end,
                    source_family="all-sources",
                )
            except AuthenticationError:
                raise
            except UpdateFailed:
                continue
            successful_types.add(data_type)

        return (
            _WindowStreams(raw=raw, all_sources=all_sources, wearables=wearables),
            frozenset(successful_types),
            raw_complete,
        )

    async def _async_fetch_expanded_window(
        self,
        start: datetime,
        end: datetime,
        *,
        include_current_intervals: bool = False,
    ) -> _ExpandedWindowResult:
        direct: dict[str, Sequence[DataPoint]] = {}
        rollups: dict[str, Sequence[DataPoint]] = {}
        successful_types: set[str] = set()

        direct_types: tuple[str, ...] = (
            (*_EXPANDED_DIRECT_TYPES, *_BODY_MEASUREMENT_TYPES)
            if self._include_body_measurements
            else _EXPANDED_DIRECT_TYPES
        )
        if include_current_intervals:
            direct_types = (*direct_types, *_EXPANDED_CURRENT_INTERVAL_TYPES)
        for data_type in direct_types:
            try:
                direct[data_type] = await self.client.async_reconcile_data_points(
                    data_type,
                    start=start,
                    end=end,
                    source_family="all-sources",
                )
            except AuthenticationError:
                raise
            except UpdateFailed:
                continue
            successful_types.add(data_type)

        for data_type in _EXPANDED_ROLLUP_TYPES:
            try:
                rollups[data_type] = await self.client.async_daily_rollup_data_points(
                    data_type,
                    start=start,
                    end=end,
                    source_family="all-sources",
                )
            except AuthenticationError:
                raise
            except UpdateFailed:
                continue
            successful_types.add(data_type)

        return _ExpandedWindowResult(
            direct=direct,
            rollups=rollups,
            successful_types=frozenset(successful_types),
        )


class _WindowStreams:
    """Fetched stream families for one API time window."""

    __slots__ = ("all_sources", "raw", "wearables")

    def __init__(
        self,
        *,
        raw: DataPointStreams,
        all_sources: DataPointStreams,
        wearables: DataPointStreams,
    ) -> None:
        self.raw = raw
        self.all_sources = all_sources
        self.wearables = wearables


def _merge_partial_summary(
    previous: DailySummary | None,
    normalized: DailySummary,
    successful_types: frozenset[str],
    *,
    preserve_source: bool,
    updated_at: datetime,
) -> DailySummary:
    """Apply successful groups while retaining failed groups from prior history."""
    values = {
        field.name: getattr(normalized, field.name)
        for field in fields(DailySummary)
        if field.name != "date"
    }
    if previous is not None:
        for group, data_types in _GROUP_TYPES.items():
            if not data_types <= successful_types:
                for field_name in _GROUP_FIELDS[group]:
                    values[field_name] = getattr(previous, field_name)
    if preserve_source:
        values["source"] = previous.source if previous is not None else SourceKind.UNAVAILABLE
    values["complete"] = False
    values["updated_at"] = updated_at
    return DailySummary(date=normalized.date, **values)


def _merge_partial_expanded(
    previous: ExpandedDailyMetrics | None,
    normalized: ExpandedDailyMetrics,
    successful_types: frozenset[str],
) -> ExpandedDailyMetrics:
    """Apply successful expanded groups while retaining failed prior groups."""
    values = {field.name: getattr(normalized, field.name) for field in fields(ExpandedDailyMetrics)}
    if previous is not None:
        for data_type, field_names in _EXPANDED_GROUP_FIELDS.items():
            if data_type not in successful_types:
                for field_name in field_names:
                    values[field_name] = getattr(previous, field_name)
    return ExpandedDailyMetrics(**values)


def _returned_days(streams: _WindowStreams, start: date, end: date) -> set[date]:
    days: set[date] = set()
    for family in (streams.all_sources, streams.wearables):
        for data_type, points in family.items():
            for point in points:
                point_day = _point_day(data_type, point)
                if point_day is not None and start <= point_day < end:
                    days.add(point_day)
    return days


def _streams_for_day(streams: DataPointStreams, day: date) -> dict[str, Sequence[DataPoint]]:
    result: dict[str, Sequence[DataPoint]] = {}
    for data_type, points in streams.items():
        dated = tuple(point for point in points if _point_day(data_type, point) == day)
        if dated:
            result[data_type] = dated
        elif (
            data_type != "total-calories"
            and points
            and all(_point_day(data_type, point) is None for point in points)
        ):
            # Raw list records can contain platform attribution without a time field.
            result[data_type] = points
    return result


def _stream_count_diagnostics(streams: DataPointStreams) -> tuple[str, ...]:
    """Return per-type point counts without health values."""
    return tuple(f"{data_type}={len(streams.get(data_type, ()))}" for data_type in _DATA_TYPES)


def _platform_label_diagnostics(streams: DataPointStreams) -> tuple[str, ...]:
    """Return recognized raw source platform labels without source identifiers."""
    labels: list[str] = []
    for data_type in _DATA_TYPES:
        platforms: set[str] = set()
        for point in streams.get(data_type, ()):
            data_source = point.get("dataSource")
            if not isinstance(data_source, Mapping):
                continue
            platform = data_source.get("platform")
            if isinstance(platform, str) and platform.strip():
                platforms.add(platform.strip().upper())
        if platforms:
            labels.append(f"{data_type}={','.join(sorted(platforms))}")
    return tuple(labels)


def _platform_labels(points: Sequence[DataPoint]) -> tuple[str, ...]:
    """Return normalized source platform names without source identifiers."""
    platforms: set[str] = set()
    for point in points:
        data_source = point.get("dataSource")
        if not isinstance(data_source, Mapping):
            continue
        platform = data_source.get("platform")
        if isinstance(platform, str) and platform.strip():
            platforms.add(platform.strip().upper())
    return tuple(sorted(platforms))


def _log_optional_probe(results: Mapping[str, Mapping[str, object]]) -> None:
    """Log only aggregate optional-type availability metadata."""
    summaries = []
    for data_type, result in results.items():
        platforms = result["source_platforms"]
        platform_label = (
            ",".join(cast(tuple[str, ...], platforms)) if platforms else "none"
        )
        summaries.append(
            f"{data_type} status={result['status']} raw={result['raw_count']} "
            f"all_sources={result['all_sources_count']} "
            f"wearables={result['wearables_count']} platforms={platform_label}"
        )
    _LOGGER.info("Optional data type availability probe: %s", "; ".join(summaries))


def _format_sequence(values: tuple[str, ...]) -> str:
    """Format diagnostic tuples compactly without Python string quotes."""
    return f"({', '.join(values)})"


def _sleep_shape_counts(points: Sequence[DataPoint]) -> tuple[int, int]:
    """Count available sleep summaries and stages without retaining their values."""
    summary_count = 0
    stage_count = 0
    for point in points:
        payload = point.get("sleep")
        summary = payload.get("summary") if isinstance(payload, Mapping) else None
        if not isinstance(summary, Mapping):
            continue
        summary_count += 1
        stages = summary.get("stagesSummary")
        if isinstance(stages, Sequence) and not isinstance(
            stages, (str, bytes, bytearray)
        ):
            stage_count += len(stages)
    return summary_count, stage_count


def _point_day(data_type: str, point: DataPoint) -> date | None:
    payload = point.get(_PAYLOAD_KEYS[data_type])
    if not isinstance(payload, Mapping):
        return None
    if data_type == "total-calories":
        return _daily_rollup_date(point, "civilStartTime")
    if data_type in {"daily-heart-rate-variability", "daily-resting-heart-rate"}:
        daily = payload.get("date")
        if not isinstance(daily, Mapping):
            return None
        year, month, day = daily.get("year"), daily.get("month"), daily.get("day")
        if not all(type(value) is int for value in (year, month, day)):
            return None
        try:
            return date(cast(int, year), cast(int, month), cast(int, day))
        except ValueError:
            return None

    if data_type in {"heart-rate", "heart-rate-variability"}:
        return _physical_date(payload.get("sampleTime"), "physicalTime", "utcOffset")

    interval = payload.get("interval")
    if data_type == "sleep":
        return _physical_date(interval, "endTime", "endUtcOffset")
    if data_type == "exercise":
        civil_day = _civil_date(interval, "civilStartTime")
        if civil_day is not None:
            return civil_day
    return _physical_date(interval, "startTime", "startUtcOffset")


def _daily_rollup_date(value: object, time_key: str) -> date | None:
    if not isinstance(value, Mapping):
        return None
    civil = value.get(time_key)
    if not isinstance(civil, Mapping):
        return None
    if "time" in civil:
        return _civil_date(value, time_key)

    civil_date = civil.get("date")
    if not isinstance(civil_date, Mapping):
        return None
    year = civil_date.get("year")
    month = civil_date.get("month")
    day = civil_date.get("day")
    if not all(type(component) is int for component in (year, month, day)):
        return None
    try:
        return date(cast(int, year), cast(int, month), cast(int, day))
    except ValueError:
        return None


def _civil_date(value: object, time_key: str) -> date | None:
    if not isinstance(value, Mapping):
        return None
    civil = value.get(time_key)
    if not isinstance(civil, Mapping):
        return None

    civil_date = civil.get("date")
    civil_time = civil.get("time")
    if not isinstance(civil_date, Mapping) or not isinstance(civil_time, Mapping):
        return None

    year = civil_date.get("year")
    month = civil_date.get("month")
    day = civil_date.get("day")
    hours = civil_time.get("hours", 0)
    minutes = civil_time.get("minutes", 0)
    seconds = civil_time.get("seconds", 0)
    nanos = civil_time.get("nanos", 0)
    values = (year, month, day, hours, minutes, seconds, nanos)
    if not all(type(component) is int for component in values):
        return None
    if not 0 <= cast(int, nanos) <= 999_999_999:
        return None

    try:
        parsed_date = date(cast(int, year), cast(int, month), cast(int, day))
        time(
            cast(int, hours),
            cast(int, minutes),
            cast(int, seconds),
            cast(int, nanos) // 1000,
        )
    except ValueError:
        return None
    return parsed_date


def _physical_date(value: object, time_key: str, offset_key: str) -> date | None:
    if not isinstance(value, Mapping):
        return None
    timestamp = value.get(time_key)
    offset = value.get(offset_key)
    if not isinstance(timestamp, str) or not isinstance(offset, str):
        return None
    try:
        physical = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if physical.tzinfo is None or physical.utcoffset() is None:
        return None
    offset_seconds = _duration_seconds(offset)
    if offset_seconds is None or abs(offset_seconds) > 18 * 60 * 60:
        return None
    return (physical.astimezone(UTC) + timedelta(seconds=offset_seconds)).date()


def _duration_seconds(value: str) -> float | None:
    match = _DURATION_RE.fullmatch(value)
    if match is None:
        return None
    sign, whole, fraction = match.groups()
    seconds = float(f"{whole}.{fraction or '0'}")
    return -seconds if sign else seconds


def _start_of_day(day: date, reference: datetime) -> datetime:
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("coordinator clock must return a timezone-aware datetime")
    return datetime.combine(day, time.min, tzinfo=reference.tzinfo)


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:
        return day.replace(year=day.year - years, day=28)
