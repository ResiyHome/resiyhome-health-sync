"""Application credentials support for Health Sync."""

from typing import override

from homeassistant.components.application_credentials import ClientCredential
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_entry_oauth2_flow

from .const import BASE_SCOPES, DOMAIN, TOKEN_URL

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_CLOUD_CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"
GOOGLE_CLOUD_CONSENT_URL = "https://console.cloud.google.com/apis/credentials/consent"


class HealthSyncOAuth2Implementation(config_entry_oauth2_flow.LocalOAuth2Implementation):
    """Google OAuth implementation with the baseline read-only Health scopes."""

    def __init__(
        self,
        hass: HomeAssistant,
        auth_domain: str,
        credential: ClientCredential,
    ) -> None:
        super().__init__(
            hass,
            auth_domain,
            credential.client_id,
            credential.client_secret,
            AUTHORIZE_URL,
            TOKEN_URL,
        )
        self._name = credential.name

    @property
    @override
    def name(self) -> str:
        """Return the user-visible credential name."""
        return self._name or self.client_id

    @property
    @override
    def extra_authorize_data(self) -> dict[str, str]:
        """Request durable, explicitly scoped Google Health access."""
        return {
            "scope": " ".join(BASE_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "false",
        }


async def async_get_auth_implementation(
    hass: HomeAssistant, auth_domain: str, credential: ClientCredential
) -> config_entry_oauth2_flow.AbstractOAuth2Implementation:
    """Return the OAuth implementation for a stored Google client credential."""
    return HealthSyncOAuth2Implementation(hass, auth_domain, credential)


async def async_get_description_placeholders(hass: HomeAssistant) -> dict[str, str]:
    """Return links shown in the Home Assistant credentials dialog."""
    return {
        "oauth_creds_url": GOOGLE_CLOUD_CREDENTIALS_URL,
        "oauth_consent_url": GOOGLE_CLOUD_CONSENT_URL,
        "domain": DOMAIN,
    }
