"""Tests for Home Assistant application credentials support."""

from typing import Any
from urllib.parse import parse_qs, urlparse

from homeassistant.components.application_credentials import ClientCredential

from custom_components.resiyhome_health_sync.application_credentials import (
    HealthSyncOAuth2Implementation,
    async_get_auth_implementation,
)
from custom_components.resiyhome_health_sync.const import DOMAIN, SCOPES, TOKEN_URL


async def test_auth_implementation_uses_google_health_oauth_settings(
    hass: Any, current_request_with_host: None
) -> None:
    """Application credentials produce a scoped HA callback OAuth implementation."""
    implementation = await async_get_auth_implementation(
        hass,
        "family-google-client",
        ClientCredential("public-client-id", "top-secret-client-value", "Family"),
    )

    assert isinstance(implementation, HealthSyncOAuth2Implementation)
    assert implementation.domain == "family-google-client"
    assert implementation.name == "Family"
    assert implementation.token_url == TOKEN_URL

    url = await implementation.async_generate_authorize_url("flow-id")
    parsed = urlparse(url)
    query = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.google.com"
    assert query["client_id"] == ["public-client-id"]
    assert query["redirect_uri"] == ["https://example.com/auth/external/callback"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == [" ".join(SCOPES)]
    assert query["access_type"] == ["offline"]
    assert query["prompt"] == ["consent"]
    assert query["include_granted_scopes"] == ["false"]
    assert "state" in query


async def test_auth_implementation_defaults_name_to_client_id(
    hass: Any, current_request_with_host: None
) -> None:
    """Unnamed Google credentials are still identifiable in the picker."""
    implementation = await async_get_auth_implementation(
        hass,
        DOMAIN,
        ClientCredential("public-client-id", "top-secret-client-value"),
    )

    assert implementation.name == "public-client-id"
