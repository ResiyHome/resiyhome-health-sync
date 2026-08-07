"""Config flow for secure Health Sync enrollment."""

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, override

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType
from homeassistant.util import slugify

from .capabilities import (
    UnsupportedScopeError,
    requested_scopes,
    validate_granted_scopes,
)
from .const import BASE_SCOPES, DOMAIN, NUTRITION_SCOPE, SETTINGS_SCOPE

_LOGGER = logging.getLogger(__name__)
_REQUESTED_SCOPES_CONTEXT = "resiyhome_health_sync_requested_scopes"

_PERSON_SCHEMA = vol.Schema(
    {
        vol.Required("person_name"): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT)),
    }
)


class HealthSyncConfigFlow(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Enroll one independently authorized Google Health person."""

    DOMAIN = DOMAIN
    VERSION = 1
    MINOR_VERSION = 2

    def __init__(self) -> None:
        super().__init__()
        self._person_data: dict[str, str] | None = None
        self._reauth_implementation_id: str | None = None
        self._requested_options: dict[str, object] = {}

    @staticmethod
    @callback
    @override
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the per-person body measurement options flow."""
        return HealthSyncOptionsFlow(config_entry)

    @property
    @override
    def logger(self) -> logging.Logger:
        """Return logger."""
        return _LOGGER

    @property
    @override
    def extra_authorize_data(self) -> dict[str, str]:
        """Request only the scopes needed by this flow's enabled capabilities."""
        return {
            "scope": " ".join(requested_scopes(self._requested_options)),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
        }

    @override
    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect the person name before starting Home Assistant OAuth."""
        errors: dict[str, str] = {}
        if user_input is not None:
            person_name = str(user_input["person_name"]).strip()
            person_slug = slugify(person_name)
            if not person_name or not person_slug:
                errors["person_name"] = "invalid_person_name"
            else:
                await self.async_set_unique_id(person_slug)
                self._abort_if_unique_id_configured()
                self._person_data = {
                    "person_name": person_name,
                    "person_slug": person_slug,
                }
                return await self.async_step_pick_implementation()

        return self.async_show_form(
            step_id="user",
            data_schema=_PERSON_SCHEMA,
            errors=errors,
        )

    @override
    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Reauthorize the existing person without creating another entry."""
        entry = self._get_reauth_entry()
        self._person_data = {
            "person_name": str(entry.data["person_name"]),
            "person_slug": str(entry.data["person_slug"]),
        }
        self._requested_options = dict(entry.options)
        self.context[_REQUESTED_SCOPES_CONTEXT] = list(
            requested_scopes(self._requested_options)
        )
        implementation_id = entry.data.get("auth_implementation")
        self._reauth_implementation_id = (
            implementation_id if isinstance(implementation_id, str) else None
        )
        await self.async_set_unique_id(self._person_data["person_slug"])
        self._abort_if_unique_id_mismatch()
        return self.async_show_form(step_id="reauth_confirm")

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start OAuth from an operator-driven request context."""
        return await self._async_start_existing_entry_oauth()

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure an existing person with Home Assistant OAuth."""
        entry = self._get_reconfigure_entry()
        self._person_data = {
            "person_name": str(entry.data["person_name"]),
            "person_slug": str(entry.data["person_slug"]),
        }
        self._requested_options = dict(entry.options)
        self.context[_REQUESTED_SCOPES_CONTEXT] = list(
            requested_scopes(self._requested_options)
        )
        implementation_id = entry.data.get("auth_implementation")
        self._reauth_implementation_id = (
            implementation_id if isinstance(implementation_id, str) else None
        )
        await self.async_set_unique_id(self._person_data["person_slug"])
        self._abort_if_unique_id_mismatch()
        return self.async_show_form(step_id="reconfigure_confirm")

    async def async_step_reconfigure_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Start OAuth from a manual reconfigure request."""
        return await self._async_start_existing_entry_oauth()

    async def _async_start_existing_entry_oauth(self) -> ConfigFlowResult:
        """Start OAuth for an existing entry."""
        assert self._person_data is not None
        implementations = await config_entry_oauth2_flow.async_get_implementations(
            self.hass, DOMAIN
        )
        if (
            self._reauth_implementation_id is not None
            and self._reauth_implementation_id in implementations
        ):
            self.flow_impl = implementations[self._reauth_implementation_id]
            return await self.async_step_auth()
        return await self.async_step_pick_implementation()

    @override
    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Create or update a config entry in the runtime credential format."""
        assert self._person_data is not None
        token = data.get("token")
        if not isinstance(token, dict):
            return self.async_abort(reason="oauth_error")

        credential_data = _entry_data_from_oauth_token(
            self._person_data,
            str(data["auth_implementation"]),
            token,
            self._requested_options,
        )
        if credential_data is None:
            return self.async_abort(reason="missing_scope")

        if self.source == config_entries.SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            if not self._is_current_existing_entry_flow(entry):
                return self.async_abort(reason="invalid_flow_state")
            return self.async_update_reload_and_abort(
                entry,
                data=credential_data,
            )
        if self.source == config_entries.SOURCE_RECONFIGURE:
            entry = self._get_reconfigure_entry()
            if not self._is_current_existing_entry_flow(entry):
                return self.async_abort(reason="invalid_flow_state")
            return self.async_update_reload_and_abort(
                entry,
                data=credential_data,
            )
        return self.async_create_entry(title=self._person_data["person_name"], data=credential_data)

    def _is_current_existing_entry_flow(self, entry: ConfigEntry) -> bool:
        """Return whether this flow still owns the entry's current scope generation."""
        current_generation = list(requested_scopes(entry.options))
        if self.context.get(_REQUESTED_SCOPES_CONTEXT) != current_generation:
            return False
        return any(
            flow["flow_id"] == self.flow_id
            and flow["context"].get(_REQUESTED_SCOPES_CONTEXT) == current_generation
            for flow in self.hass.config_entries.flow.async_progress_by_handler(
                DOMAIN,
                include_uninitialized=True,
                match_context={"entry_id": entry.entry_id},
            )
        )


class HealthSyncOptionsFlow(OptionsFlow):
    """Manage body measurement collection for one person."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Retain the existing per-person options for the form defaults."""
        self._include_body_measurements = bool(
            config_entry.options.get("include_body_measurements", False)
        )
        self._include_nutrition = bool(config_entry.options.get("include_nutrition", False))
        self._include_paired_devices = bool(
            config_entry.options.get("include_paired_devices", False)
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and save the body measurement option."""
        if user_input is not None:
            result = self.async_create_entry(data=user_input)
            options_changed = dict(self.config_entry.options) != user_input
            granted_scopes = self.config_entry.data.get("scopes", ())
            missing_scopes = set(requested_scopes(user_input)) - set(granted_scopes)
            if options_changed and missing_scopes:
                self.hass.async_create_task(
                    self._async_start_reauth_after_options_update(),
                    f"Health Sync optional scope reauth {self.config_entry.entry_id}",
                    eager_start=False,
                )
            elif options_changed:
                self.hass.async_create_task(
                    self._async_reload_after_options_update(),
                    f"Health Sync options reload {self.config_entry.entry_id}",
                    eager_start=False,
                )
            return result

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "include_body_measurements",
                        default=self._include_body_measurements,
                    ): bool,
                    vol.Required(
                        "include_nutrition",
                        default=self._include_nutrition,
                    ): bool,
                    vol.Required(
                        "include_paired_devices",
                        default=self._include_paired_devices,
                    ): bool,
                }
            ),
        )

    def _active_authorization_flows(self) -> list[ConfigFlowResult]:
        """Return active reauth and reconfigure flows for this entry."""
        return [
            flow
            for flow in self.hass.config_entries.flow.async_progress_by_handler(
                DOMAIN,
                include_uninitialized=True,
            )
            if (
                flow["context"].get("entry_id") == self.config_entry.entry_id
                and flow["context"].get("source")
                in {config_entries.SOURCE_REAUTH, config_entries.SOURCE_RECONFIGURE}
            )
        ]

    async def _async_start_reauth_after_options_update(self) -> None:
        """Start reauth after the options manager has persisted this result."""
        desired_scopes = list(requested_scopes(self.config_entry.options))
        granted_scopes = set(self.config_entry.data.get("scopes", ()))
        if not set(desired_scopes) - granted_scopes:
            return
        active_flows = self._active_authorization_flows()
        if any(
            flow["context"].get(_REQUESTED_SCOPES_CONTEXT) == desired_scopes
            for flow in active_flows
        ):
            return
        for flow in active_flows:
            self.hass.config_entries.flow.async_abort(flow["flow_id"])
        self.config_entry.async_start_reauth(self.hass)

    async def _async_reload_after_options_update(self) -> None:
        """Reload once after the options manager has persisted a scope-complete result."""
        self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)


def _entry_data_from_oauth_token(
    person_data: dict[str, str],
    auth_implementation: str,
    token: dict[str, Any],
    requested_options: Mapping[str, object],
) -> dict[str, Any] | None:
    """Convert HA OAuth token dictionaries into the existing runtime data shape."""
    access_token = token.get("access_token")
    refresh_token = token.get("refresh_token")
    expires_at = token.get("expires_at")
    raw_scope = token.get("scope")
    if not isinstance(access_token, str) or not access_token:
        return None
    if not isinstance(refresh_token, str) or not refresh_token:
        return None
    if not isinstance(expires_at, int | float):
        return None
    if not isinstance(raw_scope, str):
        return None

    try:
        grant = validate_granted_scopes(raw_scope.split(), requested_options)
    except UnsupportedScopeError:
        return None
    if not grant.baseline_valid:
        return None

    ordered_granted_scopes = [
        scope
        for scope in (*BASE_SCOPES, NUTRITION_SCOPE, SETTINGS_SCOPE)
        if scope in grant.granted_scopes
    ]
    return {
        **person_data,
        "auth_implementation": auth_implementation,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": datetime.fromtimestamp(float(expires_at), UTC).isoformat(),
        "scopes": ordered_granted_scopes,
    }
