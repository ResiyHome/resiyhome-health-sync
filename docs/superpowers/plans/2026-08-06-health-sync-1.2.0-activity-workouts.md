# Health Sync 1.2.0 Activity and Workouts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a backward-compatible Health Sync 1.2.0 beta that adds comprehensive workout summaries and bounded details, activity-level metrics, elevation gain, standard and running VO2 max, swimming metrics, and sleep efficiency.

**Architecture:** Extend the immutable normalized models and versioned private store additively, then populate them through the existing activity capability and coordinator failure-isolation boundaries. Stable scalar values become Home Assistant sensors; variable workout splits, events, heart-zone details, and running mobility values remain bounded attributes on one disabled-by-default details sensor. A separate 30-day workout-enrichment cursor updates recent stored workouts without rewinding the 20-year core cursor.

**Tech Stack:** Python 3.13, Home Assistant custom integration APIs, `aiohttp`, immutable dataclasses, Home Assistant `Store`, `pytest`, `pytest-homeassistant-custom-component`, Ruff.

## Global Constraints

- Preserve every existing config entry, entity unique ID, entity ID, Recorder history row, and normalized history row.
- Keep existing read-only OAuth scopes; 1.2.0 must not require reauthorization.
- Preserve every existing sensor key, default, unit, and behavior except that existing last-workout entities correctly retain the latest stored workout across midnight.
- Add no direct Apple Health, Fitbit, GPS route, webhook, or write API integration.
- Never persist or expose Google resource names, account identifiers, tokens, or GPS coordinates.
- Optional fields and data types fail independently and retain prior valid values.
- Cap stored and exposed workout details at 50 splits and 50 events per workout.
- Restart only the 90-day expanded cursor during v3-to-v4 migration; never rewind the 20-year core cursor.
- Write a failing test and verify its expected failure before changing production code for each behavior.
- Remaining sleep expansion and additional nutrition detail are out of scope.

---

### Task 1: Register New Google Activity Data Types

**Files:**
- Modify: `custom_components/resiyhome_health_sync/api.py`
- Modify: `custom_components/resiyhome_health_sync/coordinator.py`
- Test: `tests/test_api.py`
- Test: `tests/test_probe.py`

**Interfaces:**
- Consumes: existing `_DataTypeSpec`, `build_time_filter()`, and `get_data_type_operations()`.
- Produces: transport support for `activity-level` and `swim-lengths-data`; retains existing support for `altitude`, `vo2-max`, and `run-vo2-max`.

- [ ] **Step 1: Add failing operation and filter tests**

```python
@pytest.mark.parametrize(
    ("data_type", "filter_field"),
    [
        ("activity-level", "activity_level.interval.start_time"),
        ("swim-lengths-data", "swim_lengths_data.interval.start_time"),
    ],
)
def test_activity_expansion_types_build_physical_filters(
    data_type: str, filter_field: str
) -> None:
    value = build_time_filter(data_type, START, END)
    assert value.startswith(f'{filter_field} >= "')
    expected_operations = (
        {"list", "reconcile"}
        if data_type == "activity-level"
        else {"list", "reconcile", "rollup", "daily_rollup"}
    )
    assert get_data_type_operations(data_type) == expected_operations
```

Add a probe assertion that both identifiers are accepted and no payload values
are returned by the probe action.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_api.py tests/test_probe.py -k "activity_expansion or activity-level or swim-lengths" -v
```

Expected: failure because `_DATA_TYPE_SPECS` and the probe allowlist do not
recognize both identifiers.

- [ ] **Step 3: Add exact transport specifications**

```python
"activity-level": _DataTypeSpec(
    "activity_level.interval.start_time",
    "physical",
),
"swim-lengths-data": _DataTypeSpec(
    "swim_lengths_data.interval.start_time",
    "physical",
    operations=_LIST_RECONCILE_ROLLUPS,
),
```

Add both identifiers to `OPTIONAL_PROBE_DATA_TYPES` without changing scopes or
client methods.

- [ ] **Step 4: Run focused API and probe tests**

```bash
.venv/bin/python -m pytest tests/test_api.py tests/test_probe.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/resiyhome_health_sync/api.py custom_components/resiyhome_health_sync/coordinator.py tests/test_api.py tests/test_probe.py
git commit -m "feat: register activity expansion data types"
```

---

### Task 2: Add Immutable Models and Schema v4 Storage

**Files:**
- Modify: `custom_components/resiyhome_health_sync/models.py`
- Modify: `custom_components/resiyhome_health_sync/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Produces:
  - `WorkoutMobilityMetrics`
  - `WorkoutSplit`
  - `WorkoutEvent`
  - expanded `WorkoutSummary`
  - expanded `ExpandedDailyMetrics`
  - `DailySummary.sleep_efficiency_percentage`
  - `CoordinatorSnapshot.latest_workout`
  - `HealthHistoryStore.workout_enrichment_cursor`
  - `HealthHistoryStore.async_checkpoint_workout_enrichment()`

- [ ] **Step 1: Write failing immutable-model and v3 migration tests**

Create model instances with every new optional field, serialize them, reload
them, and assert equality. Add a literal schema-v3 fixture and assert:

```python
assert migrated.backfill_cursor == original_backfill_cursor
assert migrated.expanded_backfill_cursor is None
assert migrated.workout_enrichment_cursor is None
assert migrated_rows[0].workouts[0].activity_type == "RUNNING"
assert migrated_rows[0].workouts[0].splits == ()
assert migrated_rows[0].workouts[0].events == ()
```

Add corrupt-payload tests for negative counts, invalid enums, naive timestamps,
more than 50 stored details, and non-finite values. Assert a failed confirmed
write leaves the original in-memory and persisted document unchanged.

- [ ] **Step 2: Run storage tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_storage.py -k "v4 or workout or activity_level or swim or efficiency" -v
```

Expected: failures because schema v4 fields and dataclasses do not exist.

- [ ] **Step 3: Add the immutable dataclasses**

Implement these exact contracts:

```python
@dataclass(slots=True, frozen=True)
class WorkoutMobilityMetrics:
    average_ground_contact_time_ms: float | None = None
    average_cadence_steps_per_minute: float | None = None
    average_stride_length_m: float | None = None
    average_vertical_oscillation_m: float | None = None
    average_vertical_ratio: float | None = None


@dataclass(slots=True, frozen=True)
class WorkoutSplit:
    split_type: str
    start: datetime
    end: datetime
    active_duration_minutes: float | None = None
    active_energy_kcal: float | None = None
    distance_m: float | None = None
    steps: int | None = None
    average_heart_rate: float | None = None
    active_zone_minutes: float | None = None
    average_speed_mps: float | None = None
    average_pace_seconds_per_km: float | None = None
    elevation_gain_m: float | None = None
    heart_zone_minutes: Mapping[str, float] = field(default_factory=_empty_mapping)


@dataclass(slots=True, frozen=True)
class WorkoutEvent:
    event_type: str
    event_time: datetime
```

Extend `WorkoutSummary`, `ExpandedDailyMetrics`, `DailySummary`, and
`CoordinatorSnapshot` with the exact fields in the approved design. Snapshot
fields include the latest workout plus latest standard/running VO2 values and
their timestamps. Wrap every mapping in `MappingProxyType` in `__post_init__`.

- [ ] **Step 4: Implement schema v4 migration and round-trip serialization**

Set `_SCHEMA_VERSION = 4`. Add `workout_enrichment_cursor` to
`_DOCUMENT_FIELDS`, `_serialize_document()`, `_deserialize_document()`, store
state, properties, invalidation, and all transactional checkpoint calls.

Add:

```python
def _migrate_v3_document(document: Mapping[str, object]) -> dict[str, object]:
    """Add activity/workout fields and reset only bounded expansion cursors."""
```

The migration must validate the complete v3 shape before returning:

```python
{
    **document,
    "schema_version": 4,
    "expanded_backfill_cursor": None,
    "workout_enrichment_cursor": None,
}
```

Serialize and deserialize every new field using finite, non-negative,
timezone-aware validators. Validate split/event enums against fixed allowlists
and reject persisted arrays larger than 50.

Add:

```python
async def async_checkpoint_workout_enrichment(
    self,
    summaries: Mapping[date, DailySummary],
    next_cursor: date | None,
) -> None:
    """Atomically persist enriched summaries and the independent cursor."""
```

- [ ] **Step 5: Run storage tests**

```bash
.venv/bin/python -m pytest tests/test_storage.py -v
```

Expected: pass, including all legacy migration fixtures.

- [ ] **Step 6: Commit**

```bash
git add custom_components/resiyhome_health_sync/models.py custom_components/resiyhome_health_sync/storage.py tests/test_storage.py
git commit -m "feat: add activity and workout storage schema"
```

---

### Task 3: Normalize Comprehensive Workout Details

**Files:**
- Modify: `custom_components/resiyhome_health_sync/normalize.py`
- Test: `tests/test_normalize.py`
- Test fixture: `tests/fixtures/steps_fitbit_healthkit.json`
- Modify fixture generator: `tests/fixtures/generate_steps_fixture.py`

**Interfaces:**
- Consumes: Task 2 dataclasses.
- Produces:

```python
def _workouts(stream: DataPointStreams) -> tuple[WorkoutSummary, ...]
def _workout(
    payload: Mapping[str, object] | None,
    heart_rate_points: Sequence[DataPoint],
) -> WorkoutSummary | None
```

- [ ] **Step 1: Add a complete documented workout fixture and failing tests**

The fixture must include:

```json
{
  "caloriesKcal": 380.0,
  "distanceMillimeters": 5000000.0,
  "steps": "6200",
  "averageSpeedMillimetersPerSecond": 2777.78,
  "averagePaceSecondsPerMeter": 0.36,
  "averageHeartRateBeatsPerMinute": "148",
  "elevationGainMillimeters": 125000.0,
  "activeZoneMinutes": "30",
  "runVo2Max": 46.2,
  "totalSwimLengths": 0
}
```

Also include heart-zone durations, all five mobility fields, two splits, four
events, GPS metadata, and in-window/out-of-window heart-rate samples.

Assert conversions, ordering, immutable mappings, maximum heart-rate
derivation, detail counts, and `details_truncated`.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/test_normalize.py -k "workout" -v
```

Expected: new field assertions fail against the existing five-field
`WorkoutSummary`.

- [ ] **Step 3: Parse independent optional summary fields**

Add focused helpers:

```python
def _workout_metrics(value: object) -> dict[str, object]:
    """Normalize each optional exercise summary field independently."""

def _workout_maximum_heart_rate(
    points: Sequence[DataPoint], start: datetime, end: datetime
) -> float | None:
    """Return the maximum valid sample inside the closed workout interval."""

def _workout_splits(value: object) -> tuple[tuple[WorkoutSplit, ...], int, bool]:
    """Return at most 50 sanitized splits, source count, and truncation."""

def _workout_events(value: object) -> tuple[tuple[WorkoutEvent, ...], int, bool]:
    """Return at most 50 sanitized events, source count, and truncation."""
```

Use meters, meters/second, seconds/kilometer, bpm, minutes, and
`mL/kg/min`. Preserve the baseline workout if one optional field is malformed.
Drop malformed split/event rows individually.

- [ ] **Step 4: Add boundary and adversarial tests**

Cover negative, boolean-as-number, non-finite, unknown enum, naïve timestamp,
inverted interval, split outside workout, future event, duplicate event,
invalid duration, 51-element truncation, and no heart-rate samples.

Name each test for one behavior and verify each new test fails before adding
the corresponding validation.

- [ ] **Step 5: Run normalization tests**

```bash
.venv/bin/python -m pytest tests/test_normalize.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add custom_components/resiyhome_health_sync/normalize.py tests/test_normalize.py tests/fixtures/steps_fitbit_healthkit.json tests/fixtures/generate_steps_fixture.py
git commit -m "feat: normalize comprehensive workout details"
```

---

### Task 4: Retain the Latest Workout and Enrich 30 Days Safely

**Files:**
- Modify: `custom_components/resiyhome_health_sync/coordinator.py`
- Modify: `custom_components/resiyhome_health_sync/storage.py`
- Test: `tests/test_coordinator.py`
- Test: `tests/test_backfill.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: Task 2 checkpoint API and Task 3 workout normalization.
- Produces:

```python
_WORKOUT_ENRICHMENT_DAYS = 30
_WORKOUT_ENRICHMENT_WINDOW_DAYS = 7

async def _async_backfill_workout_enrichment_step_locked(self) -> None
def _latest_workout(rows: Sequence[DailySummary]) -> WorkoutSummary | None
def _merge_enriched_workouts(
    previous: DailySummary | None,
    enriched: DailySummary,
) -> DailySummary
```

- [ ] **Step 1: Add failing latest-workout and enrichment tests**

Assert:

- a workout from yesterday remains `snapshot.latest_workout` after a
  workout-free current refresh
- a newer current workout replaces it
- a seven-day enrichment window requests only `exercise` and `heart-rate`
- matching uses `(start, end, activity_type)`
- existing type, duration, interval, and calories are never erased
- optional details are added idempotently
- interruption resumes from the durable cursor
- current refresh waiters retain priority
- completion occurs at exactly `today - 30 days`
- the 20-year core cursor is unchanged

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/test_coordinator.py tests/test_backfill.py -k "latest_workout or workout_enrichment" -v
```

Expected: failures because snapshot selection and enrichment do not exist.

- [ ] **Step 3: Rebuild latest workout from loaded history**

During `_async_ensure_history_loaded()`, select the workout with the greatest
timezone-aware `end`, falling back to `start` only when needed. Recompute after
current refresh and after each enrichment checkpoint.

Do not serialize `CoordinatorSnapshot.latest_workout` separately; it is derived
from normalized daily rows.

- [ ] **Step 4: Implement one bounded enrichment step**

Add a third independently locked phase to `async_backfill_step()`. Fetch
reconciled exercise and heart-rate streams for one seven-day window, normalize
each day, merge optional workout fields, and call
`async_checkpoint_workout_enrichment()` once per completed window.

Use `asyncio.shield()` around the checkpoint and mirror cursor state only after
the durable write completes, matching existing core and expanded checkpoint
semantics.

- [ ] **Step 5: Run coordinator, backfill, and storage tests**

```bash
.venv/bin/python -m pytest tests/test_coordinator.py tests/test_backfill.py tests/test_storage.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add custom_components/resiyhome_health_sync/coordinator.py custom_components/resiyhome_health_sync/storage.py tests/test_coordinator.py tests/test_backfill.py tests/test_storage.py
git commit -m "feat: enrich and retain recent workouts"
```

---

### Task 5: Normalize Activity Levels and Elevation Gain

**Files:**
- Modify: `custom_components/resiyhome_health_sync/coordinator.py`
- Modify: `custom_components/resiyhome_health_sync/expanded_metrics.py`
- Test: `tests/test_expanded_metrics.py`
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Produces:

```python
def _activity_levels(
    stream: DataPointStreams,
    day: date,
    refresh_time: datetime,
) -> tuple[Mapping[str, float], str | None, datetime | None]

def _elevation_gain(
    direct: DataPointStreams,
    rollups: DataPointStreams,
    day: date,
) -> float | None
```

- [ ] **Step 1: Write failing normalization tests**

Cover all four documented activity enums, midnight-crossing intervals, local
offsets, overlapping malformed rows, future intervals, unknown enums,
deterministic latest selection, millimeter-to-meter conversion, rollup
preference, direct fallback, and true zero.

Expected result example:

```python
assert result.activity_level_minutes == {
    "sedentary": 120.0,
    "lightly_active": 45.0,
    "moderately_active": 20.0,
    "very_active": 10.0,
}
assert result.latest_activity_level == "lightly_active"
assert result.elevation_gain_m == 125.0
```

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/test_expanded_metrics.py -k "activity_level or elevation" -v
```

- [ ] **Step 3: Implement normalization and coordinator fetch groups**

Add `activity-level` to expanded direct/current interval types. Add `altitude`
to expanded direct types and daily rollup types. Pass `refresh_time` into
`normalize_expanded_day()` so future observations cannot become latest.

Add both types to `_EXPANDED_GROUP_FIELDS` and partial-merge maps so a failed
request retains only its prior metric group.

- [ ] **Step 4: Run expanded and coordinator tests**

```bash
.venv/bin/python -m pytest tests/test_expanded_metrics.py tests/test_coordinator.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/resiyhome_health_sync/coordinator.py custom_components/resiyhome_health_sync/expanded_metrics.py tests/test_expanded_metrics.py tests/test_coordinator.py
git commit -m "feat: add activity levels and elevation gain"
```

---

### Task 6: Normalize Standard VO2, Running VO2, and Swimming

**Files:**
- Modify: `custom_components/resiyhome_health_sync/coordinator.py`
- Modify: `custom_components/resiyhome_health_sync/expanded_metrics.py`
- Test: `tests/test_expanded_metrics.py`
- Test: `tests/test_coordinator.py`

**Interfaces:**
- Produces:

```python
def _latest_vo2_max(
    stream: DataPointStreams, day: date, refresh_time: datetime
) -> tuple[float | None, str | None, datetime | None]

def _latest_run_vo2_max(
    stream: DataPointStreams, day: date, refresh_time: datetime
) -> tuple[float | None, datetime | None]

def _swimming(
    stream: DataPointStreams, day: date
) -> tuple[int | None, int | None, Mapping[str, int], str | None]
```

- [ ] **Step 1: Add failing sample and swimming tests**

Test every documented VO2 measurement method, latest valid sample selection,
future exclusion, malformed values, and unavailable behavior.

For swimming, test all stroke enums, unspecified strokes, count and stroke
totals, invalid intervals, deterministic primary-stroke ties, and true zero.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/test_expanded_metrics.py -k "latest_vo2 or run_vo2 or swimming" -v
```

- [ ] **Step 3: Implement sample and swimming normalization**

Use exact normalized method strings and these primary-stroke tie priorities:

```python
SWIM_STROKE_PRIORITY = (
    "freestyle",
    "backstroke",
    "breaststroke",
    "butterfly",
)
```

An unspecified stroke contributes to `swim_strokes` but not
`primary_swim_stroke`.

- [ ] **Step 4: Add fetch and partial-failure support**

Add `vo2-max`, `run-vo2-max`, and `swim-lengths-data` to expanded direct types,
group fields, returned-day routing, diagnostics count inputs, and partial merge
maps. Do not replace or rename existing daily VO2 fields.

Recompute snapshot-level latest standard and running VO2 values from loaded
history after startup, each successful current refresh, and each expanded
checkpoint. A day without a new sample must retain the latest older sample;
only a successful history rebuild with no samples yields unavailable.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/python -m pytest tests/test_expanded_metrics.py tests/test_coordinator.py tests/test_backfill.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add custom_components/resiyhome_health_sync/coordinator.py custom_components/resiyhome_health_sync/expanded_metrics.py tests/test_expanded_metrics.py tests/test_coordinator.py tests/test_backfill.py
git commit -m "feat: add VO2 and swimming metrics"
```

---

### Task 7: Derive Sleep Efficiency

**Files:**
- Modify: `custom_components/resiyhome_health_sync/normalize.py`
- Modify: `custom_components/resiyhome_health_sync/storage.py`
- Test: `tests/test_normalize.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Produces:

```python
def _sleep_efficiency(
    sleep_minutes: float | None,
    sleep_period_minutes: float | None,
) -> float | None
```

- [ ] **Step 1: Add failing calculation tests**

```python
@pytest.mark.parametrize(
    ("asleep", "period", "expected"),
    [
        (375.0, 402.0, pytest.approx(93.28358)),
        (0.0, 402.0, 0.0),
        (None, 402.0, None),
        (375.0, None, None),
        (375.0, 0.0, None),
        (403.0, 402.0, None),
    ],
)
def test_sleep_efficiency(asleep, period, expected):
    assert _sleep_efficiency(asleep, period) == expected
```

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/test_normalize.py tests/test_storage.py -k "sleep_efficiency" -v
```

- [ ] **Step 3: Implement and persist the derived scalar**

Calculate only from the same selected sleep session fields. Add the scalar to
schema-v4 summary serialization and deserialization; schema-v3 migration adds
`None` and does not infer historical values until the expanded/core refresh
reprocesses that day.

- [ ] **Step 4: Run tests**

```bash
.venv/bin/python -m pytest tests/test_normalize.py tests/test_storage.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/resiyhome_health_sync/normalize.py custom_components/resiyhome_health_sync/storage.py tests/test_normalize.py tests/test_storage.py
git commit -m "feat: add sleep efficiency metric"
```

---

### Task 8: Add Home Assistant Sensor Entities and Translations

**Files:**
- Modify: `custom_components/resiyhome_health_sync/sensor.py`
- Modify: `custom_components/resiyhome_health_sync/strings.json`
- Modify: `custom_components/resiyhome_health_sync/translations/en.json`
- Test: `tests/test_entities.py`
- Test: `tests/test_release_identity.py`
- Test: `tests/test_documentation.py`

**Interfaces:**
- Consumes: `CoordinatorSnapshot.latest_workout`, new daily and expanded fields.
- Produces: the exact enabled/disabled sensor keys from the approved design.

- [ ] **Step 1: Add failing entity identity and state tests**

Assert all 23 new keys, defaults, units, device classes, state classes,
translations, unavailable behavior, and unique IDs. Assert every pre-1.2.0
unique ID remains unchanged.

Test that existing `last_workout_type` and `last_workout_duration` read
`snapshot.latest_workout`, not only `snapshot.current_day.workouts`.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/test_entities.py tests/test_release_identity.py tests/test_documentation.py -k "workout or activity or vo2 or swim or efficiency" -v
```

- [ ] **Step 3: Add sensor helpers and stable entities**

Add:

```python
def _latest_workout_field(field: str) -> ValueFunction:
    return lambda snapshot: (
        getattr(snapshot.latest_workout, field)
        if snapshot.latest_workout is not None
        else None
    )

def _last_workout_details_attributes(
    snapshot: CoordinatorSnapshot,
) -> Mapping[str, StateType]:
    """Return bounded JSON-safe workout detail attributes."""
```

Use `SensorDeviceClass.TIMESTAMP` for start/end and observation timestamps,
Home Assistant standard energy/distance/speed/percentage classes where
available, and no synthetic zero for missing values.

- [ ] **Step 4: Add exact defaults**

Enabled by default:

```text
last_workout_calories
last_workout_start
last_workout_end
last_workout_distance
last_workout_steps
last_workout_average_heart_rate
last_workout_maximum_heart_rate
sleep_efficiency
lightly_active_minutes_today
moderately_active_minutes_today
very_active_minutes_today
latest_activity_level
elevation_gain_today
```

Disabled by default:

```text
last_workout_active_zone_minutes
last_workout_average_speed
last_workout_average_pace
last_workout_elevation_gain
last_workout_details
latest_vo2_max
latest_running_vo2_max
swim_lengths_today
swim_strokes_today
primary_swim_stroke_today
```

- [ ] **Step 5: Run entity, identity, and translation tests**

```bash
.venv/bin/python -m pytest tests/test_entities.py tests/test_release_identity.py tests/test_documentation.py -v
```

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add custom_components/resiyhome_health_sync/sensor.py custom_components/resiyhome_health_sync/strings.json custom_components/resiyhome_health_sync/translations/en.json tests/test_entities.py tests/test_release_identity.py tests/test_documentation.py
git commit -m "feat: expose activity and workout entities"
```

---

### Task 9: Extend Authenticated History and Value-Free Diagnostics

**Files:**
- Modify: `custom_components/resiyhome_health_sync/websocket.py`
- Modify: `custom_components/resiyhome_health_sync/diagnostics.py`
- Test: `tests/test_websocket.py`
- Test: `tests/test_diagnostics.py`

**Interfaces:**
- Produces: allowlisted scalar history metrics and count/status-only diagnostic
  summaries.

- [ ] **Step 1: Add failing privacy and allowlist tests**

Assert authenticated history accepts new scalar daily/expanded fields but
rejects split arrays, event arrays, workout names, GPS metadata, and unknown
metrics.

Assert diagnostics contain:

```python
{
    "workout_count": 1,
    "split_count": 2,
    "event_count": 4,
    "details_truncated": False,
    "swim_record_count": 3,
    "workout_enrichment_cursor": "2042-07-01",
    "workout_enrichment_complete": False,
}
```

and contain none of the underlying health values, workout names, health-record
timestamps, resource names, tokens, splits, or events.

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/test_websocket.py tests/test_diagnostics.py -k "workout or activity or vo2 or swim or enrichment or efficiency" -v
```

- [ ] **Step 3: Extend allowlists and count-only summaries**

Add only stable scalar fields and immutable scalar mappings to history
allowlists. Convert timestamps to ISO strings only in authenticated history.
Keep variable workout detail arrays out of ordinary history responses.

Add diagnostics availability booleans and counts. Route the final diagnostics
object through existing recursive sanitization.

- [ ] **Step 4: Run privacy tests**

```bash
.venv/bin/python -m pytest tests/test_websocket.py tests/test_diagnostics.py -v
```

Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add custom_components/resiyhome_health_sync/websocket.py custom_components/resiyhome_health_sync/diagnostics.py tests/test_websocket.py tests/test_diagnostics.py
git commit -m "feat: add private activity history diagnostics"
```

---

### Task 10: Document and Package 1.2.0 Beta 1

**Files:**
- Modify: `README.md`
- Modify: `CHANGELOG.md`
- Modify: `docs/entities.md`
- Modify: `docs/data-and-privacy.md`
- Modify: `docs/actions-and-history.md`
- Modify: `docs/troubleshooting.md`
- Modify: `docs/upgrading-and-removal.md`
- Modify: `custom_components/resiyhome_health_sync/manifest.json`
- Test: `tests/test_documentation.py`
- Test: `tests/test_packaging.py`
- Test: `tests/test_public_release.py`

**Interfaces:**
- Produces: public beta version `1.2.0-beta.1` and complete user-facing
  documentation.

- [ ] **Step 1: Add failing packaging and documentation assertions**

Require:

- manifest version `1.2.0-beta.1`
- changelog entry for every new entity group
- explicit “no reauthorization required”
- disabled-by-default entity list
- elevation-gain clarification
- GPS privacy statement
- 30-day enrichment explanation
- existing ID/history preservation statement

- [ ] **Step 2: Verify RED**

```bash
.venv/bin/python -m pytest tests/test_documentation.py tests/test_packaging.py tests/test_public_release.py -v
```

- [ ] **Step 3: Update documentation and manifest**

Document unavailable behavior when Google supplies no record, exact units,
details attribute caps, latest-workout cross-day selection, enrichment status,
and the future release boundary for remaining sleep and nutrition work.

- [ ] **Step 4: Run focused release tests**

```bash
.venv/bin/python -m pytest tests/test_documentation.py tests/test_packaging.py tests/test_public_release.py -v
```

Expected: pass.

- [ ] **Step 5: Run the complete verification gate**

```bash
.venv/bin/python -m pytest tests -v
.venv/bin/python -m ruff check custom_components/resiyhome_health_sync tests
.venv/bin/python -m compileall custom_components/resiyhome_health_sync tests
.venv/bin/python scripts/verify_public_release.py .
git diff --check
git status --short
```

Expected: all tests pass, Ruff reports no issues, compileall exits zero, public
release verification exits zero without findings, diff check is clean, and
only intentional files are modified.

- [ ] **Step 6: Commit the beta preparation**

```bash
git add README.md CHANGELOG.md docs custom_components/resiyhome_health_sync/manifest.json tests/test_documentation.py tests/test_packaging.py tests/test_public_release.py
git commit -m "chore: prepare Health Sync 1.2.0 beta 1"
```

- [ ] **Step 7: Perform final review before publication**

Use `superpowers:requesting-code-review`, address accepted findings with fresh
RED/GREEN cycles, rerun the complete verification gate, and use
`superpowers:verification-before-completion` before pushing or opening a pull
request. Do not publish, tag, merge, deploy to Home Assistant, or create a
GitHub release without a separate explicit user approval after beta
verification.
