# Troubleshooting

Start with the person's Health Sync config entry under **Settings > Devices & services**.
Check the authorization-problem and stale-data binary sensors before forcing refreshes.

## Authorization expires every seven days

Google OAuth clients with publishing status **Testing** receive refresh tokens that expire
after seven days. Use **Reauthenticate** on the existing person's entry. To avoid the
Testing-mode expiration, the Google Cloud project administrator can evaluate moving the
OAuth app to **In Production**. An unverified-app warning and other Google limits may
still apply.

Do not add a replacement config entry for the same person. Reauthentication preserves the
stable person slug, entity identity, and normalized history.

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

## Missing-scope or consent failure

The Google Cloud client's Data Access page and the person's consent must include exactly:

- `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`
- `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
- `https://www.googleapis.com/auth/googlehealth.sleep.readonly`

After correcting scopes, use **Reauthenticate** on the existing entry. Declining one of
the required scopes prevents setup because the runtime contract requires the complete
three-scope set.

## Entities are unavailable

Unavailable is different from zero. A valid provider zero remains zero; unavailable means
Google returned no usable value, a required metric group was incomplete, the person did
not have that data type, or the latest fetch failed before any prior value existed.

Check these in order:

1. Confirm the Google account selected during authorization is the intended account.
2. Confirm the source app or device has synchronized data to Google Health.
3. Wait through one normal 15-minute polling interval.
4. Check `health_authorization_problem` and `health_data_stale`.
5. Run `resiyhome_health_sync.refresh` once with the stable person slug. Repeated calls
   inside five minutes use the manual cooldown and do not create more Google polls.
6. Download diagnostics from the config entry, inspect them locally, and sanitize any
   other supporting material before requesting support.

Weight is disabled by default in the entity registry. This registry setting is separate
from whether an enabled entity has a usable state. After you enable the entity, its state
may be unavailable until body measurements are opted in and Google supplies usable data.
Enable the per-person `include_body_measurements` option to opt in. Expanded history can
take multiple 15-minute background windows to complete.

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

## History requires repair

The history store validates strictly and fails closed when its schema or content is not
safe to interpret. Never directly edit Home Assistant `.storage`. Restore a backup or use
a supported recovery path instead. Preserve a current backup and open a sanitized bug
report before deleting any storage. Do not attach the storage file.

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
