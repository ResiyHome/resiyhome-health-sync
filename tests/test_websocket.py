"""Tests for normalized Health Sync history WebSocket access."""

import json
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components import websocket_api
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_integration,
    mock_platform,
)

from custom_components.resiyhome_health_sync import (
    async_setup_entry,
    async_unload_entry,
    config_flow,
)
from custom_components.resiyhome_health_sync.const import DOMAIN, NUTRITION_SCOPE, SCOPES
from custom_components.resiyhome_health_sync.models import (
    CoordinatorSnapshot,
    DailySummary,
    ExpandedDailyMetrics,
    SourceKind,
)
from custom_components.resiyhome_health_sync.websocket import _COMMAND, _async_handle_history

NOW = datetime(2042, 7, 13, 12, 0, tzinfo=UTC)


def _entry(
    hass,
    *,
    person_name: str = "Sample Alpha",
    person_slug: str = "sample_alpha",
    include_body_measurements: bool = False,
    include_nutrition: bool = False,
    scopes: tuple[str, ...] = SCOPES,
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
            "expires_at": "2042-07-13T13:00:00+00:00",
            "scopes": list(scopes),
        },
        options={
            "include_body_measurements": include_body_measurements,
            "include_nutrition": include_nutrition,
        },
    )
    entry.add_to_hass(hass)
    return entry


def _register_integration(hass) -> None:
    module = MockModule(
        DOMAIN,
        async_setup_entry=async_setup_entry,
        async_unload_entry=async_unload_entry,
        partial_manifest={"config_flow": True, "version": "0.1.0"},
    )
    mock_integration(hass, module, built_in=False)
    mock_platform(hass, f"{DOMAIN}.config_flow", config_flow, built_in=False)


def _summary(
    day: date,
    *,
    steps: int | None,
    source: SourceKind = SourceKind.MIXED,
    expanded: ExpandedDailyMetrics | None = None,
    total_energy_kcal: float | None = None,
    nutrition_energy_kcal: float | None = None,
    hydration_ml: float | None = None,
    sleep_period_minutes: float | None = None,
    sleep_onset_minutes: float | None = None,
    sleep_after_wake_minutes: float | None = None,
) -> DailySummary:
    return DailySummary(
        date=day,
        steps=steps,
        fitbit_steps=None,
        distance_m=1234.5,
        active_energy_kcal=None,
        exercise_minutes=30.0,
        sleep_minutes=None,
        sleep_stages={"deep": 60.0},
        resting_heart_rate=54.0,
        average_heart_rate=None,
        minimum_heart_rate=None,
        maximum_heart_rate=None,
        hrv_ms=None,
        expanded=expanded or ExpandedDailyMetrics(),
        source=source,
        complete=True,
        updated_at=NOW,
        total_energy_kcal=total_energy_kcal,
        nutrition_energy_kcal=nutrition_energy_kcal,
        hydration_ml=hydration_ml,
        sleep_period_minutes=sleep_period_minutes,
        sleep_onset_minutes=sleep_onset_minutes,
        sleep_after_wake_minutes=sleep_after_wake_minutes,
    )


def _coordinator(history) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = CoordinatorSnapshot(
        current_day=_summary(date(2042, 7, 13), steps=6200),
        last_success=NOW,
        last_attempt=NOW,
        authorization_healthy=True,
        backfill_cursor=date(2042, 7, 1),
        backfill_complete=True,
    )
    coordinator.data_types = ("steps", "sleep")
    coordinator.is_stale = False
    coordinator.async_refresh_current = AsyncMock(return_value=coordinator.data)
    coordinator.async_set_updated_data = MagicMock()
    coordinator.async_backfill_step = AsyncMock()
    return coordinator


async def _setup_person(hass, entry: MockConfigEntry, history, coordinator):
    history.async_shutdown = AsyncMock()
    with (
        patch(
            "custom_components.resiyhome_health_sync.GoogleHealthClient", return_value=MagicMock()
        ),
        patch("custom_components.resiyhome_health_sync.HealthHistoryStore", return_value=history),
        patch(
            "custom_components.resiyhome_health_sync.HealthSyncCoordinator",
            return_value=coordinator,
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


class _Connection:
    """Capture Home Assistant WebSocket command output without binding a socket."""

    def __init__(self) -> None:
        self.result: dict | None = None
        self.error: dict | None = None

    def send_result(self, msg_id: int, result: dict) -> None:
        self.result = {"id": msg_id, "success": True, "result": result}

    def send_error(self, msg_id: int, code: str, message: str) -> None:
        self.error = {
            "id": msg_id,
            "success": False,
            "error": {"code": code, "message": message},
        }


async def test_history_websocket_returns_normalized_inclusive_records(hass) -> None:
    _register_integration(hass)
    sample_alpha = _entry(hass, person_slug="sample_alpha")
    history = MagicMock()
    history.backfill_cursor = date(2042, 7, 1)
    rows = [
        _summary(date(2042, 7, 12), steps=None, source=SourceKind.UNAVAILABLE),
        _summary(date(2042, 7, 13), steps=6200),
    ]
    history.async_load = AsyncMock(return_value=rows)
    history.async_query = AsyncMock(return_value=rows)
    coordinator = _coordinator(history)
    await _setup_person(hass, sample_alpha, history, coordinator)

    connection = _Connection()
    await _async_handle_history(
        hass,
        connection,
        {
            "id": 7,
            "type": "resiyhome_health_sync/history",
            "person": "sample_alpha",
            "start_date": "2042-07-12",
            "end_date": "2042-07-13",
            "metrics": ["steps", "sleep_minutes", "source"],
        },
    )

    assert connection.error is None
    assert connection.result == {
        "id": 7,
        "success": True,
        "result": {
            "person": "sample_alpha",
            "start_date": "2042-07-12",
            "end_date": "2042-07-13",
            "records": [
                {
                    "date": "2042-07-12",
                    "steps": None,
                    "sleep_minutes": None,
                    "source": "unavailable",
                },
                {
                    "date": "2042-07-13",
                    "steps": 6200,
                    "sleep_minutes": None,
                    "source": "mixed",
                },
            ],
        },
    }
    history.async_query.assert_awaited_once_with(date(2042, 7, 12), date(2042, 7, 13))
    assert "raw" not in repr(connection.result).lower()
    assert "client_secret" not in repr(connection.result).lower()


async def test_history_websocket_returns_only_json_safe_normalized_expanded_metrics(hass) -> None:
    _register_integration(hass)
    sample_alpha = _entry(hass, person_slug="sample_alpha", include_body_measurements=True)
    expanded = ExpandedDailyMetrics(
        active_zone_minutes={"fat_burn": 12.0, "cardio": 8.0, "peak": 4.0},
        vo2_max=42.5,
        vo2_estimated=False,
        cardio_fitness_level="GOOD",
        oxygen_average=96.2,
        oxygen_lower_bound=95.1,
        oxygen_upper_bound=97.3,
        oxygen_standard_deviation=0.4,
        daily_respiratory_rate=15.4,
        sleep_respiratory_rates={"full": 14.8, "deep": 14.1},
        sleep_respiratory_standard_deviation=0.7,
        sleep_respiratory_signal_to_noise=3.2,
        floors=7,
        sedentary_minutes=480.0,
        heart_zone_minutes={"vigorous": 23.5},
        heart_zone_thresholds={"vigorous": (133, 159)},
        heart_zone_calories={"vigorous": 184.2},
        weight_kg=80.5,
    )
    row = _summary(date(2042, 7, 13), steps=6200, expanded=expanded)
    history = MagicMock()
    history.backfill_cursor = date(2042, 7, 1)
    history.async_load = AsyncMock(return_value=[row])
    history.async_query = AsyncMock(return_value=[row])
    await _setup_person(hass, sample_alpha, history, _coordinator(history))
    metrics = [
        "active_zone_minutes",
        "vo2_max",
        "vo2_estimated",
        "cardio_fitness_level",
        "oxygen_average",
        "oxygen_lower_bound",
        "oxygen_upper_bound",
        "oxygen_standard_deviation",
        "daily_respiratory_rate",
        "sleep_respiratory_rates",
        "sleep_respiratory_standard_deviation",
        "sleep_respiratory_signal_to_noise",
        "floors",
        "sedentary_minutes",
        "heart_zone_minutes",
        "heart_zone_thresholds",
        "heart_zone_calories",
        "weight_kg",
    ]

    connection = _Connection()
    await _async_handle_history(
        hass,
        connection,
        {
            "id": 8,
            "type": "resiyhome_health_sync/history",
            "person": "sample_alpha",
            "start_date": "2042-07-13",
            "end_date": "2042-07-13",
            "metrics": metrics,
        },
    )

    assert connection.error is None
    assert connection.result is not None
    record = connection.result["result"]["records"][0]
    assert record == {
        "date": "2042-07-13",
        "active_zone_minutes": {"fat_burn": 12.0, "cardio": 8.0, "peak": 4.0},
        "vo2_max": 42.5,
        "vo2_estimated": False,
        "cardio_fitness_level": "GOOD",
        "oxygen_average": 96.2,
        "oxygen_lower_bound": 95.1,
        "oxygen_upper_bound": 97.3,
        "oxygen_standard_deviation": 0.4,
        "daily_respiratory_rate": 15.4,
        "sleep_respiratory_rates": {"full": 14.8, "deep": 14.1},
        "sleep_respiratory_standard_deviation": 0.7,
        "sleep_respiratory_signal_to_noise": 3.2,
        "floors": 7,
        "sedentary_minutes": 480.0,
        "heart_zone_minutes": {"vigorous": 23.5},
        "heart_zone_thresholds": {"vigorous": [133, 159]},
        "heart_zone_calories": {"vigorous": 184.2},
        "weight_kg": 80.5,
    }
    assert json.loads(json.dumps(connection.result)) == connection.result
    assert "raw" not in repr(connection.result).lower()
    assert "samples" not in repr(connection.result).lower()


async def test_history_weight_is_hidden_when_body_measurements_are_disabled(hass) -> None:
    """A stale stored weight cannot cross the current per-person opt-out boundary."""
    _register_integration(hass)
    entry = _entry(hass, person_slug="sample_alpha", include_body_measurements=False)
    row = _summary(
        date(2042, 7, 13),
        steps=6200,
        expanded=ExpandedDailyMetrics(
            weight_kg=80.5,
            body_fat_percentage=21.4,
            height_m=1.778,
        ),
    )
    history = MagicMock()
    history.backfill_cursor = date(2042, 7, 1)
    history.async_load = AsyncMock(return_value=[row])
    history.async_query = AsyncMock(return_value=[row])
    await _setup_person(hass, entry, history, _coordinator(history))
    connection = _Connection()

    await _async_handle_history(
        hass,
        connection,
        {
            "id": 10,
            "type": "resiyhome_health_sync/history",
            "person": "sample_alpha",
            "start_date": "2042-07-13",
            "end_date": "2042-07-13",
            "metrics": ["weight_kg", "body_fat_percentage", "height_m"],
        },
    )

    assert connection.error is None
    assert connection.result is not None
    assert connection.result["result"]["records"] == [
        {
            "date": "2042-07-13",
            "weight_kg": None,
            "body_fat_percentage": None,
            "height_m": None,
        }
    ]


async def test_history_websocket_returns_allowlisted_parity_metrics(hass) -> None:
    """Authorized requests expose normalized parity fields without paired metadata."""
    _register_integration(hass)
    entry = _entry(
        hass,
        person_slug="sample_alpha",
        include_body_measurements=True,
        include_nutrition=True,
        scopes=(*SCOPES, NUTRITION_SCOPE),
    )
    row = _summary(
        date(2042, 7, 13),
        steps=6200,
        total_energy_kcal=2410.5,
        nutrition_energy_kcal=1830.0,
        hydration_ml=2150.0,
        sleep_period_minutes=402.0,
        sleep_onset_minutes=6.0,
        sleep_after_wake_minutes=12.0,
        expanded=ExpandedDailyMetrics(
            body_fat_percentage=21.4,
            height_m=1.778,
        ),
    )
    history = MagicMock()
    history.async_load = AsyncMock(return_value=[row])
    history.async_query = AsyncMock(return_value=[row])
    await _setup_person(hass, entry, history, _coordinator(history))
    connection = _Connection()

    await _async_handle_history(
        hass,
        connection,
        {
            "id": 13,
            "type": "resiyhome_health_sync/history",
            "person": "sample_alpha",
            "start_date": "2042-07-13",
            "end_date": "2042-07-13",
            "metrics": [
                "total_energy_kcal",
                "nutrition_energy_kcal",
                "hydration_ml",
                "sleep_period_minutes",
                "sleep_onset_minutes",
                "sleep_after_wake_minutes",
                "body_fat_percentage",
                "height_m",
            ],
        },
    )

    assert connection.error is None
    assert connection.result is not None
    assert connection.result["result"]["records"] == [
        {
            "date": "2042-07-13",
            "total_energy_kcal": 2410.5,
            "nutrition_energy_kcal": 1830.0,
            "hydration_ml": 2150.0,
            "sleep_period_minutes": 402.0,
            "sleep_onset_minutes": 6.0,
            "sleep_after_wake_minutes": 12.0,
            "body_fat_percentage": 21.4,
            "height_m": 1.778,
        }
    ]


@pytest.mark.parametrize(
    ("include_nutrition", "scopes"),
    [
        (False, (*SCOPES, NUTRITION_SCOPE)),
        (True, SCOPES),
    ],
)
async def test_history_nutrition_requires_option_and_scope(
    hass,
    include_nutrition: bool,
    scopes: tuple[str, ...],
) -> None:
    """Stored nutrition remains unavailable unless both consent gates are active."""
    _register_integration(hass)
    entry = _entry(
        hass,
        person_slug="sample_alpha",
        include_nutrition=include_nutrition,
        scopes=scopes,
    )
    row = _summary(
        date(2042, 7, 13),
        steps=6200,
        nutrition_energy_kcal=1830.0,
        hydration_ml=2150.0,
    )
    history = MagicMock()
    history.async_load = AsyncMock(return_value=[row])
    history.async_query = AsyncMock(return_value=[row])
    await _setup_person(hass, entry, history, _coordinator(history))
    connection = _Connection()

    await _async_handle_history(
        hass,
        connection,
        {
            "id": 14,
            "type": "resiyhome_health_sync/history",
            "person": "sample_alpha",
            "start_date": "2042-07-13",
            "end_date": "2042-07-13",
            "metrics": ["nutrition_energy_kcal", "hydration_ml"],
        },
    )

    assert connection.error is None
    assert connection.result is not None
    assert connection.result["result"]["records"] == [
        {
            "date": "2042-07-13",
            "nutrition_energy_kcal": None,
            "hydration_ml": None,
        }
    ]


async def test_history_optional_unavailable_values_remain_none(hass) -> None:
    """Missing normalized values are never inferred as zero by history output."""
    _register_integration(hass)
    entry = _entry(
        hass,
        person_slug="sample_alpha",
        include_body_measurements=True,
        include_nutrition=True,
        scopes=(*SCOPES, NUTRITION_SCOPE),
    )
    row = _summary(date(2042, 7, 13), steps=6200)
    history = MagicMock()
    history.async_load = AsyncMock(return_value=[row])
    history.async_query = AsyncMock(return_value=[row])
    await _setup_person(hass, entry, history, _coordinator(history))
    connection = _Connection()
    metrics = [
        "total_energy_kcal",
        "nutrition_energy_kcal",
        "hydration_ml",
        "sleep_period_minutes",
        "sleep_onset_minutes",
        "sleep_after_wake_minutes",
        "body_fat_percentage",
        "height_m",
    ]

    await _async_handle_history(
        hass,
        connection,
        {
            "id": 15,
            "type": "resiyhome_health_sync/history",
            "person": "sample_alpha",
            "start_date": "2042-07-13",
            "end_date": "2042-07-13",
            "metrics": metrics,
        },
    )

    assert connection.error is None
    assert connection.result is not None
    assert connection.result["result"]["records"] == [
        {"date": "2042-07-13", **dict.fromkeys(metrics)}
    ]


async def test_expanded_history_accepts_exactly_ninety_inclusive_days(hass) -> None:
    """The documented 90-day limit counts both requested date boundaries."""
    _register_integration(hass)
    entry = _entry(hass, person_slug="sample_alpha")
    row = _summary(date(2042, 7, 13), steps=6200)
    history = MagicMock()
    history.backfill_cursor = date(2042, 7, 1)
    history.async_load = AsyncMock(return_value=[row])
    history.async_query = AsyncMock(return_value=[row])
    await _setup_person(hass, entry, history, _coordinator(history))
    connection = _Connection()

    await _async_handle_history(
        hass,
        connection,
        {
            "id": 12,
            "type": "resiyhome_health_sync/history",
            "person": "sample_alpha",
            "start_date": "2042-04-15",
            "end_date": "2042-07-13",
            "metrics": ["vo2_max"],
        },
    )

    assert connection.error is None
    history.async_query.assert_awaited_once_with(date(2042, 4, 15), date(2042, 7, 13))


async def test_history_websocket_preserves_long_range_core_defaults(hass) -> None:
    _register_integration(hass)
    sample_alpha = _entry(hass, person_slug="sample_alpha")
    row = _summary(date(2042, 7, 13), steps=6200)
    history = MagicMock()
    history.backfill_cursor = date(2042, 7, 1)
    history.async_load = AsyncMock(return_value=[row])
    history.async_query = AsyncMock(return_value=[row])
    await _setup_person(hass, sample_alpha, history, _coordinator(history))

    connection = _Connection()
    await _async_handle_history(
        hass,
        connection,
        {
            "id": 9,
            "type": "resiyhome_health_sync/history",
            "person": "sample_alpha",
            "start_date": "2042-04-14",
            "end_date": "2042-07-13",
        },
    )

    assert connection.error is None
    assert connection.result is not None
    record = connection.result["result"]["records"][0]
    assert set(record) == {
        "date",
        "steps",
        "fitbit_steps",
        "distance_m",
        "active_energy_kcal",
        "exercise_minutes",
        "sleep_minutes",
        "resting_heart_rate",
        "average_heart_rate",
        "minimum_heart_rate",
        "maximum_heart_rate",
        "hrv_ms",
        "total_energy_kcal",
        "sleep_period_minutes",
        "sleep_onset_minutes",
        "sleep_after_wake_minutes",
        "source",
        "complete",
    }
    history.async_query.assert_awaited_once_with(date(2042, 4, 14), date(2042, 7, 13))


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (
            {"person": "sample_beta", "start_date": "2042-07-12", "end_date": "2042-07-13"},
            "not_found",
        ),
        (
            {"person": "sample_alpha", "start_date": "bad", "end_date": "2042-07-13"},
            "invalid_format",
        ),
        (
            {"person": "sample_alpha", "start_date": "20260712", "end_date": "2042-07-13"},
            "invalid_format",
        ),
        (
            {"person": "sample_alpha", "start_date": "2042-07-14", "end_date": "2042-07-13"},
            "invalid_range",
        ),
        (
            {"person": "sample_alpha", "start_date": "2000-01-01", "end_date": "2042-07-13"},
            "range_too_large",
        ),
        (
            {
                "person": "sample_alpha",
                "start_date": "2042-07-12",
                "end_date": "2042-07-13",
                "metrics": ["steps", "raw_points"],
            },
            "invalid_metric",
        ),
        (
            {
                "person": "sample_alpha",
                "start_date": "2042-07-12",
                "end_date": "2042-07-13",
                "metrics": ["paired_devices"],
            },
            "invalid_metric",
        ),
        (
            {
                "person": "sample_alpha",
                "start_date": "2042-04-14",
                "end_date": "2042-07-13",
                "metrics": ["vo2_max"],
            },
            "range_too_large",
        ),
    ],
)
async def test_history_websocket_validates_person_dates_and_metrics(
    hass, payload, code: str
) -> None:
    _register_integration(hass)
    sample_alpha = _entry(hass, person_slug="sample_alpha")
    history = MagicMock()
    history.backfill_cursor = date(2042, 7, 1)
    history.async_load = AsyncMock(return_value=[])
    history.async_query = AsyncMock(return_value=[])
    coordinator = _coordinator(history)
    await _setup_person(hass, sample_alpha, history, coordinator)

    connection = _Connection()
    await _async_handle_history(
        hass,
        connection,
        {"id": 11, "type": "resiyhome_health_sync/history", **payload},
    )

    assert connection.result is None
    assert connection.error is not None
    assert connection.error["success"] is False
    assert connection.error["error"]["code"] == code
    history.async_query.assert_not_awaited()


async def test_history_websocket_registration_is_removed_after_final_entry_unloads(hass) -> None:
    """The shared history command remains only while at least one person is loaded."""
    _register_integration(hass)
    sample_alpha = _entry(hass, person_slug="sample_alpha")
    sample_beta = _entry(hass, person_name="Sample Beta", person_slug="sample_beta")
    history_one = MagicMock()
    history_one.async_load = AsyncMock(return_value=[])
    history_one.async_shutdown = AsyncMock()
    history_two = MagicMock()
    history_two.async_load = AsyncMock(return_value=[])
    history_two.async_shutdown = AsyncMock()
    coordinator_one = _coordinator(history_one)
    coordinator_two = _coordinator(history_two)

    with (
        patch(
            "custom_components.resiyhome_health_sync.GoogleHealthClient", return_value=MagicMock()
        ),
        patch(
            "custom_components.resiyhome_health_sync.HealthHistoryStore",
            side_effect=[history_one, history_two],
        ),
        patch(
            "custom_components.resiyhome_health_sync.HealthSyncCoordinator",
            side_effect=[coordinator_one, coordinator_two],
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups", new=AsyncMock()),
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=True),
        ),
    ):
        assert await async_setup_entry(hass, sample_alpha)
        assert await async_setup_entry(hass, sample_beta)
        assert _COMMAND in hass.data[websocket_api.DOMAIN]

        assert await async_unload_entry(hass, sample_alpha)
        assert _COMMAND in hass.data[websocket_api.DOMAIN]

        assert await async_unload_entry(hass, sample_beta)
        assert _COMMAND not in hass.data[websocket_api.DOMAIN]
