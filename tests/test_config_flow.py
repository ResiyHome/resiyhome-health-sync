"""Tests for secure Health Sync enrollment."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from homeassistant import config_entries
from homeassistant.components.application_credentials import ClientCredential
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.selector import TextSelector, TextSelectorType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.resiyhome_health_sync.application_credentials import (
    HealthSyncOAuth2Implementation,
)
from custom_components.resiyhome_health_sync.capabilities import validate_granted_scopes
from custom_components.resiyhome_health_sync.const import (
    BASE_SCOPES,
    DOMAIN,
    NUTRITION_SCOPE,
    SCOPES,
    SETTINGS_SCOPE,
)

PERSON_INPUT = {"person_name": "  Sample Alpha  "}


@pytest.fixture(autouse=True)
def register_local_flow_handler() -> Any:
    """Register the local flow without scanning editable-install import hooks."""
    from custom_components.resiyhome_health_sync import config_flow as _config_flow  # noqa: F401

    with (
        patch("homeassistant.config_entries._load_integration", new=AsyncMock()),
        patch(
            "homeassistant.config_entries._support_single_config_entry_only",
            new=AsyncMock(return_value=False),
        ),
    ):
        yield


def _oauth_token(*, scopes: tuple[str, ...] = SCOPES) -> dict[str, Any]:
    """Return a Home Assistant OAuth helper token dictionary."""
    return {
        "access" + "_token": "secret-access-token",
        "refresh" + "_token": "secret-refresh-token",
        "expires_in": 3600,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).timestamp(),
        "scope": " ".join(scopes),
        "token_type": "Bearer",
    }


def _register_oauth_implementation(hass: Any) -> HealthSyncOAuth2Implementation:
    """Register one test OAuth implementation for config-flow use."""
    from custom_components.resiyhome_health_sync.config_flow import HealthSyncConfigFlow

    implementation = HealthSyncOAuth2Implementation(
        hass,
        "family-google-client",
        ClientCredential("public-client-id", "top-secret-client-value", "Family Google"),
    )
    HealthSyncConfigFlow.async_register_implementation(hass, implementation)
    return implementation


async def _advance_to_external_auth(
    hass: Any, current_request_with_host: None
) -> dict[str, Any]:
    _register_oauth_implementation(hass)
    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(first["flow_id"], PERSON_INPUT)


async def test_person_setup_only_collects_person_name(hass) -> None:
    """Initial setup no longer asks for Google client credentials per person."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    fields = {
        marker.schema: validator for marker, validator in result["data_schema"].schema.items()
    }
    assert list(fields) == ["person_name"]
    assert isinstance(fields["person_name"], TextSelector)
    assert fields["person_name"].config["type"] == TextSelectorType.TEXT


async def test_person_flow_uses_ha_oauth_redirect_and_stores_runtime_token_shape(
    hass, current_request_with_host
) -> None:
    """Enrollment redirects through HA OAuth and stores the existing runtime data shape."""
    implementation = _register_oauth_implementation(hass)
    implementation.async_resolve_external_data = AsyncMock(return_value=_oauth_token())  # type: ignore[method-assign]

    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    external = await hass.config_entries.flow.async_configure(first["flow_id"], PERSON_INPUT)

    assert external["type"] is FlowResultType.EXTERNAL_STEP
    query = parse_qs(urlparse(external["url"]).query)
    assert query["redirect_uri"] == ["https://example.com/auth/external/callback"]
    assert query["scope"] == [" ".join(SCOPES)]
    assert "top-secret-client-value" not in external["url"]

    callback = await hass.config_entries.flow.async_configure(
        external["flow_id"],
        {"code": "one-time-secret-code", "state": {"redirect_uri": query["redirect_uri"][0]}},
    )
    assert callback["type"] is FlowResultType.EXTERNAL_STEP_DONE
    result = await hass.config_entries.flow.async_configure(callback["flow_id"])

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Sample Alpha"
    assert result["data"]["person_name"] == "Sample Alpha"
    assert result["data"]["person_slug"] == "sample_alpha"
    assert result["data"]["auth_implementation"] == "family-google-client"
    assert result["data"]["access_token"] == "secret-access-token"
    assert result["data"]["refresh_token"] == "secret-refresh-token"
    assert result["data"]["scopes"] == list(SCOPES)
    assert isinstance(datetime.fromisoformat(result["data"]["expires_at"]), datetime)
    assert "token" not in result["data"]
    assert "authorization_code" not in result["data"]
    assert "one-time-secret-code" not in repr(result)


async def test_duplicate_person_slug_aborts_before_authorization(hass) -> None:
    """A person slug owns exactly one independent config entry."""
    MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={"person_slug": "sample_alpha"},
    ).add_to_hass(hass)

    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(first["flow_id"], PERSON_INPUT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_duplicate_active_person_flow_aborts_as_already_in_progress(
    hass, current_request_with_host
) -> None:
    """Two simultaneous enrollments cannot reserve the same normalized person slug."""
    first = await _advance_to_external_auth(hass, current_request_with_host)
    assert first["type"] is FlowResultType.EXTERNAL_STEP

    second = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(second["flow_id"], PERSON_INPUT)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_in_progress"


async def test_missing_scope_aborts_without_storing_tokens(hass, current_request_with_host) -> None:
    """Google must grant every required read-only scope."""
    implementation = _register_oauth_implementation(hass)
    implementation.async_resolve_external_data = AsyncMock(  # type: ignore[method-assign]
        return_value=_oauth_token(scopes=SCOPES[:2])
    )

    first = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    external = await hass.config_entries.flow.async_configure(first["flow_id"], PERSON_INPUT)
    callback = await hass.config_entries.flow.async_configure(
        external["flow_id"], {"code": "one-time-secret-code", "state": {"redirect_uri": "uri"}}
    )
    result = await hass.config_entries.flow.async_configure(callback["flow_id"])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "missing_scope"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 0


async def test_reauth_updates_only_the_matching_entry(hass, current_request_with_host) -> None:
    """Reauthorization replaces token state without creating another person."""
    implementation = _register_oauth_implementation(hass)
    implementation.async_resolve_external_data = AsyncMock(return_value=_oauth_token())  # type: ignore[method-assign]
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={
            "person_name": "Sample Alpha",
            "person_slug": "sample_alpha",
            "auth_implementation": "family-google-client",
            "access" + "_token": "old-access",
            "refresh" + "_token": "old-refresh",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(SCOPES),
        },
    )
    entry.add_to_hass(hass)
    original_entry_id = entry.entry_id

    first = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    assert first["type"] is FlowResultType.FORM
    assert first["step_id"] == "reauth_confirm"
    external = await hass.config_entries.flow.async_configure(first["flow_id"])
    assert external["type"] is FlowResultType.EXTERNAL_STEP
    callback = await hass.config_entries.flow.async_configure(
        external["flow_id"], {"code": "one-time-secret-code", "state": {"redirect_uri": "uri"}}
    )
    result = await hass.config_entries.flow.async_configure(callback["flow_id"])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["refresh_token"] == "secret-refresh-token"
    assert entry.data["person_slug"] == "sample_alpha"
    assert "authorization_code" not in entry.data
    entries = hass.config_entries.async_entries("resiyhome_health_sync")
    assert len(entries) == 1
    assert entries[0].entry_id == original_entry_id
    assert entries[0].unique_id == "sample_alpha"


async def test_optional_scope_reauth_keeps_entry_when_optional_consent_is_declined(
    hass, current_request_with_host
) -> None:
    """Declining an optional permission cannot replace or disable a baseline entry."""
    implementation = _register_oauth_implementation(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={
            "person_name": "Sample Alpha",
            "person_slug": "sample_alpha",
            "auth_implementation": "family-google-client",
            "access" + "_token": "old-access",
            "refresh" + "_token": "old-refresh",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(BASE_SCOPES),
        },
        options={"include_nutrition": True},
    )
    entry.add_to_hass(hass)
    original_entry_id = entry.entry_id

    assert validate_granted_scopes(entry.data["scopes"], {}).baseline_valid is True

    first = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    external = await hass.config_entries.flow.async_configure(first["flow_id"])
    query = parse_qs(urlparse(external["url"]).query)
    assert query["scope"] == [" ".join((*BASE_SCOPES, NUTRITION_SCOPE))]

    implementation.async_resolve_external_data = AsyncMock(  # type: ignore[method-assign]
        return_value=_oauth_token(scopes=BASE_SCOPES)
    )
    callback = await hass.config_entries.flow.async_configure(
        external["flow_id"],
        {"code": "one-time-secret-code", "state": {"redirect_uri": "uri"}},
    )
    result = await hass.config_entries.flow.async_configure(callback["flow_id"])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.entry_id == original_entry_id
    assert entry.unique_id == "sample_alpha"
    assert entry.data["scopes"] == list(BASE_SCOPES)
    assert entry.options["include_nutrition"] is True
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_active_reauth_with_stale_scope_generation_cannot_update_entry(hass) -> None:
    """An in-flight flow cannot commit credentials for superseded options."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={
            "person_name": "Sample Alpha",
            "person_slug": "sample_alpha",
            "auth_implementation": "family-google-client",
            "access" + "_token": "old-access",
            "refresh" + "_token": "old-refresh",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(BASE_SCOPES),
        },
        options={"include_nutrition": True},
    )
    entry.add_to_hass(hass)
    entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    progress = hass.config_entries.flow.async_progress_by_handler(
        DOMAIN,
        match_context={"entry_id": entry.entry_id},
    )
    flow = hass.config_entries.flow._progress[progress[0]["flow_id"]]
    before = dict(entry.data)

    hass.config_entries.async_update_entry(
        entry,
        options={
            "include_nutrition": True,
            "include_paired_devices": True,
        },
    )
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
        result = await flow.async_oauth_create_entry(
            {
                "auth_implementation": "family-google-client",
                "token": _oauth_token(scopes=(*BASE_SCOPES, NUTRITION_SCOPE)),
            }
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_flow_state"
    assert entry.data == before
    reload.assert_not_awaited()


async def test_replaced_stale_reauth_completion_cannot_update_entry(hass) -> None:
    """A removed flow cannot mutate the entry after its replacement starts."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={
            "person_name": "Sample Alpha",
            "person_slug": "sample_alpha",
            "auth_implementation": "family-google-client",
            "access" + "_token": "old-access",
            "refresh" + "_token": "old-refresh",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(BASE_SCOPES),
        },
        options={"include_nutrition": True},
    )
    entry.add_to_hass(hass)
    entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    first = hass.config_entries.flow.async_progress_by_handler(
        DOMAIN,
        match_context={"entry_id": entry.entry_id},
    )[0]
    stale_flow = hass.config_entries.flow._progress[first["flow_id"]]

    hass.config_entries.flow.async_abort(first["flow_id"])
    entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    replacement = hass.config_entries.flow.async_progress_by_handler(
        DOMAIN,
        match_context={"entry_id": entry.entry_id},
    )[0]
    before = dict(entry.data)

    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
        result = await stale_flow.async_oauth_create_entry(
            {
                "auth_implementation": "family-google-client",
                "token": _oauth_token(scopes=(*BASE_SCOPES, NUTRITION_SCOPE)),
            }
        )
        await hass.async_block_till_done()

    assert replacement["flow_id"] != first["flow_id"]
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "invalid_flow_state"
    assert entry.data == before
    reload.assert_not_awaited()


async def test_reauth_rejects_unknown_scope_without_mutating_entry(
    hass, current_request_with_host
) -> None:
    """A token containing an unsupported scope cannot replace working credentials."""
    implementation = _register_oauth_implementation(hass)
    implementation.async_resolve_external_data = AsyncMock(  # type: ignore[method-assign]
        return_value=_oauth_token(scopes=(*BASE_SCOPES, "https://example.invalid/write"))
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={
            "person_name": "Sample Alpha",
            "person_slug": "sample_alpha",
            "auth_implementation": "family-google-client",
            "access" + "_token": "old-access",
            "refresh" + "_token": "old-refresh",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(BASE_SCOPES),
        },
        options={"include_nutrition": True},
    )
    entry.add_to_hass(hass)
    before = dict(entry.data)

    first = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    external = await hass.config_entries.flow.async_configure(first["flow_id"])
    callback = await hass.config_entries.flow.async_configure(
        external["flow_id"],
        {"code": "one-time-secret-code", "state": {"redirect_uri": "uri"}},
    )
    result = await hass.config_entries.flow.async_configure(callback["flow_id"])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "missing_scope"
    assert entry.data == before


async def test_reconfigure_requests_scopes_for_existing_entry_options(
    hass, current_request_with_host
) -> None:
    """Reconfigure requests the paired-device permission enabled for this person."""
    _register_oauth_implementation(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={
            "person_name": "Sample Alpha",
            "person_slug": "sample_alpha",
            "auth_implementation": "family-google-client",
            "access" + "_token": "old-access",
            "refresh" + "_token": "old-refresh",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(BASE_SCOPES),
        },
        options={"include_paired_devices": True},
    )
    entry.add_to_hass(hass)

    first = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    external = await hass.config_entries.flow.async_configure(first["flow_id"])
    query = parse_qs(urlparse(external["url"]).query)

    assert query["scope"] == [" ".join((*BASE_SCOPES, SETTINGS_SCOPE))]


async def test_reauth_removes_legacy_client_credentials(hass, current_request_with_host) -> None:
    """Legacy OAuth Playground entries move to HA application credentials on reauth."""
    implementation = _register_oauth_implementation(hass)
    implementation.async_resolve_external_data = AsyncMock(return_value=_oauth_token())  # type: ignore[method-assign]
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={
            "person_name": "Sample Alpha",
            "person_slug": "sample_alpha",
            "client_id": "legacy-client-id",
            "client" + "_secret": "legacy-client-secret",
            "access" + "_token": "old-access",
            "refresh" + "_token": "old-refresh",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(SCOPES),
        },
    )
    entry.add_to_hass(hass)

    first = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    external = await hass.config_entries.flow.async_configure(first["flow_id"])
    callback = await hass.config_entries.flow.async_configure(
        external["flow_id"], {"code": "one-time-secret-code", "state": {"redirect_uri": "uri"}}
    )
    result = await hass.config_entries.flow.async_configure(callback["flow_id"])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["auth_implementation"] == "family-google-client"
    assert entry.data["person_slug"] == "sample_alpha"
    assert "client_id" not in entry.data
    assert "client_secret" not in entry.data


async def test_reconfigure_updates_existing_entry_without_duplicate(
    hass, current_request_with_host
) -> None:
    """Manual reconfigure migrates an existing person through HA OAuth."""
    implementation = _register_oauth_implementation(hass)
    implementation.async_resolve_external_data = AsyncMock(return_value=_oauth_token())  # type: ignore[method-assign]
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={
            "person_name": "Sample Alpha",
            "person_slug": "sample_alpha",
            "client_id": "legacy-client-id",
            "client" + "_secret": "legacy-client-secret",
            "access" + "_token": "old-access",
            "refresh" + "_token": "old-refresh",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(SCOPES),
        },
    )
    entry.add_to_hass(hass)

    first = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        data=dict(entry.data),
    )
    assert first["type"] is FlowResultType.FORM
    assert first["step_id"] == "reconfigure_confirm"
    external = await hass.config_entries.flow.async_configure(first["flow_id"])
    callback = await hass.config_entries.flow.async_configure(
        external["flow_id"], {"code": "one-time-secret-code", "state": {"redirect_uri": "uri"}}
    )
    result = await hass.config_entries.flow.async_configure(callback["flow_id"])

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["auth_implementation"] == "family-google-client"
    assert entry.data["refresh_token"] == "secret-refresh-token"
    assert entry.data["person_slug"] == "sample_alpha"
    assert "client_id" not in entry.data
    assert "client_secret" not in entry.data
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_body_measurement_options_default_disabled_and_save_only_selected_person(
    hass,
) -> None:
    """Options are private to one person and do not alter OAuth credentials."""
    sample_alpha = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={
            "person_name": "Sample Alpha",
            "person_slug": "sample_alpha",
            "auth_implementation": "family-google-client",
            "access" + "_token": "sample_alpha-access-token",
            "refresh" + "_token": "sample_alpha-refresh-token",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(SCOPES),
        },
    )
    sample_beta = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Beta",
        unique_id="sample_beta",
        data={
            "person_name": "Sample Beta",
            "person_slug": "sample_beta",
            "auth_implementation": "family-google-client",
            "access" + "_token": "sample_beta-access-token",
            "refresh" + "_token": "sample_beta-refresh-token",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(SCOPES),
        },
        options={"include_body_measurements": False},
    )
    sample_alpha.add_to_hass(hass)
    sample_beta.add_to_hass(hass)
    sample_alpha_data = dict(sample_alpha.data)

    form = await hass.config_entries.options.async_init(sample_alpha.entry_id)

    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "init"
    assert form["data_schema"]({}) == {
        "include_body_measurements": False,
        "include_nutrition": False,
        "include_paired_devices": False,
    }

    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
        saved = await hass.config_entries.options.async_configure(
            form["flow_id"], {"include_body_measurements": True}
        )
        unchanged_form = await hass.config_entries.options.async_init(sample_alpha.entry_id)
        unchanged = await hass.config_entries.options.async_configure(
            unchanged_form["flow_id"], {"include_body_measurements": True}
        )
        await hass.async_block_till_done()

    assert saved["type"] is FlowResultType.CREATE_ENTRY
    assert unchanged["type"] is FlowResultType.CREATE_ENTRY
    reload.assert_awaited_once_with(sample_alpha.entry_id)
    assert sample_alpha.options == {
        "include_body_measurements": True,
        "include_nutrition": False,
        "include_paired_devices": False,
    }
    assert sample_alpha.data == sample_alpha_data
    assert sample_beta.options == {"include_body_measurements": False}
    assert sample_beta.data["refresh_token"] == "sample_beta-refresh-token"


async def test_enabling_optional_scope_starts_one_reauth_without_reload(
    hass,
) -> None:
    """Repeated option submissions cannot duplicate reauth flows or reload early."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={
            "person_name": "Sample Alpha",
            "person_slug": "sample_alpha",
            "auth_implementation": "family-google-client",
            "access" + "_token": "sample-alpha-access",
            "refresh" + "_token": "sample-alpha-refresh",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(BASE_SCOPES),
        },
    )
    entry.add_to_hass(hass)

    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert form["data_schema"]({}) == {
        "include_body_measurements": False,
        "include_nutrition": False,
        "include_paired_devices": False,
    }
    selected = {
        "include_body_measurements": False,
        "include_nutrition": True,
        "include_paired_devices": False,
    }

    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
        saved = await hass.config_entries.options.async_configure(form["flow_id"], selected)
        await hass.async_block_till_done()
        repeated_form = await hass.config_entries.options.async_init(entry.entry_id)
        repeated = await hass.config_entries.options.async_configure(
            repeated_form["flow_id"], selected
        )
        await hass.async_block_till_done()

    reauth_flows = [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(
            DOMAIN,
            match_context={"entry_id": entry.entry_id},
        )
        if flow["context"]["source"] == config_entries.SOURCE_REAUTH
    ]
    assert saved["type"] is FlowResultType.CREATE_ENTRY
    assert repeated["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == selected
    assert len(reauth_flows) == 1
    reload.assert_not_awaited()


@pytest.mark.parametrize(
    "granted_scopes",
    [
        BASE_SCOPES,
        (*BASE_SCOPES, NUTRITION_SCOPE),
    ],
)
async def test_option_enable_through_reauth_performs_exactly_one_reload(
    hass,
    current_request_with_host,
    granted_scopes: tuple[str, ...],
) -> None:
    """Full or partial consent reloads a capability-enable transaction once."""
    implementation = _register_oauth_implementation(hass)
    implementation.async_resolve_external_data = AsyncMock(  # type: ignore[method-assign]
        return_value=_oauth_token(scopes=granted_scopes)
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={
            "person_name": "Sample Alpha",
            "person_slug": "sample_alpha",
            "auth_implementation": "family-google-client",
            "access" + "_token": "old-access",
            "refresh" + "_token": "old-refresh",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(BASE_SCOPES),
        },
    )
    entry.add_to_hass(hass)
    selected = {
        "include_body_measurements": False,
        "include_nutrition": True,
        "include_paired_devices": False,
    }

    form = await hass.config_entries.options.async_init(entry.entry_id)
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
        await hass.config_entries.options.async_configure(form["flow_id"], selected)
        await hass.async_block_till_done()
        reload.assert_not_awaited()

        reauth = hass.config_entries.flow.async_progress_by_handler(
            DOMAIN,
            match_context={"entry_id": entry.entry_id},
        )[0]
        external = await hass.config_entries.flow.async_configure(reauth["flow_id"])
        callback = await hass.config_entries.flow.async_configure(
            external["flow_id"],
            {"code": "one-time-secret-code", "state": {"redirect_uri": "uri"}},
        )
        result = await hass.config_entries.flow.async_configure(callback["flow_id"])
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data["scopes"] == list(granted_scopes)
    reload.assert_awaited_once_with(entry.entry_id)


@pytest.mark.parametrize(
    ("current_options", "current_scopes"),
    [
        (
            {
                "include_body_measurements": False,
                "include_nutrition": False,
                "include_paired_devices": False,
            },
            BASE_SCOPES,
        ),
        (
            {
                "include_body_measurements": False,
                "include_nutrition": True,
                "include_paired_devices": False,
            },
            (*BASE_SCOPES, NUTRITION_SCOPE),
        ),
    ],
)
async def test_deferred_reauth_rechecks_current_options_and_persisted_scopes(
    hass,
    current_options: dict[str, bool],
    current_scopes: tuple[str, ...],
) -> None:
    """A deferred task does nothing when its missing permission race is resolved."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={
            "person_name": "Sample Alpha",
            "person_slug": "sample_alpha",
            "auth_implementation": "family-google-client",
            "access" + "_token": "old-access",
            "refresh" + "_token": "old-refresh",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(BASE_SCOPES),
        },
    )
    entry.add_to_hass(hass)
    selected = {
        "include_body_measurements": False,
        "include_nutrition": True,
        "include_paired_devices": False,
    }
    deferred_tasks: list[Any] = []
    original_create_task = hass.async_create_task

    def capture_deferred_task(
        target: Any,
        name: str | None = None,
        eager_start: bool = True,
    ) -> Any:
        if name == f"Health Sync optional scope reauth {entry.entry_id}":
            deferred_tasks.append(target)
            return MagicMock()
        return original_create_task(target, name, eager_start)

    form = await hass.config_entries.options.async_init(entry.entry_id)
    with (
        patch.object(hass, "async_create_task", side_effect=capture_deferred_task),
        patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload,
    ):
        await hass.config_entries.options.async_configure(form["flow_id"], selected)
        await hass.async_block_till_done()

        assert len(deferred_tasks) == 1
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, "scopes": list(current_scopes)},
            options=current_options,
        )
        await deferred_tasks[0]
        await hass.async_block_till_done()

    assert (
        hass.config_entries.flow.async_progress_by_handler(
            DOMAIN,
            match_context={"entry_id": entry.entry_id},
        )
        == []
    )
    reload.assert_not_awaited()


async def test_enabling_already_granted_optional_scope_does_not_start_reauth(
    hass,
) -> None:
    """An available optional permission only updates options and reloads the entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={
            "person_name": "Sample Alpha",
            "person_slug": "sample_alpha",
            "auth_implementation": "family-google-client",
            "access" + "_token": "sample-alpha-access",
            "refresh" + "_token": "sample-alpha-refresh",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": [*BASE_SCOPES, NUTRITION_SCOPE],
        },
    )
    entry.add_to_hass(hass)
    selected = {
        "include_body_measurements": False,
        "include_nutrition": True,
        "include_paired_devices": False,
    }

    form = await hass.config_entries.options.async_init(entry.entry_id)
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
        await hass.config_entries.options.async_configure(form["flow_id"], selected)
        await hass.async_block_till_done()

    assert (
        hass.config_entries.flow.async_progress_by_handler(
            DOMAIN,
            match_context={"entry_id": entry.entry_id},
        )
        == []
    )
    reload.assert_awaited_once_with(entry.entry_id)


async def test_disabling_optional_capability_reloads_once_without_reauth(hass) -> None:
    """Disabling an optional capability applies with one ordinary reload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={
            "person_name": "Sample Alpha",
            "person_slug": "sample_alpha",
            "auth_implementation": "family-google-client",
            "access" + "_token": "sample-alpha-access",
            "refresh" + "_token": "sample-alpha-refresh",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": [*BASE_SCOPES, NUTRITION_SCOPE],
        },
        options={
            "include_body_measurements": False,
            "include_nutrition": True,
            "include_paired_devices": False,
        },
    )
    entry.add_to_hass(hass)
    selected = {
        "include_body_measurements": False,
        "include_nutrition": False,
        "include_paired_devices": False,
    }

    form = await hass.config_entries.options.async_init(entry.entry_id)
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
        await hass.config_entries.options.async_configure(form["flow_id"], selected)
        await hass.async_block_till_done()

    assert (
        hass.config_entries.flow.async_progress_by_handler(
            DOMAIN,
            match_context={"entry_id": entry.entry_id},
        )
        == []
    )
    reload.assert_awaited_once_with(entry.entry_id)


async def test_enabling_another_optional_scope_replaces_stale_reauth(
    hass, current_request_with_host
) -> None:
    """An active reauth cannot strand a capability enabled after that flow started."""
    _register_oauth_implementation(hass)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Sample Alpha",
        unique_id="sample_alpha",
        data={
            "person_name": "Sample Alpha",
            "person_slug": "sample_alpha",
            "auth_implementation": "family-google-client",
            "access" + "_token": "sample-alpha-access",
            "refresh" + "_token": "sample-alpha-refresh",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(BASE_SCOPES),
        },
        options={
            "include_body_measurements": False,
            "include_nutrition": True,
            "include_paired_devices": False,
        },
    )
    entry.add_to_hass(hass)
    entry.async_start_reauth(hass)
    await hass.async_block_till_done()

    selected = {
        "include_body_measurements": False,
        "include_nutrition": True,
        "include_paired_devices": True,
    }
    form = await hass.config_entries.options.async_init(entry.entry_id)
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()):
        await hass.config_entries.options.async_configure(form["flow_id"], selected)
        await hass.async_block_till_done()

    reauth_flows = [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(
            DOMAIN,
            match_context={"entry_id": entry.entry_id},
        )
        if flow["context"]["source"] == config_entries.SOURCE_REAUTH
    ]
    assert len(reauth_flows) == 1

    external = await hass.config_entries.flow.async_configure(reauth_flows[0]["flow_id"])
    query = parse_qs(urlparse(external["url"]).query)
    assert query["scope"] == [
        " ".join((*BASE_SCOPES, NUTRITION_SCOPE, SETTINGS_SCOPE))
    ]


def test_reconfigure_strings_do_not_require_missing_placeholders() -> None:
    """Reconfigure strings are rendered without description placeholders."""
    strings = json.loads(
        (
            Path(__file__).parents[1]
            / "custom_components/resiyhome_health_sync/strings.json"
        ).read_text()
    )
    translation = json.loads(
        (
            Path(__file__).parents[1]
            / "custom_components/resiyhome_health_sync/translations/en.json"
        ).read_text()
    )

    for source in (strings, translation):
        description = source["config"]["step"]["reconfigure_confirm"]["description"]
        assert "{name}" not in description
