# Changelog

All notable public changes to Health Sync by ResiyHome are recorded here.

## 1.1.0-beta.2 - 2026-08-06

### Fixed

- Corrected the Total calories burned today daily-rollup request so its page
  count remains within Google's maximum query-duration validation.
- Added a bounded historical Height lookup when no measurement exists in the
  normal body-history window. This retrieves the latest sparse measurement
  without extending every body-metric backfill request.
- Restricted the public-history privacy scanner to commits reachable from the
  checked-out release branch. Unrelated local branches no longer block a clean
  release, while the full patch, path, blob, credential, identity, and PNG
  checks remain active for release history.

### Upgrade And Test

- Install the beta completely through HACS, then restart Home Assistant once.
- Enable the disabled-by-default Total calories burned today and Height entities
  if needed, run the Health Sync refresh action, and verify their live Google
  values before this beta is promoted.
- Existing entity IDs, normalized history, configuration entries, and OAuth
  authorization are preserved.

## 1.1.0-beta.1 - 2026-08-06

### Added

- Added Total calories burned today and detailed sleep-timing entities from the
  existing baseline Google Health authorization.
- Expanded `include_body_measurements` to Weight, Body-fat percentage, and
  Height. All three body entities are created disabled by default in the Home
  Assistant entity registry.
- Added per-person `include_nutrition` support for Calories consumed today and
  Water consumed today through the optional
  `googlehealth.nutrition.readonly` scope. This release starts normalized
  nutrition with the first successful opt-in refresh and has
  no historical nutrition backfill.
- Added per-person `include_paired_devices` support through the optional
  `googlehealth.settings.readonly` scope. Each current Google paired tracker or
  scale can expose Battery level and Paired-device last sync entities.

### Changed

- Upgrades preserve existing config entries and baseline-only authorizations. New
  optional permissions are requested through reauthorization on the same
  person's entry, and declining one leaves baseline sensors working.
- Added the eight static person entity keys without changing existing entity
  identities. Paired battery and sync entities are created dynamically per
  person and paired-device identity.
- Clarified that setup accepts a person name and derives the slug used for
  entity and action identity, while normalized history storage is owned by the
  Home Assistant config-entry ID.
- Extended normalized history with total calories, sleep timing, body fat,
  height, and current-day nutrition fields. Paired-device metadata remains
  current only and is excluded from normalized history.
- Moved automatic redacted refresh diagnostics from warning to debug logging.
  The explicitly requested optional-data availability probe now logs at info.

### Privacy

- Food names, raw nutrition logs, MAC addresses, raw paired-device resource
  IDs, and device feature lists are excluded from normalized storage and
  diagnostics.
- Disabling an optional capability stops future requests. Nutrition values
  already normalized for prior opt-in days and Home Assistant Recorder states
  are not automatically erased; removal and purge decisions remain explicit
  operator actions.

### Upgrade

- Update normally without deleting or re-adding the integration. Baseline
  sensors require no reauthorization.
- Enable nutrition or paired devices from each person's options, then complete
  Google reauthorization for that same person. Repeat per person.

## 1.0.4 - 2026-07-26

### Fixed

- Restored current-day active-zone minutes, floors, sedentary minutes, and
  heart-rate-zone minutes when Google has reconciled interval records but has
  not yet published the corresponding daily rollups.
- Kept Google's daily rollups authoritative whenever they are available, while
  retaining the reconciled interval fallback only for an incomplete current day.
- Added value-free diagnostics that report response counts and metric
  availability without logging health values, OAuth credentials, identifiers,
  or raw API payloads.

### Verified

- Confirmed the fallback against live Google Health data for three enrolled
  people. Active-zone, sedentary, and heart-rate-zone sensors populated for all
  three; floor sensors populated where Google returned current-day floor
  records. The reference person's values remained populated after a second
  manual refresh.
- Confirmed the complete automated suite with 610 passing tests, Ruff, Python
  compilation, and Home Assistant configuration validation.

### Upgrade

- No configuration, OAuth, entity ID, or history migration is required.
- Install the update completely through HACS, then restart Home Assistant once.
- After restart, use the Health Sync refresh action or wait for the next
  15-minute poll.

## 1.0.3 - 2026-07-26

### Fixed

- Accepted Google Health daily rollup responses that end at `23:59:59` on the
  same civil date, matching Google's documented response format.
- Preserved support for daily rollups that end at midnight on the following
  civil date.

### Upgrade

- No configuration, OAuth, entity ID, or history migration is required.
- Install the update completely through HACS, then restart Home Assistant once.
- After restart, use the Health Sync refresh action or wait for the next
  15-minute poll.

## 1.0.2 - 2026-07-26

### Fixed

- Corrected the Google Health `dailyRollUp` request to use the documented
  `CivilDateTime` structure. This restores active-zone minutes, floors, sedentary
  minutes, and heart-rate-zone minute rollups when Google provides source data.
- Accepted daily-rollup boundaries where Google omits the optional midnight time
  object.

### Upgrade

- No configuration, OAuth, entity ID, or history migration is required.
- Install the update completely through HACS, then restart Home Assistant once.
- Daily oxygen saturation and daily VO2 max remain daily summary metrics and may
  appear after Google finishes processing that day's source data.

## 1.0.1 - 2026-07-22

### Fixed

- Reduced core history backfill requests from 31-day to seven-day windows so high-volume
  heart-rate history remains below Google Health pagination safety limits.
- Prevented an oversized history window from repeatedly retrying without advancing its
  durable backfill checkpoint.

### Upgrade

- No configuration or OAuth changes are required. Install the update completely through
  HACS, then restart Home Assistant once.
- Existing person entries, entity IDs, recorder history, and normalized integration history
  are preserved.

## 1.0.0 - 2026-07-22

### Added

- User-owned Google OAuth through Home Assistant Application Credentials with exactly
  three read-only Google Health scopes.
- Independent person-scoped config entries, stable entity identities, reauthentication,
  and per-person body-measurement opt-in.
- Core activity, sleep, workout, heart, source, synchronization, and backfill entities.
- Expanded active-zone, VO2 max, oxygen, respiratory, floors, sedentary, heart-zone, and
  optional weight entities.
- Fifteen-minute current polling, five-minute manual-refresh cooldown, resumable bounded
  history backfill, normalized WebSocket history, and value-free optional-data probing.
- Partial metric-group recovery, explicit unavailable-versus-zero semantics, redacted
  diagnostics, and strict normalized history validation.
- HACS metadata, public brand assets, installation and operations documentation, security
  policy, contribution guidelines, code of conduct, and structured issue forms.

### Privacy

- No hosted/shared OAuth backend.
- Raw Google responses, individual samples, OAuth credentials, Google identifiers, and
  personal health values are excluded from persisted integration history and diagnostics.
- Weight collection is disabled unless each person explicitly opts in.
