# WhereIsMyStuff (WIMS) — Production Architecture & Engineering Reference

## 1. Overview & Design Principles
**WhereIsMyStuff (WIMS)** is a local-first, LAN/Tailscale-ready spatial inventory system built for fast everyday lookup across spaces, containers, and drawers with dual-photo visual context (`container_photo` + `context_photo`), interactive 2D container grids, QR camera handoff, optional local/cloud AI vision extraction, and physical presence auditing (`last_seen_at`).

Core architectural invariants:
- **Zero-Scroll Room-First Consumer UX:** `100dvh` responsive application shell (desktop left rail + mobile bottom navigation bar) with zero vertical scrolling on the `Places` home canvas.
- **Strict Layered Separation:** Route handlers never execute raw SQL, join filesystem paths, or make outbound HTTP calls; domain orchestration, data access, vision calls, and file storage are isolated in dedicated service and repository layers.
- **Database + Filesystem Atomicity:** SQLite WAL concurrency, enforced foreign keys, versioned schema migrations, online `.backup()` snapshots, atomic `.tmp` file writes (`os.replace`), session commit/rollback cleanup hooks, and reference-counted orphan garbage collection.
- **Non-Blocking External I/O:** Outbound AI vision requests (`Ollama`, `Gemini`, `OpenAI`) execute asynchronously via `httpx.AsyncClient` only after committing any uploaded photo records so SQLite write locks are never held across slow network calls.
- **Defense-in-Depth Security:** Escaped DOM interpolation + strict `/uploads/` URL sanitization on the client; bounded streaming uploads, Pillow header/format verification, path containment checks, parameterized SQLAlchemy queries, escaped `LIKE` wildcards, and whitelisted sort orders on the server.

---

## 2. Layered Backend Architecture

```
d:\inventory\
├── app\
│   ├── api\                  # Thin HTTP transport layer (validation, status codes, DI)
│   │   ├── items.py          # /api/v1/items CRUD, bulk quick-entry, verify, attachments
│   │   ├── locations.py      # /api/v1/locations tree, grid layout, gallery, QR PNG, AI scan
│   │   ├── photos.py         # /api/v1/photos upload, vision provider status, capture sessions
│   │   ├── search.py         # /api/v1/search keyword + /api/v1/search/photo visual search
│   │   └── system.py         # /api/v1/health deep probe & /api/v1/system/backup
│   ├── core\                 # Cross-cutting infrastructure
│   │   ├── config.py         # TypedSettings + .env loader
│   │   ├── database.py       # SQLAlchemy 2.0 Engine, SessionLocal, SQLite PRAGMA hook
│   │   ├── exceptions.py     # Domain exceptions & global JSON error envelope handlers
│   │   ├── logging_config.py # ContextVar request_id filter + RotatingFileHandler
│   │   ├── middleware.py     # X-Request-ID correlation, latency logging, security headers
│   │   └── network.py        # LAN IPv4/IPv6 loopback & QR base URL resolution
│   ├── db\                   # Schema lifecycle & online backup utilities
│   │   ├── migrations.py     # Deterministic, idempotent schema_migrations runner (v1..v5)
│   │   └── backup.py         # Online SQLite .backup() snapshot + integrity_check + pruning
│   ├── models\               # SQLAlchemy 2.0 typed ORM entities (Mapped / mapped_column)
│   │   ├── location.py       # Location self-referential tree + LocationPhoto gallery
│   │   ├── item.py           # Item (location_id ON DELETE SET NULL) + ItemAttachment
│   │   └── audit.py          # Immutable AuditLog (item_id ON DELETE CASCADE)
│   ├── repositories\         # Parameterized SQLAlchemy 2.0 data access layer
│   │   ├── location_repository.py # Ordered fetches, grouped item counts, recursive CTEs
│   │   └── item_repository.py     # Filtered/sorted queries, escaped ILIKE, photo ref counts
│   ├── schemas\              # Strict Pydantic v2 request/response contracts
│   │   ├── common.py         # Enums (LocationType, ItemStatus, ItemSortOrder), URL validator
│   │   ├── location.py       # LocationCreate/Update/Read/Tree, GridSlot, LocationPhoto schemas
│   │   ├── item.py           # Item CRUD, BulkCreate, Attachment, VisionAnalysis schemas
│   │   ├── audit.py          # AuditLogRead & AuditVerifyRequest
│   │   ├── search.py         # SearchResult & PhotoSearchResult envelopes
│   │   └── system.py         # Health, Backup, PhotoUpload, CaptureSession schemas
│   ├── services\             # Pure domain & orchestration logic
│   │   ├── location_service.py        # O(N) hierarchy snapshot, 2D grid layout, gallery, QR
│   │   ├── inventory_service.py       # Batch hydration, quick-entry parser, attachments, audits
│   │   ├── photo_service.py           # Bounded streams, Pillow verify/crop, deferred GC hooks
│   │   ├── capture_session_service.py # Thread-safe QR phone camera handoff & atomic upload sync
│   │   ├── search_service.py          # Token/synonym search + photo-to-query search
│   │   └── vision_service.py          # Async Ollama / Gemini / OpenAI vision client + JSON repair
│   ├── static\               # Modular ES6 frontend, mobile capture page & PWA shell
│   │   ├── css\app.css
│   │   ├── js\               # 13 zero-build ES6 modules (see Section 5)
│   │   ├── index.html        # Main SPA shell
│   │   ├── capture.html      # Mobile QR camera handoff page
│   │   ├── manifest.webmanifest
│   │   └── sw.js             # Offline app-shell service worker
│   └── main.py               # ASGI app factory, lifespan runner, static mounts
├── data\                     # SQLite DB, uploads/{originals,thumbnails,attachments}, backups/
├── logs\                     # Rotating application logs (wims.log)
├── tests\                    # Isolated pytest + Node ESM regression suite
├── .env.example              # Documented environment variable template
├── inventory.ps1             # PowerShell start/stop/restart/status launcher
└── run.py                    # Production/LAN Uvicorn launcher bound to 0.0.0.0:8000
```

---

## 3. Database Integrity, Query Efficiency & Hot Backups

### SQLite Concurrency & Integrity Pragmas
Every SQLite connection in `app/core/database.py` executes:
- `PRAGMA foreign_keys = ON;` — enforces `ON DELETE CASCADE` on child locations, location photos, item attachments, and audit logs, plus `ON DELETE SET NULL` on item `location_id`.
- `PRAGMA journal_mode = WAL;` — enables concurrent readers alongside phone/desktop writes.
- `PRAGMA synchronous = NORMAL;` — safe durability in WAL mode without excessive fsync stalls.
- `PRAGMA busy_timeout = 5000;` — waits up to 5 seconds on write contention instead of raising immediate `SQLITE_BUSY` errors.

### Versioned Schema Migrations
`app/db/migrations.py` maintains a `schema_migrations (version, name, applied_at)` table and applies numbered, idempotent migration steps inside a transaction during FastAPI `lifespan` startup:
1. `001_initial_schema` — creates core tables (`locations`, `items`, `audit_logs`) and ensures `status` / `status_note` columns.
2. `002_performance_indexes` — ensures indexes on `locations`, `items`, and `audit_logs`.
3. `003_location_grid_layout` — adds 2D container grid columns (`grid_rows`, `grid_cols`, `grid_config`, `grid_row`, `grid_col`, `grid_row_span`, `grid_col_span`).
4. `004_location_photos` — creates `location_photos` multi-photo gallery table and backfills existing `container_photo` / `context_photo` references.
5. `005_item_attachments` — creates `item_attachments` table for datasheets, charts, documents, and extra images.

### Eliminating `N+1` Queries
- **Location Tree & Counts (`LocationHierarchySnapshot`):** Loads all `Location` rows in 1 `SELECT`, grouped direct item counts (`SELECT location_id, COUNT(id) FROM items GROUP BY location_id`) in 1 `SELECT`, and optional `LocationPhoto` rows in 1 `SELECT` (skipped automatically when serializing item lists that only need breadcrumbs and cover photos). Breadcrumbs, `full_path`, inherited photos, and rolled-up `total_item_count` values are computed in memory in `O(N)`.
- **Item Lists & Search (`InventoryService.serialize_many`):** Hydrates `location_name`, `location_full_path`, `location_breadcrumbs`, and `location_photos` for item lists using a single `LocationHierarchySnapshot` (`<= 2` `SELECT` statements).
- **Descendant Subtree Lookup (`LocationRepository.get_descendant_ids`):** Uses a single recursive SQL CTE (`WITH RECURSIVE location_descendants AS ...`).

### Safe Online Hot Backups
`BackupService.create_hot_backup()` (`POST /api/v1/system/backup`) uses Python's `sqlite3.Connection.backup()` API into a `.tmp` file, runs `PRAGMA integrity_check;`, atomically renames via `os.replace`, and prunes older snapshots beyond `BACKUP_RETENTION_COUNT`.

---

## 4. Photo & Attachment Pipeline
1. **Bounded Streaming Read:** `PhotoService._read_upload_bounded()` reads uploads in `64 KB` chunks and aborts immediately with `413 Content Too Large` (`PayloadTooLargeError`) if bytes exceed `MAX_IMAGE_UPLOAD_SIZE_MB` (default `15 MB`).
2. **Content Verification:** Image uploads verify headers via `Image.verify()`, enforce `Image.MAX_IMAGE_PIXELS` (`36MP`), and whitelist `JPEG`, `PNG`, `WEBP`, `GIF`, and `BMP` (blocking polyglot scripts). Document attachments whitelist safe extensions under `/uploads/attachments/`.
3. **Atomic Write & Cropping:** Applies `ImageOps.exif_transpose()`, caps dimensions at `MAX_IMAGE_DIMENSION` (`1600px`), generates a `WebP` thumbnail, writes to `.tmp` files, and renames atomically via `os.replace()`. Vision bounding boxes `[x1, y1, x2, y2]` auto-crop item thumbnails via `crop_and_save()`.
4. **Transactional File Hygiene:** SQLAlchemy `Session` `after_commit` and `after_rollback` listeners ensure newly uploaded files are deleted if a DB transaction rolls back, while replaced/deleted asset files are unlinked only **after** commit and only when `ItemRepository.count_photo_references()` confirms zero remaining references across `items`, `locations`, `location_photos`, and `item_attachments`.

---

## 5. Modular Frontend Architecture (`app/static/js/`)
The browser client uses native ES6 modules (`<script type="module" src="/js/app.js">`) with zero build-step complexity:

| Module | Responsibility |
| :--- | :--- |
| `dom.js` | `escapeHtml`, `escapeJsStr`, `sanitizePhotoUrl`, `formatDate`, `pluralize`, `showToast`, lightbox/modal helpers, and `withSubmitLock` double-submit guard |
| `api.js` | `apiFetch` (`AbortController` timeouts + structured JSON error parsing), `apiGet`, `apiPost`, `apiPut`, `apiPatch`, `apiDelete`, `apiUpload`, and `BroadcastChannel` cross-tab sync |
| `icons.js` | Inline Lucide SVG icon registry and room keyword icon resolver |
| `unsaved-guard.js` | Dirty-state tracking and Save / Discard / Cancel confirmation prompts for modals, Grid Designer navigation, and `beforeunload` |
| `photo-compress.js` | Client-side Canvas/ImageBitmap downscaling (`<= 1600px` JPEG) with deterministic `ObjectURL` cleanup |
| `photo-editor.js` | Client-side crop, 90°/fine rotation, X/Y scale, and 4-corner homography perspective straightening |
| `phone-capture.js` | Desktop-to-phone QR capture session modal, non-overlapping status polling, and file-input interception |
| `grid-designer.js` | Interactive 2D container grid designer (multi-select merge/split, drag-to-swap/move, section breaks, cabinet spotlight overlay) |
| `state.js` | Application state, spatial tree indexing, breadcrumb/hierarchy resolvers, dropdown hydration, and initial boot retry |
| `router.js` | Hash deep-link router (`#/`, `#/needs-home`, `#/room/:id`, `#/unit/:id`, `#/drawer/:id`, `#/items`, `#/item/:id`), scroll restoration, and back-stack |
| `views.js` | XSS-hardened renderers for zero-scroll Home (`Places`), live text/photo search, `Needs a home`, Room/Container/Drawer views, Items catalog, and Item detail view |
| `modals.js` | Unified `+` action sheet, Add/Edit Item & Place forms, Snap & Batch AI Entry, Quick Photo modal, More settings, and single/bulk QR label printing |
| `app.js` | Theme lifecycle (`system` / `light` / `dark`), layered `Escape` & `/` / `Ctrl+K` shortcuts, PWA service worker registration, and explicit `window` bindings |

---

## 6. Security Controls Summary
- **DOM XSS Prevention:** Dynamic strings interpolated into `innerHTML` pass through `escapeHtml()`, inline JS string arguments pass through `escapeJsStr()`, and all image URLs pass through `sanitizePhotoUrl()` on the frontend plus `validate_safe_photo_ref()` on Pydantic input schemas.
- **SQL Injection & Wildcard Safety:** 100% SQLAlchemy 2.0 bound parameters; user search terms escape `\`, `%`, and `_` (`escape="\\"`); item `sort` query parameters are restricted to the `ItemSortOrder` enum.
- **HTTP Hardening:** `RequestContextMiddleware` attaches `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`, and `X-Request-ID`. Global exception handlers log server errors with correlation IDs without leaking stack traces or host paths.

---

## 7. Running & Testing

```powershell
# Run full isolated test suite (never touches data/inventory.db)
.\.venv\Scripts\pytest.exe -v

# Start LAN-accessible server via PowerShell helper or Python entrypoint
.\inventory.ps1
# or
.\.venv\Scripts\python.exe run.py
```
