"""Health Sync config-entry lifecycle."""

import asyncio
import logging
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryError,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import AuthenticationError, GoogleHealthClient, OAuthTokenState, UpdateFailed
from .capabilities import ScopeGrant, validate_granted_scopes
from .const import (
    BASE_SCOPES,
    DOMAIN,
    NUTRITION_SCOPE,
    SCAN_INTERVAL,
    SETTINGS_SCOPE,
)
from .coordinator import HealthSyncCoordinator
from .storage import HealthHistoryStore, HistoryStoreError
from .websocket import async_register_websocket, async_unregister_websocket

_LOGGER = logging.getLogger(__name__)
_PLATFORMS = ("sensor", "binary_sensor")
GOOGLE_REDIRECT_URI = "https://developers.google.com/oauthplayground"
_REFRESH_SERVICE = "refresh"
_OPTIONAL_PROBE_SERVICE = "probe_optional_data_types"
_DATA_KEY_SERVICE_COUNT = "refresh_service_entries"
_DATA_KEY_WEBSOCKET_REGISTERED = "websocket_registered"


@dataclass(slots=True)
class HealthSyncRuntimeData:
    """Runtime objects owned by one person's config entry."""

    client: GoogleHealthClient
    history: HealthHistoryStore
    coordinator: HealthSyncCoordinator
    scope_grant: ScopeGrant
    backfill_task: asyncio.Task[None] | None = None


type HealthSyncConfigEntry = ConfigEntry[HealthSyncRuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: HealthSyncConfigEntry) -> bool:
    """Set up one independently authorized Health Sync person."""
    token_state, scope_grant = _token_state_from_entry(entry)
    runtime: HealthSyncRuntimeData | None = None

    async def async_update_token_state(state: OAuthTokenState) -> None:
        refresh_token = state.refresh_token or str(entry.data["refresh_token"])
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                "access_token": state.access_token,
                "refresh_token": refresh_token,
                "expires_at": state.expires_at.isoformat(),
                "scopes": [
                    scope
                    for scope in (*BASE_SCOPES, NUTRITION_SCOPE, SETTINGS_SCOPE)
                    if scope in state.scopes
                ],
            },
        )
        if runtime is not None:
            runtime.scope_grant = validate_granted_scopes(state.scopes, entry.options)

    client_id, client_secret, redirect_uri = await _async_get_oauth_client_config(hass, entry)
    client = GoogleHealthClient(
        async_get_clientsession(hass),
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        token_state=token_state,
        scope_grant=scope_grant,
        token_update_callback=async_update_token_state,
    )
    history = HealthHistoryStore(hass, entry.entry_id)
    backfill_coro = None
    setup_complete = False
    try:
        coordinator = HealthSyncCoordinator(
            hass,
            client,
            history,
            include_body_measurements=bool(
                entry.options.get("include_body_measurements", False)
            ),
        )
        runtime = HealthSyncRuntimeData(client, history, coordinator, scope_grant)

        try:
            await history.async_load()
            snapshot = await coordinator.async_refresh_current()
        except AuthenticationError:
            raise ConfigEntryAuthFailed(
                "Google Health authorization must be renewed"
            ) from None
        except UpdateFailed:
            raise ConfigEntryNotReady(
                "Google Health is temporarily unavailable"
            ) from None
        except HistoryStoreError:
            raise ConfigEntryError("Health Sync history requires repair") from None

        coordinator.async_set_updated_data(snapshot)
        entry.runtime_data = runtime
        backfill_coro = _async_run_backfill(entry)
        await hass.config_entries.async_forward_entry_setups(entry, _PLATFORMS)
        runtime.backfill_task = entry.async_create_background_task(
            hass,
            backfill_coro,
            f"Health Sync backfill {entry.entry_id}",
        )
        _async_register_interfaces(hass)
        setup_complete = True
        return True
    finally:
        if not setup_complete:
            if runtime is not None and runtime.backfill_task is not None:
                runtime.backfill_task.cancel()
                await asyncio.gather(runtime.backfill_task, return_exceptions=True)
                runtime.backfill_task = None
            elif backfill_coro is not None:
                backfill_coro.close()
            await history.async_shutdown()
            if runtime is not None and getattr(entry, "runtime_data", None) is runtime:
                object.__delattr__(entry, "runtime_data")


async def async_unload_entry(hass: HomeAssistant, entry: HealthSyncConfigEntry) -> bool:
    """Unload entities and cooperatively stop backfill without deleting history."""
    if not await hass.config_entries.async_unload_platforms(entry, _PLATFORMS):
        return False

    task = entry.runtime_data.backfill_task
    if task is not None:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        entry.runtime_data.backfill_task = None
    await entry.runtime_data.history.async_shutdown()
    _async_unregister_interfaces_if_last_entry(hass)
    return True


def _async_register_interfaces(hass: HomeAssistant) -> None:
    """Register shared service and websocket interfaces exactly once."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    loaded_count = int(domain_data.get(_DATA_KEY_SERVICE_COUNT, 0)) + 1
    domain_data[_DATA_KEY_SERVICE_COUNT] = loaded_count

    if not hass.services.has_service(DOMAIN, _REFRESH_SERVICE):
        hass.services.async_register(DOMAIN, _REFRESH_SERVICE, _async_handle_refresh_service)
    if not hass.services.has_service(DOMAIN, _OPTIONAL_PROBE_SERVICE):
        hass.services.async_register(
            DOMAIN,
            _OPTIONAL_PROBE_SERVICE,
            _async_handle_optional_probe_service,
            supports_response=SupportsResponse.ONLY,
        )
    if not domain_data.get(_DATA_KEY_WEBSOCKET_REGISTERED):
        async_register_websocket(hass)
        domain_data[_DATA_KEY_WEBSOCKET_REGISTERED] = True


def _async_unregister_interfaces_if_last_entry(hass: HomeAssistant) -> None:
    """Remove shared services after the last loaded entry unloads."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    loaded_count = max(0, int(domain_data.get(_DATA_KEY_SERVICE_COUNT, 0)) - 1)
    domain_data[_DATA_KEY_SERVICE_COUNT] = loaded_count
    if loaded_count == 0 and hass.services.has_service(DOMAIN, _REFRESH_SERVICE):
        hass.services.async_remove(DOMAIN, _REFRESH_SERVICE)
    if loaded_count == 0 and hass.services.has_service(DOMAIN, _OPTIONAL_PROBE_SERVICE):
        hass.services.async_remove(DOMAIN, _OPTIONAL_PROBE_SERVICE)
    if loaded_count == 0 and domain_data.pop(_DATA_KEY_WEBSOCKET_REGISTERED, False):
        async_unregister_websocket(hass)


async def _async_handle_refresh_service(call: ServiceCall) -> None:
    """Refresh exactly one person-scoped coordinator through the manual cooldown path."""
    entry = _entry_for_person(call)
    await entry.runtime_data.coordinator.async_manual_refresh()


async def _async_handle_optional_probe_service(call: ServiceCall) -> ServiceResponse:
    """Probe optional data types without returning health values or raw payloads."""
    entry = _entry_for_person(call)
    days = call.data.get("days", 7)
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 14:
        raise ServiceValidationError("days must be an integer from 1 through 14")

    result = await entry.runtime_data.coordinator.async_probe_optional_data_types(days=days)
    return {
        "person": str(entry.data["person_slug"]),
        "days": days,
        "data_types": result,
    }


def _entry_for_person(call: ServiceCall) -> HealthSyncConfigEntry:
    """Resolve one loaded person-scoped config entry from service data."""
    person = call.data.get("person")
    if not isinstance(person, str) or not person.strip():
        raise ServiceValidationError("person is required")
    person_slug = person.strip()

    matches = [
        entry
        for entry in call.hass.config_entries.async_entries(DOMAIN)
        if entry.data.get("person_slug") == person_slug and hasattr(entry, "runtime_data")
    ]
    if len(matches) != 1:
        raise ServiceValidationError("person was not found")
    return matches[0]


async def _async_run_backfill(entry: HealthSyncConfigEntry) -> None:
    """Process bounded windows while yielding to current-data refreshes."""
    coordinator = entry.runtime_data.coordinator
    while not (coordinator.data.backfill_complete and coordinator.data.expanded_backfill_complete):
        await asyncio.sleep(SCAN_INTERVAL.total_seconds())
        try:
            await coordinator.async_backfill_step()
        except AuthenticationError:
            entry.async_start_reauth(coordinator.hass)
            return
        except UpdateFailed:
            await asyncio.sleep(SCAN_INTERVAL.total_seconds())
        except asyncio.CancelledError:
            raise
        except Exception:
            _LOGGER.error("Health Sync history backfill stopped unexpectedly")
            return


def _token_state_from_entry(
    entry: HealthSyncConfigEntry,
) -> tuple[OAuthTokenState, ScopeGrant]:
    """Validate persisted token state without including credential values in errors."""
    try:
        serialized_expiration = entry.data["expires_at"]
        serialized_scopes = entry.data["scopes"]
        access_token = entry.data["access_token"]
        refresh_token = entry.data["refresh_token"]
        if not isinstance(serialized_expiration, str):
            raise ValueError
        if not isinstance(serialized_scopes, list) or not all(
            isinstance(scope, str) for scope in serialized_scopes
        ):
            raise ValueError
        if not isinstance(access_token, str) or not access_token:
            raise ValueError
        if not isinstance(refresh_token, str) or not refresh_token:
            raise ValueError

        expires_at = datetime.fromisoformat(serialized_expiration)
        scope_grant = validate_granted_scopes(serialized_scopes, entry.options)
        scopes = scope_grant.granted_scopes
        if expires_at.tzinfo is None or expires_at.utcoffset() is None:
            raise ValueError
        if not scope_grant.baseline_valid:
            raise ValueError
    except KeyError, TypeError, ValueError:
        raise ConfigEntryAuthFailed("Health Sync authorization must be renewed") from None
    return (
        OAuthTokenState(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scopes=scopes,
        ),
        scope_grant,
    )


async def _async_get_oauth_client_config(
    hass: HomeAssistant, entry: HealthSyncConfigEntry
) -> tuple[str, str, str]:
    """Return OAuth client settings for legacy and HA application-credential entries."""
    if isinstance(entry.data.get("auth_implementation"), str):
        return await _async_get_application_oauth_client_config(hass, entry)

    client_id = entry.data.get("client_id")
    client_secret = entry.data.get("client_secret")
    if (
        isinstance(client_id, str)
        and client_id
        and isinstance(client_secret, str)
        and client_secret
    ):
        return client_id, client_secret, GOOGLE_REDIRECT_URI

    raise ConfigEntryAuthFailed("Health Sync authorization must be renewed")


async def _async_get_application_oauth_client_config(
    hass: HomeAssistant, entry: HealthSyncConfigEntry
) -> tuple[str, str, str]:
    """Return OAuth client settings from Home Assistant application credentials."""
    try:
        implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
    except ValueError:
        raise ConfigEntryAuthFailed("Health Sync authorization must be renewed") from None

    implementation_client_id = getattr(implementation, "client_id", None)
    implementation_client_secret = getattr(implementation, "client_secret", None)
    if not (
        isinstance(implementation_client_id, str)
        and implementation_client_id
        and isinstance(implementation_client_secret, str)
        and implementation_client_secret
    ):
        raise ConfigEntryAuthFailed("Health Sync authorization must be renewed")
    return implementation_client_id, implementation_client_secret, GOOGLE_REDIRECT_URI
