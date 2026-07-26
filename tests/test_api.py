"""Tests for the read-only Google Health API boundary."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
from yarl import URL

from custom_components.resiyhome_health_sync import api as api_module
from custom_components.resiyhome_health_sync.api import (
    AuthenticationError,
    GoogleHealthClient,
    MissingRequiredScopeError,
    OAuthTokenState,
    UpdateFailed,
    build_time_filter,
    get_data_type_operations,
)
from custom_components.resiyhome_health_sync.const import HEALTH_API_BASE_URL, SCOPES, TOKEN_URL

STEPS_URL = f"{HEALTH_API_BASE_URL}/users/me/dataTypes/steps/dataPoints"
RECONCILE_STEPS_URL = f"{STEPS_URL}:reconcile"
DAILY_ROLLUP_ACTIVE_ZONE_MINUTES_URL = (
    f"{HEALTH_API_BASE_URL}/users/me/dataTypes/active-zone-minutes/dataPoints:dailyRollUp"
)
VALID_REFRESH_RESPONSE = {
    "access" + "_token": "refreshed-access-token",
    "expires_in": 3600,
    "scope": " ".join(SCOPES),
    "token_type": "Bearer",
}
RAW_START = datetime(2042, 7, 12, 0, 0, tzinfo=UTC)
RAW_END = datetime(2042, 7, 13, 0, 0, tzinfo=UTC)
AUTHORIZATION_VALUE = " ".join(("Bearer", "initial-access-token"))


@pytest.fixture
def token_update_callback() -> AsyncMock:
    """Provide the coordinator persistence callback."""
    return AsyncMock()


@pytest.fixture
def client(
    hass,
    aioclient_mock,
    token_update_callback: Callable[[OAuthTokenState], Awaitable[None]],
):
    """Provide a client with a Home Assistant-managed mocked HTTP session."""
    return GoogleHealthClient(
        async_get_clientsession(hass),
        **{
            "client_id": "test-client-id",
            "client" + "_secret": "test-client-secret",
            "redirect_uri": "https://example.invalid/oauth/callback",
            "token_state": OAuthTokenState(
                **{
                    "access" + "_token": "initial-access-token",
                    "refresh" + "_token": "initial-refresh-token",
                    "expires_at": datetime(2042, 7, 13, tzinfo=UTC),
                    "scopes": frozenset(SCOPES),
                }
            ),
            "token_update_callback": token_update_callback,
        },
    )


async def test_exchange_rejects_missing_scope(
    client, aioclient_mock, token_update_callback
) -> None:
    """An authorization result missing any approved read scope is rejected."""
    aioclient_mock.post(
        TOKEN_URL,
        json={
            "access" + "_token": "new-access-token",
            "refresh" + "_token": "new-refresh-token",
            "expires_in": 3600,
            "scope": SCOPES[0],
        },
    )

    with pytest.raises(MissingRequiredScopeError):
        await client.async_exchange_code("one-time-code")

    token_update_callback.assert_not_awaited()


async def test_exchange_rejects_extra_scope(client, aioclient_mock, token_update_callback) -> None:
    """An authorization result granting a scope outside the approved set is rejected."""
    aioclient_mock.post(
        TOKEN_URL,
        json={
            "access" + "_token": "new-access-token",
            "refresh" + "_token": "new-refresh-token",
            "expires_in": 3600,
            "scope": f"{' '.join(SCOPES)} https://www.googleapis.com/auth/userinfo.email",
        },
    )

    with pytest.raises(MissingRequiredScopeError):
        await client.async_exchange_code("one-time-code")

    token_update_callback.assert_not_awaited()


async def test_exchange_persists_only_valid_full_scope_token_state(
    client, aioclient_mock, token_update_callback
) -> None:
    """A successful exchange persists the full scoped state through the coordinator."""
    aioclient_mock.post(
        TOKEN_URL,
        json={
            **VALID_REFRESH_RESPONSE,
            "refresh" + "_token": "new-refresh-token",
        },
    )

    state = await client.async_exchange_code("one-time-code")

    assert state.scopes == frozenset(SCOPES)
    assert state.refresh_token == "new-refresh-token"
    token_update_callback.assert_awaited_once_with(state)
    method, _, data, headers = aioclient_mock.mock_calls[0]
    assert method.lower() == "post"
    assert data == {
        "code": "one-time-code",
        "client_id": "test-client-id",
        "client" + "_secret": "test-client-secret",
        "redirect_uri": "https://example.invalid/oauth/callback",
        "grant_type": "authorization_code",
    }
    assert headers == {"Content-Type": "application/x-www-form-urlencoded"}


async def test_refresh_preserves_existing_refresh_token(
    client, aioclient_mock, token_update_callback
) -> None:
    """Google refresh responses without a refresh token retain the existing one."""
    aioclient_mock.post(TOKEN_URL, json=VALID_REFRESH_RESPONSE)

    state = await client.async_refresh_access_token()

    assert state.refresh_token == "initial-refresh-token"
    token_update_callback.assert_awaited_once_with(state)


async def test_failed_token_persistence_keeps_the_last_successful_state(
    client, aioclient_mock, token_update_callback
) -> None:
    """The in-memory token state is not replaced before coordinator persistence succeeds."""
    token_update_callback.side_effect = RuntimeError("storage unavailable")
    aioclient_mock.post(TOKEN_URL, json=VALID_REFRESH_RESPONSE)

    with pytest.raises(UpdateFailed):
        await client.async_refresh_access_token()

    assert client._token_state.access_token == "initial-access-token"


async def test_refresh_retries_request_once(client, aioclient_mock, token_update_callback) -> None:
    """A data request receives exactly one token refresh and retry after a 401."""
    responses = iter(
        (
            AiohttpClientMockResponse("get", URL(STEPS_URL), status=401),
            AiohttpClientMockResponse("get", URL(STEPS_URL), json={"dataPoints": []}),
        )
    )

    async def get_response(*_args: Any) -> AiohttpClientMockResponse:
        return next(responses)

    aioclient_mock.get(STEPS_URL, side_effect=get_response)
    aioclient_mock.post(TOKEN_URL, json=VALID_REFRESH_RESPONSE)

    assert await client.async_list_data_points("steps", start=RAW_START, end=RAW_END) == []
    assert [call[0].lower() for call in aioclient_mock.mock_calls] == ["get", "post", "get"]
    token_update_callback.assert_awaited_once()


async def test_second_unauthorized_response_requires_reauthentication(
    client, aioclient_mock
) -> None:
    """A 401 after a refresh is an authentication failure, not an endless retry."""
    aioclient_mock.get(STEPS_URL, status=401)
    aioclient_mock.post(TOKEN_URL, json=VALID_REFRESH_RESPONSE)
    aioclient_mock.get(STEPS_URL, status=401)

    with pytest.raises(AuthenticationError):
        await client.async_list_data_points("steps", start=RAW_START, end=RAW_END)

    assert aioclient_mock.call_count == 3


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_transient_data_failures_raise_update_failed(
    client, aioclient_mock, status: int
) -> None:
    """Rate limits and server failures are surfaced as retryable update failures."""
    aioclient_mock.get(STEPS_URL, status=status)

    with pytest.raises(UpdateFailed):
        await client.async_list_data_points("steps", start=RAW_START, end=RAW_END)


async def test_list_data_points_paginates_with_get_only(client, aioclient_mock) -> None:
    """List results are accumulated across all GET pages without write calls."""
    expected_filter = build_time_filter("steps", RAW_START, RAW_END)
    aioclient_mock.get(
        STEPS_URL,
        params={"pageSize": 10000, "filter": expected_filter, "pageToken": "next-page"},
        json={"dataPoints": [{"steps": {"count": "2"}}]},
    )
    aioclient_mock.get(
        STEPS_URL,
        params={"pageSize": 10000, "filter": expected_filter},
        json={
            "dataPoints": [{"steps": {"count": "1"}}],
            "nextPageToken": "next-page",
        },
    )

    result = await client.async_list_data_points("steps", start=RAW_START, end=RAW_END)

    assert result == [{"steps": {"count": "1"}}, {"steps": {"count": "2"}}]
    assert [call[0].lower() for call in aioclient_mock.mock_calls] == ["get", "get"]
    assert aioclient_mock.mock_calls[0][3] == {
        "Accept": "application/json",
        "Authorization": AUTHORIZATION_VALUE,
    }


@pytest.mark.parametrize(
    ("data_type", "page_size"),
    [("steps", 10000), ("exercise", 25), ("sleep", 25)],
)
async def test_list_uses_the_supported_data_type_page_size(
    client, aioclient_mock, data_type: str, page_size: int
) -> None:
    """List uses the Google Health maximum page size for the selected data type."""
    data_points_url = f"{HEALTH_API_BASE_URL}/users/me/dataTypes/{data_type}/dataPoints"
    expected_params = {
        "pageSize": page_size,
        "filter": build_time_filter(data_type, RAW_START, RAW_END),
    }
    aioclient_mock.get(data_points_url, params=expected_params, json={"dataPoints": []})

    assert await client.async_list_data_points(data_type, start=RAW_START, end=RAW_END) == []


async def test_raw_list_uses_the_exact_bounded_detroit_dst_window(client, aioclient_mock) -> None:
    """Raw source attribution is bounded to the same physical spring-DST day."""
    detroit = ZoneInfo("America/Detroit")
    start = datetime(2026, 3, 8, 0, 0, tzinfo=detroit)
    end = datetime(2026, 3, 9, 0, 0, tzinfo=detroit)
    expected_params = {
        "pageSize": 10000,
        "filter": (
            'steps.interval.start_time >= "2026-03-08T05:00:00Z" '
            'AND steps.interval.start_time < "2026-03-09T04:00:00Z"'
        ),
    }
    raw_point = {
        "dataSource": {"platform": "FITBIT"},
        "steps": {
            "interval": {
                "startTime": "2026-03-09T03:30:00Z",
                "startUtcOffset": "-14400s",
                "endTime": "2026-03-09T03:45:00Z",
                "endUtcOffset": "-14400s",
            },
            "count": "125",
        },
    }
    aioclient_mock.get(STEPS_URL, params=expected_params, json={"dataPoints": [raw_point]})

    assert await client.async_list_data_points("steps", start=start, end=end) == [raw_point]


@pytest.mark.parametrize(
    ("data_type", "page_size"),
    [("steps", 10000), ("exercise", 25), ("sleep", 25)],
)
async def test_reconcile_uses_source_family_time_range_and_data_type_page_size(
    client, aioclient_mock, data_type: str, page_size: int
) -> None:
    """Reconciliation remains a read-only GET with its supported page size."""
    start = datetime(2042, 7, 12, 0, 0, tzinfo=UTC)
    end = datetime(2042, 7, 13, 0, 0, tzinfo=UTC)
    expected_params = {
        "pageSize": page_size,
        "filter": build_time_filter(data_type, start, end),
        "dataSourceFamily": "users/me/dataSourceFamilies/google-wearables",
    }
    aioclient_mock.get(
        f"{HEALTH_API_BASE_URL}/users/me/dataTypes/{data_type}/dataPoints:reconcile",
        params=expected_params,
        json={"dataPoints": [{"steps": {"count": "42"}}]},
    )

    result = await client.async_reconcile_data_points(
        data_type, start=start, end=end, source_family="google-wearables"
    )

    assert result == [{"steps": {"count": "42"}}]
    assert [call[0].lower() for call in aioclient_mock.mock_calls] == ["get"]


@pytest.mark.parametrize(
    ("data_type", "start", "end", "expected"),
    [
        (
            "active-energy-burned",
            datetime(2042, 7, 12, 0, 0, tzinfo=UTC),
            datetime(2042, 7, 13, 0, 0, tzinfo=UTC),
            'active_energy_burned.interval.start_time >= "2042-07-12T00:00:00Z" '
            'AND active_energy_burned.interval.start_time < "2042-07-13T00:00:00Z"',
        ),
        (
            "active-minutes",
            datetime(2042, 7, 12, 0, 0, tzinfo=UTC),
            datetime(2042, 7, 13, 0, 0, tzinfo=UTC),
            'active_minutes.interval.start_time >= "2042-07-12T00:00:00Z" '
            'AND active_minutes.interval.start_time < "2042-07-13T00:00:00Z"',
        ),
        (
            "active-zone-minutes",
            datetime(2042, 7, 12, 0, 0, tzinfo=UTC),
            datetime(2042, 7, 13, 0, 0, tzinfo=UTC),
            'active_zone_minutes.interval.start_time >= "2042-07-12T00:00:00Z" '
            'AND active_zone_minutes.interval.start_time < "2042-07-13T00:00:00Z"',
        ),
        (
            "steps",
            datetime(2042, 7, 12, 0, 0, tzinfo=UTC),
            datetime(2042, 7, 13, 0, 0, tzinfo=UTC),
            'steps.interval.start_time >= "2042-07-12T00:00:00Z" '
            'AND steps.interval.start_time < "2042-07-13T00:00:00Z"',
        ),
        (
            "distance",
            datetime(2042, 7, 12, 0, 0, tzinfo=UTC),
            datetime(2042, 7, 13, 0, 0, tzinfo=UTC),
            'distance.interval.start_time >= "2042-07-12T00:00:00Z" '
            'AND distance.interval.start_time < "2042-07-13T00:00:00Z"',
        ),
        (
            "heart-rate",
            datetime(2042, 7, 12, 0, 0, tzinfo=UTC),
            datetime(2042, 7, 13, 0, 0, tzinfo=UTC),
            'heart_rate.sample_time.physical_time >= "2042-07-12T00:00:00Z" '
            'AND heart_rate.sample_time.physical_time < "2042-07-13T00:00:00Z"',
        ),
        (
            "heart-rate-variability",
            datetime(2042, 7, 12, 0, 0, tzinfo=UTC),
            datetime(2042, 7, 13, 0, 0, tzinfo=UTC),
            'heart_rate_variability.sample_time.physical_time >= "2042-07-12T00:00:00Z" '
            'AND heart_rate_variability.sample_time.physical_time < "2042-07-13T00:00:00Z"',
        ),
        (
            "daily-heart-rate-variability",
            datetime(2042, 7, 12, 0, 0, tzinfo=UTC),
            datetime(2042, 7, 13, 0, 0, tzinfo=UTC),
            'daily_heart_rate_variability.date >= "2042-07-12" '
            'AND daily_heart_rate_variability.date < "2042-07-13"',
        ),
        (
            "daily-resting-heart-rate",
            datetime(2042, 7, 12, 0, 0, tzinfo=UTC),
            datetime(2042, 7, 13, 0, 0, tzinfo=UTC),
            'daily_resting_heart_rate.date >= "2042-07-12" '
            'AND daily_resting_heart_rate.date < "2042-07-13"',
        ),
        (
            "exercise",
            datetime(2042, 7, 12, 0, 0, tzinfo=ZoneInfo("America/Detroit")),
            datetime(2042, 7, 13, 0, 0, tzinfo=ZoneInfo("America/Detroit")),
            'exercise.interval.civil_start_time >= "2042-07-12T00:00:00" '
            'AND exercise.interval.civil_start_time < "2042-07-13T00:00:00"',
        ),
        (
            "sleep",
            datetime(2042, 7, 12, 0, 0, tzinfo=UTC),
            datetime(2042, 7, 13, 0, 0, tzinfo=UTC),
            'sleep.interval.end_time >= "2042-07-12T00:00:00Z" '
            'AND sleep.interval.end_time < "2042-07-13T00:00:00Z"',
        ),
    ],
)
def test_build_time_filter_uses_the_google_health_data_type_contract(
    data_type: str, start: datetime, end: datetime, expected: str
) -> None:
    """Every supported filter category uses its documented field and time representation."""
    assert build_time_filter(data_type, start, end) == expected


def test_build_time_filter_rejects_naive_timestamps() -> None:
    """A caller must choose an explicit timezone before querying health data."""
    with pytest.raises(ValueError, match="timezone-aware"):
        build_time_filter("steps", datetime(2042, 7, 12), datetime(2042, 7, 13))


def test_build_time_filter_rejects_unsupported_data_types() -> None:
    """Unsupported data types fail closed instead of guessing an API filter field."""
    with pytest.raises(ValueError, match="unsupported"):
        build_time_filter(
            "unknown-google-health-type",
            datetime(2042, 7, 12, tzinfo=UTC),
            datetime(2042, 7, 13, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    ("data_type", "expected"),
    [
        (
            "active-zone-minutes",
            'active_zone_minutes.interval.start_time >= "2042-07-12T00:00:00Z" '
            'AND active_zone_minutes.interval.start_time < "2042-07-13T00:00:00Z"',
        ),
        (
            "daily-vo2-max",
            'daily_vo2_max.date >= "2042-07-12" '
            'AND daily_vo2_max.date < "2042-07-13"',
        ),
        (
            "oxygen-saturation",
            'oxygen_saturation.sample_time.physical_time >= "2042-07-12T00:00:00Z" '
            'AND oxygen_saturation.sample_time.physical_time < "2042-07-13T00:00:00Z"',
        ),
        (
            "respiratory-rate-sleep-summary",
            'respiratory_rate_sleep_summary.sample_time.physical_time '
            '>= "2042-07-12T00:00:00Z" AND '
            'respiratory_rate_sleep_summary.sample_time.physical_time '
            '< "2042-07-13T00:00:00Z"',
        ),
        (
            "body-fat",
            'body_fat.sample_time.physical_time >= "2042-07-12T00:00:00Z" '
            'AND body_fat.sample_time.physical_time < "2042-07-13T00:00:00Z"',
        ),
        (
            "floors",
            'floors.interval.start_time >= "2042-07-12T00:00:00Z" '
            'AND floors.interval.start_time < "2042-07-13T00:00:00Z"',
        ),
    ],
)
def test_optional_probe_data_types_use_documented_filter_fields(
    data_type: str, expected: str
) -> None:
    """Optional probes use the documented record-specific filter path."""
    assert build_time_filter(data_type, RAW_START, RAW_END) == expected


async def test_data_type_operations_reject_unsupported_endpoint_methods(client) -> None:
    """Operation metadata prevents false no-data results from invalid endpoints."""
    assert get_data_type_operations("floors") == frozenset(
        {"reconcile", "rollup", "daily_rollup"}
    )
    assert get_data_type_operations("calories-in-heart-rate-zone") == frozenset(
        {"rollup", "daily_rollup"}
    )

    with pytest.raises(ValueError, match="does not support list"):
        await client.async_list_data_points("floors", start=RAW_START, end=RAW_END)
    with pytest.raises(ValueError, match="does not support reconcile"):
        await client.async_reconcile_data_points(
            "calories-in-heart-rate-zone",
            start=RAW_START,
            end=RAW_END,
            source_family="all-sources",
        )


@pytest.mark.parametrize("next_page_token", [0, False, [], {}, None])
async def test_present_non_string_page_token_raises_update_failed(
    client, aioclient_mock, next_page_token: object
) -> None:
    """Only an absent, empty, or non-empty string continuation token is accepted."""
    aioclient_mock.get(
        STEPS_URL,
        json={"dataPoints": [], "nextPageToken": next_page_token},
    )

    with pytest.raises(UpdateFailed):
        await client.async_list_data_points("steps", start=RAW_START, end=RAW_END)


async def test_empty_page_token_completes_pagination(client, aioclient_mock) -> None:
    """An explicitly empty continuation token is a complete response."""
    aioclient_mock.get(STEPS_URL, json={"dataPoints": [], "nextPageToken": ""})

    assert await client.async_list_data_points("steps", start=RAW_START, end=RAW_END) == []


@pytest.mark.parametrize("method", ["get", "post"])
async def test_pagination_rejects_repeated_tokens_without_exposing_them(
    client, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """A provider token cycle stops before another request and remains redacted."""
    private_token = "private-continuation-token"
    response_key = "dataPoints" if method == "get" else "rollupDataPoints"
    request = AsyncMock(
        side_effect=[
            {response_key: [], "nextPageToken": private_token},
            {response_key: [], "nextPageToken": private_token},
        ]
    )
    monkeypatch.setattr(client, f"_async_{method}_json", request)

    with pytest.raises(UpdateFailed) as raised:
        if method == "get":
            await client._async_paginated_get("private/path", {})
        else:
            await client._async_paginated_post(
                "private/path", {}, response_key=response_key
            )

    assert request.await_count == 2
    assert str(raised.value) == "Google Health pagination returned a repeated page token"
    assert private_token not in str(raised.value)


@pytest.mark.parametrize("method", ["get", "post"])
async def test_pagination_rejects_endlessly_changing_tokens_at_page_limit(
    client, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """Unique continuation tokens cannot bypass the local maximum page count."""
    response_key = "dataPoints" if method == "get" else "rollupDataPoints"
    request = AsyncMock(
        side_effect=[
            {response_key: [], "nextPageToken": "private-page-one"},
            {response_key: [], "nextPageToken": "private-page-two"},
        ]
    )
    monkeypatch.setattr(client, f"_async_{method}_json", request)
    monkeypatch.setattr(api_module, "MAX_PAGINATION_PAGES", 2)

    with pytest.raises(UpdateFailed) as raised:
        if method == "get":
            await client._async_paginated_get("private/path", {})
        else:
            await client._async_paginated_post(
                "private/path", {}, response_key=response_key
            )

    assert request.await_count == 2
    assert str(raised.value) == "Google Health pagination exceeded the page limit"
    assert "private-page" not in str(raised.value)


@pytest.mark.parametrize("method", ["get", "post"])
async def test_pagination_rejects_result_overflow_without_exposing_values(
    client, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    """A bounded result accumulator fails closed without serializing health data."""
    response_key = "dataPoints" if method == "get" else "rollupDataPoints"
    request = AsyncMock(
        return_value={
            response_key: [
                {"private_health_value": "first-secret"},
                {"private_health_value": "second-secret"},
            ]
        }
    )
    monkeypatch.setattr(client, f"_async_{method}_json", request)
    monkeypatch.setattr(api_module, "MAX_PAGINATION_RESULTS", 1)

    with pytest.raises(UpdateFailed) as raised:
        if method == "get":
            await client._async_paginated_get("private/path", {})
        else:
            await client._async_paginated_post(
                "private/path", {}, response_key=response_key
            )

    assert str(raised.value) == "Google Health pagination exceeded the result limit"
    assert "secret" not in str(raised.value)


async def test_daily_rollup_posts_the_civil_range_and_paginates(client, aioclient_mock) -> None:
    """Daily rollups preserve their POST body while collecting every result page."""
    start = datetime(2042, 7, 13, tzinfo=UTC)
    end = datetime(2042, 7, 14, tzinfo=UTC)
    expected_body = {
        "range": {
            "start": {"date": {"year": 2042, "month": 7, "day": 13}},
            "end": {"date": {"year": 2042, "month": 7, "day": 14}},
        },
        "windowSizeDays": 1,
        "pageSize": 10000,
        "dataSourceFamily": "users/me/dataSourceFamilies/all-sources",
    }
    responses = iter(
        (
            AiohttpClientMockResponse(
                "post",
                URL(DAILY_ROLLUP_ACTIVE_ZONE_MINUTES_URL),
                json={
                    "rollupDataPoints": [{"activeZoneMinutes": {"total": "10"}}],
                    "nextPageToken": "next-page",
                },
            ),
            AiohttpClientMockResponse(
                "post",
                URL(DAILY_ROLLUP_ACTIVE_ZONE_MINUTES_URL),
                json={"rollupDataPoints": [{"activeZoneMinutes": {"total": "20"}}]},
            ),
        )
    )

    async def post_response(*_args: Any) -> AiohttpClientMockResponse:
        return next(responses)

    aioclient_mock.post(DAILY_ROLLUP_ACTIVE_ZONE_MINUTES_URL, side_effect=post_response)

    result = await client.async_daily_rollup_data_points(
        "active-zone-minutes", start=start, end=end, source_family="all-sources"
    )

    assert result == [
        {"activeZoneMinutes": {"total": "10"}},
        {"activeZoneMinutes": {"total": "20"}},
    ]
    assert [call[0].lower() for call in aioclient_mock.mock_calls] == ["post", "post"]
    assert aioclient_mock.mock_calls[0][2] == expected_body
    assert aioclient_mock.mock_calls[1][2] == {**expected_body, "pageToken": "next-page"}
    assert aioclient_mock.mock_calls[0][3] == {
        "Accept": "application/json",
        "Authorization": AUTHORIZATION_VALUE,
    }


async def test_daily_rollup_rejects_an_unsupported_operation(client) -> None:
    """Daily rollups fail before an API request when metadata does not allow them."""
    with pytest.raises(ValueError, match="does not support daily rollup"):
        await client.async_daily_rollup_data_points(
            "daily-vo2-max",
            start=RAW_START,
            end=RAW_END,
            source_family="all-sources",
        )


@pytest.mark.parametrize(
    ("data_type", "end", "message"),
    [
        ("calories-in-heart-rate-zone", RAW_START + timedelta(days=15), "14 days"),
        ("steps", RAW_START + timedelta(days=91), "90 days"),
    ],
)
async def test_daily_rollup_rejects_ranges_over_the_documented_limit(
    client, data_type: str, end: datetime, message: str
) -> None:
    """Each daily rollup type has an explicit maximum civil-day query span."""
    with pytest.raises(ValueError, match=message):
        await client.async_daily_rollup_data_points(
            data_type, start=RAW_START, end=end, source_family="all-sources"
        )


async def test_daily_rollup_rejects_non_midnight_civil_boundaries(client) -> None:
    """Daily API windows cannot silently truncate a partial civil day."""
    with pytest.raises(ValueError, match="midnight"):
        await client.async_daily_rollup_data_points(
            "steps",
            start=RAW_START + timedelta(minutes=1),
            end=RAW_END,
            source_family="all-sources",
        )


@pytest.mark.parametrize("next_page_token", [0, False, [], {}, None])
async def test_daily_rollup_rejects_malformed_page_tokens(
    client, aioclient_mock, next_page_token: object
) -> None:
    """Daily rollup pagination accepts only string continuation tokens."""
    aioclient_mock.post(
        DAILY_ROLLUP_ACTIVE_ZONE_MINUTES_URL,
        json={"rollupDataPoints": [], "nextPageToken": next_page_token},
    )

    with pytest.raises(UpdateFailed):
        await client.async_daily_rollup_data_points(
            "active-zone-minutes",
            start=RAW_START,
            end=RAW_END,
            source_family="all-sources",
        )


async def test_daily_rollup_refreshes_once_after_an_unauthorized_response(
    client, aioclient_mock, token_update_callback
) -> None:
    """A daily rollup request uses the established single-refresh retry contract."""
    responses = iter(
        (
            AiohttpClientMockResponse(
                "post", URL(DAILY_ROLLUP_ACTIVE_ZONE_MINUTES_URL), status=401
            ),
            AiohttpClientMockResponse(
                "post", URL(DAILY_ROLLUP_ACTIVE_ZONE_MINUTES_URL), json={"rollupDataPoints": []}
            ),
        )
    )

    async def post_response(*_args: Any) -> AiohttpClientMockResponse:
        return next(responses)

    aioclient_mock.post(DAILY_ROLLUP_ACTIVE_ZONE_MINUTES_URL, side_effect=post_response)
    aioclient_mock.post(TOKEN_URL, json=VALID_REFRESH_RESPONSE)

    assert await client.async_daily_rollup_data_points(
        "active-zone-minutes",
        start=RAW_START,
        end=RAW_END,
        source_family="all-sources",
    ) == []
    assert [call[1] for call in aioclient_mock.mock_calls] == [
        URL(DAILY_ROLLUP_ACTIVE_ZONE_MINUTES_URL),
        URL(TOKEN_URL),
        URL(DAILY_ROLLUP_ACTIVE_ZONE_MINUTES_URL),
    ]
    token_update_callback.assert_awaited_once()


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_daily_rollup_transient_failures_hide_response_content(
    client, aioclient_mock, status: int
) -> None:
    """Rollup errors remain retryable without exposing Google's response body."""
    aioclient_mock.post(
        DAILY_ROLLUP_ACTIVE_ZONE_MINUTES_URL,
        status=status,
        json={"error": "sensitive response content"},
    )

    with pytest.raises(UpdateFailed) as err:
        await client.async_daily_rollup_data_points(
            "active-zone-minutes",
            start=RAW_START,
            end=RAW_END,
            source_family="all-sources",
        )

    assert "sensitive response content" not in str(err.value)
