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

An upgrade should reuse existing config entries and history. Do not remove and re-add a
person as an upgrade procedure.

## Renew authorization without duplication

If an existing entry requests new consent or reports unhealthy authorization:

1. Open that person's existing Health Sync entry.
2. Select **Reauthenticate**.
3. Verify the intended Google account in a private browser window or by explicit account
   switching.
4. Complete consent and return to the same entry.

Reauthentication preserves the person slug, entity unique IDs, and normalized history.
Adding the same person again does not.

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

Health Sync does not currently provide a supported normalized-store erasure path.
Do not edit or delete Home Assistant internal storage directly and do not guess at files;
doing so can damage Home Assistant or affect another person's data. Create a backup first,
remove only the intended config entry through Home Assistant, and request support for
person-scoped normalized-store erasure guidance. Until an integration-owned cleanup path
is available, complete erasure of normalized integration storage cannot be guaranteed
through a supported Health Sync or Home Assistant UI action.

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
