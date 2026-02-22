"""Binary sensor platform for Fritz!Box Mesh (per-client connectivity)."""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FritzMeshCoordinator
from .fritz_mesh import ClientDevice

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fritz!Box Mesh binary sensor entities."""
    coordinator: FritzMeshCoordinator = hass.data[DOMAIN][entry.entry_id]

    known_client_macs: set[str] = set()

    @callback
    def _async_add_new_entities() -> None:
        new_entities: list[BinarySensorEntity] = []

        for mac, (client, _) in coordinator.data.clients_by_mac.items():
            if mac not in known_client_macs:
                known_client_macs.add(mac)
                new_entities.append(
                    ClientConnectivitySensor(coordinator, entry, client)
                )

        if new_entities:
            async_add_entities(new_entities)

    coordinator.async_add_listener(_async_add_new_entities)
    _async_add_new_entities()


class ClientConnectivitySensor(
    CoordinatorEntity[FritzMeshCoordinator], BinarySensorEntity
):
    """Binary sensor: on when a client device is CONNECTED to the mesh."""

    has_entity_name = True
    device_class = BinarySensorDeviceClass.CONNECTIVITY

    # name = None → friendly name equals the device name (e.g. "Laptop")
    _attr_name = None

    def __init__(
        self,
        coordinator: FritzMeshCoordinator,
        entry: ConfigEntry,
        client: ClientDevice,
    ) -> None:
        super().__init__(coordinator)
        self._client_mac = client.mac
        self._attr_unique_id = f"{entry.entry_id}_{client.mac}_connected"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, client.mac)},
            name=client.name,
        )

    @property
    def is_on(self) -> bool:
        entry = self.coordinator.data.clients_by_mac.get(self._client_mac)
        if entry is None:
            return False
        client, _ = entry
        return client.connection_state == "CONNECTED"
