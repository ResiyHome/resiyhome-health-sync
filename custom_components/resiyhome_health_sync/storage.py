"""Versioned persistence for normalized Health Sync daily summaries."""

import asyncio
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, datetime
from math import isfinite
from pathlib import Path
from typing import cast

from homeassistant.core import CoreState, Event, HomeAssistant
from homeassistant.helpers.storage import Store

from .models import DailySummary, ExpandedDailyMetrics, SourceKind, WorkoutSummary

_LOGGER = logging.getLogger(__name__)
_STORE_VERSION = 1
_STORE_MINOR_VERSION = 1
_SCHEMA_VERSION = 3
_SAVE_DELAY_SECONDS = 1.0
_STORE_DOCUMENT_FIELDS = frozenset({"version", "minor_version", "key", "data"})
_DOCUMENT_FIELDS = frozenset(
    {
        "schema_version",
        "summaries",
        "backfill_cursor",
        "expanded_backfill_cursor",
        "body_measurements_enabled",
    }
)
_SUMMARY_FIELDS = frozenset(
    {
        "date",
        "steps",
        "fitbit_steps",
        "distance_m",
        "active_energy_kcal",
        "total_energy_kcal",
        "nutrition_energy_kcal",
        "hydration_ml",
        "exercise_minutes",
        "sleep_minutes",
        "sleep_period_minutes",
        "sleep_onset_minutes",
        "sleep_after_wake_minutes",
        "sleep_stages",
        "resting_heart_rate",
        "average_heart_rate",
        "minimum_heart_rate",
        "maximum_heart_rate",
        "hrv_ms",
        "workouts",
        "expanded",
        "source",
        "complete",
        "updated_at",
    }
)
_SUMMARY_ADDITIVE_FIELDS = frozenset(
    {
        "total_energy_kcal",
        "nutrition_energy_kcal",
        "hydration_ml",
        "sleep_period_minutes",
        "sleep_onset_minutes",
        "sleep_after_wake_minutes",
    }
)
_EXPANDED_FIELDS = frozenset(
    {
        "active_zone_minutes",
        "vo2_max",
        "vo2_estimated",
        "cardio_fitness_level",
        "oxygen_average",
        "oxygen_lower_bound",
        "oxygen_upper_bound",
        "oxygen_standard_deviation",
        "daily_respiratory_rate",
        "sleep_respiratory_rates",
        "sleep_respiratory_standard_deviation",
        "sleep_respiratory_signal_to_noise",
        "floors",
        "sedentary_minutes",
        "heart_zone_minutes",
        "heart_zone_thresholds",
        "heart_zone_calories",
        "weight_kg",
        "body_fat_percentage",
        "height_m",
    }
)
_EXPANDED_ADDITIVE_FIELDS = frozenset({"body_fat_percentage", "height_m"})
_WORKOUT_FIELDS = frozenset(
    {"activity_type", "duration_minutes", "start", "end", "active_energy_kcal"}
)
_SLEEP_STAGE_TYPES = frozenset({"awake", "light", "deep", "rem", "asleep", "restless"})
_ACTIVE_ZONE_TYPES = frozenset({"fat_burn", "cardio", "peak"})
_HEART_ZONE_TYPES = frozenset({"light", "moderate", "vigorous", "peak"})
_SLEEP_RESPIRATORY_TYPES = frozenset({"deep", "light", "rem", "full"})
_CARDIO_FITNESS_LEVELS = frozenset(
    {"POOR", "FAIR", "AVERAGE", "GOOD", "VERY_GOOD", "EXCELLENT"}
)


class HistoryStoreError(ValueError):
    """Raised when persisted history cannot be safely interpreted."""


class _HistoryStore(Store[dict[str, object]]):
    """Store writer with a fail-closed, direct history reader."""

    async def async_save_confirmed(self, data: dict[str, object]) -> None:
        """Save through Store and confirm the requested document reached disk."""
        await self.async_save(data)
        if self._data is not None:
            await self._async_handle_write_data()
        persisted = await self.hass.async_add_executor_job(
            self._read_persisted_store_payload
        )
        if persisted != data:
            raise HistoryStoreError("health history write was not persisted")

    async def async_drain_pending_write_confirmed(self) -> None:
        """Drain one pending write, restoring its callbacks unless disk confirms it."""
        async with self._write_lock:
            if self._data is None:
                return
            pending = self._data
            if "data_func" in pending:
                pending_payload = cast(dict[str, object], pending["data_func"]())
            else:
                pending_payload = cast(dict[str, object], pending["data"])
            prepared = {
                "version": self.version,
                "minor_version": self.minor_version,
                "key": self.key,
                "data": pending_payload,
            }

            self._manager.async_invalidate(self.key)
            self._async_cleanup_delay_listener()
            self._async_cleanup_final_write_listener()
            self._data = None
            try:
                await self._async_write_data(prepared)
                persisted = await self.hass.async_add_executor_job(
                    self._read_persisted_store_payload
                )
                if persisted != pending_payload:
                    raise HistoryStoreError("health history write was not persisted")
            except BaseException:
                if self._data is None:
                    # Restore before releasing the write lock so shutdown cannot
                    # observe an empty queue after an unconfirmed callback write.
                    snapshot = pending_payload
                    self.async_delay_save(
                        lambda: snapshot,
                        _SAVE_DELAY_SECONDS,
                    )
                raise

    async def _async_callback_delayed_write(self) -> None:
        """Run a delayed write through the confirmed history path."""
        if self.hass.state is CoreState.stopping:
            self._async_ensure_final_write_listener()
            return
        await self._async_retry_pending_write()

    async def _async_callback_final_write(self, _event: Event) -> None:
        """Run the final write through the confirmed history path."""
        self._unsub_final_write_listener = None
        await self._async_retry_pending_write()

    async def _async_retry_pending_write(self) -> None:
        """Retain and log a delayed or final write that disk did not confirm."""
        try:
            await self.async_drain_pending_write_confirmed()
        except Exception as err:
            _LOGGER.error("Unable to persist pending history for %s: %s", self.key, err)

    async def async_load_validated_history(
        self,
    ) -> tuple[
        dict[date, DailySummary],
        date | None,
        date | None,
        bool,
        dict[str, object] | None,
        bool,
    ]:
        """Read and validate the Store wrapper exactly once without Store reset behavior."""
        async with self._write_lock:
            try:
                summaries, cursor, expanded_cursor, body_enabled, migration = (
                    await self.hass.async_add_executor_job(self._read_validated_store_document)
                )
            except Exception:
                # Prevent a queued callback from consuming stale data between a
                # failed direct read and the wrapper's fail-closed state change.
                self.async_discard_pending_write()
                raise
            return (
                summaries,
                cursor,
                expanded_cursor,
                body_enabled,
                migration,
                self._data is not None,
            )

    def _read_validated_store_document(
        self,
    ) -> tuple[
        dict[date, DailySummary],
        date | None,
        date | None,
        bool,
        dict[str, object] | None,
    ]:
        """Synchronously decode the outer Store wrapper and its normalized data."""
        outer_document = _read_store_document(Path(self.path))
        if outer_document is None:
            return {}, None, None, False, None
        stored_version, payload = _deserialize_store_wrapper(
            outer_document, self.key, self.version, self.minor_version
        )
        summaries, cursor, expanded_cursor, body_enabled, inner_migration = (
            _deserialize_document(payload)
        )
        if stored_version != self.version:
            return summaries, cursor, expanded_cursor, body_enabled, _serialize_document(
                summaries, cursor, expanded_cursor, body_enabled
            )
        return summaries, cursor, expanded_cursor, body_enabled, inner_migration

    def _read_persisted_store_payload(self) -> Mapping[str, object] | None:
        """Read only the validated inner document for write confirmation."""
        outer_document = _read_store_document(Path(self.path))
        if outer_document is None:
            return None
        _stored_version, payload = _deserialize_store_wrapper(
            outer_document, self.key, self.version, self.minor_version
        )
        return payload

    def async_discard_pending_write(self) -> None:
        """Cancel every queued write without letting stale data survive a failed load."""
        self._async_cleanup_delay_listener()
        self._async_cleanup_final_write_listener()
        self._data = None
        self._next_write_time = 0.0


class HealthHistoryStore:
    """Persist immutable normalized summaries for one Home Assistant config entry."""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError("entry_id must be a non-empty string")
        self.key = f"resiyhome_health_sync.{entry_id}.history"
        self._hass = hass
        self._store = _HistoryStore(
            hass,
            version=_STORE_VERSION,
            minor_version=_STORE_MINOR_VERSION,
            key=self.key,
        )
        self._summaries: dict[date, DailySummary] = {}
        self._backfill_cursor: date | None = None
        self._expanded_backfill_cursor: date | None = None
        self._body_measurements_enabled = False
        self._loaded = False
        self._load_failed = False
        self._closed = False
        self._lock = asyncio.Lock()

    @property
    def backfill_cursor(self) -> date | None:
        """Return the last durably persisted backfill checkpoint."""
        return self._backfill_cursor

    @property
    def expanded_backfill_cursor(self) -> date | None:
        """Return the last durably persisted expanded-history checkpoint."""
        return self._expanded_backfill_cursor

    @property
    def body_measurements_enabled(self) -> bool:
        """Return the option state used for the durable expanded-history cursor."""
        return self._body_measurements_enabled

    async def async_load(self) -> list[DailySummary]:
        """Load the validated history document without replacing corrupt content."""
        async with self._lock:
            self._raise_if_closed()
            return await self._async_load_locked()

    async def async_shutdown(self) -> None:
        """Close this instance and drain its pending delayed write."""
        async with self._lock:
            if self._closed:
                return
            await self._store.async_drain_pending_write_confirmed()
            self._closed = True

    async def async_load_payload(self, payload: object) -> dict[date, DailySummary]:
        """Decode a stored payload for migration and persistence contract tests."""
        summaries, _cursor, _expanded_cursor, _body_enabled, _migration = (
            _deserialize_document(payload)
        )
        return summaries

    async def async_upsert(self, summary: DailySummary) -> None:
        """Replace one date and batch the normal history write."""
        async with self._lock:
            await self._async_ensure_loaded_locked()
            _serialize_summary(summary)
            self._summaries[summary.date] = summary
            snapshot = self._serialize_document_for(
                self._backfill_cursor, self._expanded_backfill_cursor
            )
            self._store.async_delay_save(lambda: snapshot, _SAVE_DELAY_SECONDS)

    async def async_query(self, start: date, end: date) -> list[DailySummary]:
        """Return summaries in chronological order, including both date bounds."""
        if type(start) is not date or type(end) is not date:
            raise TypeError("start and end must be date values")
        if start > end:
            raise ValueError("start must be on or before end")
        async with self._lock:
            await self._async_ensure_loaded_locked()
            return [
                summary for day, summary in sorted(self._summaries.items()) if start <= day <= end
            ]

    async def async_set_backfill_checkpoint(self, cursor: date | None) -> None:
        """Durably save a completed backfill checkpoint boundary."""
        if cursor is not None and type(cursor) is not date:
            raise TypeError("backfill cursor must be a date or None")
        async with self._lock:
            await self._async_ensure_loaded_locked()
            document = self._serialize_document_for(cursor, self._expanded_backfill_cursor)
            await self._store.async_save(document)
            self._backfill_cursor = cursor

    async def async_checkpoint_expanded(
        self, summary: DailySummary, next_cursor: date | None
    ) -> None:
        """Durably save one expanded summary and its independent checkpoint together."""
        if next_cursor is not None and type(next_cursor) is not date:
            raise TypeError("expanded backfill cursor must be a date or None")
        _serialize_summary(summary)
        async with self._lock:
            await self._async_ensure_loaded_locked()
            updated_summaries = self._summaries | {summary.date: summary}
            document = _serialize_document(
                updated_summaries,
                self._backfill_cursor,
                next_cursor,
                self._body_measurements_enabled,
            )
            await self._store.async_save(document)
            self._summaries = updated_summaries
            self._expanded_backfill_cursor = next_cursor

    async def async_apply_body_measurement_option(
        self, enabled: bool, today: date
    ) -> list[DailySummary]:
        """Durably reset body backfill or scrub body data on an option transition."""
        if type(enabled) is not bool:
            raise TypeError("enabled must be a boolean")
        _require_date(today, "today")
        async with self._lock:
            await self._async_ensure_loaded_locked()
            has_body_measurements = any(
                summary.expanded.weight_kg is not None
                or summary.expanded.body_fat_percentage is not None
                or summary.expanded.height_m is not None
                for summary in self._summaries.values()
            )
            if enabled == self._body_measurements_enabled and (
                enabled or not has_body_measurements
            ):
                return self._ordered_summaries()

            updated_summaries = self._summaries
            expanded_cursor = self._expanded_backfill_cursor
            if enabled:
                expanded_cursor = today
            else:
                updated_summaries = {
                    day: replace(
                        summary,
                        expanded=replace(
                            summary.expanded,
                            weight_kg=None,
                            body_fat_percentage=None,
                            height_m=None,
                        ),
                    )
                    for day, summary in self._summaries.items()
                }
            document = _serialize_document(
                updated_summaries,
                self._backfill_cursor,
                expanded_cursor,
                enabled,
            )
            try:
                await self._store.async_save_confirmed(document)
            except Exception as err:
                raise HistoryStoreError(
                    "unable to persist body measurement option"
                ) from err
            self._summaries = updated_summaries
            self._expanded_backfill_cursor = expanded_cursor
            self._body_measurements_enabled = enabled
            return self._ordered_summaries()

    async def _async_load_locked(self) -> list[DailySummary]:
        """Load and replace state while the wrapper lock is held."""
        try:
            (
                summaries,
                cursor,
                expanded_cursor,
                body_enabled,
                inner_migration,
                has_pending_write,
            ) = await self._store.async_load_validated_history()
            if self._loaded and has_pending_write:
                self._load_failed = False
                return self._ordered_summaries()
            if inner_migration is not None:
                await self._store.async_save(inner_migration)
        except HistoryStoreError:
            self._invalidate_after_load_failure()
            raise
        except Exception as err:
            self._invalidate_after_load_failure()
            raise HistoryStoreError("unable to load health history") from err

        self._summaries = summaries
        self._backfill_cursor = cursor
        self._expanded_backfill_cursor = expanded_cursor
        self._body_measurements_enabled = body_enabled
        self._loaded = True
        self._load_failed = False
        return self._ordered_summaries()

    async def _async_ensure_loaded_locked(self) -> None:
        self._raise_if_closed()
        if self._load_failed:
            raise HistoryStoreError(
                "history is unavailable after a failed load; reload after repair"
            )
        if not self._loaded:
            await self._async_load_locked()

    def _raise_if_closed(self) -> None:
        if self._closed:
            raise HistoryStoreError("history store is shut down")

    def _invalidate_after_load_failure(self) -> None:
        self._store.async_discard_pending_write()
        self._summaries = {}
        self._backfill_cursor = None
        self._expanded_backfill_cursor = None
        self._body_measurements_enabled = False
        self._loaded = False
        self._load_failed = True

    def _ordered_summaries(self) -> list[DailySummary]:
        return [summary for _day, summary in sorted(self._summaries.items())]

    def _serialize_document(self) -> dict[str, object]:
        return self._serialize_document_for(
            self._backfill_cursor, self._expanded_backfill_cursor
        )

    def _serialize_document_for(
        self, cursor: date | None, expanded_cursor: date | None
    ) -> dict[str, object]:
        return _serialize_document(
            self._summaries,
            cursor,
            expanded_cursor,
            self._body_measurements_enabled,
        )


def _serialize_document(
    summaries: Mapping[date, DailySummary],
    cursor: date | None,
    expanded_cursor: date | None,
    body_measurements_enabled: bool,
) -> dict[str, object]:
    return {
        "schema_version": _SCHEMA_VERSION,
        "summaries": {
            day.isoformat(): _serialize_summary(summary)
            for day, summary in sorted(summaries.items())
        },
        "backfill_cursor": cursor.isoformat() if cursor is not None else None,
        "expanded_backfill_cursor": (
            expanded_cursor.isoformat() if expanded_cursor is not None else None
        ),
        "body_measurements_enabled": body_measurements_enabled,
    }


def _deserialize_document(
    payload: object,
) -> tuple[
    dict[date, DailySummary], date | None, date | None, bool, dict[str, object] | None
]:
    document, migrated = _migrate_document(payload)
    _require_exact_fields(document, _DOCUMENT_FIELDS, "history document")
    if document["schema_version"] != _SCHEMA_VERSION:
        raise HistoryStoreError("unsupported history schema version")

    serialized_summaries = _require_mapping(document["summaries"], "summaries")
    summaries: dict[date, DailySummary] = {}
    for serialized_day, serialized_summary in serialized_summaries.items():
        day = _parse_date(serialized_day, "summary date key")
        if day in summaries:
            raise HistoryStoreError(f"duplicate summary date: {serialized_day}")
        summary = _deserialize_summary(serialized_summary, day)
        summaries[day] = summary
    cursor = _parse_optional_date(document["backfill_cursor"], "backfill_cursor")
    expanded_cursor = _parse_optional_date(
        document["expanded_backfill_cursor"], "expanded_backfill_cursor"
    )
    body_enabled = document["body_measurements_enabled"]
    if type(body_enabled) is not bool:
        raise HistoryStoreError("body_measurements_enabled must be a boolean")
    return (
        summaries,
        cursor,
        expanded_cursor,
        body_enabled,
        _serialize_document(summaries, cursor, expanded_cursor, body_enabled)
        if migrated
        else None,
    )


def _read_store_document(path: Path) -> object | None:
    """Parse the persisted Store wrapper exactly once without recovery side effects."""
    try:
        with path.open(encoding="utf-8") as store_file:
            return cast(
                object,
                json.load(
                    store_file,
                    object_pairs_hook=_reject_duplicate_json_keys,
                    parse_constant=_reject_non_finite_json_constant,
                ),
            )
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as err:
        message = "duplicate JSON keys in health history Store content"
        if not isinstance(err, ValueError) or not str(err).startswith("duplicate JSON key"):
            message = "corrupt health history Store content"
        raise HistoryStoreError(message) from err


def _deserialize_store_wrapper(
    payload: object, expected_key: str, expected_version: int, expected_minor_version: int
) -> tuple[int, Mapping[str, object]]:
    """Validate the complete Home Assistant Store wrapper before reading its data."""
    document = _require_mapping(payload, "history Store document")
    actual_fields = set(document)
    missing_fields = _STORE_DOCUMENT_FIELDS - actual_fields
    unexpected_fields = actual_fields - _STORE_DOCUMENT_FIELDS
    if unexpected_fields or missing_fields - {"minor_version"}:
        raise HistoryStoreError(
            "history Store document has invalid fields; "
            f"missing={sorted(missing_fields)}, unexpected={sorted(unexpected_fields)}"
        )
    stored_version = document["version"]
    if type(stored_version) is not int:
        raise HistoryStoreError("history Store version must be an integer")
    if stored_version not in {0, expected_version}:
        raise HistoryStoreError(f"unsupported history Store version: {stored_version}")
    stored_minor_version = document.get("minor_version", expected_minor_version)
    if type(stored_minor_version) is not int:
        raise HistoryStoreError("history Store minor_version must be an integer")
    if stored_minor_version != expected_minor_version:
        raise HistoryStoreError(f"unsupported history Store minor version: {stored_minor_version}")
    stored_key = document["key"]
    if not isinstance(stored_key, str) or stored_key != expected_key:
        raise HistoryStoreError("history Store key does not match this config entry")
    return stored_version, _require_mapping(document["data"], "history Store data")


def _reject_non_finite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate JSON key: {key}")
        document[key] = value
    return document


def _migrate_document(payload: object) -> tuple[dict[str, object], bool]:
    document = _require_mapping(payload, "history document")
    if "schema_version" not in document:
        return _migrate_v2_document(
            _migrate_v1_document(_migrate_v0_document(document))
        ), True
    schema_version = document["schema_version"]
    if type(schema_version) is not int:
        raise HistoryStoreError("schema_version must be an integer")
    if schema_version == 1:
        return _migrate_v2_document(_migrate_v1_document(document)), True
    if schema_version == 2:
        return _migrate_v2_document(document), True
    if schema_version == _SCHEMA_VERSION:
        return dict(document), False
    raise HistoryStoreError(f"unsupported history schema version: {schema_version}")


def _migrate_v0_document(legacy: Mapping[str, object]) -> dict[str, object]:
    """Convert the single pre-schema list-or-mapping shape to schema v1."""
    _require_exact_fields(legacy, frozenset({"summaries", "backfill_cursor"}), "v0 history")
    legacy_summaries = legacy["summaries"]
    summaries: dict[str, object] = {}
    if isinstance(legacy_summaries, Mapping):
        for serialized_day, serialized_summary in legacy_summaries.items():
            if not isinstance(serialized_day, str):
                raise HistoryStoreError("v0 summary date keys must be strings")
            if serialized_day in summaries:
                raise HistoryStoreError(f"duplicate summary date: {serialized_day}")
            summaries[serialized_day] = serialized_summary
    elif isinstance(legacy_summaries, Sequence) and not isinstance(
        legacy_summaries, (str, bytes, bytearray)
    ):
        for serialized_summary in legacy_summaries:
            summary = _require_mapping(serialized_summary, "v0 summary")
            serialized_day = summary.get("date")
            if not isinstance(serialized_day, str):
                raise HistoryStoreError("v0 summary date must be a string")
            if serialized_day in summaries:
                raise HistoryStoreError(f"duplicate summary date: {serialized_day}")
            summaries[serialized_day] = dict(summary)
    else:
        raise HistoryStoreError("v0 summaries must be a list or mapping")
    return {
        "schema_version": 1,
        "summaries": summaries,
        "backfill_cursor": legacy["backfill_cursor"],
    }


def _migrate_v1_document(document: Mapping[str, object]) -> dict[str, object]:
    """Add empty expanded payloads and an independent cursor to valid v1 shape."""
    _require_exact_fields(
        document, frozenset({"schema_version", "summaries", "backfill_cursor"}), "v1 history"
    )
    migrated_summaries: dict[str, object] = {}
    for day, summary in _require_mapping(document["summaries"], "summaries").items():
        v1_summary = _require_mapping(summary, "v1 summary")
        _require_exact_fields(
            v1_summary,
            _SUMMARY_FIELDS - _SUMMARY_ADDITIVE_FIELDS - {"expanded"},
            "v1 summary",
        )
        migrated_summaries[str(day)] = {
            **v1_summary,
            "expanded": _empty_v2_expanded_payload(),
        }
    return {
        "schema_version": 2,
        "summaries": migrated_summaries,
        "backfill_cursor": document["backfill_cursor"],
        "expanded_backfill_cursor": None,
    }


def _migrate_v2_document(document: Mapping[str, object]) -> dict[str, object]:
    """Add durable body-option state to the expanded history checkpoint."""
    _require_exact_fields(
        document,
        _DOCUMENT_FIELDS - {"body_measurements_enabled"},
        "v2 history",
    )
    for summary in _require_mapping(document["summaries"], "summaries").values():
        v2_summary = _require_mapping(summary, "v2 summary")
        _require_exact_fields(
            v2_summary,
            _SUMMARY_FIELDS - _SUMMARY_ADDITIVE_FIELDS,
            "v2 summary",
        )
        v2_expanded = _require_mapping(v2_summary["expanded"], "v2 expanded")
        _require_exact_fields(
            v2_expanded,
            _EXPANDED_FIELDS - _EXPANDED_ADDITIVE_FIELDS,
            "v2 expanded",
        )
    return {
        **document,
        "schema_version": _SCHEMA_VERSION,
        "body_measurements_enabled": False,
    }


def _serialize_summary(summary: DailySummary) -> dict[str, object]:
    if not isinstance(summary, DailySummary):
        raise TypeError("summary must be a DailySummary")
    day = _require_date(summary.date, "summary date")
    source = summary.source
    if not isinstance(source, SourceKind):
        raise HistoryStoreError("source must be a SourceKind")
    if type(summary.complete) is not bool:
        raise HistoryStoreError("complete must be a boolean")
    return {
        "date": day.isoformat(),
        "steps": _serialize_optional_int(summary.steps, "steps"),
        "fitbit_steps": _serialize_optional_int(summary.fitbit_steps, "fitbit_steps"),
        "distance_m": _serialize_optional_float(summary.distance_m, "distance_m"),
        "active_energy_kcal": _serialize_optional_float(
            summary.active_energy_kcal, "active_energy_kcal"
        ),
        "total_energy_kcal": _serialize_optional_float(
            summary.total_energy_kcal, "total_energy_kcal"
        ),
        "nutrition_energy_kcal": _serialize_optional_float(
            summary.nutrition_energy_kcal, "nutrition_energy_kcal"
        ),
        "hydration_ml": _serialize_optional_float(summary.hydration_ml, "hydration_ml"),
        "exercise_minutes": _serialize_optional_float(summary.exercise_minutes, "exercise_minutes"),
        "sleep_minutes": _serialize_optional_float(summary.sleep_minutes, "sleep_minutes"),
        "sleep_period_minutes": _serialize_optional_float(
            summary.sleep_period_minutes, "sleep_period_minutes"
        ),
        "sleep_onset_minutes": _serialize_optional_float(
            summary.sleep_onset_minutes, "sleep_onset_minutes"
        ),
        "sleep_after_wake_minutes": _serialize_optional_float(
            summary.sleep_after_wake_minutes, "sleep_after_wake_minutes"
        ),
        "sleep_stages": _serialize_sleep_stages(summary.sleep_stages),
        "resting_heart_rate": _serialize_optional_float(
            summary.resting_heart_rate, "resting_heart_rate"
        ),
        "average_heart_rate": _serialize_optional_float(
            summary.average_heart_rate, "average_heart_rate"
        ),
        "minimum_heart_rate": _serialize_optional_float(
            summary.minimum_heart_rate, "minimum_heart_rate"
        ),
        "maximum_heart_rate": _serialize_optional_float(
            summary.maximum_heart_rate, "maximum_heart_rate"
        ),
        "hrv_ms": _serialize_optional_float(summary.hrv_ms, "hrv_ms"),
        "workouts": [_serialize_workout(workout) for workout in summary.workouts],
        "expanded": _serialize_expanded(summary.expanded),
        "source": source.value,
        "complete": summary.complete,
        "updated_at": _serialize_optional_datetime(summary.updated_at, "updated_at"),
    }


def _deserialize_summary(payload: object, expected_day: date) -> DailySummary:
    summary = _require_mapping(payload, "summary")
    _require_additive_fields(
        summary, _SUMMARY_FIELDS, _SUMMARY_ADDITIVE_FIELDS, "summary"
    )
    day = _parse_date(summary["date"], "summary date")
    if day != expected_day:
        raise HistoryStoreError("summary date does not match its date key")
    source_value = summary["source"]
    if not isinstance(source_value, str):
        raise HistoryStoreError("source must be a string enum")
    try:
        source = SourceKind(source_value)
    except ValueError as err:
        raise HistoryStoreError(f"invalid source enum: {source_value}") from err
    complete = summary["complete"]
    if type(complete) is not bool:
        raise HistoryStoreError("complete must be a boolean")
    return DailySummary(
        date=day,
        steps=_parse_optional_int(summary["steps"], "steps"),
        fitbit_steps=_parse_optional_int(summary["fitbit_steps"], "fitbit_steps"),
        distance_m=_parse_optional_float(summary["distance_m"], "distance_m"),
        active_energy_kcal=_parse_optional_float(
            summary["active_energy_kcal"], "active_energy_kcal"
        ),
        total_energy_kcal=_parse_optional_float(
            summary.get("total_energy_kcal"), "total_energy_kcal"
        ),
        nutrition_energy_kcal=_parse_optional_float(
            summary.get("nutrition_energy_kcal"), "nutrition_energy_kcal"
        ),
        hydration_ml=_parse_optional_float(summary.get("hydration_ml"), "hydration_ml"),
        exercise_minutes=_parse_optional_float(summary["exercise_minutes"], "exercise_minutes"),
        sleep_minutes=_parse_optional_float(summary["sleep_minutes"], "sleep_minutes"),
        sleep_period_minutes=_parse_optional_float(
            summary.get("sleep_period_minutes"), "sleep_period_minutes"
        ),
        sleep_onset_minutes=_parse_optional_float(
            summary.get("sleep_onset_minutes"), "sleep_onset_minutes"
        ),
        sleep_after_wake_minutes=_parse_optional_float(
            summary.get("sleep_after_wake_minutes"), "sleep_after_wake_minutes"
        ),
        sleep_stages=_deserialize_sleep_stages(summary["sleep_stages"]),
        resting_heart_rate=_parse_optional_float(
            summary["resting_heart_rate"], "resting_heart_rate"
        ),
        average_heart_rate=_parse_optional_float(
            summary["average_heart_rate"], "average_heart_rate"
        ),
        minimum_heart_rate=_parse_optional_float(
            summary["minimum_heart_rate"], "minimum_heart_rate"
        ),
        maximum_heart_rate=_parse_optional_float(
            summary["maximum_heart_rate"], "maximum_heart_rate"
        ),
        hrv_ms=_parse_optional_float(summary["hrv_ms"], "hrv_ms"),
        workouts=_deserialize_workouts(summary["workouts"]),
        expanded=_deserialize_expanded(summary["expanded"]),
        source=source,
        complete=complete,
        updated_at=_parse_optional_datetime(summary["updated_at"], "updated_at"),
    )


def _empty_v2_expanded_payload() -> dict[str, object]:
    return {
        key: value
        for key, value in _serialize_expanded(ExpandedDailyMetrics()).items()
        if key not in _EXPANDED_ADDITIVE_FIELDS
    }


def _serialize_expanded(value: object) -> dict[str, object]:
    if not isinstance(value, ExpandedDailyMetrics):
        raise HistoryStoreError("expanded must be an ExpandedDailyMetrics")
    serialized: dict[str, object] = {
        "active_zone_minutes": _serialize_zone_values(
            value.active_zone_minutes, "active_zone_minutes", _ACTIVE_ZONE_TYPES
        ),
        "vo2_max": _serialize_optional_float(value.vo2_max, "vo2_max"),
        "vo2_estimated": _serialize_optional_bool(value.vo2_estimated, "vo2_estimated"),
        "cardio_fitness_level": _serialize_optional_cardio_fitness_level(
            value.cardio_fitness_level
        ),
        "oxygen_average": _serialize_optional_percentage(value.oxygen_average, "oxygen_average"),
        "oxygen_lower_bound": _serialize_optional_percentage(
            value.oxygen_lower_bound, "oxygen_lower_bound"
        ),
        "oxygen_upper_bound": _serialize_optional_percentage(
            value.oxygen_upper_bound, "oxygen_upper_bound"
        ),
        "oxygen_standard_deviation": _serialize_optional_percentage(
            value.oxygen_standard_deviation, "oxygen_standard_deviation"
        ),
        "daily_respiratory_rate": _serialize_optional_float(
            value.daily_respiratory_rate, "daily_respiratory_rate"
        ),
        "sleep_respiratory_rates": _serialize_zone_values(
            value.sleep_respiratory_rates,
            "sleep_respiratory_rates",
            _SLEEP_RESPIRATORY_TYPES,
        ),
        "sleep_respiratory_standard_deviation": _serialize_optional_float(
            value.sleep_respiratory_standard_deviation,
            "sleep_respiratory_standard_deviation",
        ),
        "sleep_respiratory_signal_to_noise": _serialize_optional_float(
            value.sleep_respiratory_signal_to_noise,
            "sleep_respiratory_signal_to_noise",
        ),
        "floors": _serialize_optional_int(value.floors, "floors"),
        "sedentary_minutes": _serialize_optional_float(
            value.sedentary_minutes, "sedentary_minutes"
        ),
        "heart_zone_minutes": _serialize_zone_values(
            value.heart_zone_minutes, "heart_zone_minutes", _HEART_ZONE_TYPES
        ),
        "heart_zone_thresholds": _serialize_heart_zone_thresholds(value.heart_zone_thresholds),
        "heart_zone_calories": _serialize_zone_values(
            value.heart_zone_calories, "heart_zone_calories", _HEART_ZONE_TYPES
        ),
        "weight_kg": _serialize_optional_float(value.weight_kg, "weight_kg"),
        "body_fat_percentage": _serialize_optional_percentage(
            value.body_fat_percentage, "body_fat_percentage"
        ),
        "height_m": _serialize_optional_float(value.height_m, "height_m"),
    }
    _validate_expanded_metric_groups(serialized)
    return serialized


def _deserialize_expanded(value: object) -> ExpandedDailyMetrics:
    expanded = _require_mapping(value, "expanded")
    _require_additive_fields(
        expanded, _EXPANDED_FIELDS, _EXPANDED_ADDITIVE_FIELDS, "expanded"
    )
    result = ExpandedDailyMetrics(
        active_zone_minutes=_deserialize_zone_values(
            expanded["active_zone_minutes"], "active_zone_minutes", _ACTIVE_ZONE_TYPES
        ),
        vo2_max=_parse_optional_float(expanded["vo2_max"], "vo2_max"),
        vo2_estimated=_parse_optional_bool(expanded["vo2_estimated"], "vo2_estimated"),
        cardio_fitness_level=_parse_optional_cardio_fitness_level(expanded["cardio_fitness_level"]),
        oxygen_average=_parse_optional_percentage(expanded["oxygen_average"], "oxygen_average"),
        oxygen_lower_bound=_parse_optional_percentage(
            expanded["oxygen_lower_bound"], "oxygen_lower_bound"
        ),
        oxygen_upper_bound=_parse_optional_percentage(
            expanded["oxygen_upper_bound"], "oxygen_upper_bound"
        ),
        oxygen_standard_deviation=_parse_optional_percentage(
            expanded["oxygen_standard_deviation"], "oxygen_standard_deviation"
        ),
        daily_respiratory_rate=_parse_optional_float(
            expanded["daily_respiratory_rate"], "daily_respiratory_rate"
        ),
        sleep_respiratory_rates=_deserialize_zone_values(
            expanded["sleep_respiratory_rates"],
            "sleep_respiratory_rates",
            _SLEEP_RESPIRATORY_TYPES,
        ),
        sleep_respiratory_standard_deviation=_parse_optional_float(
            expanded["sleep_respiratory_standard_deviation"],
            "sleep_respiratory_standard_deviation",
        ),
        sleep_respiratory_signal_to_noise=_parse_optional_float(
            expanded["sleep_respiratory_signal_to_noise"],
            "sleep_respiratory_signal_to_noise",
        ),
        floors=_parse_optional_int(expanded["floors"], "floors"),
        sedentary_minutes=_parse_optional_float(expanded["sedentary_minutes"], "sedentary_minutes"),
        heart_zone_minutes=_deserialize_zone_values(
            expanded["heart_zone_minutes"], "heart_zone_minutes", _HEART_ZONE_TYPES
        ),
        heart_zone_thresholds=_deserialize_heart_zone_thresholds(
            expanded["heart_zone_thresholds"]
        ),
        heart_zone_calories=_deserialize_zone_values(
            expanded["heart_zone_calories"], "heart_zone_calories", _HEART_ZONE_TYPES
        ),
        weight_kg=_parse_optional_float(expanded["weight_kg"], "weight_kg"),
        body_fat_percentage=_parse_optional_percentage(
            expanded.get("body_fat_percentage"), "body_fat_percentage"
        ),
        height_m=_parse_optional_float(expanded.get("height_m"), "height_m"),
    )
    _serialize_expanded(result)
    return result


def _serialize_zone_values(
    value: object, field: str, allowed_zones: frozenset[str]
) -> dict[str, float]:
    zones = _require_mapping(value, field)
    serialized: dict[str, float] = {}
    for zone, amount in zones.items():
        if zone not in allowed_zones:
            raise HistoryStoreError(f"{field} contains an invalid zone")
        serialized[zone] = _parse_finite_float(amount, f"{field}.{zone}")
    return serialized


def _deserialize_zone_values(
    value: object, field: str, allowed_zones: frozenset[str]
) -> dict[str, float]:
    return _serialize_zone_values(value, field, allowed_zones)


def _serialize_heart_zone_thresholds(value: object) -> dict[str, list[int]]:
    thresholds = _require_mapping(value, "heart_zone_thresholds")
    serialized: dict[str, list[int]] = {}
    for zone, pair in thresholds.items():
        if zone not in _HEART_ZONE_TYPES:
            raise HistoryStoreError("heart_zone_thresholds contains an invalid zone")
        if not isinstance(pair, Sequence) or isinstance(pair, (str, bytes, bytearray)):
            raise HistoryStoreError("heart_zone_thresholds values must be two integers")
        if len(pair) != 2:
            raise HistoryStoreError("heart_zone_thresholds values must be two integers")
        minimum = _parse_non_negative_int(pair[0], f"heart_zone_thresholds.{zone}.minimum")
        maximum = _parse_non_negative_int(pair[1], f"heart_zone_thresholds.{zone}.maximum")
        if minimum > maximum:
            raise HistoryStoreError("heart_zone_thresholds minimum must not exceed maximum")
        serialized[zone] = [minimum, maximum]
    return serialized


def _deserialize_heart_zone_thresholds(value: object) -> dict[str, tuple[int, int]]:
    thresholds = _require_mapping(value, "heart_zone_thresholds")
    parsed: dict[str, tuple[int, int]] = {}
    for zone, pair in thresholds.items():
        if zone not in _HEART_ZONE_TYPES:
            raise HistoryStoreError("heart_zone_thresholds contains an invalid zone")
        if not isinstance(pair, list) or len(pair) != 2:
            raise HistoryStoreError("heart_zone_thresholds values must be two integers")
        minimum = _parse_non_negative_int(pair[0], f"heart_zone_thresholds.{zone}.minimum")
        maximum = _parse_non_negative_int(pair[1], f"heart_zone_thresholds.{zone}.maximum")
        if minimum > maximum:
            raise HistoryStoreError("heart_zone_thresholds minimum must not exceed maximum")
        parsed[zone] = (minimum, maximum)
    return parsed


def _serialize_optional_bool(value: object, field: str) -> bool | None:
    return None if value is None else _parse_optional_bool(value, field)


def _parse_optional_bool(value: object, field: str) -> bool | None:
    if value is None:
        return None
    if type(value) is not bool:
        raise HistoryStoreError(f"{field} must be a boolean or null")
    return value


def _serialize_optional_cardio_fitness_level(value: object) -> str | None:
    return _parse_optional_cardio_fitness_level(value)


def _parse_optional_cardio_fitness_level(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _CARDIO_FITNESS_LEVELS:
        raise HistoryStoreError("cardio_fitness_level is invalid")
    return value


def _serialize_optional_percentage(value: object, field: str) -> float | None:
    return _parse_optional_percentage(value, field)


def _parse_optional_percentage(value: object, field: str) -> float | None:
    parsed = _parse_optional_float(value, field)
    if parsed is not None and parsed > 100:
        raise HistoryStoreError(f"{field} must not exceed 100")
    return parsed


def _validate_expanded_metric_groups(value: Mapping[str, object]) -> None:
    oxygen_fields = (
        "oxygen_average",
        "oxygen_lower_bound",
        "oxygen_upper_bound",
        "oxygen_standard_deviation",
    )
    oxygen_values = tuple(value[field] for field in oxygen_fields)
    if any(item is None for item in oxygen_values):
        if not all(item is None for item in oxygen_values):
            raise HistoryStoreError("oxygen metrics must be complete or absent")
    else:
        average, lower, upper, _standard_deviation = cast(
            tuple[float, float, float, float], oxygen_values
        )
        if lower > upper or not lower <= average <= upper:
            raise HistoryStoreError("oxygen metrics have invalid bounds")

    rates = value["sleep_respiratory_rates"]
    standard_deviation = value["sleep_respiratory_standard_deviation"]
    signal_to_noise = value["sleep_respiratory_signal_to_noise"]
    if not isinstance(rates, Mapping):
        raise HistoryStoreError("sleep_respiratory_rates must be an object")
    if rates and (
        "full" not in rates or standard_deviation is None or signal_to_noise is None
    ):
        raise HistoryStoreError("sleep respiratory metrics must include full-sleep metadata")
    if not rates and (standard_deviation is not None or signal_to_noise is not None):
        raise HistoryStoreError("sleep respiratory metadata requires sleep rates")


def _serialize_sleep_stages(value: object) -> dict[str, float]:
    stages = _require_mapping(value, "sleep_stages")
    serialized: dict[str, float] = {}
    for stage, minutes in stages.items():
        if not isinstance(stage, str) or stage not in _SLEEP_STAGE_TYPES:
            raise HistoryStoreError("sleep_stages contains an invalid stage")
        serialized[stage] = _parse_finite_float(minutes, f"sleep_stages.{stage}")
    return serialized


def _deserialize_sleep_stages(value: object) -> dict[str, float]:
    return _serialize_sleep_stages(value)


def _serialize_workout(workout: WorkoutSummary) -> dict[str, object]:
    if not isinstance(workout, WorkoutSummary):
        raise HistoryStoreError("workouts must contain WorkoutSummary values")
    activity_type = _parse_activity_type(workout.activity_type)
    start = _serialize_optional_datetime(workout.start, "workout.start")
    end = _serialize_optional_datetime(workout.end, "workout.end")
    _validate_workout_interval(workout.start, workout.end)
    return {
        "activity_type": activity_type,
        "duration_minutes": _parse_finite_float(
            workout.duration_minutes, "workout.duration_minutes"
        ),
        "start": start,
        "end": end,
        "active_energy_kcal": _serialize_optional_float(
            workout.active_energy_kcal, "workout.active_energy_kcal"
        ),
    }


def _deserialize_workouts(value: object) -> tuple[WorkoutSummary, ...]:
    if not isinstance(value, list):
        raise HistoryStoreError("workouts must be a list")
    workouts: list[WorkoutSummary] = []
    for item in value:
        workout = _require_mapping(item, "workout")
        _require_exact_fields(workout, _WORKOUT_FIELDS, "workout")
        start = _parse_optional_datetime(workout["start"], "workout.start")
        end = _parse_optional_datetime(workout["end"], "workout.end")
        _validate_workout_interval(start, end)
        workouts.append(
            WorkoutSummary(
                activity_type=_parse_activity_type(workout["activity_type"]),
                duration_minutes=_parse_finite_float(
                    workout["duration_minutes"], "workout.duration_minutes"
                ),
                start=start,
                end=end,
                active_energy_kcal=_parse_optional_float(
                    workout["active_energy_kcal"], "workout.active_energy_kcal"
                ),
            )
        )
    return tuple(workouts)


def _validate_workout_interval(start: datetime | None, end: datetime | None) -> None:
    if start is not None and end is not None and end < start:
        raise HistoryStoreError("workout end must not be before workout start")


def _parse_activity_type(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise HistoryStoreError("workout.activity_type must be a non-empty string")
    return value


def _serialize_optional_int(value: object, field: str) -> int | None:
    return None if value is None else _parse_non_negative_int(value, field)


def _parse_optional_int(value: object, field: str) -> int | None:
    return None if value is None else _parse_non_negative_int(value, field)


def _parse_non_negative_int(value: object, field: str) -> int:
    if type(value) is not int or value < 0:
        raise HistoryStoreError(f"{field} must be a non-negative integer")
    return value


def _serialize_optional_float(value: object, field: str) -> float | None:
    return None if value is None else _parse_finite_float(value, field)


def _parse_optional_float(value: object, field: str) -> float | None:
    return None if value is None else _parse_finite_float(value, field)


def _parse_finite_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HistoryStoreError(f"{field} must be a finite number")
    parsed = float(value)
    if not isfinite(parsed) or parsed < 0:
        raise HistoryStoreError(f"{field} must be a finite non-negative number")
    return parsed


def _serialize_optional_datetime(value: object, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise HistoryStoreError(f"{field} must be a timezone-aware datetime")
    _require_timezone(value, field)
    return value.isoformat()


def _parse_optional_datetime(value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HistoryStoreError(f"{field} must be an ISO datetime string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as err:
        raise HistoryStoreError(f"{field} must be an ISO datetime string") from err
    _require_timezone(parsed, field)
    if parsed.isoformat() != value:
        raise HistoryStoreError(f"{field} must use canonical datetime.isoformat() output")
    return parsed


def _require_timezone(value: datetime, field: str) -> None:
    try:
        offset = value.utcoffset()
    except ValueError as err:
        raise HistoryStoreError(f"{field} has an invalid timezone") from err
    if value.tzinfo is None or offset is None:
        raise HistoryStoreError(f"{field} must be timezone-aware")


def _require_date(value: object, field: str) -> date:
    if type(value) is not date:
        raise HistoryStoreError(f"{field} must be a date")
    return value


def _parse_optional_date(value: object, field: str) -> date | None:
    return None if value is None else _parse_date(value, field)


def _parse_date(value: object, field: str) -> date:
    if not isinstance(value, str):
        raise HistoryStoreError(f"{field} must be an ISO date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as err:
        raise HistoryStoreError(f"{field} must be an ISO date string") from err
    if parsed.isoformat() != value:
        raise HistoryStoreError(f"{field} must be an ISO date string")
    return parsed


def _require_mapping(value: object, field: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise HistoryStoreError(f"{field} must be an object")
    return cast(Mapping[str, object], value)


def _require_exact_fields(
    value: Mapping[str, object], expected: frozenset[str], field: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise HistoryStoreError(
            f"{field} has invalid fields; missing={missing}, unexpected={unexpected}"
        )


def _require_additive_fields(
    value: Mapping[str, object],
    allowed: frozenset[str],
    optional: frozenset[str],
    field: str,
) -> None:
    actual = set(value)
    missing = sorted(allowed - optional - actual)
    unexpected = sorted(actual - allowed)
    if missing or unexpected:
        raise HistoryStoreError(
            f"{field} has invalid fields; missing={missing}, unexpected={unexpected}"
        )
