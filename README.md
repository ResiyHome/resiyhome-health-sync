![Health Sync by ResiyHome](assets/health-sync-by-resiyhome.png)

# Health Sync by ResiyHome

Health Sync is a read-only Home Assistant custom integration that turns a person's
Google Health data into person-scoped entities and normalized daily history. Release 1.0.1
polls Google every 15 minutes, supports multiple independently authorized people,
and keeps OAuth under the user's control.

This project is independent and is not affiliated with, sponsored by, endorsed by, or
officially connected with Google LLC, Alphabet Inc., Home Assistant, Nabu Casa, Fitbit,
or Apple Inc. Product names are used only to describe interoperability.

## Features and privacy

- Activity, sleep, heart, respiratory, oxygen, fitness, workout, and synchronization
  entities, with optional weight collection.
- One config entry and one Google authorization per person.
- Google-reconciled all-source stream values, Fitbit attribution, and HealthKit-derived
  fallback classification without connecting directly to Apple Health.
- Compact normalized daily summaries instead of stored raw Google API payloads.
- Redacted diagnostics that report availability and synchronization health without
  health values, OAuth credentials, or Google identifiers.
- Read-only access through exactly three Google Health permissions.

This integration is not a medical device and does not provide medical advice,
diagnosis, treatment, or emergency monitoring.

## Installation and upgrade

### HACS custom-repository quick start

Repository: `https://github.com/ResiyHome/resiyhome-health-sync`

In HACS, open the menu, select **Custom repositories**, enter the repository URL, select
**Integration**, and add it. Then follow this sequence:

1. Install or update Health Sync in HACS and wait until the download is fully complete.
2. Do not restart Home Assistant earlier, even if HACS prompts you.
3. After the install or update is fully downloaded, restart Home Assistant exactly once.
4. Complete the [Google Cloud OAuth prerequisite](docs/google-cloud-oauth.md), add the
   client in Home Assistant **Application Credentials**, and then add **Health Sync by
   ResiyHome** from **Settings > Devices & services**.

See the [complete installation guide](docs/installation.md) before starting.

## Google OAuth prerequisite

Each installation must use a Google Cloud project and OAuth client owned by its users.
ResiyHome does not provide a hosted OAuth service, shared client, credential proxy, or
backend. The client must grant exactly these current read-only scopes:

- `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`
- `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
- `https://www.googleapis.com/auth/googlehealth.sleep.readonly`

Use `https://my.home-assistant.io/redirect/oauth` as the Authorized redirect URI when
using Home Assistant's standard My Home Assistant callback. Full setup, Testing-mode
expiration, and secret-handling details are in the
[Google Cloud OAuth guide](docs/google-cloud-oauth.md).

## Multi-user summary

Use one user-owned Google Cloud project and client, then create one independently
authorized config entry per person. Use a private browser window or explicitly switch
Google accounts for each authorization. Choose a stable unique person slug and use
**Reauthenticate** on an existing entry instead of adding the same person again.
[Multi-user setup](docs/multi-user.md) explains the sequence and per-person body
measurement option.

## Expanded Metrics

### Enabled by default

Active-zone minutes, Daily VO2 max, Daily oxygen saturation, Daily respiratory rate,
Sleep respiratory rate, Floors today, Sedentary minutes today, and Heart-rate-zone minutes
are Enabled by default.

### Disabled by default

The following detailed entities are Disabled by default:

- active-zone minutes for fat-burn, cardio, and peak zones
- heart-rate-zone minutes for light, moderate, vigorous, and peak zones
- calories for light, moderate, vigorous, and peak heart-rate zones
- sleep respiratory rate for deep, light, and REM sleep
- Weight

Weight is disabled by default in the entity registry. Enabling the entity and opting in
to body measurements are separate steps. The per-person `include_body_measurements`
option starts a bounded 90-day normalized backfill for weight. After you enable the
entity, its state may be unavailable until body measurements are opted in and Google
supplies usable data. A valid zero is retained as data; unavailable means no usable value
was obtained, not that the entity is disabled.

Expanded metrics use reconciled daily summaries and daily rollups.
Only expanded-metric polling avoids raw high-volume streams; core source attribution transiently inspects raw records.
Neither path stores raw API payloads or Google identifiers.

## Refresh contract

A fully successful, non-paginated refresh makes 31 logical data requests when body
measurements are disabled and 32 when body measurements are enabled. This includes core
raw source-attribution requests, core and expanded reconciled requests, the wearable
steps request, and expanded daily rollups. Pagination can increase the actual HTTP request count.
A one-time authentication retry can add a token request.

Authentication failure stops the remaining poll immediately; individual metric failures are isolated,
so successful groups continue and failed groups retain prior values or remain
unavailable under the partial-update rules. The exact data contract is documented in
[Data and privacy](docs/data-and-privacy.md).

Energy dashboard remains outside this integration.

## Documentation

- [Installation](docs/installation.md)
- [Google Cloud OAuth](docs/google-cloud-oauth.md)
- [Multi-user setup](docs/multi-user.md)
- [Entity catalog](docs/entities.md)
- [Actions and history](docs/actions-and-history.md)
- [Data and privacy](docs/data-and-privacy.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Upgrading and removal](docs/upgrading-and-removal.md)
- [Changelog](CHANGELOG.md)

## Support and security

Use the repository's [issue forms](https://github.com/ResiyHome/resiyhome-health-sync/issues/new/choose)
for sanitized bug reports and feature requests. Do not attach credentials, tokens, raw
Google Health responses, health-value screenshots, or unredacted Home Assistant storage.
Report vulnerabilities through the process in [SECURITY.md](SECURITY.md).

## License and trademarks

The source is available under the [MIT License](LICENSE). Brand and third-party name
boundaries are documented in [TRADEMARKS.md](TRADEMARKS.md).
