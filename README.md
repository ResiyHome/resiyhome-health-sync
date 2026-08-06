![Health Sync by ResiyHome](assets/health-sync-by-resiyhome.png)

# Health Sync by ResiyHome

Health Sync is a read-only Home Assistant custom integration that turns a person's
Google Health data into person-scoped entities and normalized daily history. It polls
Google every 15 minutes, supports multiple independently authorized people, and keeps
OAuth under the user's control.

This project is independent and is not affiliated with, sponsored by, endorsed by, or
officially connected with Google LLC, Alphabet Inc., Home Assistant, Nabu Casa, Fitbit,
or Apple Inc. Product names are used only to describe interoperability.

## Features and privacy

- Activity, total-calorie, sleep, heart, respiratory, oxygen, fitness, workout, and
  synchronization entities.
- Optional body measurements, current-day nutrition and hydration totals, and current
  paired-device battery and sync metadata.
- One config entry and one Google authorization per person.
- Google-reconciled all-source stream values, Fitbit attribution, and HealthKit-derived
  fallback classification without connecting directly to Apple Health.
- Compact normalized daily summaries instead of stored raw Google API payloads.
- Redacted diagnostics that report availability and synchronization health without
  health values, OAuth credentials, or Google identifiers.
- Three baseline read-only scopes, plus two optional read-only scopes requested only
  when the matching per-person options are enabled.

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
backend. Every person must grant these three baseline read-only scopes:

- `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`
- `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
- `https://www.googleapis.com/auth/googlehealth.sleep.readonly`

The two optional read-only scopes are:

- `https://www.googleapis.com/auth/googlehealth.nutrition.readonly` for
  `include_nutrition`
- `https://www.googleapis.com/auth/googlehealth.settings.readonly` for
  `include_paired_devices`

Body measurements use the existing baseline health-measurements scope and do not add
another permission. Existing baseline-only authorizations remain valid until a person
enables an option that needs an optional scope.

Use `https://my.home-assistant.io/redirect/oauth` as the Authorized redirect URI when
using Home Assistant's standard My Home Assistant callback. Full setup, Testing-mode
expiration, and secret-handling details are in the
[Google Cloud OAuth guide](docs/google-cloud-oauth.md).

## Multi-user summary

Use one user-owned Google Cloud project and client, then create one independently
authorized config entry per person. Use a private browser window or explicitly switch
Google accounts for each authorization. Home Assistant asks for a person name, and
Health Sync derives the stable person slug from that name. Users do not enter or choose
the slug directly. The derived slug provides stable entity unique IDs and service and
action targeting within the existing config entry. The normalized history store is keyed
by the Home Assistant config-entry ID, not by the person slug. Use **Reauthenticate** on
that existing entry so both identity mechanisms remain attached to the same entry.
[Multi-user setup](docs/multi-user.md) explains the per-person option, consent, and
entity-registry sequence.

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
- Body-fat percentage
- Height

Weight is disabled by default in the entity registry. Body-fat percentage and Height use
the same default. Enabling an entity and opting in to body measurements are separate
steps. The per-person `include_body_measurements` option starts a bounded
90-day normalized backfill for weight, body-fat percentage, and height. After you enable the
entity, its state may be unavailable until body measurements are opted in and Google
supplies usable data. A valid zero is retained as data; unavailable means no usable value
was obtained, not that the entity is disabled.

Expanded metrics use reconciled daily summaries and daily rollups. For the
current day, active-zone minutes, floors, sedentary minutes, and
heart-rate-zone minutes use reconciled intervals until Google publishes the
daily rollup. A published daily rollup takes precedence.
Only expanded-metric polling avoids raw high-volume streams; core source attribution transiently inspects raw records.
Neither path stores raw API payloads or Google identifiers.

## Optional nutrition and paired devices

Enable `include_nutrition`, `include_paired_devices`, or both from one person's Health
Sync options. If the saved authorization lacks the matching optional scope, Home Assistant
starts reauthorization for that same config entry. Declining an optional permission
leaves baseline sensors working, while the declined capability remains unavailable.

Nutrition adds Calories consumed today and Water consumed today from Google's
`nutrition-log` and `hydration-log` all-source results for the current local day.
Nutrition has no historical backfill in this release. Daily normalized nutrition begins
with the first successful opt-in refresh.

Paired devices add one Home Assistant service device for each Google paired tracker or
scale, with Battery level and Paired-device last sync entities. The paired-device
timestamp is Fitbit mobile-device sync time reported by Google's `lastSyncTime`; it is
not Health Sync API refresh time and Health Sync does not cause the wearable or mobile
application to synchronize. Paired metadata is current only and is absent from normalized
history.

## Refresh contract

A fully successful, non-paginated refresh of baseline capabilities makes
36 logical data requests when body measurements are disabled and 39 when body measurements are enabled. This includes core
raw source-attribution requests, core and expanded reconciled requests, the wearable
steps request, current-day interval fallbacks, and expanded daily rollups.
Nutrition adds two current-day reconcile requests. Paired devices add one list request.
With every option enabled, a non-paginated refresh therefore makes 42 logical data requests.
Pagination can increase the actual HTTP request count.
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
