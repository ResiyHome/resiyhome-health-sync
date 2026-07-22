# Contributing

Contributions are welcome when they preserve Health Sync's privacy, read-only OAuth, and
person-isolation contracts. By participating, follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## Before opening a change

1. Search existing issues and pull requests.
2. Use the feature-request form for a behavior proposal or the bug form for a defect.
3. Discuss changes that add a Google data type, alter stored history, change OAuth, or
   affect entity identity before implementation.
4. Never use live credentials, real health records, account addresses, household
   fixtures, or copied Home Assistant storage in tests, commits, or issue material.

## Development setup

The project requires Python 3.14.2 or newer.

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
```

Run the focused tests for the area changed and then the complete local suite:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check .
git diff --check
```

Some WebSocket tests require permission to bind a loopback socket. If the environment
blocks that operation, report the exact skipped or failed command instead of claiming the
suite passed.

## Change requirements

- Keep the integration domain `resiyhome_health_sync` and preserve stable person-scoped
  entity unique IDs unless a reviewed migration is included.
- Request only the three scopes in `const.py`; do not add a hosted or shared OAuth client.
- Use synthetic fixtures with no real values or identifiers.
- Add a failing test first for behavior changes and capture the RED/GREEN commands.
- Update the entity catalog, privacy guide, and changelog when public behavior changes.
- Keep logs, diagnostics, services, and issue examples free of secrets and health values.
- Keep pull requests focused and explain compatibility, privacy, migration, and test impact.

## Pull requests

Describe the user-visible problem, implementation, tests, documentation changes, and any
remaining risk. Confirm that no credential, health value, personal identifier, or private
development artifact is included. Maintainers may request revisions or decline changes
that increase privacy risk, broaden OAuth, or break existing entities without migration.
