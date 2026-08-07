# Multi-user Setup

Health Sync isolates authorization, entities, options, and normalized history by person.
The intended arrangement is one household-owned Google Cloud project and OAuth client,
with one config entry per person.

## Model

- **Shared administration:** one Google Cloud project and one Web application OAuth
  client controlled by the household or group administrator.
- **Independent consent:** one config entry per person, with each person authorizing
  their own Google account.
- **Person-name input:** Home Assistant asks for a person name in the
  `person_name` field. Health Sync derives the stable person slug from that
  name. Users do not enter or choose the slug directly.
- **Stable entity identity:** The derived slug provides stable entity unique
  IDs and service and action targeting within that existing config entry.
- **Storage isolation:** The normalized history store is keyed by the Home
  Assistant config-entry ID, not by the person slug.
- **Per-person privacy choices:** body measurements, nutrition, and paired
  devices are opted in separately for each person and are off by default.

Sharing a client means its administrator controls the consent configuration and client
secret. It does not merge Google accounts or Health Sync history. Everyone should agree
who controls the project before enrollment.

## Enroll each person safely

1. Finish the shared Google Cloud and Application Credentials setup once.
2. Add every participating Google account to **Audience > Test users** if the project is
   in Testing.
3. Open a private browser window for the first person, or explicitly sign out and switch
   Google accounts before starting authorization.
4. In Home Assistant, add **Health Sync by ResiyHome**.
5. Enter a distinct display name in **Person name**. Health Sync validates the
   name and derives the slug; there is no separate slug field.
6. Select the shared Application Credentials client.
7. At Google, check the active account before granting the three baseline
   read-only scopes.
8. Return to Home Assistant and confirm exactly one new config entry and person-scoped
   entity set.
9. Close the private browser window before enrolling the next person.
10. Repeat from step 3 for each additional person.

Private browsing does not make the authorization anonymous; it reduces accidental reuse
of the previous person's active Google session.

## Configure each person's options

Open one person's Health Sync entry and select **Configure**. The choices do
not apply to any other household member:

1. Enable `include_body_measurements` for Weight, Body-fat percentage, and
   Height. The baseline health-measurements scope already covers these data
   types.
2. Enable `include_nutrition` for current-day food energy and hydration,
   `include_paired_devices` for current tracker/scale metadata, or both.
3. If an optional scope is missing, complete Google reauthorization for this
   same person's existing entry.
4. Confirm the active Google account before consent. A private browser window
   helps prevent accidental reuse of the previous person's session.
5. Repeat these steps separately for every person choosing optional
   capabilities.

Declining an optional nutrition or settings permission leaves baseline sensors
working. The declined capability remains unavailable and does not issue its
requests. Do not delete or re-add the integration to retry consent; reopen the
same person's options and reauthenticate that entry.

## Body-measurement entity registry

Weight, Body-fat percentage, and Height are created disabled by default in the
Home Assistant entity registry. Enabling `include_body_measurements` starts a
bounded 90-day normalized backfill for all three, but it does not make their
entities visible automatically. Enable each entity separately.

Opting out transactionally removes body-measurement values from the
integration's normalized history and current snapshot. It does not purge Home
Assistant Recorder states or backups, so opting out is not a complete erasure
operation.

## Nutrition and paired-device boundaries

Nutrition begins with the first successful current-day refresh after that
person opts in and grants `googlehealth.nutrition.readonly`. Nutrition has no
historical backfill in this release.

Paired-device discovery requires `googlehealth.settings.readonly`. The
returned battery and Fitbit mobile-application sync values are current
metadata only and are not part of normalized daily history. That sync
timestamp does not report when Health Sync last refreshed the Google API.

## Renewal and account changes

When an entry reports an authorization problem, select that existing config entry and
choose **Reauthenticate**. Use the same intended Google account. A new
enrollment whose derived slug matches an existing entry is rejected before
OAuth; it does not create another entry.

An ordinary update does not require baseline reauthorization. Existing
baseline-only tokens remain valid; new optional scopes are requested only
after that person's matching option is enabled.

If a person changes Google accounts, decide whether that should be a new identity. Using
Reauthenticate keeps the same config-entry ID, so Health Sync reconnects the
same normalized history store. The existing entry also retains its derived
slug for entity IDs and service or action targeting. Document any source
account change privately.

Deleting and recreating the integration creates a new config-entry ID. Even
when the same person name produces the same derived slug, the new entry does
not reconnect the old normalized history store.
