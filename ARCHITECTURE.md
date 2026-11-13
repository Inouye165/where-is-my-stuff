# WhereIsMyStuff (WIMS) — Production Architecture & Engineering Reference

## 1. Overview & Design Principles
**WhereIsMyStuff (WIMS)** is a local-first, LAN/Tailscale-ready spatial inventory system built for fast everyday lookup across rooms, containers, and drawers with dual-photo visual context (`container_photo` + `context_photo`), QR deep-linking, and physical presence auditing (`last_seen_at`).

Core architectural invariants:
- **Zero-Scroll Room-First Consumer UX:** `100dvh` responsive application shell (desktop left rail + mobile 5-slot thumb bar) with strict zero vertical scrolling on the `Places` home grid.
- **Strict Layered Separation:** Route handlers never execute raw SQL, join filesystem paths, or make outbound HTTP calls; domain logic, data access, and file storage are isolated in dedicated layers.
- **Database + Filesystem Atomicity:** SQLite WAL concurrency, enforced foreign keys, versioned schema migrations, online `.backup()` snapshots, atomic temp-file photo writes, and reference-counted orphan cleanup.
- **Defense-in-Depth Security:** Escaped DOM interpolation + strict local URL sanitization on the client; bounded streaming uploads, Pillow header/format verification, path containment checks, parameterized queries, escaped `LIKE` wildcards, and whitelisted sort orders on the server.

---

## 2. Layered Backend Architecture

```
d:\inventory\
├── app\
│   ├── api\                  # Thin HTTP transport layer (validation, status codes, DI)
│   │   ├── items.py          # /api/v1/items CRUD, bulk quick-entry, verify presence
│   │   ├── locations.py      # /api/v1/locations (& /containers) tree, QR PNG, vision hook
│   │   ├── photos.py         # /api/v1/photos/upload multipart endpoint
│   │   ├── search.py         # /api/v1/search keyword + descendant location search
│   │   └── system.py         # /api/v1/health deep probe & /api/v1/system/backup
│   ├── core\                 # Cross-cutting infrastructure
│   │   ├── config.py         # Typed Pydantic v2 Settings + .env loader
│   │   ├── database.py       # SQLAlchemy 2.0 Engine, SessionLocal, SQLite PRAGMA hook
│   │   ├── exceptions.py     # Domain exceptions & global JSON error envelope handlers
│   │   ├── logging_config.py # ContextVar request_id filter + RotatingFileHandler
│   │   ├── middleware.py     # X-Request-ID correlation, latency logging, security headers
│   │   └── network.py        # LAN IPv4 & QR base URL resolution
│   ├── db\                   # Schema lifecycle & online backup utilities
│   │   ├── migrations.py     # Deterministic, idempotent schema_migrations runner
│   │   └── backup.py         # Online SQLite .backup() snapshot + integrity_check + pruning
│   ├── models\               # SQLAlchemy 2.0 typed ORM entities (Mapped / mapped_column)
│   │   ├── location.py       # Self-referential tree (ON DELETE CASCADE)
│   │   ├── item.py           # Inventory item (location_id ON DELETE SET NULL)
│   │   └── audit.py          # Immutable presence & movement log (item_id ON DELETE CASCADE)
│   ├── repositories\         # Parameterized SQLAlchemy 2.0 data access layer
│   │   ├── location_repository.py # Ordered fetches, grouped item counts, recursive CTEs
│   │   └── item_repository.py     # Filtered/sorted queries, escaped ILIKE, photo ref counts
│   ├── schemas\              # Strict Pydantic v2 request/response contracts
│   │   ├── common.py         # Enums (LocationType, ItemStatus, ItemSortOrder), URL validator
│   │   ├── location.py       # LocationCreate/Update/Read/Tree schemas
│   │   ├── item.py           # ItemCreate/BulkCreate/Update/Read/DetailRead schemas
│   │   ├── audit.py          # AuditLogRead & AuditVerifyRequest
│   │   ├── search.py         # SearchResult envelope
│   │   └── system.py         # HealthResponse, BackupResponse, PhotoUploadResponse
│   ├── services\             # Pure domain & orchestration logic
│   │   ├── location_service.py  # O(N) LocationHierarchySnapshot, cycle guard, QR & vision
│   │   ├── inventory_service.py # Batch item hydration, quick-entry parser, audit transitions
│   │   ├── photo_service.py     # Bounded upload stream, Pillow verify/transpose, GC cleanup
│   │   └── search_service.py    # Tokenized search across items + recursive location subtrees
│   ├── static\               # Modular ES6 frontend + responsive CSS design system
│   │   ├── css\app.css
│   │   ├── js\               # app.js, dom.js, api.js, state.js, router.js, views.js, modals.js, photo-compress.js
│   │   └── index.html
│   └── main.py               # ASGI app factory, lifespan runner, static mounts
├── data\                     # Persistent SQLite DB, uploads/{originals,thumbnails}, backups/
├── logs\                     # Rotating application logs (wims.log)
├── tests\                    # Isolated pytest suite (unit, integration, regression, security)
├── .env.example              # Documented environment variable template
└── run.py                    # Production/LAN Uvicorn launcher bound to 0.0.0.0:8000
```

---

## 3. Database Integrity, Query Efficiency & Hot Backups

### SQLite Concurrency & Integrity Pragmas
Every SQLite connection in `app/core/database.py` executes:
- `PRAGMA foreign_keys = ON;` — enforces `ON DELETE CASCADE` on child locations/audit logs and `ON DELETE SET NULL` on item `location_id`.
- `PRAGMA journal_mode = WAL;` — enables concurrent readers alongside phone/desktop writes.
- `PRAGMA synchronous = NORMAL;` — safe durability in WAL mode without excessive fsync stalls.
- `PRAGMA busy_timeout = 5000;` — waits up to 5 seconds on write contention instead of raising immediate `SQLITE_BUSY` errors.

### Versioned Schema Migrations
`app/db/migrations.py` maintains a `schema_migrations (version, name, applied_at)` table and applies numbered, idempotent migration steps inside a transaction during FastAPI `lifespan` startup:
1. `001_initial_schema` — creates core tables and ensures backward-compatible columns (`status`, `status_note`).
2. `002_performance_indexes` — ensures indexes on `locations(parent_id, location_type, name)`, `items(location_id, status, category, last_seen_at, updated_at, serial_or_part_number)`, and `audit_logs(item_id, location_id, recorded_at)`.

### Eliminating `N+1` Queries
- **Location Tree & Counts (`LocationHierarchySnapshot`):** Loads all `Location` rows in 1 `SELECT` and grouped direct item counts (`SELECT location_id, COUNT(id) FROM items GROUP BY location_id`) in 1 `SELECT`. Breadcrumbs, `full_path`, inherited photos, and rolled-up `total_item_count` values are computed in memory in `O(N)`.
- **Item Lists & Search (`InventoryService.serialize_many`):** Hydrates `location_name`, `location_full_path`, `location_breadcrumbs`, and `location_photos` for arbitrarily large item lists using a single `LocationHierarchySnapshot` query (`<= 2` `SELECT` statements total).
- **Descendant Subtree Lookup (`LocationRepository.get_descendant_ids`):** Uses a single recursive SQL CTE (`WITH RECURSIVE location_descendants AS ...`) rather than iterative per-depth queries.

### Safe Online Hot Backups
Because raw file copies (`cp` / `shutil.copy2`) of an active WAL-mode SQLite database can miss uncheckpointed `.db-wal` frames or produce torn reads, `BackupService.create_hot_backup()` (`POST /api/v1/system/backup`) uses Python's `sqlite3.Connection.backup()` API into a `.tmp` file, runs `PRAGMA integrity_check;`, atomically renames via `os.replace`, and prunes older snapshots beyond `BACKUP_RETENTION_COUNT`.

---

## 4. Photo Pipeline & Orphan File Prevention
1. **Bounded Streaming Read:** `PhotoService._read_upload_bounded()` reads uploads in `64 KB` chunks and aborts immediately with `413 Content Too Large` (`PayloadTooLargeError`) if bytes exceed `MAX_IMAGE_UPLOAD_SIZE_MB` (default `15 MB`).
2. **Content Verification:** Rejects non-image extensions and SVG/HTML polyglots; sets `Image.MAX_IMAGE_PIXELS` (`36MP`) to block decompression bombs and runs `Image.verify()` + PIL format whitelisting (`JPEG`, `PNG`, `WEBP`, `GIF`, `BMP`).
3. **Atomic Write:** Applies `ImageOps.exif_transpose()` for phone camera orientation, caps dimensions at `MAX_IMAGE_DIMENSION` (`1600px`), generates a `WebP` thumbnail, writes both to `.tmp` files, and commits them via `os.replace()` (cleaning up partial `.tmp` files on any error).
4. **Reference-Counted Cleanup:** Whenever an item/location photo is replaced or an item/location subtree is deleted, `PhotoService.cleanup_unreferenced_photo()` queries `ItemRepository.count_photo_references()` across `items` and `locations`. Unreferenced originals and thumbnails are deleted only after verifying containment inside `UPLOAD_DIR` via `Path.resolve().is_relative_to(...)`.

---

## 5. Modular Frontend Architecture (`app/static/js/`)
The browser client uses native ES6 modules (`<script type="module" src="/js/app.js">`) with zero build-step complexity:

| Module | Responsibility |
| :--- | :--- |
| `dom.js` | `escapeHtml`, `escapeJsStr`, `sanitizePhotoUrl`, `formatDate`, `pluralize`, `showToast`, lightbox/modal helpers, and `withSubmitLock` double-submit guard |
| `api.js` | `apiFetch` (`AbortController` timeouts + structured JSON error extraction), `apiGet`, `apiPost`, `apiPatch`, `apiDelete`, `apiUpload` |
| `photo-compress.js` | Client-side Canvas/ImageBitmap downscaling (`<= 1600px` JPEG) with deterministic `ObjectURL` cleanup |
| `state.js` | Reactive application state, starter rooms, spatial tree indexing, breadcrumb/hierarchy resolvers, dropdown hydration |
| `router.js` | Hash deep-link router (`#/`, `#/needs-home`, `#/room/:id`, `#/unit/:id`, `#/drawer/:id`, `#/items`, `#/in-use`) & back-stack |
| `views.js` | XSS-hardened renderers for zero-scroll Home (`Places`), live search, `Needs a home`, Room/Container/Drawer drill-downs, Items catalog, In Use, and Item detail sheet |
| `modals.js` | Unified `+` action sheet, Add/Edit Item & Place forms, Snap & Batch Entry, Quick Photo modal, More settings, and single/bulk QR print |
| `app.js` | Theme lifecycle (`system` / `light` / `dark`), keyboard shortcuts (`/`, `Ctrl+K`, `Escape`), app bootstrap, and explicit `window` handler bindings |

---

## 6. Security Controls Summary
- **DOM XSS Prevention:** Dynamic strings interpolated into `innerHTML` pass through `escapeHtml()`, inline JS string arguments pass through `escapeJsStr()` (escaping quotes, slashes, newlines, `<`, and `>`), and all image URLs pass through `sanitizePhotoUrl()` on the frontend plus `validate_safe_photo_ref()` on Pydantic input schemas.
- **SQL Injection & Wildcard Safety:** 100% SQLAlchemy 2.0 bound parameters; user search terms escape `\`, `%`, and `_` (`escape="\\"`); item `sort` query parameters are restricted to the `ItemSortOrder` enum.
- **HTTP Hardening:** `RequestContextMiddleware` attaches `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`, `Permissions-Policy`, and `X-Request-ID`. Global exception handlers log server errors with correlation IDs without leaking stack traces or host paths.

---

## 7. Running & Testing

```powershell
# Run full isolated test suite (never touches data/inventory.db)
.\.venv\Scripts\pytest.exe -v

# Start LAN-accessible production server on 0.0.0.0:8000
.\.venv\Scripts\python.exe run.py
```

