"""Phase 7 "Simple by Default, Powerful on Demand" UX checks.

Modal behaviour is exercised by importing the real ES modules into Node with a
minimal lazily-populated DOM stub, so element IDs referenced by the code always
resolve without hand-maintaining a registry per test.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = ROOT / "app" / "static"
JS_DIR = (STATIC_DIR / "js").as_posix()
INDEX_HTML = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

DOM_PRELUDE = """
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.BroadcastChannel = undefined;
globalThis.window = {
  location: { host: 'localhost:8000', hash: '#/', origin: 'http://localhost:8000' },
  addEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {} }),
  confirm: () => true,
};
Object.defineProperty(globalThis, 'navigator', {
  value: { userAgent: 'Mozilla/5.0 Windows NT 10.0', maxTouchPoints: 0 },
  configurable: true,
});
globalThis.window.navigator = globalThis.navigator;

function makeEl(id) {
  const classes = new Set();
  const attrs = {};
  return {
    id,
    value: '',
    textContent: '',
    innerHTML: '',
    open: false,
    hidden: false,
    checked: false,
    disabled: false,
    style: { display: '' },
    dataset: {},
    children: [],
    get options() { return this.children; },
    classList: {
      add(...c) { c.forEach(x => classes.add(x)); },
      remove(...c) { c.forEach(x => classes.delete(x)); },
      toggle(c, force) {
        const on = force === undefined ? !classes.has(c) : Boolean(force);
        if (on) classes.add(c); else classes.delete(c);
        return on;
      },
      contains(c) { return classes.has(c); },
    },
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return k in attrs ? attrs[k] : null; },
    removeAttribute(k) { delete attrs[k]; },
    appendChild(child) { this.children.push(child); if (child) child.parentElement = this; return child; },
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    scrollIntoView() {},
    focus() {},
    click() {},
  };
}

const elements = {};
globalThis.el = (id) => (elements[id] ||= makeEl(id));
globalThis.document = {
  readyState: 'complete',
  documentElement: { setAttribute() {} },
  activeElement: null,
  getElementById: (id) => globalThis.el(id),
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener() {},
  createElement: (tag) => makeEl(tag),
};
globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({}) });

function assert(cond, msg) { if (!cond) throw new Error(msg); }
"""


def run_node(body: str) -> None:
    node_exe = shutil.which("node")
    if not node_exe:
        pytest.skip("Node.js not installed")
    script = DOM_PRELUDE + "\n(async () => {\n" + body + "\nprocess.exit(0);\n})().catch(e => { console.error(e.stack || e); process.exit(1); });\n"
    proc = subprocess.run([node_exe, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr or proc.stdout


def html_block(element_id: str) -> str:
    start = INDEX_HTML.index(f'id="{element_id}"')
    end = INDEX_HTML.index("<!-- Modal", start)
    return INDEX_HTML[start:end]


# ---------------------------------------------------------------------------
# 1. Add / Edit Item modal
# ---------------------------------------------------------------------------
def test_add_item_modal_html_collapses_optional_fields():
    block = html_block("add-item-modal")
    more_start = block.index('id="item-more-details"')
    upfront, more = block[:more_start], block[more_start:]

    for upfront_id in ("item-name", "item-location-pill-bar", "item-location-select", "item-quantity", "item-photo-input"):
        assert f'id="{upfront_id}"' in upfront, f"{upfront_id} should be visible upfront"
    for hidden_id in ("item-unit", "item-category", "item-serial", "item-tags", "item-description"):
        assert f'id="{hidden_id}"' in more, f"{hidden_id} should live behind + More details"
    assert "More details" in more


def test_add_item_modal_location_pill_and_auto_expand():
    run_node(f"""
    const stateMod = await import('file://{JS_DIR}/state.js');
    const modals = await import('file://{JS_DIR}/modals.js');
    stateMod.state.locationMap.set(7, {{ id: 7, name: 'Bin 7', full_path: 'Garage > Bin 7', location_type: 'bin' }});

    // New item with a context location: compact pill shown, picker hidden, details collapsed
    modals.openAddItemModal(7, null);
    assert(el('item-location-pill-bar').style.display !== 'none', 'pill should show when a location is preset');
    assert(el('item-location-pill-name').textContent.includes('Bin 7'), 'pill should name the location');
    assert(el('item-location-field').style.display === 'none', 'picker should be hidden behind [Change]');
    assert(el('item-more-details').open === false, 'more details collapsed for a new item');

    // [Change] reveals the picker
    modals.toggleItemLocationPicker();
    assert(el('item-location-field').style.display !== 'none', 'Change should reveal the location picker');
    assert(el('item-location-pill-bar').style.display === 'none', 'pill hides while picking');

    // New item without a location: picker shown directly
    modals.openAddItemModal(null, null);
    assert(el('item-location-field').style.display !== 'none', 'picker should show when no location is preset');
    assert(el('item-location-pill-bar').style.display === 'none', 'no pill without a location');

    // Edit with populated optional fields: auto-expand
    modals.openAddItemModal(7, {{ id: 3, name: 'Caliper', location_id: 7, quantity: 1, unit: 'pcs', category: 'Tools' }});
    assert(el('item-more-details').open === true, 'more details should auto-expand on edit when populated');

    // Edit without optional fields: stays collapsed
    modals.openAddItemModal(7, {{ id: 4, name: 'Tape', location_id: 7, quantity: 1, unit: 'pcs' }});
    assert(el('item-more-details').open === false, 'more details stays collapsed when nothing optional is set');
    """)


# ---------------------------------------------------------------------------
# 2. Add Space / Container / Drawer modal
# ---------------------------------------------------------------------------
def test_add_storage_modal_html_moves_optional_sections_behind_toggle():
    block = html_block("add-storage-modal")
    more_start = block.index('id="storage-more-details"')
    upfront, more = block[:more_start], block[more_start:]

    for upfront_id in ("storage-parent-pill-bar", "storage-name", "storage-bin-photo-field"):
        assert f'id="{upfront_id}"' in upfront, f"{upfront_id} should be upfront"
    for hidden_id in ("multi-drawer-box", "storage-desc", "storage-label-field", "storage-spot-photo-slot"):
        assert f'id="{hidden_id}"' in more, f"{hidden_id} should be behind the optional toggle"


def test_add_storage_modal_locks_type_and_uses_parent_pill_in_context():
    run_node(f"""
    const stateMod = await import('file://{JS_DIR}/state.js');
    const modals = await import('file://{JS_DIR}/modals.js');
    stateMod.state.locationMap.set(5, {{ id: 5, name: 'Tool Chest', full_path: 'Garage > Tool Chest', location_type: 'container' }});
    stateMod.state.locationMap.set(9, {{ id: 9, name: 'Garage', full_path: 'Garage', location_type: 'room' }});

    // Launched from a container: type switcher hidden, parent pill shows container
    modals.openAddStorageModal(5, 'drawer');
    el('storage-parent-select').value = '5';
    modals.syncStorageParentPill();
    assert(el('storage-type-segmented').style.display === 'none', 'type switcher must hide in context');
    assert(el('storage-parent-pill-bar').style.display !== 'none', 'parent pill should show in context');
    assert(el('storage-parent-pill-name').textContent.includes('Tool Chest'), 'pill names the parent');
    assert(el('storage-parent-field').style.display === 'none', 'parent dropdown hidden behind [Change]');
    assert(el('storage-more-details').open === false, 'optional section collapsed by default');

    modals.toggleStorageParentPicker();
    assert(el('storage-parent-field').style.display !== 'none', '[Change] reveals parent dropdown');

    // Launched without context: switcher visible, parent dropdown visible
    modals.openAddStorageModal(null, 'unit');
    el('storage-parent-select').value = '';
    modals.syncStorageParentPill();
    assert(el('storage-type-segmented').style.display !== 'none', 'switcher visible without context');
    assert(el('storage-parent-field').style.display !== 'none', 'dropdown visible without a parent');

    // Explicit lock option without a parent (e.g. "Add space" button)
    modals.openAddStorageModal(null, 'room', {{ lockType: true }});
    assert(el('storage-type-segmented').style.display === 'none', 'lockType hides the switcher');

    // Room photo is the single upfront photo; container keeps context photo behind toggle
    assert(el('storage-spot-photo-field').parentElement?.id === 'storage-primary-photo-slot', 'room photo must be upfront');
    modals.openAddStorageModal(9, 'unit');
    assert(el('storage-spot-photo-field').parentElement?.id === 'storage-spot-photo-slot', 'container context photo goes behind toggle');
    """)


# ---------------------------------------------------------------------------
# 3. Room & Storage Container view action bars
# ---------------------------------------------------------------------------
HEADER_SPLIT_JS = """
function headerParts(html) {
  const start = html.indexOf('class="context-actions"');
  assert(start !== -1, 'context actions bar missing');
  const menuAt = html.indexOf('unit-more-wrap', start);
  assert(menuAt !== -1, 'header must have a ⋯ overflow menu');
  const menuEnd = html.indexOf('unit-place-hero', menuAt) === -1 ? html.length : html.indexOf('unit-place-hero', menuAt);
  return { bar: html.slice(start, menuAt), menu: html.slice(menuAt, menuEnd) };
}
function countBtn(html, cls) { return (html.match(new RegExp(`class="btn ${cls}`, 'g')) || []).length; }
"""


def test_room_view_header_has_one_primary_one_secondary_and_overflow_menu():
    run_node(HEADER_SPLIT_JS + f"""
    const stateMod = await import('file://{JS_DIR}/state.js');
    const views = await import('file://{JS_DIR}/views.js');
    stateMod.state.locationMap.set(9, {{ id: 9, name: 'Office', full_path: 'Office', location_type: 'room', children: [], context_photo: '/uploads/originals/r.jpg' }});
    stateMod.state.items = [];
    views.renderRoomView(9);
    const html = el('app-workspace').innerHTML;
    const {{ bar, menu }} = headerParts(html);
    assert(countBtn(bar, 'btn-primary') === 1, 'room header needs exactly one primary CTA');
    assert(countBtn(bar, 'btn-secondary') === 1, 'room header needs exactly one secondary CTA');
    for (const label of ['Change space photo', 'Crop photo', 'Rename, edit, or move', 'Print QR label', 'Delete space']) {{
      assert(menu.includes(label), `room ⋯ menu missing "${{label}}"`);
    }}
    assert(!html.includes('New container'), 'duplicate "New container" section button should be gone');
    assert(!html.includes('Scan with Phone'), 'hero action row should be folded into the header');
    """)


def test_container_view_header_has_one_primary_one_secondary_and_overflow_menu():
    run_node(HEADER_SPLIT_JS + f"""
    const stateMod = await import('file://{JS_DIR}/state.js');
    const views = await import('file://{JS_DIR}/views.js');
    stateMod.state.locationMap.set(9, {{ id: 9, name: 'Office', full_path: 'Office', location_type: 'room', children: [] }});
    stateMod.state.locationMap.set(5, {{ id: 5, name: 'Tool chest', full_path: 'Office > Tool chest', location_type: 'container', parent_id: 9, children: [], container_photo: '/uploads/originals/c.jpg' }});
    stateMod.state.items = [];
    views.renderStorageUnitView(5);
    const html = el('app-workspace').innerHTML;
    const {{ bar, menu }} = headerParts(html);
    assert(countBtn(bar, 'btn-primary') === 1, 'container header needs exactly one primary CTA');
    assert(countBtn(bar, 'btn-secondary') === 1, 'container header needs exactly one secondary CTA');
    for (const label of ['Design drawer / shelf grid', 'Rename or edit', 'Change photo', 'Crop photo', 'Print QR label', 'Move to another space', 'Delete container']) {{
      assert(menu.includes(label), `container ⋯ menu missing "${{label}}"`);
    }}
    """)


# ---------------------------------------------------------------------------
# 6. Item detail page
# ---------------------------------------------------------------------------
def test_item_view_hides_empty_cards_behind_chips_and_collapses_history():
    run_node(HEADER_SPLIT_JS + f"""
    const stateMod = await import('file://{JS_DIR}/state.js');
    const views = await import('file://{JS_DIR}/views.js');
    stateMod.state.locationMap.set(7, {{ id: 7, name: 'Bin 7', full_path: 'Office > Bin 7', location_type: 'bin', children: [] }});
    stateMod.state.items = [{{ id: 31, name: 'Caliper', location_id: 7, quantity: 1, unit: 'pcs', description: null }}];
    views.renderItemView(31);
    const html = el('app-workspace').innerHTML;

    assert(!html.includes('Parts List / Assortment Contents'), 'empty parts card should collapse to a chip');
    assert(!html.includes('No notes or specs added yet'), 'empty notes card should collapse to a chip');
    for (const chip of ['Add parts list', 'Add notes', 'Attach file']) {{
      assert(html.includes(chip), `missing "+ ${{chip}}" chip`);
    }}
    assert(/id="item-attachments-card-31"[^>]*hidden/.test(html), 'attachments card hidden until files exist');
    assert(/<details[^>]*item-history-details/.test(html) && !/<details[^>]*item-history-details[^>]*open/.test(html), 'activity history collapsed by default');

    const {{ bar, menu }} = headerParts(html);
    assert(!bar.includes('QR label') && !bar.includes('btn-danger'), 'QR label and Delete move out of the header bar');
    assert(menu.includes('Print QR label') && menu.includes('Delete item'), 'QR label and Delete live in ⋯ menu');
    """)


def test_item_view_keeps_populated_notes_and_parts_cards():
    run_node(HEADER_SPLIT_JS + f"""
    const stateMod = await import('file://{JS_DIR}/state.js');
    const views = await import('file://{JS_DIR}/views.js');
    stateMod.state.locationMap.set(7, {{ id: 7, name: 'Bin 7', full_path: 'Office > Bin 7', location_type: 'bin', children: [] }});
    stateMod.state.items = [{{ id: 32, name: 'Resistor kit', location_id: 7, quantity: 1, unit: 'pcs', description: 'Kept dry' }}];
    views.renderItemView(32);
    const html = el('app-workspace').innerHTML;
    assert(html.includes('Kept dry'), 'populated notes must still render');
    assert(!html.includes('Add notes'), 'no Add notes chip when notes exist');
    """)


# ---------------------------------------------------------------------------
# 4. Grid designer toolbar
# ---------------------------------------------------------------------------
def test_grid_designer_toolbar_groups_templates_and_tools():
    run_node(f"""
    const gd = await import('file://{JS_DIR}/grid-designer.js');
    const html = gd.renderInlineGridDesignerHtml();
    const toolsAt = html.indexOf('id="gd-tools-details"');
    assert(toolsAt !== -1, 'Templates & tools toggle missing');
    const upfront = html.slice(0, toolsAt);
    const tools = html.slice(toolsAt);
    assert(upfront.includes('gd-rows-input') && upfront.includes('gd-cols-input'), 'rows × cols stay upfront');
    assert(upfront.includes('Save Grid'), 'Save Grid stays upfront');
    for (const hidden of ["applyGridPreset('3x3')", "applyGridPreset('dresser_3_2')", "applyGridPreset('organizer_split')", 'fillAllEmptyDesignerCells', 'autoNameAllDesignerSlots', 'gd-default-type']) {{
      assert(!upfront.includes(hidden), `${{hidden}} should be behind Templates & tools`);
      assert(tools.includes(hidden), `${{hidden}} missing from Templates & tools`);
    }}
    assert(!/<details[^>]*gd-tools-details[^>]*open/.test(html), 'tools collapsed by default');
    """)


# ---------------------------------------------------------------------------
# 5. Photo editor
# ---------------------------------------------------------------------------
def test_photo_editor_defaults_to_crop_rotate_and_collapses_adjustments():
    start = INDEX_HTML.index('id="photo-editor-modal"')
    block = INDEX_HTML[start:INDEX_HTML.index("<!-- Modal", start)]
    adjust_at = block.index('id="photo-editor-adjust-details"')
    upfront, adjust = block[:adjust_at], block[adjust_at:]

    assert "rotatePhotoEditor(90)" in upfront
    assert 'id="photo-editor-apply-btn"' in adjust  # footer comes after, still in modal
    assert "Adjust &amp; Perspective" in adjust
    for hidden_id in ("photo-editor-fine-rot", "photo-editor-scale-x", "photo-editor-scale-y", "photo-editor-mode-quad", "photo-editor-quad-controls"):
        assert f'id="{hidden_id}"' in adjust and f'id="{hidden_id}"' not in upfront, f"{hidden_id} belongs under Adjust & Perspective"
    assert not re.search(r'id="photo-editor-adjust-details"[^>]*\sopen', block)


# ---------------------------------------------------------------------------
# 7. Global + Add action sheet
# ---------------------------------------------------------------------------
def test_add_sheet_has_three_choices():
    start = INDEX_HTML.index('id="add-action-sheet"')
    block = INDEX_HTML[start:INDEX_HTML.index("<!-- Modal", start)]
    choices = re.findall(r"chooseAddAction\('(\w+)'\)", block)
    assert choices == ["snap", "item", "place"], choices
    assert "Scan with Camera" in block and "Add Item Manually" in block and "New Space or Container" in block


def test_add_sheet_preselects_context():
    run_node(f"""
    const stateMod = await import('file://{JS_DIR}/state.js');
    const modals = await import('file://{JS_DIR}/modals.js');
    stateMod.state.locationMap.set(9, {{ id: 9, name: 'Office', full_path: 'Office', location_type: 'room', children: [] }});
    stateMod.state.locationMap.set(5, {{ id: 5, name: 'Tool chest', full_path: 'Office > Tool chest', location_type: 'container', parent_id: 9, children: [] }});

    stateMod.state.currentView = 'unit';
    stateMod.state.currentLocationId = 5;
    modals.openAddSheet();
    assert(el('add-sheet-place-title').textContent.includes('Tool chest'), 'place card should name the open container');
    assert(el('add-sheet-item-desc').textContent.includes('Tool chest'), 'item card should name the open container');
    modals.chooseAddAction('place');
    assert(el('add-storage-modal').classList.contains('open'), 'container context opens the drawer form');
    assert(el('storage-type-segmented').style.display === 'none', 'type is preselected from context');
    assert(el('storage-parent-select').value === '5', 'parent preselected to the open container');

    el('add-storage-modal').classList.remove('open');
    stateMod.state.currentView = 'home';
    stateMod.state.currentLocationId = null;
    modals.openAddSheet();
    assert(el('add-sheet-place-title').textContent === 'New Space or Container', 'generic label without context');
    modals.chooseAddAction('place');
    assert(el('storage-type-segmented').style.display !== 'none', 'without context the user can pick a type');
    """)
