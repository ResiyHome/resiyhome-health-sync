# Security Policy

## Supported versions

Security updates are provided for the current 1.0.x release line. Users should reproduce
an issue on the latest available patch before reporting it when doing so does not risk
exposing data or credentials.

## Report a vulnerability privately

Use the repository's **Security** tab and select **Report a vulnerability** if private
vulnerability reporting is available:

`https://github.com/ResiyHome/resiyhome-health-sync/security`

If that private channel is unavailable, open a public issue containing only a request for
a private maintainer contact path. Do not describe the vulnerability, affected account,
or sensitive evidence in that public issue.

Include the affected version, impact, minimal reproduction, and suggested mitigation only
through the private channel. Maintainers will acknowledge a complete report, investigate,
and coordinate disclosure and a release when appropriate. No fixed response time is
promised.

Never attach or paste OAuth credentials, client secrets, access or refresh tokens, raw
Google Health responses, personal health values, Google account addresses, Home Assistant
backups, or unredacted Home Assistant storage. Revoke or rotate any credential that may
have been exposed before continuing the report.

## Security boundaries

Health Sync is designed for user-owned Google OAuth and local Home Assistant operation.
There is no ResiyHome-hosted OAuth backend. Diagnostics are recursively redacted, but users
must still inspect all material before sharing it. Health data and delayed synchronization
must not be used for emergency or safety-critical decisions.
