# Implementation Roadmap: WhereIsMyStuff (WIMS)

## Phase 1: Environment & Project Foundation
- [x] Initialize Python virtual environment with standard dependencies (`FastAPI`, `uvicorn`, `SQLAlchemy`, `pydantic`, `pytest`, `pillow`, `httpx`).
- [x] Configure database engine (`SQLite` with `WAL` mode and enforced foreign key constraints).
- [x] Establish base directory layouts for uploads (`originals/` and `thumbnails/`).
- [x] Set up `conftest.py` with in-memory SQLite fixture and isolated test client.

## Phase 2: Data Models & TDD Database Layer
- [x] Write unit tests for `Location` model (self-referencing hierarchical tree, breadcrumb resolution, cycles prevention).
- [x] Implement `Location` model with support for:
  - Level/type (`facility`, `room`, `rack_shelf`, `bin_container`).
  - Context photo path (where the bin sits).
  - Container photo path (what the bin looks like).
- [x] Write unit tests for `Item` model (attributes, quantity, category, tags, serial number).
- [x] Implement `Item` model with `location_id`, `last_seen_at`, and optional item photo.
- [x] Write unit tests for `AuditLog` model (tracking moves, audits, and timestamp history).
- [x] Implement `AuditLog` with foreign keys to item and location.

## Phase 3: REST API Service Layer (Modular & Headless Ready)
- [x] **Locations API** (`/api/v1/locations`):
  - [x] `POST /api/v1/locations` (Create room/shed/bin/shelf with parent reference).
  - [x] `GET /api/v1/locations` (Fetch tree or flat list with breadcrumbs).
  - [x] `GET /api/v1/locations/{id}` (Fetch location detail with child bins and items).
  - [x] `PATCH /api/v1/locations/{id}` (Update location metadata or parent).
  - [x] `DELETE /api/v1/locations/{id}` (Safe cascade / validation).
- [x] **Items API** (`/api/v1/items`):
  - [x] `POST /api/v1/items` (Create item inside a container or room).
  - [x] `GET /api/v1/items/{id}` (Fetch item details, exact location path, photos, and last-seen history).
  - [x] `PATCH /api/v1/items/{id}` (Update item fields or move to a new container).
  - [x] `POST /api/v1/items/{id}/verify` (Single-click "I verified it is here right now" audit touch).
  - [x] `DELETE /api/v1/items/{id}`.
- [x] **Search API** (`/api/v1/search`):
  - [x] `GET /api/v1/search?q={query}` (Instant search across items, tags, categories, containers, and notes).
- [x] **Photo Upload API** (`/api/v1/photos`):
  - [x] `POST /api/v1/photos/upload` (Supports container photo, location context photo, item photo).
  - [x] Automated thumbnail generation and EXIF auto-rotation.
  - [x] Serve photos securely via static mount `/uploads/...`.

## Phase 4: LAN-Accessible Web User Interface
- [x] Build responsive single-page web interface (mobile & desktop optimized).
- [x] Quick Search bar with instant typeahead results (e.g. typing "cap", "saw", "vacuum", "dog").
- [x] Visual Item Card showing:
  - Item name, category, quantity, last seen relative time badge.
  - Location breadcrumb (e.g., `Shed > Back Wall > Gray Bin #2`).
  - Dual-photo visual preview: Container exterior + Location shelf context.
  - One-tap "Mark Verified" button.
- [x] Location Explorer view (browse Facility -> Room -> Bin -> Items).
- [x] Quick Item / Container creation modal with direct camera photo upload.
- [x] LAN network runner (`python run.py`) binding to `0.0.0.0:8000` with local IP discovery printed to console.

## Phase 5: Test Suite, Regression & Verification
- [x] 100% pass on unit and integration test suites.
- [x] Regression test suite for:
  - Deeply nested location breadcrumb resolution.
  - Circular hierarchy prevention.
  - Last seen timestamp accuracy upon move vs audit.
  - Multi-term search queries with partial matching.
  - Non-ASCII and special character handling in names and tags.
  - Image upload error cases (corrupt files, unsupported formats).
- [x] Seed script with realistic examples (capacitor, reciprocating saw, portable vacuum, dog medication across house, shed, and public storage).
- [x] Verification on local network loopback.

## Phase 6: Spatial Refactor, Workbench Checkout & Physical-Digital Bridge
- [x] **Pillar 1: Desktop Workspace & Photo Dead Space**:
  - [x] Expanded workspace to `max-width: 1560px` with fluid 4-column storage unit grid (fits 6 containers above the fold).
  - [x] Collapsed empty photo placeholder boxes: sleek compact header with subtle inline `📷 Snap Cover` button on hover/corner.
  - [x] Removed duplicate `+ Add Storage Unit` button inside section header.
  - [x] Added 1-click Bulk Room Assignment banner and modal for organizing unassigned units.
- [x] **Pillar 2: Frictionless Ingestion (Snap & Batch Catalog)**:
  - [x] Added `POST /api/v1/items/bulk` with quick text parsing (`4x M3 Brass Standoffs`, `Needle Nose Pliers`, etc.).
  - [x] Added `POST /api/v1/locations/{id}/analyze-photo` with Gemini Vision check and zero-key fallback.
  - [x] Added `⚡ Snap & Catalog` modal for rapid drawer ingestion with side-by-side photo preview and multi-line parsing.
- [x] **Pillar 3: Workbench / In-Use Checkout State**:
  - [x] Added `status` (`stored` | `in_use`) and `status_note` to `Item` model, schemas, and SQLite migrations.
  - [x] Added 1-click `🔧 Pull to Workbench` and `↩ Return to Bin` toggle on Item Detail Panel and Drawer view.
  - [x] Added dedicated `🔧 Workbench` tab and filter badge in toolbar (`badge-workbench-count`).
  - [x] Added amber `On Workbench` badge on item cards, table rows, and drawer listings.
- [x] **Pillar 4: Physical-to-Digital Bridge (Deep Links & Printable QR)**:
  - [x] Hash-based routing (`#/unit/{id}`, `#/drawer/{id}`, `#/room/{id}`, `#/workbench`, `#/items`).
  - [x] Added `GET /api/v1/locations/{id}/qr` endpoint generating sharp scannable PNG QR codes.
  - [x] Added Printable QR label sheet modal with `@media print` styling for printing bin and shelf labels.
- [x] **Pillar 5: Mobile LAN Ergonomics**:
  - [x] Thumb-friendly fixed bottom navigation bar on mobile (`@media (max-width: 768px)`).
  - [x] Minimum 44px touch targets on buttons, drawer cards, and steppers.

## Phase 7: "Simple by Default, Powerful on Demand" UX Audit Backlog
- [x] **Scan with Camera Modal (`#drawer-rapid-entry-modal`) & Drawer Scan Clutter**:
  - [x] Refactored into 2-step progressive disclosure (Step 1: Phone Camera vs. Upload Photo + compact location pill; Step 2: 1-line expandable part rows, auto-scan, smart save-mode visibility, and collapsed Advanced options).
- [x] **1. Add / Edit Item Modal (`#add-item-modal`)**:
  - [x] Show only *Item Name*, *compact Location pill (`[Change]`)*, *Qty*, and *Photo* upfront; collapse *Unit*, *Category*, *Brand/Model*, *Search keywords*, and *Notes* behind `+ More details ▾` (auto-expanded on edit when populated).
- [x] **2. Add Space / Container / Drawer Modal (`#add-storage-modal`)**:
  - [x] Lock/hide the 3-way type switcher when launched from a specific context, replace parent dropdown with a compact `in <Location> [Change]` pill, show *Name + Photo* upfront, and move *Auto-create drawers / grid*, *Notes*, and *Second context photo* behind optional toggles.
- [x] **3. Room View & Storage Container View Action Bars (`views.js`)**:
  - [x] Consolidate stacked/duplicate action bars to 1 primary CTA + 1 secondary CTA in the header bar, moving *Design grid*, *Edit*, *Change/Crop photo*, *Print QR*, *Move*, and *Delete* into a clean `⋯` menu.
- [x] **4. Interactive Grid Designer Toolbar (`grid-designer.js`)**:
  - [x] Show only *Rows × Cols*, interactive visual grid, and *Save Grid* upfront; group layout templates (*3×3*, *Dresser*, *Organizer*), *Fill empty*, *Auto-name*, and *Default slot type* under `Templates & tools ▾`, and reveal merge/split controls contextually on cell selection.
- [x] **5. Photo Crop & Straighten Editor (`#photo-editor-modal`)**:
  - [x] Default to drag-crop handles + *Rotate 90°* + *Use Photo*; collapse *Fine tilt (−0.1°/+0.1°)*, *Width/Height scale*, and *4-Corner Perspective Straighten* under `Adjust & Perspective ▾`.
- [x] **6. Item Detail Page (`renderItemView` in `views.js`)**:
  - [x] Hide empty optional cards (*Parts List*, *Notes*, *Datasheets & Reference Files*) behind compact `+ Add parts list` / `+ Add notes` / `+ Attach file` chips, collapse *Activity History* by default, and move *QR label* + *Delete* into a `⋯` menu.
- [x] **7. Global `+ Add` Action Sheet (`#add-action-sheet`)**:
  - [x] Trim overlapping storage cards down to 3 clear choices (*Scan with Camera*, *Add Item Manually*, *New Space or Container*) with automatic context pre-selection from the active view.

