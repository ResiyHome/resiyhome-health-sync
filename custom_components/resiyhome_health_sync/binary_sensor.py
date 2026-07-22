"""Synchronization-health binary sensors for Health Sync."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import override

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HealthSyncConfigEntry
from .const import DOMAIN
from .coordinator import HealthSyncCoordinator
from .models import CoordinatorSnapshot

type StateFunction = Callable[[HealthSyncCoordinator, CoordinatorSnapshot], bool]
type TimingFunction = Callable[[CoordinatorSnapshot], datetime | None]


@dataclass(frozen=True, kw_only=True)
class HealthSyncBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describe a synchronization-health binary sensor."""

    state_fn: StateFunction
    timing_key: str
    timing_fn: TimingFunction


BINARY_SENSOR_DESCRIPTIONS: tuple[HealthSyncBinarySensorEntityDescription, ...] = (
    HealthSyncBinarySensorEntityDescription(
        key="health_data_stale",
        name="Health data stale",
        icon="mdi:clock-alert-outline",
        device_class=BinarySensorDeviceClass.PROBLEM,
        state_fn=lambda coordinator, snapshot: coordinator.is_stale,
        timing_key="last_success",
        timing_fn=lambda snapshot: snapshot.last_success,
    ),
    HealthSyncBinarySensorEntityDescription(
        key="health_authorization_problem",
        name="Health authorization problem",
        icon="mdi:key-alert-outline",
        device_class=BinarySensorDeviceClass.PROBLEM,
        state_fn=lambda coordinator, snapshot: not snapshot.authorization_healthy,
        timing_key="last_attempt",
        timing_fn=lambda snapshot: snapshot.last_attempt,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HealthSyncConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up one person's synchronization-health entities."""
    del hass
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        HealthSyncBinarySensor(entry, coordinator, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )


class HealthSyncBinarySensor(CoordinatorEntity[HealthSyncCoordinator], BinarySensorEntity):
    """Represent stale data or unhealthy authorization."""

    entity_description: HealthSyncBinarySensorEntityDescription

    def __init__(
        self,
        entry: HealthSyncConfigEntry,
        coordinator: HealthSyncCoordinator,
        description: HealthSyncBinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        person_slug = str(entry.data["person_slug"])
        self._attr_name = f"{entry.title} {description.name}"
        self._attr_unique_id = f"{person_slug}_{description.key}"
        self._attr_device_info = DeviceInfo(
            entry_type=DeviceEntryType.SERVICE,
            identifiers={(DOMAIN, person_slug)},
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
        """Keep health-state entities visible during the failures they describe."""
        return True

    @property
    @override
    def is_on(self) -> bool:
        """Return the synchronization-health state."""
        return self.entity_description.state_fn(self.coordinator, self.coordinator.data)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, str] | None:
        """Expose only the relevant summarized synchronization time."""
        value = self.entity_description.timing_fn(self.coordinator.data)
        if value is None:
            return None
        return {self.entity_description.timing_key: value.isoformat()}
