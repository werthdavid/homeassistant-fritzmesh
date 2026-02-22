"""DataUpdateCoordinator for Fritz!Box Mesh."""
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


@dataclass
class FritzMeshData:
    """Data returned by a single coordinator refresh."""

    mesh_nodes_by_mac: dict[str, MeshNode]
    """MAC → MeshNode for every mesh node (master + slaves)."""

    clients_by_mac: dict[str, tuple[ClientDevice, Optional[MeshNode]]]
    """MAC → (ClientDevice, connected MeshNode or None) for every client."""


class FritzMeshCoordinator(DataUpdateCoordinator[FritzMeshData]):
    """Polls the Fritz!Box and distributes topology data to all platforms."""

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
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=poll_interval),
        )
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._use_tls = use_tls

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch(self) -> MeshTopology:
        """Blocking call – run in executor thread."""
        fetcher = FritzMeshFetcher(
            address=self._host,
            port=self._port,
            user=self._username,
            password=self._password,
            use_tls=self._use_tls,
        )
        return fetcher.fetch()

    # ------------------------------------------------------------------
    # DataUpdateCoordinator interface
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> FritzMeshData:
        try:
            topology: MeshTopology = await self.hass.async_add_executor_job(self._fetch)
        except Exception as err:
            raise UpdateFailed(f"Error communicating with Fritz!Box: {err}") from err

        # Index mesh nodes by MAC
        mesh_nodes_by_mac: dict[str, MeshNode] = {
            node.mac: node for node in topology.mesh_nodes if node.mac
        }

        # Index all clients by MAC, paired with their parent mesh node
        clients_by_mac: dict[str, tuple[ClientDevice, Optional[MeshNode]]] = {}

        for node in topology.mesh_nodes:
            for client in node.clients:
                if client.mac:
                    clients_by_mac[client.mac] = (client, node)

        for client in topology.unassigned_clients:
            if client.mac:
                clients_by_mac[client.mac] = (client, None)

        return FritzMeshData(
            mesh_nodes_by_mac=mesh_nodes_by_mac,
            clients_by_mac=clients_by_mac,
        )
