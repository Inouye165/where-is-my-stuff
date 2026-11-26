# Engineering Code Audit — WhereIsMyStuff (WIMS)

> **Scope:** Full-stack audit of `app/` (FastAPI backend, SQLAlchemy data layer, services, and `app/static/` frontend) plus runtime/tooling configuration (`run.py`, `requirements.txt`, `pytest.ini`, `.env.example`) evaluated against modern production engineering standards (Meta / Google bar: security, scalability, concurrency, modularity, reliability, observability, and maintainability).
>
> **Status:** Hardening complete for a personal/family home-LAN deployment (`186 passed` in `pytest -v`). Security, concurrency, session persistence, pagination, CSP, cross-process migration locking, and full upload+DB backups are verified. Remaining deferred items (optional enterprise-scale refactors such as Alembic, FTS5, Vite/TypeScript rewrite) are noted inline.

---

## Summary Matrix

| Severity | Count | Primary Themes |
| :--- | :---: | :--- |
| **P0 — Critical** | 5 | Missing auth/rate-limiting on `0.0.0.0`, URL path traversal with API key, SSRF fail-open/rebinding, thread-unsafe DB sessions across async I/O, unvalidated file attachments |
| **P1 — High** | 7 | Full-DB snapshot on single reads, `limit=20000` full-table client fetch, process-local state/locks breaking multi-worker scaling, missing grid concurrency controls, unbounded session memory, `ILIKE` full-table scans, sequential batch HTTP |
| **P2 — Medium** | 9 | 5,020-LOC JS / 10,526-LOC CSS monoliths & 260+ `window` globals, backend god services, homegrown migrations, naive timestamps breaking Postgres, mixed commit boundaries, Host/QR URL trust, HTTP client lifecycle, import side effects, middleware/observability gaps |
| **P3 — Low** | 7 | Unpinned dependencies, missing linting/typing/CI/build pipeline, manual `os.getenv` config, untyped API responses, incomplete backups, manual SW cache busting, ORM N+1 helper |

---

## P0 — Critical (Security, Data Integrity & Outage Risks)

### 1. `[DONE]` Zero Authentication, Authorization, or Rate Limiting on Network-Exposed API
- **Locations:** [app/main.py](app/main.py#L47-L58), [app/core/config.py](app/core/config.py#L36), [app/api/system.py](app/api/system.py#L47-L60), [app/api/locations.py](app/api/locations.py#L127-L145)
- **Issue:** The server binds to `0.0.0.0:8000` by default, exposing all endpoints to the entire network without authentication, CSRF protection, or rate limiting. Any device on the LAN (or anyone if port-forwarded) can:
  - Delete all locations and items (`DELETE /api/v1/locations/{id}`, `DELETE /api/v1/items/{id}`).
  - Trigger disk-filling hot backups (`POST /api/v1/system/backup`) or crop deletion (`POST /api/v1/system/cleanup-unused-crops?dry_run=false`).
  - Burn paid Gemini/OpenAI API quotas (`POST /api/v1/locations/{id}/analyze-photo`, `POST /api/v1/search/photo`).
  - Download all uploaded photos and private PDFs/documents directly from `/uploads/...`.
- **Standard:** Defense-in-depth — even LAN-first applications should support optional token/session auth (e.g., bearer token or HttpOnly session cookie), per-IP rate limiting on expensive/mutating endpoints (AI vision, uploads, backups), and explicit opt-in before binding to non-loopback interfaces.

### 2. `[DONE]` Authenticated Path Traversal / Arbitrary Endpoint Invocation via Vision `model` Parameter
- **Locations:** [app/services/vision_service.py](app/services/vision_service.py#L293-L312), [app/api/locations.py](app/api/locations.py#L127-L145)
- **Issue:** `POST /api/v1/locations/{location_id}/analyze-photo` accepts an arbitrary `model: Optional[str] = Form(default=None)` and interpolates it directly into the Gemini API URL:
  ```python
  url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
  ```
  The server then sends its secret `x-goog-api-key` header to that URL. Because `model` is neither allowlisted nor regex-validated (e.g. `^[a-zA-Z0-9._\-]+$`), a caller can inject path traversal (`../../...`) or query/fragment characters, or force expensive models (`gemini-2.5-pro`, `gpt-4o`) without restriction.
- **Standard:** Strictly validate `model` against a server-side allowlist (or strict identifier regex) before constructing upstream URLs or payloads.

### 3. `[DONE]` SSRF Bypass via Fail-Open DNS Resolution and TOCTOU DNS Rebinding
- **Locations:** [app/services/image_lookup_service.py](app/services/image_lookup_service.py#L34-L89), [app/services/image_lookup_service.py](app/services/image_lookup_service.py#L219-L259)
- **Issue:**
  1. `_host_resolves_publicly()` **fails open**: `except (socket.gaierror, UnicodeError, OSError): return True` (lines 72–73). Any DNS failure allows the outbound request to proceed.
  2. **TOCTOU DNS rebinding:** `_is_fetchable_url()` resolves DNS via `socket.getaddrinfo()`, and `httpx` later resolves DNS *again* during `client.stream("GET", current)`. An attacker-controlled domain returned via Openverse or a redirect can return a public IP on check #1 and `127.0.0.1` / `169.254.169.254` / RFC1918 IP on check #2.
  3. IP checks test individual flags (`is_private`, `is_loopback`, etc.) rather than the canonical `not ip.is_global` check (missing CGNAT `100.64.0.0/10` and edge ranges depending on Python/IPv6 mapping).
- **Standard:** Always fail closed on DNS errors, validate `ip.is_global` on every resolved address, and pin the validated IP at the transport/socket layer (or restrict image downloads to an allowlist of known CDN hostnames).

### 4. `[DONE]` Thread-Unsafe SQLAlchemy `Session` Shared Across Worker Threads & Held Open Across External I/O
- **Locations:** [app/api/items.py](app/api/items.py#L135-L163), [app/api/locations.py](app/api/locations.py#L127-L193), [app/services/location_service.py](app/services/location_service.py#L1014-L1035), [app/services/search_service.py](app/services/search_service.py#L130-L166)
- **Issue:** Several `async def` endpoints inject a synchronous SQLAlchemy `Session = Depends(get_db)` and:
  1. Pass that single `Session` across multiple `run_in_threadpool` / `anyio.to_thread.run_sync` calls (which can execute on different worker threads, while FastAPI's generator teardown commits/closes it on yet another thread). SQLAlchemy `Session` is explicitly **not thread-safe**.
  2. Hold the DB connection open while awaiting slow external network calls (up to 120s for Ollama, 30s for Gemini/OpenAI, or minutes for `find_and_import_images_batch`). Under concurrent load this exhausts the DB connection pool and can hold SQLite write locks.
- **Standard:** Either migrate to true `AsyncSession` (`sqlalchemy.ext.asyncio`), or open/close short-lived synchronous `SessionLocal()` scopes *inside* each threadpool unit of work so no DB session is ever held across `await` boundaries or shared across threads.

### 5. `[PARTIAL — UPLOADS, HEADERS & GLOBAL CSP DONE; INDEX.HTML INLINE HANDLERS REMAIN]` Unvalidated Document Attachment Uploads & Missing Global Content-Security-Policy
- **Locations:** [app/services/photo_service.py](app/services/photo_service.py#L22-L24), [app/services/photo_service.py](app/services/photo_service.py#L334-L366), [app/core/middleware.py](app/core/middleware.py#L32-L45), [app/main.py](app/main.py#L57-L58)
- **Issue:**
  1. `process_and_save_attachment()` validates non-image uploads (`.pdf`, `.svg`, `.zip`, `.doc`, `.docx`, `.xlsx`, etc.) **solely by file extension** with no magic-byte verification, no ZIP bomb/compression ratio check, and no sanitization.
  2. Files in `/uploads/attachments/` are served statically via `StaticFiles` without `Content-Disposition: attachment` (for non-previewable types) and without a dedicated download endpoint.
  3. There is **no global `Content-Security-Policy`** header on the application's HTML pages (only on `/uploads/*.svg` and `/uploads/*.pdf` etc.), largely because the frontend relies on inline `<script>` and 490+ inline `onclick=` handlers.
- **Standard:** Verify file magic bytes/MIME signatures, serve user-uploaded attachments from a controlled endpoint (or separate origin) with `Content-Disposition: attachment` / strict CSP, and enforce a strict `Content-Security-Policy` (`default-src 'self'; script-src 'self'`) on all HTML responses.

---

## P1 — High (Scalability, Concurrency & Architectural Bottlenecks)

### 6. `[DONE]` Full-Database Snapshot Materialization on Single-Entity Reads and Writes
- **Locations:** [app/services/location_service.py](app/services/location_service.py#L140-L158), [app/services/location_service.py](app/services/location_service.py#L484-L490), [app/services/inventory_service.py](app/services/inventory_service.py#L64-L73), [app/services/unit_setup_service.py](app/services/unit_setup_service.py#L134-L139)
- **Issue:** `LocationService.build_snapshot()` loads **every `Location` row**, **all `LocationPhoto` rows**, and **all grouped item counts** from the entire database into Python memory. It is invoked on:
  - `GET /api/v1/locations/{id}`, `POST /api/v1/locations`, `PATCH /api/v1/locations/{id}`
  - `GET /api/v1/items/{id}`, `POST /api/v1/items`, `PATCH /api/v1/items/{id}`, `POST /api/v1/items/{id}/verify`
  - Every `GET /api/v1/unit-setup/sessions/{token}` poll (called every few seconds during setup)
- **Impact:** `O(N_locations + N_photos)` database and CPU cost for every single-item or single-location API call.
- **Standard:** Use targeted ancestor recursive CTEs (for breadcrumbs and inherited photos of one location or a batch of `location_id`s), or maintain a materialized path / closure table so single-entity reads are `O(depth)` instead of `O(total_table_size)`.

### 7. `[DONE]` Client Loads Entire Inventory (`limit=20000`) & APIs Lack Pagination
- **Locations:** [app/static/js/state.js](app/static/js/state.js#L573-L576), [app/api/items.py](app/api/items.py#L55-L72), [app/repositories/item_repository.py](app/repositories/item_repository.py#L22-L50), [app/api/locations.py](app/api/locations.py#L37-L49)
- **Issue:** On startup and on every mutation/cross-tab sync, the frontend calls `GET /api/v1/items?limit=20000` and `GET /api/v1/locations/tree`, pulling the entire database (including full item descriptions, breadcrumbs, and nested location photos) into browser memory. Neither `GET /api/v1/items` nor `GET /api/v1/search` supports `offset` or cursor-based pagination, nor a `total` count envelope for items.
- **Impact:** High TTFB, multi-megabyte JSON payloads, OOM risk on mobile browsers, and easy server CPU/memory exhaustion by requesting `limit=20000`.
- **Standard:** Cap page size (e.g., `le=200`), implement cursor-based (keyset) pagination (`cursor`, `limit`, `next_cursor`, `total`), and fetch scoped/paginated views on demand on the frontend.

### 8. `[PARTIAL — SESSIONS DB-PERSISTED; IN-PROCESS LOCKS/LIMITER REMAIN]` Process-Local In-Memory State & Threading Locks Break Horizontal / Multi-Worker Scaling
- **Locations:** [app/services/capture_session_service.py](app/services/capture_session_service.py#L26-L43), [app/services/unit_setup_service.py](app/services/unit_setup_service.py#L37-L54), [app/services/location_service.py](app/services/location_service.py#L31-L32), [app/services/photo_service.py](app/services/photo_service.py#L30-L31)
- **Issue:**
  - `CaptureSessionService._sessions` and `UnitSetupService._sessions` live in process-local Python dicts. Running `uvicorn --workers 2` or deploying multiple containers immediately causes 404s when a phone hits a different worker than the desktop. All active sessions are lost on restart.
  - `_GRID_SLOT_LOCK`, `_REPARENT_LOCK`, and `PhotoService._inflight_refs` / `_cleanup_lock` are process-local `threading.Lock` objects that provide **zero protection across multiple processes/workers**.
- **Standard:** Persist ephemeral sessions in the database (or Redis) with a TTL column; enforce data invariants via database-level locks (`BEGIN IMMEDIATE` / `SELECT ... FOR UPDATE`) and unique constraints rather than Python `threading.Lock`s.

### 9. `[DONE]` Missing Concurrency Controls on Grid Mutations & Missing DB-Level Uniqueness Constraints
- **Locations:** [app/services/location_service.py](app/services/location_service.py#L538-L551), [app/services/location_service.py](app/services/location_service.py#L637-L749), [app/services/location_service.py](app/services/location_service.py#L775-L859), [app/models/location.py](app/models/location.py#L26-L32)
- **Issue:** `ensure_grid_slot()` acquires `_GRID_SLOT_LOCK`, but `save_grid_layout()`, `move_grid_slot()`, `create()`, and `update()` do **not** acquire `_GRID_SLOT_LOCK`. Concurrent grid operations can interleave and persist overlapping compartments. There is also no database-level partial unique index on `(parent_id, grid_row, grid_col) WHERE grid_row IS NOT NULL AND grid_col IS NOT NULL`.
- **Standard:** Enforce grid uniqueness at the database layer and serialize all grid mutations on the same parent container within a single transaction using row-level locking on the parent `Location`.

### 10. `[DONE]` Unbounded In-Memory Session Growth (Memory Exhaustion DoS)
- **Locations:** [app/services/capture_session_service.py](app/services/capture_session_service.py#L35-L64), [app/services/unit_setup_service.py](app/services/unit_setup_service.py#L46-L95)
- **Issue:** Neither session service enforces a maximum active session cap. Expired sessions are retained for 30 minutes past `expires_at` (`stale_cutoff = now - timedelta(minutes=30)`). An attacker (or buggy client loop) calling `POST /api/v1/photos/capture-sessions` or `POST /api/v1/unit-setup/sessions` can grow `self._sessions` without bound until the process OOMs.
- **Standard:** Enforce a hard cap (e.g., LRU/max active sessions) alongside DB/Redis persistence and rate limiting.

### 11. `[PARTIAL — REGEXES PRECOMPILED; FTS5 PENDING]` Full-Table `ILIKE '%...%'` Search & Per-Item Regex Recompilation
- **Locations:** [app/repositories/item_repository.py](app/repositories/item_repository.py#L62-L86), [app/services/search_service.py](app/services/search_service.py#L74-L92)
- **Issue:**
  1. `ItemRepository.search_items()` executes leading-wildcard `ILIKE '%token%'` across 5 columns (`name`, `description`, `category`, `tags`, `serial_or_part_number`) for every token and synonym, pulling up to `_CANDIDATE_POOL = 2000` ORM rows into Python for scoring. Leading-wildcard `ILIKE` cannot use B-tree indexes and triggers a full table scan on every keystroke.
  2. `_relevance()` recompiles `re.compile(rf"(?<![a-z0-9]){re.escape(tok)}")` inside the per-item loop (up to `2000 × len(synonyms)` regex compilations per request).
- **Standard:** Use SQLite FTS5 (and PostgreSQL `tsvector` / `pg_trgm` GIN indexes) for indexed full-text search with native BM25/rank scoring; precompile any regex patterns once outside the loop.

### 12. `[DONE]` Unbounded Sequential External HTTP Calls in Batch Image Lookup
- **Locations:** [app/services/image_lookup_service.py](app/services/image_lookup_service.py#L195-L216), [app/services/image_lookup_service.py](app/services/image_lookup_service.py#L292-L316), [app/api/locations.py](app/api/locations.py#L148-L192)
- **Issue:** `find_and_import_images_batch()` processes items strictly sequentially, making up to `len(queries) × 3` external API calls (Wikimedia, Wikipedia, Openverse) + image download per item inside a synchronous HTTP request with no overall timeout, no item-count cap, no concurrency semaphore, and no background job queue. A drawer with 50 items can hang the HTTP connection for several minutes.
- **Standard:** Move batch image lookup to an asynchronous background job/task with status polling (or cap batch size and use bounded concurrency via `asyncio.Semaphore` with a strict overall deadline).

---

## P2 — Medium (Modularity, Maintainability, Reliability & Portability)

### 13. `[PARTIAL — ZERO INLINE SCRIPTS & DELEGATED DISPATCHER ADDED; FULL SPLIT PENDING]` Frontend Monolith: Oversized Files, 260+ `window` Globals & 490+ Inline Event Handlers
- **Locations:** [app/static/js/views.js](app/static/js/views.js) (5,020 LOC / 214 KB), [app/static/js/modals.js](app/static/js/modals.js) (3,008 LOC / 118 KB), [app/static/js/grid-designer.js](app/static/js/grid-designer.js) (1,623 LOC), [app/static/js/unit-setup-wizard.js](app/static/js/unit-setup-wizard.js) (1,305 LOC), [app/static/js/mobile-views.js](app/static/js/mobile-views.js) (1,214 LOC), [app/static/js/mobile-unit-setup.js](app/static/js/mobile-unit-setup.js) (1,186 LOC), [app/static/css/app.css](app/static/css/app.css) (10,526 LOC / 206 KB), [app/static/index.html](app/static/index.html) (1,324 LOC), [app/static/js/app.js](app/static/js/app.js#L570-L836)
- **Issue:**
  - `views.js` (5,020 lines) and `modals.js` (3,008 lines) combine data fetching, state mutation, HTML string templating, and DOM event handling in giant procedural files.
  - `app.js` attaches **~265 functions directly to `window`** via `Object.assign(window, ...)` to wire up **~490 inline `onclick=` / `onchange=` attributes** across `index.html` (182) and template strings (`views.js`: 211, `grid-designer.js`: 42, `mobile-views.js`: 32, `modals.js`: 14).
  - 85 `.innerHTML` assignments rely entirely on disciplined manual `escapeHtml()` / `escapeJsStr()` calls — a single missed escape introduces Stored XSS.
  - `app.css` is a single 10,526-line unminified stylesheet with no component scoping or dead-CSS pruning.
- **Standard:** Decompose views/modals by domain feature, replace inline `on*=` attributes with delegated `addEventListener` bindings (`data-action`), eliminate `window` globals (unlocking strict CSP), and adopt a typed component architecture (e.g., TypeScript + Vite + component modules or Preact/React/Lit) with automated XSS-safe templating.

### 14. `[PARTIAL — QUICK-ENTRY PARSER & ROUTER IMAGE ORCHESTRATION EXTRACTED]` Backend God Services & Leaked Layer Boundaries
- **Locations:** [app/services/location_service.py](app/services/location_service.py) (1,038 LOC), [app/services/inventory_service.py](app/services/inventory_service.py) (777 LOC), [app/services/vision_service.py](app/services/vision_service.py) (579 LOC), [app/services/photo_service.py](app/services/photo_service.py) (501 LOC), [app/api/items.py](app/api/items.py#L135-L163), [app/api/locations.py](app/api/locations.py#L148-L192)
- **Issue:**
  - `LocationService` handles CRUD, hierarchy snapshotting, grid geometry/merging/swapping, QR code generation, photo gallery management, and AI vision analysis in one class.
  - `InventoryService` handles CRUD, quick-entry NLP/regex parsing, bulk/box/assortment orchestration, PDF attachment creation, and audit logging.
  - Despite having `app/repositories/`, services execute raw SQLAlchemy `select()` / `update()` / `db.get()` calls directly in dozens of places, undermining repository encapsulation.
  - Routers `items.py` (`find_and_import_item_image`) and `locations.py` (`find_location_item_images`) embed multi-step business orchestration and orphan-file rollback logic directly in HTTP handlers.
- **Standard:** Split god services into focused single-responsibility services (`GridLayoutService`, `LocationPhotoService`, `QuickEntryParser`, `AttachmentService`, `ImageImportService`), keep routers thin, and centralize persistence queries in repositories.

### 15. `[PARTIAL — CROSS-PROCESS FILE/DB MIGRATION LOCKING ADDED; ALEMBIC DEFERRED]` Homegrown Database Migration Runner Without Rollbacks or Multi-Process Locking
- **Locations:** [app/db/migrations.py](app/db/migrations.py#L1-L172)
- **Issue:** Schema evolution relies on a custom Python list of raw `ALTER TABLE` statements mixed with `Base.metadata.create_all()`. There is no downgrade/rollback path, no autogenerated schema drift detection, and `_MIGRATION_LOCK = threading.Lock()` does not coordinate across multiple worker processes starting simultaneously.
- **Standard:** Use **Alembic** with versioned upgrade/downgrade migrations run as an explicit pre-start release step (not inside worker lifespan startup).

### 16. `[DONE]` Timezone-Naive `DateTime` Columns Break PostgreSQL Compatibility
- **Locations:** [app/models/item.py](app/models/item.py#L33-L37), [app/models/item.py](app/models/item.py#L68), [app/models/location.py](app/models/location.py#L34-L35), [app/models/location.py](app/models/location.py#L85), [app/models/audit.py](app/models/audit.py#L24)
- **Issue:** Every timestamp column uses `DateTime` (default `timezone=False`) while defaults use timezone-aware `utc_now()`. In PostgreSQL (which `AGENTS.md` mandates full compatibility with), `TIMESTAMP WITHOUT TIME ZONE` strips tzinfo and causes `TypeError: can't compare offset-naive and offset-aware datetimes` in Python/SQL comparisons.
- **Standard:** Use `DateTime(timezone=True)` across all ORM models and `TIMESTAMPTZ` in PostgreSQL migrations.

### 17. Inconsistent Transaction Boundaries (`db.commit()` Inside Services vs `get_db()`)
- **Locations:** [app/core/database.py](app/core/database.py#L38-L47), [app/services/location_service.py](app/services/location_service.py#L316), [app/services/location_service.py](app/services/location_service.py#L549), [app/services/location_service.py](app/services/location_service.py#L998), [app/services/capture_session_service.py](app/services/capture_session_service.py#L138-L145), [app/services/unit_setup_service.py](app/services/unit_setup_service.py#L274)
- **Issue:** `get_db()` implements the Unit-of-Work pattern (auto-commit on success, rollback on error), yet multiple service methods call `db.commit()` mid-request. This breaks atomicity when services are composed and causes partial commits if a later step in the same request fails.
- **Standard:** Keep transaction commit/rollback strictly at the request/Unit-of-Work boundary; services should only call `db.flush()` (or use explicit `db.begin_nested()` savepoints when needed).

### 18. `[DONE]` Unvalidated QR / Deep-Link `base_url` and `Host` Header Trust
- **Locations:** [app/core/network.py](app/core/network.py#L28-L67), [app/api/locations.py](app/api/locations.py#L63-L78), [app/services/location_service.py](app/services/location_service.py#L861-L883)
- **Issue:** `GET /api/v1/locations/{id}/qr` accepts arbitrary `target_url` and `base_url` query parameters (and falls back to the untrusted `Host` header) without validating the scheme or allowed hosts. Anyone can use the app's QR endpoint as an open QR-code generator pointing to arbitrary external/phishing URLs (e.g., `?target_url=https://evil.example`).
- **Standard:** Validate `target_url`/`base_url` against `http`/`https` schemes and an allowlist of permitted hosts (or configured `PUBLIC_BASE_URL` / LAN subnet), and add Starlette's `TrustedHostMiddleware` for `Host` header validation.

### 19. `[PARTIAL — SINGLE-OPEN CROP, LOGGING & PAYLOAD HELPERS DONE; LIFESPAN CLIENT POOLING PENDING]` No Shared `httpx.AsyncClient` Pooling, Silent Exception Swallowing & Redundant Image Decoding
- **Locations:** [app/services/vision_service.py](app/services/vision_service.py#L192), [app/services/vision_service.py](app/services/vision_service.py#L260), [app/services/vision_service.py](app/services/vision_service.py#L310), [app/services/vision_service.py](app/services/vision_service.py#L355), [app/services/vision_service.py](app/services/vision_service.py#L539-L574), [app/services/vision_service.py](app/services/vision_service.py#L456-L462), [app/services/photo_service.py](app/services/photo_service.py#L187)
- **Issue:**
  1. `VisionService` instantiates a new `httpx.AsyncClient` on every single call instead of reusing a shared client managed in FastAPI `lifespan` (losing TCP/TLS keep-alive connection pooling).
  2. `extract_search_query()` swallows all errors with bare `except Exception: pass` (lines 546–547, 572–573), hiding auth failures, quota errors, and schema changes from logs.
  3. `_crop_all()` calls `photo_service.crop_and_save(image_path, ...)` inside a loop, reopening and EXIF-transposing the same full-resolution image from disk once per detected item (e.g., 30 disk reads + decodes for a 30-part assortment kit).
  4. Gemini/OpenAI request construction is copy-pasted between `_analyze_with_*` and `extract_search_query`.
- **Standard:** Manage shared `httpx.AsyncClient` instances in app lifespan, log all upstream failures with structured context, decode the source image once per batch crop, and unify provider adapters behind a `VisionProvider` protocol/interface.

### 20. `[PARTIAL — CREATE_APP() FACTORY ADDED; MODULE SINGLETONS RETAINED FOR TEST COMPAT]` Import-Time Side Effects & Global Singleton Coupling
- **Locations:** [app/main.py](app/main.py#L56-L69), [app/core/logging_config.py](app/core/logging_config.py#L72), [app/services/photo_service.py](app/services/photo_service.py#L32-L33), [app/services/photo_service.py](app/services/photo_service.py#L474)
- **Issue:** Importing `app.main`, `app.core.logging_config`, or `app.services.photo_service` immediately mutates the filesystem (`mkdir`, creates `logs/wims.log`), mutates Pillow's global `Image.MAX_IMAGE_PIXELS`, creates the SQLAlchemy `engine`, and instantiates module-level service singletons (`location_service`, `inventory_service`, `photo_service`, etc.). This complicates isolated testing, dependency injection, and multi-environment configuration.
- **Standard:** Adopt an explicit `create_app(settings)` factory and FastAPI `Depends()`-based service/client injection so importing modules has zero filesystem or network side effects.

### 21. `[PARTIAL — PURE ASGI, STATUS LOGGING & 503 HEALTH DONE; JSON LOGS/METRICS PENDING]` `BaseHTTPMiddleware` Overhead & Incomplete Observability / Health Semantics
- **Locations:** [app/core/middleware.py](app/core/middleware.py#L12-L45), [app/core/logging_config.py](app/core/logging_config.py#L45-L48), [app/api/system.py](app/api/system.py#L16-L44)
- **Issue:**
  1. `RequestContextMiddleware` inherits from Starlette's `BaseHTTPMiddleware`, which has known performance overhead and request/response streaming caveats compared to pure ASGI middleware.
  2. Request logs omit **HTTP status code**, **client IP**, and **error code** (`"%s %s -> %.2fms"`), and logs are plain text rather than structured JSON (hard to ingest into Datadog/ELK/Cloud Logging). There are no Prometheus/OpenTelemetry metrics or tracing hooks.
  3. `GET /api/v1/health` always returns HTTP `200 OK` even when `status == "degraded"`, and creates/deletes a probe file on disk on **every** health check call (unnecessary disk I/O under frequent liveness/readiness probes).
- **Standard:** Use pure ASGI middleware, emit structured JSON logs including `status_code`, `duration_ms`, and `request_id`, expose `/metrics` or OpenTelemetry instrumentation, separate `/health/live` from `/health/ready`, and return `503 Service Unavailable` when unhealthy/degraded.

---

## P3 — Low (Tooling, Supply Chain, Typing & Developer Experience)

### 22. `[PARTIAL — BOUNDED PYPROJECT RUNTIME/DEV SPLIT ADDED; LOCKFILE OPTIONAL]` Unpinned Dependencies & Mixed Dev/Prod `requirements.txt`
- **Locations:** [requirements.txt](requirements.txt#L1-L13)
- **Issue:** All dependencies use open-ended `>=` constraints with no lockfile (`uv.lock`, `poetry.lock`, or `pip-compile` lockfile), making builds non-reproducible and vulnerable to breaking upstream releases. Test-only packages (`pytest`, `pytest-asyncio`) are bundled with production dependencies.
- **Standard:** Pin exact versions with hashes in a lockfile and separate runtime vs dev/test dependency groups in `pyproject.toml`.

### 23. `[PARTIAL — PYPROJECT RUFF/MYPY CONFIG ADDED; CI/BUNDLER PENDING]` Missing Static Analysis, Type Checking, Frontend Build Pipeline & CI/CD
- **Locations:** Repository root (no `pyproject.toml`, `ruff.toml`, `mypy.ini`, `package.json`, `tsconfig.json`, `Dockerfile`, or `.github/workflows/`)
- **Issue:** There is no automated linting (`ruff`), formatting (`ruff format`), static type checking (`mypy` / `pyright`), security scanning (`bandit` / `pip-audit`), frontend type checking/bundling/minification, or CI pipeline running tests on every commit/PR. Additionally, `pytest.ini` globally silences all `DeprecationWarning`s (`ignore::DeprecationWarning`), hiding upcoming library breaking changes.
- **Standard:** Add `pyproject.toml` configuring `ruff` + `mypy --strict` + `pytest` (filtering only targeted third-party warnings), a frontend build/lint step, and a CI workflow gating merges on lint + typecheck + tests + dependency audit.

### 24. `[PARTIAL — ENV VALIDATION HELPERS & DATA_DIR OVERRIDE ADDED]` Manual `os.getenv` Lambdas Instead of `pydantic-settings`
- **Locations:** [app/core/config.py](app/core/config.py#L17-L80)
- **Issue:** `Settings` subclasses `pydantic.BaseModel` with manual `os.getenv(...)` default factories instead of `pydantic_settings.BaseSettings`. Invalid integer env vars (e.g. `PORT=abc`) raise raw `ValueError` instead of structured Pydantic validation errors; `DATA_DIR` / `UPLOAD_DIR` / `LOG_DIR` cannot be overridden via environment variables; and `VisionService` bypasses `settings` by re-reading `os.environ.get(...)` directly in multiple methods.
- **Standard:** Use `pydantic-settings` (`BaseSettings`) as the single source of truth for all configuration, making all storage paths env-configurable and removing ad-hoc `os.environ.get()` calls from services.

### 25. `[DONE]` Untyped `dict` Return Contracts on API Endpoints
- **Locations:** [app/api/items.py](app/api/items.py#L135-L163), [app/api/locations.py](app/api/locations.py#L148-L192), [app/api/photos.py](app/api/photos.py#L18-L20), [app/api/system.py](app/api/system.py#L53-L60)
- **Issue:** Four endpoints return untyped `dict` without a `response_model`, resulting in empty `{}` schemas in OpenAPI `/docs` and no runtime response validation.
- **Standard:** Define explicit Pydantic response models for every API endpoint.

### 26. `[DONE — FULL DB+UPLOADS ZIP ARCHIVE WITH SHA-256 MANIFEST]` Incomplete Backup Strategy (SQLite-Only, Excludes Uploaded Media)
- **Locations:** [app/db/backup.py](app/db/backup.py#L12-L88)
- **Issue:** `BackupService.create_hot_backup()` only backs up a local SQLite file (fails on PostgreSQL) and does **not** include `data/uploads/` (photos and attachments), meaning restoring a backup leaves broken image/PDF references if uploads are lost.
- **Standard:** Support pluggable backup backends (SQLite / `pg_dump`) and optionally bundle or snapshot upload assets alongside DB backups with manifest checksums.

### 27. Manual Service Worker Cache Versioning & No Content-Hashed Assets
- **Locations:** [app/static/sw.js](app/static/sw.js#L1-L34)
- **Issue:** `CACHE_NAME = 'wims-shell-v50'` and `APP_SHELL_URLS` are maintained by hand. Any newly added static file or edit without a manual bump risks stale client caches or broken offline shells.
- **Standard:** Generate content-hashed asset filenames and the precache manifest automatically via a frontend build step (e.g., Vite + Workbox).

### 28. N+1 Lazy-Loading Hazard on ORM `Location.get_breadcrumbs()`
- **Locations:** [app/models/location.py](app/models/location.py#L51-L71)
- **Issue:** `Location.get_breadcrumbs()` and `Location.full_path` traverse `curr = curr.parent` using default SQLAlchemy lazy loading, issuing one SQL `SELECT` per tree level whenever accessed outside `LocationHierarchySnapshot`.
- **Standard:** Remove lazy-loading traversal methods from ORM models (or enforce `lazy="raise"` on relationships by default) so callers explicitly load required hierarchy data via repositories/CTEs.

---

## Suggested Remediation Roadmap

1. **Phase 1 — Security Hardening (P0):** Validate vision `model` allowlist, fix SSRF fail-closed DNS + IP pinning, add magic-byte validation & `Content-Disposition` for attachments, fix async/thread-safe DB session lifecycle, and introduce optional auth + rate limiting.
2. **Phase 2 — Scalability & Data Integrity (P1):** Replace per-request `build_snapshot()` with targeted CTEs, add keyset pagination to `/items` and `/search` (removing `limit=20000`), move capture/unit-setup sessions to SQLite/Redis with caps, add DB-level grid uniqueness constraints, and introduce FTS5 search.
3. **Phase 3 — Architecture & Modularity (P2):** Adopt Alembic migrations, switch to `DateTime(timezone=True)`, split `LocationService`/`InventoryService`/`views.js`/`modals.js`, eliminate `window` globals + inline `onclick=` to enable strict CSP, and upgrade middleware/logging/health probes.
4. **Phase 4 — Engineering Excellence & DX (P3):** Add `pyproject.toml` (`ruff`, `mypy`, pinned lockfile), CI workflow, `pydantic-settings`, typed response schemas, and automated frontend bundling/asset hashing.
