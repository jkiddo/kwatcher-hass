"""Sensor platform for K-Watch Messenger."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_ADDRESS, CONF_DEVICE_NAME, DOMAIN
from .coordinator import KWatchCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up K-Watch sensors from a config entry."""
    coordinator: KWatchCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities([
        KWatchLastResponseSensor(coordinator, entry),
        KWatchBatterySensor(coordinator, entry),
        KWatchConnectionSensor(coordinator, entry),
    ])


class KWatchBaseSensor(CoordinatorEntity[KWatchCoordinator], SensorEntity):
    """Base class for K-Watch sensors."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: KWatchCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data[CONF_DEVICE_ADDRESS])},
            name=entry.data.get(CONF_DEVICE_NAME, "K-WATCH"),
            manufacturer="Keeprapid",
            model="K-WATCH",
        )


class KWatchLastResponseSensor(KWatchBaseSensor):
    """Sensor showing the last response from the watch wearer."""

    _attr_name = "Last Response"
    _attr_icon = "mdi:message-reply-text"

    def __init__(
        self, coordinator: KWatchCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_last_response"

    @property
    def native_value(self) -> str | None:
        return self.coordinator.data.get("last_response")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        return {
            "last_message": data.get("last_message"),
            "last_message_time": data.get("last_message_time"),
            "last_response_time": data.get("last_response_time"),
            "message_history": data.get("message_history", []),
        }


class KWatchBatterySensor(KWatchBaseSensor):
    """Sensor showing the watch battery level."""

    _attr_name = "Battery"
    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = "%"

    def __init__(
        self, coordinator: KWatchCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_battery"

    @property
    def native_value(self) -> int | None:
        return self.coordinator.data.get("battery_level")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        charging = self.coordinator.data.get("battery_charging")
        if charging is not None:
            return {"charging": charging}
        return {}


class KWatchConnectionSensor(KWatchBaseSensor):
    """Sensor showing the BLE connection status."""

    _attr_name = "Connection"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["Connected", "Disconnected"]
    _attr_icon = "mdi:bluetooth-connect"

    def __init__(
        self, coordinator: KWatchCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_connection"

    @property
    def native_value(self) -> str:
        return "Connected" if self.coordinator.data.get("connected") else "Disconnected"

    @property
    def icon(self) -> str:
        if self.coordinator.data.get("connected"):
            return "mdi:bluetooth-connect"
        return "mdi:bluetooth-off"
