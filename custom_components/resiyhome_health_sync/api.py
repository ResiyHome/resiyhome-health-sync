"""Read-only OAuth and Google Health API transport."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from aiohttp import ClientError, ClientSession

from .capabilities import (
    CAPABILITIES,
    ScopeGrant,
    UnsupportedScopeError,
    validate_granted_scopes,
)
from .const import HEALTH_API_BASE_URL, TOKEN_URL

REQUEST_TIMEOUT_SECONDS = 30
DEFAULT_PAGE_SIZE = 10000
SESSION_PAGE_SIZE = 25
PAIRED_DEVICE_PAGE_SIZE = 100
MAX_PAIRED_DEVICE_PAGES = 20
# Local safety bounds in addition to Google's documented per-request page sizes.
MAX_PAGINATION_PAGES = 100
MAX_PAGINATION_RESULTS = 1_000_000

type DataTypeOperation = Literal["list", "get", "reconcile", "rollup", "daily_rollup"]
_LIST_RECONCILE = frozenset({"list", "reconcile"})
_LIST_RECONCILE_ROLLUPS = frozenset(
    {"list", "reconcile", "rollup", "daily_rollup"}
)
_LIST_GET_RECONCILE_ROLLUPS = frozenset(
    {"list", "get", "reconcile", "rollup", "daily_rollup"}
)


@dataclass(slots=True, frozen=True)
class _DataTypeSpec:
    """Google Health list-filter and pagination requirements for one supported type."""

    filter_field: str
    time_kind: Literal["civil", "daily", "physical"]
    page_size: int = DEFAULT_PAGE_SIZE
    operations: frozenset[DataTypeOperation] = _LIST_RECONCILE


_DATA_TYPE_SPECS: dict[str, _DataTypeSpec] = {
    "active-energy-burned": _DataTypeSpec(
        "active_energy_burned.interval.start_time",
        "physical",
        operations=_LIST_RECONCILE_ROLLUPS,
    ),
    "active-minutes": _DataTypeSpec(
        "active_minutes.interval.start_time",
        "physical",
        operations=_LIST_RECONCILE_ROLLUPS,
    ),
    "active-zone-minutes": _DataTypeSpec(
        "active_zone_minutes.interval.start_time",
        "physical",
        operations=_LIST_RECONCILE_ROLLUPS,
    ),
    "altitude": _DataTypeSpec(
        "altitude.interval.start_time", "physical", operations=_LIST_RECONCILE_ROLLUPS
    ),
    "body-fat": _DataTypeSpec(
        "body_fat.sample_time.physical_time",
        "physical",
        operations=_LIST_RECONCILE_ROLLUPS,
    ),
    "calories-in-heart-rate-zone": _DataTypeSpec(
        "calories_in_heart_rate_zone.interval.start_time",
        "physical",
        operations=frozenset({"rollup", "daily_rollup"}),
    ),
    "daily-heart-rate-zones": _DataTypeSpec("daily_heart_rate_zones.date", "daily"),
    "daily-heart-rate-variability": _DataTypeSpec("daily_heart_rate_variability.date", "daily"),
    "daily-oxygen-saturation": _DataTypeSpec("daily_oxygen_saturation.date", "daily"),
    "daily-respiratory-rate": _DataTypeSpec("daily_respiratory_rate.date", "daily"),
    "daily-resting-heart-rate": _DataTypeSpec("daily_resting_heart_rate.date", "daily"),
    "daily-vo2-max": _DataTypeSpec("daily_vo2_max.date", "daily"),
    "distance": _DataTypeSpec(
        "distance.interval.start_time", "physical", operations=_LIST_RECONCILE_ROLLUPS
    ),
    "exercise": _DataTypeSpec("exercise.interval.civil_start_time", "civil", SESSION_PAGE_SIZE),
    "floors": _DataTypeSpec(
        "floors.interval.start_time",
        "physical",
        operations=frozenset({"reconcile", "rollup", "daily_rollup"}),
    ),
    "heart-rate": _DataTypeSpec(
        "heart_rate.sample_time.physical_time",
        "physical",
        operations=_LIST_RECONCILE_ROLLUPS,
    ),
    "heart-rate-variability": _DataTypeSpec(
        "heart_rate_variability.sample_time.physical_time", "physical"
    ),
    "height": _DataTypeSpec(
        "height.sample_time.physical_time",
        "physical",
        operations=_LIST_RECONCILE_ROLLUPS,
    ),
    "hydration-log": _DataTypeSpec(
        "hydration_log.interval.civil_start_time",
        "civil",
        operations=_LIST_GET_RECONCILE_ROLLUPS,
    ),
    "oxygen-saturation": _DataTypeSpec(
        "oxygen_saturation.sample_time.physical_time", "physical"
    ),
    "respiratory-rate-sleep-summary": _DataTypeSpec(
        "respiratory_rate_sleep_summary.sample_time.physical_time", "physical"
    ),
    "run-vo2-max": _DataTypeSpec(
        "run_vo2_max.sample_time.physical_time",
        "physical",
        operations=_LIST_RECONCILE_ROLLUPS,
    ),
    "sedentary-period": _DataTypeSpec(
        "sedentary_period.interval.start_time",
        "physical",
        operations=_LIST_RECONCILE_ROLLUPS,
    ),
    "sleep": _DataTypeSpec("sleep.interval.end_time", "physical", SESSION_PAGE_SIZE),
    "steps": _DataTypeSpec(
        "steps.interval.start_time", "physical", operations=_LIST_RECONCILE_ROLLUPS
    ),
    "time-in-heart-rate-zone": _DataTypeSpec(
        "time_in_heart_rate_zone.interval.start_time",
        "physical",
        operations=_LIST_RECONCILE_ROLLUPS,
    ),
    "total-calories": _DataTypeSpec(
        "total_calories.interval.civil_start_time",
        "civil",
        operations=frozenset({"rollup", "daily_rollup"}),
    ),
    "nutrition-log": _DataTypeSpec(
        "nutrition_log.interval.civil_start_time",
        "civil",
        operations=_LIST_GET_RECONCILE_ROLLUPS,
    ),
    "vo2-max": _DataTypeSpec("vo2_max.sample_time.physical_time", "physical"),
    "weight": _DataTypeSpec(
        "weight.sample_time.physical_time",
        "physical",
        operations=_LIST_RECONCILE_ROLLUPS,
    ),
}


class GoogleHealthClientError(Exception):
    """Base error for the Google Health client."""


class MissingRequiredScopeError(GoogleHealthClientError):
    """Raised when Google did not grant every required read-only scope."""


class AuthenticationError(GoogleHealthClientError):
    """Raised when stored credentials are no longer authorized."""


class UpdateFailed(GoogleHealthClientError):
    """Raised when a refresh can be retried without reauthorization."""


@dataclass(slots=True, frozen=True)
class OAuthTokenState:
    """The credential state persisted by the coordinator without logging it."""

    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: datetime = field(repr=False)
    scopes: frozenset[str]


type TokenUpdateCallback = Callable[[OAuthTokenState], Awaitable[None]]


class GoogleHealthClient:
    """A narrow, read-only client for Google Health GET and rollup POST endpoints."""

    def __init__(
        self,
        session: ClientSession,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        token_state: OAuthTokenState,
        scope_grant: ScopeGrant,
        token_update_callback: TokenUpdateCallback,
    ) -> None:
        """Initialize the client with coordinator-owned credentials."""
        self._session = session
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._token_state = token_state
        self._scope_grant = scope_grant
        self._capability_options = {
            spec.option_key: capability_id in scope_grant.enabled_capabilities
            for capability_id, spec in CAPABILITIES.items()
            if spec.option_key is not None
        }
        self._token_update_callback = token_update_callback

    @property
    def scope_grant(self) -> ScopeGrant:
        """Return the current capability grant after any token refresh."""
        return self._scope_grant

    async def async_exchange_code(self, authorization_code: str) -> OAuthTokenState:
        """Exchange a one-time authorization code for fully scoped credentials."""
        state = await self._async_request_token(
            {
                "code": authorization_code,
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "redirect_uri": self._redirect_uri,
                "grant_type": "authorization_code",
            },
            existing_refresh_token=None,
            existing_scopes=frozenset(),
        )
        await self._async_persist_token_state(state)
        return state

    async def async_refresh_access_token(self) -> OAuthTokenState:
        """Refresh the access token while retaining the durable refresh token."""
        state = await self._async_request_token(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._token_state.refresh_token,
                "grant_type": "refresh_token",
            },
            existing_refresh_token=self._token_state.refresh_token,
            existing_scopes=self._token_state.scopes,
        )
        await self._async_persist_token_state(state)
        return state

    async def async_list_data_points(
        self, data_type: str, *, start: datetime, end: datetime
    ) -> list[dict[str, Any]]:
        """List raw data points for one type within an explicit time interval."""
        spec = _get_data_type_spec(data_type)
        if "list" not in spec.operations:
            raise ValueError(f"Google Health data type {data_type} does not support list")
        return await self._async_paginated_get(
            f"users/me/dataTypes/{data_type}/dataPoints",
            {
                "pageSize": spec.page_size,
                "filter": build_time_filter(data_type, start, end),
            },
        )

    async def async_list_paired_devices(self) -> list[Mapping[str, object]]:
        """List paired devices through Google's bounded read-only collection."""
        return await self._async_paginated_get(
            "users/me/pairedDevices",
            {"pageSize": PAIRED_DEVICE_PAGE_SIZE},
            response_key="pairedDevices",
            max_pages=MAX_PAIRED_DEVICE_PAGES,
        )

    async def async_reconcile_data_points(
        self,
        data_type: str,
        *,
        start: datetime,
        end: datetime,
        source_family: Literal["all-sources", "google-wearables"],
    ) -> list[dict[str, Any]]:
        """Read reconciled points for a source family and time interval."""
        spec = _get_data_type_spec(data_type)
        if "reconcile" not in spec.operations:
            raise ValueError(f"Google Health data type {data_type} does not support reconcile")
        params: dict[str, str | int] = {
            "pageSize": spec.page_size,
            "filter": build_time_filter(data_type, start, end),
            "dataSourceFamily": f"users/me/dataSourceFamilies/{source_family}",
        }
        return await self._async_paginated_get(
            f"users/me/dataTypes/{data_type}/dataPoints:reconcile", params
        )

    async def async_daily_rollup_data_points(
        self,
        data_type: str,
        *,
        start: datetime,
        end: datetime,
        source_family: Literal["all-sources", "google-wearables"],
    ) -> list[dict[str, Any]]:
        """Read daily rollup points for a source family and civil-day range."""
        spec = _get_data_type_spec(data_type)
        if "daily_rollup" not in spec.operations:
            raise ValueError(
                f"Google Health data type {data_type} does not support daily rollup"
            )
        _validate_daily_rollup_range(data_type, start, end)
        body: dict[str, object] = {
            "range": {"start": _civil_date(start), "end": _civil_date(end)},
            "windowSizeDays": 1,
            "pageSize": spec.page_size,
            "dataSourceFamily": f"users/me/dataSourceFamilies/{source_family}",
        }
        return await self._async_paginated_post(
            f"users/me/dataTypes/{data_type}/dataPoints:dailyRollUp",
            body,
            response_key="rollupDataPoints",
        )

    async def _async_request_token(
        self,
        form_data: dict[str, str],
        *,
        existing_refresh_token: str | None,
        existing_scopes: frozenset[str],
    ) -> OAuthTokenState:
        """Request and validate a token response without exposing its contents."""
        failure_message: str | None = None
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                async with self._session.post(
                    TOKEN_URL,
                    data=form_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                ) as response:
                    if response.status == 429 or response.status >= 500:
                        raise UpdateFailed("Google OAuth is temporarily unavailable")
                    if response.status >= 400:
                        raise AuthenticationError("Google OAuth rejected the credentials")
                    payload = await response.json()
        except (ClientError, TimeoutError):
            failure_message = "Google OAuth request failed"
        except (TypeError, ValueError):
            failure_message = "Google OAuth returned an invalid response"

        if failure_message is not None:
            raise UpdateFailed(failure_message)

        if not isinstance(payload, dict):
            raise UpdateFailed("Google OAuth returned an invalid response")

        return self._parse_token_state(payload, existing_refresh_token, existing_scopes)

    async def _async_persist_token_state(self, state: OAuthTokenState) -> None:
        """Persist first so a failed write cannot replace the working token state."""
        persistence_failed = False
        try:
            await self._token_update_callback(state)
        except Exception:
            persistence_failed = True
        if persistence_failed:
            raise UpdateFailed("Unable to persist Google OAuth credentials")
        self._token_state = state
        self._scope_grant = self._validate_granted_scopes(state.scopes)

    def _parse_token_state(
        self,
        payload: dict[str, Any],
        existing_refresh_token: str | None,
        existing_scopes: frozenset[str],
    ) -> OAuthTokenState:
        """Validate required fields without putting token values in errors."""
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise AuthenticationError("Google OAuth response did not contain an access token")

        refresh_token = payload.get("refresh_token", existing_refresh_token)
        if not isinstance(refresh_token, str) or not refresh_token:
            raise AuthenticationError("Google OAuth response did not contain a refresh token")

        expires_in = payload.get("expires_in")
        if isinstance(expires_in, bool) or not isinstance(expires_in, int) or expires_in <= 0:
            raise UpdateFailed("Google OAuth response did not contain a valid expiration")

        raw_scope = payload.get("scope")
        if raw_scope is None:
            granted_scopes = existing_scopes
        elif isinstance(raw_scope, str):
            granted_scopes = frozenset(raw_scope.split())
        else:
            raise MissingRequiredScopeError("Google OAuth returned an invalid scope response")

        self._validate_granted_scopes(granted_scopes)

        return OAuthTokenState(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
            scopes=granted_scopes,
        )

    def _validate_granted_scopes(self, granted_scopes: frozenset[str]) -> ScopeGrant:
        """Validate supported scopes while allowing declined optional permissions."""
        try:
            grant = validate_granted_scopes(granted_scopes, self._capability_options)
        except UnsupportedScopeError as err:
            raise MissingRequiredScopeError(
                "Google OAuth returned an unsupported scope"
            ) from err
        if not grant.baseline_valid:
            raise MissingRequiredScopeError(
                "Google OAuth did not grant every required baseline read scope"
            )
        return grant

    async def _async_paginated_get(
        self,
        path: str,
        params: dict[str, str | int],
        *,
        response_key: str = "dataPoints",
        max_pages: int | None = None,
    ) -> list[dict[str, Any]]:
        """Collect every page from a read-only Google Health collection."""
        async def fetch_page(page_token: str | None) -> dict[str, Any]:
            page_params = params.copy()
            if page_token is not None:
                page_params["pageToken"] = page_token
            return await self._async_get_json(
                f"{HEALTH_API_BASE_URL}/{path}", page_params
            )

        return await self._async_collect_paginated(
            fetch_page,
            response_key,
            max_pages=max_pages,
        )

    async def _async_paginated_post(
        self, path: str, body: dict[str, object], *, response_key: str
    ) -> list[dict[str, Any]]:
        """Collect every page from a read-only Google Health POST collection."""
        async def fetch_page(page_token: str | None) -> dict[str, Any]:
            page_body = body.copy()
            if page_token is not None:
                page_body["pageToken"] = page_token
            return await self._async_post_json(
                f"{HEALTH_API_BASE_URL}/{path}", page_body
            )

        return await self._async_collect_paginated(fetch_page, response_key)

    async def _async_collect_paginated(
        self,
        fetch_page: Callable[[str | None], Awaitable[dict[str, Any]]],
        response_key: str,
        *,
        max_pages: int | None = None,
    ) -> list[dict[str, Any]]:
        """Collect a bounded paginated response without exposing token or result values."""
        data_points: list[dict[str, Any]] = []
        page_token: str | None = None
        seen_tokens: set[str] = set()
        page_limit = MAX_PAGINATION_PAGES if max_pages is None else max_pages

        for _page_number in range(page_limit):
            payload = await fetch_page(page_token)
            page_data_points = payload.get(response_key, [])
            if not isinstance(page_data_points, list) or not all(
                isinstance(data_point, dict) for data_point in page_data_points
            ):
                raise UpdateFailed("Google Health returned invalid data points")
            if len(data_points) + len(page_data_points) > MAX_PAGINATION_RESULTS:
                raise UpdateFailed("Google Health pagination exceeded the result limit")
            data_points.extend(page_data_points)

            if "nextPageToken" not in payload:
                return data_points
            next_page_token = payload["nextPageToken"]
            if not isinstance(next_page_token, str):
                raise UpdateFailed("Google Health returned an invalid page token")
            if not next_page_token:
                return data_points
            if next_page_token in seen_tokens:
                raise UpdateFailed(
                    "Google Health pagination returned a repeated page token"
                )
            seen_tokens.add(next_page_token)
            page_token = next_page_token

        raise UpdateFailed("Google Health pagination exceeded the page limit")

    async def _async_get_json(self, url: str, params: dict[str, str | int]) -> dict[str, Any]:
        """Make one GET request, refreshing credentials once after a 401."""
        for has_refreshed in (False, True):
            failure_message: str | None = None
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                    async with self._session.get(
                        url,
                        params=params,
                        headers={
                            "Accept": "application/json",
                            "Authorization": f"Bearer {self._token_state.access_token}",
                        },
                    ) as response:
                        if response.status == 401:
                            if has_refreshed:
                                raise AuthenticationError(
                                    "Google Health credentials require reauthentication"
                                )
                        elif response.status == 429 or response.status >= 500:
                            raise UpdateFailed("Google Health is temporarily unavailable")
                        elif response.status >= 400:
                            raise UpdateFailed("Google Health rejected the data request")
                        else:
                            payload = await response.json()
                            if not isinstance(payload, dict):
                                raise UpdateFailed("Google Health returned an invalid response")
                            return payload
            except (ClientError, TimeoutError):
                failure_message = "Google Health request failed"
            except (TypeError, ValueError):
                failure_message = "Google Health returned an invalid response"

            if failure_message is not None:
                raise UpdateFailed(failure_message)

            await self.async_refresh_access_token()

        raise AuthenticationError("Google Health credentials require reauthentication")

    async def _async_post_json(self, url: str, body: dict[str, object]) -> dict[str, Any]:
        """Make one POST request, refreshing credentials once after a 401."""
        for has_refreshed in (False, True):
            failure_message: str | None = None
            try:
                async with asyncio.timeout(REQUEST_TIMEOUT_SECONDS):
                    async with self._session.post(
                        url,
                        json=body,
                        headers={
                            "Accept": "application/json",
                            "Authorization": f"Bearer {self._token_state.access_token}",
                        },
                    ) as response:
                        if response.status == 401:
                            if has_refreshed:
                                raise AuthenticationError(
                                    "Google Health credentials require reauthentication"
                                )
                        elif response.status == 429 or response.status >= 500:
                            raise UpdateFailed("Google Health is temporarily unavailable")
                        elif response.status >= 400:
                            raise UpdateFailed("Google Health rejected the data request")
                        else:
                            payload = await response.json()
                            if not isinstance(payload, dict):
                                raise UpdateFailed("Google Health returned an invalid response")
                            return payload
            except (ClientError, TimeoutError):
                failure_message = "Google Health request failed"
            except (TypeError, ValueError):
                failure_message = "Google Health returned an invalid response"

            if failure_message is not None:
                raise UpdateFailed(failure_message)

            await self.async_refresh_access_token()

        raise AuthenticationError("Google Health credentials require reauthentication")


def build_time_filter(data_type: str, start: datetime, end: datetime) -> str:
    """Build the documented Google Health v4 filter for a supported data type."""
    spec = _get_data_type_spec(data_type)
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must be timezone-aware")
    if end <= start:
        raise ValueError("end must be after start")

    if spec.time_kind == "civil":
        start_value = _format_civil_timestamp(start)
        end_value = _format_civil_timestamp(end)
    elif spec.time_kind == "daily":
        start_value = start.date().isoformat()
        end_value = end.date().isoformat()
    else:
        start_value = _format_timestamp(start)
        end_value = _format_timestamp(end)
    return f'{spec.filter_field} >= "{start_value}" AND {spec.filter_field} < "{end_value}"'


def get_data_type_operations(data_type: str) -> frozenset[DataTypeOperation]:
    """Return the documented read operations for one supported data type."""
    return _get_data_type_spec(data_type).operations


def _get_data_type_spec(data_type: str) -> _DataTypeSpec:
    """Return the explicit API contract for a supported data type."""
    try:
        return _DATA_TYPE_SPECS[data_type]
    except KeyError as err:
        raise ValueError(f"unsupported Google Health data type: {data_type}") from err


def _format_timestamp(value: datetime) -> str:
    """Render a timezone-aware timestamp in Google's RFC 3339 form."""
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _format_civil_timestamp(value: datetime) -> str:
    """Render a local civil timestamp without a physical-time offset."""
    return value.replace(tzinfo=None).isoformat(timespec="seconds")


def _validate_daily_rollup_range(data_type: str, start: datetime, end: datetime) -> None:
    """Validate the bounded, whole-civil-day range accepted by daily rollups."""
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("start must be timezone-aware")
    if end.tzinfo is None or end.utcoffset() is None:
        raise ValueError("end must be timezone-aware")
    if start.hour or start.minute or start.second or start.microsecond:
        raise ValueError("daily rollup start must be at civil midnight")
    if end.hour or end.minute or end.second or end.microsecond:
        raise ValueError("daily rollup end must be at civil midnight")
    if end <= start:
        raise ValueError("end must be after start")

    maximum_days = (
        14
        if data_type in {"calories-in-heart-rate-zone", "total-calories"}
        else 90
    )
    if (end.date() - start.date()).days > maximum_days:
        raise ValueError(f"daily rollup range cannot exceed {maximum_days} days")


def _civil_date(value: datetime) -> dict[str, dict[str, int]]:
    """Render a civil midnight using Google's CivilDateTime wire shape."""
    return {
        "date": {
            "year": value.year,
            "month": value.month,
            "day": value.day,
        }
    }
