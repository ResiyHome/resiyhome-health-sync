# Google Cloud OAuth Setup

Health Sync uses user-owned Google OAuth only. Create and control the Google Cloud
project and client yourself. The project does not provide a hosted/shared OAuth backend,
shared credentials, or a credential relay.

These instructions match Google's current Health API setup and Home Assistant's
Application Credentials flow. Google Cloud labels can change; use the linked official
pages if a menu name differs.

## 1. Create or select a Google Cloud project

1. Sign in to Google Cloud with the account that will administer access for the group.
2. Create a project, or select one dedicated to this integration.
3. Record which administrator controls it. Anyone with project access may be able to
   change the consent screen, client, or authorized users.

Use one project and one client for all people in the same Home Assistant installation.
Do not create a separate client for each person.

## 2. Enable the Google Health API

Open the [Google Health API library page](https://console.cloud.google.com/apis/library/health.googleapis.com),
confirm the correct project is selected, and select **Enable**.

Google's overview is [Set up Google Cloud and OAuth](https://developers.google.com/health/setup).

## 3. Configure the OAuth audience

1. Open [Google Auth Platform > Audience](https://console.cloud.google.com/auth/audience).
2. Use **External** user type unless every account is inside one eligible Google
   Workspace organization.
3. While publishing status is **Testing**, add every person who will authorize Health
   Sync as a test user. Add their Google account addresses in Cloud Console; do not put
   those addresses in Home Assistant issue reports or repository files.
4. Save the audience settings.

In **Testing**, Google's refresh tokens expire after seven days. Each affected person
must then use **Reauthenticate** in Home Assistant. Moving the app to **In Production**
generally removes that seven-day Testing expiration, although tokens can still expire or
be revoked. An unverified-app warning and Google user limits can remain for a private,
unverified project. Publishing status is not a substitute for verification, policy
compliance, or secure secret handling.

## 4. Add the approved read-only scopes

Open [Google Auth Platform > Data Access](https://console.cloud.google.com/auth/scopes),
select **Add or remove scopes**, and configure these three baseline scopes:

- `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`
- `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
- `https://www.googleapis.com/auth/googlehealth.sleep.readonly`

Also configure these optional scopes if anyone may enable the matching
per-person capability:

- `https://www.googleapis.com/auth/googlehealth.nutrition.readonly` for
  `include_nutrition`
- `https://www.googleapis.com/auth/googlehealth.settings.readonly` for
  `include_paired_devices`

The initial integration flow requests only the three baseline scopes. Health
Sync requests an optional scope later only after the matching option is
enabled. `include_body_measurements` uses the baseline health-measurements
scope and does not require another permission.

Do not add broader permissions. Confirm the current descriptions and Google's
partial consent guidance in the
[official Google Health scope list](https://developers.google.com/health/scopes).
Health Sync accepts partial consent only when all three baseline scopes remain
granted; declining an optional scope leaves the baseline entry valid.

## 5. Create the OAuth client

1. Open [Google Auth Platform > Clients](https://console.cloud.google.com/auth/clients).
2. Select **Create client**.
3. Choose **Web application**.
4. Give the client a recognizable private name.
5. Under **Authorized redirect URIs**, add exactly:

   `https://my.home-assistant.io/redirect/oauth`

6. Create the client and securely record the Client ID and client secret.

The URI is HTTPS and must match character for character, including scheme and path. It
uses My Home Assistant only to route the browser back to the user's own Home Assistant;
it is not a ResiyHome OAuth backend. If My Home Assistant is disabled, Home Assistant
uses the HTTPS public instance URL followed by `/auth/external/callback`; use the exact
redirect URI shown by Home Assistant for that flow.

## 6. Store the client in Home Assistant

1. In Home Assistant, go to **Settings > Devices & services**.
2. Open the upper-right menu and select **Application Credentials**.
3. Add credentials for **Health Sync by ResiyHome**.
4. Enter the Client ID and client secret once, then save.

Home Assistant stores Application Credentials locally. The client secret is sensitive:
never commit it, paste it into an issue, include it in a screenshot, or share an
unredacted Home Assistant backup or storage file. Rotate the secret in Google Cloud if
you suspect exposure, update Application Credentials, and reauthenticate existing entries.

## 7. Authorize each person

Add the integration once per person. Before OAuth, Home Assistant asks for the
person's display name in **Person name** (`person_name`); Health Sync derives
the stable person slug rather than presenting a slug field. Use a private
browser window or explicit Google-account switching, verify the active account
before consent, and approve all three baseline scopes. See
[Multi-user setup](multi-user.md).

For an existing person, enable `include_nutrition`,
`include_paired_devices`, or both from that person's Health Sync options.
Home Assistant starts reauthorization when the saved token lacks the newly
requested optional scope. Complete consent using the same person's Google
account and the same config entry. Do not delete, re-add, or duplicate the
entry.

For nutrition specifically:

1. Open **Settings > Devices & services > Health Sync by ResiyHome**.
2. Open the existing person's entry and select **Configure**.
3. Enable `include_nutrition` and submit the options.
4. Complete **Reauthenticate** for that same entry. If Home Assistant does not
   open it automatically, select the entry's reauthentication action.
5. Verify the intended Google account before continuing.
6. On Google's consent screen, approve Google Health nutrition access. The
   resulting authorization must contain
   `https://www.googleapis.com/auth/googlehealth.nutrition.readonly`.
7. Return to Home Assistant and run the Health Sync refresh action or wait for
   the next 15-minute poll.

Application Credentials do not need a new client ID or secret for this
upgrade. Reauthorization expands the saved grant for the existing OAuth client
and person entry.

If an optional permission is declined, baseline sensors continue. The
declined capability remains unavailable until the option is enabled and the
matching scope is granted in a later reauthorization.

## Current Google Health v4 contracts

Health Sync sends authenticated requests to the current base URL
`https://health.googleapis.com/v4`. The public implementation follows these
official references:

- [Google Health data types](https://developers.google.com/health/data-types)
- [v4 data-point schema](https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints)
- [v4 reconcile method](https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints/reconcile)
- [v4 paired-device resource](https://developers.google.com/health/reference/rest/v4/users.pairedDevices)
- [v4 paired-device list method](https://developers.google.com/health/reference/rest/v4/users.pairedDevices/list)

Current and historical health metrics use the v4 data-point methods. Optional
nutrition reads `nutrition-log` and `hydration-log` through the reconcile
method. Optional paired-device discovery uses
`GET https://health.googleapis.com/v4/{parent=users/*}/pairedDevices`, with an
empty request body, a maximum documented page size of 100, and
`googlehealth.settings.readonly`.

The paired-device response contract names are `deviceVersion`,
`batteryStatus`, `batteryLevel`, and `lastSyncTime`. The last field is the
Fitbit mobile-application sync timestamp, not Health Sync's Google API refresh
timestamp.

## Troubleshooting OAuth

- **redirect_uri_mismatch:** compare the client type and every character under
  **Authorized redirect URIs**. The client must be a Web application.
- **Access blocked or user not allowed:** while in Testing, add that Google account under
  **Audience > Test users**.
- **Reauthentication every seven days:** the project is likely still in Testing.
- **Missing baseline scope:** confirm all three baseline scopes are configured
  and selected during consent, then reauthenticate the existing entry.
- **Optional entity unavailable:** confirm the matching option is enabled,
  add its optional scope to Data Access, and reauthenticate that same person.
  Declining the optional permission does not stop baseline sensors.
- **Unverified-app warning:** expected for many private projects. Proceed only when you
  recognize and control the project shown by Google.
- **Invalid client or secret:** verify the saved Application Credentials entry. Rotate
  the client secret if it may have been disclosed.

More detail is available in [Troubleshooting](troubleshooting.md).
