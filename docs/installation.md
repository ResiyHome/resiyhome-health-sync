# Installation

Health Sync by ResiyHome 1.0.1 requires Home Assistant 2026.7.2 or newer, HACS, and a
user-owned Google Cloud OAuth client. It has no hosted or shared OAuth backend.

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
3. Enter a stable display name that produces a unique person slug. Do not rename it just
   to change dashboard wording; the slug anchors entity identity and retained history.
4. Select the saved Application Credentials entry.
5. At Google, verify the active account, review the three requested read-only scopes,
   and continue.
6. Return to Home Assistant and confirm one Health Sync device and its entities appear.
7. Review [Entities](entities.md) and [Data and privacy](data-and-privacy.md) before using
   health data in dashboards or automations.

For another person, follow [Multi-user setup](multi-user.md). If authorization fails later,
use **Reauthenticate** on that person's existing config entry.

## Official references

- [HACS custom repositories](https://www.hacs.xyz/docs/faq/custom_repositories/)
- [HACS repository dashboard](https://www.hacs.xyz/docs/use/repositories/dashboard/)
- [Home Assistant Application Credentials](https://www.home-assistant.io/integrations/application_credentials/)
