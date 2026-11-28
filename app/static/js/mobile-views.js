/**
 * Phone Lookup UI (Strictly Search -> Container/Drawer -> Item Detail)
 * Tablets (>= 768px) use the full desktop interface.
 */
import { apiDelete, apiGet, apiPatch, apiUpload } from './api.js';
import {
  closeModal,
  escapeHtml,
  escapeJsStr,
  pluralize,
  sanitizePhotoUrl,
  showToast
} from './dom.js';
import { colToLetter, cssRowForDataRow, formatSlotCoord, parseGridConfig } from './grid-designer.js';
import { getRoomIconName, lucideIcon } from './icons.js';
import { compressImageFile } from './photo-compress.js';
import { handleGlobalBack, updateHash, updatePageTitle } from './router.js';
import {
  getLocationHierarchy,
  getLocType,
  getSubtreeItems,
  getUnassignedContainers,
  isUnknownPlaceholder,
  loadAllData,
  state,
  UNASSIGNED_ROOM_LABEL,
  updateNavStateAndCounts
} from './state.js';
import {
  formatBytes,
  getDrawerPhotosList,
  parseItemDescriptionAndParts,
  renderCabinetHighlightVisual,
  renderItemNotesSection,
  toSentenceCase
} from './views.js';

export const touchCaptureState = {
  roomId: null, // number | 'needs-home' | null
  unitId: null, // number | null
  drawerId: null, // number | null
  pendingFiles: [], // Array<{ file: File, previewUrl: string, name: string }>
  saving: false,
  renamingItemId: null,
  findQuery: '',
  findResults: null,
  findLoading: false
};

let mobileFindSeq = 0;
let mobileFindTimer = null;
let mobileItemAttSeq = 0;

export function isPhoneViewport() {
  return typeof window !== 'undefined' && window.innerWidth < 768;
}

export function isMobileOrTabletViewport() {
  if (typeof window === 'undefined') return false;
  const w = window.innerWidth;
  if (w <= 1024) return true;
  const coarse = Boolean(window.matchMedia && window.matchMedia('(pointer: coarse)').matches);
  return coarse && w <= 1366;
}

export function shouldUseMobileCompanion() {
  return isPhoneViewport();
}

export function shouldUseLimitedItemView() {
  return isPhoneViewport();
}

export function toggleMobileTheme() {
  if (typeof document === 'undefined') return;
  const cur = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
  const next = cur === 'dark' ? 'light' : 'dark';
  state.themePref = next;
  try {
    localStorage.setItem('wims_theme_pref', next);
  } catch {}
  document.documentElement.setAttribute('data-theme', next);
  const themeMeta = typeof document.querySelector === 'function'
    ? document.querySelector('meta[name="theme-color"]')
    : null;
  if (themeMeta) {
    themeMeta.setAttribute('content', next === 'light' ? '#f8fafc' : '#0b1120');
  }
  const labelEl = document.getElementById('m-theme-toggle-label');
  if (labelEl) labelEl.textContent = next === 'dark' ? 'Light mode' : 'Dark mode';
}

function isRunningStandalone() {
  if (typeof window === 'undefined') return false;
  return Boolean(
    (window.matchMedia && (
      window.matchMedia('(display-mode: standalone)').matches ||
      window.matchMedia('(display-mode: fullscreen)').matches
    )) ||
    window.navigator?.standalone === true
  );
}

function renderMobileBrandBar() {
  const isLight = typeof document !== 'undefined' && document.documentElement.getAttribute('data-theme') === 'light';
  const standalone = isRunningStandalone();
  return `
    <header class="m-brand-bar">
      <div class="m-brand-left">
        <span class="rail-brand-mark" aria-hidden="true">
          ${lucideIcon('home', '', 20)}
        </span>
        <span class="m-brand-title">WhereIsMyStuff</span>
      </div>
      <div class="m-brand-actions">
        ${!standalone ? `
          <button type="button" id="m-install-app-btn" class="m-install-btn" onclick="triggerPwaInstall()">
            ${lucideIcon('plus', '', 16)} Install
          </button>
        ` : ''}
        <button type="button" class="m-theme-btn" onclick="toggleMobileTheme()">
          <span id="m-theme-toggle-label">${isLight ? 'Dark mode' : 'Light mode'}</span>
        </button>
      </div>
    </header>
  `;
}

function renderMobileSubpageTopbar(backAction = 'handleGlobalBack()', backLabel = 'Back') {
  return `
    <div class="m-topbar">
      <button type="button" class="m-back-btn" onclick="${backAction}">
        ${lucideIcon('arrowLeft', '', 20)} ${escapeHtml(backLabel)}
      </button>
      <button type="button" class="m-topbar-search-btn" onclick="navToHome()">
        ${lucideIcon('search', '', 17)} Search
      </button>
    </div>
  `;
}

export function clearTouchPendingFiles() {
  for (const entry of touchCaptureState.pendingFiles) {
    if (entry.previewUrl) {
      try { URL.revokeObjectURL(entry.previewUrl); } catch {}
    }
  }
  touchCaptureState.pendingFiles = [];
}

function getRoomContainers(room) {
  const children = room?.children || [];
  const subRooms = new Set(children.filter(c => getLocType(c) === 'room' || getLocType(c) === 'site').map(c => c.id));
  return children.filter(c => !subRooms.has(c.id));
}

export function getOrderedUnitDrawers(unit) {
  if (!unit) return [];
  const children = Array.isArray(unit.children) ? [...unit.children] : [];
  if (children.length === 0) return [unit];

  const hasGrid = Number(unit.grid_rows) > 0 && Number(unit.grid_cols) > 0;
  if (!hasGrid) {
    return children.sort((a, b) => (a.name || '').localeCompare(b.name || '', undefined, { numeric: true, sensitivity: 'base' }));
  }

  const placed = children
    .filter(d => Number(d.grid_row) >= 1 && Number(d.grid_col) >= 1)
    .sort((a, b) => (Number(a.grid_row) - Number(b.grid_row)) || (Number(a.grid_col) - Number(b.grid_col)));
  const unplaced = children
    .filter(d => !(Number(d.grid_row) >= 1 && Number(d.grid_col) >= 1))
    .sort((a, b) => (a.name || '').localeCompare(b.name || '', undefined, { numeric: true, sensitivity: 'base' }));

  return [...placed, ...unplaced];
}

function countPhotographedDrawers(unit) {
  const drawers = getOrderedUnitDrawers(unit);
  let withPhotos = 0;
  for (const d of drawers) {
    if (getDrawerPhotosList(d).length > 0) withPhotos += 1;
  }
  return { withPhotos, total: drawers.length };
}

function getLocationItemCount(loc) {
  if (!loc) return 0;
  const subtreeCount = getSubtreeItems(loc.id).length;
  return subtreeCount || Number(loc.total_item_count || 0);
}

// ============================================================================
// DRESSER / CABINET DIAGRAM RENDERER (Shared by Container Browse & Capture)
// ============================================================================

export function renderMobileCabinetDiagram(unit, activeDrawer = null, options = {}) {
  const { interactive = false, compact = false, mode = 'capture' } = options;
  if (!unit) return '';

  const isBrowse = mode === 'browse';
  const activeId = activeDrawer ? Number(activeDrawer.id) : null;
  const drawers = getOrderedUnitDrawers(unit);
  const rows = Number(unit.grid_rows) || 0;
  const cols = Number(unit.grid_cols) || 0;
  const hasGrid = rows > 0 && cols > 0;

  if (hasGrid) {
    const cfg = parseGridConfig(unit.grid_config);
    const sectionBreaks = cfg.section_breaks.filter(b => b >= 1 && b < rows);
    const placed = drawers.filter(d => Number(d.grid_row) >= 1 && Number(d.grid_col) >= 1);
    const unplaced = drawers.filter(d => !(Number(d.grid_row) >= 1 && Number(d.grid_col) >= 1));

    const occupied = new Set();
    for (const d of placed) {
      const r0 = Number(d.grid_row);
      const c0 = Number(d.grid_col);
      const rs = Math.max(1, Number(d.grid_row_span) || 1);
      const cs = Math.max(1, Number(d.grid_col_span) || 1);
      for (let r = r0; r < r0 + rs; r++) {
        for (let c = c0; c < c0 + cs; c++) {
          occupied.add(`${r},${c}`);
        }
      }
    }

    const rowTracks = ['auto'];
    for (let r = 1; r <= rows; r++) {
      rowTracks.push(compact ? 'minmax(38px, auto)' : 'minmax(76px, auto)');
      if (sectionBreaks.includes(r)) {
        rowTracks.push(compact ? '6px' : '12px');
      }
    }

    const colHeaders = Array.from({ length: cols }, (_, idx) => {
      const c = idx + 1;
      return `<div class="m-cab-hdr" style="grid-row: 1; grid-column: ${c + 1};">${escapeHtml(colToLetter(c))}</div>`;
    }).join('');

    let rowHeaders = '';
    let dividers = '';
    let emptyCells = '';
    for (let r = 1; r <= rows; r++) {
      const cssR = cssRowForDataRow(r, sectionBreaks);
      rowHeaders += `<div class="m-cab-hdr" style="grid-row: ${cssR}; grid-column: 1;">${r}</div>`;
      if (sectionBreaks.includes(r)) {
        dividers += `<div class="m-cab-divider" style="grid-row: ${cssR + 1}; grid-column: 2 / ${cols + 2};"></div>`;
      }
      for (let c = 1; c <= cols; c++) {
        if (!occupied.has(`${r},${c}`)) {
          emptyCells += `<div class="m-cab-empty" style="grid-row: ${cssR}; grid-column: ${c + 1};"></div>`;
        }
      }
    }

    const slotsHtml = placed.map(d => {
      const safeId = Number(d.id);
      const r0 = Number(d.grid_row);
      const c0 = Number(d.grid_col);
      const rs = Math.max(1, Number(d.grid_row_span) || 1);
      const cs = Math.max(1, Number(d.grid_col_span) || 1);
      const startR = cssRowForDataRow(r0, sectionBreaks);
      const endR = cssRowForDataRow(r0 + rs - 1, sectionBreaks) + 1;
      const startC = c0 + 1;
      const endC = c0 + cs + 1;
      const coord = formatSlotCoord(r0, c0, rs, cs);
      const photos = getDrawerPhotosList(d);
      const isSelected = activeId !== null && safeId === activeId;

      if (compact) {
        const tag = interactive ? 'button' : 'div';
        const clickAction = isBrowse ? `navToDrawer(${safeId}, true)` : `selectTouchDrawer(${safeId})`;
        const clickAttr = interactive ? `type="button" onclick="${clickAction}"` : '';
        return `
          <${tag}
            id="drawer-card-${safeId}"
            class="m-cab-slot is-compact ${isSelected ? (isBrowse ? 'is-highlighted' : 'is-selected') : ''} ${!isBrowse && photos.length > 0 ? 'has-photos' : ''}"
            style="grid-row: ${startR} / ${endR}; grid-column: ${startC} / ${endC};"
            ${clickAttr}
          >
            <span class="m-drawer-pull" aria-hidden="true"></span>
            <span class="m-cab-compact-label">${escapeHtml(isSelected && !interactive ? 'Here' : coord)}</span>
          </${tag}>
        `;
      }

      if (isBrowse) {
        const itemCount = getLocationItemCount(d);
        const firstThumb = photos[0] ? (photos[0].thumb || photos[0].url) : '';
        return `
          <button
            type="button"
            id="drawer-card-${safeId}"
            class="m-cab-slot ${isSelected ? 'is-highlighted' : ''}"
            style="grid-row: ${startR} / ${endR}; grid-column: ${startC} / ${endC};"
            onclick="navToDrawer(${safeId}, true)"
          >
            <span class="m-drawer-pull" aria-hidden="true"></span>
            <div class="m-cab-slot-main">
              <div class="m-cab-slot-text">
                <span class="m-cab-coord-row">
                  <span class="m-cab-coord">${escapeHtml(coord)}</span>
                  ${isSelected ? `<span class="m-cab-here-pill">Here</span>` : ''}
                </span>
                <span class="m-cab-name">${escapeHtml(d.name)}</span>
              </div>
              ${firstThumb ? `<img class="m-cab-mini-thumb" src="${escapeHtml(firstThumb)}" alt="">` : ''}
            </div>
            <span class="m-cab-item-count">${escapeHtml(pluralize(itemCount, 'item'))}</span>
          </button>
        `;
      }

      const firstThumb = photos[0] ? (photos[0].thumb || photos[0].url) : '';
      return `
        <button
          type="button"
          class="m-cab-slot ${isSelected ? 'is-selected' : ''} ${photos.length > 0 ? 'has-photos' : ''}"
          style="grid-row: ${startR} / ${endR}; grid-column: ${startC} / ${endC};"
          onclick="selectTouchDrawer(${safeId})"
        >
          <span class="m-drawer-pull" aria-hidden="true"></span>
          <div class="m-cab-slot-main">
            <div class="m-cab-slot-text">
              <span class="m-cab-coord">${escapeHtml(coord)}</span>
              <span class="m-cab-name">${escapeHtml(d.name)}</span>
            </div>
            ${firstThumb ? `<img class="m-cab-mini-thumb" src="${escapeHtml(firstThumb)}" alt="">` : ''}
          </div>
          <span class="m-cab-status ${photos.length > 0 ? 'is-done' : ''}">
            ${photos.length > 0 ? `✓ ${pluralize(photos.length, 'photo')}` : '+ Add photo'}
          </span>
        </button>
      `;
    }).join('');

    const unplacedHtml = (!compact && unplaced.length > 0) ? `
      <div class="m-cab-extra-section">
        <div class="m-cab-extra-label">Other sections</div>
        <div class="m-cab-fallback-grid cols-2">
          ${unplaced.map(d => renderFallbackDrawerButton(d, activeId, interactive, false, mode)).join('')}
        </div>
      </div>
    ` : '';

    return `
      <div class="m-cabinet-frame ${compact ? 'is-compact' : ''}">
        <div class="m-cabinet-top-cap" aria-hidden="true"></div>
        <div class="m-cabinet-scroll">
          <div
            class="m-cabinet-board"
            style="grid-template-columns: ${compact ? '22px' : '28px'} repeat(${cols}, minmax(${compact ? '36px' : '84px'}, 1fr)); grid-template-rows: ${rowTracks.join(' ')};"
          >
            <div style="grid-row: 1; grid-column: 1;"></div>
            ${colHeaders}
            ${rowHeaders}
            ${dividers}
            ${emptyCells}
            ${slotsHtml}
          </div>
        </div>
        ${unplacedHtml}
      </div>
    `;
  }

  // Fallback Dresser / Shelf Diagram when no explicit grid has been configured
  const autoCols = drawers.length >= 6 ? 2 : 1;
  return `
    <div class="m-cabinet-frame ${compact ? 'is-compact' : ''}">
      <div class="m-cabinet-top-cap" aria-hidden="true"></div>
      <div class="m-cab-fallback-grid cols-${autoCols}">
        ${drawers.map(d => renderFallbackDrawerButton(d, activeId, interactive, compact, mode)).join('')}
      </div>
    </div>
  `;
}

function renderFallbackDrawerButton(d, activeId, interactive, compact = false, mode = 'capture') {
  const safeId = Number(d.id);
  const isBrowse = mode === 'browse';
  const photos = getDrawerPhotosList(d);
  const isSelected = activeId !== null && safeId === activeId;
  const firstThumb = photos[0] ? (photos[0].thumb || photos[0].url) : '';

  if (compact || !interactive) {
    return `
      <div id="drawer-card-${safeId}" class="m-cab-slot is-compact ${isSelected ? (isBrowse ? 'is-highlighted' : 'is-selected') : ''} ${!isBrowse && photos.length > 0 ? 'has-photos' : ''}">
        <span class="m-drawer-pull" aria-hidden="true"></span>
        <span class="m-cab-compact-label">${escapeHtml(d.name)}${isSelected ? ' · Here' : ''}</span>
      </div>
    `;
  }

  if (isBrowse) {
    const itemCount = getLocationItemCount(d);
    return `
      <button
        type="button"
        id="drawer-card-${safeId}"
        class="m-cab-slot ${isSelected ? 'is-highlighted' : ''}"
        onclick="navToDrawer(${safeId}, true)"
      >
        <span class="m-drawer-pull" aria-hidden="true"></span>
        <div class="m-cab-slot-main">
          <div class="m-cab-slot-text">
            ${isSelected ? `<span class="m-cab-here-pill">Here</span>` : ''}
            <span class="m-cab-name">${escapeHtml(d.name)}</span>
          </div>
          ${firstThumb ? `<img class="m-cab-mini-thumb" src="${escapeHtml(firstThumb)}" alt="">` : ''}
        </div>
        <span class="m-cab-item-count">${escapeHtml(pluralize(itemCount, 'item'))}</span>
      </button>
    `;
  }

  return `
    <button
      type="button"
      class="m-cab-slot ${isSelected ? 'is-selected' : ''} ${photos.length > 0 ? 'has-photos' : ''}"
      onclick="selectTouchDrawer(${safeId})"
    >
      <span class="m-drawer-pull" aria-hidden="true"></span>
      <div class="m-cab-slot-main">
        <div class="m-cab-slot-text">
          <span class="m-cab-name">${escapeHtml(d.name)}</span>
        </div>
        ${firstThumb ? `<img class="m-cab-mini-thumb" src="${escapeHtml(firstThumb)}" alt="">` : ''}
      </div>
      <span class="m-cab-status ${photos.length > 0 ? 'is-done' : ''}">
        ${photos.length > 0 ? `✓ ${pluralize(photos.length, 'photo')}` : '+ Add photo'}
      </span>
    </button>
  `;
}

// ============================================================================
// PHONE VIEW 1: BASIC ITEM SEARCH (Room, Storage Container, Item)
// ============================================================================

export function renderMobileFindView() {
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;
  workspace.classList.remove('is-home-canvas', 'is-unit-view');

  const q = touchCaptureState.findQuery || '';

  workspace.innerHTML = `
    <div class="m-page-shell">
      ${renderMobileBrandBar()}

      <div class="m-search-section">
        <h1 class="m-page-heading">Find an Item</h1>
        <div class="m-search-box">
          ${lucideIcon('search', 'm-search-icon', 24)}
          <input
            type="text"
            id="mobile-find-input"
            class="m-search-input"
            placeholder="What are you looking for?"
            value="${escapeHtml(q)}"
            autocomplete="off"
            oninput="handleMobileFindInput(event)"
          >
          <button
            type="button"
            id="mobile-find-clear-btn"
            class="m-search-clear-btn"
            style="display: ${q ? 'inline-flex' : 'none'};"
            onclick="clearMobileFind()"
            aria-label="Clear search"
          >
            ${lucideIcon('x', '', 20)}
          </button>
        </div>
      </div>

      <div id="mobile-find-results"></div>
    </div>
  `;

  renderMobileFindResults();
}

export function handleMobileFindInput(e) {
  const raw = e?.target?.value || '';
  touchCaptureState.findQuery = raw;
  const clearBtn = document.getElementById('mobile-find-clear-btn');
  if (clearBtn) clearBtn.style.display = raw ? 'inline-flex' : 'none';

  const trimmed = raw.trim();
  clearTimeout(mobileFindTimer);
  if (!trimmed) {
    mobileFindSeq += 1;
    touchCaptureState.findLoading = false;
    touchCaptureState.findResults = null;
    renderMobileFindResults();
    return;
  }

  touchCaptureState.findLoading = true;
  renderMobileFindResults();
  mobileFindTimer = setTimeout(() => {
    runMobileFindSearch(trimmed);
  }, 140);
}

export function clearMobileFind() {
  clearTimeout(mobileFindTimer);
  mobileFindSeq += 1;
  touchCaptureState.findQuery = '';
  touchCaptureState.findResults = null;
  touchCaptureState.findLoading = false;
  const input = document.getElementById('mobile-find-input');
  if (input) {
    input.value = '';
    input.focus();
  }
  const clearBtn = document.getElementById('mobile-find-clear-btn');
  if (clearBtn) clearBtn.style.display = 'none';
  renderMobileFindResults();
}

async function runMobileFindSearch(query) {
  const seq = ++mobileFindSeq;
  try {
    const res = await apiGet(`/api/v1/search?q=${encodeURIComponent(query)}&limit=50`, 'Could not search items.');
    if (seq !== mobileFindSeq) return;
    touchCaptureState.findResults = Array.isArray(res?.items) ? res.items : [];
    touchCaptureState.findLoading = false;
    renderMobileFindResults();
  } catch {
    if (seq !== mobileFindSeq) return;
    touchCaptureState.findResults = [];
    touchCaptureState.findLoading = false;
    renderMobileFindResults();
  }
}

function renderMobileSearchResultCard(item) {
  const safeId = Number(item.id);
  const hier = getLocationHierarchy(item.location_id);
  const roomDisplay = hier.room ? toSentenceCase(hier.room.name) : UNASSIGNED_ROOM_LABEL;
  const hasUnit = Boolean(hier.unit);
  const targetDrawerId = Number(hier.drawer?.id || item.location_id || 0);

  return `
    <article class="m-search-card">
      <button type="button" class="m-search-item-btn" onclick="navToItem(${safeId})">
        <span class="m-search-item-name">${escapeHtml(item.name)}</span>
        <span class="m-row-chevron" aria-hidden="true">${lucideIcon('chevronRight', '', 20)}</span>
      </button>
      <div class="m-search-meta-row">
        <span class="m-search-room-pill">
          ${lucideIcon('home', '', 14)}
          <span>${escapeHtml(roomDisplay)}</span>
        </span>
        ${hasUnit ? `
          <button
            type="button"
            class="m-search-container-btn"
            onclick="locateDrawerInUnit(${Number(hier.unit.id)}, ${targetDrawerId})"
          >
            ${lucideIcon('package', '', 14)}
            <span>${escapeHtml(toSentenceCase(hier.unit.name))}</span>
            ${lucideIcon('crosshair', 'm-search-locate-icon', 14)}
          </button>
        ` : `
          <span class="m-search-container-muted">
            ${lucideIcon('package', '', 14)}
            <span>No container</span>
          </span>
        `}
      </div>
    </article>
  `;
}

function renderMobileFindResults() {
  const container = document.getElementById('mobile-find-results');
  if (!container) return;

  const q = (touchCaptureState.findQuery || '').trim();
  if (!q) {
    container.innerHTML = `
      <div class="m-search-empty-hint">
        Search across ${escapeHtml(pluralize(state.items.length, 'item'))} by name, part #, tag, or location.
      </div>
    `;
    return;
  }

  if (touchCaptureState.findLoading) {
    container.innerHTML = `<div class="m-status-msg">Searching...</div>`;
    return;
  }

  const results = Array.isArray(touchCaptureState.findResults) ? touchCaptureState.findResults : [];
  if (results.length === 0) {
    container.innerHTML = `
      <div class="m-status-msg">No items matched "${escapeHtml(q)}".</div>
    `;
    return;
  }

  container.innerHTML = `
    <div class="m-list-section">
      <div class="m-section-eyebrow">${pluralize(results.length, 'matching item')}</div>
      <div class="m-search-results-list">
        ${results.map(renderMobileSearchResultCard).join('')}
      </div>
    </div>
  `;
}

// ============================================================================
// PHONE VIEW 2: STORAGE CONTAINER ("WHERE IN") VIEW
// ============================================================================

export function renderMobileStorageUnitView(unitId) {
  const safeUnitId = Number(unitId);
  const unit = state.locationMap.get(safeUnitId);
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;
  workspace.classList.remove('is-home-canvas', 'is-unit-view');
  workspace.scrollTop = 0;

  if (!unit) {
    workspace.innerHTML = `
      <div class="m-page-shell">
        ${renderMobileSubpageTopbar()}
        <div class="m-card" style="text-align: center; padding: 2rem 1.25rem;">
          <h2 class="m-page-heading">Container not found</h2>
          <button type="button" class="btn btn-primary m-big-btn" style="margin-top: 1rem;" onclick="navToHome()">
            Back to Search
          </button>
        </div>
      </div>
    `;
    return;
  }

  const hier = getLocationHierarchy(safeUnitId);
  const roomDisplay = hier.room ? toSentenceCase(hier.room.name) : UNASSIGNED_ROOM_LABEL;
  const drawers = Array.isArray(unit.children) ? unit.children.filter(c => !isUnknownPlaceholder(c)) : [];
  const spotlightId = state.spotlightDrawerId ? Number(state.spotlightDrawerId) : null;
  const highlightedDrawer = spotlightId ? (drawers.find(d => Number(d.id) === spotlightId) || null) : null;
  const unitPhoto = sanitizePhotoUrl(unit.container_photo || unit.context_photo || '');
  const directItems = state.items.filter(i => Number(i.location_id) === safeUnitId);

  let highlightBannerHtml = '';
  if (highlightedDrawer) {
    const hasCoords = Number(highlightedDrawer.grid_row) >= 1 && Number(highlightedDrawer.grid_col) >= 1;
    const coordSuffix = hasCoords
      ? ` (${formatSlotCoord(highlightedDrawer.grid_row, highlightedDrawer.grid_col, highlightedDrawer.grid_row_span, highlightedDrawer.grid_col_span)})`
      : '';
    highlightBannerHtml = `
      <button type="button" class="m-where-highlight-banner" onclick="navToDrawer(${Number(highlightedDrawer.id)}, true)">
        <div class="m-where-highlight-copy">
          <span class="m-where-highlight-eyebrow">Highlighted Drawer</span>
          <strong class="m-where-highlight-name">${escapeHtml(highlightedDrawer.name + coordSuffix)}</strong>
        </div>
        <span class="m-where-highlight-cta">View contents →</span>
      </button>
    `;
  }

  let photoBlockHtml = '';
  if (unitPhoto) {
    const canOverlay = Boolean(
      highlightedDrawer
      && Number(unit.grid_rows) > 0
      && Number(unit.grid_cols) > 0
      && Number(highlightedDrawer.grid_row) >= 1
      && Number(highlightedDrawer.grid_col) >= 1
    );
    photoBlockHtml = `
      <section class="m-card m-unit-photo-card">
        <div class="m-section-eyebrow">${canOverlay ? 'Where in Container' : 'Container Photo'}</div>
        ${canOverlay
          ? renderCabinetHighlightVisual(unit, highlightedDrawer, { compact: false, interactive: false })
          : `
            <button type="button" class="m-unit-photo-btn" onclick="openLightbox('${escapeJsStr(unitPhoto)}', '${escapeJsStr(toSentenceCase(unit.name))}')">
              <img src="${escapeHtml(unitPhoto)}" alt="${escapeHtml(toSentenceCase(unit.name))}">
            </button>
          `
        }
      </section>
    `;
  }

  const drawersBlockHtml = drawers.length > 0 ? `
    <section class="m-unit-drawers-section">
      <div class="m-section-head-row">
        <div class="m-section-eyebrow" style="margin-bottom: 0;">Drawers &amp; Compartments</div>
        <span class="m-section-hint">Tap any drawer to see what's inside</span>
      </div>
      ${renderMobileCabinetDiagram(unit, highlightedDrawer, { interactive: true, compact: false, mode: 'browse' })}
    </section>
  ` : '';

  const directItemsHtml = directItems.length > 0 ? `
    <section class="m-card">
      <div class="m-section-eyebrow">Items in ${escapeHtml(toSentenceCase(unit.name))} (${directItems.length})</div>
      <div class="m-drawer-items-list">
        ${directItems.map(renderMobileDrawerItemRow).join('')}
      </div>
    </section>
  ` : (drawers.length === 0 ? `
    <div class="m-card">
      <p class="m-status-msg" style="padding: 0.75rem 0;">No drawers or items recorded in this container yet.</p>
    </div>
  ` : '');

  workspace.innerHTML = `
    <div class="m-page-shell">
      ${renderMobileSubpageTopbar()}

      <div class="m-step-header">
        <div class="m-context-eyebrow">${escapeHtml(roomDisplay)}</div>
        <h1 class="m-page-heading">${escapeHtml(toSentenceCase(unit.name))}</h1>
      </div>

      ${highlightBannerHtml}
      ${photoBlockHtml}
      ${drawersBlockHtml}
      ${directItemsHtml}
    </div>
  `;

  if (highlightedDrawer && typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(() => {
      const target = document.getElementById(`drawer-card-${Number(highlightedDrawer.id)}`);
      target?.scrollIntoView?.({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    });
  }
}

// ============================================================================
// PHONE VIEW 3: DRAWER CONTENTS VIEW
// ============================================================================

function renderMobileDrawerItemRow(item) {
  const safeId = Number(item.id);
  const photo = sanitizePhotoUrl(item.item_photo || '');
  const hasQty = item.quantity !== null && item.quantity !== undefined;
  const metaParts = [];
  if (hasQty) metaParts.push(`${Number(item.quantity)} ${item.unit || 'pcs'}`);
  if (item.serial_or_part_number) metaParts.push(item.serial_or_part_number);
  const metaText = metaParts.join(' · ');

  return `
    <button type="button" class="m-drawer-item-row" onclick="navToItem(${safeId}, true)">
      <div class="m-item-row-thumb">
        ${photo
          ? `<img src="${escapeHtml(photo)}" alt="${escapeHtml(item.name)}" loading="lazy">`
          : lucideIcon('package', '', 24)
        }
      </div>
      <div class="m-item-row-body">
        <div class="m-item-row-name">${escapeHtml(item.name)}</div>
        ${metaText ? `<div class="m-item-row-loc">${escapeHtml(metaText)}</div>` : ''}
      </div>
      <span class="m-row-chevron" aria-hidden="true">${lucideIcon('chevronRight', '', 20)}</span>
    </button>
  `;
}

export function renderMobileDrawerView(drawerId) {
  const safeDrawerId = Number(drawerId);
  const drawer = state.locationMap.get(safeDrawerId);
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;
  workspace.classList.remove('is-home-canvas', 'is-unit-view');
  workspace.scrollTop = 0;

  if (!drawer) {
    workspace.innerHTML = `
      <div class="m-page-shell">
        ${renderMobileSubpageTopbar()}
        <div class="m-card" style="text-align: center; padding: 2rem 1.25rem;">
          <h2 class="m-page-heading">Drawer not found</h2>
          <button type="button" class="btn btn-primary m-big-btn" style="margin-top: 1rem;" onclick="navToHome()">
            Back to Search
          </button>
        </div>
      </div>
    `;
    return;
  }

  const hier = getLocationHierarchy(safeDrawerId);
  const roomDisplay = hier.room ? toSentenceCase(hier.room.name) : UNASSIGNED_ROOM_LABEL;
  const hasUnitPill = Boolean(hier.unit && Number(hier.unit.id) !== safeDrawerId);
  const hasCoords = Number(drawer.grid_row) >= 1 && Number(drawer.grid_col) >= 1;
  const coord = hasCoords
    ? formatSlotCoord(drawer.grid_row, drawer.grid_col, drawer.grid_row_span, drawer.grid_col_span)
    : '';
  const photos = getDrawerPhotosList(drawer);
  const childBoxes = (drawer.children || []).filter(c => !isUnknownPlaceholder(c));
  const items = state.items.filter(i => Number(i.location_id) === safeDrawerId);

  const countParts = [];
  if (childBoxes.length > 0) countParts.push(pluralize(childBoxes.length, 'box', 'boxes'));
  countParts.push(pluralize(items.length, 'item'));

  workspace.innerHTML = `
    <div class="m-page-shell">
      ${renderMobileSubpageTopbar()}

      <div class="m-step-header">
        <div class="m-context-eyebrow">
          ${escapeHtml(roomDisplay)}${hasUnitPill ? ` › ${escapeHtml(toSentenceCase(hier.unit.name))}` : ''}
        </div>
        <div class="m-drawer-title-row">
          <h1 class="m-page-heading">${escapeHtml(drawer.name)}</h1>
          ${coord ? `<span class="m-coord-pill">${escapeHtml(coord)}</span>` : ''}
          ${drawer.label ? `<span class="box-label-badge">${escapeHtml(drawer.label)}</span>` : ''}
        </div>
        <p class="m-page-sub">${escapeHtml(countParts.join(' · '))}</p>
        ${hasUnitPill ? `
          <div>
            <button
              type="button"
              class="m-unit-jump-pill"
              onclick="locateDrawerInUnit(${Number(hier.unit.id)}, ${Number(hier.drawer?.id || safeDrawerId)})"
            >
              ${lucideIcon('crosshair', '', 14)} Show in ${escapeHtml(toSentenceCase(hier.unit.name))}
            </button>
          </div>
        ` : ''}
      </div>

      ${photos.length > 0 ? `
        <section class="m-card">
          <div class="m-section-eyebrow">Drawer Photos (${photos.length})</div>
          <div class="m-photo-strip">
            ${photos.map((p, idx) => `
              <div class="m-photo-card">
                <img
                  src="${escapeHtml(p.thumb || p.url)}"
                  alt="${escapeHtml(p.caption || `${drawer.name} photo ${idx + 1}`)}"
                  onclick="openLightbox('${escapeJsStr(p.url)}', '${escapeJsStr(p.caption || drawer.name)}')"
                >
              </div>
            `).join('')}
          </div>
        </section>
      ` : ''}

      ${childBoxes.length > 0 ? `
        <section class="m-card">
          <div class="m-section-eyebrow">Boxes &amp; Kits Inside (${childBoxes.length})</div>
          <div class="m-drawer-items-list">
            ${childBoxes.map(box => {
              const boxId = Number(box.id);
              const boxPhoto = sanitizePhotoUrl(box.container_photo || box.context_photo || '');
              const boxCount = getLocationItemCount(box);
              return `
                <button type="button" class="m-box-row" onclick="navToDrawer(${boxId}, true)">
                  <div class="m-item-row-thumb">
                    ${boxPhoto
                      ? `<img src="${escapeHtml(boxPhoto)}" alt="${escapeHtml(box.name)}" loading="lazy">`
                      : lucideIcon('archive', '', 24)
                    }
                  </div>
                  <div class="m-item-row-body">
                    <div class="m-item-row-name">
                      ${box.label ? `<span class="box-label-badge">${escapeHtml(box.label)}</span> ` : ''}${escapeHtml(box.name)}
                    </div>
                    <div class="m-item-row-loc">${escapeHtml(pluralize(boxCount, 'item'))}</div>
                  </div>
                  <span class="m-row-chevron" aria-hidden="true">${lucideIcon('chevronRight', '', 20)}</span>
                </button>
              `;
            }).join('')}
          </div>
        </section>
      ` : ''}

      <section class="m-card">
        <div class="m-section-eyebrow">Items Inside (${items.length})</div>
        ${items.length > 0 ? `
          <div class="m-drawer-items-list">
            ${items.map(renderMobileDrawerItemRow).join('')}
          </div>
        ` : `
          <p class="m-status-msg" style="padding: 0.75rem 0;">
            ${childBoxes.length > 0 ? 'No loose items in this drawer — check the boxes above.' : 'No items recorded in this drawer yet.'}
          </p>
        `}
      </section>
    </div>
  `;
}

// ============================================================================
// PHONE VIEW 4: READ-ONLY ITEM DETAIL PAGE (Images, Notes, PDFs/Files)
// ============================================================================

export function startLimitedItemRename(itemId) {
  touchCaptureState.renamingItemId = Number(itemId);
  renderLimitedItemView(Number(itemId));
  requestAnimationFrame(() => {
    const input = document.getElementById('mobile-limited-rename-input');
    if (input) {
      input.focus();
      input.select();
    }
  });
}

export function cancelLimitedItemRename(itemId) {
  touchCaptureState.renamingItemId = null;
  renderLimitedItemView(Number(itemId));
}

export async function saveLimitedItemRename(itemId) {
  const input = document.getElementById('mobile-limited-rename-input');
  const nextName = (input?.value || '').trim();
  if (!nextName) {
    showToast('Item name cannot be empty.', 'error');
    if (input) input.focus();
    return;
  }
  try {
    await apiPatch(`/api/v1/items/${Number(itemId)}`, { name: nextName }, 'Could not rename item.');
    touchCaptureState.renamingItemId = null;
    showToast('Item renamed.');
    await loadAllData();
  } catch (err) {
    showToast(err?.message || 'Could not rename item.', 'error');
  }
}

export function openMobileNotesModal(itemId) {
  const item = state.items.find(i => Number(i.id) === Number(itemId));
  if (!item) return;
  const modal = document.getElementById('mobile-notes-modal');
  const titleEl = document.getElementById('mobile-notes-modal-title');
  const bodyEl = document.getElementById('mobile-notes-modal-body');
  if (!modal || !bodyEl) return;

  if (titleEl) titleEl.textContent = `${item.name} — Notes`;
  const notesHtml = renderItemNotesSection(item);
  bodyEl.innerHTML = notesHtml || `<p class="m-status-msg">No notes saved for this item.</p>`;
  modal.classList.add('open');
}

export function closeMobileNotesModal() {
  closeModal('mobile-notes-modal');
}

function collectMobileItemImages(item, loc, imageAttachments = []) {
  const seen = new Set();
  const list = [];
  const pushImg = (rawUrl, rawThumb, caption) => {
    const url = sanitizePhotoUrl(rawUrl || '');
    if (!url || seen.has(url)) return;
    seen.add(url);
    list.push({
      url,
      thumb: sanitizePhotoUrl(rawThumb || '') || url,
      caption: caption || (item ? item.name : 'Photo')
    });
  };

  if (item && item.item_photo) {
    pushImg(item.item_photo, null, item.name);
  }
  for (const att of imageAttachments) {
    pushImg(att.file_path, att.thumbnail_path, att.title || att.filename || item?.name);
  }
  if (loc) {
    for (const p of getDrawerPhotosList(loc)) {
      pushImg(p.url, p.thumb, p.caption || loc.name);
    }
  }
  return list;
}

function renderMobileImagesGridHtml(images) {
  if (!images.length) return '';
  return `
    <div class="m-section-eyebrow">Images (${images.length})</div>
    <div class="m-images-grid">
      ${images.map((img, idx) => `
        <button
          type="button"
          class="m-image-tile"
          onclick="openLightbox('${escapeJsStr(img.url)}', '${escapeJsStr(img.caption || `Photo ${idx + 1}`)}')"
          aria-label="View image ${idx + 1}: ${escapeHtml(img.caption || 'Photo')}"
        >
          <img src="${escapeHtml(img.thumb || img.url)}" alt="${escapeHtml(img.caption || `Photo ${idx + 1}`)}" loading="lazy">
        </button>
      `).join('')}
    </div>
  `;
}

async function loadMobileItemAttachments(itemId) {
  const safeId = Number(itemId);
  const seq = ++mobileItemAttSeq;
  const docsSection = document.getElementById(`mobile-item-docs-section-${safeId}`);
  const imagesSection = document.getElementById(`mobile-item-images-section-${safeId}`);

  try {
    const attachments = await apiGet(`/api/v1/items/${safeId}/attachments`);
    if (seq !== mobileItemAttSeq) return;

    const allAtts = Array.isArray(attachments) ? attachments : [];
    const imageAtts = allAtts.filter(a => a && a.attachment_type === 'image');
    const docAtts = allAtts.filter(a => a && a.attachment_type !== 'image');

    const item = state.items.find(i => Number(i.id) === safeId);
    const loc = item && item.location_id ? state.locationMap.get(item.location_id) : null;
    const currentImagesEl = document.getElementById(`mobile-item-images-section-${safeId}`);
    if (currentImagesEl && item) {
      const allImages = collectMobileItemImages(item, loc, imageAtts);
      if (allImages.length > 0) {
        currentImagesEl.hidden = false;
        currentImagesEl.innerHTML = renderMobileImagesGridHtml(allImages);
      } else {
        currentImagesEl.hidden = true;
      }
    }

    const currentDocsEl = document.getElementById(`mobile-item-docs-section-${safeId}`);
    if (!currentDocsEl) return;

    if (docAtts.length === 0) {
      currentDocsEl.hidden = true;
      return;
    }

    currentDocsEl.hidden = false;
    currentDocsEl.innerHTML = `
      <div class="m-section-eyebrow">Files &amp; PDFs (${docAtts.length})</div>
      <div class="m-docs-list">
        ${docAtts.map(att => {
          const safeUrl = sanitizePhotoUrl(att.file_path || '');
          const badge = String(att.attachment_type || 'file').toUpperCase();
          const title = att.title || att.filename || 'Document';
          const sizeStr = formatBytes(att.size_bytes);
          const meta = [att.filename, sizeStr].filter(Boolean).join(' · ');
          if (!safeUrl) {
            return `
              <div class="m-doc-row">
                <span class="m-doc-badge">${escapeHtml(badge)}</span>
                <div class="m-doc-info">
                  <div class="m-doc-title">${escapeHtml(title)}</div>
                  ${meta ? `<div class="m-doc-meta">${escapeHtml(meta)}</div>` : ''}
                </div>
              </div>
            `;
          }
          return `
            <a
              class="m-doc-row"
              href="${escapeHtml(safeUrl)}"
              target="_blank"
              rel="noopener noreferrer"
            >
              <span class="m-doc-badge">${escapeHtml(badge)}</span>
              <div class="m-doc-info">
                <div class="m-doc-title">${escapeHtml(title)}</div>
                ${meta ? `<div class="m-doc-meta">${escapeHtml(meta)}</div>` : ''}
              </div>
              <span class="m-row-chevron" aria-hidden="true">${lucideIcon('chevronRight', '', 20)}</span>
            </a>
          `;
        }).join('')}
      </div>
    `;
  } catch {
    if (seq !== mobileItemAttSeq) return;
    if (docsSection) docsSection.hidden = true;
    if (imagesSection && !imagesSection.innerHTML.trim()) imagesSection.hidden = true;
  }
}

export function renderLimitedItemView(itemId) {
  const safeItemId = Number(itemId);
  const item = state.items.find(i => Number(i.id) === safeItemId);
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;
  workspace.classList.remove('is-home-canvas', 'is-unit-view');
  workspace.scrollTop = 0;

  if (!item) {
    workspace.innerHTML = `
      <div class="m-page-shell">
        ${renderMobileSubpageTopbar()}
        <div class="m-card" style="text-align: center; padding: 2rem 1.25rem;">
          <h2 class="m-page-heading">Item not found</h2>
          <button type="button" class="btn btn-primary m-big-btn" style="margin-top: 1rem;" onclick="navToHome()">
            Back to Search
          </button>
        </div>
      </div>
    `;
    return;
  }

  const hier = getLocationHierarchy(item.location_id);
  const loc = item.location_id ? state.locationMap.get(item.location_id) : null;
  const itemPhoto = sanitizePhotoUrl(item.item_photo || '');
  const hasQty = item.quantity !== null && item.quantity !== undefined;
  const qtyText = hasQty ? `Qty: ${Number(item.quantity)} ${item.unit || 'pcs'}` : '';
  const targetDrawerId = Number(hier.drawer?.id || item.location_id || 0);

  const locationRows = [];
  if (hier.isNeedsHome) {
    locationRows.push({ label: 'Room', value: UNASSIGNED_ROOM_LABEL, onclick: '' });
  } else if (hier.room) {
    locationRows.push({ label: 'Room', value: toSentenceCase(hier.room.name), onclick: '' });
  }
  if (hier.unit) {
    locationRows.push({
      label: 'Storage Container',
      value: toSentenceCase(hier.unit.name),
      onclick: `locateDrawerInUnit(${Number(hier.unit.id)}, ${targetDrawerId})`
    });
  }
  if (hier.drawer && (!hier.unit || hier.drawer.id !== hier.unit.id)) {
    const hasCoords = Number(hier.drawer.grid_row) >= 1 && Number(hier.drawer.grid_col) >= 1;
    const coordSuffix = hasCoords
      ? ` (${formatSlotCoord(hier.drawer.grid_row, hier.drawer.grid_col, hier.drawer.grid_row_span, hier.drawer.grid_col_span)})`
      : '';
    locationRows.push({
      label: 'Drawer / Spot',
      value: `${hier.drawer.name}${coordSuffix}`,
      onclick: `navToDrawer(${Number(hier.drawer.id)}, true)`
    });
  }
  for (const sub of (hier.subLocations || [])) {
    locationRows.push({
      label: 'Box / Kit',
      value: sub.name,
      onclick: `navToDrawer(${Number(sub.id)}, true)`
    });
  }

  const parsedDesc = parseItemDescriptionAndParts(item.description);
  const notesHtml = renderItemNotesSection(item);
  const initialImages = collectMobileItemImages(item, loc, []);

  workspace.innerHTML = `
    <div class="m-page-shell">
      ${renderMobileSubpageTopbar()}

      <div class="mobile-limited-grid">
        <section class="m-card">
          ${itemPhoto ? `
            <div class="m-item-hero-photo">
              <img src="${escapeHtml(itemPhoto)}" alt="${escapeHtml(item.name)}" onclick="openLightbox('${escapeJsStr(itemPhoto)}', '${escapeJsStr(item.name)}')">
            </div>
          ` : ''}

          <div class="m-item-title-area">
            ${item.category ? `<div class="m-item-category-chip">${escapeHtml(item.category)}</div>` : ''}
            <h1 class="m-item-title">${escapeHtml(item.name)}</h1>
            <div class="m-item-badges-row">
              ${qtyText ? `<span class="m-qty-badge">${escapeHtml(qtyText)}</span>` : ''}
              ${item.serial_or_part_number ? `<span class="m-model-badge">${escapeHtml(item.serial_or_part_number)}</span>` : ''}
            </div>
          </div>
        </section>

        <section class="m-card">
          <div class="m-section-eyebrow">Where to Find It</div>
          ${locationRows.length > 0 ? `
            <div class="m-where-list">
              ${locationRows.map(r => r.onclick ? `
                <button type="button" class="m-where-row is-clickable" onclick="${r.onclick}">
                  <span class="m-where-label">${escapeHtml(r.label)}</span>
                  <span class="m-where-value-wrap">
                    <span class="m-where-value">${escapeHtml(r.value)}</span>
                    ${lucideIcon('chevronRight', 'm-row-chevron', 18)}
                  </span>
                </button>
              ` : `
                <div class="m-where-row">
                  <span class="m-where-label">${escapeHtml(r.label)}</span>
                  <span class="m-where-value">${escapeHtml(r.value)}</span>
                </div>
              `).join('')}
            </div>
          ` : `
            <p class="m-status-msg" style="padding: 0.5rem 0;">Not assigned to a container yet.</p>
          `}
        </section>

        <section class="m-card" id="mobile-item-images-section-${safeItemId}" ${initialImages.length === 0 ? 'hidden' : ''}>
          ${initialImages.length > 0 ? renderMobileImagesGridHtml(initialImages) : ''}
        </section>

        ${notesHtml ? `
          <section class="m-card m-notes-card">
            <div class="m-notes-card-head">
              <span class="m-section-eyebrow" style="margin-bottom: 0;">Notes</span>
              <button type="button" class="btn btn-secondary btn-xs" onclick="openMobileNotesModal(${safeItemId})">
                Full View
              </button>
            </div>
            <div class="m-notes-card-body">
              ${notesHtml}
            </div>
          </section>
        ` : ''}

        ${parsedDesc.parts.length > 0 ? `
          <section class="m-card">
            <div class="m-section-eyebrow">Parts Inside (${parsedDesc.parts.length})</div>
            <div class="item-parts-readonly-list">
              ${parsedDesc.parts.map(p => `
                <div class="item-part-readonly-row">
                  <span class="item-part-qty-pill">×${Math.max(1, Number(p.qty || 1))}</span>
                  <div style="flex: 1; min-width: 0;">
                    <span class="item-part-name">${escapeHtml(p.name)}</span>
                    ${p.partNumber ? `<span class="item-part-code">${escapeHtml(p.partNumber)}</span>` : ''}
                    ${p.specs ? `<div class="item-part-specs">${escapeHtml(p.specs)}</div>` : ''}
                  </div>
                </div>
              `).join('')}
            </div>
          </section>
        ` : ''}

        <section class="m-card" id="mobile-item-docs-section-${safeItemId}" hidden></section>
      </div>
    </div>
  `;

  void loadMobileItemAttachments(safeItemId);
}

// ============================================================================
// TAB 2: ADD PHOTOS FLOW (Room -> Cabinet -> Dresser/Cabinet Diagram -> Photos)
// ============================================================================

function buildTouchCaptureHash() {
  const { roomId, unitId, drawerId } = touchCaptureState;
  if (roomId && unitId && drawerId) return `#/touch-capture/${roomId}/${unitId}/${drawerId}`;
  if (roomId && unitId) return `#/touch-capture/${roomId}/${unitId}`;
  if (roomId) return `#/touch-capture/${roomId}`;
  return '#/touch-capture';
}

export function navToTouchCapture(roomId = null, unitId = null, drawerId = null, recordHistory = true) {
  const prevDrawerId = touchCaptureState.drawerId;
  const nextDrawerId = drawerId ? Number(drawerId) : null;
  if (prevDrawerId !== nextDrawerId) {
    clearTouchPendingFiles();
  }

  touchCaptureState.roomId = roomId === 'needs-home' ? 'needs-home' : (roomId ? Number(roomId) : null);
  touchCaptureState.unitId = unitId ? Number(unitId) : null;
  touchCaptureState.drawerId = nextDrawerId;

  state.activeTab = 'touch-capture';
  state.currentView = 'touch-capture';
  state.currentLocationId = nextDrawerId || touchCaptureState.unitId || (typeof touchCaptureState.roomId === 'number' ? touchCaptureState.roomId : null);
  state.spotlightDrawerId = null;
  updatePageTitle('Add Drawer Photos');

  if (recordHistory) {
    const hash = buildTouchCaptureHash();
    state.historyStack.push({
      tab: 'touch-capture',
      view: 'touch-capture',
      roomId: touchCaptureState.roomId,
      unitId: touchCaptureState.unitId,
      drawerId: touchCaptureState.drawerId
    });
    updateHash(hash);
  }

  updateNavStateAndCounts();
  renderTouchCaptureView();
}

export function selectTouchRoom(roomId) {
  clearTouchPendingFiles();
  navToTouchCapture(roomId, null, null, true);
}

export function selectTouchUnit(unitId) {
  clearTouchPendingFiles();
  const unit = state.locationMap.get(Number(unitId));
  const drawers = getOrderedUnitDrawers(unit);
  const autoDrawerId = (!isPhoneViewport() || (unit && (!unit.children || unit.children.length === 0)))
    ? (drawers[0]?.id || null)
    : null;
  navToTouchCapture(touchCaptureState.roomId, Number(unitId), autoDrawerId, true);
}

export function selectTouchDrawer(drawerId) {
  if (touchCaptureState.drawerId === Number(drawerId)) return;
  clearTouchPendingFiles();
  navToTouchCapture(touchCaptureState.roomId, touchCaptureState.unitId, Number(drawerId), true);
}

export function stepTouchDrawer(delta) {
  const unit = touchCaptureState.unitId ? state.locationMap.get(touchCaptureState.unitId) : null;
  if (!unit) return;
  const drawers = getOrderedUnitDrawers(unit);
  if (drawers.length === 0) return;

  const idx = drawers.findIndex(d => d.id === touchCaptureState.drawerId);
  const nextIdx = idx === -1 ? 0 : idx + delta;
  if (nextIdx < 0 || nextIdx >= drawers.length) return;

  clearTouchPendingFiles();
  navToTouchCapture(touchCaptureState.roomId, unit.id, drawers[nextIdx].id, false);
  updateHash(buildTouchCaptureHash());
}

export function handleTouchPhotoPicked(e) {
  const files = Array.from(e.target?.files || []).filter(Boolean);
  if (!files.length) return;
  for (const file of files) {
    let previewUrl = '';
    try { previewUrl = URL.createObjectURL(file); } catch {}
    touchCaptureState.pendingFiles.push({
      file,
      previewUrl,
      name: file.name || 'Photo'
    });
  }
  if (e.target) e.target.value = '';
  renderTouchCaptureView();
}

export function removePendingTouchPhoto(index) {
  const idx = Number(index);
  if (touchCaptureState.saving) return;
  if (idx < 0 || idx >= touchCaptureState.pendingFiles.length) return;
  const [removed] = touchCaptureState.pendingFiles.splice(idx, 1);
  if (removed?.previewUrl) {
    try { URL.revokeObjectURL(removed.previewUrl); } catch {}
  }
  renderTouchCaptureView();
}

export async function saveTouchDrawerPhotos(advance = true) {
  if (touchCaptureState.saving) return;
  const drawerId = touchCaptureState.drawerId;
  const drawer = drawerId ? state.locationMap.get(drawerId) : null;
  const unit = touchCaptureState.unitId ? state.locationMap.get(touchCaptureState.unitId) : null;
  if (!drawer || touchCaptureState.pendingFiles.length === 0) return;

  touchCaptureState.saving = true;
  renderTouchCaptureView();

  const totalToUpload = touchCaptureState.pendingFiles.length;
  try {
    // Remove each file once saved so a retry after a mid-batch failure doesn't duplicate it.
    while (touchCaptureState.pendingFiles.length > 0) {
      const pending = touchCaptureState.pendingFiles[0];
      const compressed = await compressImageFile(pending.file);
      const fd = new FormData();
      fd.append('file', compressed);
      await apiUpload(`/api/v1/locations/${drawer.id}/photos`, fd, 'Photo upload failed.');
      const pos = touchCaptureState.pendingFiles.indexOf(pending);
      if (pos !== -1) touchCaptureState.pendingFiles.splice(pos, 1);
      if (pending.previewUrl) {
        try { URL.revokeObjectURL(pending.previewUrl); } catch {}
      }
    }

    clearTouchPendingFiles();
    showToast(`Saved ${pluralize(totalToUpload, 'photo')} to ${drawer.name}`);

    if (touchCaptureState.drawerId !== drawerId) {
      touchCaptureState.saving = false;
      await loadAllData();
      return;
    }

    if (advance && unit) {
      const drawers = getOrderedUnitDrawers(unit);
      const curIdx = drawers.findIndex(d => d.id === drawer.id);
      if (curIdx !== -1 && curIdx + 1 < drawers.length) {
        touchCaptureState.drawerId = drawers[curIdx + 1].id;
        updateHash(buildTouchCaptureHash());
      } else {
        showToast(`All drawers in ${unit.name} done!`);
        if (isPhoneViewport()) {
          touchCaptureState.drawerId = null;
          updateHash(buildTouchCaptureHash());
        }
      }
    }

    touchCaptureState.saving = false;
    await loadAllData();
  } catch (err) {
    touchCaptureState.saving = false;
    showToast(err?.message || 'Could not save photo.', 'error');
    renderTouchCaptureView();
  }
}

export async function deleteTouchDrawerPhoto(drawerId, photoId) {
  if (!drawerId || !photoId) return;
  try {
    await apiDelete(`/api/v1/locations/${drawerId}/photos/${photoId}`, 'Could not delete photo.');
    showToast('Photo removed.');
    await loadAllData();
  } catch (err) {
    showToast(err?.message || 'Could not delete photo.', 'error');
  }
}

function renderTouchRoomsStep() {
  const rooms = state.namedRooms || [];
  const unassigned = getUnassignedContainers();

  return `
    ${renderMobileBrandBar()}
    <div class="m-step-header">
      <h1 class="m-page-heading">Choose a Room</h1>
      <p class="m-page-sub">Select where the cabinet or dresser is located</p>
    </div>

    ${rooms.length === 0 && unassigned.length === 0 ? `
      <div class="m-card" style="text-align: center; padding: 2rem 1.25rem;">
        <p class="m-page-sub">No rooms set up yet. Add a room on your computer or tap Add Storage Unit to get started.</p>
      </div>
    ` : `
      <div class="m-choice-grid">
        ${rooms.map(room => {
          const safeId = Number(room.id);
          const photo = sanitizePhotoUrl(room.context_photo || room.container_photo || '');
          const containers = getRoomContainers(room);
          const iconName = getRoomIconName(room);
          return `
            <button type="button" class="m-choice-card" onclick="selectTouchRoom(${safeId})">
              <div class="m-choice-thumb">
                ${photo
                  ? `<img src="${escapeHtml(photo)}" alt="${escapeHtml(room.name)}">`
                  : lucideIcon(iconName, '', 28)
                }
              </div>
              <div class="m-choice-info">
                <div class="m-choice-title">${escapeHtml(toSentenceCase(room.name))}</div>
                <div class="m-choice-meta">${escapeHtml(pluralize(containers.length, 'cabinet / unit'))}</div>
              </div>
              <span class="m-row-chevron" aria-hidden="true">${lucideIcon('chevronRight', '', 22)}</span>
            </button>
          `;
        }).join('')}

        ${unassigned.length > 0 ? `
          <button type="button" class="m-choice-card" onclick="selectTouchRoom('needs-home')">
            <div class="m-choice-thumb">${lucideIcon('archive', '', 28)}</div>
            <div class="m-choice-info">
              <div class="m-choice-title">${escapeHtml(UNASSIGNED_ROOM_LABEL)}</div>
              <div class="m-choice-meta">${escapeHtml(pluralize(unassigned.length, 'cabinet / unit'))}</div>
            </div>
            <span class="m-row-chevron" aria-hidden="true">${lucideIcon('chevronRight', '', 22)}</span>
          </button>
        ` : ''}
      </div>
    `}
  `;
}

function renderTouchUnitsStep(room, units) {
  const roomTitle = room ? toSentenceCase(room.name) : UNASSIGNED_ROOM_LABEL;
  const roomIdArg = room ? Number(room.id) : 'null';
  return `
    <div class="m-topbar">
      <button type="button" class="m-back-btn" onclick="navToTouchCapture(null, null, null)">
        ${lucideIcon('arrowLeft', '', 20)} Rooms
      </button>
      <button type="button" class="btn btn-primary btn-sm" onclick="launchMobileUnitSetup(${roomIdArg})">
        ${lucideIcon('plus', '', 16)} Add Storage Unit
      </button>
    </div>

    <div class="m-step-header">
      <h1 class="m-page-heading">${escapeHtml(roomTitle)}</h1>
      <p class="m-page-sub">Choose a cabinet, dresser, or organizer</p>
    </div>

    ${units.length === 0 ? `
      <div class="m-card" style="text-align: center; padding: 2rem 1.25rem;">
        <p class="m-page-sub">No cabinets or organizers in ${escapeHtml(roomTitle)} yet.</p>
        <button type="button" class="btn btn-primary m-big-btn" style="margin-top: 0.75rem;" onclick="launchMobileUnitSetup(${roomIdArg})">
          ${lucideIcon('plus', '', 18)} Add Storage Unit
        </button>
      </div>
    ` : `
      <div class="m-choice-grid">
        ${units.map(unit => {
          const safeId = Number(unit.id);
          const photo = sanitizePhotoUrl(unit.container_photo || unit.context_photo || '');
          const stats = countPhotographedDrawers(unit);
          const hasChildren = Array.isArray(unit.children) && unit.children.length > 0;
          const allDone = stats.total > 0 && stats.withPhotos === stats.total;
          return `
            <button type="button" class="m-choice-card" onclick="selectTouchUnit(${safeId})">
              <div class="m-choice-thumb">
                ${photo
                  ? `<img src="${escapeHtml(photo)}" alt="${escapeHtml(unit.name)}">`
                  : lucideIcon(hasChildren ? 'grid' : 'archive', '', 28)
                }
              </div>
              <div class="m-choice-info">
                <div class="m-choice-title">${escapeHtml(toSentenceCase(unit.name))}</div>
                <div class="m-choice-meta ${allDone ? 'is-complete' : ''}">
                  ${hasChildren
                    ? `${stats.withPhotos} of ${stats.total} drawers have photos`
                    : (stats.withPhotos > 0 ? '✓ Photo added' : 'No photo yet')
                  }
                </div>
              </div>
              <span class="m-row-chevron" aria-hidden="true">${lucideIcon('chevronRight', '', 22)}</span>
            </button>
          `;
        }).join('')}
      </div>
    `}
  `;
}

function renderTouchDrawerCapturePanel(unit, drawers, drawer, showMiniDiagram = false) {
  if (!drawer) {
    return `
      <div class="m-card m-empty-capture">
        ${lucideIcon('camera', '', 38)}
        <p>Touch any drawer or section on the diagram to add photos.</p>
      </div>
    `;
  }

  const safeDrawerId = Number(drawer.id);
  const idx = drawers.findIndex(d => d.id === drawer.id);
  const hasPrev = idx > 0;
  const hasNext = idx !== -1 && idx + 1 < drawers.length;
  const existingPhotos = getDrawerPhotosList(drawer);
  const pending = touchCaptureState.pendingFiles;
  const isSaving = touchCaptureState.saving;

  return `
    <div class="m-card m-capture-card">
      <div class="m-capture-head">
        <div>
          <div class="m-drawer-step-pill">${drawers.length > 1 ? `Drawer ${idx + 1} of ${drawers.length}` : toSentenceCase(unit.name)}</div>
          <h2 class="m-capture-drawer-title">${escapeHtml(drawer.name)}</h2>
        </div>
      </div>

      ${showMiniDiagram && drawers.length > 1 ? `
        <div class="m-mini-diagram-wrap">
          ${renderMobileCabinetDiagram(unit, drawer, { interactive: true, compact: true })}
        </div>
      ` : ''}

      <div class="m-camera-buttons">
        <label class="m-take-photo-btn">
          <input type="file" accept="image/*" capture="environment" style="display: none;" onchange="handleTouchPhotoPicked(event)">
          ${lucideIcon('camera', '', 28)}
          <span>Take Photo</span>
        </label>
        <label class="m-choose-photo-btn">
          <input type="file" accept="image/*" multiple style="display: none;" onchange="handleTouchPhotoPicked(event)">
          ${lucideIcon('upload', '', 22)}
          <span>Choose from Photos</span>
        </label>
      </div>

      ${pending.length > 0 ? `
        <div class="m-photos-group">
          <div class="m-section-eyebrow">New Photos Ready to Save (${pending.length})</div>
          <div class="m-photo-strip">
            ${pending.map((entry, pIdx) => `
              <div class="m-photo-card is-pending">
                ${entry.previewUrl
                  ? `<img src="${escapeHtml(entry.previewUrl)}" alt="${escapeHtml(entry.name)}">`
                  : `<span>${escapeHtml(entry.name)}</span>`
                }
                <button
                  type="button"
                  class="m-photo-remove-btn"
                  title="Remove photo"
                  aria-label="Remove photo"
                  onclick="removePendingTouchPhoto(${pIdx})"
                >×</button>
              </div>
            `).join('')}
          </div>
        </div>
      ` : ''}

      ${existingPhotos.length > 0 ? `
        <div class="m-photos-group">
          <div class="m-section-eyebrow">Saved Photos (${existingPhotos.length})</div>
          <div class="m-photo-strip">
            ${existingPhotos.map((p, pIdx) => `
              <div class="m-photo-card">
                <img
                  src="${escapeHtml(p.thumb || p.url)}"
                  alt="${escapeHtml(p.caption || `${drawer.name} ${pIdx + 1}`)}"
                  onclick="openLightbox('${escapeJsStr(p.url)}', '${escapeJsStr(p.caption || drawer.name)}')"
                >
                ${p.id ? `
                  <button
                    type="button"
                    class="m-photo-remove-btn"
                    title="Remove photo"
                    aria-label="Remove photo"
                    onclick="event.stopPropagation(); deleteTouchDrawerPhoto(${safeDrawerId}, ${Number(p.id)})"
                  >×</button>
                ` : ''}
              </div>
            `).join('')}
          </div>
        </div>
      ` : ''}

      <div class="touch-save-bar">
        ${pending.length > 0 ? `
          <button
            type="button"
            class="btn btn-primary m-save-next-btn"
            ${isSaving ? 'disabled' : ''}
            onclick="saveTouchDrawerPhotos(true)"
          >
            ${isSaving ? 'Saving...' : (hasNext ? 'Save & Next →' : 'Save & Done ✓')}
          </button>
          <button
            type="button"
            class="btn btn-secondary m-save-stay-btn"
            ${isSaving ? 'disabled' : ''}
            onclick="saveTouchDrawerPhotos(false)"
          >
            Save
          </button>
        ` : (drawers.length > 1 ? `
          <button
            type="button"
            class="btn btn-secondary m-step-nav-btn"
            ${hasPrev ? '' : 'disabled'}
            onclick="stepTouchDrawer(-1)"
          >
            ${lucideIcon('arrowLeft', '', 18)} Previous
          </button>
          <button
            type="button"
            class="btn btn-secondary m-step-nav-btn"
            ${hasNext ? '' : 'disabled'}
            onclick="stepTouchDrawer(1)"
          >
            Next ${lucideIcon('arrowRight', '', 18)}
          </button>
        ` : '')}
      </div>
    </div>
  `;
}

export function renderTouchCaptureView() {
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;
  workspace.classList.remove('is-home-canvas', 'is-unit-view');
  workspace.scrollTop = 0;

  const { roomId, unitId, drawerId } = touchCaptureState;

  if (!roomId) {
    workspace.innerHTML = `<div class="m-page-shell">${renderTouchRoomsStep()}</div>`;
    return;
  }

  const isNeedsHome = roomId === 'needs-home';
  const room = isNeedsHome ? null : state.locationMap.get(roomId);
  if (!isNeedsHome && !room) {
    touchCaptureState.roomId = null;
    workspace.innerHTML = `<div class="m-page-shell">${renderTouchRoomsStep()}</div>`;
    return;
  }

  const units = isNeedsHome ? getUnassignedContainers() : getRoomContainers(room);

  if (!unitId) {
    workspace.innerHTML = `<div class="m-page-shell">${renderTouchUnitsStep(room, units)}</div>`;
    return;
  }

  const unit = state.locationMap.get(unitId);
  if (!unit) {
    touchCaptureState.unitId = null;
    workspace.innerHTML = `<div class="m-page-shell">${renderTouchUnitsStep(room, units)}</div>`;
    return;
  }

  const drawers = getOrderedUnitDrawers(unit);
  const activeDrawer = drawerId ? drawers.find(d => d.id === drawerId) || null : null;
  const phone = isPhoneViewport();

  // Phone View when a Drawer is Selected: Focused Photo Capture + Mini Cabinet Diagram
  if (phone && activeDrawer) {
    const hasSubDrawers = Array.isArray(unit.children) && unit.children.length > 0;
    const backAction = hasSubDrawers
      ? `navToTouchCapture('${escapeJsStr(String(roomId))}', ${Number(unit.id)}, null)`
      : `navToTouchCapture('${escapeJsStr(String(roomId))}', null, null)`;
    const backLabel = hasSubDrawers ? 'Cabinet Diagram' : 'Cabinets';

    workspace.innerHTML = `
      <div class="m-page-shell">
        <div class="m-topbar">
          <button type="button" class="m-back-btn" onclick="${backAction}">
            ${lucideIcon('arrowLeft', '', 20)} ${escapeHtml(backLabel)}
          </button>
          <span class="m-topbar-unit-name">${escapeHtml(toSentenceCase(unit.name))}</span>
        </div>
        ${renderTouchDrawerCapturePanel(unit, drawers, activeDrawer, true)}
      </div>
    `;
    return;
  }

  // Phone Cabinet Diagram Picker OR Tablet Side-by-Side Split View
  workspace.innerHTML = `
    <div class="m-page-shell ${phone ? '' : 'is-tablet-split'}">
      <div class="m-topbar">
        <button type="button" class="m-back-btn" onclick="navToTouchCapture('${escapeJsStr(String(roomId))}', null, null)">
          ${lucideIcon('arrowLeft', '', 20)} Cabinets
        </button>
      </div>

      <div class="m-step-header">
        <h1 class="m-page-heading">${escapeHtml(toSentenceCase(unit.name))}</h1>
        <p class="m-page-sub">Touch a drawer or section below to add photos</p>
      </div>

      <div class="touch-split-body">
        <div class="touch-split-left">
          ${renderMobileCabinetDiagram(unit, activeDrawer, { interactive: true, compact: false })}
        </div>
        ${!phone ? `
          <div class="touch-split-right">
            ${renderTouchDrawerCapturePanel(unit, drawers, activeDrawer, false)}
          </div>
        ` : ''}
      </div>
    </div>
  `;
}
