# Installation

Health Sync by ResiyHome requires Home Assistant 2026.7.2 or newer, HACS, and
a user-owned Google Cloud OAuth client. It has no hosted or shared OAuth
backend.

## Before you begin

1. Create a current backup of Home Assistant and download it to another device.
2. Confirm HACS is installed and working.
3. Complete [Google Cloud OAuth setup](google-cloud-oauth.md). Keep the client secret
   private.
4. For more than one person, read [Multi-user setup](multi-user.md) before enrollment.

## Add the custom repository

1. Open **HACS** in Home Assistant.
2. Open the menu in the upper-right corner and select **Custom repositories**.
3. Enter `https://github.com/ResiyHome/resiyhome-health-sync`.
4. Select **Integration** as the repository type, then select **Add**.
5. Open **Health Sync by ResiyHome** and select **Download**.
6. Choose the current release and start the download.

## Complete the install with one restart

1. Wait until HACS shows that the download is fully complete. A **Pending restart**
   status is expected for an integration.
2. Do not restart while HACS is still downloading or unpacking files.
3. After the download is fully complete, restart Home Assistant exactly once.
4. Wait for Home Assistant to finish starting. Do not perform a second restart for the
   OAuth or config-entry steps below.

## Add Application Credentials

1. Go to **Settings > Devices & services**.
2. Open the upper-right menu and select **Application Credentials**.
3. Select **Add Application Credentials** and choose **Health Sync by ResiyHome**.
4. Enter a recognizable local name, the Google OAuth Client ID, and the client secret.
5. Save. The secret is stored by Home Assistant; never place it in this repository,
   issue reports, screenshots, or logs.

## Enroll the first person

1. Go to **Settings > Devices & services** and select **Add integration**.
2. Search for and select **Health Sync by ResiyHome**.
3. Enter the person's display name in **Person name**. The setup UI asks for
   `person_name`, shown as Person name. Health Sync derives the stable person
   slug from that name. Users do not enter or choose the slug directly.
4. Select the saved Application Credentials entry.
5. At Google, verify the active account, review the three requested baseline
   read-only scopes, and continue.
6. Return to Home Assistant and confirm one Health Sync device and its entities appear.
7. Review [Entities](entities.md) and [Data and privacy](data-and-privacy.md) before using
   health data in dashboards or automations.

For another person, follow [Multi-user setup](multi-user.md). If authorization fails later,
use **Reauthenticate** on that person's existing config entry.

## Enable per-person optional capabilities

New entries begin with body measurements, nutrition, and paired devices
disabled. Configure each person independently:

1. Open that person's Health Sync entry and select **Configure**.
2. Enable `include_body_measurements` for Weight, Body-fat percentage, and
   Height. This option uses the existing baseline permission and does not
   require Google reauthorization.
3. Enable `include_nutrition` for current-day Calories consumed today and
   Water consumed today, and/or `include_paired_devices` for current paired
   trackers and scales.
4. When Home Assistant starts reauthorization, verify the same person's
   Google account and approve the requested optional scope.
5. Repeat these steps separately for each additional person.

Declining an optional permission leaves baseline sensors working. The
optional entities remain unavailable until both their per-person option and
scope are active.

Weight, Body-fat percentage, and Height are created disabled by default in the
entity registry. Open the person's entities and enable each body measurement
that should be visible. Nutrition entities are registered after nutrition is
both enabled and authorized. Paired battery and sync entities are created
dynamically when Google returns a paired device.

Nutrition has no historical backfill in this release. It starts accumulating
normalized current-day values at the first successful opt-in refresh. Paired
devices are current metadata only.

## Existing installations

Update through HACS without deleting or re-adding the integration. Existing
config entries, entity identities, and normalized history are reused.
Baseline sensors continue without reauthorization. Follow the optional
capability steps above separately for each person who chooses to enable new
permissions.

The derived slug supplies stable entity unique IDs and service and action
targeting within an existing config entry. The normalized history store is
keyed separately by that entry's Home Assistant config-entry ID. Deleting and
recreating an entry generates a new config-entry ID, so entering the same
person name again does not reconnect the retained store from the removed
entry.

## Official references

- [HACS custom repositories](https://www.hacs.xyz/docs/faq/custom_repositories/)
- [HACS repository dashboard](https://www.hacs.xyz/docs/use/repositories/dashboard/)
- [Home Assistant Application Credentials](https://www.home-assistant.io/integrations/application_credentials/)
- [Google Health v4 data points](https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints)
