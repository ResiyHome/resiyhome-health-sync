# Troubleshooting

Start with the person's Health Sync config entry under **Settings > Devices & services**.
Check the authorization-problem and stale-data binary sensors before forcing refreshes.

## Authorization expires every seven days

Google OAuth clients with publishing status **Testing** receive refresh tokens that expire
after seven days. Use **Reauthenticate** on the existing person's entry. To avoid the
Testing-mode expiration, the Google Cloud project administrator can evaluate moving the
OAuth app to **In Production**. An unverified-app warning and other Google limits may
still apply.

Do not add a replacement config entry for the same person. Reauthentication
keeps the original config-entry ID, so Health Sync reconnects its existing
normalized history store. The same entry also retains the derived slug used
for entity identity and service or action targeting.

## Google says the user is not allowed

While the OAuth audience is in Testing, add the intended Google account under **Google
Auth Platform > Audience > Test users**. Then restart only the authorization flow; Home
Assistant does not need another restart.

## `redirect_uri_mismatch`

Confirm the OAuth client is a **Web application** and that **Authorized redirect URIs**
contains the exact HTTPS callback used by Home Assistant:

`https://my.home-assistant.io/redirect/oauth`

If My Home Assistant is disabled, use the exact HTTPS public Home Assistant base URL plus
`/auth/external/callback` shown by the active flow. Scheme, host, port, path, and trailing
characters must match exactly.

## Missing baseline scope or optional consent

The Google Cloud client's Data Access page and every person's baseline consent
must include:

- `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`
- `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
- `https://www.googleapis.com/auth/googlehealth.sleep.readonly`

The optional capabilities additionally require:

- `https://www.googleapis.com/auth/googlehealth.nutrition.readonly` for
  `include_nutrition`
- `https://www.googleapis.com/auth/googlehealth.settings.readonly` for
  `include_paired_devices`

After correcting scopes, use **Reauthenticate** on the existing entry.
Declining a baseline scope prevents setup because the runtime requires the
complete three-scope baseline. Declining an optional permission leaves
baseline sensors working; the optional capability remains unavailable. Never
delete or re-add the integration to repair consent.

## Entities are unavailable

Unavailable is different from zero. A valid provider zero remains zero; unavailable means
Google returned no usable value, a required metric group was incomplete, the person did
not have that data type, or the latest fetch failed before any prior value existed.

Check these in order:

1. Confirm the Google account selected during authorization is the intended account.
2. Confirm the source app, wearable, or Fitbit mobile application has
   synchronized data to Google Health.
3. Wait through one normal 15-minute polling interval.
4. Check `health_authorization_problem` and `health_data_stale`.
5. Run `resiyhome_health_sync.refresh` once with the stable derived person
   slug. Find it using the entity guidance in
   [Actions and history](actions-and-history.md); it is not a setup field.
   Repeated calls inside five minutes use the manual cooldown and do not
   create more Google polls.
6. Download diagnostics from the config entry, inspect them locally, and sanitize any
   other supporting material before requesting support.

Weight is disabled by default in the entity registry. Body-fat percentage and
Height use the same default. This registry setting is separate from whether an
enabled entity has a usable state. After you enable the entity, its state may
be unavailable until body measurements are opted in and Google supplies
usable data. Enable the per-person `include_body_measurements` option to opt
in. Expanded history can take multiple 15-minute background windows to
complete.

Calories consumed today and Water consumed today require both
`include_nutrition` and the nutrition scope. They use only the current local
day. Nutrition has no historical backfill in this release, so days before the
first successful opt-in refresh remain unavailable.

Paired battery and last-sync entities require both
`include_paired_devices` and the settings scope. They are created dynamically
only after Google returns a paired tracker or scale. A device that disappears
from the current response leaves its existing entities unavailable rather
than deleting their registry identity.

## Fitbit steps differ from total steps

`steps_today` uses Google's reconciled all-source result. `fitbit_steps_today` uses the
Google-wearables reconciled result. They can legitimately differ when other sources are
present or Google applies source reconciliation. Health Sync does not sum overlapping raw
provider records.

## Data is stale or a refresh partially failed

The stale binary sensor turns on after 45 minutes without a successful current refresh.
Authentication errors stop the poll and request reauthentication. Other API failures are
isolated by metric group, so one group may keep its prior normalized value while another
updates. Network recovery is retried on later scheduled polls; do not create duplicate
entries or repeatedly restart Home Assistant.

## Paired-device sync time looks old

`last_successful_synchronization` is Health Sync API refresh time:
the last successful Google poll by this integration. Paired-device last sync
is Fitbit mobile-device sync time from Google's v4 `lastSyncTime`. Health Sync
does not trigger a wearable or mobile sync. Open the Fitbit mobile application,
confirm the intended tracker or scale synchronizes there, then wait for or
request one Health Sync refresh.

Google's current v4 paired-device payload uses `deviceVersion`,
`batteryStatus`, `batteryLevel`, and `lastSyncTime`. Battery status values are
`High`, `Medium`, `Low`, or `Empty`. Sanitize diagnostics and screenshots;
never post a MAC address, raw resource name, or device feature list.

## An option was disabled but old data remains

Disabling an option stops future requests; it is not a general data purge.
Body-measurement opt-out scrubs normalized weight, body-fat, and height fields.
Nutrition already normalized for opt-in days remains in the private daily
store. Paired metadata is absent from normalized history, but Home Assistant
can retain device/entity registry rows and Recorder states. Recorder history,
backups, OAuth revocation, config-entry removal, and normalized-store removal
are separate operator decisions. See
[Upgrading and removal](upgrading-and-removal.md).

## History requires repair

The history store validates strictly and fails closed when its schema or content is not
safe to interpret. Never directly edit Home Assistant `.storage`. Restore a backup or use
a supported recovery path instead. Preserve a current backup and open a sanitized bug
report before deleting any storage. Do not attach the storage file.

Deleting and recreating the entry is not a history repair. The replacement
receives a new config-entry ID and does not reconnect the retained normalized
store, even if its person name derives the same slug.

## HACS downloaded the integration but Home Assistant cannot find it

Confirm the HACS download finished before the restart. HACS should show a downloaded or
pending-restart state. For a completed installation, perform the one post-download restart
described in [Installation](installation.md). Repeated restarts do not repair an incomplete
download.

## Requesting support safely

Use the repository's [bug report form](https://github.com/ResiyHome/resiyhome-health-sync/issues/new/choose).
Include versions, sanitized symptoms, reproduction steps, binary-sensor states, and a
description of which metric keys are unavailable.

Do not attach OAuth credentials, client secrets, access or refresh tokens, raw Google
Health responses, screenshots containing personal health values, or unredacted Home
Assistant storage. Also remove account addresses, names, person slugs, device identifiers,
and exact health values from pasted logs or screenshots.
