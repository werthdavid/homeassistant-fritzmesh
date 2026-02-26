"""Sensor platform for Fritz!Box Mesh.

Entities created
────────────────
Per-mesh-node (one set per Fritz!Box / repeater discovered):
  • MeshNodeCountSensor("connected_devices") – total client count
  • MeshNodeCountSensor("wifi_devices")      – WLAN client count
  • MeshNodeCountSensor("lan_devices")       – LAN client count

Per-client device (one set per client MAC discovered):
  • ClientMeshNodeSensor   – name of the mesh node the client is connected to
  • ClientConnectionSensor – "WiFi" or "LAN"

One-per-integration:
  • FritzMeshTopologySensor – state = number of mesh nodes; attributes contain
                              the full topology dict consumed by fritzmesh-card

Dynamic discovery
─────────────────
Rather than pre-declaring every entity, we use a coordinator listener callback
(_async_add_new_entities) that fires after each coordinator refresh.  New
MAC addresses that weren't seen before are registered as entities on the fly.
This means entities appear in HA's UI automatically as new devices join the
network, with no restart required.
"""
from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity, SensorStateClass, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CONF_HOST
from .coordinator import FritzMeshCoordinator
from .fritz_mesh import ClientDevice, MeshNode

_LOGGER = logging.getLogger(__name__)


# ── Platform setup ────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Fritz!Box Mesh sensor entities for a given config entry.

    This function is called by HA when the integration is loaded.  It:
      1. Creates the one-per-entry topology sensor immediately.
      2. Registers a listener that adds per-node and per-client sensors
         whenever the coordinator delivers a new data snapshot containing
         previously unseen MAC addresses.

    Args:
        hass:              Home Assistant instance.
        entry:             The config entry this platform belongs to.
        async_add_entities: Callback to register new entities with HA.
    """
    coordinator: FritzMeshCoordinator = hass.data[DOMAIN][entry.entry_id]

    # The topology sensor is a singleton per config entry; create it once.
    # The fritzmesh-card Lovelace card reads its `extra_state_attributes`.
    async_add_entities([FritzMeshTopologySensor(coordinator, entry)])

    # Track which MACs already have entities so we don't create duplicates
    # across successive coordinator refreshes.
    known_node_macs:   set[str] = set()
    known_client_macs: set[str] = set()

    @callback
    def _async_add_new_entities() -> None:
        """Inspect the latest coordinator data and create entities for new MACs.

        The @callback decorator marks this as a synchronous function safe to
        call from the HA event loop.  It is invoked by the coordinator after
        every successful refresh.
        """
        new_entities: list[SensorEntity] = []

        # ── Mesh node sensors ───────────────────────────────────────────────
        for mac, node in coordinator.data.mesh_nodes_by_mac.items():
            if mac not in known_node_macs:
                known_node_macs.add(mac)
                # Create three count sensors for each newly-discovered mesh node.
                new_entities.extend(
                    [
                        # Total number of client devices currently on this node.
                        MeshNodeCountSensor(
                            coordinator, entry, node,
                            sensor_key="connected_devices",
                            sensor_name="Connected Devices",
                            icon="mdi:devices",
                        ),
                        # Subset: wireless clients only.
                        MeshNodeCountSensor(
                            coordinator, entry, node,
                            sensor_key="wifi_devices",
                            sensor_name="WiFi Devices",
                            icon="mdi:wifi",
                        ),
                        # Subset: wired (Ethernet) clients only.
                        MeshNodeCountSensor(
                            coordinator, entry, node,
                            sensor_key="lan_devices",
                            sensor_name="LAN Devices",
                            icon="mdi:ethernet",
                        ),
                        MeshNodeRateSensor(
                            coordinator, entry, node,
                            sensor_key="current_rx_rate",
                            sensor_name="Current RX Rate",
                            icon="mdi:download-network",
                            direction="rx",
                        ),
                        MeshNodeRateSensor(
                            coordinator, entry, node,
                            sensor_key="current_tx_rate",
                            sensor_name="Current TX Rate",
                            icon="mdi:upload-network",
                            direction="tx",
                        ),
                    ]
                )

        # ── Client sensors ──────────────────────────────────────────────────
        for mac, (client, _) in coordinator.data.clients_by_mac.items():
            if mac not in known_client_macs:
                known_client_macs.add(mac)
                new_entities.extend(
                    [
                        # Which mesh node (by name) is this client connected to?
                        ClientMeshNodeSensor(coordinator, entry, client),
                        # What medium is it using – WiFi or LAN?
                        ClientConnectionSensor(coordinator, entry, client),
                    ]
                )

        if new_entities:
            async_add_entities(new_entities)

    # Subscribe the callback so it runs after every coordinator refresh.
    coordinator.async_add_listener(_async_add_new_entities)

    # Call once immediately to create entities from the data already fetched
    # during async_config_entry_first_refresh().
    _async_add_new_entities()


# ── Mesh node sensors ─────────────────────────────────────────────────────────

class MeshNodeCountSensor(CoordinatorEntity[FritzMeshCoordinator], SensorEntity):
    """Reports a client device count for one mesh node.

    Three variants are instantiated per node (controlled by `sensor_key`):
      "connected_devices" → total client count (the node's clients list length)
      "wifi_devices"      → count of clients with connection_type == "WLAN"
      "lan_devices"       → count of clients with connection_type == "LAN"

    The entity is attached to a DeviceInfo identified by the node's MAC address.
    This groups all three count sensors under a single device card in HA's UI.
    """

    has_entity_name = True                             # entity name = device name + sensor name
    state_class     = SensorStateClass.MEASUREMENT     # numeric, not cumulative
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
        """Initialise the count sensor.

        Args:
            coordinator: Shared data coordinator for this config entry.
            entry:       Config entry (used for unique_id prefix).
            node:        The MeshNode this sensor tracks.
            sensor_key:  One of "connected_devices", "wifi_devices", "lan_devices".
            sensor_name: Human-readable name shown in the HA UI.
            icon:        MDI icon string (e.g. "mdi:wifi").
        """
        super().__init__(coordinator)
        # Store only the MAC, not the full MeshNode object, so we look up
        # the latest node data from the coordinator on every state read.
        self._node_mac   = node.mac
        self._sensor_key = sensor_key

        self._attr_name      = sensor_name
        self._attr_icon      = icon
        # Unique ID must be stable across restarts; we use entry_id + MAC + key.
        self._attr_unique_id = f"{entry.entry_id}_{node.mac}_{sensor_key}"

        # Bind this entity to the mesh node's device in HA's device registry.
        # All three count sensors will appear under the same device card.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, node.mac)},
            name=node.name,
            model=node.model,
            manufacturer=node.vendor,
            sw_version=node.firmware,
        )

    @property
    def native_value(self) -> int | None:
        """Return the current count value from the latest coordinator data.

        Returns None if the mesh node is no longer present in the topology
        (e.g. a repeater was removed from the mesh while HA was running).
        """
        # Always fetch from coordinator.data rather than storing state locally,
        # so the value is guaranteed to reflect the most recent poll.
        node = self.coordinator.data.mesh_nodes_by_mac.get(self._node_mac)
        if node is None:
            return None

        if self._sensor_key == "connected_devices":
            # Count all clients regardless of connection type.
            return len(node.clients)
        if self._sensor_key == "wifi_devices":
            # Count only wireless clients (WLAN = Wireless LAN in Fritz!Box terminology).
            return sum(1 for c in node.clients if c.connection_type == "WLAN")
        if self._sensor_key == "lan_devices":
            # Count only wired Ethernet clients.
            return sum(1 for c in node.clients if c.connection_type == "LAN")

        return None  # unreachable, but keeps the type checker happy


class MeshNodeRateSensor(CoordinatorEntity[FritzMeshCoordinator], SensorEntity):
    """Reports aggregate current transfer rate for a mesh node (kbit/s)."""

    has_entity_name = True
    state_class = SensorStateClass.MEASUREMENT
    device_class = SensorDeviceClass.DATA_RATE
    native_unit_of_measurement = "kbit/s"

    def __init__(
        self,
        coordinator: FritzMeshCoordinator,
        entry: ConfigEntry,
        node: MeshNode,
        sensor_key: str,
        sensor_name: str,
        icon: str,
        direction: str,  # "rx" or "tx"
    ) -> None:
        super().__init__(coordinator)
        self._node_mac = node.mac
        self._direction = direction
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
        if self._direction == "rx":
            return sum(max(0, c.cur_rx_kbps) for c in node.clients)
        return sum(max(0, c.cur_tx_kbps) for c in node.clients)


# ── Client sensors – base class ───────────────────────────────────────────────

class _ClientSensorBase(CoordinatorEntity[FritzMeshCoordinator], SensorEntity):
    """Base class for sensor entities that are associated with a single client device.

    Provides the shared DeviceInfo binding and unique_id pattern so that both
    ClientMeshNodeSensor and ClientConnectionSensor appear under the same
    device card for their client in HA's UI.

    Subclasses must implement the `native_value` property.
    """

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
        """Initialise the client sensor base.

        Args:
            coordinator: Shared data coordinator for this config entry.
            entry:       Config entry (used for unique_id prefix).
            client:      The ClientDevice this sensor tracks.
            sensor_key:  Short identifier appended to unique_id (e.g. "mesh_node").
            sensor_name: Human-readable name (e.g. "Mesh Node").
            icon:        MDI icon string.
        """
        super().__init__(coordinator)
        # Store only the MAC so we always look up fresh data from the coordinator.
        self._client_mac     = client.mac
        self._attr_name      = sensor_name
        self._attr_icon      = icon
        self._attr_unique_id = f"{entry.entry_id}_{client.mac}_{sensor_key}"

        # Group this sensor under the client device in the HA device registry.
        # The ClientConnectivitySensor (binary_sensor.py) uses the same
        # identifiers, so all three entities share one device card.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, client.mac)},
            name=client.name,
        )


# ── Client sensors – concrete implementations ─────────────────────────────────

class ClientMeshNodeSensor(_ClientSensorBase):
    """Reports which mesh node a client is currently connected to.

    State: the human-readable name of the mesh node (e.g. "FRITZ!Repeater 2400"),
    or None if the client has no known parent mesh node.

    This lets users build automations like "notify me when the laptop roams
    from the master to the repeater in the garden".
    """

    def __init__(
        self,
        coordinator: FritzMeshCoordinator,
        entry: ConfigEntry,
        client: ClientDevice,
    ) -> None:
        super().__init__(
            coordinator, entry, client,
            sensor_key="mesh_node",
            sensor_name="Mesh Node",
            icon="mdi:router-wireless",
        )

    @property
    def native_value(self) -> str | None:
        """Return the name of the mesh node this client is connected to."""
        entry = self.coordinator.data.clients_by_mac.get(self._client_mac)
        if entry is None:
            return None
        _, mesh_node = entry
        # mesh_node is None for unassigned clients.
        return mesh_node.name if mesh_node else None


class ClientConnectionSensor(_ClientSensorBase):
    """Reports the connection medium for a client device: "WiFi" or "LAN".

    State is a human-readable string rather than the raw Fritz!Box value
    ("WLAN") to be more immediately understandable in the HA dashboard.
    """

    def __init__(
        self,
        coordinator: FritzMeshCoordinator,
        entry: ConfigEntry,
        client: ClientDevice,
    ) -> None:
        super().__init__(
            coordinator, entry, client,
            sensor_key="connection",
            sensor_name="Connection",
            icon="mdi:connection",
        )

    @property
    def native_value(self) -> str | None:
        """Return "WiFi", "LAN", or the raw connection_type string as a fallback."""
        entry = self.coordinator.data.clients_by_mac.get(self._client_mac)
        if entry is None:
            return None
        client, _ = entry

        # Translate Fritz!Box internal strings to user-friendly labels.
        if client.connection_type == "WLAN":
            return "WiFi"
        if client.connection_type == "LAN":
            return "LAN"
        # Fallback: return whatever string the Fritz!Box gave us, or None.
        return client.connection_type or None


# ── Topology sensor ───────────────────────────────────────────────────────────

class FritzMeshTopologySensor(CoordinatorEntity[FritzMeshCoordinator], SensorEntity):
    """Exposes the complete mesh topology as a single HA sensor.

    This is the entity that the ``fritzmesh-card`` Lovelace card reads.
    It intentionally bundles the entire topology into `extra_state_attributes`
    rather than spreading data across dozens of individual sensors, because
    the card needs all of it in a single entity-state subscription.

    State (native_value):
        The number of mesh nodes currently known (integer, unit = "nodes").
        This gives a useful at-a-glance count in the entity list.

    Attributes (extra_state_attributes):
        host:               The Fritz!Box IP/hostname configured by the user.
        mesh_nodes:         Ordered list of node dicts (master first, then
                            slaves alphabetically).  Each dict includes the
                            node's name, MAC, role, model, firmware, and a
                            list of its connected client dicts.
        unassigned_clients: List of client dicts for devices not assigned
                            to any mesh node.

    Attribute structure consumed by the Lovelace card (per client dict):
        name, mac, ip, connection_type, connection_state,
        interface_name, cur_rx_kbps, cur_tx_kbps, max_rx_kbps, max_tx_kbps
    """

    has_entity_name = True
    _attr_icon = "mdi:router-network"
    _attr_native_unit_of_measurement = "nodes"
    # Keep large topology payload available in HA state for the Lovelace card,
    # but prevent recorder from persisting it to avoid the 16 KiB DB limit.
    _unrecorded_attributes = frozenset({"mesh_nodes", "unassigned_clients"})

    def __init__(
        self,
        coordinator: FritzMeshCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialise the topology sensor.

        Args:
            coordinator: Shared data coordinator for this config entry.
            entry:       Config entry; we read `CONF_HOST` for display and use
                         `entry_id` to build the unique_id.
        """
        super().__init__(coordinator)
        self._host: str = entry.data.get(CONF_HOST, "")
        self._entry_id: str = entry.entry_id
        self._attr_name      = "Topology"
        self._attr_unique_id = f"{entry.entry_id}_topology"

        # Attach to a virtual device that represents the whole integration
        # (identified by entry_id, not a node MAC) so it doesn't collide with
        # the individual mesh node or client device cards.
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Fritz!Box Mesh ({self._host})",
        )

    # ── State ─────────────────────────────────────────────────────────────────

    @property
    def native_value(self) -> int:
        """Return the total number of mesh nodes in the current topology."""
        return len(self.coordinator.data.mesh_nodes_by_mac)

    # ── Attributes ────────────────────────────────────────────────────────────

    @property
    def extra_state_attributes(self) -> dict:
        """Build the full topology dict consumed by the Lovelace card.

        This property is called by HA every time the entity's state is written.
        It rebuilds the dict from scratch each time to ensure the card always
        sees current data.

        Returns:
            A JSON-serialisable dict with keys "host", "mesh_nodes", and
            "unassigned_clients".  All values are plain Python types (str, int,
            list, dict, None) so they survive the HA state machine serialisation.
        """
        data = self.coordinator.data
        mesh_node_entity_ids, connected_entity_ids = self._client_entity_id_maps()

        # Order: master first (sort key 0), then slaves alphabetically.
        # This ordering is what the Lovelace card expects; the first node
        # is treated as the master for the left-hand panel display.
        ordered_nodes = sorted(
            data.mesh_nodes_by_mac.values(),
            key=lambda n: (0 if n.role == "master" else 1, n.name),
        )

        mesh_nodes = []
        for node in ordered_nodes:
            # Serialise each client connected to this mesh node.
            clients = [
                {
                    "name":             c.name,
                    "mac":              c.mac,
                    "ip":               c.ip,
                    "connection_type":  c.connection_type,   # "WLAN" or "LAN"
                    "connection_state": c.connection_state,  # "CONNECTED" / "DISCONNECTED"
                    "interface_name":   c.interface_name,    # e.g. "AP:5G:0"
                    "cur_rx_kbps":      c.cur_rx_kbps,       # current receive speed
                    "cur_tx_kbps":      c.cur_tx_kbps,       # current transmit speed
                    "max_rx_kbps":      c.max_rx_kbps,       # max (negotiated) receive speed
                    "max_tx_kbps":      c.max_tx_kbps,       # max (negotiated) transmit speed
                    "ha_entity_id":               mesh_node_entity_ids.get(c.mac.upper()),
                    "ha_entity_mesh_node_id":     mesh_node_entity_ids.get(c.mac.upper()),
                    "ha_entity_connected_id":     connected_entity_ids.get(c.mac.upper()),
                }
                for c in node.clients
            ]
            mesh_nodes.append(
                {
                    "name":             node.name,
                    "mac":              node.mac,
                    "ip":               node.ip,
                    "role":             node.role,           # "master" or "slave"
                    "model":            node.model,          # hardware model string
                    "vendor":           node.vendor,
                    "firmware":         node.firmware,
                    "parent_link_type": node.parent_link_type,  # "WLAN"/"LAN" for slaves
                    "parent_cur_rx_kbps": node.parent_cur_rx_kbps,
                    "parent_cur_tx_kbps": node.parent_cur_tx_kbps,
                    "parent_max_rx_kbps": node.parent_max_rx_kbps,
                    "parent_max_tx_kbps": node.parent_max_tx_kbps,
                    "clients_cur_rx_kbps_total": sum(max(0, c.cur_rx_kbps) for c in node.clients),
                    "clients_cur_tx_kbps_total": sum(max(0, c.cur_tx_kbps) for c in node.clients),
                    "clients":          clients,
                }
            )

        # Collect clients that have no known parent mesh node.
        # The card renders these under an "Unassigned" section.
        unassigned = [
            {
                "name":             client.name,
                "mac":              client.mac,
                "ip":               client.ip,
                "connection_type":  client.connection_type,
                "connection_state": client.connection_state,
                "interface_name":   client.interface_name,
                "cur_rx_kbps":      client.cur_rx_kbps,
                "cur_tx_kbps":      client.cur_tx_kbps,
                "max_rx_kbps":      client.max_rx_kbps,
                "max_tx_kbps":      client.max_tx_kbps,
                "ha_entity_id":               mesh_node_entity_ids.get(client.mac.upper()),
                "ha_entity_mesh_node_id":     mesh_node_entity_ids.get(client.mac.upper()),
                "ha_entity_connected_id":     connected_entity_ids.get(client.mac.upper()),
            }
            # Iterate over clients_by_mac to find the ones with mesh_node=None.
            for client, mesh_node in data.clients_by_mac.values()
            if mesh_node is None
        ]

        return {
            "host":               self._host,
            "mesh_nodes":         mesh_nodes,
            "unassigned_clients": unassigned,
        }

    def _client_entity_id_maps(self) -> tuple[dict[str, str], dict[str, str]]:
        """Map client MAC addresses to mesh-node and connectivity entity IDs."""
        if self.hass is None:
            return {}, {}

        registry = er.async_get(self.hass)
        entries = er.async_entries_for_config_entry(registry, self._entry_id)
        prefix = f"{self._entry_id}_"
        mesh_node_suffix = "_mesh_node"
        connected_suffix = "_connected"

        mesh_node_mapping: dict[str, str] = {}
        connected_mapping: dict[str, str] = {}
        for entity in entries:
            unique_id = entity.unique_id or ""
            if not unique_id.startswith(prefix):
                continue
            if unique_id.endswith(mesh_node_suffix):
                mac = unique_id[len(prefix):-len(mesh_node_suffix)].upper()
                mesh_node_mapping[mac] = entity.entity_id
            elif unique_id.endswith(connected_suffix):
                mac = unique_id[len(prefix):-len(connected_suffix)].upper()
                connected_mapping[mac] = entity.entity_id
        return mesh_node_mapping, connected_mapping
