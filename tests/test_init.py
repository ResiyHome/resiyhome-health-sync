"""Tests for the Health Sync config-entry lifecycle."""

import asyncio
import gc
import json
import traceback
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.components.application_credentials import ClientCredential
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_entry_oauth2_flow
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_integration,
    mock_platform,
)

from custom_components.resiyhome_health_sync import (
    _async_run_backfill,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.resiyhome_health_sync.api import (
    AuthenticationError,
    OAuthTokenState,
    UpdateFailed,
)
from custom_components.resiyhome_health_sync.application_credentials import (
    HealthSyncOAuth2Implementation,
)
from custom_components.resiyhome_health_sync.capabilities import (
    CapabilityId,
    validate_granted_scopes,
)
from custom_components.resiyhome_health_sync.const import (
    BASE_SCOPES,
    DOMAIN,
    NUTRITION_SCOPE,
    SCAN_INTERVAL,
    SCOPES,
)
from custom_components.resiyhome_health_sync.models import (
    CoordinatorSnapshot,
    DailySummary,
    ExpandedDailyMetrics,
)
from custom_components.resiyhome_health_sync.storage import (
    HealthHistoryStore,
    HistoryStoreError,
)


def _entry(
    hass, *, person_name: str = "Sample Alpha", person_slug: str = "sample_alpha"
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=person_name,
        unique_id=person_slug,
        data={
            "person_name": person_name,
            "person_slug": person_slug,
            "client_id": f"{person_slug}-client-id",
            "client" + "_secret": f"{person_slug}-client-secret",
            "access" + "_token": f"{person_slug}-access-token",
            "refresh" + "_token": f"{person_slug}-refresh-token",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(SCOPES),
        },
    )
    entry.add_to_hass(hass)
    return entry


def _application_credential_entry(
    hass, *, person_name: str = "Sample Alpha", person_slug: str = "sample_alpha"
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=person_name,
        unique_id=person_slug,
        data={
            "person_name": person_name,
            "person_slug": person_slug,
            "auth_implementation": "family-google-client",
            "access" + "_token": f"{person_slug}-access-token",
            "refresh" + "_token": f"{person_slug}-refresh-token",
            "expires_at": "2042-07-13T12:00:00+00:00",
            "scopes": list(SCOPES),
        },
    )
    entry.add_to_hass(hass)
    return entry


def _register_application_credential_implementation(
    hass,
) -> HealthSyncOAuth2Implementation:
    implementation = HealthSyncOAuth2Implementation(
        hass,
        "family-google-client",
        ClientCredential("family-client-id", "family-client-secret", "Family Google"),
    )
    config_entry_oauth2_flow.async_register_implementation(
        hass,
        DOMAIN,
        implementation,
    )
    return implementation


def _oauth_token() -> dict[str, object]:
    return {
        "access" + "_token": "new-access-token",
        "refresh" + "_token": "new-refresh-token",
        "expires_in": 3600,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).timestamp(),
        "scope": " ".join(SCOPES),
        "token_type": "Bearer",
    }


def _register_real_entry_lifecycle(hass) -> None:
    """Register the real lifecycle behind Home Assistant's config-entry manager."""
    module = MockModule(
        DOMAIN,
        async_setup_entry=async_setup_entry,
        async_unload_entry=async_unload_entry,
        partial_manifest={"config_flow": True, "version": "0.1.0"},
    )
    mock_integration(hass, module, built_in=False)
    from custom_components.resiyhome_health_sync import config_flow

    mock_platform(hass, f"{DOMAIN}.config_flow", config_flow, built_in=False)


def _lifecycle_patches(*, refresh_error: Exception | None = None):
    client = MagicMock()
    history = MagicMock()
    history.async_load = AsyncMock(return_value=[])
    history.async_shutdown = AsyncMock()
    coordinator = MagicMock()
    coordinator.async_refresh_current = AsyncMock()
    if refresh_error is not None:
        coordinator.async_refresh_current.side_effect = refresh_error
    coordinator.async_set_updated_data = MagicMock()
    coordinator.async_backfill_step = AsyncMock()
    coordinator.data.backfill_complete = True
    return (
        client,
        history,
        coordinator,
        (
            patch(
                "custom_components.resiyhome_health_sync.GoogleHealthClient", return_value=client
            ),
            patch(
                "custom_components.resiyhome_health_sync.HealthHistoryStore", return_value=history
            ),
            patch(
                "custom_components.resiyhome_health_sync.HealthSyncCoordinator",
                return_value=coordinator,
            ),
        ),
    )


async def test_setup_loads_history_refreshes_then_forwards_and_starts_backfill(hass) -> None:
    """Platforms and backfill start only after cached history and current data exist."""
    entry = _entry(hass)
    client, history, coordinator, patches = _lifecycle_patches()
    events: list[str] = []
    history.async_load.side_effect = lambda: events.append("history") or []
    coordinator.async_refresh_current.side_effect = lambda: (
        events.append("refresh") or coordinator.data
    )

    async def forward(*_args) -> None:
        events.append("platforms")

    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(
            hass.config_entries, "async_forward_entry_setups", side_effect=forward
        ) as forward_mock,
    ):
        assert await async_setup_entry(hass, entry) is True
        await hass.async_block_till_done()

    assert events[:3] == ["history", "refresh", "platforms"]
    forward_mock.assert_awaited_once_with(entry, ("sensor", "binary_sensor"))
    coordinator.async_set_updated_data.assert_called_once_with(coordinator.data)
    assert entry.runtime_data.client is client
    assert entry.runtime_data.history is history
    assert entry.runtime_data.coordinator is coordinator
    history.async_shutdown.assert_not_awaited()


@pytest.mark.parametrize("enabled", [False, True])
async def test_setup_passes_body_measurement_option_to_coordinator(hass, enabled: bool) -> None:
    entry = _entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={"include_body_measurements": enabled},
    )
    _client, _history, coordinator, patches = _lifecycle_patches()

    with (
        patches[0],
        patches[1],
        patches[2] as coordinator_class,
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        assert await async_setup_entry(hass, entry) is True
        await hass.async_block_till_done()

    assert coordinator_class.call_args.kwargs["include_body_measurements"] is enabled
    assert entry.runtime_data.coordinator is coordinator


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (AuthenticationError("secret auth detail"), ConfigEntryAuthFailed),
        (UpdateFailed("secret transport detail"), ConfigEntryNotReady),
    ],
)
async def test_setup_maps_first_refresh_failures(hass, caplog, error, expected) -> None:
    """Auth failures request reauth while transient failures remain retryable."""
    entry = _entry(hass)
    _client, history, _coordinator, patches = _lifecycle_patches(refresh_error=error)

    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ) as forward_mock,
        pytest.raises(expected) as raised,
    ):
        await async_setup_entry(hass, entry)

    forward_mock.assert_not_awaited()
    exposed = "".join(traceback.format_exception(raised.value)) + caplog.text
    assert "secret auth detail" not in exposed
    assert "secret transport detail" not in exposed
    history.async_shutdown.assert_awaited_once_with()


async def test_setup_shuts_history_after_coordinator_construction_failure(hass) -> None:
    """The earliest failure after store construction still closes that store."""
    entry = _entry(hass)
    _client, history, _coordinator, patches = _lifecycle_patches()
    failure = RuntimeError("coordinator construction failure marker")

    with (
        patches[0],
        patches[1],
        patch(
            "custom_components.resiyhome_health_sync.HealthSyncCoordinator",
            side_effect=failure,
        ),
        pytest.raises(RuntimeError, match="coordinator construction failure marker"),
    ):
        await async_setup_entry(hass, entry)

    history.async_load.assert_not_awaited()
    history.async_shutdown.assert_awaited_once_with()
    assert not hasattr(entry, "runtime_data")


async def test_setup_shuts_history_after_load_failure(hass) -> None:
    """A rejected history load closes its instance before setup reports repair."""
    entry = _entry(hass)
    _client, history, coordinator, patches = _lifecycle_patches()
    history.async_load.side_effect = HistoryStoreError("corrupt history")

    with (
        patches[0],
        patches[1],
        patches[2],
        pytest.raises(ConfigEntryError, match="requires repair"),
    ):
        await async_setup_entry(hass, entry)

    history.async_shutdown.assert_awaited_once_with()
    coordinator.async_refresh_current.assert_not_awaited()
    assert not hasattr(entry, "runtime_data")


async def test_cancelled_setup_shuts_history_before_propagating(hass) -> None:
    """Cancellation after store construction cannot abandon delayed callbacks."""
    entry = _entry(hass)
    _client, history, coordinator, patches = _lifecycle_patches()
    refresh_started = asyncio.Event()
    shutdown_finished = asyncio.Event()

    async def blocked_refresh() -> None:
        refresh_started.set()
        await asyncio.Event().wait()

    async def shutdown_history() -> None:
        shutdown_finished.set()

    coordinator.async_refresh_current.side_effect = blocked_refresh
    history.async_shutdown.side_effect = shutdown_history

    with patches[0], patches[1], patches[2]:
        setup = asyncio.create_task(async_setup_entry(hass, entry))
        await refresh_started.wait()
        setup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await setup

    assert shutdown_finished.is_set()
    history.async_shutdown.assert_awaited_once_with()
    assert not hasattr(entry, "runtime_data")


async def test_refreshed_tokens_update_only_the_matching_config_entry(hass) -> None:
    """The API callback persists refreshed credentials without logging values."""
    entry = _entry(hass)
    _client, _history, _coordinator, patches = _lifecycle_patches()

    with (
        patches[0] as client_class,
        patches[1],
        patches[2],
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        await async_setup_entry(hass, entry)

    callback = client_class.call_args.kwargs["token_update_callback"]
    assert client_class.call_args.kwargs["redirect_uri"] == (
        "https://developers.google.com/oauthplayground"
    )
    state = OAuthTokenState(
        **{
            "access" + "_token": "new-access-token",
            "refresh" + "_token": "new-refresh-token",
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "scopes": frozenset(SCOPES),
        }
    )
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
        await callback(state)
        await hass.async_block_till_done()

    reload.assert_not_awaited()
    assert entry.data["access_token"] == "new-access-token"
    assert entry.data["refresh_token"] == "new-refresh-token"
    assert entry.data["client_secret"] == "sample_alpha-client-secret"
    assert entry.data["person_slug"] == "sample_alpha"


@pytest.mark.parametrize(
    ("source", "expected_reason"),
    [
        (config_entries.SOURCE_REAUTH, "reauth_successful"),
        (config_entries.SOURCE_RECONFIGURE, "reconfigure_successful"),
    ],
)
async def test_oauth_entry_update_schedules_exactly_one_reload(
    hass,
    current_request_with_host,
    source: str,
    expected_reason: str,
) -> None:
    """Reauth and reconfigure use their own single reload path."""
    _register_real_entry_lifecycle(hass)
    implementation = _register_application_credential_implementation(hass)
    implementation.async_resolve_external_data = AsyncMock(return_value=_oauth_token())  # type: ignore[method-assign]
    entry = _application_credential_entry(hass)
    _client, _history, _coordinator, patches = _lifecycle_patches()

    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
            first = await hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": source, "entry_id": entry.entry_id},
                data=dict(entry.data),
            )
            external = await hass.config_entries.flow.async_configure(first["flow_id"])
            callback = await hass.config_entries.flow.async_configure(
                external["flow_id"],
                {"code": "one-time-secret-code", "state": {"redirect_uri": "uri"}},
            )
            result = await hass.config_entries.flow.async_configure(callback["flow_id"])
            await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == expected_reason
    reload.assert_awaited_once_with(entry.entry_id)


async def test_application_credential_entry_uses_registered_oauth_client(hass) -> None:
    """New HA OAuth entries pull client settings from application credentials."""
    _register_application_credential_implementation(hass)
    entry = _application_credential_entry(hass)
    _client, _history, _coordinator, patches = _lifecycle_patches()

    with (
        patches[0] as client_class,
        patches[1],
        patches[2],
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        await async_setup_entry(hass, entry)

    assert client_class.call_args.kwargs["client_id"] == "family-client-id"
    assert client_class.call_args.kwargs["client_secret"] == "family-client-secret"
    assert "client_id" not in entry.data
    assert "client_secret" not in entry.data


async def test_baseline_entry_with_missing_optional_scope_still_sets_up(hass) -> None:
    """An unavailable optional capability cannot turn valid baseline access into reauth."""
    _register_application_credential_implementation(hass)
    entry = _application_credential_entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        options={
            "include_body_measurements": False,
            "include_nutrition": True,
            "include_paired_devices": False,
        },
    )
    _client, _history, _coordinator, patches = _lifecycle_patches()

    with (
        patches[0] as client_class,
        patches[1],
        patches[2],
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        assert await async_setup_entry(hass, entry) is True

    scope_grant = client_class.call_args.kwargs["scope_grant"]
    assert scope_grant == validate_granted_scopes(BASE_SCOPES, entry.options)
    assert entry.runtime_data.scope_grant is scope_grant
    assert scope_grant.baseline_valid is True
    assert scope_grant.enabled_capabilities >= {
        CapabilityId.CORE_ACTIVITY,
        CapabilityId.SLEEP,
        CapabilityId.NUTRITION,
    }
    assert scope_grant.available_capabilities >= {
        CapabilityId.CORE_ACTIVITY,
        CapabilityId.SLEEP,
    }
    assert scope_grant.missing_optional_scopes == {NUTRITION_SCOPE}
    assert (
        hass.config_entries.flow.async_progress_by_handler(
            DOMAIN,
            match_context={"entry_id": entry.entry_id},
        )
        == []
    )


async def test_auth_implementation_takes_precedence_over_legacy_client_fields(hass) -> None:
    """Stale legacy fields cannot override HA application credentials."""
    _register_application_credential_implementation(hass)
    entry = _application_credential_entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            "client_id": "legacy-client-id",
            "client" + "_secret": "legacy-client-secret",
        },
    )
    _client, _history, _coordinator, patches = _lifecycle_patches()

    with (
        patches[0] as client_class,
        patches[1],
        patches[2],
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        await async_setup_entry(hass, entry)

    assert client_class.call_args.kwargs["client_id"] == "family-client-id"
    assert client_class.call_args.kwargs["client_secret"] == "family-client-secret"


async def test_token_update_retains_refresh_token_when_replacement_is_absent(hass) -> None:
    """A refresh response cannot erase the durable refresh credential."""
    entry = _entry(hass)
    _client, _history, _coordinator, patches = _lifecycle_patches()

    with (
        patches[0] as client_class,
        patches[1],
        patches[2],
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        await async_setup_entry(hass, entry)

    callback = client_class.call_args.kwargs["token_update_callback"]
    await callback(
        OAuthTokenState(
            **{
                "access" + "_token": "new-access-token",
                "refresh" + "_token": "",
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
                "scopes": frozenset(SCOPES),
            }
        )
    )

    assert entry.data["access_token"] == "new-access-token"
    assert entry.data["refresh_token"] == "sample_alpha-refresh-token"


async def test_token_update_persists_actual_supported_optional_scopes(hass) -> None:
    """Refresh persistence cannot erase an optional permission retained by Google."""
    entry = _entry(hass)
    _client, _history, _coordinator, patches = _lifecycle_patches()

    with (
        patches[0] as client_class,
        patches[1],
        patches[2],
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        await async_setup_entry(hass, entry)

    callback = client_class.call_args.kwargs["token_update_callback"]
    await callback(
        OAuthTokenState(
            **{
                "access" + "_token": "new-access-token",
                "refresh" + "_token": "new-refresh-token",
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
                "scopes": frozenset((*BASE_SCOPES, NUTRITION_SCOPE)),
            }
        )
    )

    assert entry.data["scopes"] == [*BASE_SCOPES, NUTRITION_SCOPE]


@pytest.mark.parametrize(
    ("updates"),
    [
        {"access" + "_token": ""},
        {"access" + "_token": None},
        {"refresh" + "_token": ""},
        {"refresh" + "_token": 42},
        {"expires_at": "not-a-date"},
        {"expires_at": "2042-07-13T12:00:00"},
        {"scopes": None},
        {"scopes": "malformed"},
        {"scopes": [*SCOPES, 1]},
        {"scopes": [*SCOPES, "https://example.invalid/write"]},
    ],
)
async def test_invalid_persisted_oauth_state_requires_reauthentication(hass, updates) -> None:
    """Invalid stored OAuth state is renewable authorization, not fatal configuration."""
    entry = _entry(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, **updates},
    )

    with patch("custom_components.resiyhome_health_sync.GoogleHealthClient") as client_class:
        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(hass, entry)

    client_class.assert_not_called()


@pytest.mark.parametrize("missing_field", ["access_token", "refresh_token", "expires_at", "scopes"])
async def test_missing_persisted_oauth_state_requires_reauthentication(
    hass, missing_field: str
) -> None:
    """Missing durable OAuth fields start reauth before any API client is created."""
    entry = _entry(hass)
    data = dict(entry.data)
    data.pop(missing_field)
    hass.config_entries.async_update_entry(entry, data=data)

    with patch("custom_components.resiyhome_health_sync.GoogleHealthClient") as client_class:
        with pytest.raises(ConfigEntryAuthFailed):
            await async_setup_entry(hass, entry)

    client_class.assert_not_called()


async def test_background_auth_failure_starts_one_reauth_and_exits_secret_safe(
    hass, caplog
) -> None:
    """Revoked backfill authorization starts reauth once without leaking error text."""
    entry = _entry(hass)
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.data.backfill_complete = False
    coordinator.async_backfill_step = AsyncMock(
        side_effect=AuthenticationError("secret revoked-token detail")
    )
    entry.runtime_data = MagicMock(coordinator=coordinator)

    with (
        patch("custom_components.resiyhome_health_sync.asyncio.sleep", new=AsyncMock()),
        patch.object(entry, "async_start_reauth") as start_reauth,
    ):
        await _async_run_backfill(entry)

    start_reauth.assert_called_once_with(hass)
    coordinator.async_backfill_step.assert_awaited_once()
    assert "secret revoked-token detail" not in caplog.text


async def test_background_backfill_waits_before_first_window(hass) -> None:
    """Startup cannot immediately import history windows on the HA event loop."""
    entry = _entry(hass)
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.data.backfill_complete = False
    coordinator.async_backfill_step = AsyncMock(return_value=coordinator.data)
    entry.runtime_data = MagicMock(coordinator=coordinator)
    delays: list[float] = []

    async def capture_sleep(delay: float) -> None:
        delays.append(delay)
        raise asyncio.CancelledError

    with patch("custom_components.resiyhome_health_sync.asyncio.sleep", side_effect=capture_sleep):
        with pytest.raises(asyncio.CancelledError):
            await _async_run_backfill(entry)

    coordinator.async_backfill_step.assert_not_awaited()
    assert delays == [SCAN_INTERVAL.total_seconds()]


async def test_background_backfill_continues_until_expanded_history_is_complete(hass) -> None:
    entry = _entry(hass)
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.data.backfill_complete = True
    coordinator.data.expanded_backfill_complete = False

    async def complete_expanded_backfill():
        coordinator.data.expanded_backfill_complete = True
        return coordinator.data

    coordinator.async_backfill_step = AsyncMock(side_effect=complete_expanded_backfill)
    entry.runtime_data = MagicMock(coordinator=coordinator)

    with patch("custom_components.resiyhome_health_sync.asyncio.sleep", new=AsyncMock()):
        await _async_run_backfill(entry)

    coordinator.async_backfill_step.assert_awaited_once()


async def test_two_entries_isolate_token_callbacks_and_history_namespaces(hass) -> None:
    """One person's refresh cannot update another person's tokens or history key."""
    sample_alpha = _entry(hass)
    sample_beta = _entry(hass, person_name="Sample Beta", person_slug="sample_beta")
    clients = [MagicMock(), MagicMock()]
    coordinators = [MagicMock(), MagicMock()]
    for coordinator in coordinators:
        coordinator.async_refresh_current = AsyncMock(return_value=coordinator.data)
        coordinator.async_set_updated_data = MagicMock()
        coordinator.data.backfill_complete = True

    with (
        patch(
            "custom_components.resiyhome_health_sync.GoogleHealthClient", side_effect=clients
        ) as client_class,
        patch(
            "custom_components.resiyhome_health_sync.HealthSyncCoordinator",
            side_effect=coordinators,
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        await async_setup_entry(hass, sample_alpha)
        await async_setup_entry(hass, sample_beta)
        await hass.async_block_till_done()

    sample_alpha_callback = client_class.call_args_list[0].kwargs["token_update_callback"]
    await sample_alpha_callback(
        OAuthTokenState(
            **{
                "access" + "_token": "sample_alpha-new-access",
                "refresh" + "_token": "sample_alpha-new-refresh",
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
                "scopes": frozenset(SCOPES),
            }
        )
    )

    assert sample_alpha.data["refresh_token"] == "sample_alpha-new-refresh"
    assert sample_beta.data["refresh_token"] == "sample_beta-refresh-token"
    assert (
        sample_alpha.runtime_data.history.key
        == f"resiyhome_health_sync.{sample_alpha.entry_id}.history"
    )
    assert (
        sample_beta.runtime_data.history.key
        == f"resiyhome_health_sync.{sample_beta.entry_id}.history"
    )
    assert sample_alpha.runtime_data.history.key != sample_beta.runtime_data.history.key


async def test_config_entry_manager_setup_and_unload_cleans_runtime_and_task(hass) -> None:
    """Home Assistant owns successful setup state and removes runtime data on unload."""
    _register_real_entry_lifecycle(hass)
    entry = _entry(hass)
    _client, _history, coordinator, patches = _lifecycle_patches()

    with (
        patches[0],
        patches[1],
        patches[2],
        patch("custom_components.resiyhome_health_sync.asyncio.sleep", new=AsyncMock()),
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
        patch.object(
            hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is True
        await hass.async_block_till_done()
        assert entry.state is config_entries.ConfigEntryState.LOADED
        assert entry.runtime_data.backfill_task is not None
        assert entry.runtime_data.backfill_task.done()
        assert hass.services.has_service(DOMAIN, "refresh")
        assert hass.services.has_service(DOMAIN, "probe_optional_data_types")

        assert await hass.config_entries.async_unload(entry.entry_id) is True

    assert entry.state is config_entries.ConfigEntryState.NOT_LOADED
    assert not hasattr(entry, "runtime_data")
    assert not hass.services.has_service(DOMAIN, "refresh")
    assert not hass.services.has_service(DOMAIN, "probe_optional_data_types")
    coordinator.async_backfill_step.assert_not_awaited()


async def test_config_entry_manager_auth_failure_starts_reauth_and_cleans_runtime(
    hass, caplog
) -> None:
    """Home Assistant converts startup auth failure into one clean reauth flow."""
    _register_real_entry_lifecycle(hass)
    _register_application_credential_implementation(hass)
    entry = _application_credential_entry(hass)
    _client, _history, _coordinator, patches = _lifecycle_patches(
        refresh_error=AuthenticationError("secret startup auth detail")
    )

    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        await hass.async_block_till_done()

    reauth_flows = [
        flow
        for flow in hass.config_entries.flow.async_progress_by_handler(DOMAIN)
        if flow["context"]["source"] == config_entries.SOURCE_REAUTH
    ]
    assert len(reauth_flows) == 1
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    assert not hasattr(entry, "runtime_data")
    assert "secret startup auth detail" not in caplog.text


async def test_config_entry_manager_forward_failure_cleans_partial_runtime(hass, caplog) -> None:
    """A platform-forwarding exception leaves no runtime or backfill task behind."""
    _register_real_entry_lifecycle(hass)
    entry = _entry(hass)
    _client, history, coordinator, patches = _lifecycle_patches()
    failure = RuntimeError("platform forwarding failure marker")
    shutdown_saw_runtime = False

    async def shutdown_history() -> None:
        nonlocal shutdown_saw_runtime
        shutdown_saw_runtime = getattr(entry, "runtime_data", None) is not None

    history.async_shutdown.side_effect = shutdown_history

    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(side_effect=failure),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        await hass.async_block_till_done()

    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    assert not hasattr(entry, "runtime_data")
    assert not any(
        task.get_name().startswith("Health Sync backfill") for task in asyncio.all_tasks()
    )
    coordinator.async_backfill_step.assert_not_awaited()
    history.async_shutdown.assert_awaited_once_with()
    assert shutdown_saw_runtime
    assert not hasattr(history, "async_remove") or history.async_remove.call_count == 0
    assert "platform forwarding failure marker" in caplog.text


@pytest.mark.filterwarnings("error::RuntimeWarning")
@pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning")
async def test_config_entry_manager_task_creation_failure_closes_backfill_coroutine(
    hass, caplog
) -> None:
    """A rejected background task cannot leak its unowned coroutine or runtime."""
    _register_real_entry_lifecycle(hass)
    entry = _entry(hass)
    _client, history, coordinator, patches = _lifecycle_patches()
    failure = RuntimeError("backfill task creation failure marker")

    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new=AsyncMock(),
        ),
        patch.object(
            entry,
            "async_create_background_task",
            side_effect=failure,
        ) as create_task,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        await hass.async_block_till_done()
        create_task.assert_called_once()
        original_exception_logged = (
            "RuntimeError: backfill task creation failure marker" in caplog.text
        )
        create_task.reset_mock()
        create_task.side_effect = None
        failure.__traceback__ = None
        caplog.clear()

    gc.collect()

    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    assert not hasattr(entry, "runtime_data")
    assert not any(
        task.get_name().startswith("Health Sync backfill") for task in asyncio.all_tasks()
    )
    coordinator.async_backfill_step.assert_not_awaited()
    history.async_shutdown.assert_awaited_once_with()
    assert not hasattr(history, "async_remove") or history.async_remove.call_count == 0
    assert original_exception_logged


async def test_late_setup_failure_then_retry_does_not_accumulate_reload_callbacks(hass) -> None:
    """A failed setup cannot retain callbacks that duplicate later reloads."""
    entry = _entry(hass)
    _client, history, _coordinator, patches = _lifecycle_patches()
    failure = RuntimeError("interface registration failure marker")

    with (
        patches[0],
        patches[1],
        patches[2],
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
        patch(
            "custom_components.resiyhome_health_sync._async_register_interfaces",
            side_effect=[failure, None],
        ) as register_interfaces,
    ):
        with pytest.raises(RuntimeError, match="interface registration failure marker"):
            await async_setup_entry(hass, entry)
        assert entry.update_listeners == []
        history.async_shutdown.assert_awaited_once_with()

        assert await async_setup_entry(hass, entry) is True

    assert register_interfaces.call_count == 2
    history.async_shutdown.assert_awaited_once_with()
    assert entry.update_listeners == []


async def test_failed_setup_real_store_retry_cannot_resurrect_old_body_snapshot(
    hass,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed setup drains its real store before a same-key retry can scrub it."""
    entry = _entry(hass)
    today = date(2042, 7, 21)
    measured = DailySummary(
        date=date(2042, 7, 15),
        steps=7300,
        expanded=ExpandedDailyMetrics(
            weight_kg=80.5,
            body_fat_percentage=21.4,
            height_m=1.778,
        ),
    )
    baseline = DailySummary(
        date=date(2042, 7, 16),
        steps=7100,
        expanded=ExpandedDailyMetrics(floors=4),
    )
    stores: list[HealthHistoryStore] = []

    def history_factory(hass, entry_id: str) -> HealthHistoryStore:
        history = HealthHistoryStore(hass, entry_id)
        async def write_store_document(data: dict[str, object]) -> None:
            if "data_func" in data:
                data["data"] = data.pop("data_func")()
            path = Path(history._store.path)
            serialized = json.dumps(data)

            def write() -> None:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(serialized)

            await hass.async_add_executor_job(write)

        monkeypatch.setattr(history._store, "_async_write_data", write_store_document)
        stores.append(history)
        return history

    def coordinator_factory(_hass, _client, history, **_kwargs):
        coordinator = MagicMock()
        coordinator.data = CoordinatorSnapshot(
            backfill_complete=True,
            expanded_backfill_complete=True,
        )

        async def refresh_current() -> CoordinatorSnapshot:
            if len(stores) == 1:
                await history.async_set_backfill_checkpoint(date(2042, 7, 1))
                await history.async_apply_body_measurement_option(True, today)
                await history.async_checkpoint_expanded(baseline, date(2042, 7, 7))
                await history.async_upsert(measured)
            else:
                await history.async_apply_body_measurement_option(False, today)
            return coordinator.data

        coordinator.async_refresh_current = AsyncMock(side_effect=refresh_current)
        coordinator.async_set_updated_data = MagicMock()
        coordinator.async_backfill_step = AsyncMock()
        return coordinator

    failure = RuntimeError("platform forwarding failure marker")
    forward = AsyncMock(side_effect=[failure, None])

    with (
        patch(
            "custom_components.resiyhome_health_sync.GoogleHealthClient",
            return_value=MagicMock(),
        ),
        patch(
            "custom_components.resiyhome_health_sync.HealthHistoryStore",
            side_effect=history_factory,
        ),
        patch(
            "custom_components.resiyhome_health_sync.HealthSyncCoordinator",
            side_effect=coordinator_factory,
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", new=forward),
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=True),
        ),
    ):
        with pytest.raises(RuntimeError, match="platform forwarding failure marker"):
            await async_setup_entry(hass, entry)
        assert not hasattr(entry, "runtime_data")

        hass.config_entries.async_update_entry(
            entry,
            options={"include_body_measurements": False},
        )
        assert await async_setup_entry(hass, entry) is True

        old, current = stores
        old._store._async_schedule_callback_delayed_write()
        hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
        await hass.async_block_till_done()

        restarted = HealthHistoryStore(hass, entry.entry_id)
        rows = await restarted.async_load()
        assert restarted.body_measurements_enabled is False
        assert restarted.backfill_cursor == date(2042, 7, 1)
        assert restarted.expanded_backfill_cursor == date(2042, 7, 7)
        assert [row.date for row in rows] == [measured.date, baseline.date]
        assert all(row.expanded.weight_kg is None for row in rows)
        assert all(row.expanded.body_fat_percentage is None for row in rows)
        assert all(row.expanded.height_m is None for row in rows)
        assert rows[0].steps == measured.steps
        assert rows[1].expanded.floors == baseline.expanded.floors
        assert entry.runtime_data.history is current

        assert await async_unload_entry(hass, entry) is True


async def test_unload_cancels_and_awaits_backfill_without_removing_history(hass) -> None:
    """Unload stops cooperative work but leaves durable health history intact."""
    entry = _entry(hass)
    client, history, coordinator, patches = _lifecycle_patches()
    started = asyncio.Event()
    cancelled = asyncio.Event()
    shutdown_after_cancel = False
    coordinator.data.backfill_complete = False

    async def blocked_backfill():
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    async def shutdown_history() -> None:
        nonlocal shutdown_after_cancel
        shutdown_after_cancel = cancelled.is_set()

    coordinator.async_backfill_step.side_effect = blocked_backfill
    history.async_shutdown.side_effect = shutdown_history

    with (
        patches[0],
        patches[1],
        patches[2],
        patch("custom_components.resiyhome_health_sync.asyncio.sleep", new=AsyncMock()),
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
        patch.object(
            hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)
        ) as unload_mock,
    ):
        await async_setup_entry(hass, entry)
        await started.wait()
        assert await async_unload_entry(hass, entry) is True

    assert cancelled.is_set()
    assert shutdown_after_cancel
    unload_mock.assert_awaited_once_with(entry, ("sensor", "binary_sensor"))
    assert history.async_load.await_count == 1
    history.async_shutdown.assert_awaited_once_with()
    assert not hasattr(history, "async_remove") or history.async_remove.call_count == 0
    assert entry.runtime_data.client is client
