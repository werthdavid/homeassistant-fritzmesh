/**
 * Fritz!Box Mesh Topology Card  –  v1.1.0
 *
 * A custom Lovelace card that visualises the Fritz!Box mesh network as a
 * hierarchical tree diagram:
 *
 *   ┌──────────┐        Clients
 *   │ Fritz!Box│ ────── ≡ Laptop (WiFi 5 GHz → 867 Mbit/s)
 *   │  7590 AX │ ────── ≡ Desktop (LAN → 1 Gbit/s)
 *   └──────────┘
 *        │
 *    ....│ WiFi
 *   ┌──────────┐        Clients
 *   │ Repeater │ ────── ≡ Phone (WiFi 2.4 GHz → 144 Mbit/s)
 *   └──────────┘
 *
 * Visual conventions:
 *   Solid green line  = LAN connection
 *   Dashed green line = WiFi connection
 *   Reduced opacity   = device is disconnected
 *
 * Card YAML configuration:
 *   type: custom:fritzmesh-card
 *   entity: sensor.fritzmesh_topology   # required – the FritzMeshTopologySensor
 *   title: Fritz!Box Mesh               # optional; omit to use default title,
 *                                       # set to "" to hide the header entirely
 *   hide_offline_nodes: true            # optional; hide repeaters with
 *                                       # disconnected uplinks
 *
 * Data flow:
 *   Home Assistant state machine
 *     → set hass()          (called on every HA state update)
 *       → _render()         (only if topology attributes changed)
 *         → _masterPanel()  (left sticky panel: router icon + device info)
 *         → _masterSection() (direct clients of the master)
 *         → _slaveSection()  (one section per repeater + its clients)
 *         → _clientRow()     (individual device rows with speed/band label)
 */

const CARD_VERSION = "1.9.2";

// Top-level guard: runs the instant the script is parsed, before any class
// or constant definition. Visible in console at "Info" level.
console.info("[fritzmesh-card] script executing, version", CARD_VERSION,
  "| already defined:", !!customElements.get("fritzmesh-card"));

// ── Inline SVG icons ──────────────────────────────────────────────────────────
//
// All icons are Material Design SVGs embedded as strings.  Using inline SVGs
// avoids any dependency on external icon libraries (like mdi) that might not
// be available when the card is first loaded.
//
// Each icon is stored as a string of raw <svg> markup so it can be inserted
// directly into innerHTML without escaping.

const ICON = {
  // Router icon – used for the master Fritz!Box panel on the left.
  router: `<svg viewBox="0 0 24 24" fill="currentColor">
    <path d="M20.2 5.9l.8-.8C19.6 3.7 17.9 3 16 3s-3.6.7-4.8 1.8l.8.8C13 4.6
    14.4 4 16 4s3 .6 4.2 1.9zM19.4 6.7c-.9-.9-2.1-1.4-3.4-1.4s-2.5.5-3.4
    1.4l.8.8C14.1 6.8 15 6.4 16 6.4s1.9.4 2.6 1.1l.8-.8zM19
    13h-2V9h-2v4H4c-1.1 0-2 .9-2 2v4c0 1.1.9 2 2 2h15c1.1 0 2-.9
    2-2v-4c0-1.1-.9-2-2-2zm0 6H4v-4h15v4zM6 17.5C6 18.3 5.3 19 4.5
    19S3 18.3 3 17.5 3.7 16 4.5 16 6 16.7 6 17.5z"/>
  </svg>`,

  // Access point / repeater icon – used in slave node cards.
  ap: `<svg viewBox="0 0 24 24" fill="currentColor">
    <path d="M12 3C6.95 3 3.15 5.85 2 9.7L3.72 10C4.73 6.79 8.1 4.5 12
    4.5c3.9 0 7.27 2.29 8.28 5.5L22 9.7C20.85 5.85 17.05 3 12 3zm0
    4c-2.44 0-4.44 1.46-5.2 3.5L8.6 11c.6-1.39 1.99-2.38 3.4-2.38s2.8.99
    3.4 2.38l1.8-.5C16.44 8.46 14.44 7 12 7zm0 4c-1.1 0-2 .9-2 2s.9 2 2
    2 2-.9 2-2-.9-2-2-2zm0 5c-.55 0-1 .45-1 1v4h2v-4c0-.55-.45-1-1-1z"/>
  </svg>`,

  // WiFi signal icon – shown next to wireless client rows.
  wifi: `<svg viewBox="0 0 24 24" fill="currentColor">
    <path d="M1 9l2 2c4.97-4.97 13.03-4.97 18 0l2-2C16.93 2.93 7.08 2.93
    1 9zm8 8l3 3 3-3c-1.65-1.66-4.34-1.66-6 0zm-4-4l2 2c2.76-2.76 7.24-2.76
    10 0l2-2C15.14 9.14 8.87 9.14 5 13z"/>
  </svg>`,

  // Ethernet plug icon – shown next to wired client rows.
  lan: `<svg viewBox="0 0 24 24" fill="currentColor">
    <path d="M8 3H5L2 7l3 4h3l1.5 2H9c-1.1 0-2 .9-2 2v4c0 1.1.9 2 2
    2h6c1.1 0 2-.9 2-2v-4c0-1.1-.9-2-2-2h-1.5L15 11h3l3-4-3-4h-3l-3
    4v.17L10 8.83V8l-2-5zm1 10h6v4H9v-4z"/>
  </svg>`,

  // Question-mark circle – used for the "Unassigned" section and error state.
  unknown: `<svg viewBox="0 0 24 24" fill="currentColor">
    <path d="M11 18h2v-2h-2v2zm1-16C6.48 2 2 6.48 2 12s4.48 10 10
    10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8
    8-8 8 3.59 8 8-3.59 8-8 8zm0-14c-2.21 0-4 1.79-4 4h2c0-1.1.9-2
    2-2s2 .9 2 2c0 2-3 1.75-3 5h2c0-2.25 3-2.5 3-5 0-2.21-1.79-4-4-4z"/>
  </svg>`,

  transfer: `<svg viewBox="0 0 24 24" fill="currentColor">
    <path d="M7 6h10l-3.5-3.5 1.4-1.4L21.8 8l-6.9 6.9-1.4-1.4L17 10H7V6zm10
    8H7l3.5 3.5-1.4 1.4L2.2 12l6.9-6.9 1.4 1.4L7 10h10v4z"/>
  </svg>`,
};

// ── Utility helpers ───────────────────────────────────────────────────────────

/**
 * HTML-escape a value for safe insertion into innerHTML.
 *
 * Converts the value to a string (treating null/undefined as ""), then
 * replaces the five characters that have special meaning in HTML:
 *   &  →  &amp;
 *   <  →  &lt;
 *   >  →  &gt;
 *   "  →  &quot;
 *
 * @param {*} s - Value to escape (anything with a toString method).
 * @returns {string} HTML-safe string ready for insertion into innerHTML.
 */
const esc = (s) =>
  String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

const HEX_COLOR_RE = /^#([0-9a-fA-F]{6})$/;

function sanitizeHexColor(value, fallback) {
  const v = String(value ?? "").trim();
  return HEX_COLOR_RE.test(v) ? v : fallback;
}

function hexToRgba(hex, alpha) {
  const clean = sanitizeHexColor(hex, "#000000").slice(1);
  const r = parseInt(clean.slice(0, 2), 16);
  const g = parseInt(clean.slice(2, 4), 16);
  const b = parseInt(clean.slice(4, 6), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}

function sanitizeFontScale(value, fallback = 100) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(80, Math.min(140, Math.round(n)));
}

/**
 * Format a speed value from kbit/s to a human-readable string.
 *
 * The Fritz!Box reports all speeds in kbit/s.  This function converts them to
 * the most appropriate unit:
 *   ≥ 1 000 000 kbit/s → Gbit/s  (e.g. 2 500 000 → "2.5 Gbit/s")
 *   ≥ 1 000 kbit/s     → Mbit/s  (e.g.   867 000 → "867 Mbit/s")
 *   < 1 000 kbit/s     → kbit/s  (e.g.       720 → "720 kbit/s")
 *
 * @param {number|null|undefined} kbps - Speed in kbit/s.
 * @returns {string|null} Formatted string, or null if the value is falsy/zero.
 */
function fmtSpeed(kbps) {
  if (!kbps || kbps <= 0) return null;
  if (kbps >= 1_000_000) return `${(kbps / 1_000_000).toFixed(1)} Gbit/s`;
  if (kbps >= 1_000)     return `${Math.round(kbps / 1_000)} Mbit/s`;
  return `${kbps} kbit/s`;
}

/**
 * Extract the WiFi frequency band from a Fritz!Box interface name string.
 *
 * Fritz!Box encodes band and radio information in the interface name field,
 * for example:
 *   "AP:5G:0"   → 5 GHz WiFi
 *   "AP:2G:0"   → 2.4 GHz WiFi
 *   "LAN:1"     → Wired Ethernet port 1
 *   "ETH:0"     → Wired Ethernet (alternative naming)
 *
 * @param {string|null|undefined} ifaceName - The interface_name field from the topology.
 * @returns {string|null} Human-readable band/medium string, or null if unrecognised.
 */
function getBand(ifaceName) {
  if (!ifaceName) return null;
  const u = ifaceName.toUpperCase();
  if (u.includes("5G"))                          return "5 GHz";
  if (u.includes("2G") || u.startsWith("AP:2"))  return "2,4 GHz";   // German decimal comma
  if (u.startsWith("LAN") || u.startsWith("ETH")) return "LAN";
  return null;
}

/**
 * Build the connection label string shown next to each client row.
 *
 * Examples:
 *   WiFi client on 5 GHz at 867 Mbit/s → "5 GHz → 867 Mbit/s"
 *   WiFi client, no speed data          → "WiFi"
 *   LAN client at 1 Gbit/s             → "LAN → 1.0 Gbit/s"
 *   LAN client, no speed data          → "LAN"
 *
 * For WiFi clients, we prefer cur_rx_kbps (current actual speed) over
 * max_rx_kbps (negotiated maximum) because the current speed is more
 * informative.  For LAN clients, max speed is usually the more stable
 * and reliable number.
 *
 * @param {Object} c - Client object from the topology attributes.
 * @param {string} c.connection_type  - "WLAN" or "LAN".
 * @param {string} c.interface_name   - Fritz!Box interface name (for band detection).
 * @param {number} c.cur_rx_kbps      - Current receive throughput in kbit/s.
 * @param {number} c.max_rx_kbps      - Maximum (negotiated) receive speed in kbit/s.
 * @returns {string} Connection label suitable for display in the card.
 */
function connLabel(c) {
  if (c.connection_type === "WLAN") {
    // Derive band from interface name; fall back to generic "WiFi" label.
    const band = getBand(c.interface_name) ?? "WiFi";
    // Prefer current speed; fall back to negotiated max if current is zero.
    const spd  = fmtSpeed(c.cur_rx_kbps || c.max_rx_kbps);
    return spd ? `${band} → ${spd}` : band;
  }
  // LAN: prefer max speed (more stable than instantaneous current speed).
  const spd = fmtSpeed(c.max_rx_kbps || c.cur_rx_kbps);
  return spd ? `LAN → ${spd}` : "LAN";
}

/**
 * Comparator for sorting client device arrays.
 *
 * Sort order:
 *   1. Connected devices before disconnected ones (so active devices are
 *      always visible at the top of each mesh node's client list).
 *   2. Alphabetically by name (falling back to MAC address if no name).
 *
 * Designed for use with Array.prototype.sort().
 *
 * @param {Object} a - First client object.
 * @param {Object} b - Second client object.
 * @returns {number} Negative if a < b, positive if a > b, 0 if equal.
 */
const clientSort = (a, b) => {
  // Primary sort: connection state.  "CONNECTED" sorts before everything else.
  if (a.connection_state !== b.connection_state)
    return a.connection_state === "CONNECTED" ? -1 : 1;
  // Secondary sort: alphabetical by display name, falling back to MAC.
  return (a.name || a.mac || "").localeCompare(b.name || b.mac || "");
};

// ── Card component ─────────────────────────────────────────────────────────────
//
// FritzMeshCard is a standard Web Component (extends HTMLElement).
// Home Assistant requires custom cards to:
//   1. Extend HTMLElement (not LitElement or any other framework class).
//   2. Implement setConfig(config) to accept card YAML configuration.
//   3. Implement a `set hass(hass)` setter called whenever HA state changes.
//   4. Optionally implement getCardSize() to help the layout engine.
//   5. Register with customElements.define().

class FritzMeshCard extends HTMLElement {
  constructor() {
    super();
    // Attach a Shadow DOM to encapsulate styles from the rest of the dashboard.
    // mode: "open" allows external JS to access shadowRoot if needed.
    this.attachShadow({ mode: "open" });

    // _config stores the card YAML options set by setConfig().
    this._config  = null;
    this._hass = null;

    // _lastKey is a JSON snapshot of the previous state attributes.
    // We compare it on every `set hass()` call to skip re-renders when
    // nothing has changed, avoiding unnecessary DOM updates.
    this._lastKey = "";
    this._sizeMode = "";
    this._resizeObserver = null;
  }

  /**
   * Return a minimal stub configuration so the card editor can show a preview.
   * Called by the HA dashboard editor when the user selects this card type.
   *
   * @returns {Object} Default card configuration.
   */
  static getStubConfig() {
    return { entity: "sensor.fritz_box_mesh_192_168_178_1_topology" };
  }

  /**
   * Return the custom element used as the visual UI editor for this card.
   * HA calls this method when the user switches to the "Visual Editor" tab in
   * the card configuration dialog.
   *
   * @returns {HTMLElement} An instance of FritzMeshCardEditor.
   */
  static getConfigElement() {
    return document.createElement("fritzmesh-card-editor");
  }

  /**
   * Accept the card configuration from the YAML/UI editor.
   *
   * Called by HA once when the card is first created (or when the user edits
   * the YAML).  Must throw an Error if the configuration is invalid, because
   * HA will display that error message to the user.
   *
   * @param {Object} config - Parsed card YAML as a plain object.
   * @param {string} config.entity - Entity ID of the FritzMeshTopologySensor.
   * @param {string} [config.title] - Optional card header text.
   * @throws {Error} If `entity` is missing from the configuration.
   */
  connectedCallback() {
    // Ensure the shadow DOM is never empty while the card waits for the
    // first hass update.  An empty shadow root can confuse HA's card loader.
    if (!this.shadowRoot.innerHTML) {
      this.shadowRoot.innerHTML = `<style>${STYLES}</style><ha-card></ha-card>`;
    }
    this._ensureResizeObserver();
    this._updateSizeMode(this.clientWidth);
  }

  disconnectedCallback() {
    if (this._resizeObserver) {
      this._resizeObserver.disconnect();
      this._resizeObserver = null;
    }
  }

  setConfig(config) {
    // Log at `info` level so it always appears in the browser console
    // (unlike `debug` which requires enabling "Verbose" in DevTools).

    if (!config?.entity) {
      const msg = `fritzmesh-card: \`entity\` is missing or empty. ` +
        `Received config: ${JSON.stringify(config)}`;
      console.error(msg);
      throw new Error(msg);
    }
    // Backward compatibility: old configs may still carry "none".
    const rawNameInfoDisplay = config.name_info_display ?? "mesh_node";
    const nameInfoDisplay = rawNameInfoDisplay === "none" ? "mesh_node" : rawNameInfoDisplay;
    if (!["mesh_node", "connection_state"].includes(nameInfoDisplay)) {
      throw new Error("fritzmesh-card: name_info_display must be 'mesh_node' or 'connection_state'");
    }
    const nodeSort = config.node_sort ?? "default";
    if (!["default", "name", "ip", "mac"].includes(nodeSort)) {
      throw new Error("fritzmesh-card: node_sort must be 'default', 'name', 'ip', or 'mac'");
    }
    const transferMetricMode = config.transfer_metric_mode ?? "aggregate";
    if (!["aggregate", "uplink", "max_single", "average"].includes(transferMetricMode)) {
      throw new Error(
        "fritzmesh-card: transfer_metric_mode must be 'aggregate', 'uplink', 'max_single', or 'average'"
      );
    }
    const hideOfflineNodes = config.hide_offline_nodes === true;
    this._config = {
      ...config,
      url_template: config.url_template ?? "http://{ip}",
      name_info_display: nameInfoDisplay,
      node_sort: nodeSort,
      transfer_metric_mode: transferMetricMode,
      hide_offline_nodes: hideOfflineNodes,
      line_color: sanitizeHexColor(config.line_color, "#4caf50"),
      accent_color: sanitizeHexColor(config.accent_color, "#1976d2"),
      text_dim_color: sanitizeHexColor(config.text_dim_color, "#888888"),
      master_panel_start_color: sanitizeHexColor(config.master_panel_start_color, "#1565c0"),
      master_panel_end_color: sanitizeHexColor(config.master_panel_end_color, "#1e88e5"),
      font_scale: sanitizeFontScale(config.font_scale, 100),
    };

    // Config-only changes (e.g. colors/font scale) should re-render immediately
    // even when the entity attributes themselves didn't change.
    this._lastKey = "";
    if (this._hass) {
      const state = this._hass?.states?.[this._config.entity];
      this._render(state);
    }
  }

  /**
   * Receive the latest HA state from the dashboard.
   *
   * HA calls this setter whenever *any* entity state changes, not just the
   * one this card cares about.  We therefore:
   *   1. Look up only our configured entity in hass.states.
   *   2. Serialise its attributes to a JSON string.
   *   3. Compare with the previous serialisation (_lastKey).
   *   4. Only re-render if something actually changed.
   *
   * @param {Object} hass - The full Home Assistant state object.
   */
  set hass(hass) {
    if (!this._config) return;
    this._hass = hass;

    const state = hass?.states?.[this._config.entity];
    // Serialise the entire attributes object as a cache key.
    // Using JSON.stringify on attributes catches any nested change.
    const key   = JSON.stringify(state?.attributes);

    // Bail out early if the attributes haven't changed since the last render.
    if (key === this._lastKey) return;

    this._lastKey = key;
    try {
      this._render(state);
    } catch (e) {
      console.error("fritzmesh-card render error:", e);
      this._setHTML("Fritz!Box Mesh Topology", `
        <div class="msg warn">
          ${ICON.unknown}
          <span>Render error: <code>${esc(String(e))}</code></span>
        </div>`);
    }
  }

  /**
   * Estimate the card height in grid rows for the dashboard layout engine.
   *
   * HA uses this value to pre-allocate space in the grid before the card
   * renders.  We compute it from the number of nodes and total client count
   * to produce a reasonable estimate.
   *
   * Formula: max(4, ceil((nodes × 3 + totalClients) / 4))
   *   - Each mesh node contributes ~3 rows (header + a few clients).
   *   - Each client contributes 1 row.
   *   - Minimum size is 4 rows so the card is never tiny.
   *
   * @returns {number} Estimated height in grid rows (integer ≥ 4).
   */
  getCardSize() {
    const nodes   = this._lastKey ? (JSON.parse(this._lastKey)?.mesh_nodes ?? []) : [];
    const clients = nodes.reduce((s, n) => s + (n.clients?.length ?? 0), 0);
    return Math.max(4, Math.ceil((nodes.length * 3 + clients) / 4));
  }

  /**
   * Default grid behavior for dashboard Sections view.
   * Users can still override this with per-card `grid_options` in UI/YAML.
   */
  getGridOptions() {
    return {
      columns: 9,
      rows: 3,
      min_columns: 4,
      max_columns: 12,
      min_rows: 2,
      max_rows: 8,
    };
  }

  // ── Render ────────────────────────────────────────────────────────────────

  /**
   * Main render method: build the full card HTML from the entity state.
   *
   * Called by the `set hass()` setter only when the topology attributes have
   * changed.  Sets the shadow DOM innerHTML via _setHTML().
   *
   * @param {Object|undefined} state - HA entity state object, or undefined if
   *                                   the entity doesn't exist yet.
   */
  _render(state) {
    // Determine the card title.  config.title=undefined → use default.
    // config.title="" → hide the header entirely (by passing an empty string
    // to _setHTML which renders no header element for falsy values).
    const title = this._config.title !== undefined
      ? this._config.title
      : "Fritz!Box Mesh Topology";

    // Handle missing or unavailable entity state.
    if (!state || state.state === "unavailable") {
      this._setHTML(title, `
        <div class="msg warn">
          ${ICON.unknown}
          Entity <code>${esc(this._config.entity)}</code> is unavailable.
        </div>`);
      return;
    }

    // Extract the topology data from the entity's extra_state_attributes.
    const attrs      = state.attributes ?? {};
    const nodes      = attrs.mesh_nodes ?? [];         // array of mesh node objects
    const unassigned = attrs.unassigned_clients ?? []; // clients with no known parent node
    const host       = attrs.host ?? "";               // Fritz!Box IP for display

    // Show a placeholder while waiting for the first coordinator update.
    if (!nodes.length) {
      this._setHTML(title,
        `<div class="msg">No topology data yet — waiting for first coordinator update.</div>`);
      return;
    }

    // Identify the master node (role === "master") and all slave repeaters.
    // The topology sensor always sorts master first, but we find it explicitly
    // for clarity and resilience.
    const master = nodes.find((n) => n.role === "master") ?? nodes[0];
    let slaves = this._sortSlaveNodes(nodes.filter((n) => n !== master));
    if (this._config.hide_offline_nodes) {
      slaves = slaves.filter((node) => this._isNodeOnline(node));
    }

    // Build the two-column layout:
    //   Left  → sticky master panel (blue gradient card with router icon)
    //   Right → scrollable tree of sections (master clients, then each slave)
    this._setHTML(title, `
      <div class="layout">
        ${this._masterPanel(master, host)}
        <div class="tree">
          ${this._masterSection(master)}
          ${slaves.map((s) => this._slaveSection(s)).join("")}
          ${!this._config.hide_offline_nodes && unassigned.length ? this._unassignedSection(unassigned) : ""}
        </div>
      </div>`);
  }

  // ── Left panel ─────────────────────────────────────────────────────────────

  /**
   * Render the left-hand master Fritz!Box panel.
   *
   * This panel is sticky-positioned so it stays visible while the user scrolls
   * through a long list of slaves and clients on the right.
   *
   * @param {Object} node - The master MeshNode object from topology attributes.
   * @param {string} host - Fritz!Box IP/hostname from the topology attributes.
   * @returns {string} HTML string for the master panel div.
   */
  _masterPanel(node, host) {
    const nodeRates = this._nodeRateLabel(node);
    return `
      <div class="master-panel">
        <div class="mp-icon">${ICON.router}</div>
        <div class="mp-name">${esc(node?.name ?? "Fritz!Box")}</div>
        ${node?.model    ? `<div class="mp-model">${esc(node.model)}</div>` : ""}
        ${host           ? `<div class="mp-ip">${esc(host)}</div>` : ""}
        ${node?.firmware ? `<div class="mp-fw">FW ${esc(node.firmware)}</div>` : ""}
        ${nodeRates ? `<div class="mp-rate">${ICON.transfer}<span>${esc(nodeRates)}</span></div>` : ""}
        <div class="mp-badge">HEIMNETZ</div>
      </div>`;
  }

  // ── Master section ─────────────────────────────────────────────────────────

  /**
   * Render the section showing clients directly connected to the master node.
   *
   * This is always the first section in the tree column.  Clients are sorted
   * (connected first, then alphabetically) before rendering.
   *
   * @param {Object} node - The master MeshNode object.
   * @returns {string} HTML string for the master clients section.
   */
  _masterSection(node) {
    // Sort a copy to avoid mutating the original attribute array.
    const clients = this._sortClients(this._visibleClients([...(node?.clients ?? [])]));
    return `
      <div class="section">
        <div class="section-row">
          <span class="row-label">Clients</span>
        </div>
        <div class="clients">
          ${clients.length
            ? clients.map((c) => this._clientRow(c)).join("")
            : '<div class="no-clients">No direct clients</div>'}
        </div>
      </div>`;
  }

  // ── Slave section ──────────────────────────────────────────────────────────

  /**
   * Render one section for a slave (repeater) mesh node and its clients.
   *
   * Each slave section shows:
   *   • A horizontal branch line from the backbone (solid=LAN, dashed=WiFi)
   *   • A badge ("LAN" or "WiFi") indicating how the slave connects to master
   *   • A slave card with the AP icon, repeater name, model, and badge
   *   • A list of client rows for devices connected to this repeater
   *
   * @param {Object} node - A slave MeshNode object from topology attributes.
   * @returns {string} HTML string for the slave section.
   */
  _slaveSection(node) {
    const clients  = this._sortClients(this._visibleClients([...(node?.clients ?? [])]));
    const nodeRates = this._nodeRateLabel(node);
    // parent_link_type is "WLAN" or "LAN"; default to "LAN" if missing.
    const linkType = node.parent_link_type || "LAN";
    const isWifi   = linkType === "WLAN";

    return `
      <div class="section">
        <div class="section-row">
          <!-- Branch line: dashed for WiFi, solid for LAN -->
          <div class="h-line ${isWifi ? "wifi" : "lan"}"></div>
          <!-- Badge indicating the uplink medium to the parent mesh node -->
          <span class="row-label ${isWifi ? "wifi-label" : "lan-label"}">${isWifi ? "WiFi" : "LAN"}</span>
          <!-- Slave device card -->
          <div class="slave-card">
            <!-- Icon colour: blue for LAN-connected repeater, green for WiFi-connected -->
            <div class="sc-icon ${isWifi ? "sc-wifi" : "sc-lan"}">${ICON.ap}</div>
            <div class="sc-info">
              <div class="sc-name">${esc(node.name)}</div>
              ${node.model ? `<div class="sc-model">${esc(node.model)}</div>` : ""}
              ${nodeRates ? `<div class="sc-rate">${ICON.transfer}<span>${esc(nodeRates)}</span></div>` : ""}
              <div class="sc-badge">${isWifi ? "WIFI REPEATER" : "REPEATER"}</div>
            </div>
          </div>
        </div>
        <!-- Client list for this repeater -->
        <div class="clients">
          ${clients.length
            ? clients.map((c) => this._clientRow(c)).join("")
            : '<div class="no-clients">No clients</div>'}
        </div>
      </div>`;
  }

  // ── Unassigned section ─────────────────────────────────────────────────────

  /**
   * Render a section for clients that couldn't be assigned to any mesh node.
   *
   * Unassigned clients are rare in practice but can occur when the Fritz!Box
   * reports a device in its host table but not in the mesh link graph (e.g.
   * a device that was recently connected and is being tracked by the router
   * but whose link entry has already expired).
   *
   * The section is visually distinguished by a dashed border and reduced
   * opacity on the "Unassigned" card, and uses the unknown icon.
   *
   * @param {Array} clients - Array of unassigned client objects.
   * @returns {string} HTML string for the unassigned section.
   */
  _unassignedSection(clients) {
    return `
      <div class="section">
        <div class="section-row">
          <div class="h-line lan"></div>
          <span class="row-label">?</span>
          <div class="slave-card unassigned">
            <div class="sc-icon">${ICON.unknown}</div>
            <div class="sc-info">
              <div class="sc-name">Unassigned</div>
              <div class="sc-badge">UNKNOWN</div>
            </div>
          </div>
        </div>
        <div class="clients">
          ${this._sortClients([...clients]).map((c) => this._clientRow(c)).join("")}
        </div>
      </div>`;
  }

  // ── Client row ─────────────────────────────────────────────────────────────

  /**
   * Render a single client device row.
   *
   * Each row contains (left to right):
   *   • A short horizontal line (solid=LAN, dashed=WiFi) connecting to the
   *     section backbone
   *   • A speed/band label (e.g. "5 GHz → 867 Mbit/s") from connLabel()
   *   • A small WiFi or LAN icon
   *   • The device name (hostname), highlighted in blue when connected
   *   • The IP address in monospace, if available
   *
   * Disconnected clients receive the ".off" CSS class which reduces opacity
   * to 38%, making them visually subordinate to connected devices.
   *
   * @param {Object} client - Client object from the topology attributes.
   * @param {string} client.connection_state - "CONNECTED" or other.
   * @param {string} client.connection_type  - "WLAN" or "LAN".
   * @param {string} [client.name]           - Device hostname.
   * @param {string} [client.mac]            - MAC address (fallback display name).
   * @param {string} [client.ip]             - IPv4 address.
   * @returns {string} HTML string for the client row div.
   */
  _clientRow(client) {
    const on    = client.connection_state === "CONNECTED";  // true → active/bright
    const wifi  = client.connection_type  === "WLAN";       // true → WiFi, false → LAN
    const label = connLabel(client);                         // "5 GHz → 867 Mbit/s" etc.
    const name  = client.name || client.mac || "?";         // display name fallback chain
    const ip = client.ip || "";
    const entityId = this._resolveMoreInfoEntityId(client);

    return `
      <div class="client-row${on ? "" : " off"}">
        <!-- Branch line: dashed for WiFi, solid for LAN -->
        <div class="cl-line ${wifi ? "wifi" : "lan"}"></div>
        <!-- Speed / band label -->
        <span class="cl-label">${esc(label)}</span>
        <!-- Connection type icon (WiFi waves or Ethernet plug) -->
        <span class="cl-icon">${wifi ? ICON.wifi : ICON.lan}</span>
        <!-- Device hostname -->
        <button
          type="button"
          class="cl-name client-action"
          data-click-source="name"
          data-entity-id="${encodeURIComponent(entityId)}"
          data-ip="${encodeURIComponent(ip)}"
        >${esc(name)}</button>
        <!-- IP address (shown only when known) -->
        ${ip
          ? `<button
              type="button"
              class="cl-ip client-action"
              data-click-source="ip"
              data-entity-id="${encodeURIComponent(entityId)}"
              data-ip="${encodeURIComponent(ip)}"
            >${esc(ip)}</button>`
          : ""
        }
      </div>`;
  }

  // ── Scaffold ───────────────────────────────────────────────────────────────

  /**
   * Write the complete card HTML to the shadow DOM.
   *
   * Wraps `body` in an <ha-card> element (HA's standard card web component)
   * with an optional header.  The <style> block is injected first so the
   * shadow DOM's scoped CSS applies to everything inside.
   *
   * @param {string} title - Card header text.  If falsy, no header is rendered.
   * @param {string} body  - Inner HTML to place inside the card body div.
   */
  _setHTML(title, body) {
    const cfgStyles = this._configStyles();
    this.shadowRoot.innerHTML = `
      <style>${STYLES}${cfgStyles}</style>
      <ha-card>
        ${title ? `<div class="card-header">${esc(title)}</div>` : ""}
        <div class="card-body">${body}</div>
      </ha-card>`;
    this._updateSizeMode(this.clientWidth);
    this._wireClientActions();
  }

  _wireClientActions() {
    const actionEls = this.shadowRoot.querySelectorAll(".client-action");
    actionEls.forEach((el) => {
      el.addEventListener("click", (e) => {
        e.stopPropagation();
        this._handleClientAction(el);
      });
      el.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          e.stopPropagation();
          this._handleClientAction(el);
        }
      });
    });
  }

  _handleClientAction(el) {
    const source = el.dataset.clickSource || "name";
    const entityId = decodeURIComponent(el.dataset.entityId || "");
    const ip = decodeURIComponent(el.dataset.ip || "");

    if (source === "ip") {
      this._openClientUrl(ip);
      return;
    }
    this._openMoreInfo(entityId);
  }

  _openMoreInfo(entityId) {
    if (!entityId) {
      console.warn("[fritzmesh-card] no mapped HA entity_id found for more-info click");
      return;
    }
    this.dispatchEvent(new CustomEvent("hass-more-info", {
      bubbles: true,
      composed: true,
      detail: { entityId },
    }));
  }

  _openClientUrl(ip) {
    if (!ip) return;
    const url = this._buildClientUrl(ip);
    if (!url) return;
    window.open(url, "_blank", "noopener,noreferrer");
  }

  _buildClientUrl(ip) {
    const template = this._config?.url_template || "http://{ip}";
    let url = template.includes("{ip}") ? template.replaceAll("{ip}", ip) : `${template}${ip}`;
    if (!/^https?:\/\//i.test(url)) {
      url = `http://${url}`;
    }
    return url;
  }

  _resolveMoreInfoEntityId(client) {
    const mode = this._config?.name_info_display ?? "mesh_node";
    if (mode === "connection_state") {
      return client.ha_entity_connected_id || client.ha_entity_id || "";
    }
    return client.ha_entity_mesh_node_id || client.ha_entity_id || client.ha_entity_connected_id || "";
  }

  _nodeRateLabel(node) {
    if (!node) return "";
    const mode = this._config?.transfer_metric_mode ?? "aggregate";
    const clients = Array.isArray(node.clients) ? node.clients : [];

    let txKbps = 0;
    let rxKbps = 0;
    let labelPrefix = "";

    if (mode === "uplink") {
      txKbps = node.parent_cur_tx_kbps || 0;
      rxKbps = node.parent_cur_rx_kbps || 0;
      labelPrefix = "Uplink ";
    } else if (mode === "max_single") {
      txKbps = clients.reduce((m, c) => Math.max(m, c?.cur_tx_kbps || 0), 0);
      rxKbps = clients.reduce((m, c) => Math.max(m, c?.cur_rx_kbps || 0), 0);
      labelPrefix = "Max ";
    } else if (mode === "average") {
      const count = clients.length || 0;
      if (count > 0) {
        txKbps = Math.round(
          clients.reduce((s, c) => s + Math.max(0, c?.cur_tx_kbps || 0), 0) / count
        );
        rxKbps = Math.round(
          clients.reduce((s, c) => s + Math.max(0, c?.cur_rx_kbps || 0), 0) / count
        );
      }
      labelPrefix = "Avg ";
    } else {
      txKbps = node.clients_cur_tx_kbps_total || node.parent_cur_tx_kbps || 0;
      rxKbps = node.clients_cur_rx_kbps_total || node.parent_cur_rx_kbps || 0;
      labelPrefix = "Agg ";
    }

    const tx = fmtSpeed(txKbps);
    const rx = fmtSpeed(rxKbps);
    if (!tx && !rx) return "";
    if (tx && rx) return `${labelPrefix}TX ${tx} / RX ${rx}`;
    return tx ? `${labelPrefix}TX ${tx}` : `${labelPrefix}RX ${rx}`;
  }

  _sortSlaveNodes(nodes) {
    const mode = this._config?.node_sort ?? "default";
    if (mode === "default") return nodes;
    const sorted = [...nodes];
    if (mode === "name") {
      sorted.sort((a, b) => this._compareName(a?.name, b?.name));
      return sorted;
    }
    if (mode === "mac") {
      sorted.sort((a, b) => this._compareMac(a?.mac, b?.mac) || this._compareName(a?.name, b?.name));
      return sorted;
    }
    if (mode === "ip") {
      sorted.sort((a, b) => this._compareIp(a?.ip, b?.ip) || this._compareMac(a?.mac, b?.mac) || this._compareName(a?.name, b?.name));
      return sorted;
    }
    return nodes;
  }

  _isNodeOnline(node) {
    if (!node) return false;
    if (node.role === "master") return true;
    const linkState = String(node.parent_link_state || "").toUpperCase();
    // If the attribute is missing (older integration payload), keep the node
    // visible for backwards compatibility.
    if (!linkState) return true;
    return linkState === "CONNECTED";
  }

  _visibleClients(clients) {
    if (!this._config?.hide_offline_nodes) return clients;
    return clients.filter((client) => String(client?.connection_state || "").toUpperCase() === "CONNECTED");
  }

  _sortClients(clients) {
    const mode = this._config?.node_sort ?? "default";
    if (mode === "default") {
      return [...clients].sort(clientSort);
    }
    const sorted = [...clients];
    if (mode === "name") {
      sorted.sort((a, b) => this._compareName(a?.name || a?.mac, b?.name || b?.mac));
      return sorted;
    }
    if (mode === "mac") {
      sorted.sort((a, b) => this._compareMac(a?.mac, b?.mac) || this._compareName(a?.name || a?.mac, b?.name || b?.mac));
      return sorted;
    }
    if (mode === "ip") {
      sorted.sort((a, b) => this._compareIp(a?.ip, b?.ip) || this._compareMac(a?.mac, b?.mac) || this._compareName(a?.name || a?.mac, b?.name || b?.mac));
      return sorted;
    }
    return [...clients].sort(clientSort);
  }

  _compareIp(a, b) {
    const pa = this._ipParts(a);
    const pb = this._ipParts(b);
    if (!pa && !pb) return 0;
    if (!pa) return 1;   // unknown IP last
    if (!pb) return -1;  // known IP first
    for (let i = 0; i < 4; i += 1) {
      if (pa[i] !== pb[i]) return pa[i] - pb[i];
    }
    return 0;
  }

  _compareMac(a, b) {
    const ma = this._macParts(a);
    const mb = this._macParts(b);
    if (!ma && !mb) {
      return this._normString(a).localeCompare(this._normString(b));
    }
    if (!ma) return 1;   // unknown MAC last
    if (!mb) return -1;  // known MAC first
    for (let i = 0; i < 6; i += 1) {
      if (ma[i] !== mb[i]) return ma[i] - mb[i];
    }
    return 0;
  }

  _ipParts(ip) {
    const s = String(ip || "").trim().split("/", 1)[0];
    const parts = s.split(".");
    if (parts.length !== 4) return null;
    const out = [];
    for (const p of parts) {
      const n = Number(p);
      if (!Number.isInteger(n) || n < 0 || n > 255) return null;
      out.push(n);
    }
    return out;
  }

  _macParts(mac) {
    const s = String(mac || "").toLowerCase().replace(/[^0-9a-f]/g, "");
    if (s.length !== 12) return null;
    const out = [];
    for (let i = 0; i < 12; i += 2) {
      const n = Number.parseInt(s.slice(i, i + 2), 16);
      if (!Number.isFinite(n)) return null;
      out.push(n);
    }
    return out;
  }

  _compareName(a, b) {
    return this._normString(a).localeCompare(this._normString(b));
  }

  _normString(v) {
    return String(v || "").trim().toLowerCase();
  }

  _configStyles() {
    const lineColor = sanitizeHexColor(this._config?.line_color, "#4caf50");
    const accentColor = sanitizeHexColor(this._config?.accent_color, "#1976d2");
    const textDimColor = sanitizeHexColor(this._config?.text_dim_color, "#888888");
    const masterPanelStart = sanitizeHexColor(this._config?.master_panel_start_color, "#1565c0");
    const masterPanelEnd = sanitizeHexColor(this._config?.master_panel_end_color, "#1e88e5");
    const fontScale = sanitizeFontScale(this._config?.font_scale, 100);
    return `
:host {
  --green: ${lineColor};
  --green-fade: ${hexToRgba(lineColor, 0.18)};
  --blue: ${accentColor};
  --text-dim: ${textDimColor};
  --master-panel-start: ${masterPanelStart};
  --master-panel-end: ${masterPanelEnd};
  --fm-font-scale: ${fontScale}%;
}
`;
  }

  _ensureResizeObserver() {
    if (this._resizeObserver) return;
    this._resizeObserver = new ResizeObserver((entries) => {
      const width = entries?.[0]?.contentRect?.width ?? this.clientWidth;
      this._updateSizeMode(width);
    });
    this._resizeObserver.observe(this);
  }

  _updateSizeMode(width) {
    const mode = width < 520 ? "compact" : width < 760 ? "medium" : "full";
    if (mode === this._sizeMode) return;
    this._sizeMode = mode;
    this.setAttribute("data-size", mode);
  }
}

// ── Visual editor ─────────────────────────────────────────────────────────────
//
// FritzMeshCardEditor is the UI editor element rendered inside the card
// configuration dialog when the user picks the "Visual Editor" tab.
//
// HA protocol:
//   • HA calls FritzMeshCard.getConfigElement() to obtain this element.
//   • It then calls setConfig(config) with the current card YAML.
//   • It sets the `hass` property so the entity picker can query entity IDs.
//   • Whenever the user changes a field, the editor must fire a
//     "config-changed" CustomEvent with { detail: { config } } so HA can
//     update the YAML pane and the live preview.

class FritzMeshCardEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass   = null;
  }

  /** Receive the current card config – re-renders the form. */
  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  /** Receive the HA instance and re-render available entities. */
  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    const entities = this._applicableEntities();
    const currentEntity = this._config.entity ?? "";
    const currentTitle = this._config.title ?? "";
    const currentUrlTemplate = this._config.url_template ?? "http://{ip}";
    const currentNameInfoDisplay = this._config.name_info_display ?? "mesh_node";
    const currentNodeSort = this._config.node_sort ?? "default";
    const currentTransferMetricMode = this._config.transfer_metric_mode ?? "aggregate";
    const currentHideOfflineNodes = this._config.hide_offline_nodes === true;
    const currentLineColor = sanitizeHexColor(this._config.line_color, "#4caf50");
    const currentAccentColor = sanitizeHexColor(this._config.accent_color, "#1976d2");
    const currentTextDimColor = sanitizeHexColor(this._config.text_dim_color, "#888888");
    const currentMasterPanelStart = sanitizeHexColor(this._config.master_panel_start_color, "#1565c0");
    const currentMasterPanelEnd = sanitizeHexColor(this._config.master_panel_end_color, "#1e88e5");
    const currentFontScale = sanitizeFontScale(this._config.font_scale, 100);

    this.shadowRoot.innerHTML = `
      <style>
        .card-config {
          display: flex;
          flex-direction: column;
          gap: 12px;
          padding: 12px 0;
        }
        label {
          display: block;
          font-size: 0.86rem;
          font-weight: 600;
          margin-bottom: 6px;
        }
        select,
        input {
          box-sizing: border-box;
          width: 100%;
          padding: 8px;
          border-radius: 6px;
          border: 1px solid var(--divider-color, #d0d0d0);
          background: var(--card-background-color, #fff);
          color: var(--primary-text-color, #111);
          font: inherit;
        }
        .hint {
          margin-top: 4px;
          font-size: 0.78rem;
          color: var(--secondary-text-color, #777);
        }
        .toggle-row {
          display: flex;
          align-items: center;
          gap: 8px;
          margin-bottom: 0;
        }
        .toggle-row input[type="checkbox"] {
          width: auto;
          margin: 0;
          padding: 0;
        }
      </style>
      <div class="card-config">
        <div>
          <label for="entity-select">Topology sensor (required)</label>
          <select id="entity-select">
            <option value="">Select an entity...</option>
            ${entities.map((entityId) => `
              <option value="${esc(entityId)}" ${entityId === currentEntity ? "selected" : ""}>
                ${esc(entityId)}
              </option>
            `).join("")}
          </select>
          <div class="hint">Only FritzMesh topology sensors are shown here.</div>
        </div>

        <div>
          <label for="entity-input">Or enter entity id manually</label>
          <input
            id="entity-input"
            type="text"
            placeholder="sensor.fritz_box_mesh_..._topology"
            value="${esc(currentEntity)}"
          />
        </div>

        <div>
          <label for="title-input">Card title (optional)</label>
          <input
            id="title-input"
            type="text"
            placeholder="Fritz!Box Mesh Topology"
            value="${esc(currentTitle)}"
          />
          <div class="hint">Leave empty to use default title.</div>
        </div>

        <div>
          <label for="name-info-display">Name detail display</label>
          <select id="name-info-display">
            <option value="mesh_node" ${currentNameInfoDisplay === "mesh_node" ? "selected" : ""}>Connected mesh node</option>
            <option value="connection_state" ${currentNameInfoDisplay === "connection_state" ? "selected" : ""}>Connection state</option>
          </select>
          <div class="hint">Defines what the More Info popup shows when clicking a device name.</div>
        </div>

        <div>
          <label for="node-sort">Node sorting</label>
          <select id="node-sort">
            <option value="default" ${currentNodeSort === "default" ? "selected" : ""}>Default</option>
            <option value="name" ${currentNodeSort === "name" ? "selected" : ""}>By name</option>
            <option value="ip" ${currentNodeSort === "ip" ? "selected" : ""}>By IP</option>
            <option value="mac" ${currentNodeSort === "mac" ? "selected" : ""}>By MAC</option>
          </select>
          <div class="hint">Sorts slave/repeater nodes in the topology list.</div>
        </div>

        <div>
          <label for="transfer-metric-mode">Transfer metric mode</label>
          <select id="transfer-metric-mode">
            <option value="aggregate" ${currentTransferMetricMode === "aggregate" ? "selected" : ""}>Aggregate</option>
            <option value="uplink" ${currentTransferMetricMode === "uplink" ? "selected" : ""}>Uplink only</option>
            <option value="max_single" ${currentTransferMetricMode === "max_single" ? "selected" : ""}>Max single client</option>
            <option value="average" ${currentTransferMetricMode === "average" ? "selected" : ""}>Average client</option>
          </select>
          <div class="hint">Controls TX/RX metric shown on master and repeater cards.</div>
        </div>

        <div>
          <label class="toggle-row" for="hide-offline-nodes">
            <input
              id="hide-offline-nodes"
              type="checkbox"
              ${currentHideOfflineNodes ? "checked" : ""}
            />
            <span>Hide offline nodes</span>
          </label>
          <div class="hint">Hides disconnected repeaters, disconnected clients, and all unassigned devices.</div>
        </div>

        <div>
          <label for="url-template">URL template (for IP clicks)</label>
          <input
            id="url-template"
            type="text"
            placeholder="http://{ip}"
            value="${esc(currentUrlTemplate)}"
          />
          <div class="hint">Use <code>{ip}</code> as placeholder, e.g. <code>https://{ip}</code>.</div>
        </div>

        <div>
          <label for="line-color">Line color</label>
          <input id="line-color" type="color" value="${esc(currentLineColor)}" />
        </div>

        <div>
          <label for="accent-color">Accent color</label>
          <input id="accent-color" type="color" value="${esc(currentAccentColor)}" />
        </div>

        <div>
          <label for="text-dim-color">Secondary text color</label>
          <input id="text-dim-color" type="color" value="${esc(currentTextDimColor)}" />
        </div>

        <div>
          <label for="font-scale">Font size scale (%)</label>
          <input id="font-scale" type="number" min="80" max="140" step="1" value="${esc(String(currentFontScale))}" />
          <div class="hint">Scales all card text from 80% to 140%.</div>
        </div>

        <div>
          <label for="master-panel-start-color">Master panel gradient start</label>
          <input id="master-panel-start-color" type="color" value="${esc(currentMasterPanelStart)}" />
        </div>

        <div>
          <label for="master-panel-end-color">Master panel gradient end</label>
          <input id="master-panel-end-color" type="color" value="${esc(currentMasterPanelEnd)}" />
        </div>
      </div>`;

    const entitySelect = this.shadowRoot.querySelector("#entity-select");
    const entityInput = this.shadowRoot.querySelector("#entity-input");
    const titleInput = this.shadowRoot.querySelector("#title-input");
    const nameInfoDisplayInput = this.shadowRoot.querySelector("#name-info-display");
    const nodeSortInput = this.shadowRoot.querySelector("#node-sort");
    const transferMetricModeInput = this.shadowRoot.querySelector("#transfer-metric-mode");
    const hideOfflineNodesInput = this.shadowRoot.querySelector("#hide-offline-nodes");
    const urlTemplateInput = this.shadowRoot.querySelector("#url-template");
    const lineColorInput = this.shadowRoot.querySelector("#line-color");
    const accentColorInput = this.shadowRoot.querySelector("#accent-color");
    const textDimColorInput = this.shadowRoot.querySelector("#text-dim-color");
    const fontScaleInput = this.shadowRoot.querySelector("#font-scale");
    const masterPanelStartColorInput = this.shadowRoot.querySelector("#master-panel-start-color");
    const masterPanelEndColorInput = this.shadowRoot.querySelector("#master-panel-end-color");

    entitySelect?.addEventListener("change", (e) => {
      const val = e.target.value;
      const cfg = { ...this._config };
      if (val) cfg.entity = val;
      this._dispatch(cfg);
      if (entityInput) entityInput.value = val;
    });

    entityInput?.addEventListener("change", (e) => {
      const val = e.target.value?.trim();
      const cfg = { ...this._config };
      if (val) cfg.entity = val;
      else delete cfg.entity;
      this._dispatch(cfg);
    });

    titleInput?.addEventListener("change", (e) => {
      const val = e.target.value;
      const cfg = { ...this._config };
      if (val !== "") cfg.title = val;
      else delete cfg.title;
      this._dispatch(cfg);
    });

    nameInfoDisplayInput?.addEventListener("change", (e) => {
      const val = e.target.value;
      const cfg = { ...this._config, name_info_display: val };
      this._dispatch(cfg);
    });

    nodeSortInput?.addEventListener("change", (e) => {
      const val = e.target.value;
      const cfg = { ...this._config, node_sort: val };
      this._dispatch(cfg);
    });

    transferMetricModeInput?.addEventListener("change", (e) => {
      const val = e.target.value;
      const cfg = { ...this._config, transfer_metric_mode: val };
      this._dispatch(cfg);
    });

    hideOfflineNodesInput?.addEventListener("change", (e) => {
      const cfg = { ...this._config };
      if (e.target.checked) cfg.hide_offline_nodes = true;
      else delete cfg.hide_offline_nodes;
      this._dispatch(cfg);
    });

    urlTemplateInput?.addEventListener("change", (e) => {
      const val = e.target.value?.trim();
      const cfg = { ...this._config };
      if (val) cfg.url_template = val;
      else delete cfg.url_template;
      this._dispatch(cfg);
    });

    lineColorInput?.addEventListener("change", (e) => {
      const cfg = { ...this._config, line_color: sanitizeHexColor(e.target.value, "#4caf50") };
      this._dispatch(cfg);
    });

    accentColorInput?.addEventListener("change", (e) => {
      const cfg = { ...this._config, accent_color: sanitizeHexColor(e.target.value, "#1976d2") };
      this._dispatch(cfg);
    });

    textDimColorInput?.addEventListener("change", (e) => {
      const cfg = { ...this._config, text_dim_color: sanitizeHexColor(e.target.value, "#888888") };
      this._dispatch(cfg);
    });

    fontScaleInput?.addEventListener("change", (e) => {
      const cfg = { ...this._config, font_scale: sanitizeFontScale(e.target.value, 100) };
      this._dispatch(cfg);
    });

    masterPanelStartColorInput?.addEventListener("change", (e) => {
      const cfg = {
        ...this._config,
        master_panel_start_color: sanitizeHexColor(e.target.value, "#1565c0"),
      };
      this._dispatch(cfg);
    });

    masterPanelEndColorInput?.addEventListener("change", (e) => {
      const cfg = {
        ...this._config,
        master_panel_end_color: sanitizeHexColor(e.target.value, "#1e88e5"),
      };
      this._dispatch(cfg);
    });
  }

  /** Fire the config-changed event that HA listens for. */
  _dispatch(config) {
    this._config = config;
    this.dispatchEvent(new CustomEvent("config-changed", {
      detail:   { config },
      bubbles:  true,
      composed: true,
    }));
  }

  /**
   * Filter Lovelace entity picker to sensors that look like FritzMesh topology.
   *
   * We intentionally allow fallback by entity-id pattern for startup timing:
   * states can be incomplete while the editor first opens.
   */
  _isApplicableEntity(entityId) {
    if (!entityId || !entityId.startsWith("sensor.")) return false;

    const state = this._hass?.states?.[entityId];
    const attrs = state?.attributes ?? {};

    // Strong signal: topology payload attributes provided by this integration.
    if (Array.isArray(attrs.mesh_nodes)) return true;
    if (Array.isArray(attrs.unassigned_clients)) return true;

    // Fallback signal for early startup or unavailable state object.
    return entityId.endsWith("_topology");
  }

  _applicableEntities() {
    if (!this._hass?.states) return [];
    return Object.keys(this._hass.states)
      .filter((entityId) => this._isApplicableEntity(entityId))
      .sort((a, b) => a.localeCompare(b));
  }
}

// ── Styles ────────────────────────────────────────────────────────────────────
//
// All CSS is scoped to the shadow DOM, so it cannot leak into the surrounding
// Lovelace dashboard and vice versa.  We use CSS custom properties (variables)
// to pull colours from the active HA theme where possible.
//
// Layout summary:
//   .layout        – flex row: left panel + right tree
//   .master-panel  – sticky left column (blue gradient card)
//   .tree          – right scrollable column with a vertical green backbone line
//   .section       – one row of the tree (master clients, or one slave + clients)
//   .section-row   – horizontal branch from backbone to the label/slave-card
//   .clients       – stacked list of client rows within a section
//   .client-row    – single device row (line + label + icon + name + IP)

const STYLES = `
/* ── CSS custom properties (theme integration) ── */
:host {
  display: block;
  height: 100%;
  min-height: 0;
  font-size: var(--fm-font-scale, 100%);
  /* Green for connection lines; we avoid using HA theme green to guarantee
     visibility on both light and dark themes. */
  --green:      #4caf50;
  --green-fade: rgba(76,175,80,.18);
  /* Blue for active device names and slave icon colour (LAN-connected). */
  --blue-dark:  #1565c0;
  --blue:       #1976d2;
  /* Pull text and background colours from the active HA theme. */
  --text-dim:   var(--secondary-text-color, #888);
  --card-bg:    var(--card-background-color, #fff);
  --divider:    var(--divider-color, #e0e0e0);
  --sec-bg:     var(--secondary-background-color, #f5f5f5);
}
/* Clip and stretch card body for grid-constrained layouts. */
ha-card {
  overflow: hidden;
  height: 100%;
  min-height: 0;
  display: flex;
  flex-direction: column;
}

/* ── Card header ── */
.card-header {
  padding: 14px 16px 10px;
  font-size: 1.05em;
  font-weight: 700;
  color: var(--primary-text-color);
  border-bottom: 1px solid var(--divider);
}
/* Scroll both directions when card is constrained by grid rows/columns. */
.card-body {
  padding: 12px 14px 16px;
  overflow: auto;
  flex: 1;
  min-height: 0;
  box-sizing: border-box;
}

/* ── Two-column layout ── */
.layout {
  display: flex;
  gap: 16px;
  align-items: flex-start; /* both columns start at the top */
  min-width: 0;
  min-height: 0;
}

/* ── LEFT: master Fritz!Box panel ── */
.master-panel {
  flex-shrink: 0;    /* never shrink; keep a fixed width */
  width: 152px;
  /* Blue gradient matching AVM's brand colours */
  background: linear-gradient(155deg, var(--master-panel-start, #1565c0) 0%, var(--master-panel-end, #1e88e5) 100%);
  color: #fff;
  border-radius: 12px;
  padding: 14px 12px;
  box-shadow: 0 3px 10px rgba(21,101,192,.4);
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 3px;
  /* Sticky: the master panel stays in view as the user scrolls the tree. */
  position: sticky;
  top: 0;
  align-self: flex-start;
}
.mp-icon { width: 52px; height: 52px; color: rgba(255,255,255,.9); margin-bottom: 2px; }
.mp-icon svg { width: 100%; height: 100%; }
.mp-name  { font-weight: 700; font-size: .9em; line-height: 1.25; }
.mp-model { font-size: .72em; opacity: .82; margin-top: 1px; }
.mp-ip    { font-size: .72em; font-family: monospace; opacity: .9; }
.mp-fw    { font-size: .66em; opacity: .65; }
.mp-rate {
  margin-top: 4px;
  font-size: .66em;
  opacity: .92;
  display: flex;
  align-items: center;
  gap: 4px;
}
.mp-rate svg { width: 12px; height: 12px; }
/* "HEIMNETZ" badge (German for "home network") – AVM branding convention. */
.mp-badge {
  margin-top: 8px;
  background: rgba(255,255,255,.22);  /* semi-transparent white pill */
  border-radius: 4px;
  padding: 2px 10px;
  font-size: .66em;
  font-weight: 800;
  letter-spacing: .1em;
}

/* ── RIGHT: tree column ── */
.tree {
  flex: 1;         /* take up remaining horizontal space */
  min-width: 0;    /* allow shrinking below content width (prevents overflow) */
  min-height: 0;
  /* The vertical green line that forms the tree backbone.
     Each section branches off this line with a ::before pseudo-element. */
  border-left: 2px solid var(--green);
  margin-left: 4px;
}

/* Medium cards: slightly tighter spacing and narrower master panel. */
:host([data-size="medium"]) .layout { gap: 12px; }
:host([data-size="medium"]) .master-panel { width: 132px; padding: 12px 10px; position: static; }
:host([data-size="medium"]) .mp-icon { width: 44px; height: 44px; }
:host([data-size="medium"]) .cl-label { min-width: 96px; }
:host([data-size="medium"]) .cl-line { width: 34px; }

/* Compact cards: stack master panel above tree for narrow columns. */
:host([data-size="compact"]) .layout {
  flex-direction: column;
  gap: 10px;
}
:host([data-size="compact"]) .master-panel {
  position: static;
  width: auto;
  max-width: none;
  align-self: stretch;
  border-radius: 10px;
}
:host([data-size="compact"]) .tree {
  border-left: none;
  margin-left: 0;
}
:host([data-size="compact"]) .section {
  padding-left: 0;
}
:host([data-size="compact"]) .section-row::before {
  display: none;
}
:host([data-size="compact"]) .h-line,
:host([data-size="compact"]) .cl-line {
  width: 14px;
}
:host([data-size="compact"]) .clients {
  padding-left: 0;
}
:host([data-size="compact"]) .slave-card {
  min-width: 0;
  max-width: 100%;
}
:host([data-size="compact"]) .cl-label {
  min-width: 78px;
}

/* ── Section: one entry in the tree (master clients or one slave + clients) ── */
.section {
  padding: 10px 0 6px 22px;  /* left padding creates space for the branch lines */
  position: relative;
}
/* Last section: reduce bottom padding so the backbone line doesn't overshoot. */
.section:last-child {
  padding-bottom: 2px;
}

/* ── Section row: the horizontal branch from backbone to label / slave card ── */
.section-row {
  display: flex;
  align-items: center;
  gap: 5px;
  position: relative;
  margin-bottom: 6px;
}
/*
 * The ::before pseudo-element draws the short horizontal stub that connects
 * the vertical backbone to the label or slave card.  It is placed on
 * .section-row (rather than .section) so that top:50% + translateY(-50%)
 * perfectly centres the line with the row content, regardless of the row's
 * height (which varies between the simple "Clients" label and the taller
 * slave card).
 */
.section-row::before {
  content: "";
  position: absolute;
  left: -22px;        /* aligns with the padding-left on .section */
  top: 50%;
  transform: translateY(-50%);
  width: 22px;
  height: 2px;
  background: var(--green);
}

/* ── Horizontal extension line (backbone → label in slave sections) ── */
/* Extends the visual connection between the backbone and the LAN/WiFi badge. */
.h-line {
  height: 2px;
  width: 22px;
  flex-shrink: 0;
}
/* Solid line for LAN connections. */
.h-line.lan  { background: var(--green); }
/* Dashed line for WiFi connections (repeating gradient trick). */
.h-line.wifi {
  background-image: repeating-linear-gradient(
    to right, var(--green) 0, var(--green) 5px,
    transparent 5px, transparent 10px);
}

/* ── Row label (e.g. "LAN", "WiFi", "Clients") ── */
.row-label {
  font-size: .68em;
  font-weight: 600;
  white-space: nowrap;
  flex-shrink: 0;
  border-radius: 3px;
  padding: 1px 6px;
  border: 1px solid var(--green-fade);
  color: var(--green);
  background: var(--green-fade);
}
/* WiFi label uses a slightly lighter green to visually distinguish from LAN. */
.wifi-label { color: #66bb6a; border-color: rgba(102,187,106,.3); background: rgba(102,187,106,.1); }
.lan-label  { color: var(--green); }

/* ── Slave repeater card ── */
.slave-card {
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 7px 11px 7px 9px;
  border-radius: 9px;
  background: var(--sec-bg);
  border: 1px solid var(--divider);
  flex-shrink: 0;
  min-width: 120px;
  max-width: 220px;   /* prevent very long names from breaking the layout */
}
/* Unassigned section card gets a dashed border and reduced opacity. */
.unassigned { opacity: .65; border-style: dashed; }

.sc-icon      { width: 26px; height: 26px; flex-shrink: 0; }
.sc-icon svg  { width: 100%; height: 100%; }
/* LAN-connected slave → blue icon to match the master panel colour. */
.sc-lan       { color: var(--blue); }
/* WiFi-connected slave → green icon to match the WiFi line colour. */
.sc-wifi      { color: #43a047; }

.sc-info  { min-width: 0; }  /* allow text to shrink and truncate */
.sc-name  { font-weight: 700; font-size: .88em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sc-model { font-size: .7em; color: var(--text-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 1px; }
.sc-rate {
  margin-top: 2px;
  font-size: .66em;
  color: var(--text-dim);
  display: flex;
  align-items: center;
  gap: 4px;
}
.sc-rate svg { width: 12px; height: 12px; }
.sc-badge {
  display: inline-block;
  margin-top: 4px;
  background: var(--blue);
  color: #fff;
  font-size: .6em;
  font-weight: 800;
  letter-spacing: .07em;
  padding: 1px 5px;
  border-radius: 3px;
}

/* ── Client list within a section ── */
.clients {
  display: flex;
  flex-direction: column;
  gap: 1px;           /* tight spacing for dense lists */
  padding-left: 10px; /* indent relative to the section backbone */
}

/* ── Individual client row ── */
.client-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 26px;
  padding: 2px 0;
}
/* Disconnected devices fade out to distinguish them from active ones. */
.client-row.off { opacity: .38; }

/* Short horizontal line connecting the client to its section's backbone. */
.cl-line       { flex-shrink: 0; width: 48px; height: 2px; }
.cl-line.lan   { background: var(--green); }
.cl-line.wifi  {
  background-image: repeating-linear-gradient(
    to right, var(--green) 0, var(--green) 5px,
    transparent 5px, transparent 10px);
}

/* Speed / band label (e.g. "5 GHz → 867 Mbit/s"). */
.cl-label {
  font-size: .7em;
  color: var(--text-dim);
  min-width: 118px;   /* fixed width keeps device names left-aligned */
  flex-shrink: 0;
  white-space: nowrap;
}

/* Small WiFi / LAN icon next to the speed label. */
.cl-icon      { width: 15px; height: 15px; flex-shrink: 0; color: var(--text-dim); }
.cl-icon svg  { width: 100%; height: 100%; }

/* Device hostname: blue + bold when connected, plain when disconnected. */
.cl-name { font-size: .84em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.client-row:not(.off) .cl-name { color: var(--blue); font-weight: 500; }

/* IP address in monospace (only shown when known). */
.cl-ip { font-size: .68em; font-family: monospace; color: var(--text-dim); white-space: nowrap; margin-left: 2px; flex-shrink: 0; }

/* Click actions (name and IP) */
.client-action {
  border: 0;
  padding: 0;
  margin: 0;
  background: transparent;
  text-align: left;
  cursor: pointer;
  font: inherit;
}
.client-action:hover {
  text-decoration: underline;
}
.client-action:focus-visible {
  outline: 2px solid var(--blue);
  outline-offset: 1px;
  border-radius: 2px;
}

/* Placeholder shown when a mesh node has no clients. */
.no-clients { font-size: .8em; color: var(--text-dim); font-style: italic; padding: 4px 0; }

/* ── Status / error messages ── */
.msg       { display: flex; align-items: center; gap: 10px; padding: 16px 0; font-size: .9em; color: var(--text-dim); }
.msg.warn  { color: var(--warning-color, #e6a817); }  /* orange for unavailable state */
.msg svg   { width: 24px; height: 24px; flex-shrink: 0; }
`;

// ── Registration ──────────────────────────────────────────────────────────────
//
// customElements.define() registers FritzMeshCard as the implementation for
// the "fritzmesh-card" custom element tag.  The guard prevents an error if
// the script is somehow loaded twice (e.g. after a hot-reload).

if (!customElements.get("fritzmesh-card")) {
  customElements.define("fritzmesh-card", FritzMeshCard);
  customElements.define("fritzmesh-card-editor", FritzMeshCardEditor);

  // On a cold (uncached) load HA renders the dashboard before this script
  // finishes downloading.  It finds "fritzmesh-card" undefined, shows
  // "Configuration error", and does not retry on its own.
  //
  // Fix: dispatch `ll-rebuild` directly on `hui-root`, the Lovelace shadow
  // component that owns the handler.  Dispatching on `window` does NOT work
  // because events cannot travel downward through shadow DOM boundaries.
  //
  // We retry until hui-root is found (it may not be mounted yet if this
  // script loads very quickly, before Lovelace has initialised).
  (function _rebuildLovelace(retriesLeft) {
    const huiRoot = document
      .querySelector("home-assistant")
      ?.shadowRoot?.querySelector("home-assistant-main")
      ?.shadowRoot?.querySelector("ha-panel-lovelace")
      ?.shadowRoot?.querySelector("hui-root");

    if (huiRoot) {
      console.info("[fritzmesh-card] dispatching ll-rebuild → hui-root");
      huiRoot.dispatchEvent(new Event("ll-rebuild"));
    } else if (retriesLeft > 0) {
      setTimeout(() => _rebuildLovelace(retriesLeft - 1), 250);
    } else {
      console.warn("[fritzmesh-card] hui-root not found after retries");
    }
  })(20); // up to 20 × 250 ms = 5 s of retries

  // Log a styled banner to the browser console so users can confirm the
  // card version at a glance when troubleshooting.
  console.info(
    `%c FRITZMESH-CARD %c v${CARD_VERSION} `,
    "color:#fff;background:#1565c0;font-weight:700;padding:2px 4px;border-radius:3px 0 0 3px",
    "color:#1565c0;background:#fff;font-weight:700;padding:2px 4px;border-radius:0 3px 3px 0;border:1px solid #1565c0"
  );
}

// Register the card in HA's custom-cards registry so it appears in the
// dashboard card picker UI with a name and description.
// The ??= operator only assigns if window.customCards is null/undefined.
window.customCards ??= [];
window.customCards.push({
  type:        "fritzmesh-card",
  name:        "Fritz!Box Mesh Topology",
  description: "Visualises which devices are connected to which mesh node.",
  preview:     false,  // set true to show a live preview in the card picker
});
