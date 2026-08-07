# Health Sync 1.2.0 Activity and Workouts Design

**Status:** Approved direction; written specification pending final user review

**Release objective:** Add comprehensive workout, activity-level, elevation,
VO2 max, swimming, and sleep-efficiency support without changing existing
entity identities, OAuth scopes, normalized history semantics, or baseline
refresh reliability.

## Release Sequence

Version 1.2.0 is the comprehensive activity and workouts release.

- 1.2.0: workout summaries and details, daily activity levels, elevation gain,
  standard and running VO2 max, swimming, and sleep efficiency.
- Next release: remaining useful sleep metrics.
- Following release: additional nutrition detail.

The later sleep and nutrition work is explicitly outside 1.2.0. Version 1.2.0
must leave additive extension points for those releases without pre-creating
unused fields or entities.

## Constraints

- Preserve every existing config entry, entity unique ID, entity ID, Recorder
  history row, and normalized history row.
- Keep the existing read-only activity and fitness OAuth permission. Version
  1.2.0 must not require reauthorization.
- Add no direct Apple Health, Fitbit, GPS route, webhook, or write API
  integration.
- Never store or expose Google data-point resource names, account identifiers,
  access tokens, refresh tokens, or GPS coordinates.
- Optional Google fields must fail independently. One malformed metric cannot
  invalidate an otherwise valid workout or daily summary.
- Existing sensors retain their current keys, names, units, defaults, and
  behavior.
- New advanced or sparsely populated sensors are disabled by default.
- All production behavior is developed test-first.

## Official API Basis

The design follows Google's documented v4 contracts:

- Exercise sessions contain intervals, active duration, summary metrics,
  splits, events, and metadata:
  <https://developers.google.com/health/data-types/workouts>
- Exercise `metricsSummary` can include calories, distance, steps, average
  speed, average pace, average heart rate, elevation gain, Active Zone Minutes,
  heart-rate-zone durations, running VO2 max, total swim lengths, and advanced
  running mobility metrics:
  <https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints>
- Activity-level intervals use `SEDENTARY`, `LIGHTLY_ACTIVE`,
  `MODERATELY_ACTIVE`, and `VERY_ACTIVE`.
- Altitude records represent elevation-gain deltas in millimeters, not height
  above sea level.
- Standard VO2 max and running VO2 max are sample data types.
- Swim-length records contain an interval, stroke type, and stroke count.

## Data Model

### Workout summary

Extend `WorkoutSummary` with optional fields. Existing stored workouts remain
valid when every new field is absent.

- `display_name: str | None`
- `active_energy_kcal: float | None` (existing)
- `distance_m: float | None`
- `steps: int | None`
- `average_heart_rate: float | None`
- `maximum_heart_rate: float | None`
- `active_zone_minutes: float | None`
- `average_speed_mps: float | None`
- `average_pace_seconds_per_km: float | None`
- `elevation_gain_m: float | None`
- `run_vo2_max: float | None`
- `total_swim_lengths: int | None`
- `heart_zone_minutes: Mapping[str, float]`
- `mobility: WorkoutMobilityMetrics | None`
- `splits: tuple[WorkoutSplit, ...]`
- `events: tuple[WorkoutEvent, ...]`
- `split_count: int`
- `event_count: int`
- `details_truncated: bool`
- `has_gps: bool | None`

`WorkoutMobilityMetrics` contains optional normalized fields for average ground
contact time, cadence, stride length, vertical oscillation, and vertical ratio.
These values are detail attributes, not independent entities in 1.2.0.

`WorkoutSplit` contains only sanitized split type, start, end, active duration,
and the same optional scalar summary fields that Google supplies for that
split. `WorkoutEvent` contains only event type and event time.

Store no more than 50 splits and 50 events per workout. Preserve the original
validated counts and set `details_truncated` when either source array exceeds
its cap.

### Daily activity expansion

Extend `ExpandedDailyMetrics` with:

- `activity_level_minutes: Mapping[str, float]`
- `latest_activity_level: str | None`
- `latest_activity_level_at: datetime | None`
- `elevation_gain_m: float | None`
- `latest_vo2_max: float | None`
- `latest_vo2_max_method: str | None`
- `latest_vo2_max_at: datetime | None`
- `latest_run_vo2_max: float | None`
- `latest_run_vo2_max_at: datetime | None`
- `swim_lengths: int | None`
- `swim_strokes: int | None`
- `swim_strokes_by_type: Mapping[str, int]`
- `primary_swim_stroke: str | None`

Continue using the existing `daily_vo2_max` fields unchanged.

### Latest workout selection

Add `latest_workout: WorkoutSummary | None` to `CoordinatorSnapshot`. Rebuild
it from the newest valid workout across loaded normalized history and the
current refresh. The existing `last_workout_type` and `last_workout_duration`
entities and every new last-workout entity use this same selected object.
Crossing midnight therefore does not make the last workout unavailable merely
because no new workout occurred today.

### Sleep efficiency

Add `sleep_efficiency_percentage: float | None` to `DailySummary`.

Calculate:

```text
sleep_minutes / sleep_period_minutes * 100
```

The result is unavailable when either input is missing, the sleep period is
zero, asleep minutes exceed the sleep period, or the result is non-finite.
A true zero remains valid when Google supplies a valid non-zero sleep period.

## Fetch and Normalization

### Existing exercise request

Do not add another exercise endpoint for metrics already present in the
reconciled exercise payload. Extend the existing parser so each optional
summary field is validated independently.

Maximum workout heart rate is not a documented exercise summary field. Derive
it from valid reconciled heart-rate samples whose timestamps fall within the
workout interval. If no valid in-window samples exist, leave it unavailable.
Never substitute the daily maximum.

### New daily activity requests

Add these data types to the existing activity capability:

- `activity-level`
- `altitude`
- `vo2-max`
- `run-vo2-max`
- `swim-lengths-data`

Use reconciled all-source records for current-day values. Clamp intervals to
the local-day boundary before summing durations. Reject future, inverted,
non-finite, unknown-enum, or out-of-day records individually.

Activity-level minute totals are calculated from reconciled intervals because
Google documents list and reconcile support for this data type. The latest
activity level is the valid interval with the most recent end time not later
than the refresh time. Its timestamp is exposed as `observed_at`; the entity is
not described as a guaranteed live state.

Altitude is normalized as meters of elevation gain. The integration must never
label it as altitude above sea level.

For standard and running VO2 max, retain the latest valid sample at or before
the refresh time and expose its measurement timestamp. Standard VO2 max also
retains the documented measurement method.

For swimming, count valid swim-length records, sum stroke counts, retain a
per-stroke breakdown, and choose the primary stroke by greatest stroke count.
Ties use the fixed order freestyle, backstroke, breaststroke, butterfly. An
unspecified stroke contributes to total strokes but cannot become the primary
stroke.

### Recent workout enrichment

Do not restart or rewind the 20-year core backfill. Add an independently
versioned, bounded enrichment cursor that re-reads the most recent 30 days of
exercise and heart-rate data after migration. Current refreshes continue while
enrichment runs.

The enrichment pass may add missing optional fields to matching stored
workouts but must not delete or replace existing valid workout type, duration,
start, end, or calories. Match a workout by normalized start, end, and activity
type; do not persist a Google resource identifier.

Enrichment completes in bounded day windows and records progress separately so
an interruption resumes safely. A permanently absent optional field is a
successful absence, not an error.

## Home Assistant Entities

### Enabled by default

- `last_workout_calories`
- `last_workout_start`
- `last_workout_end`
- `last_workout_distance`
- `last_workout_steps`
- `last_workout_average_heart_rate`
- `last_workout_maximum_heart_rate`
- `sleep_efficiency`
- `lightly_active_minutes_today`
- `moderately_active_minutes_today`
- `very_active_minutes_today`
- `latest_activity_level`
- `elevation_gain_today`

### Disabled by default

- `last_workout_active_zone_minutes`
- `last_workout_average_speed`
- `last_workout_average_pace`
- `last_workout_elevation_gain`
- `last_workout_details`
- `latest_vo2_max`
- `latest_running_vo2_max`
- `swim_lengths_today`
- `swim_strokes_today`
- `primary_swim_stroke_today`

The existing `total_calories_burned_today`, `daily_vo2_max`,
`active_zone_minutes_today`, `floors_today`, `sedentary_minutes_today`, and
heart-rate-zone sensors remain unchanged. Do not create duplicates.

### Last workout details

`last_workout_details` uses the workout display name as its state and falls
back to the normalized activity type. Its bounded attributes contain:

- summary scalar values and heart-rate-zone durations
- sanitized running mobility metrics
- `has_gps` as a boolean only
- split and event counts
- at most 50 sanitized split objects
- at most 50 sanitized event objects
- `details_truncated`

It never exposes route data or Google resource identifiers.

### Units and classes

- Energy: `kcal`
- Distance and elevation: meters, allowing Home Assistant unit conversion
- Speed: meters per second
- Pace: seconds per kilometer
- Heart rate: `bpm`
- Duration: minutes
- VO2 max: `mL/kg/min`
- Sleep efficiency: percent
- Start/end/observation times: timestamp device class
- Steps, lengths, and strokes: non-negative totals

## Storage Migration

Bump the private normalized store from schema v3 to schema v4.

The v3-to-v4 migration:

1. Retains every existing top-level row and scalar value.
2. Adds absent expanded fields with empty or unavailable defaults.
3. Extends each workout with optional detail fields and empty detail tuples.
4. Resets only `expanded_backfill_cursor` to `None` so the existing bounded
   90-day expanded pass can populate the new activity, VO2, elevation, and
   swimming fields without rewinding the 20-year core cursor.
5. Adds a separate recent-workout-enrichment cursor. Completion is derived in
   the coordinator when the cursor reaches the fixed 30-day boundary.
6. Validates the migrated object before the first v4 write.
7. Writes transactionally and reads the result back before declaring migration
   success.

Malformed legacy data fails closed without partially rewriting the store.
Disabling or lacking a new sensor does not erase an existing workout.

## History and WebSocket

Add the new stable scalar daily fields to authenticated history allowlists.
Detailed workout splits and events are not included in ordinary daily history
rows. A last-workout history response may expose only sanitized summary values,
detail counts, and `details_truncated`.

Existing WebSocket command names, authentication requirements, person/date
validation, metric validation, result limits, and recursive redaction remain
unchanged.

## Diagnostics and Logging

Diagnostics report only:

- availability booleans for new scalar metrics
- counts of workouts, splits, events, and swimming records
- whether workout details were truncated
- enrichment status and cursor
- sanitized error categories

Diagnostics never include health values, workout names, timestamps from health
records, raw arrays, platform identifiers, tokens, routes, or Google resource
names. Routine capability absence remains debug-level logging.

## Failure Isolation

- A failed new data type retains the prior valid value for that metric group.
- Exercise summary fields are optional and independently validated.
- Malformed splits or events are dropped individually and counted as invalid;
  they do not invalidate the workout.
- A failed enrichment pass does not block current-day polling.
- Missing swimming or VO2 data is normal and does not mark authorization
  unhealthy.
- No new metric changes `complete`, stale-data, or authorization semantics for
  existing baseline groups.

## Testing

Test-first coverage must include:

- every new documented payload field and unit conversion
- malformed, missing, negative, non-finite, future, and out-of-range values
- heart-rate maximum derivation inside and outside workout boundaries
- split/event caps, truncation, ordering, and redaction
- activity intervals crossing midnight and local-day clamping
- activity-level tie and latest-observation behavior
- VO2 method enums and sample-time ordering
- swimming stroke aggregation and deterministic primary-stroke ties
- sleep efficiency including true zero and invalid denominators
- v3-to-v4 migration, transactional failure, and round-trip compatibility
- bounded enrichment resume, idempotence, and non-destructive merge behavior
- entity IDs, defaults, units, state classes, translations, and unavailable
  behavior
- partial API failures and retained prior values
- diagnostics and WebSocket privacy gates
- packaging, documentation, public-release identity, and full regression suite

## Documentation

Update README, entities, data/privacy, actions/history, troubleshooting,
upgrading, and changelog documentation.

Release notes must state:

- no new OAuth scope or reauthorization is required for 1.2.0
- new advanced entities may be disabled by default
- values appear only when Google supplies the corresponding records
- elevation means gain, not sea-level altitude
- workout GPS routes are not read or stored
- a one-time bounded 30-day workout enrichment may run after upgrade
- existing entity IDs and stored history are preserved

## Acceptance Criteria

Version 1.2.0 is ready for beta only when:

- all new behavior has failing-test evidence followed by passing tests
- the full existing test suite passes without deleted compatibility assertions
- Ruff, compileall, packaging, and public-release checks pass
- a v3 fixture migrates to v4 without changing any pre-existing value
- current refresh continues during enrichment
- diagnostics contain no health values or identifiers
- Home Assistant loads with the same existing entities before new entities are
  enabled
- live testing confirms at least one real workout summary and verifies graceful
  unavailability for data Google does not supply
