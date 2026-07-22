# Data and Privacy

Health data, account authorization, and Home Assistant backups are sensitive. Read this
page before enrollment and before sharing diagnostics or support material.

## Ownership and data flow

1. The user creates and controls the Google Cloud project and OAuth client.
2. Home Assistant stores the Application Credentials client secret and each person's
   OAuth credentials locally.
3. Each person grants exactly three read-only Google Health scopes.
4. Health Sync polls Google Health over HTTPS every 15 minutes.
5. The integration normalizes daily data, exposes current entities, and stores compact
   normalized daily summaries in Home Assistant storage.

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

Expanded metrics use all-source reconciled daily summaries and daily rollups. Weight uses
a reconciled sample only when that person opts in.

## What is stored

The private per-entry store contains normalized daily summaries, source classification,
completion metadata, backfill cursors, and the body-measurement option state. Core history
is imported in resumable 31-day windows up to Google's 20-year provider boundary.
Expanded metrics use a bounded 90-day backfill in 14-day windows.

Weight is excluded unless `include_body_measurements` is enabled for that person. Enabling
it starts a bounded 90-day weight backfill. Disabling it transactionally removes weight
from normalized integration history and clears the latest weight snapshot.

Home Assistant Recorder may separately store entity states according to the user's
Recorder configuration. Integration opt-out and removal do not automatically purge those
Recorder rows or backups.

## What is not stored or exposed

Health Sync does not persist raw Google API payloads, individual samples, or Google source
identifiers. It does not place OAuth credentials, client secrets, access tokens, refresh
tokens, raw responses, or health values in diagnostics. Entity attributes use an explicit
normalized allowlist.

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
non-paginated current refresh uses 31 logical data requests, or 32 with weight enabled;
pagination and the authentication retry can increase actual HTTP traffic.

Synchronization status is explicit: data is stale after 45 minutes without a successful
current refresh, while an authorization problem is reported separately.

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
