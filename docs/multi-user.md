# Multi-user Setup

Health Sync isolates authorization, entities, options, and normalized history by person.
The intended arrangement is one household-owned Google Cloud project and OAuth client,
with one config entry per person.

## Model

- **Shared administration:** one Google Cloud project and one Web application OAuth
  client controlled by the household or group administrator.
- **Independent consent:** one config entry per person, with each person authorizing
  their own Google account.
- **Stable entity identity:** each entry has a unique, stable person slug derived at first
  enrollment. The person slug anchors entity unique IDs and service targeting.
- **Storage isolation:** The Home Assistant config entry ID independently isolates that
  person's normalized history store.
- **Per-person privacy choice:** body measurements are opted in separately for each
  person and are off by default.

Sharing a client means its administrator controls the consent configuration and client
secret. It does not merge Google accounts or Health Sync history. Everyone should agree
who controls the project before enrollment.

## Enroll each person safely

1. Finish the shared Google Cloud and Application Credentials setup once.
2. Add every participating Google account to **Audience > Test users** if the project is
   in Testing.
3. Open a private browser window for the first person, or explicitly sign out and switch
   Google accounts before starting authorization.
4. In Home Assistant, add **Health Sync by ResiyHome**.
5. Enter a unique display name whose generated person slug will remain stable. Avoid
   punctuation-only names and do not reuse another person's name.
6. Select the shared Application Credentials client.
7. At Google, check the active account before granting the three read-only scopes.
8. Return to Home Assistant and confirm exactly one new config entry and person-scoped
   entity set.
9. Close the private browser window before enrolling the next person.
10. Repeat from step 3 for each additional person.

Private browsing does not make the authorization anonymous; it reduces accidental reuse
of the previous person's active Google session.

## Body measurements

Weight collection is disabled by default. For a person who knowingly opts in:

1. Open that person's Health Sync config entry.
2. Select **Configure**.
3. Enable `include_body_measurements` and submit.
4. Confirm the disabled-by-default Weight entity exists in the entity registry; enable
   the entity separately if it should be visible.

Opting in starts a bounded 90-day weight backfill for that person. Opting out removes
stored weight from normalized integration history and clears the current weight snapshot.
It does not purge Home Assistant recorder states that may already have been retained.

## Renewal and account changes

When an entry reports an authorization problem, select that existing config entry and
choose **Reauthenticate**. Use the same intended Google account. Never add the same person
again to repair OAuth; a duplicate would not preserve the original entry identity and
could produce competing entity sets.

If a person changes Google accounts, decide whether that should be a new identity. Using
Reauthenticate preserves the existing person slug, entities, and retained history while
changing the authorized source account, so document that decision privately.
