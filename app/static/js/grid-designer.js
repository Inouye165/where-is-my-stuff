/**
 * Container Grid Renderer & Interactive Grid Designer
 * Supports numeric rows (1, 2, 3...), alphabetic columns (A, B, C...),
 * variable drawer spans (merge/combine, resize, split), and section dividers.
 */
import { apiPost, apiPut } from './api.js';
import { renderSlotCarouselHtml, scrollSlotCarousel, syncAllSlotCarousels, syncSlotCarousel } from './carousel.js';
import { getGridInlineEditState } from './compartment-actions.js';
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

function normalizeCoordToken(str) {
  return String(str || '').trim().replace(/[\u2013\u2014-]/g, '-').replace(/\s+/g, '').toUpperCase();
}

export function isSameAsCoord(name, coord) {
  if (!name || !String(name).trim()) return true;
  return normalizeCoordToken(name) === normalizeCoordToken(coord);
}

function isGenericAutoSlotName(name) {
  return /^(drawer|bin|shelf|compartment|cubby|slot)\s+\d+$/i.test(String(name || '').trim());
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

export function computeUniformRowBands(rows, cols, slots, sectionBreaks = []) {
  const bands = [];
  let r = 1;
  while (r <= rows) {
    const covering = [];
    let coveredCols = 0;
    for (const s of slots) {
      const r0 = Number(s.grid_row);
      const rs = Math.max(1, Number(s.grid_row_span) || 1);
      const cs = Math.max(1, Number(s.grid_col_span) || 1);
      if (r0 <= r && r < r0 + rs) {
        covering.push(s);
        coveredCols += cs;
      }
    }
    const firstSpan = covering.length > 0 ? Math.max(1, Number(covering[0].grid_row_span) || 1) : 1;
    const allSameBand = firstSpan > 1
      && coveredCols === cols
      && r + firstSpan - 1 <= rows
      && covering.every(s => Number(s.grid_row) === r && Math.max(1, Number(s.grid_row_span) || 1) === firstSpan)
      && !sectionBreaks.some(b => b >= r && b < r + firstSpan - 1);

    if (allSameBand) {
      const endRow = r + firstSpan - 1;
      bands.push({ startRow: r, endRow, span: firstSpan, label: `${r}–${endRow}` });
      r = endRow + 1;
    } else {
      bands.push({ startRow: r, endRow: r, span: 1, label: `${r}` });
      r += 1;
    }
  }
  return bands;
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
  const minColWidth = diagram ? '12px' : compact ? '28px' : cols >= 7 ? '108px' : '124px';
  const rowTracks = ['auto']; // header row
  for (let r = 1; r <= rows; r++) {
    rowTracks.push(
      diagram ? 'minmax(14px, 1fr)' : compact ? 'minmax(32px, auto)' : isSingleCol ? 'auto' : '92px'
    );
    if (sectionBreaks.includes(r)) {
      rowTracks.push(diagram ? '4px' : compact ? '8px' : '14px');
    }
  }

  const colHeaderHtml = Array.from({ length: cols }, (_, idx) => {
    const c = idx + 1;
    return `<div class="cgrid-col-hdr" style="grid-row: 1; grid-column: ${c + 1};">${escapeHtml(colToLetter(c))}</div>`;
  }).join('');

  const rowBands = computeUniformRowBands(rows, cols, placed, sectionBreaks);
  let rowHdrHtml = '';
  for (const band of rowBands) {
    const startCssR = cssRowForDataRow(band.startRow, sectionBreaks);
    const endCssR = cssRowForDataRow(band.endRow, sectionBreaks) + 1;
    rowHdrHtml += `<div class="cgrid-row-hdr ${band.span > 1 ? 'is-merged-row' : ''}" style="grid-row: ${startCssR} / ${endCssR}; grid-column: 1;">${escapeHtml(band.label)}</div>`;
  }

  let dividerHtml = '';
  for (let r = 1; r <= rows; r++) {
    const cssR = cssRowForDataRow(r, sectionBreaks);
    if (sectionBreaks.includes(r)) {
      dividerHtml += `
        <div class="cgrid-section-divider" style="grid-row: ${cssR + 1}; grid-column: 1 / ${cols + 2};" title="Section divider">
          <span></span>
        </div>
      `;
    }
  }

  const safeUnitId = Number(unit?.id) || 0;
  const inlineEdit = (!compact && !diagram) ? getGridInlineEditState() : { renameId: null, quickAddId: null };

  let emptyCellsHtml = '';
  for (let r = 1; r <= rows; r++) {
    const cssR = cssRowForDataRow(r, sectionBreaks);
    for (let c = 1; c <= cols; c++) {
      if (!occupiedMap.has(`${r},${c}`)) {
        if (!compact && !diagram && safeUnitId) {
          const emptyCoord = formatSlotCoord(r, c, 1, 1);
          emptyCellsHtml += `
            <button
              type="button"
              class="cgrid-empty-slot is-interactive"
              style="grid-row: ${cssR}; grid-column: ${c + 1};"
              onclick="openOrCreateGridCompartment(${safeUnitId}, ${r}, ${c})"
              ondragover="handleGridSlotDragOver(event)"
              ondragleave="handleGridSlotDragLeave(event)"
              ondrop="handleGridSlotDropOnEmpty(event, ${safeUnitId}, ${r}, ${c})"
              title="Open compartment ${escapeHtml(emptyCoord)}"
              aria-label="Open empty compartment ${escapeHtml(emptyCoord)}"
            >
              <span class="cgrid-empty-slot-coord">${escapeHtml(emptyCoord)}</span>
              <span class="cgrid-empty-slot-cta">+ Open</span>
            </button>
          `;
        } else {
          emptyCellsHtml += `<div class="cgrid-empty-slot" style="grid-row: ${cssR}; grid-column: ${c + 1};" aria-hidden="true"></div>`;
        }
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
    const subtreeItems = getSubtreeItems(d.id);
    const itemCount = subtreeItems.length || Number(d.total_item_count || 0);
    const safeId = Number(d.id);
    const onClickAttr = diagram
      ? `locateDrawerInUnit(${safeUnitId}, ${safeId})`
      : `navToDrawer(${safeId}, true)`;
    const sameAsCoord = isSameAsCoord(d.name, coord);

    if (diagram) {
      const diagTitle = sameAsCoord ? coord : `${d.name} (${coord})`;
      return `
        <button
          type="button"
          class="cgrid-slot is-diagram ${isHighlighted ? 'is-highlighted' : ''}"
          style="grid-row: ${startCssR} / ${endCssR}; grid-column: ${startCssC} / ${endCssC};"
          onclick="${onClickAttr}"
          title="${escapeHtml(diagTitle)}"
          aria-label="${escapeHtml(diagTitle)}${isHighlighted ? ' — item is here' : ''}"
        >
          ${isHighlighted ? `<span class="cgrid-diagram-coord">${escapeHtml(coord)}</span>` : ''}
        </button>
      `;
    }

    const slotPhotos = (!compact && !diagram) ? getSlotPhotos(d) : [];
    const childBoxes = (d.children || []).filter(c => !isUnknownPlaceholder(c));
    const childBoxCount = childBoxes.length;
    const isEmptySlot = !compact && !diagram && itemCount === 0 && slotPhotos.length === 0 && childBoxCount === 0;
    const slotCarouselHtml = renderSlotCarouselHtml(slotPhotos);

    // Resolve clean, non-redundant display title & optional secondary preview
    const firstContentName = childBoxes[0]?.name || subtreeItems[0]?.name || '';
    let displayTitle = '';
    let secondaryPreview = '';
    if (sameAsCoord) {
      displayTitle = firstContentName;
      const totalEntries = childBoxCount + subtreeItems.length;
      if (displayTitle && totalEntries > 1) {
        secondaryPreview = `+${totalEntries - 1} more`;
      }
    } else {
      displayTitle = d.name || '';
      if (
        !compact &&
        !isSingleCol &&
        firstContentName &&
        isGenericAutoSlotName(displayTitle) &&
        normalizeCoordToken(firstContentName) !== normalizeCoordToken(displayTitle)
      ) {
        secondaryPreview = firstContentName;
      }
    }

    const tooltipParts = [coord];
    if (!sameAsCoord && d.name) tooltipParts.push(d.name);
    else if (firstContentName) tooltipParts.push(firstContentName);
    if (itemCount > 0) tooltipParts.push(`${itemCount} ${itemCount === 1 ? 'item' : 'items'}`);
    else if (childBoxCount > 0) tooltipParts.push(`${childBoxCount} ${childBoxCount === 1 ? 'box' : 'boxes'}`);
    else tooltipParts.push('Empty');
    const slotTooltip = tooltipParts.join(' · ');

    if (compact) {
      return `
        <div
          role="button"
          tabindex="0"
          class="cgrid-slot ${isHighlighted ? 'is-highlighted spotlight-highlight' : ''} is-compact"
          style="grid-row: ${startCssR} / ${endCssR}; grid-column: ${startCssC} / ${endCssC};"
          onclick="${onClickAttr}"
          onkeydown="if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); ${onClickAttr}; }"
          title="${escapeHtml(slotTooltip)}"
        >
          <div class="cgrid-slot-info">
            <span class="cgrid-slot-coord">${escapeHtml(coord)}</span>
            ${displayTitle ? `<span class="cgrid-slot-name">${escapeHtml(displayTitle)}</span>` : ''}
            ${isHighlighted ? `<span class="cgrid-here-badge">Here</span>` : ''}
          </div>
        </div>
      `;
    }

    const isRenamingCell = inlineEdit.renameId === safeId;
    const isQuickAddingCell = inlineEdit.quickAddId === safeId;
    const isInlineActive = isRenamingCell || isQuickAddingCell;

    const quickActionBtnsHtml = !isInlineActive ? `
      <span class="cgrid-slot-quick-btns" onclick="event.stopPropagation()">
        <button
          type="button"
          class="cgrid-quick-btn"
          title="${sameAsCoord ? `Name ${escapeHtml(coord)}` : `Rename ${escapeHtml(coord)}`}"
          aria-label="${sameAsCoord ? `Name ${escapeHtml(coord)}` : `Rename ${escapeHtml(coord)}`}"
          onclick="event.stopPropagation(); startGridCellRename(${safeId}, event)"
        >${lucideIcon('pencil', '', 11)}</button>
        <button
          type="button"
          class="cgrid-quick-btn"
          title="Add item to ${escapeHtml(coord)}"
          aria-label="Add item to ${escapeHtml(coord)}"
          onclick="event.stopPropagation(); startGridCellQuickAdd(${safeId}, event)"
        >${lucideIcon('plus', '', 12)}</button>
      </span>
    ` : '';

    const inlineFormHtml = isRenamingCell ? `
      <div class="cgrid-inline-form" onclick="event.stopPropagation()">
        <input
          type="text"
          id="grid-cell-rename-${safeId}"
          class="cgrid-inline-input"
          value="${escapeHtml(sameAsCoord ? '' : (d.name || ''))}"
          placeholder="Name ${escapeHtml(coord)}…"
          aria-label="Compartment name"
          onkeydown="if (event.key === 'Enter') { event.preventDefault(); saveGridCellRename(${safeId}, event); } else if (event.key === 'Escape') { event.preventDefault(); cancelGridCellInlineEdit(event); }"
        >
        <div class="cgrid-inline-form-btns">
          <button type="button" class="btn btn-primary btn-xs" onclick="event.stopPropagation(); saveGridCellRename(${safeId}, event)">Save</button>
          <button type="button" class="btn btn-secondary btn-xs" onclick="event.stopPropagation(); cancelGridCellInlineEdit(event)">Cancel</button>
        </div>
      </div>
    ` : isQuickAddingCell ? `
      <div class="cgrid-inline-form" onclick="event.stopPropagation()">
        <input
          type="text"
          id="grid-cell-add-${safeId}"
          class="cgrid-inline-input"
          placeholder="Add item (e.g. 2x M3 screw)…"
          aria-label="Quick add item"
          onkeydown="if (event.key === 'Enter') { event.preventDefault(); submitGridCellQuickAdd(${safeId}, event); } else if (event.key === 'Escape') { event.preventDefault(); cancelGridCellInlineEdit(event); }"
        >
        <div class="cgrid-inline-form-btns">
          <button type="button" class="btn btn-primary btn-xs" onclick="event.stopPropagation(); submitGridCellQuickAdd(${safeId}, event)">Add</button>
          <button type="button" class="btn btn-secondary btn-xs" title="Scan photo with AI" onclick="event.stopPropagation(); cancelGridCellInlineEdit(event); scanCompartmentPhotosWithAi(${safeId})">${lucideIcon('camera', '', 12)}</button>
          <button type="button" class="btn btn-secondary btn-xs" onclick="event.stopPropagation(); cancelGridCellInlineEdit(event)">Cancel</button>
        </div>
      </div>
    ` : '';

    const cellClickAttr = isInlineActive ? '' : onClickAttr;
    const cellKeydownAttr = isInlineActive
      ? ''
      : `if (event.key === 'Enter' || event.key === ' ') { if (event.target === this) { event.preventDefault(); ${onClickAttr}; } }`;
    const cellDragAttrs = (!isInlineActive && safeUnitId)
      ? `draggable="true"
        ondragstart="handleGridSlotDragStart(event, ${safeUnitId}, ${safeId})"
        ondragover="handleGridSlotDragOver(event)"
        ondragleave="handleGridSlotDragLeave(event)"
        ondrop="handleGridSlotDropOnSlot(event, ${safeUnitId}, ${safeId})"
        ondragend="handleGridSlotDragEnd(event)"`
      : '';

    if (isSingleCol) {
      let singleColBadge = '';
      if (!isHighlighted) {
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
          id="drawer-card-${safeId}"
          class="cgrid-slot ${isHighlighted ? 'is-highlighted spotlight-highlight' : ''} ${isEmptySlot ? 'is-empty' : ''} ${isInlineActive ? 'is-inline-editing' : ''}"
          style="grid-row: ${startCssR} / ${endCssR}; grid-column: ${startCssC} / ${endCssC};"
          onclick="${cellClickAttr}"
          onkeydown="${cellKeydownAttr}"
          ${cellDragAttrs}
          title="${escapeHtml(slotTooltip)}"
        >
          <div class="cgrid-slot-body">
            <div class="cgrid-slot-info">
              <div class="cgrid-slot-head">
                <span class="cgrid-slot-coord">${escapeHtml(coord)}</span>
                ${quickActionBtnsHtml}
              </div>
              ${isInlineActive ? inlineFormHtml : `
                ${displayTitle ? `<span class="cgrid-slot-name">${escapeHtml(displayTitle)}</span>` : ''}
                ${isHighlighted ? `<span class="cgrid-here-badge">Inside here</span>` : ''}
              `}
            </div>
            ${!isInlineActive ? slotCarouselHtml : ''}
            ${!isInlineActive ? singleColBadge : ''}
          </div>
        </div>
      `;
    }

    const rightBadgeHtml = isHighlighted
      ? `<span class="cgrid-here-badge">Inside here</span>`
      : itemCount > 0
        ? `<span class="cgrid-slot-count ${itemCount > 1 ? 'is-multi' : ''}" aria-label="${itemCount} ${itemCount === 1 ? 'item' : 'items'}">${itemCount}</span>`
        : childBoxCount > 0
          ? `<span class="cgrid-slot-count">${childBoxCount} ${childBoxCount === 1 ? 'box' : 'boxes'}</span>`
          : '';

    return `
      <div
        role="button"
        tabindex="0"
        id="drawer-card-${safeId}"
        class="cgrid-slot ${isHighlighted ? 'is-highlighted spotlight-highlight' : ''} ${isEmptySlot ? 'is-empty' : ''} ${slotPhotos.length > 0 ? 'has-photos' : ''} ${isInlineActive ? 'is-inline-editing' : ''}"
        style="grid-row: ${startCssR} / ${endCssR}; grid-column: ${startCssC} / ${endCssC};"
        onclick="${cellClickAttr}"
        onkeydown="${cellKeydownAttr}"
        ${cellDragAttrs}
        title="${escapeHtml(slotTooltip)}"
      >
        <div class="cgrid-slot-head">
          <span class="cgrid-slot-coord">${escapeHtml(coord)}</span>
          <span class="cgrid-slot-head-right">
            ${quickActionBtnsHtml}
            ${rightBadgeHtml}
          </span>
        </div>
        ${isInlineActive ? inlineFormHtml : `
          <div class="cgrid-slot-info">
            ${displayTitle ? `<span class="cgrid-slot-name">${escapeHtml(displayTitle)}</span>` : ''}
            ${secondaryPreview ? `<span class="cgrid-slot-sub">${escapeHtml(secondaryPreview)}</span>` : ''}
            ${isEmptySlot ? `<span class="cgrid-slot-empty-tag">Empty</span>` : ''}
          </div>
          ${slotCarouselHtml}
        `}
      </div>
    `;
  }).join('');

  if (!compact && !diagram && typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(syncAllSlotCarousels);
  }

  const wrapIdAttr = (!compact && !diagram && safeUnitId) ? `id="cgrid-wrap-${safeUnitId}"` : '';
  const boardIdAttr = (!compact && !diagram && safeUnitId) ? `id="cgrid-board-${safeUnitId}"` : '';

  return `
    <div ${wrapIdAttr} class="cgrid-wrap ${compact ? 'is-compact' : ''} ${diagram ? 'is-diagram' : ''} ${isSingleCol ? 'is-single-col' : ''}">
      <div
        ${boardIdAttr}
        class="cgrid-board"
        style="grid-template-columns: ${diagram ? '16px' : compact ? '24px' : '28px'} repeat(${cols}, minmax(${minColWidth}, 1fr)); grid-template-rows: ${rowTracks.join(' ')};"
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
  undoStack: [],
  redoStack: [],
};

function snapshotDesignerState() {
  return {
    rows: designer.rows,
    cols: designer.cols,
    sectionBreaks: [...designer.sectionBreaks],
    slots: designer.slots.map(s => ({
      ...s,
      merge_from_ids: [...(s.merge_from_ids || [])],
    })),
    selectedKeys: Array.from(designer.selectedKeys),
  };
}

function restoreDesignerSnapshot(snap) {
  if (!snap) return;
  designer.rows = snap.rows;
  designer.cols = snap.cols;
  designer.sectionBreaks = [...snap.sectionBreaks];
  designer.slots = snap.slots.map(s => ({
    ...s,
    merge_from_ids: [...(s.merge_from_ids || [])],
  }));
  designer.selectedKeys = new Set(snap.selectedKeys || []);
}

function pushDesignerHistory() {
  designer.undoStack.push(snapshotDesignerState());
  if (designer.undoStack.length > 50) designer.undoStack.shift();
  designer.redoStack = [];
}

function updateUndoRedoButtons() {
  if (typeof document === 'undefined') return;
  const undoBtn = document.getElementById('gd-undo-btn');
  const redoBtn = document.getElementById('gd-redo-btn');
  const saveBtn = document.getElementById('gd-sidebar-save-btn');
  const topSaveBtn = document.getElementById('gd-save-btn');
  if (undoBtn) undoBtn.disabled = designer.undoStack.length === 0;
  if (redoBtn) redoBtn.disabled = designer.redoStack.length === 0;
  const dirty = isGridDesignerDirty();
  if (saveBtn) saveBtn.classList.toggle('is-dirty', dirty);
  if (topSaveBtn) topSaveBtn.classList.toggle('is-dirty', dirty);
}

export function undoGridDesigner() {
  if (designer.undoStack.length === 0) return;
  designer.redoStack.push(snapshotDesignerState());
  const prev = designer.undoStack.pop();
  restoreDesignerSnapshot(prev);
  syncDesignerControls();
  renderDesignerCanvas();
}

export function redoGridDesigner() {
  if (designer.redoStack.length === 0) return;
  designer.undoStack.push(snapshotDesignerState());
  const next = designer.redoStack.pop();
  restoreDesignerSnapshot(next);
  syncDesignerControls();
  renderDesignerCanvas();
}

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

      <div class="gd-workspace">
        <div id="gd-canvas-wrap" class="gd-canvas-wrap"></div>

        <aside class="gd-sidebar" aria-label="Grid editing tools">
          <div class="gd-sidebar-actions">
            <div class="gd-sidebar-save-row">
              <button type="button" class="btn btn-primary btn-sm gd-sidebar-save-btn" id="gd-sidebar-save-btn" onclick="saveGridDesigner()">Save Grid</button>
              <button type="button" class="btn btn-secondary btn-sm" onclick="cancelInlineGridDesigner()">Cancel</button>
            </div>
            <div class="gd-sidebar-history-row">
              <button type="button" class="btn btn-secondary btn-xs" id="gd-undo-btn" onclick="undoGridDesigner()" disabled title="Undo (Ctrl+Z)">↶ Undo</button>
              <button type="button" class="btn btn-secondary btn-xs" id="gd-redo-btn" onclick="redoGridDesigner()" disabled title="Redo (Ctrl+Y)">↷ Redo</button>
            </div>
          </div>

          <div id="gd-inspector" class="gd-inspector"></div>

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

            <div class="gd-mode-group">
              <button type="button" id="gd-multi-toggle" class="btn btn-secondary btn-sm ${designer.multiSelectMode ? 'active' : ''}" aria-pressed="${designer.multiSelectMode ? 'true' : 'false'}" onclick="toggleDesignerMultiSelect()">
                Select multiple
              </button>
            </div>

            <details class="gd-tools-details" id="gd-tools-details">
              <summary class="btn btn-secondary btn-sm gd-tools-summary">Templates &amp; tools ▾</summary>
              <div class="gd-tools-panel">
                <div class="gd-presets-group">
                  <span class="gd-tools-label">Templates</span>
                  <button type="button" class="btn btn-secondary btn-sm" onclick="applyGridPreset('3x3')">3×3</button>
                  <button type="button" class="btn btn-secondary btn-sm" onclick="applyGridPreset('4x4')">4×4</button>
                  <button type="button" class="btn btn-secondary btn-sm" onclick="applyGridPreset('dresser_3_2')">Dresser (3 + 2×2)</button>
                  <button type="button" class="btn btn-secondary btn-sm" onclick="applyGridPreset('organizer_split')">Parts Organizer</button>
                </div>

                <div class="gd-mode-group">
                  <span class="gd-tools-label">Tools</span>
                  <select id="gd-default-type" class="gd-type-select" onchange="setDesignerDefaultType(this.value)" aria-label="Default slot type" title="Type for new &amp; auto-named slots">
                    ${Object.entries(SLOT_TYPE_LABELS).map(([val, label]) => `
                      <option value="${val}" ${designer.defaultType === val ? 'selected' : ''}>${label}</option>
                    `).join('')}
                  </select>
                  <button type="button" class="btn btn-secondary btn-sm" onclick="fillAllEmptyDesignerCells()">Fill empty</button>
                  <button type="button" class="btn btn-secondary btn-sm" onclick="autoNameAllDesignerSlots()">Auto-name</button>
                </div>
              </div>
            </details>
          </div>
        </aside>
      </div>
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
  designer.undoStack = [];
  designer.redoStack = [];
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
  updateUndoRedoButtons();
}

export function updateGridDesignerDimensions() {
  pushDesignerHistory();
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
  pushDesignerHistory();
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
  renderDesignerCanvas();
}

export function toggleRowSectionBreak(rowNum) {
  const r = Number(rowNum);
  if (r < 1 || r >= designer.rows) return;
  pushDesignerHistory();
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
  pushDesignerHistory();
  const label = SLOT_TYPE_LABELS[designer.defaultType] || 'Drawer';
  const sorted = [...designer.slots].sort((a, b) => (a.grid_row - b.grid_row) || (a.grid_col - b.grid_col));
  sorted.forEach((slot, idx) => {
    slot.location_type = designer.defaultType;
    slot.name = `${label} ${idx + 1}`;
  });
  renderDesignerCanvas();
}

export function fillAllEmptyDesignerCells() {
  pushDesignerHistory();
  fillEmptyCellsInDesigner();
  designer.selectedKeys.clear();
  renderDesignerCanvas();
}

export function addDrawerAtEmptyCell(r, c) {
  pushDesignerHistory();
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
  pushDesignerHistory();

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

  pushDesignerHistory();
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

  pushDesignerHistory();

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
  pushDesignerHistory();
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
    const coord = formatSlotCoord(slot.grid_row, slot.grid_col, slot.grid_row_span, slot.grid_col_span);
    const same = isSameAsCoord(value, coord);
    cellEl.textContent = same ? 'Drawer' : value;
    cellEl.classList.toggle('is-muted-placeholder', same);
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
    rowTracks.push('68px');
    if (breaks.includes(r)) rowTracks.push('14px');
  }

  const colHdrs = Array.from({ length: designer.cols }, (_, i) => {
    const c = i + 1;
    return `<div class="cgrid-col-hdr" style="grid-row: 1; grid-column: ${c + 1};">${escapeHtml(colToLetter(c))}</div>`;
  }).join('');

  const rowBands = computeUniformRowBands(designer.rows, designer.cols, designer.slots, breaks);
  let rowHdrs = '';
  for (const band of rowBands) {
    const startCssR = cssRowForDataRow(band.startRow, breaks);
    const endCssR = cssRowForDataRow(band.endRow, breaks) + 1;
    const hasBreak = breaks.includes(band.endRow);
    rowHdrs += `
      <div class="cgrid-row-hdr is-editable ${band.span > 1 ? 'is-merged-row' : ''}" style="grid-row: ${startCssR} / ${endCssR}; grid-column: 1;">
        <span>${escapeHtml(band.label)}</span>
        ${band.endRow < designer.rows ? `
          <button
            type="button"
            class="gd-break-btn ${hasBreak ? 'active' : ''}"
            onclick="toggleRowSectionBreak(${band.endRow})"
            title="${hasBreak ? 'Remove section divider below row ' + band.label : 'Add section divider below row ' + band.label}"
          >—</button>
        ` : ''}
      </div>
    `;
  }

  let dividers = '';
  for (let r = 1; r <= designer.rows; r++) {
    const cssR = cssRowForDataRow(r, breaks);
    if (breaks.includes(r)) {
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
    const same = isSameAsCoord(s.name, coord);
    const typeFallback = SLOT_TYPE_LABELS[s.location_type] || 'Drawer';

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
        title="${escapeHtml(same ? coord : `${coord} · ${s.name}`)}"
      >
        <span class="cgrid-slot-coord">${escapeHtml(coord)}</span>
        <span class="cgrid-slot-name ${same ? 'is-muted-placeholder' : ''}">${escapeHtml(same ? typeFallback : s.name)}</span>
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
        Click <strong>—</strong> next to a row number to add a section divider.
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
            ${designer.multiSelectMode
              ? `<span class="gd-inspector-inline-hint">Tap more drawers to combine</span>`
              : `<button type="button" class="btn btn-secondary btn-sm" onclick="toggleDesignerMultiSelect()">Combine with others…</button>`}
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
  updateUndoRedoButtons();
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

  pushDesignerHistory();

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

  pushDesignerHistory();
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

// =========================================================================
// Live Container Grid View Drag-and-Drop (Grab & Move / Swap Bins)
// =========================================================================

const gridViewDrag = {
  unitId: null,
  slotId: null,
  inFlight: false,
};

function clearGridViewDragClasses() {
  if (typeof document === 'undefined') return;
  document.querySelectorAll('.cgrid-board.is-slot-dragging').forEach(el => el.classList.remove('is-slot-dragging'));
  document.querySelectorAll('.cgrid-slot.is-dragging').forEach(el => el.classList.remove('is-dragging'));
  document.querySelectorAll('.is-drop-target').forEach(el => el.classList.remove('is-drop-target'));
}

function readGridDragSource(event) {
  if (gridViewDrag.slotId && gridViewDrag.unitId) {
    return { unitId: Number(gridViewDrag.unitId), slotId: Number(gridViewDrag.slotId) };
  }
  const raw = event?.dataTransfer?.getData('text/plain') || '';
  const match = /^grid-slot:(\d+):(\d+)$/.exec(raw);
  if (!match) return null;
  return { unitId: Number(match[1]), slotId: Number(match[2]) };
}

async function executeGridSlotMove(unitId, payload, successMessage) {
  if (gridViewDrag.inFlight) return;
  gridViewDrag.inFlight = true;
  try {
    await apiPost(`/api/v1/locations/${Number(unitId)}/grid/move`, payload, 'Could not move drawer.');
    await loadAllData();
    showToast(successMessage);
  } catch (err) {
    showToast(err?.message || 'Could not move drawer.', 'info');
  } finally {
    gridViewDrag.inFlight = false;
  }
}

export function handleGridSlotDragStart(event, unitId, slotId) {
  if (gridViewDrag.inFlight) {
    event?.preventDefault?.();
    return;
  }
  gridViewDrag.unitId = Number(unitId);
  gridViewDrag.slotId = Number(slotId);
  if (event?.dataTransfer) {
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', `grid-slot:${Number(unitId)}:${Number(slotId)}`);
  }
  const el = event?.currentTarget;
  el?.classList?.add('is-dragging');
  el?.closest?.('.cgrid-board')?.classList?.add('is-slot-dragging');
}

export function handleGridSlotDragOver(event) {
  if (!gridViewDrag.slotId) return;
  event?.preventDefault?.();
  if (event?.dataTransfer) event.dataTransfer.dropEffect = 'move';
  event?.currentTarget?.classList?.add('is-drop-target');
}

export function handleGridSlotDragLeave(event) {
  event?.currentTarget?.classList?.remove('is-drop-target');
}

export function handleGridSlotDragEnd(event) {
  gridViewDrag.unitId = null;
  gridViewDrag.slotId = null;
  event?.currentTarget?.classList?.remove('is-dragging');
  clearGridViewDragClasses();
}

export async function handleGridSlotDropOnSlot(event, unitId, targetSlotId) {
  event?.preventDefault?.();
  const src = readGridDragSource(event);
  gridViewDrag.unitId = null;
  gridViewDrag.slotId = null;
  clearGridViewDragClasses();

  const targetId = Number(targetSlotId);
  if (!src || src.unitId !== Number(unitId) || !targetId || src.slotId === targetId) return;

  await executeGridSlotMove(
    unitId,
    { source_id: src.slotId, target_id: targetId },
    'Drawers swapped'
  );
}

export async function handleGridSlotDropOnEmpty(event, unitId, targetRow, targetCol) {
  event?.preventDefault?.();
  const src = readGridDragSource(event);
  gridViewDrag.unitId = null;
  gridViewDrag.slotId = null;
  clearGridViewDragClasses();

  if (!src || src.unitId !== Number(unitId)) return;

  await executeGridSlotMove(
    unitId,
    { source_id: src.slotId, target_row: Number(targetRow), target_col: Number(targetCol) },
    'Drawer moved'
  );
}

registerGridUnsavedHooks({
  isDirty: isGridDesignerDirty,
  save: saveGridDesigner,
  discard: discardGridDesigner,
});

