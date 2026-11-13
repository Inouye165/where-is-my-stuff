# AGENTS.md - Engineering Guidelines for Autonomous & Human Contributors

## 1. Project Philosophy & Tone
This codebase is maintained with direct, pragmatic human engineering standards.
- **No AI Filler**: Avoid vacuous comments (`# Initialize variables`, `# Returns the result`), over-engineered boilerplate, and speculative abstractions.
- **Zero Hallucinated / Made-Up Information**:
  - Autonomous agents must **NEVER** fabricate, assume, or invent physical spaces, room names, item attributes, quantities, part numbers, or seed data.
  - Do **NOT** assume the user has a "Basement", "Garage", "Workshop", "Main House", or "Shed" unless explicitly provided by the user.
  - If information (e.g., location, room, quantity, category, specs) is missing or unconfirmed, use `None` or an explicit placeholder such as `"Unknown"` / `"Unknown Location"`.
  - Never populate live user databases with fabricated "sample" assets unless explicitly requested by the user for testing purposes.
- **Code Clarity**: Write self-documenting code with precise names, explicit type hints, and standard idioms.
- **Single Source of Truth**: Keep data models, schemas, and database constraints synchronized without duplicate validation logic.

---

## 2. Technology Stack & Boundaries
- **Runtime**: Python 3.11+
- **Framework**: FastAPI (async REST API + static UI mounting)
- **Validation & Serialization**: Pydantic v2
- **ORM / Persistence**: SQLAlchemy 2.0 (Declarative Dataclass / modern mapped columns)
- **Database Engine**: SQLite with `PRAGMA foreign_keys = ON;` and `PRAGMA journal_mode = WAL;`. Must remain fully compatible with PostgreSQL.
- **Testing**: `pytest`, `httpx` (async test client), `pytest-asyncio`
- **Image Processing**: `Pillow` (for resizing, orientation correction via EXIF, and WebP thumbnail generation)

---

## 3. Directory Structure
```
d:\inventory\
├── app\
│   ├── api\               # REST API routers (v1 endpoints)
│   │   ├── items.py
│   │   ├── locations.py
│   │   ├── photos.py
│   │   └── search.py
│   ├── core\              # App configuration, database connection, lifecycle
│   │   ├── config.py
│   │   └── database.py
│   ├── models\            # SQLAlchemy database models
│   │   ├── item.py
│   │   ├── location.py
│   │   └── audit.py
│   ├── schemas\           # Pydantic request/response models
│   │   ├── item.py
│   │   ├── location.py
│   │   └── search.py
│   ├── services\          # Business logic (search, storage, audit logging)
│   │   ├── inventory_service.py
│   │   ├── location_service.py
│   │   └── photo_service.py
│   ├── static\            # Mobile-friendly web frontend assets
│   │   ├── css\
│   │   ├── js\
│   │   └── index.html
│   └── main.py            # App factory & ASGI entry point
├── data\                  # Local data storage (ignored by git except .gitkeep)
│   ├── inventory.db
│   └── uploads\
│       ├── originals\
│       └── thumbnails\
├── tests\                 # Test suite (TDD + Regression)
│   ├── conftest.py        # Fixtures, test DB session, test client
│   ├── test_models.py
│   ├── test_locations_api.py
│   ├── test_items_api.py
│   ├── test_photos_api.py
│   ├── test_search_api.py
│   └── test_regression.py # Regression checks for fixed bugs and edge cases
├── AGENTS.md
├── TODO.md
├── VISION.md
├── requirements.txt
└── run.py                 # Runner script for network binding (0.0.0.0)
```

---

## 4. Development & Testing Protocol (Strict TDD)

### The TDD Workflow
1. **Red**: Before implementing any endpoint or business service, write the failing test in `tests/`.
2. **Green**: Write the minimal code necessary in `app/` to pass the test.
3. **Refactor**: Clean up implementation without altering external contracts or breaking tests.
4. **Regression**: Any reported bug must be reproduced with an isolated test in `tests/test_regression.py` before applying the fix.

### Running Tests
```bash
pytest -v
```
All tests must execute against an isolated in-memory or temporary test SQLite database with foreign keys enabled. Tests must never write to `data/inventory.db`.

---

## 5. Coding Standards & Invariants

### 1. Database Transactions & Integrity
- Foreign keys must always be enforced:
  ```python
  @event.listens_for(Engine, "connect")
  def set_sqlite_pragma(dbapi_connection, connection_record):
      cursor = dbapi_connection.cursor()
      cursor.execute("PRAGMA foreign_keys=ON")
      cursor.execute("PRAGMA journal_mode=WAL")
      cursor.close()
  ```
- Use async sessions or clean dependency-injected session management (`get_db`).
- Every mutation must commit or rollback cleanly.

### 2. Location Tree Hierarchy
- Locations can be top-level (e.g. `parent_id = None` for Shed, Public Storage, House) or nested arbitrarily deep (`Parent -> Child`).
- A location record must validate that it cannot be set as its own ancestor (prevent circular trees).
- Display paths must be computable as breadcrumbs (e.g. `Public Storage Unit 4B > Shelf 2 > Red Bin #3`).

### 3. Dual Photo Associations
- **Location/Bin Photos**: Attached directly to the Location/Bin.
  - `context_photo`: Wide shot of the area where the bin or shelf sits.
  - `container_photo`: Close-up of the bin, tote, or organizer.
- **Item Photos**: Attached to the Item itself (`item_photo`).
- Uploads must be stored with content-addressed or UUID filenames. Raw files must never overwrite existing assets.

### 4. "Last Seen" / Audit Invariant
- Any item creation, move, or explicit "Verify" action writes an entry to `audit_logs` and updates `item.last_seen_at`.
- Timezones must always be stored in UTC (`datetime.now(timezone.utc)`).

### 5. Network & Binding Invariants
- The development and production runners must bind to `0.0.0.0` so any device on the local LAN (e.g. phone scanning or inspecting storage) can connect without proxy reconfiguration.
- Port default: `8000`.

---

## 6. API Guidelines
- Version all endpoints under `/api/v1/...`.
- Return proper HTTP status codes: `201 Created` for creations, `404 Not Found` for missing resources, `422 Unprocessable Entity` for validation errors.
- Support JSON request bodies for metadata and `multipart/form-data` for image uploads.
- Auto-generate complete OpenAPI documentation at `/docs`.

