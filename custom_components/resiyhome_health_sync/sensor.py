"""Sensor entities for normalized Health Sync data."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import cast, override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.components.sensor.const import DOMAIN as SENSOR_DOMAIN
from homeassistant.const import (
    PERCENTAGE,
    UnitOfEnergy,
    UnitOfLength,
    UnitOfMass,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HealthSyncConfigEntry
from .capabilities import CapabilityId
from .const import DOMAIN
from .coordinator import HealthSyncCoordinator
from .models import CoordinatorSnapshot, DailySummary, PairedDeviceSummary, SourceKind

type SensorValue = date | datetime | float | int | str | None
type ValueFunction = Callable[[CoordinatorSnapshot], SensorValue]
type AttributeValue = bool | float | int | str
type AttributesFunction = Callable[[CoordinatorSnapshot], dict[str, AttributeValue] | None]


@dataclass(frozen=True, kw_only=True)
class HealthSyncSensorEntityDescription(SensorEntityDescription):
    """Describe one normalized health sensor."""

    value_fn: ValueFunction
    attributes_fn: AttributesFunction | None = None
    summary_metadata: bool = False
    required_capability: CapabilityId | None = None


def _summary_value(field: str) -> ValueFunction:
    def value(snapshot: CoordinatorSnapshot) -> SensorValue:
        summary = snapshot.current_day
        return cast(SensorValue, getattr(summary, field)) if summary is not None else None

    return value


def _sleep_stage(stage: str) -> ValueFunction:
    def value(snapshot: CoordinatorSnapshot) -> SensorValue:
        summary = snapshot.current_day
        return summary.sleep_stages.get(stage) if summary is not None else None

    return value


def _expanded_value(field: str) -> ValueFunction:
    def value(snapshot: CoordinatorSnapshot) -> SensorValue:
        summary = snapshot.current_day
        return cast(SensorValue, getattr(summary.expanded, field)) if summary is not None else None

    return value


def _expanded_mapping_value(field: str, key: str) -> ValueFunction:
    def value(snapshot: CoordinatorSnapshot) -> SensorValue:
        summary = snapshot.current_day
        if summary is None:
            return None
        values = cast(Mapping[str, float], getattr(summary.expanded, field))
        return values.get(key)

    return value


def _expanded_mapping_total(field: str) -> ValueFunction:
    def value(snapshot: CoordinatorSnapshot) -> SensorValue:
        summary = snapshot.current_day
        if summary is None:
            return None
        values = cast(Mapping[str, float], getattr(summary.expanded, field))
        return sum(values.values()) if values else None

    return value


def _expanded_attributes(**fields: str) -> AttributesFunction:
    def attributes(snapshot: CoordinatorSnapshot) -> dict[str, AttributeValue] | None:
        summary = snapshot.current_day
        if summary is None:
            return None
        result = {
            attribute: cast(AttributeValue, value)
            for attribute, field in fields.items()
            if (value := getattr(summary.expanded, field)) is not None
        }
        return result or None

    return attributes


def _expanded_mapping_attributes(field: str, **keys: str) -> AttributesFunction:
    def attributes(snapshot: CoordinatorSnapshot) -> dict[str, AttributeValue] | None:
        summary = snapshot.current_day
        if summary is None:
            return None
        values = cast(Mapping[str, AttributeValue], getattr(summary.expanded, field))
        result = {attribute: values[key] for attribute, key in keys.items() if key in values}
        return result or None

    return attributes


def _heart_zone_threshold_attributes(zone: str) -> AttributesFunction:
    def attributes(snapshot: CoordinatorSnapshot) -> dict[str, AttributeValue] | None:
        summary = snapshot.current_day
        if summary is None:
            return None
        thresholds = summary.expanded.heart_zone_thresholds.get(zone)
        if thresholds is None:
            return None
        return {"minimum_bpm": thresholds[0], "maximum_bpm": thresholds[1]}

    return attributes


def _measurement_date(field: str) -> AttributesFunction:
    def attributes(snapshot: CoordinatorSnapshot) -> dict[str, AttributeValue] | None:
        measured_at = cast(date | None, getattr(snapshot, field))
        return {"measurement_date": measured_at.isoformat()} if measured_at is not None else None

    return attributes


def _last_workout_field(field: str) -> ValueFunction:
    def value(snapshot: CoordinatorSnapshot) -> SensorValue:
        summary = snapshot.current_day
        if summary is None or not summary.workouts:
            return None
        return cast(SensorValue, getattr(summary.workouts[-1], field))

    return value


_DAILY_TOTALS = SensorStateClass.TOTAL_INCREASING
_MEASUREMENT = SensorStateClass.MEASUREMENT

SENSOR_DESCRIPTIONS: tuple[HealthSyncSensorEntityDescription, ...] = (
    HealthSyncSensorEntityDescription(
        key="steps_today",
        name="Steps today",
        icon="mdi:shoe-print",
        native_unit_of_measurement="steps",
        state_class=_DAILY_TOTALS,
        suggested_display_precision=0,
        value_fn=_summary_value("steps"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="fitbit_steps_today",
        name="Fitbit steps today",
        icon="mdi:watch-variant",
        native_unit_of_measurement="steps",
        state_class=_DAILY_TOTALS,
        suggested_display_precision=0,
        value_fn=_summary_value("fitbit_steps"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="distance_today",
        name="Distance today",
        icon="mdi:map-marker-distance",
        native_unit_of_measurement=UnitOfLength.METERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=_DAILY_TOTALS,
        suggested_display_precision=1,
        value_fn=_summary_value("distance_m"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="active_energy_today",
        name="Active energy today",
        icon="mdi:fire",
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        device_class=SensorDeviceClass.ENERGY,
        state_class=_DAILY_TOTALS,
        suggested_display_precision=1,
        value_fn=_summary_value("active_energy_kcal"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="total_calories_burned_today",
        name="Total calories burned today",
        translation_key="total_calories_burned_today",
        icon="mdi:fire",
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        device_class=SensorDeviceClass.ENERGY,
        state_class=_DAILY_TOTALS,
        suggested_display_precision=1,
        value_fn=_summary_value("total_energy_kcal"),
        summary_metadata=True,
        required_capability=CapabilityId.CORE_ACTIVITY,
    ),
    HealthSyncSensorEntityDescription(
        key="calories_consumed_today",
        name="Calories consumed today",
        translation_key="calories_consumed_today",
        icon="mdi:food-apple-outline",
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        device_class=SensorDeviceClass.ENERGY,
        state_class=_DAILY_TOTALS,
        suggested_display_precision=1,
        value_fn=_summary_value("nutrition_energy_kcal"),
        summary_metadata=True,
        required_capability=CapabilityId.NUTRITION,
    ),
    HealthSyncSensorEntityDescription(
        key="water_consumed_today",
        name="Water consumed today",
        translation_key="water_consumed_today",
        icon="mdi:cup-water",
        native_unit_of_measurement=UnitOfVolume.MILLILITERS,
        device_class=SensorDeviceClass.VOLUME,
        state_class=_DAILY_TOTALS,
        suggested_display_precision=1,
        value_fn=_summary_value("hydration_ml"),
        summary_metadata=True,
        required_capability=CapabilityId.NUTRITION,
    ),
    HealthSyncSensorEntityDescription(
        key="exercise_minutes_today",
        name="Exercise minutes today",
        icon="mdi:run",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_DAILY_TOTALS,
        suggested_display_precision=1,
        value_fn=_summary_value("exercise_minutes"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="last_sleep_duration",
        name="Last sleep duration",
        icon="mdi:sleep",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_summary_value("sleep_minutes"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="sleep_time_in_bed",
        name="Sleep time in bed",
        translation_key="sleep_time_in_bed",
        icon="mdi:bed-clock",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_summary_value("sleep_period_minutes"),
        summary_metadata=True,
        required_capability=CapabilityId.SLEEP,
    ),
    HealthSyncSensorEntityDescription(
        key="sleep_time_to_fall_asleep",
        name="Sleep time to fall asleep",
        translation_key="sleep_time_to_fall_asleep",
        icon="mdi:timer-sand",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_summary_value("sleep_onset_minutes"),
        summary_metadata=True,
        required_capability=CapabilityId.SLEEP,
    ),
    HealthSyncSensorEntityDescription(
        key="sleep_time_after_waking",
        name="Sleep time after waking",
        translation_key="sleep_time_after_waking",
        icon="mdi:alarm",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_summary_value("sleep_after_wake_minutes"),
        summary_metadata=True,
        required_capability=CapabilityId.SLEEP,
    ),
    HealthSyncSensorEntityDescription(
        key="sleep_awake_duration",
        name="Sleep awake duration",
        icon="mdi:eye-outline",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_sleep_stage("awake"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="sleep_rem_duration",
        name="Sleep REM duration",
        icon="mdi:head-cog-outline",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_sleep_stage("rem"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="sleep_light_duration",
        name="Sleep light duration",
        icon="mdi:weather-night-partly-cloudy",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_sleep_stage("light"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="sleep_deep_duration",
        name="Sleep deep duration",
        icon="mdi:weather-night",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_sleep_stage("deep"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="resting_heart_rate",
        name="Resting heart rate",
        icon="mdi:heart",
        native_unit_of_measurement="bpm",
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_summary_value("resting_heart_rate"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="average_heart_rate",
        name="Average heart rate",
        icon="mdi:heart-pulse",
        native_unit_of_measurement="bpm",
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_summary_value("average_heart_rate"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="minimum_heart_rate",
        name="Minimum heart rate",
        icon="mdi:heart-minus-outline",
        native_unit_of_measurement="bpm",
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_summary_value("minimum_heart_rate"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="maximum_heart_rate",
        name="Maximum heart rate",
        icon="mdi:heart-plus-outline",
        native_unit_of_measurement="bpm",
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_summary_value("maximum_heart_rate"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="heart_rate_variability",
        name="Heart rate variability",
        icon="mdi:heart-flash",
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_summary_value("hrv_ms"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="active_zone_minutes_today",
        name="Active zone minutes today",
        icon="mdi:heart-circle-outline",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_DAILY_TOTALS,
        suggested_display_precision=1,
        value_fn=_expanded_mapping_total("active_zone_minutes"),
        attributes_fn=_expanded_mapping_attributes(
            "active_zone_minutes",
            fat_burn_minutes="fat_burn",
            cardio_minutes="cardio",
            peak_minutes="peak",
        ),
    ),
    HealthSyncSensorEntityDescription(
        key="daily_vo2_max",
        name="Daily VO2 max",
        icon="mdi:lungs",
        native_unit_of_measurement="mL/kg/min",
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_expanded_value("vo2_max"),
        attributes_fn=_expanded_attributes(
            fitness_level="cardio_fitness_level",
            estimated="vo2_estimated",
        ),
    ),
    HealthSyncSensorEntityDescription(
        key="daily_oxygen_saturation",
        name="Daily oxygen saturation",
        icon="mdi:percent-circle-outline",
        native_unit_of_measurement="%",
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_expanded_value("oxygen_average"),
        attributes_fn=_expanded_attributes(
            lower_bound="oxygen_lower_bound",
            upper_bound="oxygen_upper_bound",
            standard_deviation="oxygen_standard_deviation",
        ),
    ),
    HealthSyncSensorEntityDescription(
        key="daily_respiratory_rate",
        name="Daily respiratory rate",
        icon="mdi:lungs",
        native_unit_of_measurement="breaths/min",
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_expanded_value("daily_respiratory_rate"),
    ),
    HealthSyncSensorEntityDescription(
        key="sleep_respiratory_rate",
        name="Sleep respiratory rate",
        icon="mdi:sleep",
        native_unit_of_measurement="breaths/min",
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_expanded_mapping_value("sleep_respiratory_rates", "full"),
        attributes_fn=_expanded_attributes(
            standard_deviation="sleep_respiratory_standard_deviation",
            signal_to_noise="sleep_respiratory_signal_to_noise",
        ),
    ),
    HealthSyncSensorEntityDescription(
        key="floors_today",
        name="Floors today",
        icon="mdi:stairs-up",
        native_unit_of_measurement="floors",
        state_class=_DAILY_TOTALS,
        suggested_display_precision=0,
        value_fn=_expanded_value("floors"),
    ),
    HealthSyncSensorEntityDescription(
        key="sedentary_minutes_today",
        name="Sedentary minutes today",
        icon="mdi:seat-outline",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_DAILY_TOTALS,
        suggested_display_precision=1,
        value_fn=_expanded_value("sedentary_minutes"),
    ),
    HealthSyncSensorEntityDescription(
        key="heart_rate_zone_minutes_today",
        name="Heart rate zone minutes today",
        icon="mdi:heart-pulse",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_DAILY_TOTALS,
        suggested_display_precision=1,
        value_fn=_expanded_mapping_total("heart_zone_minutes"),
        attributes_fn=_expanded_mapping_attributes(
            "heart_zone_minutes",
            light_minutes="light",
            moderate_minutes="moderate",
            vigorous_minutes="vigorous",
            peak_minutes="peak",
        ),
    ),
    *(
        HealthSyncSensorEntityDescription(
            key=f"active_zone_{zone}_minutes_today",
            name=f"Active zone {label} minutes today",
            icon="mdi:heart-circle-outline",
            native_unit_of_measurement=UnitOfTime.MINUTES,
            device_class=SensorDeviceClass.DURATION,
            state_class=_DAILY_TOTALS,
            suggested_display_precision=1,
            entity_registry_enabled_default=False,
            value_fn=_expanded_mapping_value("active_zone_minutes", zone),
        )
        for zone, label in (("fat_burn", "fat burn"), ("cardio", "cardio"), ("peak", "peak"))
    ),
    *(
        HealthSyncSensorEntityDescription(
            key=f"heart_rate_zone_{zone}_minutes_today",
            name=f"Heart rate zone {zone} minutes today",
            icon="mdi:heart-pulse",
            native_unit_of_measurement=UnitOfTime.MINUTES,
            device_class=SensorDeviceClass.DURATION,
            state_class=_DAILY_TOTALS,
            suggested_display_precision=1,
            entity_registry_enabled_default=False,
            value_fn=_expanded_mapping_value("heart_zone_minutes", zone),
            attributes_fn=_heart_zone_threshold_attributes(zone),
        )
        for zone in ("light", "moderate", "vigorous", "peak")
    ),
    *(
        HealthSyncSensorEntityDescription(
            key=f"sleep_{phase}_respiratory_rate",
            name=f"Sleep {phase} respiratory rate",
            icon="mdi:sleep",
            native_unit_of_measurement="breaths/min",
            state_class=_MEASUREMENT,
            suggested_display_precision=1,
            entity_registry_enabled_default=False,
            value_fn=_expanded_mapping_value("sleep_respiratory_rates", phase),
        )
        for phase in ("deep", "light", "rem")
    ),
    *(
        HealthSyncSensorEntityDescription(
            key=f"heart_rate_zone_{zone}_calories_today",
            name=f"Heart rate zone {zone} calories today",
            icon="mdi:fire",
            native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
            device_class=SensorDeviceClass.ENERGY,
            state_class=_DAILY_TOTALS,
            suggested_display_precision=1,
            entity_registry_enabled_default=False,
            value_fn=_expanded_mapping_value("heart_zone_calories", zone),
        )
        for zone in ("light", "moderate", "vigorous", "peak")
    ),
    HealthSyncSensorEntityDescription(
        key="weight",
        name="Weight",
        icon="mdi:scale-bathroom",
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        device_class=SensorDeviceClass.WEIGHT,
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda snapshot: snapshot.latest_weight_kg,
        attributes_fn=_measurement_date("latest_weight_at"),
        required_capability=CapabilityId.BODY_MEASUREMENTS,
    ),
    HealthSyncSensorEntityDescription(
        key="body_fat",
        name="Body fat",
        translation_key="body_fat",
        icon="mdi:percent-outline",
        native_unit_of_measurement=PERCENTAGE,
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        entity_registry_enabled_default=False,
        value_fn=lambda snapshot: snapshot.latest_body_fat_percentage,
        attributes_fn=_measurement_date("latest_body_fat_at"),
        required_capability=CapabilityId.BODY_MEASUREMENTS,
    ),
    HealthSyncSensorEntityDescription(
        key="height",
        name="Height",
        translation_key="height",
        icon="mdi:human-male-height",
        native_unit_of_measurement=UnitOfLength.METERS,
        device_class=SensorDeviceClass.DISTANCE,
        state_class=_MEASUREMENT,
        suggested_display_precision=3,
        entity_registry_enabled_default=False,
        value_fn=lambda snapshot: snapshot.latest_height_m,
        attributes_fn=_measurement_date("latest_height_at"),
        required_capability=CapabilityId.BODY_MEASUREMENTS,
    ),
    HealthSyncSensorEntityDescription(
        key="last_workout_type",
        name="Last workout type",
        icon="mdi:run-fast",
        value_fn=_last_workout_field("activity_type"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="last_workout_duration",
        name="Last workout duration",
        icon="mdi:timer-outline",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        state_class=_MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_last_workout_field("duration_minutes"),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="current_source",
        name="Current source",
        icon="mdi:source-branch",
        device_class=SensorDeviceClass.ENUM,
        options=[source.value for source in SourceKind],
        value_fn=lambda snapshot: (
            snapshot.current_day.source.value if snapshot.current_day is not None else None
        ),
        summary_metadata=True,
    ),
    HealthSyncSensorEntityDescription(
        key="last_successful_synchronization",
        name="Last successful synchronization",
        icon="mdi:cloud-check-outline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda snapshot: snapshot.last_success,
    ),
    HealthSyncSensorEntityDescription(
        key="backfill_status",
        name="Backfill status",
        icon="mdi:database-sync-outline",
        device_class=SensorDeviceClass.ENUM,
        options=["in_progress", "complete"],
        value_fn=lambda snapshot: "complete" if snapshot.backfill_complete else "in_progress",
    ),
    HealthSyncSensorEntityDescription(
        key="backfill_cursor",
        name="Backfill cursor",
        icon="mdi:calendar-arrow-left",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda snapshot: snapshot.backfill_cursor,
    ),
)

PAIRED_DEVICE_SENSOR_DESCRIPTIONS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="battery_level",
        translation_key="battery_level",
        icon="mdi:battery",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=_MEASUREMENT,
        suggested_display_precision=0,
    ),
    SensorEntityDescription(
        key="last_device_sync",
        translation_key="last_device_sync",
        icon="mdi:cellphone-sync",
        device_class=SensorDeviceClass.TIMESTAMP,
    ),
)


def _person_sensor_descriptions(
    hass: HomeAssistant,
    entry: HealthSyncConfigEntry,
) -> tuple[HealthSyncSensorEntityDescription, ...]:
    """Return sensors eligible for first registration or retained reload."""
    registry = er.async_get(hass)
    person_slug = str(entry.data["person_slug"])
    nutrition_available = (
        CapabilityId.NUTRITION in entry.runtime_data.scope_grant.available_capabilities
    )
    return tuple(
        description
        for description in SENSOR_DESCRIPTIONS
        if description.required_capability is not CapabilityId.NUTRITION
        or nutrition_available
        or registry.async_get_entity_id(
            SENSOR_DOMAIN,
            DOMAIN,
            f"{person_slug}_{description.key}",
        )
        is not None
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HealthSyncConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one person's normalized health sensors."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        HealthSyncSensor(entry, coordinator, description)
        for description in _person_sensor_descriptions(hass, entry)
    )
    person_slug = str(entry.data["person_slug"])
    added: set[tuple[str, str, str]] = set()

    @callback
    def _async_add_paired_device_entities() -> None:
        new_entities: list[HealthSyncPairedDeviceSensor] = []
        for device in coordinator.data.paired_devices:
            for description in PAIRED_DEVICE_SENSOR_DESCRIPTIONS:
                identity = (person_slug, device.identity_digest, description.key)
                if identity in added:
                    continue
                added.add(identity)
                new_entities.append(
                    HealthSyncPairedDeviceSensor(entry, coordinator, device, description)
                )
        if new_entities:
            async_add_entities(new_entities)

    _async_add_paired_device_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_add_paired_device_entities))


class HealthSyncSensor(CoordinatorEntity[HealthSyncCoordinator], SensorEntity):
    """Represent one normalized health value."""

    entity_description: HealthSyncSensorEntityDescription

    def __init__(
        self,
        entry: HealthSyncConfigEntry,
        coordinator: HealthSyncCoordinator,
        description: HealthSyncSensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._person_slug = str(entry.data["person_slug"])
        self._attr_name = f"{entry.title} {description.name}"
        self._attr_unique_id = f"{self._person_slug}_{description.key}"
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, self._person_slug)},
            name=entry.title,
            manufacturer="ResiyHome",
            model="Google Health",
        )

    @property
    @override
    def suggested_object_id(self) -> str:
        """Let Home Assistant prefix the immutable enrollment name once."""
        return self.entity_description.key

    @property
    @override
    def available(self) -> bool:
        """Mark missing metrics unavailable without hiding last-known data."""
        return self.native_value is not None

    @property
    @override
    def native_value(self) -> SensorValue:
        """Return the normalized value without substituting missing data."""
        if not self._capability_available:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, AttributeValue] | None:
        """Expose only explicitly allowlisted normalized metadata."""
        if not self._capability_available:
            return None
        attributes: dict[str, AttributeValue] = {}
        summary: DailySummary | None = self.coordinator.data.current_day
        if self.entity_description.summary_metadata and summary is not None:
            attributes.update(
                {
                    "source": summary.source.value,
                    "complete": summary.complete,
                    "summary_date": summary.date.isoformat(),
                }
            )
            if summary.updated_at is not None:
                attributes["data_updated_at"] = summary.updated_at.isoformat()
        if self.entity_description.attributes_fn is not None:
            attributes.update(self.entity_description.attributes_fn(self.coordinator.data) or {})
        return attributes or None

    @property
    def _capability_available(self) -> bool:
        """Return whether this entry can currently provide the sensor's data."""
        required = self.entity_description.required_capability
        return (
            required is None
            or required in self._entry.runtime_data.scope_grant.available_capabilities
        )


class HealthSyncPairedDeviceSensor(CoordinatorEntity[HealthSyncCoordinator], SensorEntity):
    """Represent one sanitized value from a Google paired device."""

    _attr_has_entity_name = True
    entity_description: SensorEntityDescription

    def __init__(
        self,
        entry: HealthSyncConfigEntry,
        coordinator: HealthSyncCoordinator,
        device: PairedDeviceSummary,
        description: SensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._identity_digest = device.identity_digest
        person_slug = str(entry.data["person_slug"])
        paired_identifier = f"{person_slug}_paired_{device.identity_digest}"
        self._attr_unique_id = f"{paired_identifier}_{description.key}"
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, paired_identifier)},
            name=f"{entry.title} {device.product_name}",
            manufacturer="ResiyHome",
            model=device.product_name,
            model_id=device.device_type,
        )

    @property
    def _paired_device(self) -> PairedDeviceSummary | None:
        return next(
            (
                device
                for device in self.coordinator.data.paired_devices
                if device.identity_digest == self._identity_digest
            ),
            None,
        )

    @property
    @override
    def available(self) -> bool:
        """Expose a retained entity as unavailable while its device is absent."""
        return self._paired_device is not None and self.native_value is not None

    @property
    @override
    def native_value(self) -> datetime | int | None:
        """Return the paired value without retaining raw Google identifiers."""
        device = self._paired_device
        if device is None:
            return None
        if self.entity_description.key == "battery_level":
            return device.battery_percentage
        return device.last_sync

    @property
    @override
    def extra_state_attributes(self) -> dict[str, AttributeValue] | None:
        """Expose only the documented normalized battery status."""
        device = self._paired_device
        if (
            device is None
            or self.entity_description.key != "battery_level"
            or device.battery_status is None
        ):
            return None
        return {"battery_status": device.battery_status}
