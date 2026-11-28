# WhereIsMyStuff (WIMS)

WhereIsMyStuff is a self-hosted, LAN-ready physical inventory tracker built on FastAPI, SQLAlchemy 2.0, SQLite (WAL mode), and zero-build native ES modules. It models real storage hierarchies—rooms/spaces, multi-drawer organizers as interactive 2D grids, bins, compartments, and nested boxes—with dual-photo context, QR phone camera handoff, a document capture/OCR studio, and an immutable UTC audit trail.

## Quick Start (Under 2 Minutes)

### 1. Create the virtual environment and install dependencies
```powershell
python -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
Copy-Item .env.example .env  # Optional: adjust ports, LAN base URL, PIN lock, or AI models
```

### 2. Start the server

Run with the PowerShell helper (starts Ollama automatically if installed unless `-NoOllama` is passed, runs WIMS in the foreground, and exits early if port `8000` is already listening):
```powershell
.\inventory.ps1                # Start server in the foreground
.\inventory.ps1 -Open          # Start server and open http://localhost:8000 once healthy
.\inventory.ps1 -NoOllama      # Skip launching Ollama
```

Or launch directly with Python:
```powershell
.\.venv\Scripts\python.exe run.py
```

By default, WIMS binds to `0.0.0.0:8000` (HTTP) and—when `HTTPS_ENABLED=true`—starts a local HTTPS listener on `0.0.0.0:8443` using an auto-generated self-signed LAN certificate so phone browsers allow `getUserMedia` camera access over Wi-Fi:
- Desktop browser: `http://localhost:8000`
- Phone / tablet on the same Wi-Fi: `http://<your-lan-ip>:8000` (or `https://<your-lan-ip>:8443` for live camera flows)
- OpenAPI / Swagger UI: `http://localhost:8000/docs`

## How the App Works

- **Hierarchical Spaces & 2D Container Grids:** Nest spaces, shelves, bins, drawers, compartments, and sub-boxes to arbitrary depth (`Location`). Multi-drawer cabinets render as customizable `rows × columns` 2D grids with multi-span merged cells, section breaks, drag-and-drop slot swaps, inline rename/quick-add, and undo/redo (`Ctrl+Z` / `Ctrl+Y`).
- **Quick Label Lookup & In-Page Locate:** Search items, drawer names, container labels (`M2`), or grid coordinates (`A1`, `D12`) from Home search or directly inside a container page (`/` or `Ctrl+K`) to jump straight to or spotlight a compartment on its organizer photo/grid.
- **Dual-Photo Context & Editor:** Attach context photos (`context_photo` showing where a shelf sits), container photos (`container_photo` + `LocationPhoto` galleries), and item photos. Built-in client/server tools handle EXIF orientation, cropping, fine tilt, 4-corner perspective straightening, and WebP thumbnail generation.
- **Phone QR Camera Handoff & Guided Unit Setup:** Scan temporary QR codes from desktop to capture container photos, batch drawer shots, or label close-ups directly from a phone (`capture.html`, `setup.html`, `label-capture.html`).
- **Document Capture, OCR & PDF Studio:** Acquire manuals/labels from connected scanners, webcam, uploaded files, or phone handoff; run perspective enhancement, grayscale/contrast filters, stitch multi-page scans, extract text via `rapidocr-onnxruntime`, and save dual PDF attachments (`ItemAttachment`).
- **Smart Bundle Import & Optional Vision AI:** Extract item lists, bounding-box crops, or product specs from photos, PDFs, URLs, and pasted text using local Ollama (`qwen2.5vl:7b` default) or optional Gemini / OpenAI keys. Plain-text multi-line quick entry (`4x M3 Brass Standoffs`) and manual CRUD work completely offline without AI.
- **Presence Auditing & Automated Backups:** Creations, moves, and one-tap "Verified" actions update `item.last_seen_at` and append UTC rows to `audit_logs`. `POST /api/v1/system/backup` (plus a 24h background scheduler) writes verified hot SQLite `.backup()` archives and asset zips under `data/backups/`.

## Configuration

See [.env.example](.env.example) for all supported settings, including:
- Server & LAN binding (`HOST`, `PORT`, `HTTPS_ENABLED`, `HTTPS_PORT`, `PUBLIC_BASE_URL`, `CORS_ALLOWED_ORIGINS`)
- Optional PIN auth & rate limits (`WIMS_ACCESS_PIN`, `WIMS_AUTH_BYPASS_LOCALHOST`, `WIMS_SESSION_SECRET`, `RATE_LIMIT_*`)
- Image/upload bounds (`MAX_IMAGE_UPLOAD_SIZE_MB`, `MAX_IMAGE_DIMENSION`, `MAX_LABEL_SCAN_DIMENSION`, `MAX_IMAGE_PIXELS`)
- Vision providers (`VISION_PROVIDER`, `OLLAMA_BASE_URL`, `OLLAMA_VISION_MODEL`, `GEMINI_API_KEY`, `OPENAI_API_KEY`)
- Logging & hot backups (`LOG_LEVEL`, `BACKUP_RETENTION_COUNT`, `AUTO_BACKUP_ENABLED`, `AUTO_BACKUP_INTERVAL_HOURS`)

See [ARCHITECTURE.md](ARCHITECTURE.md) for system boundaries, schema migrations, query/performance invariants, and security controls.

## Testing & Linting

Tests always run against isolated in-memory/temporary SQLite databases and temporary upload directories—never touching `data/inventory.db`:

```powershell
.\.venv\Scripts\pytest.exe -v
ruff check app run.py
```
