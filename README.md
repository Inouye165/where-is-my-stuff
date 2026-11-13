# WhereIsMyStuff (WIMS) - Network Inventory System

A self-hosted, modular physical inventory tracker with dual-photo visual verification, hierarchical location modeling (Room, Shed, Public Storage, Bin), and a "Last Seen" presence audit trail.

---

## Quick Start

### 1. Launch the Server
```bash
# Using the virtual environment
.venv\Scripts\python run.py
```

The terminal will display:
```
=================================================================
 WhereIsMyStuff (WIMS) Inventory
=================================================================
 * Localhost access:        http://localhost:8000
 * Local Network (Phone):   http://192.168.x.x:8000
 * Interactive API Docs:    http://192.168.x.x:8000/docs
=================================================================
```

### 2. Connect from Any Device on Your Network
- Open `http://<your-lan-ip>:8000` on your smartphone, tablet, or laptop while connected to your home Wi-Fi.
- Use your phone's camera directly through the web UI to snap:
  1. **Container Photo**: What the bin, drawer, or tote looks like on the outside.
  2. **Spot Photo**: Where the bin or shelf is located in the room or storage area.
  3. **Item Photo**: Optional close-up of the component or tool itself.

---

## Key Capabilities

- **Instant Search**: Type `"esp32"`, `"stepper"`, `"picoscope"`, or `"driver"` to retrieve exact locations and drawers in under 5 milliseconds.
- **Dual Visual Confirmation**:
  - **Where it's stored**: Wide-angle photo of the shelf, rack, or room.
  - **What the bin looks like**: Close-up of the tote, drawer, or organizer exterior.
- **"Mark Verified" Button**: Single-tap action to record that you physically saw the item in that spot today, preventing ghost inventory.
- **Audit & Movement History**: Every relocation, audit touch, or modification is recorded with UTC timestamps.
- **Hierarchical Locations**: Infinite-depth tree (`Site -> Room/Area -> Shelf/Rack -> Bin/Drawer`).
- **Headless & Modular API**: Standard FastAPI REST API documented via Swagger/OpenAPI at `/docs`.

---

## Running the Automated Test Suite

Strict test-driven testing and regression test suite:
```bash
.venv\Scripts\pytest -v
```
Tests run against an isolated in-memory SQLite database with foreign key enforcement and cleanup.

