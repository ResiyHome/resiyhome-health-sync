# Entity Catalog

Each config entry creates one service device for one person. Entity unique IDs combine
the stable person slug with the runtime key below. Home Assistant may prepend the entry
name to the generated entity ID.

## Reading the tables

- **Default:** Enabled entities are created active. Disabled entities remain in the
  entity registry until the user enables them.
- **Reconciled:** Values come from Google's all-source reconcile result. Raw records are
  used only to classify source platforms, never summed into health values.
- **Rollup:** Values come from Google's all-source daily rollup.
- **Partial failure:** A failed metric group keeps its prior normalized value when one
  exists. A first fetch with no usable value is unavailable.
- **Zero:** A valid provider zero remains zero. Missing, malformed, incomplete, or absent
  data is not changed to zero; the sensor is unavailable instead.

## Core daily sensors

| Runtime key | Name | Unit | Default | Source and fallback |
| --- | --- | --- | --- | --- |
| `steps_today` | Steps today | steps | Enabled | Reconciled all-source steps; prior group on partial failure |
| `fitbit_steps_today` | Fitbit steps today | steps | Enabled | Google-wearables reconciled steps; unavailable when no wearable result |
| `distance_today` | Distance today | m | Enabled | Reconciled distance converted from millimeters; prior group on partial failure |
| `active_energy_today` | Active energy today | kcal | Enabled | Reconciled active energy; prior group on partial failure |
| `exercise_minutes_today` | Exercise minutes today | min | Enabled | Reconciled active-minute intervals; prior group on partial failure |
| `last_sleep_duration` | Last sleep duration | min | Enabled | Latest valid reconciled sleep session; unavailable when no valid session |
| `sleep_awake_duration` | Sleep awake duration | min | Enabled | Awake stage from latest valid reconciled sleep session |
| `sleep_rem_duration` | Sleep REM duration | min | Enabled | REM stage from latest valid reconciled sleep session |
| `sleep_light_duration` | Sleep light duration | min | Enabled | Light stage from latest valid reconciled sleep session |
| `sleep_deep_duration` | Sleep deep duration | min | Enabled | Deep stage from latest valid reconciled sleep session |
| `resting_heart_rate` | Resting heart rate | bpm | Enabled | Latest reconciled daily resting-heart-rate value |
| `average_heart_rate` | Average heart rate | bpm | Enabled | Calculated from valid reconciled heart-rate samples |
| `minimum_heart_rate` | Minimum heart rate | bpm | Enabled | Calculated from valid reconciled heart-rate samples |
| `maximum_heart_rate` | Maximum heart rate | bpm | Enabled | Calculated from valid reconciled heart-rate samples |
| `heart_rate_variability` | Heart rate variability | ms | Enabled | Reconciled daily HRV, with valid sample-form HRV fallback |

Core sensors include normalized `source`, `complete`, `summary_date`, and, when known,
`data_updated_at` attributes. The source is `fitbit`, `apple_fallback`, `mixed`, or
`unavailable`. `apple_fallback` means Google supplied canonical values while raw platform
metadata indicated HealthKit and no Google-wearables steps were available. Health Sync
does not connect directly to Apple Health.

## Expanded sensors enabled by default

| Runtime key | Name | Unit | Default | Source and fallback |
| --- | --- | --- | --- | --- |
| `active_zone_minutes_today` | Active zone minutes today | min | Enabled | Sum of complete fat-burn, cardio, and peak daily-rollup fields |
| `daily_vo2_max` | Daily VO2 max | mL/kg/min | Enabled | One complete reconciled daily summary; exposes fitness level and estimated metadata when present |
| `daily_oxygen_saturation` | Daily oxygen saturation | % | Enabled | One complete reconciled daily summary; requires valid average, bounds, and standard deviation |
| `daily_respiratory_rate` | Daily respiratory rate | breaths/min | Enabled | One complete reconciled daily summary |
| `sleep_respiratory_rate` | Sleep respiratory rate | breaths/min | Enabled | Complete full-sleep reconciled summary; exposes standard deviation and signal-to-noise metadata |
| `floors_today` | Floors today | floors | Enabled | All-source daily rollup |
| `sedentary_minutes_today` | Sedentary minutes today | min | Enabled | All-source daily rollup converted from duration |
| `heart_rate_zone_minutes_today` | Heart rate zone minutes today | min | Enabled | Sum of available all-source heart-zone daily-rollup durations |

Expanded groups use no substitute metric when their required response shape is missing or
invalid. A partial refresh preserves the prior normalized group; otherwise the entity is
unavailable.

## Detailed sensors disabled by default

| Runtime key | Name | Unit | Default | Source and fallback |
| --- | --- | --- | --- | --- |
| `active_zone_fat_burn_minutes_today` | Active zone fat burn minutes today | min | Disabled | Fat-burn field from complete active-zone daily rollup |
| `active_zone_cardio_minutes_today` | Active zone cardio minutes today | min | Disabled | Cardio field from complete active-zone daily rollup |
| `active_zone_peak_minutes_today` | Active zone peak minutes today | min | Disabled | Peak field from complete active-zone daily rollup |
| `heart_rate_zone_light_minutes_today` | Heart rate zone light minutes today | min | Disabled | Light-zone daily rollup; threshold attributes from reconciled daily zones when available |
| `heart_rate_zone_moderate_minutes_today` | Heart rate zone moderate minutes today | min | Disabled | Moderate-zone daily rollup; threshold attributes from reconciled daily zones when available |
| `heart_rate_zone_vigorous_minutes_today` | Heart rate zone vigorous minutes today | min | Disabled | Vigorous-zone daily rollup; threshold attributes from reconciled daily zones when available |
| `heart_rate_zone_peak_minutes_today` | Heart rate zone peak minutes today | min | Disabled | Peak-zone daily rollup; threshold attributes from reconciled daily zones when available |
| `sleep_deep_respiratory_rate` | Sleep deep respiratory rate | breaths/min | Disabled | Deep-phase value from complete sleep respiratory summary |
| `sleep_light_respiratory_rate` | Sleep light respiratory rate | breaths/min | Disabled | Light-phase value from complete sleep respiratory summary |
| `sleep_rem_respiratory_rate` | Sleep rem respiratory rate | breaths/min | Disabled | REM-phase value from complete sleep respiratory summary |
| `heart_rate_zone_light_calories_today` | Heart rate zone light calories today | kcal | Disabled | Light-zone all-source daily rollup |
| `heart_rate_zone_moderate_calories_today` | Heart rate zone moderate calories today | kcal | Disabled | Moderate-zone all-source daily rollup |
| `heart_rate_zone_vigorous_calories_today` | Heart rate zone vigorous calories today | kcal | Disabled | Vigorous-zone all-source daily rollup |
| `heart_rate_zone_peak_calories_today` | Heart rate zone peak calories today | kcal | Disabled | Peak-zone all-source daily rollup |
| `weight` | Weight | kg | Disabled | Latest valid reconciled weight sample; no value unless body measurements are opted in |

The `weight` sensor has two gates: `include_body_measurements` must be enabled for that
person, and the entity itself must be enabled in Home Assistant.
Weight is disabled by default in the entity registry.
After a user enables the entity, it may remain unavailable until body measurements are opted in
and Google supplies usable weight data. Opt-in permits only weight, triggers a bounded
90-day normalized weight backfill, and does not enable other body measurements. The
`measurement_date` attribute identifies the normalized sample date. Opting out removes
weight from integration history and the current snapshot, but does not purge prior Home
Assistant recorder states.

## Activity and synchronization sensors

| Runtime key | Name | Unit | Default | Source and fallback |
| --- | --- | --- | --- | --- |
| `last_workout_type` | Last workout type | none | Enabled | Activity type from the latest valid reconciled workout; unavailable if none |
| `last_workout_duration` | Last workout duration | min | Enabled | Duration from the latest valid reconciled workout; unavailable if none |
| `current_source` | Current source | none | Enabled | Local enum classification: `fitbit`, `apple_fallback`, `mixed`, or `unavailable` |
| `last_successful_synchronization` | Last successful synchronization | none | Enabled | Local timestamp of the latest successful current refresh |
| `backfill_status` | Backfill status | none | Enabled | Local enum state: `in_progress` or `complete` |
| `backfill_cursor` | Backfill cursor | none | Enabled | Local date for the oldest completed core-history boundary; unavailable before a cursor exists |

## Binary sensors

Binary sensors remain available during failures so they can report the problem.

| Runtime key | Name | Default | On means | Attributes |
| --- | --- | --- | --- | --- |
| `health_data_stale` | Health data stale | Enabled | No successful refresh exists or the last success is more than 45 minutes old | `last_success` when known |
| `health_authorization_problem` | Health authorization problem | Enabled | The current OAuth authorization is unhealthy | `last_attempt` when known |
