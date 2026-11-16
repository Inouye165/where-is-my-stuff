/**
 * Container Grid Renderer & Interactive Grid Designer
 * Supports numeric rows (1, 2, 3...), alphabetic columns (A, B, C...),
 * variable drawer spans (merge/combine, resize, split), and section dividers.
 */
import { apiPut } from './api.js';
import { renderSlotCarouselHtml, scrollSlotCarousel, syncAllSlotCarousels, syncSlotCarousel } from './carousel.js';
import { escapeHtml, escapeJsStr, sanitizePhotoUrl, showToast, withSubmitLock } from './dom.js';
import { lucideIcon } from './icons.js';
import { getSubtreeItems, isUnknownPlaceholder, loadAllData, state } from './state.js';
import { guardNavigation, registerGridUnsavedHooks } from './unsaved-guard.js';

export { renderSlotCarouselHtml, scrollSlotCarousel, syncAllSlotCarousels, syncSlotCarousel };

function getLocationOwnPhotos(loc) {
  const list = [];
  const seen = new Set();
  const rawPhotos = Array.isArray(loc?.photos) ? loc.photos : [];
  const primarySafe = sanitizePhotoUrl(loc?.container_photo || '');

  if (primarySafe) {
    const matched = rawPhotos.find(p => sanitizePhotoUrl(p.photo_path || '') === primarySafe);
    seen.add(primarySafe);
    list.push({
      id: matched ? matched.id : null,
      url: primarySafe,
      thumb: matched ? (sanitizePhotoUrl(matched.thumbnail_path || '') || primarySafe) : primarySafe,
      label: (matched && matched.caption) || loc?.name || 'Photo',
    });
  }

  for (const p of rawPhotos) {
    const url = sanitizePhotoUrl(p.photo_path || '');
    if (url && !seen.has(url)) {
      seen.add(url);
      list.push({
        id: p.id,
        url,
        thumb: sanitizePhotoUrl(p.thumbnail_path || '') || url,
        label: p.caption || loc?.name || 'Photo',
      });
    }
  }

  const contextSafe = sanitizePhotoUrl(loc?.context_photo || '');
  if (contextSafe && !seen.has(contextSafe)) {
    seen.add(contextSafe);
    list.push({
      id: null,
      url: contextSafe,
      thumb: contextSafe,
      label: loc?.name || 'Photo',
    });
  }
  return list;
}

export function getSlotPhotos(drawer) {
  if (!drawer) return [];
  const list = [];
  const seen = new Set();
  const pushEntry = (entry) => {
    if (!entry || !entry.url || seen.has(entry.url)) return;
    seen.add(entry.url);
    list.push(entry);
  };

  const collectContainers = (node) => {
    const children = (node?.children || []).filter(c => !isUnknownPlaceholder(c));
    for (const child of children) {
      const childPhotos = getLocationOwnPhotos(child);
      if (childPhotos.length > 0) {
        const cover = childPhotos[0];
        pushEntry({
          url: cover.url,
          thumb: cover.thumb,
          label: child.name || cover.label,
          navType: 'drawer',
          navId: Number(child.id),
        });
      }
      collectContainers(child);
    }
  };
  collectContainers(drawer);

  const directItems = (state.items || []).filter(
    item => Number(item.location_id) === Number(drawer.id)
  );
  for (const it of directItems) {
    const itemImg = sanitizePhotoUrl(it.item_photo || '');
    if (itemImg && !itemImg.includes('_crop_')) {
      pushEntry({
        url: itemImg,
        thumb: itemImg,
        label: it.name || drawer.name || 'Item',
        navType: 'item',
        navId: Number(it.id),
      });
    }
  }

  for (const own of getLocationOwnPhotos(drawer)) {
    pushEntry({
      ...own,
      navType: 'drawer',
      navId: Number(drawer.id),
    });
  }

  return list;
}

export function colToLetter(colIndex) {
  if (!colIndex || colIndex < 1 || colIndex > 26) return String(colIndex || '');
  return String.fromCharCode(64 + colIndex);
}

export function formatSlotCoord(row, col, rowSpan = 1, colSpan = 1) {
  const r = Number(row) || 1;
  const c = Number(col) || 1;
  const rs = Number(rowSpan) || 1;
  const cs = Number(colSpan) || 1;
  const start = `${colToLetter(c)}${r}`;
  if (rs === 1 && cs === 1) return start;
  const end = `${colToLetter(c + cs - 1)}${r + rs - 1}`;
  return `${start}–${end}`;
}

export function parseGridConfig(rawConfig) {
  if (!rawConfig) return { section_breaks: [] };
  try {
    const parsed = typeof rawConfig === 'string' ? JSON.parse(rawConfig) : rawConfig;
    const breaks = Array.isArray(parsed?.section_breaks)
      ? parsed.section_breaks.map(n => Number(n)).filter(n => Number.isInteger(n) && n >= 1)
      : [];
    return { section_breaks: Array.from(new Set(breaks)).sort((a, b) => a - b) };
  } catch {
    return { section_breaks: [] };
  }
}

/**
 * Computes CSS grid row start/end accounting for section divider tracks.
 * Header is CSS row 1. Data row `r` is preceded by `breaksBefore(r)` divider tracks.
 */
export function cssRowForDataRow(r, sectionBreaks) {
  let breaksBefore = 0;
  for (const b of sectionBreaks) {
    if (b < r) breaksBefore += 1;
  }
  return 1 + r + breaksBefore;
}

/**
 * Shared read-only / navigable grid renderer used on:
 *  - Container view (renderStorageUnitView)
 *  - Item "Where it lives" page (renderItemDetailView)
 *  - Item slide-over drawer (openItemDrawer)
 */
export function renderContainerGridHtml(unit, options = {}) {
  const {
    highlightDrawerId = null,
    compact = false,
    diagram = false,
  } = options;

  const rows = Number(unit?.grid_rows) || 0;
  const cols = Number(unit?.grid_cols) || 0;
  if (!rows || !cols) return '';

  const cfg = parseGridConfig(unit.grid_config);
  const sectionBreaks = cfg.section_breaks.filter(b => b >= 1 && b < rows);
  const drawers = unit.children || [];
  const placed = drawers.filter(d => Number(d.grid_row) >= 1 && Number(d.grid_col) >= 1);

  // Build occupied cell map to render subtle empty-slot placeholders
  const occupiedMap = new Map();
  for (const d of placed) {
    const r0 = Number(d.grid_row);
    const c0 = Number(d.grid_col);
    const rs = Math.max(1, Number(d.grid_row_span) || 1);
    const cs = Math.max(1, Number(d.grid_col_span) || 1);
    for (let r = r0; r < r0 + rs; r++) {
      for (let c = c0; c < c0 + cs; c++) {
        occupiedMap.set(`${r},${c}`, d.id);
      }
    }
  }

  // Build explicit grid-template-rows
  const isSingleCol = cols === 1 && !compact && !diagram;
  const rowTracks = ['auto']; // header row
  for (let r = 1; r <= rows; r++) {
    rowTracks.push(
      diagram ? 'minmax(14px, 1fr)' : compact ? 'minmax(32px, auto)' : isSingleCol ? 'auto' : 'minmax(64px, auto)'
    );
    if (sectionBreaks.includes(r)) {
      rowTracks.push(diagram ? '4px' : compact ? '8px' : '12px');
    }
  }

  const colHeaderHtml = Array.from({ length: cols }, (_, idx) => {
    const c = idx + 1;
    return `<div class="cgrid-col-hdr" style="grid-row: 1; grid-column: ${c + 1};">${escapeHtml(colToLetter(c))}</div>`;
  }).join('');

  let rowHdrHtml = '';
  let dividerHtml = '';
  for (let r = 1; r <= rows; r++) {
    const cssR = cssRowForDataRow(r, sectionBreaks);
    rowHdrHtml += `<div class="cgrid-row-hdr" style="grid-row: ${cssR}; grid-column: 1;">${r}</div>`;
    if (sectionBreaks.includes(r)) {
      dividerHtml += `
        <div class="cgrid-section-divider" style="grid-row: ${cssR + 1}; grid-column: 1 / ${cols + 2};" title="Section divider">
          <span></span>
        </div>
      `;
    }
  }

  let emptyCellsHtml = '';
  for (let r = 1; r <= rows; r++) {
    const cssR = cssRowForDataRow(r, sectionBreaks);
    for (let c = 1; c <= cols; c++) {
      if (!occupiedMap.has(`${r},${c}`)) {
        emptyCellsHtml += `<div class="cgrid-empty-slot" style="grid-row: ${cssR}; grid-column: ${c + 1};"></div>`;
      }
    }
  }

  const slotsHtml = placed.map(d => {
    const r0 = Number(d.grid_row);
    const c0 = Number(d.grid_col);
    const rs = Math.max(1, Number(d.grid_row_span) || 1);
    const cs = Math.max(1, Number(d.grid_col_span) || 1);
    const startCssR = cssRowForDataRow(r0, sectionBreaks);
    const endCssR = cssRowForDataRow(r0 + rs - 1, sectionBreaks) + 1;
    const startCssC = c0 + 1;
    const endCssC = c0 + cs + 1;

    const isHighlighted = highlightDrawerId !== null && Number(d.id) === Number(highlightDrawerId);
    const coord = formatSlotCoord(r0, c0, rs, cs);
    const itemCount = getSubtreeItems(d.id).length || Number(d.total_item_count || 0);
    const safeId = Number(d.id);
    const onClickAttr = diagram
      ? `locateDrawerInUnit(${Number(unit.id)}, ${safeId})`
      : `navToDrawer(${safeId}, true)`;

    if (diagram) {
      return `
        <button
          type="button"
          class="cgrid-slot is-diagram ${isHighlighted ? 'is-highlighted' : ''}"
          style="grid-row: ${startCssR} / ${endCssR}; grid-column: ${startCssC} / ${endCssC};"
          onclick="${onClickAttr}"
          title="${escapeHtml(d.name)} (${escapeHtml(coord)})"
          aria-label="${escapeHtml(d.name)} (${escapeHtml(coord)})${isHighlighted ? ' — item is here' : ''}"
        >
          ${isHighlighted ? `<span class="cgrid-diagram-coord">${escapeHtml(coord)}</span>` : ''}
        </button>
      `;
    }

    const slotPhotos = (!compact && !diagram) ? getSlotPhotos(d) : [];
    const childBoxCount = (d.children || []).filter(c => !isUnknownPlaceholder(c)).length;
    const isEmptySlot = !compact && !diagram && itemCount === 0 && slotPhotos.length === 0 && childBoxCount === 0;
    const slotCarouselHtml = renderSlotCarouselHtml(slotPhotos);

    let singleColBadge = '';
    if (isSingleCol && !isHighlighted) {
      const parts = [];
      if (childBoxCount > 0) parts.push(`${childBoxCount} ${childBoxCount === 1 ? 'box' : 'boxes'}`);
      if (itemCount > 0) parts.push(`${itemCount} ${itemCount === 1 ? 'item' : 'items'}`);
      singleColBadge = parts.length > 0
        ? `<span class="cgrid-slot-summary">${escapeHtml(parts.join(' · '))}</span>`
        : `<span class="cgrid-slot-summary is-empty">Empty</span>`;
    }

    return `
      <div
        role="button"
        tabindex="0"
        id="${compact ? '' : `drawer-card-${safeId}`}"
        class="cgrid-slot ${isHighlighted ? 'is-highlighted spotlight-highlight' : ''} ${compact ? 'is-compact' : ''} ${isEmptySlot ? 'is-empty' : ''}"
        style="grid-row: ${startCssR} / ${endCssR}; grid-column: ${startCssC} / ${endCssC};"
        onclick="${onClickAttr}"
        onkeydown="if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); ${onClickAttr}; }"
        title="${escapeHtml(d.name)} (${escapeHtml(coord)})"
      >
        <div class="cgrid-slot-body">
          <div class="cgrid-slot-info">
            <span class="cgrid-slot-coord">${escapeHtml(coord)}</span>
            <span class="cgrid-slot-name">${escapeHtml(d.name)}</span>
            ${isHighlighted ? `<span class="cgrid-here-badge">${compact ? 'Here' : 'Inside here'}</span>` : ''}
          </div>
          ${slotCarouselHtml}
          ${singleColBadge}
        </div>
        ${!isSingleCol && !compact && !isHighlighted && itemCount > 0 ? `<span class="cgrid-slot-count">${itemCount}</span>` : ''}
      </div>
    `;
  }).join('');

  if (!compact && !diagram && typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(syncAllSlotCarousels);
  }

  return `
    <div class="cgrid-wrap ${compact ? 'is-compact' : ''} ${diagram ? 'is-diagram' : ''} ${isSingleCol ? 'is-single-col' : ''}">
      <div
        class="cgrid-board"
        style="grid-template-columns: ${diagram ? '16px' : compact ? '24px' : '30px'} repeat(${cols}, ${diagram ? 'minmax(12px, 1fr)' : `minmax(${compact ? '28px' : '54px'}, 1fr)`}); grid-template-rows: ${rowTracks.join(' ')};"
      >
        <div class="cgrid-corner" style="grid-row: 1; grid-column: 1;"></div>
        ${colHeaderHtml}
        ${rowHdrHtml}
        ${dividerHtml}
        ${emptyCellsHtml}
        ${slotsHtml}
      </div>
    </div>
  `;
}

// =========================================================================
// Interactive Grid Designer Modal State & Logic
// =========================================================================

const designer = {
  unitId: null,
  isInline: false,
  rows: 3,
  cols: 6,
  defaultType: 'drawer',
  sectionBreaks: [],
  slots: [], // { tempId, id, name, location_type, grid_row, grid_col, grid_row_span, grid_col_span, merge_from_ids }
  selectedKeys: new Set(), // Set of tempIds (or `empty:r,c` for empty cells)
  multiSelectMode: false,
  nextTempId: 1,
  draggedTempId: null,
  initialSnapshot: '',
};

const SLOT_TYPE_LABELS = {
  drawer: 'Drawer',
  bin: 'Bin',
  shelf: 'Shelf',
  compartment: 'Compartment',
  cubby: 'Cubby',
};

function formatAutoSlotName(type, row, col) {
  const label = SLOT_TYPE_LABELS[type] || 'Drawer';
  const index = (Math.max(1, Number(row) || 1) - 1) * Math.max(1, Number(designer.cols) || 1) + Math.max(1, Number(col) || 1);
  return `${label} ${index}`;
}

function serializeDesignerState() {
  return JSON.stringify({
    rows: designer.rows,
    cols: designer.cols,
    sectionBreaks: designer.sectionBreaks,
    slots: designer.slots
      .map(s => ({
        id: s.id || null,
        name: s.name || '',
        location_type: s.location_type || 'drawer',
        grid_row: s.grid_row,
        grid_col: s.grid_col,
        grid_row_span: s.grid_row_span,
        grid_col_span: s.grid_col_span,
      }))
      .sort((a, b) => (a.grid_row - b.grid_row) || (a.grid_col - b.grid_col)),
  });
}

export function isGridDesignerDirty() {
  if (!designer.unitId || !designer.isInline) return false;
  return serializeDesignerState() !== designer.initialSnapshot;
}

export function discardGridDesigner() {
  designer.isInline = false;
  designer.unitId = null;
  designer.initialSnapshot = '';
  designer.selectedKeys.clear();
}

export function isInlineGridEditing(unitId) {
  return designer.isInline && Number(designer.unitId) === Number(unitId);
}

export function cancelInlineGridDesigner() {
  guardNavigation(() => {
    discardGridDesigner();
    window.renderCurrentWorkspace?.();
  });
}

export function openInlineGridDesigner(unitId, initialRows = null, initialCols = null) {
  initDesignerState(unitId, initialRows, initialCols);
  designer.isInline = true;
  window.renderCurrentWorkspace?.();
}

export function renderInlineGridDesignerHtml() {
  return `
    <div class="gd-inline-shell">
      <div class="gd-inline-header">
        <div>
          <h3 class="gd-inline-title">Design Drawer &amp; Shelf Grid</h3>
          <p class="gd-inline-sub">Rows are numbered (1, 2, 3…), columns are lettered (A, B, C…). Click a drawer to rename or resize, or select multiple to combine.</p>
        </div>
        <div class="gd-inline-header-actions">
          <button type="button" class="btn btn-secondary btn-sm" onclick="cancelInlineGridDesigner()">Cancel</button>
          <button type="button" class="btn btn-primary btn-sm" id="gd-save-btn" onclick="saveGridDesigner()">Save Grid</button>
        </div>
      </div>

      <div class="gd-toolbar">
        <div class="gd-dims-group">
          <label class="gd-dim-field">
            <span>Rows</span>
            <input type="number" id="gd-rows-input" min="1" max="26" value="${designer.rows}" onchange="updateGridDesignerDimensions()">
          </label>
          <label class="gd-dim-field">
            <span>Cols</span>
            <input type="number" id="gd-cols-input" min="1" max="26" value="${designer.cols}" onchange="updateGridDesignerDimensions()">
          </label>
        </div>

        <div class="gd-presets-group">
          <button type="button" class="btn btn-secondary btn-sm" onclick="applyGridPreset('3x3')">3×3</button>
          <button type="button" class="btn btn-secondary btn-sm" onclick="applyGridPreset('4x4')">4×4</button>
          <button type="button" class="btn btn-secondary btn-sm" onclick="applyGridPreset('dresser_3_2')">Dresser (3 + 2×2)</button>
          <button type="button" class="btn btn-secondary btn-sm" onclick="applyGridPreset('organizer_split')">Parts Organizer</button>
        </div>

        <div class="gd-mode-group">
          <select id="gd-default-type" class="gd-type-select" onchange="setDesignerDefaultType(this.value)" aria-label="Default slot type" title="Type for new &amp; auto-named slots">
            ${Object.entries(SLOT_TYPE_LABELS).map(([val, label]) => `
              <option value="${val}" ${designer.defaultType === val ? 'selected' : ''}>${label}</option>
            `).join('')}
          </select>
          <button type="button" id="gd-multi-toggle" class="btn btn-secondary btn-sm ${designer.multiSelectMode ? 'active' : ''}" aria-pressed="${designer.multiSelectMode ? 'true' : 'false'}" onclick="toggleDesignerMultiSelect()">
            Select multiple
          </button>
          <button type="button" class="btn btn-secondary btn-sm" onclick="fillAllEmptyDesignerCells()">Fill empty</button>
          <button type="button" class="btn btn-secondary btn-sm" onclick="autoNameAllDesignerSlots()">Auto-name</button>
        </div>
      </div>

      <div id="gd-inspector" class="gd-inspector"></div>
      <div id="gd-canvas-wrap" class="gd-canvas-wrap"></div>
    </div>
  `;
}

export function mountInlineGridDesigner() {
  syncDesignerControls();
  renderDesignerCanvas();
}

function makeTempId() {
  const id = `slot-${designer.nextTempId}`;
  designer.nextTempId += 1;
  return id;
}

function initDesignerState(unitId, initialRows = null, initialCols = null) {
  const unit = state.locationMap.get(Number(unitId));
  if (!unit) {
    showToast('Container not found.', 'error');
    return false;
  }

  designer.unitId = Number(unit.id);
  designer.selectedKeys.clear();
  designer.multiSelectMode = false;

  const hasExistingGrid = Number(unit.grid_rows) > 0 && Number(unit.grid_cols) > 0;
  designer.rows = Number(initialRows) || (hasExistingGrid ? Number(unit.grid_rows) : 3);
  designer.cols = Number(initialCols) || (hasExistingGrid ? Number(unit.grid_cols) : 4);
  const cfg = parseGridConfig(unit.grid_config);
  designer.sectionBreaks = cfg.section_breaks.filter(b => b >= 1 && b < designer.rows);

  const children = unit.children || [];
  const placed = children.filter(c => Number(c.grid_row) >= 1 && Number(c.grid_col) >= 1);

  if (placed.length > 0) {
    designer.slots = placed.map(c => ({
      tempId: makeTempId(),
      id: c.id,
      name: c.name,
      location_type: c.location_type || 'drawer',
      grid_row: Number(c.grid_row),
      grid_col: Number(c.grid_col),
      grid_row_span: Math.max(1, Number(c.grid_row_span) || 1),
      grid_col_span: Math.max(1, Number(c.grid_col_span) || 1),
      merge_from_ids: [],
    }));
  } else if (children.length > 0) {
    const neededRows = Math.max(designer.rows, Math.ceil(children.length / designer.cols));
    designer.rows = Math.min(26, neededRows);
    designer.slots = children.map((c, idx) => {
      const r = Math.floor(idx / designer.cols) + 1;
      const col = (idx % designer.cols) + 1;
      return {
        tempId: makeTempId(),
        id: c.id,
        name: c.name,
        location_type: c.location_type || 'drawer',
        grid_row: r,
        grid_col: col,
        grid_row_span: 1,
        grid_col_span: 1,
        merge_from_ids: [],
      };
    });
  } else {
    designer.slots = [];
    fillEmptyCellsInDesigner();
  }
  designer.initialSnapshot = serializeDesignerState();
  return true;
}

function fillEmptyCellsInDesigner() {
  const occ = buildDesignerOccupancy();
  for (let r = 1; r <= designer.rows; r++) {
    for (let c = 1; c <= designer.cols; c++) {
      if (!occ.has(`${r},${c}`)) {
        designer.slots.push({
          tempId: makeTempId(),
          id: null,
          name: formatAutoSlotName(designer.defaultType, r, c),
          location_type: designer.defaultType,
          grid_row: r,
          grid_col: c,
          grid_row_span: 1,
          grid_col_span: 1,
          merge_from_ids: [],
        });
      }
    }
  }
}

function buildDesignerOccupancy() {
  const occ = new Map();
  for (const s of designer.slots) {
    for (let r = s.grid_row; r < s.grid_row + s.grid_row_span; r++) {
      for (let c = s.grid_col; c < s.grid_col + s.grid_col_span; c++) {
        occ.set(`${r},${c}`, s);
      }
    }
  }
  return occ;
}

function syncDesignerControls() {
  const rowsInput = document.getElementById('gd-rows-input');
  const colsInput = document.getElementById('gd-cols-input');
  const multiBtn = document.getElementById('gd-multi-toggle');
  if (rowsInput) rowsInput.value = String(designer.rows);
  if (colsInput) colsInput.value = String(designer.cols);
  if (multiBtn) {
    multiBtn.classList.toggle('active', designer.multiSelectMode);
    multiBtn.setAttribute('aria-pressed', designer.multiSelectMode ? 'true' : 'false');
  }
}

export function updateGridDesignerDimensions() {
  const rowsVal = Math.max(1, Math.min(26, parseInt(document.getElementById('gd-rows-input')?.value, 10) || 1));
  const colsVal = Math.max(1, Math.min(26, parseInt(document.getElementById('gd-cols-input')?.value, 10) || 1));
  designer.rows = rowsVal;
  designer.cols = colsVal;
  designer.sectionBreaks = designer.sectionBreaks.filter(b => b >= 1 && b < designer.rows);

  // Clamp or remove slots that fall completely outside new bounds
  designer.slots = designer.slots
    .filter(s => s.grid_row <= designer.rows && s.grid_col <= designer.cols)
    .map(s => ({
      ...s,
      grid_row_span: Math.min(s.grid_row_span, designer.rows - s.grid_row + 1),
      grid_col_span: Math.min(s.grid_col_span, designer.cols - s.grid_col + 1),
    }));

  designer.selectedKeys.clear();
  renderDesignerCanvas();
}

export function applyGridPreset(presetName) {
  const existingQueue = designer.slots.filter(s => s.id !== null);
  const takeExisting = (fallbackName) => {
    const prev = existingQueue.shift();
    return prev
      ? { id: prev.id, name: prev.name, location_type: prev.location_type, merge_from_ids: prev.merge_from_ids || [] }
      : { id: null, name: fallbackName, location_type: 'drawer', merge_from_ids: [] };
  };

  designer.slots = [];
  designer.sectionBreaks = [];
  designer.selectedKeys.clear();

  if (presetName === 'dresser_3_2') {
    designer.rows = 3;
    designer.cols = 6;
    const layout = [
      [1, 1, 1, 2, 'Top Left'], [1, 3, 1, 2, 'Top Center'], [1, 5, 1, 2, 'Top Right'],
      [2, 1, 1, 3, 'Middle Left'], [2, 4, 1, 3, 'Middle Right'],
      [3, 1, 1, 3, 'Bottom Left'], [3, 4, 1, 3, 'Bottom Right'],
    ];
    for (const [r, c, rs, cs, label] of layout) {
      const base = takeExisting(label);
      designer.slots.push({
        tempId: makeTempId(),
        ...base,
        name: label,
        grid_row: r,
        grid_col: c,
        grid_row_span: rs,
        grid_col_span: cs,
      });
    }
  } else if (presetName === 'organizer_split') {
    designer.rows = 5;
    designer.cols = 6;
    designer.sectionBreaks = [3];
    // Upper section: rows 1-2 = 6 small drawers each, row 3 = 3 medium drawers
    for (let r = 1; r <= 2; r++) {
      for (let c = 1; c <= 6; c++) {
        const label = formatSlotCoord(r, c, 1, 1);
        const base = takeExisting(label);
        designer.slots.push({
          tempId: makeTempId(),
          ...base,
          grid_row: r,
          grid_col: c,
          grid_row_span: 1,
          grid_col_span: 1,
        });
      }
    }
    for (let c = 1; c <= 5; c += 2) {
      const label = formatSlotCoord(3, c, 1, 2);
      const base = takeExisting(label);
      designer.slots.push({
        tempId: makeTempId(),
        ...base,
        grid_row: 3,
        grid_col: c,
        grid_row_span: 1,
        grid_col_span: 2,
      });
    }
    // Lower section: rows 4-5 = 2 large drawers (3 cols x 2 rows each)
    for (const c of [1, 4]) {
      const label = formatSlotCoord(4, c, 2, 3);
      const base = takeExisting(label);
      designer.slots.push({
        tempId: makeTempId(),
        ...base,
        grid_row: 4,
        grid_col: c,
        grid_row_span: 2,
        grid_col_span: 3,
      });
    }
  } else {
    // 3x3 or 4x4 uniform
    const size = presetName === '4x4' ? 4 : 3;
    designer.rows = size;
    designer.cols = size;
    for (let r = 1; r <= size; r++) {
      for (let c = 1; c <= size; c++) {
        const label = formatSlotCoord(r, c, 1, 1);
        const base = takeExisting(label);
        designer.slots.push({
          tempId: makeTempId(),
          ...base,
          grid_row: r,
          grid_col: c,
          grid_row_span: 1,
          grid_col_span: 1,
        });
      }
    }
  }

  syncDesignerControls();
  renderDesignerCanvas();
}

export function toggleDesignerMultiSelect() {
  designer.multiSelectMode = !designer.multiSelectMode;
  syncDesignerControls();
}

export function toggleRowSectionBreak(rowNum) {
  const r = Number(rowNum);
  if (r < 1 || r >= designer.rows) return;
  if (designer.sectionBreaks.includes(r)) {
    designer.sectionBreaks = designer.sectionBreaks.filter(b => b !== r);
  } else {
    designer.sectionBreaks = [...designer.sectionBreaks, r].sort((a, b) => a - b);
  }
  renderDesignerCanvas();
}

export function handleDesignerCellClick(event, key) {
  const isMulti = designer.multiSelectMode || Boolean(event && (event.shiftKey || event.ctrlKey || event.metaKey));
  if (isMulti) {
    if (designer.selectedKeys.has(key)) {
      designer.selectedKeys.delete(key);
    } else {
      designer.selectedKeys.add(key);
    }
  } else {
    if (designer.selectedKeys.size === 1 && designer.selectedKeys.has(key)) {
      designer.selectedKeys.clear();
    } else {
      designer.selectedKeys.clear();
      designer.selectedKeys.add(key);
    }
  }
  renderDesignerCanvas();
}

export function setDesignerDefaultType(nextType) {
  designer.defaultType = SLOT_TYPE_LABELS[nextType] ? nextType : 'drawer';
}

export function autoNameAllDesignerSlots() {
  const label = SLOT_TYPE_LABELS[designer.defaultType] || 'Drawer';
  const sorted = [...designer.slots].sort((a, b) => (a.grid_row - b.grid_row) || (a.grid_col - b.grid_col));
  sorted.forEach((slot, idx) => {
    slot.location_type = designer.defaultType;
    slot.name = `${label} ${idx + 1}`;
  });
  renderDesignerCanvas();
}

export function fillAllEmptyDesignerCells() {
  fillEmptyCellsInDesigner();
  designer.selectedKeys.clear();
  renderDesignerCanvas();
}

export function addDrawerAtEmptyCell(r, c) {
  const newSlot = {
    tempId: makeTempId(),
    id: null,
    name: formatAutoSlotName(designer.defaultType, r, c),
    location_type: designer.defaultType,
    grid_row: r,
    grid_col: c,
    grid_row_span: 1,
    grid_col_span: 1,
    merge_from_ids: [],
  };
  designer.slots.push(newSlot);
  designer.selectedKeys.clear();
  designer.selectedKeys.add(newSlot.tempId);
  renderDesignerCanvas();
}

export function combineSelectedDesignerCells() {
  if (designer.selectedKeys.size < 2) {
    showToast('Select 2 or more adjacent drawers or cells to combine.', 'info');
    return;
  }

  const selectedSlots = [];
  let minR = Infinity;
  let maxR = -Infinity;
  let minC = Infinity;
  let maxC = -Infinity;

  for (const key of designer.selectedKeys) {
    if (key.startsWith('empty:')) {
      const [r, c] = key.slice(6).split(',').map(Number);
      minR = Math.min(minR, r);
      maxR = Math.max(maxR, r);
      minC = Math.min(minC, c);
      maxC = Math.max(maxC, c);
    } else {
      const slot = designer.slots.find(s => s.tempId === key);
      if (slot) {
        selectedSlots.push(slot);
        minR = Math.min(minR, slot.grid_row);
        maxR = Math.max(maxR, slot.grid_row + slot.grid_row_span - 1);
        minC = Math.min(minC, slot.grid_col);
        maxC = Math.max(maxC, slot.grid_col + slot.grid_col_span - 1);
      }
    }
  }

  // Ensure no un-selected slot overlaps the bounding rectangle
  const selectedSlotIds = new Set(selectedSlots.map(s => s.tempId));
  for (const s of designer.slots) {
    if (selectedSlotIds.has(s.tempId)) continue;
    const sMaxR = s.grid_row + s.grid_row_span - 1;
    const sMaxC = s.grid_col + s.grid_col_span - 1;
    const overlaps = !(sMaxR < minR || s.grid_row > maxR || sMaxC < minC || s.grid_col > maxC);
    if (overlaps) {
      showToast('Selected area must form a clean rectangle without clipping other drawers.', 'error');
      return;
    }
  }

  const rowSpan = maxR - minR + 1;
  const colSpan = maxC - minC + 1;
  const primary = selectedSlots.find(s => s.id !== null) || selectedSlots[0] || null;
  const absorbedIds = [];
  for (const s of selectedSlots) {
    if (s !== primary) {
      if (s.id !== null) absorbedIds.push(s.id);
      if (Array.isArray(s.merge_from_ids)) absorbedIds.push(...s.merge_from_ids);
    }
  }

  // Remove all selected slots, add combined slot
  designer.slots = designer.slots.filter(s => !selectedSlotIds.has(s.tempId));
  const coordLabel = formatSlotCoord(minR, minC, rowSpan, colSpan);
  const combined = {
    tempId: makeTempId(),
    id: primary ? primary.id : null,
    name: primary && primary.name && !/^[A-Z]\d+(–[A-Z]\d+)?$/.test(primary.name) ? primary.name : coordLabel,
    location_type: primary ? primary.location_type : 'drawer',
    grid_row: minR,
    grid_col: minC,
    grid_row_span: rowSpan,
    grid_col_span: colSpan,
    merge_from_ids: [...(primary?.merge_from_ids || []), ...absorbedIds],
  };

  designer.slots.push(combined);
  designer.selectedKeys.clear();
  designer.selectedKeys.add(combined.tempId);
  designer.multiSelectMode = false;
  syncDesignerControls();
  renderDesignerCanvas();
}

export function resizeSelectedDesignerSlot(axis, delta) {
  if (designer.selectedKeys.size !== 1) return;
  const [key] = Array.from(designer.selectedKeys);
  const slot = designer.slots.find(s => s.tempId === key);
  if (!slot) return;

  const nextRowSpan = axis === 'row' ? slot.grid_row_span + delta : slot.grid_row_span;
  const nextColSpan = axis === 'col' ? slot.grid_col_span + delta : slot.grid_col_span;

  if (nextRowSpan < 1 || nextColSpan < 1) return;
  if (slot.grid_row + nextRowSpan - 1 > designer.rows || slot.grid_col + nextColSpan - 1 > designer.cols) {
    showToast('Cannot expand past the grid edge. Increase rows or columns first.', 'info');
    return;
  }

  // Check if expanding hits another drawer
  const maxR = slot.grid_row + nextRowSpan - 1;
  const maxC = slot.grid_col + nextColSpan - 1;
  for (const other of designer.slots) {
    if (other.tempId === slot.tempId) continue;
    const oMaxR = other.grid_row + other.grid_row_span - 1;
    const oMaxC = other.grid_col + other.grid_col_span - 1;
    const overlaps = !(oMaxR < slot.grid_row || other.grid_row > maxR || oMaxC < slot.grid_col || other.grid_col > maxC);
    if (overlaps) {
      showToast('Remove or combine with the adjacent drawer first.', 'info');
      return;
    }
  }

  slot.grid_row_span = nextRowSpan;
  slot.grid_col_span = nextColSpan;
  if (/^[A-Z]\d+(–[A-Z]\d+)?$/.test(slot.name)) {
    slot.name = formatSlotCoord(slot.grid_row, slot.grid_col, slot.grid_row_span, slot.grid_col_span);
  }
  renderDesignerCanvas();
}

export function splitSelectedDesignerSlot() {
  if (designer.selectedKeys.size !== 1) return;
  const [key] = Array.from(designer.selectedKeys);
  const slot = designer.slots.find(s => s.tempId === key);
  if (!slot || (slot.grid_row_span <= 1 && slot.grid_col_span <= 1)) return;

  designer.slots = designer.slots.filter(s => s.tempId !== slot.tempId);
  let first = true;
  for (let r = slot.grid_row; r < slot.grid_row + slot.grid_row_span; r++) {
    for (let c = slot.grid_col; c < slot.grid_col + slot.grid_col_span; c++) {
      const child = {
        tempId: makeTempId(),
        id: first ? slot.id : null,
        name: first && slot.name && !/^[A-Z]\d+(–[A-Z]\d+)?$/.test(slot.name)
          ? slot.name
          : formatSlotCoord(r, c, 1, 1),
        location_type: slot.location_type,
        grid_row: r,
        grid_col: c,
        grid_row_span: 1,
        grid_col_span: 1,
        merge_from_ids: first ? (slot.merge_from_ids || []) : [],
      };
      designer.slots.push(child);
      if (first) {
        designer.selectedKeys.clear();
        designer.selectedKeys.add(child.tempId);
      }
      first = false;
    }
  }
  renderDesignerCanvas();
}

export function removeSelectedDesignerSlot() {
  if (designer.selectedKeys.size === 0) return;
  const toDelete = new Set(designer.selectedKeys);
  designer.slots = designer.slots.filter(s => !toDelete.has(s.tempId));
  designer.selectedKeys.clear();
  renderDesignerCanvas();
}

export function updateSelectedDesignerSlotField(field, value) {
  if (designer.selectedKeys.size !== 1) return;
  const [key] = Array.from(designer.selectedKeys);
  const slot = designer.slots.find(s => s.tempId === key);
  if (!slot) return;
  slot[field] = value;

  // Update the live cell label without losing input focus
  const cellEl = document.querySelector(`[data-gd-slot="${slot.tempId}"] .cgrid-slot-name`);
  if (cellEl && field === 'name') {
    cellEl.textContent = value || formatSlotCoord(slot.grid_row, slot.grid_col, slot.grid_row_span, slot.grid_col_span);
  }
}

function renderDesignerCanvas() {
  const boardWrap = document.getElementById('gd-canvas-wrap');
  const inspectorEl = document.getElementById('gd-inspector');
  if (!boardWrap || !inspectorEl) return;

  const occ = buildDesignerOccupancy();
  const breaks = designer.sectionBreaks;

  const rowTracks = ['auto'];
  for (let r = 1; r <= designer.rows; r++) {
    rowTracks.push('minmax(54px, auto)');
    if (breaks.includes(r)) rowTracks.push('14px');
  }

  const colHdrs = Array.from({ length: designer.cols }, (_, i) => {
    const c = i + 1;
    return `<div class="cgrid-col-hdr" style="grid-row: 1; grid-column: ${c + 1};">${escapeHtml(colToLetter(c))}</div>`;
  }).join('');

  let rowHdrs = '';
  let dividers = '';
  for (let r = 1; r <= designer.rows; r++) {
    const cssR = cssRowForDataRow(r, breaks);
    const hasBreak = breaks.includes(r);
    rowHdrs += `
      <div class="cgrid-row-hdr is-editable" style="grid-row: ${cssR}; grid-column: 1;">
        <span>${r}</span>
        ${r < designer.rows ? `
          <button
            type="button"
            class="gd-break-btn ${hasBreak ? 'active' : ''}"
            onclick="toggleRowSectionBreak(${r})"
            title="${hasBreak ? 'Remove section divider below row ' + r : 'Add section divider below row ' + r}"
          >—</button>
        ` : ''}
      </div>
    `;
    if (hasBreak) {
      dividers += `
        <div class="cgrid-section-divider is-designer" style="grid-row: ${cssR + 1}; grid-column: 1 / ${designer.cols + 2};">
          <span>Section Divider</span>
        </div>
      `;
    }
  }

  let emptyCells = '';
  for (let r = 1; r <= designer.rows; r++) {
    const cssR = cssRowForDataRow(r, breaks);
    for (let c = 1; c <= designer.cols; c++) {
      if (!occ.has(`${r},${c}`)) {
        const emptyKey = `empty:${r},${c}`;
        const isSel = designer.selectedKeys.has(emptyKey);
        emptyCells += `
          <button
            type="button"
            class="cgrid-empty-slot is-designer ${isSel ? 'is-selected' : ''}"
            style="grid-row: ${cssR}; grid-column: ${c + 1};"
            onclick="handleDesignerCellClick(event, '${escapeJsStr(emptyKey)}')"
            ondragover="handleDesignerDragOver(event)"
            ondragleave="handleDesignerDragLeave(event)"
            ondrop="handleDesignerDropOnEmpty(event, ${r}, ${c})"
            title="Empty cell ${escapeHtml(colToLetter(c))}${r}"
          >+</button>
        `;
      }
    }
  }

  const slotEls = designer.slots.map(s => {
    const startCssR = cssRowForDataRow(s.grid_row, breaks);
    const endCssR = cssRowForDataRow(s.grid_row + s.grid_row_span - 1, breaks) + 1;
    const startCssC = s.grid_col + 1;
    const endCssC = s.grid_col + s.grid_col_span + 1;
    const isSel = designer.selectedKeys.has(s.tempId);
    const coord = formatSlotCoord(s.grid_row, s.grid_col, s.grid_row_span, s.grid_col_span);

    return `
      <button
        type="button"
        draggable="true"
        data-gd-slot="${escapeHtml(s.tempId)}"
        class="cgrid-slot is-designer ${isSel ? 'is-selected' : ''}"
        style="grid-row: ${startCssR} / ${endCssR}; grid-column: ${startCssC} / ${endCssC};"
        onclick="handleDesignerCellClick(event, '${escapeJsStr(s.tempId)}')"
        ondragstart="handleDesignerDragStart(event, '${escapeJsStr(s.tempId)}')"
        ondragover="handleDesignerDragOver(event)"
        ondragleave="handleDesignerDragLeave(event)"
        ondrop="handleDesignerDropOnSlot(event, '${escapeJsStr(s.tempId)}')"
        ondragend="handleDesignerDragEnd(event)"
      >
        <span class="cgrid-slot-coord">${escapeHtml(coord)}</span>
        <span class="cgrid-slot-name">${escapeHtml(s.name)}</span>
      </button>
    `;
  }).join('');

  boardWrap.innerHTML = `
    <div
      class="cgrid-board"
      style="grid-template-columns: 42px repeat(${designer.cols}, minmax(52px, 1fr)); grid-template-rows: ${rowTracks.join(' ')};"
    >
      <div class="cgrid-corner" style="grid-row: 1; grid-column: 1;"></div>
      ${colHdrs}
      ${rowHdrs}
      ${dividers}
      ${emptyCells}
      ${slotEls}
    </div>
  `;

  // Render Inspector
  if (designer.selectedKeys.size === 0) {
    inspectorEl.innerHTML = `
      <div class="gd-inspector-hint">
        Tap a drawer to rename or resize it. Use <strong>Select multiple</strong> (or Shift+Click) to combine drawers into a larger one, or click <strong>—</strong> next to a row number to add a section divider.
      </div>
    `;
  } else if (designer.selectedKeys.size === 1) {
    const [key] = Array.from(designer.selectedKeys);
    if (key.startsWith('empty:')) {
      const [r, c] = key.slice(6).split(',').map(Number);
      inspectorEl.innerHTML = `
        <div class="gd-inspector-row">
          <span>Empty cell <strong>${escapeHtml(colToLetter(c))}${r}</strong></span>
          <button type="button" class="btn btn-primary btn-sm" onclick="addDrawerAtEmptyCell(${r}, ${c})">+ Add drawer here</button>
        </div>
      `;
    } else {
      const slot = designer.slots.find(s => s.tempId === key);
      if (!slot) return;
      const coord = formatSlotCoord(slot.grid_row, slot.grid_col, slot.grid_row_span, slot.grid_col_span);
      const isMerged = slot.grid_row_span > 1 || slot.grid_col_span > 1;
      inspectorEl.innerHTML = `
        <div class="gd-inspector-panel">
          <div class="gd-inspector-top">
            <span class="gd-coord-pill">${escapeHtml(coord)}</span>
            <input
              type="text"
              class="gd-name-input"
              value="${escapeHtml(slot.name)}"
              placeholder="Drawer label"
              oninput="updateSelectedDesignerSlotField('name', this.value)"
            >
            <select class="gd-type-select" onchange="updateSelectedDesignerSlotField('location_type', this.value)">
              <option value="drawer" ${slot.location_type === 'drawer' ? 'selected' : ''}>Drawer</option>
              <option value="bin" ${slot.location_type === 'bin' ? 'selected' : ''}>Bin</option>
              <option value="shelf" ${slot.location_type === 'shelf' ? 'selected' : ''}>Shelf</option>
              <option value="compartment" ${slot.location_type === 'compartment' ? 'selected' : ''}>Compartment</option>
              <option value="cubby" ${slot.location_type === 'cubby' ? 'selected' : ''}>Cubby</option>
              <option value="container" ${slot.location_type === 'container' ? 'selected' : ''}>Cabinet</option>
            </select>
          </div>
          <div class="gd-inspector-actions">
            <div class="gd-span-ctrl">
              <span>Width</span>
              <button type="button" class="btn btn-secondary btn-sm" onclick="resizeSelectedDesignerSlot('col', -1)" ${slot.grid_col_span <= 1 ? 'disabled' : ''}>−</button>
              <strong>${slot.grid_col_span}</strong>
              <button type="button" class="btn btn-secondary btn-sm" onclick="resizeSelectedDesignerSlot('col', 1)">+</button>
            </div>
            <div class="gd-span-ctrl">
              <span>Height</span>
              <button type="button" class="btn btn-secondary btn-sm" onclick="resizeSelectedDesignerSlot('row', -1)" ${slot.grid_row_span <= 1 ? 'disabled' : ''}>−</button>
              <strong>${slot.grid_row_span}</strong>
              <button type="button" class="btn btn-secondary btn-sm" onclick="resizeSelectedDesignerSlot('row', 1)">+</button>
            </div>
            ${isMerged ? `<button type="button" class="btn btn-secondary btn-sm" onclick="splitSelectedDesignerSlot()">Split</button>` : ''}
            <button type="button" class="btn btn-danger btn-sm" onclick="removeSelectedDesignerSlot()">Remove</button>
          </div>
        </div>
      `;
    }
  } else {
    inspectorEl.innerHTML = `
      <div class="gd-inspector-row">
        <span><strong>${designer.selectedKeys.size}</strong> cells selected</span>
        <div style="display: flex; gap: 0.5rem;">
          <button type="button" class="btn btn-primary btn-sm" onclick="combineSelectedDesignerCells()">Combine into one drawer</button>
          <button type="button" class="btn btn-secondary btn-sm" onclick="designerClearSelection()">Clear</button>
        </div>
      </div>
    `;
  }
}

export function designerClearSelection() {
  designer.selectedKeys.clear();
  renderDesignerCanvas();
}

function rectsOverlap(a, b) {
  const aMaxR = a.r + a.rs - 1;
  const aMaxC = a.c + a.cs - 1;
  const bMaxR = b.r + b.rs - 1;
  const bMaxC = b.c + b.cs - 1;
  return !(aMaxR < b.r || a.r > bMaxR || aMaxC < b.c || a.c > bMaxC);
}

function fitsBounds(rect) {
  return (
    rect.r >= 1 &&
    rect.c >= 1 &&
    rect.r + rect.rs - 1 <= designer.rows &&
    rect.c + rect.cs - 1 <= designer.cols
  );
}

function refreshAutoCoordName(slot) {
  if (/^[A-Z]\d+(–[A-Z]\d+)?$/.test(slot.name || '')) {
    slot.name = formatSlotCoord(slot.grid_row, slot.grid_col, slot.grid_row_span, slot.grid_col_span);
  }
}

export function handleDesignerDragStart(event, tempId) {
  designer.draggedTempId = tempId;
  if (event.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', tempId);
  }
  event.currentTarget?.classList?.add('is-dragging');
}

export function handleDesignerDragOver(event) {
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
  event.currentTarget?.classList?.add('is-drop-target');
}

export function handleDesignerDragLeave(event) {
  event.currentTarget?.classList?.remove('is-drop-target');
}

export function handleDesignerDragEnd(event) {
  designer.draggedTempId = null;
  event.currentTarget?.classList?.remove('is-dragging');
  document.querySelectorAll('.is-drop-target').forEach(el => el.classList.remove('is-drop-target'));
}

export function handleDesignerDropOnSlot(event, targetTempId) {
  event.preventDefault();
  event.currentTarget?.classList?.remove('is-drop-target');
  const sourceTempId = designer.draggedTempId || event.dataTransfer?.getData('text/plain');
  designer.draggedTempId = null;
  if (!sourceTempId || sourceTempId === targetTempId) return;

  const src = designer.slots.find(s => s.tempId === sourceTempId);
  const dst = designer.slots.find(s => s.tempId === targetTempId);
  if (!src || !dst) return;

  const newSrcRect = { r: dst.grid_row, c: dst.grid_col, rs: src.grid_row_span, cs: src.grid_col_span };
  const newDstRect = { r: src.grid_row, c: src.grid_col, rs: dst.grid_row_span, cs: dst.grid_col_span };

  if (!fitsBounds(newSrcRect) || !fitsBounds(newDstRect) || rectsOverlap(newSrcRect, newDstRect)) {
    showToast('Cannot swap drawers of different sizes there (not enough space).', 'info');
    return;
  }

  for (const other of designer.slots) {
    if (other.tempId === src.tempId || other.tempId === dst.tempId) continue;
    const otherRect = { r: other.grid_row, c: other.grid_col, rs: other.grid_row_span, cs: other.grid_col_span };
    if (rectsOverlap(newSrcRect, otherRect) || rectsOverlap(newDstRect, otherRect)) {
      showToast('Cannot swap there because it would overlap a neighboring drawer.', 'info');
      return;
    }
  }

  src.grid_row = newSrcRect.r;
  src.grid_col = newSrcRect.c;
  dst.grid_row = newDstRect.r;
  dst.grid_col = newDstRect.c;
  refreshAutoCoordName(src);
  refreshAutoCoordName(dst);

  designer.selectedKeys.clear();
  designer.selectedKeys.add(src.tempId);
  renderDesignerCanvas();
}

export function handleDesignerDropOnEmpty(event, targetRow, targetCol) {
  event.preventDefault();
  event.currentTarget?.classList?.remove('is-drop-target');
  const sourceTempId = designer.draggedTempId || event.dataTransfer?.getData('text/plain');
  designer.draggedTempId = null;
  if (!sourceTempId) return;

  const src = designer.slots.find(s => s.tempId === sourceTempId);
  if (!src) return;

  const newRect = { r: Number(targetRow), c: Number(targetCol), rs: src.grid_row_span, cs: src.grid_col_span };
  if (!fitsBounds(newRect)) {
    showToast('Not enough space to place that drawer there.', 'info');
    return;
  }

  for (const other of designer.slots) {
    if (other.tempId === src.tempId) continue;
    const otherRect = { r: other.grid_row, c: other.grid_col, rs: other.grid_row_span, cs: other.grid_col_span };
    if (rectsOverlap(newRect, otherRect)) {
      showToast('Not enough space — overlaps a neighboring drawer.', 'info');
      return;
    }
  }

  src.grid_row = newRect.r;
  src.grid_col = newRect.c;
  refreshAutoCoordName(src);

  designer.selectedKeys.clear();
  designer.selectedKeys.add(src.tempId);
  renderDesignerCanvas();
}

export async function saveGridDesigner() {
  if (!designer.unitId) return false;
  const result = await withSubmitLock('gd-save-btn', 'Saving...', async () => {
    try {
      const payload = {
        grid_rows: designer.rows,
        grid_cols: designer.cols,
        grid_config: JSON.stringify({ section_breaks: designer.sectionBreaks }),
        slots: designer.slots.map(s => ({
          id: s.id || null,
          name: (s.name || '').trim() || formatSlotCoord(s.grid_row, s.grid_col, s.grid_row_span, s.grid_col_span),
          location_type: s.location_type || 'drawer',
          grid_row: s.grid_row,
          grid_col: s.grid_col,
          grid_row_span: s.grid_row_span,
          grid_col_span: s.grid_col_span,
          merge_from_ids: s.merge_from_ids || [],
        })),
      };

      await apiPut(`/api/v1/locations/${designer.unitId}/grid`, payload, 'Could not save grid layout.');
      discardGridDesigner();
      await loadAllData();
      showToast('Container grid saved!');
      return true;
    } catch (err) {
      showToast(err.message || 'Could not save grid layout.', 'error');
      return false;
    }
  });
  return Boolean(result);
}

registerGridUnsavedHooks({
  isDirty: isGridDesignerDirty,
  save: saveGridDesigner,
  discard: discardGridDesigner,
});

