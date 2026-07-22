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

## 4. Add exactly the required scopes

Open [Google Auth Platform > Data Access](https://console.cloud.google.com/auth/scopes),
select **Add or remove scopes**, and add exactly these three scopes:

- `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`
- `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
- `https://www.googleapis.com/auth/googlehealth.sleep.readonly`

Do not add broader permissions. Confirm the current permission descriptions in the
[official Google Health scope list](https://developers.google.com/health/scopes).

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

Add the integration once per person. Use a private browser window or explicit
Google-account switching, verify the active account before consent, and approve all three
scopes. See [Multi-user setup](multi-user.md).

If Google reports missing scopes, update Data Access first and then use
**Reauthenticate** on the existing Home Assistant entry. Do not add a duplicate entry.

## Troubleshooting OAuth

- **redirect_uri_mismatch:** compare the client type and every character under
  **Authorized redirect URIs**. The client must be a Web application.
- **Access blocked or user not allowed:** while in Testing, add that Google account under
  **Audience > Test users**.
- **Reauthentication every seven days:** the project is likely still in Testing.
- **Missing scope:** confirm all three exact scopes are configured and selected during
  consent, then reauthenticate the existing entry.
- **Unverified-app warning:** expected for many private projects. Proceed only when you
  recognize and control the project shown by Google.
- **Invalid client or secret:** verify the saved Application Credentials entry. Rotate
  the client secret if it may have been disclosed.

More detail is available in [Troubleshooting](troubleshooting.md).
