# Health Sync Performance Diagnostics Design

## Purpose

Add bounded, value-free performance instrumentation to Health Sync so users who
report CPU spikes can provide evidence that can be correlated with the
integration's 15-minute refresh cycle. The instrumentation must not create a
new performance problem, expose health information, or add visible Home
Assistant entities.

## Scope

This change adds performance information only to Home Assistant's downloadable
config-entry diagnostics. It does not change the polling interval, fetch
behavior, backfill behavior, sensor model, history retention, or OAuth scopes.

The change must not:

- create diagnostic sensors or other entities;
- write performance records to the Home Assistant recorder;
- persist performance records in integration storage;
- make additional Google API requests;
- retain raw Google responses, URLs, query parameters, resource names, device
  identifiers, account identifiers, person names, health values, or OAuth
  material;
- log routine performance records at warning or info level.

## Architecture

Each config entry owns an in-memory performance recorder with a fixed maximum
of 12 completed operation records. At a 15-minute polling interval, this
captures approximately three hours of scheduled refresh activity. The recorder
is created with the entry runtime and is discarded when the entry unloads or
Home Assistant restarts.

Instrumentation is split across three existing boundaries:

1. The Google Health client counts outbound provider requests and accumulates
   request elapsed time for the currently active operation.
2. The coordinator measures the complete current-refresh and backfill-step
   elapsed time and labels the operation type and outcome.
3. The history store reports aggregate read and write elapsed time to the same
   active operation without recording query parameters or stored values.

The recorder exposes immutable snapshots to diagnostics. It must use monotonic
time for elapsed-duration measurements and wall-clock UTC only for the public
operation start timestamp.

## Operation Record

Each completed operation record contains only these allowlisted fields:

- `operation`: `current_refresh` or `backfill_step`;
- `started_at`: UTC ISO timestamp;
- `duration_ms`: total operation duration;
- `google_request_count`: number of outbound Google requests;
- `google_request_duration_ms`: cumulative Google request duration;
- `storage_read_count`: number of integration history reads;
- `storage_read_duration_ms`: cumulative history-read duration;
- `storage_write_count`: number of integration history writes/checkpoints;
- `storage_write_duration_ms`: cumulative history-write duration;
- `queued_wait_duration_ms`: time waiting for the coordinator lock;
- `backfill_active`: whether either backfill path was incomplete when the
  operation started;
- `enabled_capabilities`: sorted safe capability enum names only;
- `outcome`: `success`, `temporary_error`, `authorization_error`,
  `storage_error`, or `cancelled`;
- `slowest_phase`: one of `google_requests`, `storage_reads`,
  `storage_writes`, `coordinator_wait`, or `other`.

Durations are non-negative integers rounded to milliseconds. The record never
contains exception messages. Unknown exceptions map to `temporary_error` in
the performance record and continue through the integration's existing error
handling unchanged.

## Concurrency

Current refresh and backfill already coordinate through the entry's lock.
Instrumentation must preserve that behavior. Each asynchronous operation uses
its own context-local measurement state so concurrent config entries cannot
mix counts or timings.

The recorder tracks whether a current refresh waited for an existing operation
through `queued_wait_duration_ms`. It does not alter lock ordering, introduce a
new lock, or delay provider and storage operations.

Google requests issued outside a measured refresh or backfill operation are
not added to a record. This includes setup validation, OAuth activity, and the
optional availability probe.

## Diagnostics Output

Loaded config-entry diagnostics gain a `performance` object with:

- `polling_interval_minutes`;
- `record_limit`;
- `record_count`;
- `summary`;
- `recent_operations` in oldest-to-newest order.

The summary contains:

- successful and failed operation counts;
- average and maximum total duration;
- average and maximum Google request count;
- average and maximum Google request duration;
- average and maximum storage-read duration;
- average and maximum storage-write duration;
- average and maximum coordinator wait duration;
- the most frequent slowest phase;
- current core and expanded backfill completion booleans.

An entry that has not completed a measured operation returns an empty recent
operation list and zero/null summary values. A disabled or unloaded entry
returns no runtime performance records.

The complete diagnostics result continues through the existing recursive
redaction function. The performance schema is explicitly allowlisted and tests
must prove that unsafe keys and scalar values cannot pass through it.

## Performance Budget

Instrumentation must remain constant-space and constant-work per measured
boundary:

- at most 12 records per config entry;
- no filesystem or recorder writes;
- no background task or timer;
- no additional network calls;
- monotonic clock reads and integer additions only during provider/storage
  calls;
- diagnostics summaries computed only when a user downloads diagnostics.

## User Support Checklist

Documentation will ask affected users to provide:

1. Health Sync version and Home Assistant Core version.
2. Home Assistant installation type and hardware model/specifications.
3. Number of Health Sync people configured.
4. Enabled optional capabilities for each person.
5. Whether core and expanded backfill are complete.
6. Approximate normal CPU use and spike level.
7. A CPU-history screenshot covering at least one hour.
8. Downloaded Health Sync diagnostics for every configured person.
9. Whether the pattern continues after backfill completes.
10. Whether temporarily disabling all Health Sync config entries removes the
    pattern.

Users must be told that diagnostics are designed to be redacted, but they
should still review files before posting them publicly. Temporarily disabling
entries is an isolation test, not a required fix.

## Error Handling

Performance instrumentation is observational. It must never replace or suppress
the integration's existing authentication, temporary provider, storage, or
cancellation behavior. If recording a measurement itself fails, the health
operation continues and diagnostics omit that record.

Cancellation records are appended only after existing cancellation-safe
checkpoint handling completes. This preserves atomic cursor and history state.

## Testing

Automated tests must cover:

- a successful current refresh record;
- provider request count and cumulative duration without extra requests;
- storage read/write count and cumulative duration;
- queued lock-wait duration;
- temporary, authorization, storage, and cancellation outcomes;
- separate telemetry for concurrent config entries;
- fixed 12-record retention and oldest-record eviction;
- empty and populated diagnostics summaries;
- disabled-entry diagnostics;
- recursive redaction of injected unsafe keys and values;
- no new entities and no persistent performance data;
- unchanged refresh, backfill, storage, and API behavior through the existing
  full test suite.

Verification includes the complete test suite, Ruff, compileall, packaging
checks, and the public-release privacy scanner. Live CPU correlation remains a
post-install observational check and is not required to prove unit behavior.

## Release Strategy

Ship the instrumentation in a beta before a stable release. Collect diagnostics
from affected users and compare operation timestamps and durations with their
CPU history. Do not claim a CPU fix unless the collected evidence identifies a
specific expensive phase and a subsequent change measurably reduces it.
