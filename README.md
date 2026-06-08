# rackpad-importer
A small web app for importing NetBox device-type YAML into Rackpad as port templates.
Rackpad does not currently have native NetBox import, so this fills that gap. Paste or upload a device-type YAML from the NetBox community library, pick a Rackpad device type, preview the mapped ports, and import directly into your Rackpad SQLite database.
<img width="2483" height="1285" alt="image" src="https://github.com/user-attachments/assets/5027b0b8-e4b3-455d-aa96-4dc4830099c7" />

# Features

- Paste YAML directly or drag and drop a .yaml / .yml file
- Maps NetBox interface types to Rackpad port kinds (rj45, sfp_plus, qsfp, power, console, usb, wifi, virtual, etc.)
- Handles interfaces, power-ports, power-outlets, and console-ports
- Live preview of parsed ports before writing anything
- DB connection indicator — shows green when Rackpad's database is reachable
- INSERT OR REPLACE — re-importing a YAML updates the existing template cleanly
- Adapts to the installed Rackpad schema version at runtime
