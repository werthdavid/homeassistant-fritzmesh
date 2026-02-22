"""Sensor platform for Fritz!Box Mesh."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_HOST
from .coordinator import FritzMeshCoordinator
from .fritz_mesh import ClientDevice, MeshNode

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fritz!Box Mesh sensor entities."""
    coordinator: FritzMeshCoordinator = hass.data[DOMAIN][entry.entry_id]

    # Topology sensor is created once — the card reads from it.
    async_add_entities([FritzMeshTopologySensor(coordinator, entry)])

    known_node_macs: set[str] = set()
    known_client_macs: set[str] = set()

    @callback
    def _async_add_new_entities() -> None:
        new_entities: list[SensorEntity] = []

        # --- Mesh node count sensors ---
        for mac, node in coordinator.data.mesh_nodes_by_mac.items():
            if mac not in known_node_macs:
                known_node_macs.add(mac)
                new_entities.extend(
                    [
                        MeshNodeCountSensor(
                            coordinator, entry, node, "connected_devices",
                            "Connected Devices", "mdi:devices",
                        ),
                        MeshNodeCountSensor(
                            coordinator, entry, node, "wifi_devices",
                            "WiFi Devices", "mdi:wifi",
                        ),
                        MeshNodeCountSensor(
                            coordinator, entry, node, "lan_devices",
                            "LAN Devices", "mdi:ethernet",
                        ),
                    ]
                )

        # --- Client state sensors ---
        for mac, (client, _) in coordinator.data.clients_by_mac.items():
            if mac not in known_client_macs:
                known_client_macs.add(mac)
                new_entities.extend(
                    [
                        ClientMeshNodeSensor(coordinator, entry, client),
                        ClientConnectionSensor(coordinator, entry, client),
                    ]
                )

        if new_entities:
            async_add_entities(new_entities)

    coordinator.async_add_listener(_async_add_new_entities)
    _async_add_new_entities()


# ---------------------------------------------------------------------------
# Mesh node sensors
# ---------------------------------------------------------------------------


class MeshNodeCountSensor(CoordinatorEntity[FritzMeshCoordinator], SensorEntity):
    """Reports a client count for one mesh node (total / WiFi / LAN)."""

    has_entity_name = True
    state_class = SensorStateClass.MEASUREMENT
    native_unit_of_measurement = "devices"

    def __init__(
        self,
        coordinator: FritzMeshCoordinator,
        entry: ConfigEntry,
        node: MeshNode,
        sensor_key: str,
        sensor_name: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._node_mac = node.mac
        self._sensor_key = sensor_key
        self._attr_name = sensor_name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{node.mac}_{sensor_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node.mac)},
            name=node.name,
            model=node.model,
            manufacturer=node.vendor,
            sw_version=node.firmware,
        )

    @property
    def native_value(self) -> int | None:
        node = self.coordinator.data.mesh_nodes_by_mac.get(self._node_mac)
        if node is None:
            return None
        if self._sensor_key == "connected_devices":
            return len(node.clients)
        if self._sensor_key == "wifi_devices":
            return sum(1 for c in node.clients if c.connection_type == "WLAN")
        if self._sensor_key == "lan_devices":
            return sum(1 for c in node.clients if c.connection_type == "LAN")
        return None


# ---------------------------------------------------------------------------
# Client sensors
# ---------------------------------------------------------------------------


class _ClientSensorBase(CoordinatorEntity[FritzMeshCoordinator], SensorEntity):
    """Base for sensors attached to a client device."""

    has_entity_name = True

    def __init__(
        self,
        coordinator: FritzMeshCoordinator,
        entry: ConfigEntry,
        client: ClientDevice,
        sensor_key: str,
        sensor_name: str,
        icon: str,
    ) -> None:
        super().__init__(coordinator)
        self._client_mac = client.mac
        self._attr_name = sensor_name
        self._attr_icon = icon
        self._attr_unique_id = f"{entry.entry_id}_{client.mac}_{sensor_key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, client.mac)},
            name=client.name,
        )


class ClientMeshNodeSensor(_ClientSensorBase):
    """Reports which mesh node a client is currently connected to."""

    def __init__(
        self,
        coordinator: FritzMeshCoordinator,
        entry: ConfigEntry,
        client: ClientDevice,
    ) -> None:
        super().__init__(
            coordinator, entry, client,
            "mesh_node", "Mesh Node", "mdi:router-wireless",
        )

    @property
    def native_value(self) -> str | None:
        entry = self.coordinator.data.clients_by_mac.get(self._client_mac)
        if entry is None:
            return None
        _, mesh_node = entry
        return mesh_node.name if mesh_node else None


class ClientConnectionSensor(_ClientSensorBase):
    """Reports the connection type (WiFi / LAN) for a client device."""

    def __init__(
        self,
        coordinator: FritzMeshCoordinator,
        entry: ConfigEntry,
        client: ClientDevice,
    ) -> None:
        super().__init__(
            coordinator, entry, client,
            "connection", "Connection", "mdi:connection",
        )

    @property
    def native_value(self) -> str | None:
        entry = self.coordinator.data.clients_by_mac.get(self._client_mac)
        if entry is None:
            return None
        client, _ = entry
        if client.connection_type == "WLAN":
            return "WiFi"
        if client.connection_type == "LAN":
            return "LAN"
        return client.connection_type or None


# ---------------------------------------------------------------------------
# Topology sensor — full mesh snapshot for the Lovelace card
# ---------------------------------------------------------------------------


class FritzMeshTopologySensor(CoordinatorEntity[FritzMeshCoordinator], SensorEntity):
    """One sensor per entry that exposes the complete mesh topology as attributes.

    The ``fritzmesh-card`` Lovelace card reads this entity; no other sensors
    are needed for the visualisation.
    """

    has_entity_name = True
    _attr_icon = "mdi:router-network"
    _attr_native_unit_of_measurement = "nodes"

    def __init__(
        self,
        coordinator: FritzMeshCoordinator,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator)
        self._host: str = entry.data.get(CONF_HOST, "")
        self._attr_name = "Topology"
        self._attr_unique_id = f"{entry.entry_id}_topology"
        # Attach to a virtual "integration" device so it doesn't collide with
        # node or client devices.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Fritz!Box Mesh ({self._host})",
        )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.mesh_nodes_by_mac)

    # ------------------------------------------------------------------
    # Attributes — consumed by the Lovelace card
    # ------------------------------------------------------------------

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data

        # Ordered: master first, then slaves alphabetically
        ordered_nodes = sorted(
            data.mesh_nodes_by_mac.values(),
            key=lambda n: (0 if n.role == "master" else 1, n.name),
        )

        mesh_nodes = []
        for node in ordered_nodes:
            clients = [
                {
                    "name": c.name,
                    "mac": c.mac,
                    "ip": c.ip,
                    "connection_type": c.connection_type,
                    "connection_state": c.connection_state,
                    "interface_name": c.interface_name,
                    "cur_rx_kbps": c.cur_rx_kbps,
                    "cur_tx_kbps": c.cur_tx_kbps,
                    "max_rx_kbps": c.max_rx_kbps,
                    "max_tx_kbps": c.max_tx_kbps,
                }
                for c in node.clients
            ]
            mesh_nodes.append(
                {
                    "name": node.name,
                    "mac": node.mac,
                    "role": node.role,
                    "model": node.model,
                    "vendor": node.vendor,
                    "firmware": node.firmware,
                    "parent_link_type": node.parent_link_type,
                    "clients": clients,
                }
            )

        unassigned = [
            {
                "name": client.name,
                "mac": client.mac,
                "ip": client.ip,
                "connection_type": client.connection_type,
                "connection_state": client.connection_state,
                "interface_name": client.interface_name,
                "cur_rx_kbps": client.cur_rx_kbps,
                "cur_tx_kbps": client.cur_tx_kbps,
                "max_rx_kbps": client.max_rx_kbps,
                "max_tx_kbps": client.max_tx_kbps,
            }
            for client, mesh_node in data.clients_by_mac.values()
            if mesh_node is None
        ]

        return {
            "host": self._host,
            "mesh_nodes": mesh_nodes,
            "unassigned_clients": unassigned,
        }
