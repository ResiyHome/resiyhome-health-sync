# Upgrading and Removal

## Upgrade safely

1. Create a current Home Assistant backup, download it to another device, and read the
   release notes.
2. In HACS, install the Health Sync update or select **Redownload** for the new version.
3. Wait until the download is fully complete. Do not restart during the download.
4. After HACS finishes, restart Home Assistant exactly once.
5. Confirm each existing person entry loads, retains the same entities, and reports a
   healthy authorization.
6. Confirm current data and backfill status before changing dashboards or automations.

An upgrade should reuse existing config entries. Keeping the same Home
Assistant config-entry ID reconnects the same normalized history store. Do
not remove and re-add a person as an upgrade procedure.

## Enable new optional capabilities after an update

Do not delete or re-add the integration. Existing config entries update in
place, and Baseline sensors continue without reauthorization.

For each person who wants a new optional capability:

1. Open that person's Health Sync options.
2. Enable `include_nutrition`, `include_paired_devices`, or both.
3. Complete Google reauthorization for that same person.
4. Select **Reauthenticate** on that existing entry and verify the same Google
   account is active before consent.
5. For nutrition, approve Google Health nutrition access
   (`https://www.googleapis.com/auth/googlehealth.nutrition.readonly`) on
   Google's consent screen.
6. Return to Home Assistant and run the Health Sync refresh action or wait for
   the next 15-minute poll.
7. Confirm the optional entities populate after a successful refresh.

Declining an optional permission leaves baseline sensors working. The
declined capability remains unavailable until that person grants its scope.
Repeat these steps separately for each household member.

You do not need to create new Home Assistant Application Credentials, rotate
the OAuth client secret, or delete and recreate a person. Reauthorization
expands the saved scope grant while preserving the existing config-entry ID,
entity IDs, normalized history, and Recorder history.

`include_body_measurements` uses the existing baseline
health-measurements scope, so enabling Weight, Body-fat percentage, and Height
does not require a new Google permission. Those three entities are created
disabled by default in the entity registry and must be enabled individually.

## Renew authorization without duplication

If an existing entry requests new consent or reports unhealthy authorization:

1. Open that person's existing Health Sync entry.
2. Select **Reauthenticate**.
3. Verify the intended Google account in a private browser window or by explicit account
   switching.
4. Complete consent and return to the same entry.

Reauthentication keeps the same Home Assistant config-entry ID, so Health
Sync reconnects the same normalized history store. The existing entry
separately retains its derived person slug for entity unique IDs and service
or action targeting.

Deleting and recreating the integration creates a new config-entry ID. Even
when the same person name produces the same derived slug, the new entry does
not reconnect the old normalized history store. If the original entry still
exists, setup rejects another enrollment with the same derived slug before
OAuth.

## Disable a capability without assuming erasure

Disabling an option stops that capability's future Google requests. It does
not remove the person's config entry, revoke OAuth, delete entity-registry
rows, purge Home Assistant Recorder history, or delete backups.

- `include_body_measurements`: opting out transactionally scrubs normalized
  weight, body-fat, and height fields from the private integration history and
  current snapshot. Recorder states and backups remain separate.
- `include_nutrition`: opting out stops current-day nutrition and hydration
  requests. Values already normalized from prior opt-in refreshes remain in
  the private daily store and are gated from history output while the option
  or scope is inactive.
- `include_paired_devices`: opting out stops settings requests and clears the
  current in-memory paired tuple. Paired metadata is never stored in
  normalized daily history, but existing Home Assistant device/entity rows
  and Recorder states can remain.

Nutrition has no historical backfill in this release. Its normalized history
starts with the first successful opt-in refresh. Paired devices are current
metadata only.

## Remove one person

Before removal, decide what must be retained or erased and review backups. These are
separate operations with separate retention behavior:

- **Config-entry removal:** stops polling for that person and removes active entities.
- **Recorder data:** remains subject to Home Assistant Recorder retention and purge
  settings.
- **Normalized integration storage:** is retained by Health Sync when an entry is
  unloaded or removed.
- **OAuth revocation:** is controlled from the person's Google Account and is not caused
  by Home Assistant removal.
- **HACS files:** are shared integration code and are removed separately after all people
  are removed.
- **Backups:** remain independent copies until the user deletes them under their backup
  policy.

Then:

1. Remove that person's Health Sync config entry from **Settings > Devices & services**.
2. In the person's Google Account, review third-party access and revoke the OAuth grant if
   it is no longer needed.
3. Remove dashboards and automations that reference the person's entities.
4. Review Home Assistant Recorder data and backups separately.

Removing or unloading a config entry stops polling and removes its active entities, but
Health Sync does not automatically purge the integration's private normalized
history file, Home Assistant Recorder rows, or backups. This prevents an ordinary unload
or reauthentication from destroying history, but it also means entry removal alone is not
complete erasure.

That retained normalized file remains keyed by the removed config-entry ID.
A replacement entry receives a different ID and does not reconnect it merely
because the same display name derives the same person slug.

Health Sync does not currently provide a supported normalized-store erasure path.
Do not edit or delete Home Assistant internal storage directly and do not guess at files;
doing so can damage Home Assistant or affect another person's data. Create a backup first,
remove only the intended config entry through Home Assistant, and request support for
person-scoped normalized-store erasure guidance. Until an integration-owned cleanup path
is available, complete erasure of normalized integration storage cannot be guaranteed
through a supported Health Sync or Home Assistant UI action.

Removal and purge are therefore explicit operator actions, not a side effect
of disabling an option. Complete erasure cannot be guaranteed by an option
change, config-entry removal, or HACS removal because Recorder retention,
normalized storage, OAuth grants, and backups are independent.

## Remove the integration

1. Remove every Health Sync config entry and complete the data decisions above.
2. Delete the saved Health Sync Application Credentials only after no entry needs the
   shared client.
3. In HACS, remove Health Sync by ResiyHome and wait for removal to finish.
4. Restart Home Assistant once after HACS completes removal.
5. If nobody uses the Google Cloud project, revoke remaining grants, disable the Google
   Health API, rotate or delete the client secret, and delete the OAuth client or project
   according to the administrator's retention policy.

Removing HACS files does not perform config-entry removal, erase Recorder data or
normalized integration storage, complete OAuth revocation, or delete backups. Those are
separate actions.
