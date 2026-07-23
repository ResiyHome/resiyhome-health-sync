# Changelog

All notable public changes to Health Sync by ResiyHome are recorded here.

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
