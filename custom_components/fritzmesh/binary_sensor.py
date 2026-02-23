"""Binary sensor platform for Fritz!Box Mesh (per-client connectivity).

Entities created
────────────────
One ClientConnectivitySensor per client MAC address discovered in the topology.

  State: on  (True)  → client.connection_state == "CONNECTED"
         off (False) → any other state, or client not found in latest data

Device class: CONNECTIVITY
  Home Assistant renders this as "Connected" / "Disconnected" in the UI and
  uses an appropriate icon (link / link-off).

Device grouping
───────────────
Each ClientConnectivitySensor is bound to a DeviceInfo with the same
identifiers as the ClientMeshNodeSensor and ClientConnectionSensor created in
sensor.py.  This means HA groups all three entities under one device card
for each client, keeping the device registry clean.

Dynamic discovery
─────────────────
Like the sensor platform, we use a coordinator listener callback to detect new
client MACs after each refresh and register entities on the fly, without
requiring a restart.
"""
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


# ── Platform setup ────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fritz!Box Mesh binary sensor entities for a given config entry.

    Registers a coordinator listener that creates a ClientConnectivitySensor for
    every client MAC that hasn't been seen before.  The listener is also called
    once immediately to handle devices already present after the first fetch.

    Args:
        hass:              Home Assistant instance.
        entry:             The config entry this platform belongs to.
        async_add_entities: Callback to register new entities with HA.
    """
    coordinator: FritzMeshCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Track known MACs to avoid creating duplicate sensors across refreshes.
    known_client_macs: set[str] = set()

    @callback
    def _async_add_new_entities() -> None:
        """Create binary sensors for client MACs not yet seen.

        Called after every successful coordinator refresh.  New client devices
        that appear (e.g. a phone reconnects to the WiFi) will get their binary
        sensor entity created automatically.
        """
        new_entities: list[BinarySensorEntity] = []

        for mac, (client, _) in coordinator.data.clients_by_mac.items():
            # The second element of the tuple is the parent MeshNode (or None);
            # we don't need it here – only the ClientDevice matters.
            if mac not in known_client_macs:
                known_client_macs.add(mac)
                new_entities.append(
                    ClientConnectivitySensor(coordinator, entry, client)
                )

        if new_entities:
            async_add_entities(new_entities)

    # Subscribe so the callback fires after every coordinator update.
    coordinator.async_add_listener(_async_add_new_entities)

    # Process devices that were already fetched during initial setup.
    _async_add_new_entities()


# ── Binary sensor entity ──────────────────────────────────────────────────────

class ClientConnectivitySensor(
    CoordinatorEntity[FritzMeshCoordinator], BinarySensorEntity
):
    """Binary sensor that is ON when a client device is connected to the mesh.

    Uses the CONNECTIVITY device class so Home Assistant displays it with the
    standard "Connected" / "Disconnected" labels and appropriate icon.

    The entity name is intentionally set to None (`_attr_name = None`), which
    causes Home Assistant to use the device name (e.g. "Laptop") as the entity's
    friendly name, making it more natural in the dashboard and automations.
    """

    has_entity_name = True
    device_class = BinarySensorDeviceClass.CONNECTIVITY

    # Setting name to None makes the entity's friendly name equal to its device
    # name.  For example, if the device is called "My Phone", the sensor will
    # appear as "My Phone" (not "My Phone Connectivity" or similar).
    _attr_name = None

    def __init__(
        self,
        coordinator: FritzMeshCoordinator,
        entry: ConfigEntry,
        client: ClientDevice,
    ) -> None:
        """Initialise the connectivity sensor.

        Args:
            coordinator: Shared data coordinator for this config entry.
            entry:       Config entry; used for the unique_id prefix.
            client:      The ClientDevice whose connectivity this sensor tracks.
        """
        super().__init__(coordinator)
        # Store only the MAC; the coordinator is the source of truth for current state.
        self._client_mac = client.mac

        # Build a unique_id that is stable across restarts: entry_id + MAC + suffix.
        self._attr_unique_id = f"{entry.entry_id}_{client.mac}_connected"

        # Share the same device identifiers as the sensor platform entities for
        # this client, so HA groups all three entities under a single device card.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, client.mac)},
            name=client.name,
        )

    @property
    def is_on(self) -> bool:
        """Return True when the client device is actively connected to the mesh.

        Reads the latest `connection_state` from the coordinator data rather
        than caching it, so state is always current after a refresh.

        Returns:
            True  if connection_state == "CONNECTED".
            False if the client is not in the latest topology data (e.g. it was
                  removed from the Fritz!Box host list) or has any other state
                  ("DISCONNECTED", "unknown", etc.).
        """
        entry = self.coordinator.data.clients_by_mac.get(self._client_mac)
        if entry is None:
            # Client disappeared from topology entirely → treat as disconnected.
            return False
        client, _ = entry
        return client.connection_state == "CONNECTED"
