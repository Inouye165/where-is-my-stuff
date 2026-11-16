# WhereIsMyStuff (WIMS) — Network Inventory System

A self-hosted, local-network physical inventory tracker built with FastAPI, SQLAlchemy 2.0, SQLite (WAL), and a zero-build modular ES6 web interface. WIMS pairs hierarchical spatial organization and interactive 2D container grids with dual-photo visual verification, QR camera handoff, optional local/cloud AI vision cataloging, and a UTC presence audit trail.

---

## Quick Start

### 1. Set Up the Environment
```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
Copy-Item .env.example .env  # Optional: customize port, LAN URL, or AI vision models
```

### 2. Start the Server

Using the PowerShell management script (background process with health verification):
```powershell
.\inventory.ps1          # Start server (or report status if already running)
.\inventory.ps1 -Open    # Start server and open http://localhost:8000 in browser
.\inventory.ps1 status   # Check PID, health, and LAN URL
.\inventory.ps1 stop     # Stop running server
```

Or run directly in the foreground:
```powershell
.\.venv\Scripts\python.exe run.py
```

By default, WIMS binds to `0.0.0.0:8000`:
- **Desktop (Localhost):** `http://localhost:8000`
- **Phone / Tablet on Wi-Fi:** `http://<your-lan-ip>:8000`
- **OpenAPI / Swagger Docs:** `http://localhost:8000/docs`

---

## Key Capabilities

- **Hierarchical Spaces & Container Grid Designer**: Organize spaces, shelves, containers, bins, drawers, compartments, and cubbies to arbitrary depth. Model multi-drawer cabinets visually with customizable `rows × columns` grids, merged multi-span drawers, section dividers, and click-to-locate highlighting.
- **Dual-Photo Context & Perspective Editor**:
  - **Context Photo (`context_photo`)**: Wide shot showing where a container or shelf sits.
  - **Container Gallery (`container_photo` + `LocationPhoto`)**: Exterior and interior shots of bins and drawers.
  - **Item Photo & Attachments**: Close-up item photos (with crop, fine rotation, and 4-corner perspective straighten) plus attached datasheets, charts, and documents (`PDF`, `SVG`, `CSV`, `TXT`, `MD`, `JSON`, Office docs, `ZIP`).
- **Desktop-to-Phone QR Camera Handoff**: Click any photo upload on desktop to display a temporary QR code; scan with a phone on the same Wi-Fi to snap and stream single or batch photos straight into the active form or drawer.
- **AI Vision Drawer Scanning & Photo Search**: Detect items, quantities, specs, and bounding-box thumbnails from drawer photos using local **Ollama** (`qwen3-vl:8b` default, offline-capable) or optional **Gemini** / **OpenAI** APIs, with multi-line quick-entry text parsing (`4x M3 Brass Standoffs`, `Needle Nose Pliers`) always available without AI.
- **Instant Search**: Plain-language keyword and synonym search across item names, part/serial numbers, categories, tags, notes, and recursive location paths (`/api/v1/search`), plus visual photo search (`/api/v1/search/photo`).
- **"Seen Today" Presence Auditing**: One-tap verification updates `last_seen_at` and records immutable UTC entries in `audit_logs` across creations, moves, status updates, and inspections.
- **Printable QR Labels & Online Backups**: Generate single or bulk printable QR label sheets (`/api/v1/locations/{id}/qr`) that deep-link directly to a container or drawer, and create verified hot SQLite snapshots via `POST /api/v1/system/backup`.

---

## Configuration

Copy [`.env.example`](file:///d:/inventory/.env.example) to `.env` to configure:
- Network binding (`HOST`, `PORT`, `PUBLIC_BASE_URL`, `CORS_ALLOWED_ORIGINS`)
- Upload bounds (`MAX_IMAGE_UPLOAD_SIZE_MB`, `MAX_IMAGE_DIMENSION`, `THUMBNAIL_MAX_DIMENSION`, `MAX_IMAGE_PIXELS`)
- Vision providers (`VISION_PROVIDER`, `OLLAMA_BASE_URL`, `OLLAMA_VISION_MODEL`, `GEMINI_API_KEY`, `OPENAI_API_KEY`)
- Logging & backup retention (`LOG_LEVEL`, `BACKUP_RETENTION_COUNT`)

See [`ARCHITECTURE.md`](file:///d:/inventory/ARCHITECTURE.md) for full details on layered architecture, database migrations, photo reference counting, and security controls.

---

## Running the Test Suite

```powershell
.\.venv\Scripts\pytest.exe -v
```

All tests run against isolated in-memory or temporary SQLite databases with `PRAGMA foreign_keys = ON` and temporary upload directories—never touching `data/inventory.db`.
