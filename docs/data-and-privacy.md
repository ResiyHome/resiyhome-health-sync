# Data and Privacy

Health data, account authorization, and Home Assistant backups are sensitive. Read this
page before enrollment and before sharing diagnostics or support material.

## Ownership and data flow

1. The user creates and controls the Google Cloud project and OAuth client.
2. Home Assistant stores the Application Credentials client secret and each person's
   OAuth credentials locally.
3. Each person grants three baseline read-only Google Health scopes. Nutrition
   and paired-device options request their own optional read-only scopes.
4. Health Sync polls Google Health over HTTPS every 15 minutes.
5. The integration normalizes daily data, exposes current entities, and stores compact
   normalized daily summaries in Home Assistant storage.

During enrollment, Home Assistant asks for `person_name` and Health Sync
derives a stable person slug for entity IDs and service or action targeting.
The slug is not a user-entered storage key. Normalized history is isolated by
the Home Assistant config-entry ID.

ResiyHome operates no hosted/shared OAuth backend, token relay, account service, or health
database for this integration.

## Google reconciliation and source attribution

Metric values come from Google's reconciled all-source stream, which resolves overlapping
provider records before Health Sync normalizes them. The integration does not add Fitbit,
HealthKit, and other provider values together.

Core polling transiently fetches raw records but examines only their platform labels to
recognize Fitbit and HealthKit. It does not normalize, store, or expose their health values.
Google-wearables reconciled steps support Fitbit attribution. When no
wearable steps exist but Google returns canonical data with HealthKit platform evidence,
the local source classification can be `apple_fallback`.
Health Sync does not connect directly to Apple Health; HealthKit-derived data must already be present in Google Health.
`mixed` means both wearable data and HealthKit evidence contributed to the day's source
classification, not that the integration performed raw arithmetic.

Expanded metrics use all-source reconciled daily summaries and daily rollups. Active-zone
minutes, floors, sedentary minutes, and heart-rate-zone minutes also use all-source
reconciled intervals for the incomplete current day until Google publishes a daily
rollup. Total calories uses Google's `total-calories` daily rollup. Weight,
body-fat percentage, and height use reconciled samples only when that person
opts in.

Optional nutrition uses the current local day's reconciled `nutrition-log` and
`hydration-log` points. The integration retains only summed kcal and
milliliters, not meal detail. The v4 reconcile contract is documented by
Google at
[users.dataTypes.dataPoints.reconcile](https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints/reconcile).

## What is stored

The private normalized history store is keyed by the Home Assistant
config-entry ID. It contains normalized daily summaries, source
classification, completion metadata, backfill cursors, and the
body-measurement option state. Additive daily fields include total calories,
sleep timing, body measurements when enabled, and nutrition values collected
after opt-in. Core history is imported in resumable seven-day windows up to
Google's 20-year provider boundary. The smaller window keeps high-volume
heart-rate history within Google Health pagination limits. Expanded metrics
use a bounded 90-day backfill in 14-day windows.

Weight, body-fat percentage, and height are excluded unless
`include_body_measurements` is enabled for that person. Enabling it starts a
bounded 90-day body-measurement backfill. Disabling it transactionally removes
all three body fields from normalized integration history and clears their
latest snapshots.

Daily normalized nutrition starts with the first successful opt-in refresh.
Nutrition has no historical backfill in this release.
Disabling `include_nutrition` stops future nutrition requests but does not erase already normalized nutrition values from the private daily store.

Paired devices are current metadata and are never written to normalized history.
The coordinator retains a sanitized current tuple, while Home
Assistant can retain the corresponding service-device and entity-registry
rows. Disabling `include_paired_devices` stops future settings requests and
clears the current tuple; existing paired entities become unavailable.

Home Assistant Recorder may separately store entity states according to the
user's Recorder configuration. Disabling an option is not a purge of Home
Assistant Recorder history or backups. Removal and purge remain an explicit
operator action with separate retention behavior.

## What is not stored or exposed

Health Sync does not persist raw Google API payloads, individual samples, or
Google source identifiers. No food names, raw nutrition logs, MAC addresses,
raw device resource IDs, or feature lists are stored by the integration.
Paired-device identity is reduced to a one-way digest before it reaches the
coordinator. It does not place OAuth credentials, client secrets, access
tokens, refresh tokens, raw responses, health values, device models, or
product names in diagnostics. Entity attributes use an explicit normalized
allowlist.

Diagnostics are recursively redacted: sensitive keys are removed at every nested level,
then only synchronization status, availability flags, dates, counts, source category,
and history bounds are returned. Redaction reduces support risk but is not permission to
share blindly. Review every file and screenshot before attaching it to an issue.

## Availability, zeros, and partial failures

A valid zero from Google is data and remains zero. Missing data, an invalid response
shape, an incomplete metric group, or an absent sample produces unavailable rather than
a fabricated zero. This matches Google's distinction between true zeros and missing
tracking periods for supported data types. See Google's
[Data presence and true zeros](https://developers.google.com/health/data-presence-and-true-zeros)
guide for the provider behavior.

Authentication failure stops the remaining poll and starts Home Assistant's
reauthentication path. A token refresh receives one retry. Non-authentication failures are
isolated by metric group: successful groups update, while a failed group keeps its prior
normalized value when available or remains unavailable. A fully successful,
non-paginated baseline current refresh uses 36 logical data requests, or 39
with body measurements enabled. Nutrition adds two current-day reconcile
requests and paired devices add one list request. Pagination and the
authentication retry can increase actual HTTP traffic.

Synchronization status is explicit: data is stale after 45 minutes without a successful
current refresh, while an authorization problem is reported separately.

`last_successful_synchronization` is Health Sync API refresh time. A dynamic
paired device's `last_device_sync` is Google's v4 `lastSyncTime`: Fitbit
mobile-device sync time. Health Sync does not trigger the wearable or mobile
application to synchronize, so either clock can advance without the other.

## Disabling, removal, and erasure

Disabling any option stops that capability's future requests. It does not
delete the config entry, revoke Google OAuth, remove Home Assistant
entity-registry rows, purge Home Assistant Recorder history, or delete
backups.

The normalized integration store has feature-specific behavior: body opt-out
scrubs normalized body fields; nutrition opt-out retains normalized nutrition
already collected; paired metadata was never part of normalized history.
Config-entry removal retains the private normalized history file. Health Sync
does not currently provide a supported full-store purge action, so
complete erasure cannot be guaranteed through an ordinary option change or removal.
Recreating an entry uses a new config-entry ID and therefore does not reconnect
that retained file, even if the same display name derives the same slug.
Review [Upgrading and removal](upgrading-and-removal.md) before any
explicit operator action.

## Security and support boundaries

Never publish or attach OAuth credentials, the client secret, access or refresh tokens,
raw Google Health responses, screenshots containing personal health values, or
unredacted Home Assistant storage or backups. Do not post Google account addresses,
person names, stable slugs, device identifiers, or retained history when a sanitized
description is sufficient.

This software is not a medical device, does not provide medical advice, and must not be
used for diagnosis, treatment decisions, emergency monitoring, or safety-critical alerts.
Provider availability, delayed synchronization, partial failures, and Home Assistant
outages can all make data late or unavailable.

For complete removal considerations, see [Upgrading and removal](upgrading-and-removal.md).
