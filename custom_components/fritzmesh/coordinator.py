"""DataUpdateCoordinator for Fritz!Box Mesh.

The coordinator is the central hub for all data in this integration.
Home Assistant's DataUpdateCoordinator handles the polling timer and ensures
all subscribing entities receive the same fresh data object at the same time,
rather than each entity making its own independent network calls.

Data flow
──────────
  HA event loop
      │  (every `poll_interval` seconds)
      ▼
  _async_update_data()          ← runs on the HA event loop (async)
      │
      │  hass.async_add_executor_job()
      ▼
  _fetch()                      ← runs in a thread-pool executor (blocking)
      │
      │  FritzMeshFetcher.fetch()
      ▼
  Fritz!Box TR-064 / SOAP       ← network I/O

  The parsed MeshTopology is converted into a FritzMeshData object and
  returned to the coordinator, which then notifies all registered listeners
  (sensor and binary-sensor entities) so they can update their state.

Usage in platform modules
──────────────────────────
  coordinator = hass.data[DOMAIN][entry.entry_id]   # retrieve coordinator
  coordinator.data                                   # latest FritzMeshData
  coordinator.async_add_listener(callback)           # subscribe to updates
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
from typing import Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .fritz_mesh import FritzMeshFetcher, MeshNode, ClientDevice, MeshTopology

_LOGGER = logging.getLogger(__name__)


# ── Shared data model ─────────────────────────────────────────────────────────

@dataclass
class FritzMeshData:
    """Snapshot of the Fritz!Box mesh topology returned by a single coordinator refresh.

    Both dicts are keyed by MAC address (upper-case, colon-separated) so that
    entity objects can look up the latest data for their device in O(1) time
    without iterating over all nodes or clients.

    Attributes:
        mesh_nodes_by_mac:
            Maps MAC address → MeshNode for every Fritz!Box device that is
            part of the mesh (master router plus all repeater slaves).

        clients_by_mac:
            Maps MAC address → (ClientDevice, Optional[MeshNode]) for every
            non-mesh device seen in the topology.
            The second element of the tuple is the mesh node the client is
            directly connected to, or None if the client couldn't be assigned
            to any mesh node (i.e. it appears in unassigned_clients).
    """

    mesh_nodes_by_mac: dict[str, MeshNode]
    """MAC → MeshNode for every mesh node (master + slaves)."""

    clients_by_mac: dict[str, tuple[ClientDevice, Optional[MeshNode]]]
    """MAC → (ClientDevice, connected MeshNode or None) for every client."""


# ── Coordinator ───────────────────────────────────────────────────────────────

class FritzMeshCoordinator(DataUpdateCoordinator[FritzMeshData]):
    """Polls the Fritz!Box and distributes topology data to all platforms.

    Inherits from DataUpdateCoordinator which provides:
      • Automatic periodic scheduling via `update_interval`.
      • Deduplication: only one fetch is in-flight at a time.
      • Listener management: entities subscribe via async_add_listener().
      • Error propagation: UpdateFailed exceptions mark entities unavailable.

    One coordinator instance is created per config entry (i.e. per Fritz!Box).
    """

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        username: str,
        password: str,
        use_tls: bool,
        poll_interval: int,
    ) -> None:
        """Initialise the coordinator.

        Args:
            hass:          The Home Assistant instance.
            host:          Fritz!Box hostname or IP address.
            port:          TR-064 port (49000 for HTTP, 49443 for HTTPS).
            username:      Web-UI username (may be empty string).
            password:      Web-UI password (may be empty string).
            use_tls:       Whether to use HTTPS for the TR-064 connection.
            poll_interval: Seconds between topology refreshes.
        """
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,                              # used in log messages
            update_interval=timedelta(seconds=poll_interval),
        )
        # Store connection details so _fetch() can build a fresh fetcher.
        # We create a new FritzMeshFetcher on each poll instead of caching it
        # on the coordinator, keeping the coordinator stateless with respect
        # to the network connection.
        self._host     = host
        self._port     = port
        self._username = username
        self._password = password
        self._use_tls  = use_tls

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _fetch(self) -> MeshTopology:
        """Perform a blocking fetch from the Fritz!Box.

        This method is intentionally synchronous because fritzconnection uses
        Python's `socket` module directly (no asyncio support).  It must be
        called inside an executor thread via `hass.async_add_executor_job()`.

        Returns:
            A freshly parsed MeshTopology.

        Raises:
            Any exception raised by FritzMeshFetcher.fetch() (network errors,
            auth failures, SOAP parsing errors, …).
        """
        fetcher = FritzMeshFetcher(
            address=self._host,
            port=self._port,
            user=self._username,
            password=self._password,
            use_tls=self._use_tls,
        )
        return fetcher.fetch()

    # ── DataUpdateCoordinator interface ───────────────────────────────────────

    async def _async_update_data(self) -> FritzMeshData:
        """Fetch new topology data and transform it into a FritzMeshData object.

        Called automatically by the parent class according to `update_interval`.
        Also called explicitly by async_config_entry_first_refresh() during
        integration setup.

        The method:
          1. Runs _fetch() in a thread-pool executor (non-blocking for HA).
          2. Converts the MeshTopology into two MAC-keyed lookup dicts.
          3. Returns a FritzMeshData snapshot that all subscribed entities
             will receive via their CoordinatorEntity._handle_coordinator_update().

        Returns:
            A FritzMeshData populated from the latest topology snapshot.

        Raises:
            UpdateFailed: Wraps any exception from _fetch() so that HA marks
                          entities as unavailable and logs a structured message.
        """
        try:
            # hass.async_add_executor_job schedules _fetch() in the default
            # thread-pool executor and awaits it without blocking the event loop.
            topology: MeshTopology = await self.hass.async_add_executor_job(self._fetch)
        except Exception as err:
            # Wrapping in UpdateFailed causes HA to set entity availability to
            # False and surface a clear error in the logs / repairs panel.
            raise UpdateFailed(f"Error communicating with Fritz!Box: {err}") from err

        # ── Build mesh-node index ────────────────────────────────────────────
        # Skip nodes without a MAC (shouldn't happen, but defensive coding).
        mesh_nodes_by_mac: dict[str, MeshNode] = {
            node.mac: node
            for node in topology.mesh_nodes
            if node.mac
        }

        # ── Build client index ───────────────────────────────────────────────
        clients_by_mac: dict[str, tuple[ClientDevice, Optional[MeshNode]]] = {}

        # Clients assigned to a specific mesh node.
        for node in topology.mesh_nodes:
            for client in node.clients:
                if client.mac:
                    # Pair the client with its parent mesh node so entity code
                    # can display "connected to FRITZ!Repeater 2400".
                    clients_by_mac[client.mac] = (client, node)

        # Unassigned clients (visible in the topology but not linked to a node).
        for client in topology.unassigned_clients:
            if client.mac:
                # Second element is None to signal "parent unknown".
                clients_by_mac[client.mac] = (client, None)

        return FritzMeshData(
            mesh_nodes_by_mac=mesh_nodes_by_mac,
            clients_by_mac=clients_by_mac,
        )
