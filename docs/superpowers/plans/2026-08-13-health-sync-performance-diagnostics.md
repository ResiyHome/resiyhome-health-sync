# Health Sync Performance Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded, value-free, in-memory performance records to downloadable Health Sync diagnostics without adding entities, persistence, polling, or Google requests.

**Architecture:** A new `performance.py` module owns a 12-record deque and one context-local active measurement per asynchronous operation. The coordinator starts and completes current-refresh/backfill measurements; the API and history store report request/read/write timing into the active measurement; diagnostics computes summaries only when downloaded.

**Tech Stack:** Python 3.14, Home Assistant `DataUpdateCoordinator`, `contextvars`, `dataclasses`, `collections.deque`, `time.perf_counter`, pytest, Ruff.

## Global Constraints

- Performance data is downloadable-diagnostics only; no diagnostic entities.
- Keep at most 12 completed operation records per config entry.
- Keep records in memory only and discard them on unload/restart.
- Add no Google requests, timers, background tasks, recorder writes, or integration-store fields.
- Retain no health values, raw payloads, request URLs/parameters, resource names, device/account/person identifiers, OAuth material, or exception messages.
- Preserve existing refresh, backfill, cancellation, storage, authentication, and partial-failure behavior.
- Use monotonic time for elapsed durations and UTC wall time only for `started_at`.
- Continue routing the final diagnostics object through recursive redaction.

---

### Task 1: Bounded In-Memory Performance Recorder

**Files:**
- Create: `custom_components/resiyhome_health_sync/performance.py`
- Create: `tests/test_performance.py`

**Interfaces:**
- Produces: `PerformanceRecorder(record_limit: int = 12)`
- Produces: `PerformanceRecorder.begin(operation, *, backfill_active, enabled_capabilities, started_at=None) -> PerformanceOperation`
- Produces: `PerformanceOperation.activate() -> Token`
- Produces: `PerformanceOperation.add_google_request(duration_seconds) -> None`
- Produces: `PerformanceOperation.add_storage_read(duration_seconds) -> None`
- Produces: `PerformanceOperation.add_storage_write(duration_seconds) -> None`
- Produces: `PerformanceOperation.add_coordinator_wait(duration_seconds) -> None`
- Produces: `PerformanceOperation.finish(outcome) -> None`
- Produces: `PerformanceRecorder.snapshot() -> tuple[PerformanceRecord, ...]`
- Produces module no-op helpers `record_google_request`, `record_storage_read`, and `record_storage_write` that update only the context-local active operation.

- [ ] **Step 1: Write failing recorder tests**

Create tests that use a deterministic monotonic clock and UTC start time:

```python
def test_recorder_collects_only_allowlisted_bounded_operation_data() -> None:
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
```

Also test:

- the third record evicts the oldest when `record_limit=2`;
- a helper call without an active context is a no-op;
- two async tasks with separate recorders do not mix counts;
- `finish` and `deactivate` are idempotent;
- an invalid operation prevents measurement creation; an invalid outcome prevents
  record creation; invalid capabilities are omitted; and negative or non-finite
  durations are ignored, all without raising into health work;
- records are frozen snapshots and contain no free-form strings.

- [ ] **Step 2: Run the recorder tests and verify RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_performance.py -v
```

Expected: collection fails because `custom_components.resiyhome_health_sync.performance` does not exist.

- [ ] **Step 3: Implement the minimal recorder**

Implement frozen `PerformanceRecord`, mutable private measurement state, and
`PerformanceRecorder` using `deque(maxlen=record_limit)` and a
`ContextVar[PerformanceOperation | None]`.

Use exact allowlists:

```python
OPERATIONS = frozenset({"current_refresh", "backfill_step"})
OUTCOMES = frozenset(
    {"success", "temporary_error", "authorization_error", "storage_error", "cancelled"}
)
PHASES = frozenset(
    {"google_requests", "storage_reads", "storage_writes", "coordinator_wait", "other"}
)
RECORD_LIMIT = 12
```

Round valid seconds to non-negative integer milliseconds. Compute
`other_duration` as total duration minus the four accumulated phase durations,
clamped to zero. Determine `slowest_phase` from those five values using stable
`PHASES` order for ties. Capability input must be filtered to `CapabilityId`
values and stored as a sorted tuple before serialization.

- [ ] **Step 4: Run recorder tests and verify GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_performance.py -v
.venv/bin/python -m ruff check custom_components/resiyhome_health_sync/performance.py tests/test_performance.py
```

Expected: all recorder tests pass and Ruff reports no errors.

- [ ] **Step 5: Commit the recorder**

```bash
git add custom_components/resiyhome_health_sync/performance.py tests/test_performance.py
git commit -m "feat: add bounded performance recorder"
```

---

### Task 2: Measure Existing Google Requests Without Adding Calls

**Files:**
- Modify: `custom_components/resiyhome_health_sync/api.py:504-583`
- Modify: `tests/test_api.py`

**Interfaces:**
- Consumes: `record_google_request(duration_seconds: float) -> None`
- Preserves: `_async_get_json` and `_async_post_json` signatures and all retry/error behavior.

- [ ] **Step 1: Write failing transport timing tests**

Add tests that activate a `PerformanceOperation`, perform one successful GET
and POST through the existing fake session, and assert one count per actual
HTTP attempt. Add a 401-refresh-success test asserting two HTTP attempts, not
one logical method call:

```python
async def test_transport_counts_each_actual_http_attempt(hass) -> None:
    recorder = PerformanceRecorder(monotonic=clock)
    operation = recorder.begin(
        "current_refresh",
        backfill_active=False,
        enabled_capabilities=("core_activity",),
        started_at=NOW,
    )
    token = operation.activate()
    try:
        await client._async_get_json("https://health.googleapis.test/value", {})
    finally:
        operation.finish("success")
        operation.deactivate(token)

    assert recorder.snapshot()[0].google_request_count == 1
```

Assert request timing does not change payload parsing, headers, refresh-token
retry, pagination bounds, or raised exception types. Assert the fake session's
call count is unchanged from the pre-instrumentation expectation.

- [ ] **Step 2: Run focused API tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_api.py -k "performance or attempt" -v
```

Expected: the performance request count remains zero.

- [ ] **Step 3: Instrument only the actual HTTP context**

In `_async_get_json` and `_async_post_json`, read `perf_counter()` immediately
before `self._session.get/post` and call `record_google_request()` in a
`finally` block surrounding that one actual request attempt. Do not wrap token
refresh or pagination collection as provider requests, and do not pass URLs,
parameters, bodies, statuses, or exceptions to the recorder.

- [ ] **Step 4: Verify API behavior and timing**

```bash
.venv/bin/python -m pytest tests/test_api.py -v
.venv/bin/python -m ruff check custom_components/resiyhome_health_sync/api.py tests/test_api.py
```

Expected: all API tests pass with unchanged request call counts.

- [ ] **Step 5: Commit request instrumentation**

```bash
git add custom_components/resiyhome_health_sync/api.py tests/test_api.py
git commit -m "feat: measure Google Health request time"
```

---

### Task 3: Measure Existing History Reads and Writes

**Files:**
- Modify: `custom_components/resiyhome_health_sync/storage.py:295-365`
- Modify: `tests/test_storage.py`

**Interfaces:**
- Consumes: `record_storage_read(duration_seconds: float) -> None`
- Consumes: `record_storage_write(duration_seconds: float) -> None`
- Preserves all `HealthHistoryStore` method signatures and schema fields.

- [ ] **Step 1: Write failing storage timing and persistence tests**

Activate an operation and assert:

- `async_query` increments read count once;
- `async_upsert`, `async_set_backfill_checkpoint`, and
  `async_checkpoint_expanded` each increment write count once per public call;
- a raised `HistoryStoreError` is still timed and still propagates;
- saved JSON contains no `performance`, `duration`, `request_count`, or
  operation-record fields;
- a newly constructed store has no access to the old recorder's records.

Do not assert private helper call counts; the public storage operation is the
measurement boundary.

- [ ] **Step 2: Run focused storage tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_storage.py -k "performance or timing or persisted_document" -v
```

Expected: active operation storage counts remain zero.

- [ ] **Step 3: Add no-op timing around public storage boundaries**

Wrap only these methods with monotonic start/finally reporting:

```python
async def async_query(self, start: date, end: date) -> list[DailySummary]:
    started = perf_counter()
    try:
        return ...
    finally:
        record_storage_read(perf_counter() - started)
```

Use the equivalent write wrapper for `async_upsert`,
`async_set_backfill_checkpoint`, and `async_checkpoint_expanded`. Do not add a
recorder member to `HealthHistoryStore` and do not modify `_DOCUMENT_FIELDS`,
schema versions, serialization, save delay, or cancellation-safe writes.

- [ ] **Step 4: Verify storage behavior and timing**

```bash
.venv/bin/python -m pytest tests/test_storage.py -v
.venv/bin/python -m ruff check custom_components/resiyhome_health_sync/storage.py tests/test_storage.py
```

Expected: all storage tests pass and persisted documents are byte-schema
compatible with existing fixtures.

- [ ] **Step 5: Commit storage instrumentation**

```bash
git add custom_components/resiyhome_health_sync/storage.py tests/test_storage.py
git commit -m "feat: measure health history operations"
```

---

### Task 4: Record Coordinator Refresh and Backfill Operations

**Files:**
- Modify: `custom_components/resiyhome_health_sync/coordinator.py:225-540`
- Modify: `custom_components/resiyhome_health_sync/__init__.py:40-145`
- Modify: `tests/test_coordinator.py`
- Modify: `tests/test_init.py`

**Interfaces:**
- Consumes: `PerformanceRecorder`
- Changes constructor to `HealthSyncCoordinator(..., performance: PerformanceRecorder, ...)`
- Adds runtime field `performance: PerformanceRecorder`
- Produces operation records for `async_refresh_current` and
  `async_backfill_step`.

- [ ] **Step 1: Write failing coordinator lifecycle tests**

Add tests for:

- successful scheduled/manual/setup current refresh produces exactly one
  `current_refresh` record per call;
- successful `async_backfill_step` produces one combined `backfill_step`
  record, not separate core/expanded records;
- enabled capabilities are safe sorted `CapabilityId.value` names;
- `backfill_active` reflects core or expanded incomplete state at operation
  start;
- lock waiting accumulates into `queued_wait_duration_ms`;
- `AuthenticationError`, `UpdateFailed`, `HistoryStoreError`, and
  `CancelledError` map to the approved outcome strings while the original
  exception still propagates;
- current and backfill operations do not overlap incorrectly or mix counts;
- two config entries receive different recorder instances;
- unload removes the runtime recorder with the runtime and restart creates an
  empty recorder;
- no platform/entity setup count changes.

- [ ] **Step 2: Run coordinator/init tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_coordinator.py tests/test_init.py -k "performance or recorder or wait_duration" -v
```

Expected: constructor/runtime assertions fail because no recorder is wired.

- [ ] **Step 3: Wire one recorder per config entry**

In `async_setup_entry`:

```python
performance = PerformanceRecorder()
coordinator = HealthSyncCoordinator(
    hass,
    client,
    history,
    performance=performance,
    include_body_measurements=...,
)
runtime = HealthSyncRuntimeData(client, history, coordinator, scope_grant, performance)
```

Keep creation local to the config entry. Do not register it in `hass.data` or
storage.

- [ ] **Step 4: Wrap coordinator operations and waits**

Start measurement before lock acquisition so queue time is visible. Activate
the context before any API/storage work and always deactivate in `finally`.
Map exceptions only for the record:

```python
try:
    snapshot = await existing_operation()
except asyncio.CancelledError:
    measurement.finish("cancelled")
    raise
except AuthenticationError:
    measurement.finish("authorization_error")
    raise
except HistoryStoreError:
    measurement.finish("storage_error")
    raise
except UpdateFailed:
    measurement.finish("temporary_error")
    raise
except Exception:
    measurement.finish("temporary_error")
    raise
else:
    measurement.finish("success")
    return snapshot
finally:
    measurement.deactivate(token)
```

Measure cumulative wait around existing lock acquisition without changing
lock priority or polling. The backfill wrapper must finish after both core and
expanded work, including cancellation-safe checkpoint completion.

- [ ] **Step 5: Verify coordinator/runtime behavior**

```bash
.venv/bin/python -m pytest tests/test_coordinator.py tests/test_init.py -v
.venv/bin/python -m ruff check custom_components/resiyhome_health_sync/coordinator.py custom_components/resiyhome_health_sync/__init__.py tests/test_coordinator.py tests/test_init.py
```

Expected: all coordinator and setup/unload tests pass with unchanged entity and
background-task behavior.

- [ ] **Step 6: Commit lifecycle instrumentation**

```bash
git add custom_components/resiyhome_health_sync/coordinator.py custom_components/resiyhome_health_sync/__init__.py tests/test_coordinator.py tests/test_init.py
git commit -m "feat: record refresh performance diagnostics"
```

---

### Task 5: Expose Redacted Diagnostics and Support Checklist

**Files:**
- Modify: `custom_components/resiyhome_health_sync/diagnostics.py:205-285`
- Modify: `tests/test_diagnostics.py`
- Modify: `docs/troubleshooting.md`
- Modify: `tests/test_documentation.py`

**Interfaces:**
- Consumes: `entry.runtime_data.performance.snapshot()`
- Produces: `_summarize_performance(records, snapshot) -> dict[str, object]`
- Adds diagnostics top-level `performance` object only for loaded entries.

- [ ] **Step 1: Write failing diagnostics summary tests**

Construct safe records and assert this exact structure:

```python
assert result["performance"] == {
    "polling_interval_minutes": 15,
    "record_limit": 12,
    "record_count": 2,
    "summary": {
        "successful_operation_count": 1,
        "failed_operation_count": 1,
        "average_duration_ms": 375,
        "maximum_duration_ms": 500,
        "average_google_request_count": 12,
        "maximum_google_request_count": 14,
        "average_google_request_duration_ms": 200,
        "maximum_google_request_duration_ms": 250,
        "average_storage_read_duration_ms": 20,
        "maximum_storage_read_duration_ms": 25,
        "average_storage_write_duration_ms": 30,
        "maximum_storage_write_duration_ms": 40,
        "average_coordinator_wait_duration_ms": 5,
        "maximum_coordinator_wait_duration_ms": 10,
        "most_frequent_slowest_phase": "google_requests",
        "core_backfill_complete": True,
        "expanded_backfill_complete": False,
    },
    "recent_operations": [...],
}
```

Use round-half-up or integer-floor consistently and document the choice in the
test. Add tests for empty records, deterministic tie-breaking of the most
frequent phase using `PHASES` order, oldest-to-newest order, and unloaded entry
output without runtime records.

Inject forbidden keys and values into a fake recorder snapshot and assert the
existing `FORBIDDEN` scan and `REDACTED` behavior still fail closed. Assert no
health metric values appear in the performance subtree.

- [ ] **Step 2: Write failing support-document tests**

Add a documentation assertion requiring all ten checklist items from the
approved design plus these statements:

```text
Performance records are memory-only and limited to the latest 12 operations.
Review diagnostics before posting them publicly.
Temporarily disabling Health Sync is an isolation test, not a required fix.
```

- [ ] **Step 3: Run diagnostics/document tests and verify RED**

```bash
.venv/bin/python -m pytest tests/test_diagnostics.py tests/test_documentation.py -k "performance or cpu or support" -v
```

Expected: no `performance` diagnostics object and no CPU support checklist.

- [ ] **Step 4: Implement diagnostics summary**

Compute the summary only inside `async_get_config_entry_diagnostics`. Use the
immutable recorder snapshot, integer arithmetic, stable phase ordering, and
the existing `_redact_recursive` final gate. Do not read Home Assistant CPU
entities from the integration and do not perform I/O while summarizing.

For zero records, counts are zero, averages/maxima are `None`, and
`most_frequent_slowest_phase` is `None`.

- [ ] **Step 5: Add the user-facing support checklist**

Add a `CPU spikes every 15 minutes` section to `docs/troubleshooting.md` that:

- explains that cadence is a correlation clue, not proof;
- gives exact navigation to download diagnostics for every Health Sync config
  entry;
- asks for the ten approved information items;
- tells users not to include health values, logs with credentials, raw Google
  payloads, or unreviewed diagnostics;
- recommends observing at least four cycles (one hour);
- states that disabling entries temporarily is optional isolation and that
  users should re-enable them after the observation.

- [ ] **Step 6: Verify diagnostics privacy and documentation**

```bash
.venv/bin/python -m pytest tests/test_diagnostics.py tests/test_documentation.py -v
.venv/bin/python -m ruff check custom_components/resiyhome_health_sync/diagnostics.py tests/test_diagnostics.py tests/test_documentation.py
```

Expected: diagnostics and documentation tests pass, including recursive
redaction.

- [ ] **Step 7: Commit diagnostics and support documentation**

```bash
git add custom_components/resiyhome_health_sync/diagnostics.py tests/test_diagnostics.py docs/troubleshooting.md tests/test_documentation.py
git commit -m "docs: add CPU diagnostic support workflow"
```

---

### Task 6: Full Verification and Beta Readiness Report

**Files:**
- Modify only if verification exposes a defect in the implementation above.

**Interfaces:**
- Validates all preceding tasks as one release candidate.

- [ ] **Step 1: Run the complete local verification suite**

```bash
.venv/bin/python -m pytest tests -v
.venv/bin/python -m ruff check custom_components/resiyhome_health_sync tests
.venv/bin/python -m compileall custom_components/resiyhome_health_sync tests
.venv/bin/python scripts/verify_public_release.py
```

Expected: all commands pass. If the public scanner reports the previously known
reachable-history identifier, report it separately and do not rewrite history
as part of this feature.

- [ ] **Step 2: Verify no product-surface or persistence expansion**

```bash
git diff origin/main -- custom_components/resiyhome_health_sync/sensor.py custom_components/resiyhome_health_sync/binary_sensor.py custom_components/resiyhome_health_sync/storage.py
rg -n 'performance|recent_operations|duration_ms' custom_components/resiyhome_health_sync/storage.py custom_components/resiyhome_health_sync/sensor.py custom_components/resiyhome_health_sync/binary_sensor.py
```

Expected: no sensor/binary-sensor changes; storage contains timing hooks only
and no serialized performance field.

- [ ] **Step 3: Review the final diff and status**

```bash
git diff --check
git status --short --branch
git log --oneline --decorate -10
```

Report changed files, exact test totals, any blocked live checks, and confirm no
Home Assistant deployment or restart occurred.

- [ ] **Step 4: Request code review before beta publication**

Invoke `superpowers:requesting-code-review` against the feature diff. Resolve
only confirmed findings with TDD. Do not change the manifest version, tag,
push, or publish until Russ separately approves the beta release step.
