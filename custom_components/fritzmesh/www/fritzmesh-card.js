/**
 * Fritz!Box Mesh Topology Card  –  v1.1.0
 *
 * Visualises the mesh network tree: master Fritz!Box → slave repeaters → clients.
 * Solid green lines = LAN · dashed green lines = WiFi
 *
 * Card config:
 *   type: custom:fritzmesh-card
 *   entity: sensor.fritzmesh_topology
 *   title: Fritz!Box Mesh              # optional; set "" to hide
 */

const CARD_VERSION = "1.1.0";

// ── Inline SVG icons (no external dependency needed) ──────────────────────────

const ICON = {
  router: `<svg viewBox="0 0 24 24" fill="currentColor">
    <path d="M20.2 5.9l.8-.8C19.6 3.7 17.9 3 16 3s-3.6.7-4.8 1.8l.8.8C13 4.6
    14.4 4 16 4s3 .6 4.2 1.9zM19.4 6.7c-.9-.9-2.1-1.4-3.4-1.4s-2.5.5-3.4
    1.4l.8.8C14.1 6.8 15 6.4 16 6.4s1.9.4 2.6 1.1l.8-.8zM19
    13h-2V9h-2v4H4c-1.1 0-2 .9-2 2v4c0 1.1.9 2 2 2h15c1.1 0 2-.9
    2-2v-4c0-1.1-.9-2-2-2zm0 6H4v-4h15v4zM6 17.5C6 18.3 5.3 19 4.5
    19S3 18.3 3 17.5 3.7 16 4.5 16 6 16.7 6 17.5z"/>
  </svg>`,
  ap: `<svg viewBox="0 0 24 24" fill="currentColor">
    <path d="M12 3C6.95 3 3.15 5.85 2 9.7L3.72 10C4.73 6.79 8.1 4.5 12
    4.5c3.9 0 7.27 2.29 8.28 5.5L22 9.7C20.85 5.85 17.05 3 12 3zm0
    4c-2.44 0-4.44 1.46-5.2 3.5L8.6 11c.6-1.39 1.99-2.38 3.4-2.38s2.8.99
    3.4 2.38l1.8-.5C16.44 8.46 14.44 7 12 7zm0 4c-1.1 0-2 .9-2 2s.9 2 2
    2 2-.9 2-2-.9-2-2-2zm0 5c-.55 0-1 .45-1 1v4h2v-4c0-.55-.45-1-1-1z"/>
  </svg>`,
  wifi: `<svg viewBox="0 0 24 24" fill="currentColor">
    <path d="M1 9l2 2c4.97-4.97 13.03-4.97 18 0l2-2C16.93 2.93 7.08 2.93
    1 9zm8 8l3 3 3-3c-1.65-1.66-4.34-1.66-6 0zm-4-4l2 2c2.76-2.76 7.24-2.76
    10 0l2-2C15.14 9.14 8.87 9.14 5 13z"/>
  </svg>`,
  lan: `<svg viewBox="0 0 24 24" fill="currentColor">
    <path d="M8 3H5L2 7l3 4h3l1.5 2H9c-1.1 0-2 .9-2 2v4c0 1.1.9 2 2
    2h6c1.1 0 2-.9 2-2v-4c0-1.1-.9-2-2-2h-1.5L15 11h3l3-4-3-4h-3l-3
    4v.17L10 8.83V8l-2-5zm1 10h6v4H9v-4z"/>
  </svg>`,
  unknown: `<svg viewBox="0 0 24 24" fill="currentColor">
    <path d="M11 18h2v-2h-2v2zm1-16C6.48 2 2 6.48 2 12s4.48 10 10
    10 10-4.48 10-10S17.52 2 12 2zm0 18c-4.41 0-8-3.59-8-8s3.59-8
    8-8 8 3.59 8 8-3.59 8-8 8zm0-14c-2.21 0-4 1.79-4 4h2c0-1.1.9-2
    2-2s2 .9 2 2c0 2-3 1.75-3 5h2c0-2.25 3-2.5 3-5 0-2.21-1.79-4-4-4z"/>
  </svg>`,
};

// ── Helpers ────────────────────────────────────────────────────────────────────

const esc = (s) =>
  String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");

function fmtSpeed(kbps) {
  if (!kbps || kbps <= 0) return null;
  if (kbps >= 1_000_000) return `${(kbps / 1_000_000).toFixed(0)} Gbit/s`;
  if (kbps >= 1_000)     return `${Math.round(kbps / 1_000)} Mbit/s`;
  return `${kbps} kbit/s`;
}

function getBand(ifaceName) {
  if (!ifaceName) return null;
  const u = ifaceName.toUpperCase();
  if (u.includes("5G"))                         return "5 GHz";
  if (u.includes("2G") || u.startsWith("AP:2")) return "2,4 GHz";
  if (u.startsWith("LAN") || u.startsWith("ETH")) return "LAN";
  return null;
}

function connLabel(c) {
  if (c.connection_type === "WLAN") {
    const band = getBand(c.interface_name) ?? "WiFi";
    const spd  = fmtSpeed(c.cur_rx_kbps || c.max_rx_kbps);
    return spd ? `${band} → ${spd}` : band;
  }
  const spd = fmtSpeed(c.max_rx_kbps || c.cur_rx_kbps);
  return spd ? `LAN → ${spd}` : "LAN";
}

const clientSort = (a, b) => {
  if (a.connection_state !== b.connection_state)
    return a.connection_state === "CONNECTED" ? -1 : 1;
  return (a.name || a.mac || "").localeCompare(b.name || b.mac || "");
};

// ── Card component ─────────────────────────────────────────────────────────────

class FritzMeshCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config  = null;
    this._lastKey = "";
  }

  static getStubConfig() {
    return { entity: "sensor.fritzmesh_topology" };
  }

  setConfig(config) {
    if (!config.entity)
      throw new Error("fritzmesh-card: set `entity` to your topology sensor.");
    this._config = config;
  }

  set hass(hass) {
    if (!this._config) return;
    const state = hass?.states?.[this._config.entity];
    const key   = JSON.stringify(state?.attributes);
    if (key === this._lastKey) return;
    this._lastKey = key;
    this._render(state);
  }

  getCardSize() {
    const nodes   = this._lastKey ? (JSON.parse(this._lastKey)?.mesh_nodes ?? []) : [];
    const clients = nodes.reduce((s, n) => s + (n.clients?.length ?? 0), 0);
    return Math.max(4, Math.ceil((nodes.length * 3 + clients) / 4));
  }

  // ── Render ───────────────────────────────────────────────────────────────

  _render(state) {
    const title = this._config.title !== undefined
      ? this._config.title
      : "Fritz!Box Mesh Topology";

    if (!state || state.state === "unavailable") {
      this._setHTML(title, `
        <div class="msg warn">
          ${ICON.unknown}
          Entity <code>${esc(this._config.entity)}</code> is unavailable.
        </div>`);
      return;
    }

    const attrs      = state.attributes ?? {};
    const nodes      = attrs.mesh_nodes ?? [];
    const unassigned = attrs.unassigned_clients ?? [];
    const host       = attrs.host ?? "";

    if (!nodes.length) {
      this._setHTML(title,
        `<div class="msg">No topology data yet — waiting for first coordinator update.</div>`);
      return;
    }

    const master = nodes.find((n) => n.role === "master") ?? nodes[0];
    const slaves = nodes.filter((n) => n !== master);

    this._setHTML(title, `
      <div class="layout">
        ${this._masterPanel(master, host)}
        <div class="tree">
          ${this._masterSection(master)}
          ${slaves.map((s) => this._slaveSection(s)).join("")}
          ${unassigned.length ? this._unassignedSection(unassigned) : ""}
        </div>
      </div>`);
  }

  // ── Left panel ──────────────────────────────────────────────────────────

  _masterPanel(node, host) {
    return `
      <div class="master-panel">
        <div class="mp-icon">${ICON.router}</div>
        <div class="mp-name">${esc(node?.name ?? "Fritz!Box")}</div>
        ${node?.model    ? `<div class="mp-model">${esc(node.model)}</div>` : ""}
        ${host           ? `<div class="mp-ip">${esc(host)}</div>` : ""}
        ${node?.firmware ? `<div class="mp-fw">FW ${esc(node.firmware)}</div>` : ""}
        <div class="mp-badge">HEIMNETZ</div>
      </div>`;
  }

  // ── Master section (direct clients) ─────────────────────────────────────

  _masterSection(node) {
    const clients = [...(node?.clients ?? [])].sort(clientSort);
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

  // ── Slave section ────────────────────────────────────────────────────────

  _slaveSection(node) {
    const clients  = [...(node?.clients ?? [])].sort(clientSort);
    const linkType = node.parent_link_type || "LAN";
    const isWifi   = linkType === "WLAN";

    return `
      <div class="section">
        <div class="section-row">
          <div class="h-line ${isWifi ? "wifi" : "lan"}"></div>
          <span class="row-label ${isWifi ? "wifi-label" : "lan-label"}">${isWifi ? "WiFi" : "LAN"}</span>
          <div class="slave-card">
            <div class="sc-icon ${isWifi ? "sc-wifi" : "sc-lan"}">${ICON.ap}</div>
            <div class="sc-info">
              <div class="sc-name">${esc(node.name)}</div>
              ${node.model ? `<div class="sc-model">${esc(node.model)}</div>` : ""}
              <div class="sc-badge">${isWifi ? "WIFI REPEATER" : "REPEATER"}</div>
            </div>
          </div>
        </div>
        <div class="clients">
          ${clients.length
            ? clients.map((c) => this._clientRow(c)).join("")
            : '<div class="no-clients">No clients</div>'}
        </div>
      </div>`;
  }

  // ── Unassigned section ───────────────────────────────────────────────────

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
          ${[...clients].sort(clientSort).map((c) => this._clientRow(c)).join("")}
        </div>
      </div>`;
  }

  // ── Client row ───────────────────────────────────────────────────────────

  _clientRow(client) {
    const on    = client.connection_state === "CONNECTED";
    const wifi  = client.connection_type === "WLAN";
    const label = connLabel(client);
    const name  = client.name || client.mac || "?";

    return `
      <div class="client-row${on ? "" : " off"}">
        <div class="cl-line ${wifi ? "wifi" : "lan"}"></div>
        <span class="cl-label">${esc(label)}</span>
        <span class="cl-icon">${wifi ? ICON.wifi : ICON.lan}</span>
        <span class="cl-name">${esc(name)}</span>
        ${client.ip ? `<span class="cl-ip">${esc(client.ip)}</span>` : ""}
      </div>`;
  }

  // ── Scaffold ─────────────────────────────────────────────────────────────

  _setHTML(title, body) {
    this.shadowRoot.innerHTML = `
      <style>${STYLES}</style>
      <ha-card>
        ${title ? `<div class="card-header">${esc(title)}</div>` : ""}
        <div class="card-body">${body}</div>
      </ha-card>`;
  }
}

// ── Styles ─────────────────────────────────────────────────────────────────────

const STYLES = `
/* ── Variables ── */
:host {
  display: block;
  --green:      #4caf50;
  --green-fade: rgba(76,175,80,.18);
  --blue-dark:  #1565c0;
  --blue:       #1976d2;
  --text-dim:   var(--secondary-text-color, #888);
  --card-bg:    var(--card-background-color, #fff);
  --divider:    var(--divider-color, #e0e0e0);
  --sec-bg:     var(--secondary-background-color, #f5f5f5);
}
ha-card { overflow: hidden; }

/* ── Header ── */
.card-header {
  padding: 14px 16px 10px;
  font-size: 1.05em;
  font-weight: 700;
  color: var(--primary-text-color);
  border-bottom: 1px solid var(--divider);
}
.card-body { padding: 12px 14px 16px; overflow-x: auto; }

/* ── Two-column layout ── */
.layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
  min-width: 380px;
}

/* ── LEFT: master panel ── */
.master-panel {
  flex-shrink: 0;
  width: 152px;
  background: linear-gradient(155deg, #1565c0 0%, #1e88e5 100%);
  color: #fff;
  border-radius: 12px;
  padding: 14px 12px;
  box-shadow: 0 3px 10px rgba(21,101,192,.4);
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 3px;
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
.mp-badge {
  margin-top: 8px;
  background: rgba(255,255,255,.22);
  border-radius: 4px;
  padding: 2px 10px;
  font-size: .66em;
  font-weight: 800;
  letter-spacing: .1em;
}

/* ── RIGHT: tree ── */
.tree {
  flex: 1;
  min-width: 0;
  /* Vertical backbone */
  border-left: 2px solid var(--green);
  margin-left: 4px;
}

/* ── Section (one per slave / master clients) ── */
.section {
  padding: 10px 0 6px 22px;
  position: relative;
}
/* Last section: shorten backbone so it doesn't overshoot */
.section:last-child {
  padding-bottom: 2px;
}

/* ── Section row: the horizontal "branch" from backbone ── */
.section-row {
  display: flex;
  align-items: center;
  gap: 5px;
  position: relative;
  margin-bottom: 6px;
}
/*
 * KEY FIX: ::before is placed on .section-row (not .section),
 * so top:50% + translateY(-50%) perfectly centres the branch line
 * with whatever content height the row has (slave card or label).
 */
.section-row::before {
  content: "";
  position: absolute;
  left: -22px;
  top: 50%;
  transform: translateY(-50%);
  width: 22px;
  height: 2px;
  background: var(--green);
}

/* ── Horizontal extension line (backbone→label) ── */
.h-line {
  height: 2px;
  width: 22px;
  flex-shrink: 0;
}
.h-line.lan  { background: var(--green); }
.h-line.wifi {
  background-image: repeating-linear-gradient(
    to right, var(--green) 0, var(--green) 5px,
    transparent 5px, transparent 10px);
}

/* ── Row label (LAN / WiFi / Clients) ── */
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
.wifi-label { color: #66bb6a; border-color: rgba(102,187,106,.3); background: rgba(102,187,106,.1); }
.lan-label  { color: var(--green); }

/* ── Slave node card ── */
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
  max-width: 220px;
}
.unassigned { opacity: .65; border-style: dashed; }

.sc-icon      { width: 26px; height: 26px; flex-shrink: 0; }
.sc-icon svg  { width: 100%; height: 100%; }
.sc-lan       { color: var(--blue); }
.sc-wifi      { color: #43a047; }

.sc-info  { min-width: 0; }
.sc-name  { font-weight: 700; font-size: .88em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.sc-model { font-size: .7em; color: var(--text-dim); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; margin-top: 1px; }
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

/* ── Client list ── */
.clients {
  display: flex;
  flex-direction: column;
  gap: 1px;
  padding-left: 10px;
}

.client-row {
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 26px;
  padding: 2px 0;
}
.client-row.off { opacity: .38; }

/* Connection dash/solid line */
.cl-line       { flex-shrink: 0; width: 48px; height: 2px; }
.cl-line.lan   { background: var(--green); }
.cl-line.wifi  {
  background-image: repeating-linear-gradient(
    to right, var(--green) 0, var(--green) 5px,
    transparent 5px, transparent 10px);
}

/* Speed / band label */
.cl-label {
  font-size: .7em;
  color: var(--text-dim);
  min-width: 118px;
  flex-shrink: 0;
  white-space: nowrap;
}

/* Connection type icon */
.cl-icon      { width: 15px; height: 15px; flex-shrink: 0; color: var(--text-dim); }
.cl-icon svg  { width: 100%; height: 100%; }

/* Device name */
.cl-name { font-size: .84em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.client-row:not(.off) .cl-name { color: var(--blue); font-weight: 500; }

/* IP address */
.cl-ip { font-size: .68em; font-family: monospace; color: var(--text-dim); white-space: nowrap; margin-left: 2px; flex-shrink: 0; }

.no-clients { font-size: .8em; color: var(--text-dim); font-style: italic; padding: 4px 0; }

/* Status / error messages */
.msg       { display: flex; align-items: center; gap: 10px; padding: 16px 0; font-size: .9em; color: var(--text-dim); }
.msg.warn  { color: var(--warning-color, #e6a817); }
.msg svg   { width: 24px; height: 24px; flex-shrink: 0; }
`;

// ── Registration ───────────────────────────────────────────────────────────────

if (!customElements.get("fritzmesh-card")) {
  customElements.define("fritzmesh-card", FritzMeshCard);
  console.info(
    `%c FRITZMESH-CARD %c v${CARD_VERSION} `,
    "color:#fff;background:#1565c0;font-weight:700;padding:2px 4px;border-radius:3px 0 0 3px",
    "color:#1565c0;background:#fff;font-weight:700;padding:2px 4px;border-radius:0 3px 3px 0;border:1px solid #1565c0"
  );
}

window.customCards ??= [];
window.customCards.push({
  type:        "fritzmesh-card",
  name:        "Fritz!Box Mesh Topology",
  description: "Visualises which devices are connected to which mesh node.",
  preview:     false,
});
