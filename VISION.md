# System Vision & Requirements: WhereIsMyStuff (WIMS)

## 1. Problem Statement
Physical belongings scatter across garages, workshops, basements, sheds, and off-site storage units. Over time, mental maps decay:
- "Do I already own a 470uF electrolytic capacitor, or do I need to order one?"
- "Which bin in the shed holds the reciprocating saw blades?"
- "Where is the unopened backup box of dog medication, and when did anyone last confirm it was actually there?"

Searching physical spaces without visual cues is slow and frustrating. Text descriptions alone ("gray tote in garage") fail when six identical gray totes sit on the same wire shelving rack.

## 2. Core Value Proposition
A self-hosted, local-network inventory system that answers two questions within 5 seconds:
1. **Where is it right now?** (Precise hierarchy down to the specific shelf, bin, or drawer).
2. **What does the physical spot look like?** (Dual-photo visual confirmation: the storage container itself, plus the wider room/rack context).

Additionally, it tracks **proof of physical presence** ("Last Seen" timestamp and audit history), preventing ghost inventory.

---

## 3. Physical Hierarchy Model
The physical world is nested. The data model reflects this via a recursive parent-child location tree:

```
[Site / Facility]         e.g., Home, Shed, Public Storage Unit 42B
   └── [Area / Room]      e.g., Garage, Electronics Bench, Upper Loft
        └── [Placement]   e.g., Rack B, Metal Cabinet, Northwest Corner
             └── [Container] e.g., Blue Tote #4, Akro-Mils Drawer 12-A
                  └── [Item] e.g., DeWalt Reciprocating Saw, 220uF Capacitor (10x)
```

### Visual Verification Dimensions
Every container or location can carry two critical visual references:
1. **Context Photo (`context_photo`)**: Shows where the container lives in the room/shed (e.g., wide shot of Shelf 3 in the North Shed).
2. **Container Photo (`container_photo`)**: Shows the exterior of the bin, tote, or organizer with its label/markings so it is instantly recognizable on sight.
3. **Item Photo (`item_photo`, optional)**: Shows the item itself for rapid identification.

---

## 4. Key Functional Capabilities

### Fast, Forgiving Search
- Real-time search across item names, aliases, part numbers, tags, categories, and container labels.
- Sub-second query response over local Wi-Fi from mobile phones, tablets, and desktop workstations.

### "Last Seen" / Physical Audit Verification
- Dedicated single-click action: **"Mark Verified / Last Seen"**.
- Automatically logs user, timestamp, and location at verification time.
- Displays aging indicators (e.g., "Verified 3 days ago" vs. "Not verified in 14 months").

### Network-Accessible Web UI
- Accessible across the local LAN (`http://<local-ip>:<port>`).
- Responsive mobile-first UI for immediate use while walking around with a phone camera.
- Direct image capture from phone browser using HTML5 camera inputs.

### API-First Architecture
- Clean decoupled backend exposing standard OpenAPI / JSON endpoints.
- Allows headless operation, third-party barcode/RFID scanners, external scripts, CLI utilities, and future mobile native apps.

---

## 5. Architectural Principles

1. **Zero Cloud Lock-in**:
   - 100% self-hosted on local hardware.
   - All media and database records live on local disk in predictable, human-inspectable directory structures.

2. **Language & Stack Standards**:
   - **Backend**: Python 3.11+ with FastAPI, Pydantic v2, and SQLAlchemy 2.0.
   - **Database**: SQLite with Write-Ahead Logging (`WAL` mode) enabled for concurrent reads and resilient writes. Designed with clean schema abstractions so switching to PostgreSQL is a config-string change.
   - **Media Storage**: Local hashed/UUID storage with automated WebP compression and thumbnail generation to preserve disk space and keep mobile page loads instant.
   - **Frontend**: Lightweight, high-performance UI delivered directly by the application server (no fragile node build chain required for runtime deployment).

3. **Engineering Integrity**:
   - Fully typed schemas (Pydantic / Python typing).
   - Database migrations managed and tracked.
   - Comprehensive test coverage: unit tests for domain logic, integration tests for API endpoints, regression tests for search and file uploads.

