"""Entity contracts for the Health Sync integration."""

import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.sensor import (
    ATTR_STATE_CLASS,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.components.sensor.recorder import reset_detected
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfTime,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_integration,
    mock_platform,
)

from custom_components.resiyhome_health_sync import (
    HealthSyncRuntimeData,
    async_setup_entry,
    async_unload_entry,
    binary_sensor,
    config_flow,
    sensor,
)
from custom_components.resiyhome_health_sync.const import DOMAIN, SCOPES
from custom_components.resiyhome_health_sync.coordinator import HealthSyncCoordinator
from custom_components.resiyhome_health_sync.models import (
    CoordinatorSnapshot,
    DailySummary,
    ExpandedDailyMetrics,
    SourceKind,
    WorkoutSummary,
)

NOW = datetime(2042, 7, 13, 12, 0, tzinfo=UTC)
FORBIDDEN_ATTRIBUTE_FRAGMENTS = {
    "access_token",
    "authorization_code",
    "client_id",
    "client_secret",
    "data_points",
    "google_user",
    "raw",
    "refresh_token",
}


def _state(hass, entity_id: str):
    state = hass.states.get(entity_id)
    assert state is not None
    return state


def _summary(
    *,
    day: date = date(2042, 7, 13),
    steps: int | None = 6200,
    sleep_minutes: float | None = 435.0,
    expanded: ExpandedDailyMetrics | None = None,
) -> DailySummary:
    return DailySummary(
        date=day,
        steps=steps,
        fitbit_steps=5800,
        distance_m=4812.5,
        active_energy_kcal=512.5,
        exercise_minutes=45.0,
        sleep_minutes=sleep_minutes,
        sleep_stages={"awake": 38.0, "rem": 92.0, "light": 231.0, "deep": 74.0},
        resting_heart_rate=54.0,
        average_heart_rate=76.5,
        minimum_heart_rate=48.0,
        maximum_heart_rate=151.0,
        hrv_ms=43.2,
        workouts=(
            WorkoutSummary(
                activity_type="running",
                duration_minutes=31.5,
                start=datetime(2042, 7, 13, 10, 0, tzinfo=UTC),
                end=datetime(2042, 7, 13, 10, 31, 30, tzinfo=UTC),
                active_energy_kcal=220.0,
            ),
        ),
        expanded=expanded or ExpandedDailyMetrics(),
        source=SourceKind.MIXED,
        complete=False,
        updated_at=NOW,
    )


def _expanded_metrics() -> ExpandedDailyMetrics:
    return ExpandedDailyMetrics(
        active_zone_minutes={"fat_burn": 12.0, "cardio": 8.0, "peak": 4.0},
        vo2_max=42.5,
        vo2_estimated=False,
        cardio_fitness_level="GOOD",
        oxygen_average=96.2,
        oxygen_lower_bound=95.1,
        oxygen_upper_bound=97.3,
        oxygen_standard_deviation=0.4,
        daily_respiratory_rate=15.4,
        sleep_respiratory_rates={"full": 14.8, "deep": 14.1, "light": 15.2, "rem": 14.6},
        sleep_respiratory_standard_deviation=0.7,
        sleep_respiratory_signal_to_noise=3.2,
        floors=7,
        sedentary_minutes=480.0,
        heart_zone_minutes={"light": 20.0, "moderate": 15.0, "vigorous": 10.0, "peak": 5.0},
        heart_zone_thresholds={
            "light": (90, 109),
            "moderate": (110, 132),
            "vigorous": (133, 159),
            "peak": (160, 220),
        },
        heart_zone_calories={
            "light": 80.0,
            "moderate": 120.0,
            "vigorous": 184.2,
            "peak": 45.0,
        },
        weight_kg=80.5,
    )


def _entry(hass, person_name: str, person_slug: str) -> MockConfigEntry:
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
            "scopes": list(SCOPES),
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
    mock_platform(hass, f"{DOMAIN}.sensor", sensor, built_in=False)
    mock_platform(hass, f"{DOMAIN}.binary_sensor", binary_sensor, built_in=False)


async def _setup_person(
    hass,
    *,
    person_name: str = "Sample Alpha",
    person_slug: str = "sample_alpha",
    summary: DailySummary | None = None,
    now: datetime = NOW,
) -> tuple[MockConfigEntry, HealthSyncCoordinator, list[datetime]]:
    entry = _entry(hass, person_name, person_slug)
    clock = [now]
    history = MagicMock()
    history.backfill_cursor = date(2042, 7, 1)
    history.async_load = AsyncMock(return_value=[])
    coordinator = HealthSyncCoordinator(
        hass,
        MagicMock(),
        history,
        now=lambda: clock[0],
    )
    snapshot = CoordinatorSnapshot(
        current_day=summary if summary is not None else _summary(),
        last_success=now,
        last_attempt=now,
        authorization_healthy=True,
        backfill_cursor=date(2042, 7, 1),
        backfill_complete=True,
    )
    coordinator.data = snapshot
    coordinator.async_refresh_current = AsyncMock(return_value=snapshot)  # type: ignore[method-assign]

    with (
        patch(
            "custom_components.resiyhome_health_sync.GoogleHealthClient", return_value=MagicMock()
        ),
        patch("custom_components.resiyhome_health_sync.HealthHistoryStore", return_value=history),
        patch(
            "custom_components.resiyhome_health_sync.HealthSyncCoordinator",
            return_value=coordinator,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert isinstance(entry.runtime_data, HealthSyncRuntimeData)
    return entry, coordinator, clock


@pytest.fixture
async def sample_alpha(hass):
    _register_integration(hass)
    return await _setup_person(hass)


async def test_entities_have_stable_person_scoped_ids_and_device(hass, sample_alpha) -> None:
    entry, _coordinator, _clock = sample_alpha
    assert _state(hass, "sensor.sample_alpha_steps_today").state == "6200"
    assert _state(hass, "binary_sensor.sample_alpha_health_data_stale").state == STATE_OFF

    registry = er.async_get(hass)
    steps_entry = registry.async_get("sensor.sample_alpha_steps_today")
    stale_entry = registry.async_get("binary_sensor.sample_alpha_health_data_stale")
    assert steps_entry is not None
    assert stale_entry is not None
    assert registry.async_get("sensor.sample_alpha_steps_today").unique_id == (
        "sample_alpha_steps_today"
    )
    assert registry.async_get("binary_sensor.sample_alpha_health_data_stale").unique_id == (
        "sample_alpha_health_data_stale"
    )
    assert steps_entry.unique_id == "sample_alpha_steps_today"
    assert stale_entry.unique_id == "sample_alpha_health_data_stale"
    assert steps_entry.config_entry_id == entry.entry_id
    assert steps_entry.device_id == stale_entry.device_id
    device = dr.async_get(hass).async_get(steps_entry.device_id)
    assert device is not None
    assert device.manufacturer == "ResiyHome"


@pytest.mark.parametrize(
    ("entity_id", "unit", "device_class", "state_class"),
    [
        ("sensor.sample_alpha_steps_today", "steps", None, SensorStateClass.TOTAL_INCREASING),
        (
            "sensor.sample_alpha_fitbit_steps_today",
            "steps",
            None,
            SensorStateClass.TOTAL_INCREASING,
        ),
        (
            "sensor.sample_alpha_distance_today",
            UnitOfLength.METERS,
            SensorDeviceClass.DISTANCE,
            SensorStateClass.TOTAL_INCREASING,
        ),
        (
            "sensor.sample_alpha_active_energy_today",
            UnitOfEnergy.KILO_CALORIE,
            SensorDeviceClass.ENERGY,
            SensorStateClass.TOTAL_INCREASING,
        ),
        (
            "sensor.sample_alpha_exercise_minutes_today",
            UnitOfTime.MINUTES,
            SensorDeviceClass.DURATION,
            SensorStateClass.TOTAL_INCREASING,
        ),
        (
            "sensor.sample_alpha_last_sleep_duration",
            UnitOfTime.MINUTES,
            SensorDeviceClass.DURATION,
            SensorStateClass.MEASUREMENT,
        ),
        (
            "sensor.sample_alpha_average_heart_rate",
            "bpm",
            None,
            SensorStateClass.MEASUREMENT,
        ),
        (
            "sensor.sample_alpha_heart_rate_variability",
            UnitOfTime.MILLISECONDS,
            SensorDeviceClass.DURATION,
            SensorStateClass.MEASUREMENT,
        ),
    ],
)
async def test_statistics_units_and_device_classes(
    hass, sample_alpha, entity_id, unit, device_class, state_class
) -> None:
    state = _state(hass, entity_id)
    assert state.attributes.get(ATTR_UNIT_OF_MEASUREMENT) == unit
    assert state.attributes.get(ATTR_DEVICE_CLASS) == device_class
    assert state.attributes[ATTR_STATE_CLASS] == state_class


@pytest.mark.parametrize(
    ("entity_key", "state", "unit", "device_class", "state_class", "attributes"),
    [
        (
            "active_zone_minutes_today",
            "24.0",
            UnitOfTime.MINUTES,
            SensorDeviceClass.DURATION,
            SensorStateClass.TOTAL_INCREASING,
            {"fat_burn_minutes": 12.0, "cardio_minutes": 8.0, "peak_minutes": 4.0},
        ),
        (
            "daily_vo2_max",
            "42.5",
            "mL/kg/min",
            None,
            SensorStateClass.MEASUREMENT,
            {"fitness_level": "GOOD", "estimated": False},
        ),
        (
            "daily_oxygen_saturation",
            "96.2",
            "%",
            None,
            SensorStateClass.MEASUREMENT,
            {"lower_bound": 95.1, "upper_bound": 97.3, "standard_deviation": 0.4},
        ),
        (
            "daily_respiratory_rate",
            "15.4",
            "breaths/min",
            None,
            SensorStateClass.MEASUREMENT,
            {},
        ),
        (
            "sleep_respiratory_rate",
            "14.8",
            "breaths/min",
            None,
            SensorStateClass.MEASUREMENT,
            {"standard_deviation": 0.7, "signal_to_noise": 3.2},
        ),
        (
            "floors_today",
            "7",
            "floors",
            None,
            SensorStateClass.TOTAL_INCREASING,
            {},
        ),
        (
            "sedentary_minutes_today",
            "480.0",
            UnitOfTime.MINUTES,
            SensorDeviceClass.DURATION,
            SensorStateClass.TOTAL_INCREASING,
            {},
        ),
        (
            "heart_rate_zone_minutes_today",
            "50.0",
            UnitOfTime.MINUTES,
            SensorDeviceClass.DURATION,
            SensorStateClass.TOTAL_INCREASING,
            {
                "light_minutes": 20.0,
                "moderate_minutes": 15.0,
                "vigorous_minutes": 10.0,
                "peak_minutes": 5.0,
            },
        ),
    ],
)
async def test_enabled_expanded_entities_have_stable_contracts(
    hass, entity_key, state, unit, device_class, state_class, attributes
) -> None:
    _register_integration(hass)
    entry, _coordinator, _clock = await _setup_person(
        hass, summary=_summary(expanded=_expanded_metrics())
    )

    entity_state = _state(hass, f"sensor.sample_alpha_{entity_key}")
    registry_entry = er.async_get(hass).async_get(entity_state.entity_id)
    assert registry_entry is not None
    assert registry_entry.unique_id == f"sample_alpha_{entity_key}"
    assert registry_entry.config_entry_id == entry.entry_id
    assert entity_state.state == state
    assert entity_state.attributes[ATTR_UNIT_OF_MEASUREMENT] == unit
    assert entity_state.attributes.get(ATTR_DEVICE_CLASS) == device_class
    assert entity_state.attributes[ATTR_STATE_CLASS] == state_class
    metadata_keys = {
        ATTR_DEVICE_CLASS,
        ATTR_STATE_CLASS,
        ATTR_UNIT_OF_MEASUREMENT,
        "friendly_name",
        "icon",
    }
    assert {
        key: value for key, value in entity_state.attributes.items() if key not in metadata_keys
    } == attributes


async def test_expanded_entities_distinguish_missing_values_from_zero(hass) -> None:
    _register_integration(hass)
    _entry, coordinator, _clock = await _setup_person(
        hass, summary=_summary(expanded=ExpandedDailyMetrics())
    )
    enabled = (
        "active_zone_minutes_today",
        "daily_vo2_max",
        "daily_oxygen_saturation",
        "daily_respiratory_rate",
        "sleep_respiratory_rate",
        "floors_today",
        "sedentary_minutes_today",
        "heart_rate_zone_minutes_today",
    )
    for key in enabled:
        assert _state(hass, f"sensor.sample_alpha_{key}").state == STATE_UNAVAILABLE

    coordinator.async_set_updated_data(
        CoordinatorSnapshot(
            current_day=_summary(
                expanded=ExpandedDailyMetrics(
                    active_zone_minutes={"fat_burn": 0.0, "cardio": 0.0, "peak": 0.0},
                    vo2_max=0.0,
                    oxygen_average=0.0,
                    daily_respiratory_rate=0.0,
                    sleep_respiratory_rates={"full": 0.0},
                    floors=0,
                    sedentary_minutes=0.0,
                    heart_zone_minutes={
                        "light": 0.0,
                        "moderate": 0.0,
                        "vigorous": 0.0,
                        "peak": 0.0,
                    },
                )
            ),
            last_success=NOW,
            last_attempt=NOW,
            authorization_healthy=True,
            expanded_backfill_complete=True,
        )
    )
    await hass.async_block_till_done()

    for key in enabled:
        assert _state(hass, f"sensor.sample_alpha_{key}").state in {"0", "0.0"}


def test_detailed_expanded_entities_are_exactly_disabled_by_default() -> None:
    disabled_keys = {
        "active_zone_fat_burn_minutes_today",
        "active_zone_cardio_minutes_today",
        "active_zone_peak_minutes_today",
        "heart_rate_zone_light_minutes_today",
        "heart_rate_zone_moderate_minutes_today",
        "heart_rate_zone_vigorous_minutes_today",
        "heart_rate_zone_peak_minutes_today",
        "sleep_deep_respiratory_rate",
        "sleep_light_respiratory_rate",
        "sleep_rem_respiratory_rate",
        "heart_rate_zone_light_calories_today",
        "heart_rate_zone_moderate_calories_today",
        "heart_rate_zone_vigorous_calories_today",
        "heart_rate_zone_peak_calories_today",
        "weight",
    }
    description_keys = [description.key for description in sensor.SENSOR_DESCRIPTIONS]
    assert len(description_keys) == len(set(description_keys))
    descriptions = {description.key: description for description in sensor.SENSOR_DESCRIPTIONS}

    assert disabled_keys <= descriptions.keys()
    assert {
        key
        for key, description in descriptions.items()
        if not description.entity_registry_enabled_default
    } == disabled_keys

    for zone in ("light", "moderate", "vigorous", "peak"):
        description = descriptions[f"heart_rate_zone_{zone}_minutes_today"]
        snapshot = CoordinatorSnapshot(current_day=_summary(expanded=_expanded_metrics()))
        assert description.value_fn(snapshot) == _expanded_metrics().heart_zone_minutes[zone]
        assert description.attributes_fn(snapshot) == {
            "minimum_bpm": _expanded_metrics().heart_zone_thresholds[zone][0],
            "maximum_bpm": _expanded_metrics().heart_zone_thresholds[zone][1],
        }


def test_weight_requires_body_measurement_opt_in_and_uses_latest_snapshot() -> None:
    description = next(
        description for description in sensor.SENSOR_DESCRIPTIONS if description.key == "weight"
    )
    coordinator = MagicMock()
    coordinator.data = CoordinatorSnapshot(
        current_day=_summary(expanded=ExpandedDailyMetrics(weight_kg=79.0)),
        latest_weight_kg=80.5,
        latest_weight_at=date(2042, 7, 12),
    )
    entry = MagicMock()
    entry.title = "Sample Alpha"
    entry.data = {"person_slug": "sample_alpha"}
    entry.options = {"include_body_measurements": False}

    disabled = sensor.HealthSyncSensor(entry, coordinator, description)
    assert disabled.unique_id == "sample_alpha_weight"
    assert disabled.native_value is None
    assert disabled.extra_state_attributes is None

    entry.options = {"include_body_measurements": True}
    enabled = sensor.HealthSyncSensor(entry, coordinator, description)
    assert enabled.native_value == 80.5
    assert enabled.extra_state_attributes == {"measurement_date": "2042-07-12"}


async def test_daily_total_midnight_reset_is_valid_total_increasing_cycle(
    hass, sample_alpha
) -> None:
    _entry, coordinator, clock = sample_alpha
    previous = _state(hass, "sensor.sample_alpha_steps_today")

    clock[0] = NOW + timedelta(hours=12)
    coordinator.async_set_updated_data(
        CoordinatorSnapshot(
            current_day=_summary(day=date(2042, 7, 14), steps=300),
            last_success=clock[0],
            last_attempt=clock[0],
            authorization_healthy=True,
            backfill_complete=True,
        )
    )
    await hass.async_block_till_done()

    current = _state(hass, "sensor.sample_alpha_steps_today")
    assert current.state == "300"
    assert current.attributes[ATTR_STATE_CLASS] == SensorStateClass.TOTAL_INCREASING
    assert reset_detected(hass, current.entity_id, 300.0, 6200.0, current) is True
    assert previous.attributes[ATTR_STATE_CLASS] == SensorStateClass.TOTAL_INCREASING


async def test_total_increasing_is_limited_to_daily_cumulative_metrics(hass, sample_alpha) -> None:
    expected_totals = {
        "sensor.sample_alpha_active_energy_today",
        "sensor.sample_alpha_active_zone_minutes_today",
        "sensor.sample_alpha_distance_today",
        "sensor.sample_alpha_exercise_minutes_today",
        "sensor.sample_alpha_fitbit_steps_today",
        "sensor.sample_alpha_floors_today",
        "sensor.sample_alpha_heart_rate_zone_minutes_today",
        "sensor.sample_alpha_sedentary_minutes_today",
        "sensor.sample_alpha_steps_today",
    }
    actual_totals = {
        state.entity_id
        for state in hass.states.async_all("sensor")
        if state.entity_id.startswith("sensor.sample_alpha_")
        and state.attributes.get(ATTR_STATE_CLASS) == SensorStateClass.TOTAL_INCREASING
    }
    assert actual_totals == expected_totals


async def test_missing_metric_is_unavailable_not_zero(hass) -> None:
    _register_integration(hass)
    await _setup_person(hass, summary=_summary(sleep_minutes=None))
    assert _state(hass, "sensor.sample_alpha_last_sleep_duration").state == STATE_UNAVAILABLE
    assert _state(hass, "sensor.sample_alpha_steps_today").state == "6200"


async def test_explicit_zero_remains_available(hass) -> None:
    _register_integration(hass)
    await _setup_person(hass, summary=_summary(steps=0))
    assert _state(hass, "sensor.sample_alpha_steps_today").state == "0"


async def test_snapshot_and_timestamp_entities_have_exact_semantics(hass, sample_alpha) -> None:
    assert _state(hass, "sensor.sample_alpha_last_workout_type").state == "running"
    assert _state(hass, "sensor.sample_alpha_last_workout_duration").state == "31.5"
    sync = _state(hass, "sensor.sample_alpha_last_successful_synchronization")
    assert sync.attributes[ATTR_DEVICE_CLASS] == SensorDeviceClass.TIMESTAMP
    assert datetime.fromisoformat(sync.state).tzinfo is not None
    assert _state(hass, "sensor.sample_alpha_current_source").state == SourceKind.MIXED
    assert _state(hass, "sensor.sample_alpha_backfill_status").state == "complete"


async def test_stale_boundary_is_strictly_more_than_45_minutes(hass, sample_alpha) -> None:
    _entry, coordinator, clock = sample_alpha
    clock[0] = NOW + timedelta(minutes=45)
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()
    assert _state(hass, "binary_sensor.sample_alpha_health_data_stale").state == STATE_OFF

    clock[0] += timedelta(seconds=1)
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()
    assert _state(hass, "binary_sensor.sample_alpha_health_data_stale").state == STATE_ON


async def test_never_synchronized_is_stale(hass, sample_alpha) -> None:
    _entry, coordinator, _clock = sample_alpha
    coordinator.data.last_success = None
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()
    assert _state(hass, "binary_sensor.sample_alpha_health_data_stale").state == STATE_ON


async def test_authorization_problem_distinguishes_auth_from_transient_failure(
    hass, sample_alpha
) -> None:
    _entry, coordinator, _clock = sample_alpha
    coordinator.data.current_day = _summary(sleep_minutes=None)
    coordinator.last_update_success = False
    coordinator.data.authorization_healthy = True
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert (
        _state(hass, "binary_sensor.sample_alpha_health_authorization_problem").state == STATE_OFF
    )
    assert _state(hass, "sensor.sample_alpha_steps_today").state == "6200"
    assert _state(hass, "sensor.sample_alpha_last_sleep_duration").state == STATE_UNAVAILABLE

    coordinator.data.authorization_healthy = False
    coordinator.async_update_listeners()
    await hass.async_block_till_done()
    assert _state(hass, "binary_sensor.sample_alpha_health_authorization_problem").state == STATE_ON


async def test_multiple_entries_remain_independent(hass) -> None:
    _register_integration(hass)
    sample_alpha_entry, sample_alpha_coordinator, _ = await _setup_person(hass)
    sample_beta_entry, sample_beta_coordinator, _ = await _setup_person(
        hass,
        person_name="Sample Beta",
        person_slug="sample_beta",
        summary=_summary(steps=9100),
    )

    assert _state(hass, "sensor.sample_alpha_steps_today").state == "6200"
    assert _state(hass, "sensor.sample_beta_steps_today").state == "9100"
    assert sample_alpha_entry.entry_id != sample_beta_entry.entry_id
    assert sample_alpha_coordinator is not sample_beta_coordinator
    registry = er.async_get(hass)
    assert (
        registry.async_get("sensor.sample_alpha_steps_today").unique_id
        == "sample_alpha_steps_today"
    )  # type: ignore[union-attr]
    assert (
        registry.async_get("sensor.sample_beta_steps_today").unique_id == "sample_beta_steps_today"
    )  # type: ignore[union-attr]


async def test_entity_ids_survive_display_name_change_and_reload(hass, sample_alpha) -> None:
    entry, coordinator, _clock = sample_alpha
    runtime = entry.runtime_data
    hass.config_entries.async_update_entry(entry, title="Person Three")

    with (
        patch(
            "custom_components.resiyhome_health_sync.GoogleHealthClient",
            return_value=runtime.client,
        ),
        patch(
            "custom_components.resiyhome_health_sync.HealthHistoryStore",
            return_value=runtime.history,
        ),
        patch(
            "custom_components.resiyhome_health_sync.HealthSyncCoordinator",
            return_value=coordinator,
        ),
    ):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()

    registry = er.async_get(hass)
    steps_entry = registry.async_get("sensor.sample_alpha_steps_today")
    stale_entry = registry.async_get("binary_sensor.sample_alpha_health_data_stale")
    assert steps_entry is not None
    assert stale_entry is not None
    assert steps_entry.unique_id == "sample_alpha_steps_today"
    assert stale_entry.unique_id == "sample_alpha_health_data_stale"


def _walk(value: Any, visit: Callable[[str], None]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            visit(str(key))
            _walk(nested, visit)
    elif isinstance(value, list | tuple | set):
        for nested in value:
            _walk(nested, visit)
    else:
        visit(str(value))


async def test_attributes_are_recursively_redacted_and_allowlisted(hass, sample_alpha) -> None:
    entry, _coordinator, _clock = sample_alpha
    secret_values = {
        str(entry.data[field]).lower()
        for field in ("access_token", "client_id", "client_secret", "refresh_token")
    }
    for state in hass.states.async_all():
        if not state.entity_id.startswith(("sensor.sample_alpha_", "binary_sensor.sample_alpha_")):
            continue
        serialized = json.dumps(state.attributes, default=str).lower()
        assert not any(fragment in serialized for fragment in FORBIDDEN_ATTRIBUTE_FRAGMENTS)
        assert not any(secret in serialized for secret in secret_values)
        _walk(
            state.attributes,
            lambda item: (
                pytest.fail(f"credential material in attributes: {item}")
                if any(fragment in item.lower() for fragment in FORBIDDEN_ATTRIBUTE_FRAGMENTS)
                else None
            ),
        )

        integration_attributes = {
            key
            for key in state.attributes
            if key
            not in {
                "device_class",
                "entity_category",
                "friendly_name",
                "icon",
                "options",
                "state_class",
                "unit_of_measurement",
            }
        }
        assert integration_attributes <= {
            "complete",
            "data_updated_at",
            "last_attempt",
            "last_success",
            "source",
            "summary_date",
        }
