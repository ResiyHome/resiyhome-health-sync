# Entity Catalog

Each config entry creates one service device for one person. Entity unique IDs combine
the stable person slug derived from the enrolled person name with the runtime
key below. The setup UI does not ask for a slug. Home Assistant may prepend
the entry name to the generated entity ID. This slug identifies entities and
service or action targets within the existing entry; normalized history is
stored under the Home Assistant config-entry ID instead.

This implementation adds eight static person entity keys:
`total_calories_burned_today`, `sleep_time_in_bed`,
`sleep_time_to_fall_asleep`, `sleep_time_after_waking`, `body_fat`, `height`,
`calories_consumed_today`, and `water_consumed_today`. Paired-device entities
are dynamic because each authorized Google account can return a different set
of trackers and scales.

## Reading the tables

- **Default:** Enabled entities are created active. Disabled entities remain in the
  entity registry until the user enables them.
- **Reconciled:** Values come from Google's all-source reconcile result. Raw records are
  used only to classify source platforms, never summed into health values.
- **Rollup:** Values come from Google's all-source daily rollup. Current-day
  interval fallbacks are used only where the catalog explicitly says so, and a
  published rollup takes precedence.
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
| `total_calories_burned_today` | Total calories burned today | kcal | Enabled | All-source `total-calories` daily rollup; prior activity group on partial failure |
| `exercise_minutes_today` | Exercise minutes today | min | Enabled | Reconciled active-minute intervals; prior group on partial failure |
| `last_sleep_duration` | Last sleep duration | min | Enabled | Latest valid reconciled sleep session; unavailable when no valid session |
| `sleep_time_in_bed` | Sleep time in bed | min | Enabled | `minutesInSleepPeriod` from the same latest valid reconciled sleep session |
| `sleep_time_to_fall_asleep` | Sleep time to fall asleep | min | Enabled | `minutesToFallAsleep` from the same latest valid reconciled sleep session |
| `sleep_time_after_waking` | Sleep time after waking | min | Enabled | `minutesAfterWakeUp` from the same latest valid reconciled sleep session |
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
| `active_zone_minutes_today` | Active zone minutes today | min | Enabled | Sum of fat-burn, cardio, and peak daily-rollup fields; reconciled current-day intervals until the rollup is published |
| `daily_vo2_max` | Daily VO2 max | mL/kg/min | Enabled | One complete reconciled daily summary; exposes fitness level and estimated metadata when present |
| `daily_oxygen_saturation` | Daily oxygen saturation | % | Enabled | One complete reconciled daily summary; requires valid average, bounds, and standard deviation |
| `daily_respiratory_rate` | Daily respiratory rate | breaths/min | Enabled | One complete reconciled daily summary |
| `sleep_respiratory_rate` | Sleep respiratory rate | breaths/min | Enabled | Complete full-sleep reconciled summary; exposes standard deviation and signal-to-noise metadata |
| `floors_today` | Floors today | floors | Enabled | All-source daily rollup; sum of reconciled current-day floor intervals until the rollup is published |
| `sedentary_minutes_today` | Sedentary minutes today | min | Enabled | All-source daily rollup converted from duration; sum of reconciled current-day interval durations until the rollup is published |
| `heart_rate_zone_minutes_today` | Heart rate zone minutes today | min | Enabled | Sum of available all-source heart-zone daily-rollup durations; reconciled current-day interval durations until the rollup is published |

Expanded groups use no substitute metric when their required response shape is missing or
invalid. A partial refresh preserves the prior normalized group; otherwise the entity is
unavailable.

## Nutrition sensors

These entities are first registered only after `include_nutrition` is enabled
for the person and Google grants
`https://www.googleapis.com/auth/googlehealth.nutrition.readonly`.

| Runtime key | Name | Unit | Default | Source and fallback |
| --- | --- | --- | --- | --- |
| `calories_consumed_today` | Calories consumed today | kcal | Enabled after opt-in | Sum of current local day's all-source `nutrition-log` energy; prior current-day nutrition group on temporary failure |
| `water_consumed_today` | Water consumed today | mL | Enabled after opt-in | Sum of current local day's all-source `hydration-log` amount consumed; prior current-day nutrition group on temporary failure |

Nutrition has no historical backfill in this release. Daily normalized
nutrition begins with the first successful opt-in refresh. Health Sync does
not infer food intake from calories burned or hydration from another metric.
If nutrition is later disabled, retained nutrition entities become
unavailable and no future nutrition requests are made.

## Detailed sensors disabled by default

| Runtime key | Name | Unit | Default | Source and fallback |
| --- | --- | --- | --- | --- |
| `active_zone_fat_burn_minutes_today` | Active zone fat burn minutes today | min | Disabled | Fat-burn rollup field with reconciled current-day interval fallback |
| `active_zone_cardio_minutes_today` | Active zone cardio minutes today | min | Disabled | Cardio rollup field with reconciled current-day interval fallback |
| `active_zone_peak_minutes_today` | Active zone peak minutes today | min | Disabled | Peak rollup field with reconciled current-day interval fallback |
| `heart_rate_zone_light_minutes_today` | Heart rate zone light minutes today | min | Disabled | Light-zone rollup with reconciled current-day interval fallback; threshold attributes from reconciled daily zones when available |
| `heart_rate_zone_moderate_minutes_today` | Heart rate zone moderate minutes today | min | Disabled | Moderate-zone rollup with reconciled current-day interval fallback; threshold attributes from reconciled daily zones when available |
| `heart_rate_zone_vigorous_minutes_today` | Heart rate zone vigorous minutes today | min | Disabled | Vigorous-zone rollup with reconciled current-day interval fallback; threshold attributes from reconciled daily zones when available |
| `heart_rate_zone_peak_minutes_today` | Heart rate zone peak minutes today | min | Disabled | Peak-zone rollup with reconciled current-day interval fallback; threshold attributes from reconciled daily zones when available |
| `sleep_deep_respiratory_rate` | Sleep deep respiratory rate | breaths/min | Disabled | Deep-phase value from complete sleep respiratory summary |
| `sleep_light_respiratory_rate` | Sleep light respiratory rate | breaths/min | Disabled | Light-phase value from complete sleep respiratory summary |
| `sleep_rem_respiratory_rate` | Sleep rem respiratory rate | breaths/min | Disabled | REM-phase value from complete sleep respiratory summary |
| `heart_rate_zone_light_calories_today` | Heart rate zone light calories today | kcal | Disabled | Light-zone all-source daily rollup |
| `heart_rate_zone_moderate_calories_today` | Heart rate zone moderate calories today | kcal | Disabled | Moderate-zone all-source daily rollup |
| `heart_rate_zone_vigorous_calories_today` | Heart rate zone vigorous calories today | kcal | Disabled | Vigorous-zone all-source daily rollup |
| `heart_rate_zone_peak_calories_today` | Heart rate zone peak calories today | kcal | Disabled | Peak-zone all-source daily rollup |
| `weight` | Weight | kg | Disabled | Latest valid reconciled weight sample; no value unless body measurements are opted in |
| `body_fat` | Body-fat percentage | % | Disabled | Latest valid reconciled `body-fat` percentage; no value unless body measurements are opted in |
| `height` | Height | m | Disabled | Latest valid reconciled height converted from millimeters; no value unless body measurements are opted in |

Weight, Body-fat percentage, and Height are created
disabled by default in the entity registry. Enable each body-measurement entity in the entity registry
before using it. Every body entity has two gates:
`include_body_measurements` must be enabled for that person, and the entity
itself must be enabled in Home Assistant.

After it is enabled, the entity may remain unavailable until body measurements are opted in
and Google supplies usable data.
Opt-in starts a bounded 90-day normalized backfill for all three body
measurements. The `measurement_date` attribute identifies the latest normalized
sample date. Opting out transactionally removes weight, body-fat, and height
values from normalized integration history and the current snapshot, but does
not purge prior Home Assistant Recorder states or backups.

## Dynamic paired-device entities

After `include_paired_devices` is enabled and Google grants
`https://www.googleapis.com/auth/googlehealth.settings.readonly`, Health Sync
creates one Home Assistant service device for each returned tracker or scale.
It creates one battery and one last-sync entity per paired device:

| Dynamic runtime key | Display meaning | Unit or class | Source |
| --- | --- | --- | --- |
| `battery_level` | Battery level | % battery | Current v4 `batteryLevel`; `battery_status` attribute from `batteryStatus` |
| `last_device_sync` | Paired-device last sync | timestamp | Current v4 `lastSyncTime` |

Google's current v4 paired-device contract uses `deviceVersion` for the device
product/model, `batteryStatus` with `High`, `Medium`, `Low`, or `Empty`,
integer `batteryLevel`, and RFC 3339 `lastSyncTime`. Health Sync retains only
those sanitized values, device type, and a one-way identity digest. It does
not retain the raw Google resource name, MAC address, or feature list.

Paired devices are current metadata only and never enter normalized daily
history. A temporarily missing device or disabled option leaves an existing
entity-registry row unavailable instead of creating a new identity later.

## Activity and synchronization sensors

| Runtime key | Name | Unit | Default | Source and fallback |
| --- | --- | --- | --- | --- |
| `last_workout_type` | Last workout type | none | Enabled | Activity type from the latest valid reconciled workout; unavailable if none |
| `last_workout_duration` | Last workout duration | min | Enabled | Duration from the latest valid reconciled workout; unavailable if none |
| `current_source` | Current source | none | Enabled | Local enum classification: `fitbit`, `apple_fallback`, `mixed`, or `unavailable` |
| `last_successful_synchronization` | Last successful synchronization | none | Enabled | Health Sync API refresh time for the latest successful current Google poll |
| `backfill_status` | Backfill status | none | Enabled | Local enum state: `in_progress` or `complete` |
| `backfill_cursor` | Backfill cursor | none | Enabled | Local date for the oldest completed core-history boundary; unavailable before a cursor exists |

Health Sync API refresh time and Fitbit mobile-device sync time are different
clocks. `last_successful_synchronization` reports when Health Sync last
completed a Google API refresh. A paired device's `last_device_sync` reports
Google's `lastSyncTime`, which the v4 contract defines as the last sync with
the Fitbit mobile application. Health Sync does not initiate wearable or
mobile synchronization.

## Binary sensors

Binary sensors remain available during failures so they can report the problem.

| Runtime key | Name | Default | On means | Attributes |
| --- | --- | --- | --- | --- |
| `health_data_stale` | Health data stale | Enabled | No successful refresh exists or the last success is more than 45 minutes old | `last_success` when known |
| `health_authorization_problem` | Health authorization problem | Enabled | The current OAuth authorization is unhealthy | `last_attempt` when known |
