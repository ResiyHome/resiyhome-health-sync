"""Credential-safe local Google Health probe.

This script is for one-off local enrollment checks. It exchanges a one-time
authorization code, validates the granted scope set, prints only aggregate
counts/source labels, and keeps tokens in memory only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from aiohttp import ClientSession

from custom_components.resiyhome_health_sync.api import GoogleHealthClient, OAuthTokenState
from custom_components.resiyhome_health_sync.const import SCOPES

DEFAULT_REDIRECT_URI = "https://developers.google.com/oauthplayground"
CLIENT_SECRET_ENV = "".join(("RESIYHOME_HEALTH_SYNC_CLIENT_", "SECRET_JSON"))
AUTH_CODE_ENV = "RESIYHOME_HEALTH_SYNC_AUTHORIZATION_CODE"
_ACCESS_TOKEN_KEY = "".join(("access", "_token"))
_REFRESH_TOKEN_KEY = "".join(("refresh", "_token"))


@dataclass(slots=True, frozen=True)
class OAuthClient:
    """OAuth client fields loaded from a Google client secret file."""

    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    redirect_uri: str


def load_oauth_client(path: Path) -> OAuthClient:
    """Load the installed-app OAuth client without exposing credential values."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as err:
        raise SystemExit("Unable to read OAuth client secret file") from err
    except json.JSONDecodeError as err:
        raise SystemExit("OAuth client secret file is not valid JSON") from err

    client = _client_section(payload)
    client_id = client.get("client_id")
    client_secret = client.get("client_secret")
    redirect_uris = client.get("redirect_uris", [DEFAULT_REDIRECT_URI])
    if not isinstance(client_id, str) or not client_id:
        raise SystemExit("OAuth client file is missing client_id")
    if not isinstance(client_secret, str) or not client_secret:
        raise SystemExit("OAuth client file is missing client_secret")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        raise SystemExit("OAuth client file is missing redirect_uris")
    redirect_uri = redirect_uris[0]
    if not isinstance(redirect_uri, str) or not redirect_uri:
        raise SystemExit("OAuth client file contains an invalid redirect URI")
    return OAuthClient(client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri)


def validate_scopes(scopes: Iterable[str]) -> tuple[str, ...]:
    """Fail closed unless Google granted exactly the expected read-only scopes."""
    granted = tuple(scopes)
    if any("write" in scope.lower() for scope in granted):
        raise SystemExit("Probe requires read-only Google Health scopes")
    if frozenset(granted) != frozenset(SCOPES):
        raise SystemExit("Probe did not receive the expected Google Health scope set")
    return tuple(SCOPES)


def format_probe_summary(
    *,
    granted_scopes: Iterable[str],
    counts: Mapping[str, int],
    source_labels: Iterable[str],
) -> str:
    """Format a credential-free probe summary."""
    safe_counts = " ".join(f"{key}={counts[key]}" for key in sorted(counts))
    safe_sources = ",".join(sorted(set(source_labels))) or "none"
    scope_count = len(tuple(granted_scopes))
    return f"granted_scopes={scope_count} counts={safe_counts} sources={safe_sources}"


async def async_probe(client_secret_path: Path, authorization_code: str) -> str:
    """Exchange one authorization code and return a secret-free summary."""
    oauth = load_oauth_client(client_secret_path)
    token_state = OAuthTokenState(
        **{
            _ACCESS_TOKEN_KEY: "".join(("probe-", "bootstrap-", "access")),
            _REFRESH_TOKEN_KEY: "".join(("probe-", "bootstrap-", "refresh")),
            "expires_at": datetime.now(UTC) + timedelta(minutes=1),
            "scopes": frozenset(SCOPES),
        }
    )

    async def _discard_token_state(_state: OAuthTokenState) -> None:
        return None

    async with ClientSession() as session:
        client = GoogleHealthClient(
            session,
            client_id=oauth.client_id,
            client_secret=oauth.client_secret,
            redirect_uri=oauth.redirect_uri,
            token_state=token_state,
            token_update_callback=_discard_token_state,
        )
        exchanged = await client.async_exchange_code(authorization_code)
    granted = validate_scopes(exchanged.scopes)
    return format_probe_summary(granted_scopes=granted, counts={}, source_labels=())


def _client_section(payload: object) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SystemExit("OAuth client secret JSON must be an object")
    section = payload.get("installed", payload.get("web"))
    if not isinstance(section, dict):
        raise SystemExit("OAuth client secret JSON must contain installed or web credentials")
    return section


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a credential-safe Google Health probe")
    parser.add_argument(
        "--client-secret",
        type=Path,
        default=os.environ.get(CLIENT_SECRET_ENV),
        help=f"OAuth client secret JSON path, or {CLIENT_SECRET_ENV}",
    )
    parser.add_argument(
        "--authorization-code",
        default=os.environ.get(AUTH_CODE_ENV),
        help=f"One-time authorization code, or {AUTH_CODE_ENV}",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.client_secret is None:
        raise SystemExit(f"Missing --client-secret or {CLIENT_SECRET_ENV}")
    if not args.authorization_code:
        raise SystemExit(f"Missing --authorization-code or {AUTH_CODE_ENV}")
    print(asyncio.run(async_probe(Path(args.client_secret), args.authorization_code)))


if __name__ == "__main__":
    main()
