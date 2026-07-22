# Changelog

All notable public changes to Health Sync by ResiyHome are recorded here.

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
