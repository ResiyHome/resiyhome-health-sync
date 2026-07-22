"""Tests for the local Google Health probe script."""

from __future__ import annotations

import json

import pytest

from custom_components.resiyhome_health_sync.const import SCOPES
from scripts import health_sync_probe


def test_probe_uses_rebranded_environment_variables() -> None:
    assert health_sync_probe.CLIENT_SECRET_ENV == "RESIYHOME_HEALTH_SYNC_CLIENT_SECRET_JSON"
    assert health_sync_probe.AUTH_CODE_ENV == "RESIYHOME_HEALTH_SYNC_AUTHORIZATION_CODE"


def test_probe_loads_oauth_client_secret_without_leaking_values(tmp_path) -> None:
    credentials = tmp_path / "client_secret.json"
    client_id = "-".join(("synthetic", "client", "id"))
    synthetic_credential = "-".join(("synthetic", "client", "value"))
    credentials.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": client_id,
                    "".join(("client", "_secret")): synthetic_credential,
                    "redirect_uris": ["https://developers.google.com/oauthplayground"],
                }
            }
        ),
        encoding="utf-8",
    )

    result = health_sync_probe.load_oauth_client(credentials)

    assert result.client_id == client_id
    assert result.client_secret == synthetic_credential
    assert result.redirect_uri == "https://developers.google.com/oauthplayground"
    assert synthetic_credential not in repr(result)


def test_probe_rejects_write_scopes() -> None:
    with pytest.raises(SystemExit, match="read-only"):
        health_sync_probe.validate_scopes((*SCOPES, "https://example.invalid/write"))


def test_probe_summary_never_contains_tokens_or_identifiers() -> None:
    text = health_sync_probe.format_probe_summary(
        granted_scopes=SCOPES,
        counts={"steps": 12, "sleep": 1},
        source_labels={"fitbit", "health_kit"},
    )

    assert "granted_scopes=3" in text
    assert "steps=12" in text
    assert "fitbit" in text
    assert "health_kit" in text
    assert "access_token" not in text
    assert "refresh_token" not in text
    assert "client_id" not in text
