#!/usr/bin/env python3
"""
Rackpad NetBox Importer
Web UI for importing NetBox device-type YAML into Rackpad's portTemplates table.
Mount your Rackpad data directory to /data (or set RACKPAD_DB env var).
"""

from flask import Flask, request, jsonify, render_template_string
import yaml
import json
import sqlite3
import re
import os
from datetime import datetime, timezone
from pathlib import Path

app = Flask(__name__)
DB_PATH = os.environ.get("RACKPAD_DB", "/data/rackpad.db")

# ---------------------------------------------------------------------------
# Conversion logic
# ---------------------------------------------------------------------------

INTERFACE_TYPE_MAP: dict[str, tuple[str, str | None]] = {
    "100base-tx":          ("rj45",     "100M"),
    "1000base-t":          ("rj45",     "1G"),
    "2.5gbase-t":          ("rj45",     "2.5G"),
    "5gbase-t":            ("rj45",     "5G"),
    "10gbase-t":           ("rj45",     "10G"),
    "25gbase-t":           ("rj45",     "25G"),
    "1000base-x-gbic":     ("sfp",      "1G"),
    "1000base-x-sfp":      ("sfp",      "1G"),
    "10gbase-x-sfpp":      ("sfp_plus", "10G"),
    "10gbase-x-xenpak":    ("sfp_plus", "10G"),
    "10gbase-x-xfp":       ("sfp_plus", "10G"),
    "25gbase-x-sfp28":     ("sfp",      "25G"),
    "50gbase-x-sfp28":     ("sfp",      "50G"),
    "40gbase-x-qsfpp":     ("qsfp",     "40G"),
    "100gbase-x-cfp":      ("qsfp",     "100G"),
    "100gbase-x-qsfp28":   ("qsfp",     "100G"),
    "200gbase-x-qsfpdd":   ("qsfp",     "200G"),
    "400gbase-x-qsfpdd":   ("qsfp",     "400G"),
    "1000base-lx":         ("fiber",    "1G"),
    "1000base-sx":         ("fiber",    "1G"),
    "10gbase-lr":          ("fiber",    "10G"),
    "10gbase-sr":          ("fiber",    "10G"),
    "ieee802.11n":         ("wifi",     "300M"),
    "ieee802.11ac":        ("wifi",     "1.3G"),
    "ieee802.11ax":        ("wifi",     "WiFi6"),
    "ieee802.11be":        ("wifi",     "WiFi7"),
    "virtual":             ("virtual",  None),
    "lag":                 ("virtual",  None),
    "other":               ("rj45",     None),
}

DEVICE_TYPES = [
    "switch", "router", "firewall", "server", "rack_shelf", "ap",
    "endpoint", "vm", "container", "patch_panel", "brush_panel",
    "blanking_panel", "storage", "pdu", "ups", "kvm", "other",
]


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"['\"]", "", text)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")[:80]


def convert(data: dict, device_type: str = "other") -> dict:
    manufacturer = data.get("manufacturer", "Unknown")
    model        = data.get("model", data.get("slug", "Unknown"))
    part_number  = data.get("part_number", "")

    template_id = slugify(f"{manufacturer}-{model}")
    name        = f"{manufacturer} {model}" + (f" ({part_number})" if part_number else "")

    desc_parts: list[str] = []
    comments = (data.get("comments") or "").strip()
    if comments:
        first_line = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", comments.split("\n")[0].strip())
        if first_line:
            desc_parts.append(first_line)
    if data.get("u_height"):
        desc_parts.append(f"{data['u_height']}U")

    ports: list[dict] = []

    for iface in data.get("interfaces", []):
        iface_type = (iface.get("type") or "other").lower()
        kind, speed = INTERFACE_TYPE_MAP.get(iface_type, ("rj45", None))
        ports.append({
            "name":           iface.get("name") or iface.get("label") or f"port{len(ports)+1}",
            "kind":           kind,
            "speed":          speed,
            "face":           "front",
            "mode":           "access",
            "allowedVlanIds": [],
            "position":       len(ports) + 1,
        })

    for pp in data.get("power-ports", []):
        ports.append({
            "name":           pp.get("name", "Power"),
            "kind":           "power",
            "speed":          None,
            "face":           "rear",
            "mode":           "access",
            "allowedVlanIds": [],
            "position":       len(ports) + 1,
        })

    for cp in data.get("console-ports", []):
        cp_type = (cp.get("type") or "").lower()
        cp_kind = "usb" if "usb" in cp_type else "console"
        ports.append({
            "name":           cp.get("name", "Console"),
            "kind":           cp_kind,
            "speed":          None,
            "face":           "front",
            "mode":           "access",
            "allowedVlanIds": [],
            "position":       len(ports) + 1,
        })

    for po in data.get("power-outlets", []):
        ports.append({
            "name":           po.get("name", "Outlet"),
            "kind":           "power",
            "speed":          None,
            "face":           "rear",
            "mode":           "access",
            "allowedVlanIds": [],
            "position":       len(ports) + 1,
        })

    return {
        "id":          template_id,
        "name":        name,
        "description": " | ".join(desc_parts),
        "deviceTypes": [device_type],
        "ports":       ports,
    }


def db_insert(template: dict) -> None:
    now = datetime.now(timezone.utc).isoformat()
    candidate = {
        "id":          template["id"],
        "name":        template["name"],
        "description": template["description"],
        "deviceTypes": json.dumps(template["deviceTypes"]),
        "ports":       json.dumps(template["ports"]),
        "createdAt":   now,
        "updatedAt":   now,
    }
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("PRAGMA table_info(portTemplates)")
    existing = {row[1] for row in cur.fetchall()}
    row = {k: v for k, v in candidate.items() if k in existing}
    cols = ", ".join(row.keys())
    vals = ", ".join(f":{k}" for k in row.keys())
    cur.execute(f"INSERT OR REPLACE INTO portTemplates ({cols}) VALUES ({vals})", row)
    con.commit()
    con.close()


def db_status() -> dict:
    p = Path(DB_PATH)
    if not p.exists():
        return {"connected": False, "path": DB_PATH, "error": "File not found"}
    try:
        con = sqlite3.connect(DB_PATH)
        con.execute("SELECT COUNT(*) FROM portTemplates")
        con.close()
        return {"connected": True, "path": DB_PATH}
    except Exception as e:
        return {"connected": False, "path": DB_PATH, "error": str(e)}

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    status = db_status()
    return render_template_string(HTML, device_types=DEVICE_TYPES, db_status=status)


@app.route("/status")
def status():
    return jsonify(db_status())


@app.route("/preview", methods=["POST"])
def preview():
    try:
        body = request.get_json()
        data = yaml.safe_load(body.get("yaml", ""))
        if not isinstance(data, dict):
            raise ValueError("YAML did not parse to a mapping. Check your input.")
        template = convert(data, body.get("deviceType", "other"))
        return jsonify({"ok": True, "template": template})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/import", methods=["POST"])
def do_import():
    status = db_status()
    if not status["connected"]:
        return jsonify({"ok": False, "error": f"DB not accessible: {status.get('error', '')}. Check volume mount."}), 500
    try:
        body = request.get_json()
        data = yaml.safe_load(body.get("yaml", ""))
        if not isinstance(data, dict):
            raise ValueError("YAML did not parse to a mapping.")
        template = convert(data, body.get("deviceType", "other"))
        db_insert(template)
        return jsonify({
            "ok":      True,
            "message": f"Imported '{template['name']}' with {len(template['ports'])} ports.",
            "id":      template["id"],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Rackpad Importer</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --bg:         #090d12;
  --surface:    #0e1520;
  --surface-2:  #131d2b;
  --surface-3:  #1a2535;
  --border:     #1e2d3d;
  --border-hi:  #2d4563;
  --text:       #c8d8e8;
  --text-dim:   #5d7a94;
  --text-bright:#e8f0f8;
  --accent:     #38bdf8;
  --accent-bg:  #0c3d5e;
  --success:    #34d399;
  --success-bg: #052e16;
  --error:      #f87171;
  --error-bg:   #3b0a0a;
  --warning:    #fbbf24;

  --kind-rj45:    #60a5fa;
  --kind-sfp:     #34d399;
  --kind-sfp_plus:#a78bfa;
  --kind-qsfp:    #f472b6;
  --kind-fiber:   #2dd4bf;
  --kind-power:   #fbbf24;
  --kind-console: #fb923c;
  --kind-wifi:    #38bdf8;
  --kind-virtual: #94a3b8;
  --kind-usb:     #c084fc;
  --kind-other:   #64748b;

  --mono: 'IBM Plex Mono', monospace;
  --sans: 'IBM Plex Sans', sans-serif;
  --radius: 6px;
}

html, body {
  height: 100%;
  background: var(--bg);
  color: var(--text);
  font-family: var(--sans);
  font-size: 14px;
  line-height: 1.5;
}

/* ---- Layout ---- */
.app { display: flex; flex-direction: column; min-height: 100vh; }

header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 24px;
  border-bottom: 1px solid var(--border);
  background: var(--surface);
  position: sticky;
  top: 0;
  z-index: 10;
}

.header-left { display: flex; align-items: center; gap: 12px; }
.logo { font-family: var(--mono); font-size: 18px; font-weight: 600; color: var(--text-bright); letter-spacing: -0.5px; }
.logo span { color: var(--accent); }
.tagline { font-size: 12px; color: var(--text-dim); font-family: var(--mono); }

.db-status {
  display: flex;
  align-items: center;
  gap: 8px;
  font-family: var(--mono);
  font-size: 12px;
  padding: 6px 12px;
  border-radius: var(--radius);
  border: 1px solid var(--border);
  background: var(--surface-2);
}
.db-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--error);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--error) 25%, transparent);
}
.db-dot.ok {
  background: var(--success);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--success) 25%, transparent);
}
.db-path { color: var(--text-dim); max-width: 280px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

main {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0;
  flex: 1;
  min-height: 0;
}

.panel {
  padding: 24px;
  overflow-y: auto;
}

.panel-left {
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.panel-right { background: var(--bg); }

/* ---- Form elements ---- */
label {
  display: block;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-dim);
  margin-bottom: 8px;
  font-family: var(--mono);
}

.yaml-wrap { position: relative; flex: 1; display: flex; flex-direction: column; min-height: 0; }
.yaml-wrap .upload-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 10px;
}

textarea {
  width: 100%;
  flex: 1;
  min-height: 340px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  font-family: var(--mono);
  font-size: 12px;
  line-height: 1.6;
  padding: 14px;
  resize: vertical;
  outline: none;
  transition: border-color 0.15s;
}
textarea:focus { border-color: var(--accent); }
textarea.drag-over { border-color: var(--accent); background: color-mix(in srgb, var(--accent) 5%, var(--surface-2)); }
textarea::placeholder { color: var(--text-dim); opacity: 0.6; }

select {
  width: 100%;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  font-family: var(--mono);
  font-size: 13px;
  padding: 10px 14px;
  outline: none;
  cursor: pointer;
  transition: border-color 0.15s;
  appearance: none;
  background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%235d7a94' stroke-width='2'%3E%3Cpath d='M6 9l6 6 6-6'/%3E%3C/svg%3E");
  background-repeat: no-repeat;
  background-position: right 12px center;
  padding-right: 36px;
}
select:focus { border-color: var(--accent); }

.btn-row { display: flex; gap: 10px; }

button {
  flex: 1;
  padding: 11px 20px;
  border-radius: var(--radius);
  font-family: var(--mono);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0.04em;
  cursor: pointer;
  border: 1px solid transparent;
  transition: all 0.15s;
}

.btn-secondary {
  background: var(--surface-3);
  color: var(--text);
  border-color: var(--border);
}
.btn-secondary:hover { border-color: var(--border-hi); color: var(--text-bright); }

.btn-primary {
  background: var(--accent);
  color: #020a12;
  border-color: var(--accent);
}
.btn-primary:hover { background: color-mix(in srgb, var(--accent) 85%, white); }
.btn-primary:disabled { background: var(--accent-bg); color: var(--text-dim); border-color: var(--border); cursor: not-allowed; }

.btn-upload {
  flex: 0;
  padding: 7px 14px;
  background: var(--surface-3);
  color: var(--text-dim);
  border-color: var(--border);
  font-size: 11px;
  white-space: nowrap;
}
.btn-upload:hover { color: var(--text-bright); border-color: var(--border-hi); }

/* ---- Status toast ---- */
.toast {
  padding: 12px 16px;
  border-radius: var(--radius);
  font-family: var(--mono);
  font-size: 12px;
  border: 1px solid transparent;
  display: none;
  animation: fadeIn 0.2s ease;
}
.toast.show { display: block; }
.toast.success { background: var(--success-bg); border-color: color-mix(in srgb, var(--success) 30%, transparent); color: var(--success); }
.toast.error   { background: var(--error-bg);   border-color: color-mix(in srgb, var(--error)   30%, transparent); color: var(--error);   }

@keyframes fadeIn { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: translateY(0); } }

/* ---- Preview panel ---- */
.preview-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 300px;
  gap: 12px;
  color: var(--text-dim);
  text-align: center;
}
.preview-empty svg { opacity: 0.3; }
.preview-empty p { font-family: var(--mono); font-size: 12px; }

.preview-content { display: none; flex-direction: column; gap: 20px; }
.preview-content.show { display: flex; }

.meta-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 1px;
  background: var(--border);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  overflow: hidden;
}
.meta-cell {
  background: var(--surface);
  padding: 12px 16px;
}
.meta-cell.wide { grid-column: 1 / -1; }
.meta-key {
  font-family: var(--mono);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-dim);
  margin-bottom: 4px;
}
.meta-val {
  font-family: var(--mono);
  font-size: 13px;
  color: var(--text-bright);
  word-break: break-all;
}

.section-label {
  font-family: var(--mono);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--text-dim);
  padding-bottom: 10px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 12px;
}

/* ---- Port table ---- */
.port-table-wrap { overflow-x: auto; border-radius: var(--radius); border: 1px solid var(--border); }
table { width: 100%; border-collapse: collapse; font-family: var(--mono); font-size: 12px; }
thead tr { background: var(--surface-3); }
th {
  padding: 9px 14px;
  text-align: left;
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--text-dim);
  border-bottom: 1px solid var(--border);
  white-space: nowrap;
}
td {
  padding: 8px 14px;
  border-bottom: 1px solid var(--border);
  color: var(--text);
  vertical-align: middle;
}
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: var(--surface-2); }

.pos-badge {
  display: inline-block;
  min-width: 26px;
  text-align: center;
  padding: 2px 6px;
  background: var(--surface-3);
  border-radius: 4px;
  color: var(--text-dim);
  font-size: 11px;
}

.kind-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 500;
  background: color-mix(in srgb, var(--kind-color) 15%, transparent);
  color: var(--kind-color);
  border: 1px solid color-mix(in srgb, var(--kind-color) 25%, transparent);
}

.face-front { color: var(--text-dim); }
.face-rear  { color: var(--warning); }

.speed-val { color: var(--accent); }

/* ---- Scrollbar ---- */
::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: var(--border-hi); border-radius: 3px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-dim); }

@media (max-width: 900px) {
  main { grid-template-columns: 1fr; }
  .panel-left { border-right: none; border-bottom: 1px solid var(--border); }
}
</style>
</head>
<body>
<div class="app">

<header>
  <div class="header-left">
    <div class="logo"><span>rack</span>pad</div>
    <div class="tagline">// netbox importer</div>
  </div>
  <div class="db-status">
    <div class="db-dot {{ 'ok' if db_status.connected else '' }}" id="dbDot"></div>
    <span>{{ 'connected' if db_status.connected else 'disconnected' }}</span>
    <span class="db-path" title="{{ db_status.path }}">{{ db_status.path }}</span>
  </div>
</header>

<main>
  <div class="panel panel-left">

    <div class="yaml-wrap">
      <label>NetBox Device-Type YAML</label>
      <div class="upload-row">
        <button class="btn-upload" onclick="document.getElementById('fileInput').click()">&#8593; Load file</button>
        <span style="font-size:11px;color:var(--text-dim);font-family:var(--mono)">or paste below / drag &amp; drop onto textarea</span>
        <input type="file" id="fileInput" accept=".yaml,.yml" style="display:none" onchange="loadFile(this)">
      </div>
      <textarea id="yamlInput"
        placeholder="---&#10;manufacturer: Ubiquiti&#10;model: UniFi Dream Machine Pro SE&#10;u_height: 1&#10;interfaces:&#10;  - name: port.1&#10;    type: 1000base-t&#10;..."
        spellcheck="false"
        ondragover="event.preventDefault();this.classList.add('drag-over')"
        ondragleave="this.classList.remove('drag-over')"
        ondrop="dropFile(event)"></textarea>
    </div>

    <div>
      <label>Rackpad Device Type</label>
      <select id="deviceType">
        {% for dt in device_types %}
        <option value="{{ dt }}" {% if dt == 'other' %}selected{% endif %}>{{ dt }}</option>
        {% endfor %}
      </select>
    </div>

    <div id="toast" class="toast"></div>

    <div class="btn-row">
      <button class="btn-secondary" onclick="doPreview()">Preview</button>
      <button class="btn-primary" id="importBtn" onclick="doImport()" {% if not db_status.connected %}disabled{% endif %}>Import</button>
    </div>

  </div>

  <div class="panel panel-right">

    <div class="preview-empty" id="previewEmpty">
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <rect x="2" y="3" width="20" height="14" rx="2"/>
        <path d="M8 21h8M12 17v4"/>
      </svg>
      <p>Paste a NetBox YAML and click Preview</p>
    </div>

    <div class="preview-content" id="previewContent">
      <div>
        <div class="section-label">Template</div>
        <div class="meta-grid">
          <div class="meta-cell wide">
            <div class="meta-key">Name</div>
            <div class="meta-val" id="prevName"></div>
          </div>
          <div class="meta-cell">
            <div class="meta-key">ID</div>
            <div class="meta-val" id="prevId"></div>
          </div>
          <div class="meta-cell">
            <div class="meta-key">Device Type</div>
            <div class="meta-val" id="prevDeviceType"></div>
          </div>
          <div class="meta-cell wide">
            <div class="meta-key">Description</div>
            <div class="meta-val" id="prevDesc" style="color:var(--text-dim)"></div>
          </div>
        </div>
      </div>

      <div>
        <div class="section-label" id="portCountLabel">Ports</div>
        <div class="port-table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Name</th>
                <th>Kind</th>
                <th>Speed</th>
                <th>Face</th>
              </tr>
            </thead>
            <tbody id="portTableBody"></tbody>
          </table>
        </div>
      </div>
    </div>

  </div>
</main>

</div>

<script>
const KIND_COLORS = {
  rj45:    'var(--kind-rj45)',
  sfp:     'var(--kind-sfp)',
  sfp_plus:'var(--kind-sfp_plus)',
  qsfp:    'var(--kind-qsfp)',
  fiber:   'var(--kind-fiber)',
  power:   'var(--kind-power)',
  console: 'var(--kind-console)',
  wifi:    'var(--kind-wifi)',
  virtual: 'var(--kind-virtual)',
  usb:     'var(--kind-usb)',
};

function loadFile(input) {
  const file = input.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = e => { document.getElementById('yamlInput').value = e.target.result; };
  reader.readAsText(file);
}

function dropFile(e) {
  e.preventDefault();
  document.getElementById('yamlInput').classList.remove('drag-over');
  const file = e.dataTransfer.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = ev => { document.getElementById('yamlInput').value = ev.target.result; };
  reader.readAsText(file);
}

function showToast(msg, type) {
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.className = 'toast show ' + type;
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 6000);
}

function renderPreview(template) {
  document.getElementById('previewEmpty').style.display = 'none';
  document.getElementById('previewContent').classList.add('show');
  document.getElementById('prevName').textContent        = template.name;
  document.getElementById('prevId').textContent          = template.id;
  document.getElementById('prevDeviceType').textContent  = template.deviceTypes.join(', ');
  document.getElementById('prevDesc').textContent        = template.description || '—';
  document.getElementById('portCountLabel').textContent  = template.ports.length + ' Ports';

  const tbody = document.getElementById('portTableBody');
  tbody.innerHTML = '';
  for (const p of template.ports) {
    const color = KIND_COLORS[p.kind] || 'var(--kind-other)';
    const tr = document.createElement('tr');
    tr.innerHTML = [
      '<td><span class="pos-badge">' + p.position + '</span></td>',
      '<td>' + esc(p.name) + '</td>',
      '<td><span class="kind-badge" style="--kind-color:' + color + '">' + esc(p.kind) + '</span></td>',
      '<td>' + (p.speed ? '<span class="speed-val">' + esc(p.speed) + '</span>' : '<span style="color:var(--text-dim)">—</span>') + '</td>',
      '<td class="face-' + p.face + '">' + esc(p.face) + '</td>',
    ].join('');
    tbody.appendChild(tr);
  }
}

function esc(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function payload() {
  return {
    yaml:       document.getElementById('yamlInput').value,
    deviceType: document.getElementById('deviceType').value,
  };
}

async function doPreview() {
  try {
    const res  = await fetch('/preview', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload()) });
    const data = await res.json();
    if (!data.ok) { showToast('Error: ' + data.error, 'error'); return; }
    renderPreview(data.template);
  } catch(e) {
    showToast('Request failed: ' + e.message, 'error');
  }
}

async function doImport() {
  try {
    const res  = await fetch('/import', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload()) });
    const data = await res.json();
    if (!data.ok) { showToast('Error: ' + data.error, 'error'); return; }
    showToast(data.message, 'success');
    doPreview();
  } catch(e) {
    showToast('Request failed: ' + e.message, 'error');
  }
}

// Auto-preview if YAML already present (e.g. after page reload)
document.getElementById('yamlInput').addEventListener('input', () => {
  document.getElementById('previewContent').classList.remove('show');
  document.getElementById('previewEmpty').style.display = '';
});
</script>
</body>
</html>"""

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
