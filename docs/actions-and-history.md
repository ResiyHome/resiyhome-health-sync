# Actions and History

Health Sync exposes two Home Assistant actions and one authenticated WebSocket command.
All interfaces select a person by the stable person slug. The config entry title does not
reliably display this slug, and the runtime does not expose it in entity attributes or
diagnostics.

To find the slug, open the Health Sync device and inspect an entity that still has its
default entity ID, such as Steps today. Its stable unique ID and default entity ID end in a
known runtime key. The slug is the prefix before a known key such as `_steps_today`: for
example, the default entity ID shape is `sensor.<person_slug>_steps_today`, and the stable
unique ID shape is `<person_slug>_steps_today`. A manually renamed entity ID may hide this
pattern, so use another unrenamed Health Sync entity or inspect the stable unique ID with
trusted Home Assistant entity-registry tooling. Do not guess a slug or post it publicly.

## `resiyhome_health_sync.refresh`

Use this action to request current data for one loaded person.

| Field | Required | Meaning |
| --- | --- | --- |
| `person` | Yes | Exact stable person slug |

The action uses a five-minute manual cooldown. Calls during the cooldown return the
current coordinator snapshot without another Google poll. The normal scheduled poll runs
every 15 minutes. Manual refresh does not bypass OAuth failures, validation, partial-group
handling, or redaction.

## `resiyhome_health_sync.probe_optional_data_types`

This advanced diagnostic action checks whether candidate Google Health data types are
available before the integration adds permanent polling for them.

| Field | Required | Meaning |
| --- | --- | --- |
| `person` | Yes | Exact stable person slug |
| `days` | No | Integer lookback from 1 through 14; default 7 |

The response contains each data type's status, raw count, all-source count, wearable
count, and normalized platform labels. It never returns health values, OAuth credentials,
Google identifiers, individual samples, or raw Google API payloads. `requires_rollup`
means the candidate cannot be checked with list or reconcile operations; it does not prove
that the account has no data. The probe runs only on request and does not change the
normal 15-minute polling set.

## `resiyhome_health_sync/history`

The history interface is an authenticated Home Assistant WebSocket command intended for
dashboards and other trusted Home Assistant clients. It returns locally stored normalized
daily summaries, not Home Assistant recorder states and not Google responses.

## Request fields

| Field | Type | Meaning |
| --- | --- | --- |
| `type` | string | Exactly `resiyhome_health_sync/history` |
| `person` | string | Exact stable person slug |
| `start_date` | string | Inclusive ISO calendar date |
| `end_date` | string | Inclusive ISO calendar date |
| `metrics` | list of strings | Optional allowlist; omitting it returns default core metrics |

Core history queries are bounded to the provider's 20-year history window. Queries that
request any expanded metric are limited to at most 90 calendar dates. Dates must be
canonical `YYYY-MM-DD` strings and the start must not follow the end.

### Core metric keys

`steps`, `fitbit_steps`, `distance_m`, `active_energy_kcal`, `exercise_minutes`,
`sleep_minutes`, `resting_heart_rate`, `average_heart_rate`, `minimum_heart_rate`,
`maximum_heart_rate`, `hrv_ms`, `source`, `complete`, and `updated_at`.

The default set contains all core keys except `updated_at`.

### Expanded metric keys

`active_zone_minutes`, `vo2_max`, `vo2_estimated`, `cardio_fitness_level`,
`oxygen_average`, `oxygen_lower_bound`, `oxygen_upper_bound`,
`oxygen_standard_deviation`, `daily_respiratory_rate`, `sleep_respiratory_rates`,
`sleep_respiratory_standard_deviation`, `sleep_respiratory_signal_to_noise`, `floors`,
`sedentary_minutes`, `heart_zone_minutes`, `heart_zone_thresholds`,
`heart_zone_calories`, and `weight_kg`.

`weight_kg` is returned as unavailable unless `include_body_measurements` is enabled for
that person. Mapping and tuple values are converted to JSON-safe objects and lists. Every
record includes its normalized date. Unsupported metrics, unknown people, invalid dates,
and excessive ranges return a generic command error without exposing private data.

## Recorder history

Home Assistant may separately retain entity state history through Recorder. Recorder
retention, exclusion, purge, and backup policies are controlled by Home Assistant and are
not changed by this integration. Removing a config entry or opting out of weight does not
automatically erase recorder history already stored by Home Assistant.
