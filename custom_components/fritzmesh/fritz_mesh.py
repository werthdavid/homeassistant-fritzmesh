"""
Fritz!Box mesh topology fetcher using fritzconnection.

Overview
────────
This module is the core data layer of the integration.  It has three
responsibilities:

  1. **Data classes** – Plain Python dataclasses that model the mesh network:
       ClientDevice   – a non-mesh device (phone, laptop, smart-TV, …)
       MeshNode       – a Fritz!Box or repeater that participates in the mesh
       MeshTopology   – the root container holding all nodes and clients

  2. **Parsing** – parse_mesh_topology() converts the raw JSON dict returned
     by fritzconnection into the typed dataclass tree above.  The Fritz!Box
     JSON uses a graph structure (nodes + interfaces + links); this function
     walks that graph and builds an easy-to-consume object model.

  3. **Fetching** – FritzMeshFetcher wraps fritzconnection, calls the
     TR-064/UPnP service, and returns a fully parsed MeshTopology.

Fritz!Box mesh topology JSON structure (simplified)
────────────────────────────────────────────────────
  {
    "schema_version": "1.8",
    "nodes": [
      {
        "uid": "landevice1234",
        "device_name": "FRITZ!Box 7590",
        "device_mac_address": "AA:BB:CC:DD:EE:FF",
        "is_meshed": true,
        "mesh_role": "master",        // "master" | "slave" | "unknown"
        "node_interfaces": [
          {
            "uid": "iface-xyz",
            "name": "AP:5G:0",         // interface name encoding band/type
            "type": "WLAN",            // "WLAN" | "LAN"
            "node_links": [
              {
                "uid": "link-abc",
                "state": "CONNECTED",  // "CONNECTED" | "DISCONNECTED"
                "node_1_uid": "landevice1234",
                "node_2_uid": "landevice5678",
                "cur_data_rate_rx": 144400,   // kbit/s
                "cur_data_rate_tx": 86700,
                "max_data_rate_rx": 300000,
                "max_data_rate_tx": 300000
              }
            ]
          }
        ]
      }
    ]
  }

The key insight: every connection between two network entities (mesh node ↔
mesh node, or mesh node ↔ client) is represented as a *link* inside the
interface of one of the nodes.  We walk every node's interfaces and their
links to reconstruct who is connected to whom.
"""

import logging
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fritzconnection.lib.fritzhosts import FritzHosts
from fritzconnection.core.fritzconnection import FritzConnection

logger = logging.getLogger(__name__)


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class ClientDevice:
    """A non-mesh network device connected to the Fritz!Box network.

    Represents laptops, phones, smart-TVs, IoT gadgets, etc. – anything
    that does NOT have the `is_meshed` flag set in the Fritz!Box JSON.

    Speed fields are in kbit/s.  0 means the Fritz!Box didn't report a value
    (common for LAN devices or disconnected clients).

    Attributes:
        uid:              Raw UID string from the Fritz!Box JSON (e.g. "landevice1234").
        name:             Human-readable hostname; may be enriched later from FritzHosts.
        mac:              MAC address in upper-case colon notation ("AA:BB:CC:DD:EE:FF").
        connection_type:  "WLAN" for wireless, "LAN" for wired, "" if unknown.
        connection_state: "CONNECTED" or "DISCONNECTED" (or "unknown" for unassigned).
        ip:               IPv4 address, filled in by enrich_with_host_info(). None if unknown.
        cur_rx_kbps:      Current receive throughput in kbit/s.
        cur_tx_kbps:      Current transmit throughput in kbit/s.
        max_rx_kbps:      Negotiated (maximum possible) receive speed in kbit/s.
        max_tx_kbps:      Negotiated (maximum possible) transmit speed in kbit/s.
        interface_name:   Raw interface name from Fritz!Box (e.g. "AP:5G:0", "LAN:1").
                          The Lovelace card parses this to derive the WiFi band.
    """
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
    interface_name: str = ""    # e.g. "AP:5G:0" → 5 GHz WiFi, "LAN:1" → first LAN port


@dataclass
class MeshNode:
    """A Fritz!Box device that participates in the mesh network.

    This includes the master router AND any repeaters (slaves).  Every mesh
    node can have clients attached to it and, for slaves, a reference to the
    upstream node it is connected to (its "parent").

    Attributes:
        uid:                   Raw UID from the Fritz!Box JSON.
        name:                  Device name (e.g. "FRITZ!Box 7590 AX").
        mac:                   MAC address; used as a stable unique identifier.
        role:                  "master" for the primary router, "slave" for repeaters,
                               "unknown" when the Fritz!Box doesn't report a role.
        model:                 Hardware model string (e.g. "FRITZ!Repeater 2400").
        vendor:                Manufacturer name (almost always "AVM").
        firmware:              Firmware version string.
        is_meshed:             Always True for MeshNode objects (False entries become
                               ClientDevice objects during parsing).
        clients:               List of ClientDevice objects directly connected to
                               this mesh node.
        parent_uid:            UID of the upstream mesh node this slave is linked to.
                               None for the master node or unlinked slaves.
        parent_link_type:      "WLAN" or "LAN" – how this slave reaches its parent.
        parent_link_state:     "CONNECTED" or "DISCONNECTED" for the uplink.
        parent_interface_name: Raw interface name of the uplink connection.
    """
    uid: str
    name: str
    mac: str
    role: str                   # "master", "slave", "unknown"
    model: str = ""
    vendor: str = ""
    firmware: str = ""
    ip: Optional[str] = None
    is_meshed: bool = True
    clients: list = field(default_factory=list)  # list[ClientDevice]
    # Uplink information (only relevant for slave nodes)
    parent_uid: Optional[str] = None
    parent_link_type: str = ""
    parent_link_state: str = ""
    parent_interface_name: str = ""
    parent_cur_rx_kbps: int = 0
    parent_cur_tx_kbps: int = 0
    parent_max_rx_kbps: int = 0
    parent_max_tx_kbps: int = 0


@dataclass
class MeshTopology:
    """Root container for a complete Fritz!Box mesh snapshot.

    Attributes:
        schema_version:    The Fritz!Box JSON schema version string (e.g. "1.8").
                           Tracked for future compatibility checks.
        mesh_nodes:        Ordered list of MeshNode objects; master is always first,
                           then slaves alphabetically.
        unassigned_clients: ClientDevice objects seen in the topology JSON but not
                            linked to any mesh node (can happen with stale entries).
        raw:               The original unprocessed dict from the Fritz!Box; kept
                           here for debugging but not normally used by HA entities.
    """
    schema_version: str
    mesh_nodes: list            # list[MeshNode] – master first, then slaves
    unassigned_clients: list    # list[ClientDevice] – no known parent mesh node
    raw: dict = field(default_factory=dict)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _find_parent_link(node_uid: str, all_nodes_by_uid: dict) -> tuple[Optional[str], str, str, str]:
    """Find which mesh node (master/slave) a given node is connected to.

    Walks the node's own interface links to find one where the other endpoint
    (`node_1_uid` or `node_2_uid`) belongs to a mesh node that is in
    `all_nodes_by_uid`.

    This function is a helper kept for reference; the main parse_mesh_topology
    function handles parent-relationship assignment inline.

    Args:
        node_uid:         UID of the node whose parent we want to find.
        all_nodes_by_uid: Dict mapping UID → raw node entry (including `_raw`).

    Returns:
        A 4-tuple:
          (parent_uid, link_type, link_state, interface_name)
        All strings are empty and parent_uid is None when no parent is found.
    """
    node = all_nodes_by_uid.get(node_uid)
    if not node:
        return None, "", "", ""

    raw_node = node.get("_raw", {})
    for iface in raw_node.get("node_interfaces", []):
        for link in iface.get("node_links", []):
            # Only consider active connections; ignore stale/disconnected links.
            if link.get("state") != "CONNECTED":
                continue

            n1 = link.get("node_1_uid", "")
            n2 = link.get("node_2_uid", "")
            link_type  = iface.get("type", "")    # "WLAN" or "LAN"
            iface_name = iface.get("name", "")    # e.g. "AP:5G:0"

            # Determine which end of the link is the *other* node.
            other_uid = n1 if n2 == node_uid else n2 if n1 == node_uid else None
            if other_uid and other_uid in all_nodes_by_uid:
                other = all_nodes_by_uid[other_uid]
                # Only a mesh node can be a parent.
                if other.get("is_meshed", False):
                    return other_uid, link_type, link.get("state", ""), iface_name

    return None, "", "", ""


def _extract_primary_ipv4(ip_addresses: list[dict] | None) -> Optional[str]:
    """Extract the first IPv4 address (without CIDR suffix) from node ip list."""
    if not ip_addresses:
        return None
    for ip_entry in ip_addresses:
        if ip_entry.get("version") != "V4":
            continue
        value = ip_entry.get("value", "")
        if not value:
            continue
        return value.split("/", 1)[0]
    return None


# ── Main parser ───────────────────────────────────────────────────────────────

def parse_mesh_topology(raw: dict) -> MeshTopology:
    """Parse the raw mesh JSON from Fritz!Box into a structured MeshTopology.

    Algorithm overview
    ──────────────────
    Pass 1 – Index all nodes by UID.
        Build a flat dict of every node entry (mesh nodes AND client nodes),
        attaching the original JSON as `_raw` so later passes can inspect it.

    Pass 2 – Create MeshNode objects for mesh nodes only.
        Nodes with `is_meshed=True` become MeshNode instances; non-mesh nodes
        will become ClientDevice instances in the next pass.

    Pass 3 – Walk all links to assign clients and parent relationships.
        For every node, iterate over its interfaces and their links.  Each
        link connects exactly two nodes (node_1_uid ↔ node_2_uid).  We only
        process links where the current node is node_1 (to avoid double-
        counting) and classify each link as one of:
          • mesh-node → client  : add a ClientDevice to the mesh node's list
          • mesh-node → mesh-node : record the slave→master or slave→slave
                                    parent relationship

    Pass 4 – Collect unassigned clients.
        Any non-mesh node that was never added to a mesh node's client list
        is placed in `unassigned_clients`.

    Args:
        raw: The dict returned by FritzHosts.get_mesh_topology(raw=False).
             Top-level keys: "schema_version", "nodes".

    Returns:
        A fully populated MeshTopology instance.
    """
    schema_version = raw.get("schema_version", "unknown")
    raw_nodes = raw.get("nodes", [])

    # ── Pass 1: Index all nodes by UID ──────────────────────────────────────
    # We store a lightweight summary dict plus the full raw JSON under "_raw"
    # so that later passes don't need to re-scan the original list.
    all_nodes_by_uid: dict[str, dict] = {}
    for node in raw_nodes:
        uid = node.get("uid", "")
        entry = {
            "uid": uid,
            "name": node.get("device_name", uid),
            "mac": node.get("device_mac_address", ""),
            "ip": _extract_primary_ipv4(node.get("ip_addresses")),
            "model": node.get("device_model", ""),
            "vendor": node.get("device_manufacturer", ""),
            "firmware": node.get("device_firmware_version", ""),
            "is_meshed": node.get("is_meshed", False),
            "mesh_role": node.get("mesh_role", "unknown"),
            "_raw": node,   # keep the full original for interface/link traversal
        }
        all_nodes_by_uid[uid] = entry

    # Intermediate accumulators filled in during the link-walking pass below.
    # mesh_node_clients maps mesh-node UID → list of ClientDevice objects.
    # mesh_node_parent  maps slave UID → (parent_uid, link_type, state, iface_name).
    mesh_node_clients: dict[str, list[ClientDevice]] = {}
    mesh_node_parent:  dict[str, tuple] = {}

    # ── Pass 2: Create MeshNode stubs for is_meshed nodes ───────────────────
    mesh_nodes_by_uid: dict[str, MeshNode] = {}
    for uid, entry in all_nodes_by_uid.items():
        if entry["is_meshed"]:
            mesh_node = MeshNode(
                uid=uid,
                name=entry["name"],
                mac=entry["mac"],
                ip=entry["ip"],
                role=entry["mesh_role"],
                model=entry["model"],
                vendor=entry["vendor"],
                firmware=entry["firmware"],
                is_meshed=True,
                # clients and parent info filled in pass 3
            )
            mesh_nodes_by_uid[uid] = mesh_node
            mesh_node_clients[uid] = []  # start with an empty client list

    # ── Pass 3: Walk all links ───────────────────────────────────────────────
    for uid, entry in all_nodes_by_uid.items():
        raw_node = entry["_raw"]
        for iface in raw_node.get("node_interfaces", []):
            iface_type = iface.get("type", "")    # "WLAN" | "LAN"
            iface_name = iface.get("name", "")    # e.g. "AP:5G:0"
            for link in iface.get("node_links", []):
                n1 = link.get("node_1_uid", "")
                n2 = link.get("node_2_uid", "")
                state  = link.get("state", "DISCONNECTED")
                cur_rx = link.get("cur_data_rate_rx", 0)  # kbit/s
                cur_tx = link.get("cur_data_rate_tx", 0)
                max_rx = link.get("max_data_rate_rx", 0)
                max_tx = link.get("max_data_rate_tx", 0)

                # Process each link only once, from node_1's perspective.
                # Since both endpoints' raw JSON usually contains the same link,
                # filtering on n1==uid prevents duplicate entries.
                if n1 != uid:
                    continue

                n2_entry = all_nodes_by_uid.get(n2)
                if not n2_entry:
                    # n2 UID not found in our index – skip this orphaned link.
                    continue

                n1_is_mesh = entry["is_meshed"]
                n2_is_mesh = n2_entry["is_meshed"]

                if n1_is_mesh and not n2_is_mesh:
                    # ── Case A: mesh node → non-mesh client ─────────────────
                    # Create a ClientDevice and attach it to the mesh node.
                    # The interface type and name come from the mesh node's
                    # interface (iface_type / iface_name), telling us whether
                    # the client is on WiFi or a wired port.
                    client = ClientDevice(
                        uid=n2,
                        name=n2_entry["name"],
                        mac=n2_entry["mac"],
                        connection_type=iface_type,    # "WLAN" or "LAN"
                        connection_state=state,         # "CONNECTED" / "DISCONNECTED"
                        cur_rx_kbps=cur_rx,
                        cur_tx_kbps=cur_tx,
                        max_rx_kbps=max_rx,
                        max_tx_kbps=max_tx,
                        interface_name=iface_name,     # band info encoded here
                    )
                    if n1 in mesh_node_clients:
                        mesh_node_clients[n1].append(client)

                elif n1_is_mesh and n2_is_mesh:
                    # ── Case B: mesh node → mesh node (backbone link) ────────
                    # Determine which end is master and which is slave, then
                    # record the slave's parent.
                    n1_role = entry["mesh_role"]
                    n2_role = n2_entry["mesh_role"]

                    if n1_role == "master" and n2_role == "slave":
                        # n1 (master) is the parent of n2 (slave).
                        # Only record the first (most authoritative) link found.
                        if n2 not in mesh_node_parent:
                            mesh_node_parent[n2] = (
                                n1, iface_type, state, iface_name, cur_rx, cur_tx, max_rx, max_tx
                            )

                    elif n1_role == "slave" and n2_role == "master":
                        # n2 (master) is the parent of n1 (slave).
                        if n1 not in mesh_node_parent:
                            mesh_node_parent[n1] = (
                                n2, iface_type, state, iface_name, cur_rx, cur_tx, max_rx, max_tx
                            )

                    elif n1_role == "slave" and n2_role == "slave":
                        # Daisy-chained slaves: infer direction from interface
                        # naming when possible. "UPLINK:*" belongs to the
                        # downstream node, so n1 is child of n2 in that case.
                        iface_name_u = iface_name.upper()
                        if "UPLINK" in iface_name_u:
                            if n1 not in mesh_node_parent:
                                mesh_node_parent[n1] = (
                                    n2, iface_type, state, iface_name, cur_rx, cur_tx, max_rx, max_tx
                                )
                        else:
                            # Fallback heuristic when interface naming is not
                            # informative: keep previous behaviour.
                            if n2 not in mesh_node_parent:
                                mesh_node_parent[n2] = (
                                    n1, iface_type, state, iface_name, cur_rx, cur_tx, max_rx, max_tx
                                )

    # ── Assign accumulated data back to MeshNode objects ────────────────────

    # Attach the client lists discovered in pass 3.
    for uid, clients in mesh_node_clients.items():
        if uid in mesh_nodes_by_uid:
            mesh_nodes_by_uid[uid].clients = clients

    # Attach parent-relationship info to slave nodes.
    for uid, (
        parent_uid,
        link_type,
        link_state,
        iface_name,
        cur_rx,
        cur_tx,
        max_rx,
        max_tx,
    ) in mesh_node_parent.items():
        if uid in mesh_nodes_by_uid:
            mesh_nodes_by_uid[uid].parent_uid            = parent_uid
            mesh_nodes_by_uid[uid].parent_link_type      = link_type
            mesh_nodes_by_uid[uid].parent_link_state     = link_state
            mesh_nodes_by_uid[uid].parent_interface_name = iface_name
            mesh_nodes_by_uid[uid].parent_cur_rx_kbps    = cur_rx
            mesh_nodes_by_uid[uid].parent_cur_tx_kbps    = cur_tx
            mesh_nodes_by_uid[uid].parent_max_rx_kbps    = max_rx
            mesh_nodes_by_uid[uid].parent_max_tx_kbps    = max_tx

    # ── Pass 4: Collect unassigned clients ───────────────────────────────────
    # Build a set of all client UIDs that were successfully assigned to a
    # mesh node so we can identify the leftovers.
    assigned_client_uids = set()
    for clients in mesh_node_clients.values():
        for c in clients:
            assigned_client_uids.add(c.uid)

    # Any non-mesh node not in the assigned set becomes an unassigned client.
    # This can happen for recently-disconnected devices that are still tracked
    # by the Fritz!Box but whose link entry has vanished.
    unassigned: list[ClientDevice] = []
    for uid, entry in all_nodes_by_uid.items():
        if not entry["is_meshed"] and uid not in assigned_client_uids:
            unassigned.append(ClientDevice(
                uid=uid,
                name=entry["name"],
                mac=entry["mac"],
                connection_type="",
                connection_state="unknown",
                # Speed and IP are unavailable for unassigned clients
            ))

    # Sort mesh nodes: master first (role == "master"), then slaves by name.
    mesh_nodes = list(mesh_nodes_by_uid.values())
    mesh_nodes.sort(key=lambda n: (0 if n.role == "master" else 1, n.name))

    return MeshTopology(
        schema_version=schema_version,
        mesh_nodes=mesh_nodes,
        unassigned_clients=unassigned,
        raw=raw,
    )


def load_mesh_topology_from_json_file(path: str, config_dir: str | None = None) -> MeshTopology:
    """Load a mesh topology JSON file and parse it to MeshTopology.

    If `path` is relative and `config_dir` is provided, the file is resolved
    against that directory (Home Assistant config dir).
    """
    candidate = Path(path).expanduser()
    if not candidate.is_absolute() and config_dir:
        candidate = Path(config_dir) / candidate
    candidate = candidate.resolve()

    raw_text = candidate.read_text(encoding="utf-8")
    raw = json.loads(raw_text)
    if not isinstance(raw, dict):
        raise ValueError(f"Debug topology JSON must be an object, got {type(raw).__name__}")
    return parse_mesh_topology(raw)


# ── Host-list enrichment ──────────────────────────────────────────────────────

def enrich_with_host_info(topology: MeshTopology, hosts_info: list[dict]) -> None:
    """Cross-reference client devices with FritzHosts data to add IPs and better names.

    The mesh topology JSON from Fritz!Box sometimes contains only UIDs or
    truncated device names.  The FritzHosts service (a separate TR-064 call)
    provides a richer host table with IP addresses, MAC addresses, and the
    hostnames that appear in the Fritz!Box UI.

    This function mutates ClientDevice objects in-place; it does not create
    new objects or return anything.

    Args:
        topology:   The MeshTopology to enrich (modified in-place).
        hosts_info: List of host dicts from FritzHosts.get_hosts_info().
                    Each dict has at least: "mac", "ip", "name".
    """
    # Build a MAC → host-info lookup for O(1) access per client.
    # Normalise MAC to upper-case to match the format stored on ClientDevice.
    mac_to_host: dict[str, dict] = {}
    for host in hosts_info:
        mac = host.get("mac", "").upper()
        if mac:
            mac_to_host[mac] = host

    def _enrich_client(client: ClientDevice) -> None:
        """Apply host-info enrichment to a single ClientDevice (in-place)."""
        host = mac_to_host.get(client.mac.upper())
        if host:
            # Always overwrite IP since the topology JSON rarely includes it.
            client.ip = host.get("ip")

            # Prefer the hostname from the hosts list when our current name
            # looks like a raw UID (very long) or is missing entirely.
            name_from_host = host.get("name", "")
            if name_from_host and (not client.name or len(client.name) > 20):
                client.name = name_from_host

    # Enrich clients that are connected to a known mesh node.
    for mesh_node in topology.mesh_nodes:
        for client in mesh_node.clients:
            _enrich_client(client)

    # Also enrich unassigned clients (they appear in the topology JSON
    # but couldn't be mapped to a specific mesh node).
    for client in topology.unassigned_clients:
        _enrich_client(client)


# ── Fetcher class ─────────────────────────────────────────────────────────────

class FritzMeshFetcher:
    """High-level fetcher: connects to a Fritz!Box and returns a parsed MeshTopology.

    Uses fritzconnection (a third-party library) to speak the TR-064 SOAP
    protocol over HTTP(S) to the Fritz!Box.  All I/O is blocking/synchronous;
    callers in async contexts (e.g. the coordinator) must run this in an
    executor thread.

    The internal FritzConnection instance is lazily created and then cached
    across calls, which avoids the overhead of re-establishing the connection
    on every poll cycle.
    """

    def __init__(
        self,
        address: str,
        port: int = 49000,
        user: str = "",
        password: str = "",
        use_tls: bool = False,
        timeout: int = 10,
    ):
        """Initialise the fetcher with connection parameters.

        Args:
            address:  Hostname or IP of the Fritz!Box (e.g. "192.168.178.1").
            port:     TR-064 port; 49000 for HTTP, 49443 for HTTPS.
            user:     Fritz!Box web-UI username.  May be empty.
            password: Fritz!Box web-UI password.  May be empty.
            use_tls:  True to use HTTPS, False to use plain HTTP.
            timeout:  Socket timeout in seconds for each SOAP call.
        """
        self.address  = address
        self.port     = port
        self.user     = user
        self.password = password
        self.use_tls  = use_tls
        self.timeout  = timeout
        # Lazily created in _connect(); cached here to reuse across poll cycles.
        self._fc: Optional[FritzConnection] = None

    def _connect(self) -> FritzConnection:
        """Return (or create) the FritzConnection singleton.

        The connection is created on the first call and reused on subsequent
        calls.  fritzconnection fetches the TR-064 service description XML
        on first connect; caching avoids repeating that HTTP round-trip.

        Returns:
            An initialised FritzConnection ready for SOAP calls.
        """
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
        """Connect to Fritz!Box and return the fully parsed mesh topology.

        Execution order:
          1. Obtain (or reuse) a FritzConnection.
          2. Create a FritzHosts helper bound to that connection.
          3. Fetch the raw mesh topology JSON via the Hosts:1 TR-064 service.
          4. Parse it into the MeshTopology dataclass tree.
          5. Attempt to enrich clients with IP/hostname from the hosts list.
             This step is non-fatal; a warning is logged on failure.

        Returns:
            A fully populated MeshTopology.

        Raises:
            Any exception raised by fritzconnection (network errors,
            authentication failures, etc.) propagates to the caller.
        """
        fc = self._connect()

        # FritzHosts wraps the Hosts:1 TR-064 service and provides
        # convenience methods like get_mesh_topology() and get_hosts_info().
        fh = FritzHosts(fc=fc)

        logger.info("Fetching mesh topology...")
        # raw=False means fritzconnection parses the SOAP response XML into a
        # Python dict for us.  raw=True would return the raw XML string.
        raw = fh.get_mesh_topology(raw=False)

        topology = parse_mesh_topology(raw)

        try:
            logger.info("Fetching host list for IP enrichment...")
            # get_hosts_info() calls X_AVM-DE_GetHostListPath and returns a
            # list of dicts with keys: "mac", "ip", "name", "status", etc.
            hosts_info = fh.get_hosts_info()
            enrich_with_host_info(topology, hosts_info)
        except Exception as e:
            # IP enrichment is a best-effort step; the topology itself is
            # still useful without it.
            logger.warning("Could not fetch host list: %s", e)

        logger.info(
            "Topology: %d mesh nodes, schema %s",
            len(topology.mesh_nodes),
            topology.schema_version,
        )
        return topology

    def to_dict(self, topology: MeshTopology) -> dict:
        """Serialise a MeshTopology to a JSON-friendly dict.

        Converts all dataclass instances to plain dicts so the result can be
        stored as Home Assistant entity attributes (which must be JSON-
        serialisable) or written to a file for debugging.

        Args:
            topology: The MeshTopology to serialise.

        Returns:
            A dict with keys "schema_version", "mesh_nodes", and
            "unassigned_clients".  All values are JSON-safe primitives.
        """
        def client_to_dict(c: ClientDevice) -> dict:
            """Convert a ClientDevice to a plain dict."""
            return {
                "uid":              c.uid,
                "name":             c.name,
                "mac":              c.mac,
                "ip":               c.ip,
                "connection_type":  c.connection_type,
                "connection_state": c.connection_state,
                "interface_name":   c.interface_name,
                "cur_rx_kbps":      c.cur_rx_kbps,
                "cur_tx_kbps":      c.cur_tx_kbps,
                "max_rx_kbps":      c.max_rx_kbps,
                "max_tx_kbps":      c.max_tx_kbps,
            }

        def mesh_node_to_dict(n: MeshNode) -> dict:
            """Convert a MeshNode (including its clients) to a plain dict."""
            return {
                "uid":                  n.uid,
                "name":                 n.name,
                "mac":                  n.mac,
                "role":                 n.role,
                "model":                n.model,
                "vendor":               n.vendor,
                "firmware":             n.firmware,
                "parent_uid":           n.parent_uid,
                "parent_link_type":     n.parent_link_type,
                "parent_link_state":    n.parent_link_state,
                "parent_interface_name": n.parent_interface_name,
                "clients":              [client_to_dict(c) for c in n.clients],
            }

        return {
            "schema_version":    topology.schema_version,
            "mesh_nodes":        [mesh_node_to_dict(n) for n in topology.mesh_nodes],
            "unassigned_clients": [client_to_dict(c) for c in topology.unassigned_clients],
        }
