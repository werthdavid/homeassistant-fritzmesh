"""
Fritz!Box mesh topology fetcher using fritzconnection.

Connects to a Fritz!Box router and returns a structured view of which
client devices are connected to which mesh node (master Fritz!Box or repeaters).
"""

import logging
from dataclasses import dataclass, field
from typing import Optional

from fritzconnection.lib.fritzhosts import FritzHosts
from fritzconnection.core.fritzconnection import FritzConnection

logger = logging.getLogger(__name__)


@dataclass
class ClientDevice:
    uid: str
    name: str
    mac: str
    connection_type: str        # "WLAN" or "LAN"
    connection_state: str       # "CONNECTED" or "DISCONNECTED"
    ip: Optional[str] = None
    cur_rx_kbps: int = 0
    cur_tx_kbps: int = 0
    max_rx_kbps: int = 0
    max_tx_kbps: int = 0
    interface_name: str = ""    # e.g. "AP:5G:0", "LAN:1"


@dataclass
class MeshNode:
    uid: str
    name: str
    mac: str
    role: str                   # "master", "slave", "unknown"
    model: str = ""
    vendor: str = ""
    firmware: str = ""
    is_meshed: bool = True
    clients: list = field(default_factory=list)  # list[ClientDevice]
    # Link to parent mesh node (for slaves connected to master)
    parent_uid: Optional[str] = None
    parent_link_type: str = ""
    parent_link_state: str = ""
    parent_interface_name: str = ""


@dataclass
class MeshTopology:
    schema_version: str
    mesh_nodes: list            # list[MeshNode] - master + slaves
    unassigned_clients: list    # list[ClientDevice] - no known parent mesh node
    raw: dict = field(default_factory=dict)


def _find_parent_link(node_uid: str, all_nodes_by_uid: dict) -> tuple[Optional[str], str, str, str]:
    """
    Find which mesh node (master/slave) a given node is connected to.
    Returns (parent_uid, link_type, link_state, interface_name).

    We look at all links in the topology and find one where:
    - this node is node_2 (client side)
    - node_1 is a mesh node (master or slave)
    or vice versa.
    """
    node = all_nodes_by_uid.get(node_uid)
    if not node:
        return None, "", "", ""

    raw_node = node.get("_raw", {})
    for iface in raw_node.get("node_interfaces", []):
        for link in iface.get("node_links", []):
            if link.get("state") != "CONNECTED":
                continue

            n1 = link.get("node_1_uid", "")
            n2 = link.get("node_2_uid", "")
            link_type = iface.get("type", "")
            iface_name = iface.get("name", "")

            # Determine if the other end is a mesh node
            other_uid = n1 if n2 == node_uid else n2 if n1 == node_uid else None
            if other_uid and other_uid in all_nodes_by_uid:
                other = all_nodes_by_uid[other_uid]
                if other.get("is_meshed", False):
                    return other_uid, link_type, link.get("state", ""), iface_name

    return None, "", "", ""


def parse_mesh_topology(raw: dict) -> MeshTopology:
    """
    Parse the raw mesh JSON from Fritz!Box into a structured MeshTopology.

    The algorithm:
    1. Build a map of all nodes by uid.
    2. Separate mesh nodes (is_meshed=True) from client nodes.
    3. For each client node, walk its interface links to find which mesh node
       it is connected to, and classify the connection type (WLAN/LAN).
    4. For each slave mesh node, find its parent (master or another slave).
    """
    schema_version = raw.get("schema_version", "unknown")
    raw_nodes = raw.get("nodes", [])

    # Index all nodes by uid and attach raw data
    all_nodes_by_uid: dict[str, dict] = {}
    for node in raw_nodes:
        uid = node.get("uid", "")
        entry = {
            "uid": uid,
            "name": node.get("device_name", uid),
            "mac": node.get("device_mac_address", ""),
            "model": node.get("device_model", ""),
            "vendor": node.get("device_manufacturer", ""),
            "firmware": node.get("device_firmware_version", ""),
            "is_meshed": node.get("is_meshed", False),
            "mesh_role": node.get("mesh_role", "unknown"),
            "_raw": node,
        }
        all_nodes_by_uid[uid] = entry

    # Build link map: node_uid -> list of (other_uid, link_info, iface_info)
    # We need this to find connected clients for each mesh node
    mesh_node_clients: dict[str, list[ClientDevice]] = {}
    mesh_node_parent: dict[str, tuple] = {}

    # Build MeshNode objects for mesh nodes
    mesh_nodes_by_uid: dict[str, MeshNode] = {}
    for uid, entry in all_nodes_by_uid.items():
        if entry["is_meshed"]:
            mesh_node = MeshNode(
                uid=uid,
                name=entry["name"],
                mac=entry["mac"],
                role=entry["mesh_role"],
                model=entry["model"],
                vendor=entry["vendor"],
                firmware=entry["firmware"],
                is_meshed=True,
            )
            mesh_nodes_by_uid[uid] = mesh_node
            mesh_node_clients[uid] = []

    # Walk all links to assign clients and parent relationships
    for uid, entry in all_nodes_by_uid.items():
        raw_node = entry["_raw"]
        for iface in raw_node.get("node_interfaces", []):
            iface_type = iface.get("type", "")
            iface_name = iface.get("name", "")
            for link in iface.get("node_links", []):
                n1 = link.get("node_1_uid", "")
                n2 = link.get("node_2_uid", "")
                state = link.get("state", "DISCONNECTED")
                cur_rx = link.get("cur_data_rate_rx", 0)
                cur_tx = link.get("cur_data_rate_tx", 0)
                max_rx = link.get("max_data_rate_rx", 0)
                max_tx = link.get("max_data_rate_tx", 0)

                # We only process links once (from node_1's perspective)
                if n1 != uid:
                    continue

                n2_entry = all_nodes_by_uid.get(n2)
                if not n2_entry:
                    continue

                n1_is_mesh = entry["is_meshed"]
                n2_is_mesh = n2_entry["is_meshed"]

                if n1_is_mesh and not n2_is_mesh:
                    # n1 (mesh node) -> n2 (client): add client to mesh node
                    client = ClientDevice(
                        uid=n2,
                        name=n2_entry["name"],
                        mac=n2_entry["mac"],
                        connection_type=iface_type,
                        connection_state=state,
                        cur_rx_kbps=cur_rx,
                        cur_tx_kbps=cur_tx,
                        max_rx_kbps=max_rx,
                        max_tx_kbps=max_tx,
                        interface_name=iface_name,
                    )
                    if n1 in mesh_node_clients:
                        mesh_node_clients[n1].append(client)

                elif n1_is_mesh and n2_is_mesh:
                    # mesh-to-mesh link: slave -> master relationship
                    # The slave node (n2) has n1 as parent, or vice versa
                    # master is role "master", slave is role "slave"
                    n1_role = entry["mesh_role"]
                    n2_role = n2_entry["mesh_role"]

                    if n1_role == "master" and n2_role == "slave":
                        # n2 slave's parent is n1 master
                        if n2 not in mesh_node_parent:
                            mesh_node_parent[n2] = (n1, iface_type, state, iface_name)
                    elif n1_role == "slave" and n2_role == "master":
                        # n1 slave's parent is n2 master
                        if n1 not in mesh_node_parent:
                            mesh_node_parent[n1] = (n2, iface_type, state, iface_name)
                    elif n1_role == "slave" and n2_role == "slave":
                        # daisy-chained slaves: lower uid is "closer to master"
                        # just record for n2 that n1 is upstream
                        if n2 not in mesh_node_parent:
                            mesh_node_parent[n2] = (n1, iface_type, state, iface_name)

    # Assign clients and parent info to mesh nodes
    for uid, clients in mesh_node_clients.items():
        if uid in mesh_nodes_by_uid:
            mesh_nodes_by_uid[uid].clients = clients

    for uid, (parent_uid, link_type, link_state, iface_name) in mesh_node_parent.items():
        if uid in mesh_nodes_by_uid:
            mesh_nodes_by_uid[uid].parent_uid = parent_uid
            mesh_nodes_by_uid[uid].parent_link_type = link_type
            mesh_nodes_by_uid[uid].parent_link_state = link_state
            mesh_nodes_by_uid[uid].parent_interface_name = iface_name

    # Collect client nodes that are NOT linked to any mesh node (unassigned)
    assigned_client_uids = set()
    for clients in mesh_node_clients.values():
        for c in clients:
            assigned_client_uids.add(c.uid)

    unassigned: list[ClientDevice] = []
    for uid, entry in all_nodes_by_uid.items():
        if not entry["is_meshed"] and uid not in assigned_client_uids:
            unassigned.append(ClientDevice(
                uid=uid,
                name=entry["name"],
                mac=entry["mac"],
                connection_type="",
                connection_state="unknown",
            ))

    mesh_nodes = list(mesh_nodes_by_uid.values())
    # Sort: master first, then slaves
    mesh_nodes.sort(key=lambda n: (0 if n.role == "master" else 1, n.name))

    return MeshTopology(
        schema_version=schema_version,
        mesh_nodes=mesh_nodes,
        unassigned_clients=unassigned,
        raw=raw,
    )


def enrich_with_host_info(topology: MeshTopology, hosts_info: list[dict]) -> None:
    """
    Cross-reference client devices with FritzHosts data to add IP addresses
    and active status. hosts_info is the list from FritzHosts.get_hosts_info().
    """
    mac_to_host: dict[str, dict] = {}
    for host in hosts_info:
        mac = host.get("mac", "").upper()
        if mac:
            mac_to_host[mac] = host

    def _enrich_client(client: ClientDevice) -> None:
        host = mac_to_host.get(client.mac.upper())
        if host:
            client.ip = host.get("ip")
            # Prefer the hostname from hosts list if our name looks like a uid
            name_from_host = host.get("name", "")
            if name_from_host and (not client.name or len(client.name) > 20):
                client.name = name_from_host

    for mesh_node in topology.mesh_nodes:
        for client in mesh_node.clients:
            _enrich_client(client)
    for client in topology.unassigned_clients:
        _enrich_client(client)


class FritzMeshFetcher:
    """High-level fetcher: connects to Fritz!Box and returns parsed topology."""

    def __init__(
        self,
        address: str,
        port: int = 49000,
        user: str = "",
        password: str = "",
        use_tls: bool = False,
        timeout: int = 10,
    ):
        self.address = address
        self.port = port
        self.user = user
        self.password = password
        self.use_tls = use_tls
        self.timeout = timeout
        self._fc: Optional[FritzConnection] = None

    def _connect(self) -> FritzConnection:
        if self._fc is None:
            logger.info("Connecting to Fritz!Box at %s:%s", self.address, self.port)
            self._fc = FritzConnection(
                address=self.address,
                port=self.port,
                user=self.user,
                password=self.password,
                use_tls=self.use_tls,
                timeout=self.timeout,
            )
        return self._fc

    def fetch(self) -> MeshTopology:
        """Connect to Fritz!Box and return parsed mesh topology."""
        fc = self._connect()
        fh = FritzHosts(fc=fc)

        logger.info("Fetching mesh topology...")
        raw = fh.get_mesh_topology(raw=False)

        topology = parse_mesh_topology(raw)

        try:
            logger.info("Fetching host list for IP enrichment...")
            hosts_info = fh.get_hosts_info()
            enrich_with_host_info(topology, hosts_info)
        except Exception as e:
            logger.warning("Could not fetch host list: %s", e)

        logger.info(
            "Topology: %d mesh nodes, schema %s",
            len(topology.mesh_nodes),
            topology.schema_version,
        )
        return topology

    def to_dict(self, topology: MeshTopology) -> dict:
        """Serialize a MeshTopology to a JSON-friendly dict."""
        def client_to_dict(c: ClientDevice) -> dict:
            return {
                "uid": c.uid,
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

        def mesh_node_to_dict(n: MeshNode) -> dict:
            return {
                "uid": n.uid,
                "name": n.name,
                "mac": n.mac,
                "role": n.role,
                "model": n.model,
                "vendor": n.vendor,
                "firmware": n.firmware,
                "parent_uid": n.parent_uid,
                "parent_link_type": n.parent_link_type,
                "parent_link_state": n.parent_link_state,
                "parent_interface_name": n.parent_interface_name,
                "clients": [client_to_dict(c) for c in n.clients],
            }

        return {
            "schema_version": topology.schema_version,
            "mesh_nodes": [mesh_node_to_dict(n) for n in topology.mesh_nodes],
            "unassigned_clients": [client_to_dict(c) for c in topology.unassigned_clients],
        }
