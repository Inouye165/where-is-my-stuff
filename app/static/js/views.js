/**
 * Workspace Views & Item Slide-Drawer Renderer (XSS-Hardened)
 */
import { apiDelete, apiGet, apiPatch, apiPost, apiUpload } from './api.js';
import {
  closeModal,
  escapeHtml,
  escapeJsStr,
  formatDate,
  pluralize,
  sanitizePhotoUrl,
  showToast,
  withSubmitLock
} from './dom.js';
import { getRoomIconName, lucideIcon } from './icons.js';
import {
  isInlineGridEditing,
  mountInlineGridDesigner,
  renderContainerGridHtml,
  renderInlineGridDesignerHtml
} from './grid-designer.js';
import { chooseAddAction, openAddItemModal, openQuickPhotoModal } from './modals.js';
import { registerDesktopPhotoHandler, requestPhotoForInput } from './phone-capture.js';
import { openPhotoEditorModal } from './photo-editor.js';
import { handleGlobalBack, navToHome, navToItem, navToRoom, renderCurrentWorkspace } from './router.js';
import {
  formatHumanPath,
  getLocationHierarchy,
  getLocType,
  getQuickMoveRoomChoices,
  getRoomEmoji,
  getStarterSuggestions,
  getUnassignedContainers,
  getUnassignedLooseItems,
  isUnknownPlaceholder,
  loadAllData,
  saveJsonStorage,
  state,
  UNASSIGNED_ROOM_LABEL
} from './state.js';

export const HOME_SEARCH_HINTS = [
  'Example: "something to cut cardboard"',
  'Example: "usb charger" or "battery"',
  'Example: "drill" or "tape"',
  'Example: "cooling fan" or "switch"'
];

const SEARCH_STOP_WORDS = new Set([
  'a', 'an', 'the', 'to', 'for', 'with', 'in', 'on', 'of', 'by', 'at',
  'something', 'anything', 'thing', 'item', 'stuff', 'object', 'part',
  'that', 'which', 'used', 'my', 'i', 'need', 'find', 'looking', 'is',
  'it', 'are', 'do', 'does', 'can', 'you', 'me', 'some', 'get'
]);

const SEARCH_SYNONYMS = {
  cut: ['knife', 'blade', 'cutter', 'scissors', 'shear', 'saw'],
  cardboard: ['box', 'utility', 'cutter', 'knife'],
  tape: ['adhesive', 'masking', 'duct', 'packing'],
  light: ['led', 'lamp', 'flashlight', 'bulb', 'torch'],
  power: ['battery', 'charger', 'converter', 'supply', 'usb', 'adapter'],
  wire: ['cable', 'lead', 'jumper', 'cord', 'harness'],
  screw: ['bolt', 'nut', 'fastener', 'driver'],
  glue: ['adhesive', 'epoxy', 'cement', 'tape'],
  hot: ['heat', 'rework', 'solder', 'thermal'],
  charge: ['charger', 'charging', 'tp4056', 'usb', 'battery']
};

function tokenizePlainSearch(query) {
  const raw = query.toLowerCase().split(/[^a-z0-9\-_.]+/).filter(Boolean);
  const filtered = raw.filter(t => !SEARCH_STOP_WORDS.has(t) && t.length > 1);
  const base = filtered.length > 0 ? filtered : raw;
  const expanded = new Set(base);
  for (const tok of base) {
    const syns = SEARCH_SYNONYMS[tok];
    if (syns) syns.forEach(s => expanded.add(s));
  }
  return Array.from(expanded);
}

// ============================================================================
// 1. ZERO-SCROLL HOME VIEW ('Places') + HERO SEARCH + 'NEEDS A HOME' TILE
// ============================================================================
export function renderHomePlacesView() {
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;
  workspace.classList.add('is-home-canvas');

  const totalThings = pluralize(state.items.length, 'item');
  const totalContainers = pluralize(state.containers.length, 'container');
  const currentHint = HOME_SEARCH_HINTS[state.homeSearchHintIndex % HOME_SEARCH_HINTS.length];
  const hasSearchOpen = Boolean(state.homeSearchQuery || state.photoSearchPreviewUrl || state.photoSearchLoading);

  workspace.innerHTML = `
    <div class="home-canvas-inner">
      <div class="home-top-bar">
        <div class="home-search-header">
          <div class="hero-search-bar">
            ${lucideIcon('search', 'hero-search-icon', 20)}
            <input
              type="text"
              id="home-search-input"
              class="hero-search-input"
              placeholder="What are you looking for?"
              value="${escapeHtml(state.homeSearchQuery)}"
              autocomplete="off"
              oninput="handleHomeSearchInput(event)"
            >
            <button
              type="button"
              id="home-search-clear-btn"
              class="hero-search-clear"
              style="display: ${hasSearchOpen ? 'inline-flex' : 'none'};"
              onclick="clearHomeSearch()"
              aria-label="Clear search"
            >
              ${lucideIcon('x', '', 16)}
            </button>
            <button
              type="button"
              class="hero-camera-btn"
              onclick="openHomeCameraSnap()"
              title="Search by photo"
              aria-label="Search by photo"
            >
              ${lucideIcon('camera', '', 20)}
            </button>
          </div>
          <div id="home-search-panel" class="home-search-panel ${hasSearchOpen ? 'open' : ''}"></div>
        </div>
        <div class="home-meta-row">
          <div class="hero-search-examples" id="home-search-subtext">${escapeHtml(currentHint)}</div>
          <div class="home-total-line" id="home-total-line">${escapeHtml(totalThings)} in ${escapeHtml(totalContainers)}</div>
        </div>
      </div>

      <div class="home-main-columns">
        <div id="home-stage-body" class="home-rooms-stage"></div>
        <aside id="home-right-col" class="home-right-col" aria-label="Recently added"></aside>
      </div>
    </div>
  `;

  renderHomeStageBody();
  renderHomeSearchPanel();
  renderHomeRightColumn();
}

export function handleHomeSearchInput(e) {
  const val = e.target.value || '';
  state.homeSearchQuery = val.trim();
  if (!state.homeSearchQuery && !state.photoSearchPreviewUrl) {
    state.photoSearchLoading = false;
  }

  const clearBtn = document.getElementById('home-search-clear-btn');
  if (clearBtn) {
    clearBtn.style.display = (val.length > 0 || state.photoSearchPreviewUrl) ? 'inline-flex' : 'none';
  }

  renderHomeSearchPanel();
}

export function clearHomeSearch() {
  state.homeSearchQuery = '';
  if (state.photoSearchPreviewUrl) {
    try {
      URL.revokeObjectURL(state.photoSearchPreviewUrl);
    } catch { }
  }
  state.photoSearchPreviewUrl = '';
  state.photoSearchLoading = false;

  const fileInput = document.getElementById('home-photo-search-input');
  if (fileInput) fileInput.value = '';

  const input = document.getElementById('home-search-input');
  if (input) {
    input.value = '';
    input.focus();
  }
  const clearBtn = document.getElementById('home-search-clear-btn');
  if (clearBtn) clearBtn.style.display = 'none';
  renderHomeSearchPanel();
}

export function editExistingPhoto({ kind, id, photoUrl, title = 'Crop & Rotate' }) {
  const safeUrl = sanitizePhotoUrl(photoUrl);
  if (!safeUrl || !id) return;
  openPhotoEditorModal({
    imageUrl: safeUrl,
    title,
    onApply: async (uploaded) => {
      if (kind === 'item') {
        await apiPatch(`/api/v1/items/${id}`, { item_photo: uploaded.original_path }, 'Could not update photo.');
      } else {
        const body = kind === 'spot' ? { context_photo: uploaded.original_path } : { container_photo: uploaded.original_path };
        await apiPatch(`/api/v1/locations/${id}`, body, 'Could not update photo.');
      }
      showToast('Photo updated!');
      await loadAllData();
      if (state.activeDrawerItem && kind === 'item') {
        openItemDrawer(state.activeDrawerItem.id);
      }
    },
  });
}

export function openHomeCameraSnap() {
  const fileInput = document.getElementById('home-photo-search-input');
  if (fileInput) {
    requestPhotoForInput('home-photo-search-input');
  } else {
    chooseAddAction('snap');
  }
}

registerDesktopPhotoHandler('home-photo-search-input', {
  label: 'Search by photo',
  onPhotoReceived: async (photo) => {
    const safeUrl = sanitizePhotoUrl(photo.original_path);
    if (!safeUrl) return;
    try {
      const resp = await fetch(safeUrl);
      const blob = await resp.blob();
      const file = new File([blob], photo.filename || 'phone-photo.jpg', {
        type: blob.type || 'image/jpeg',
      });
      await handleHomePhotoSearch({ target: { files: [file] } });
    } catch {
      showToast('Could not load photo from phone.', 'error');
    }
  },
});

export async function handleHomePhotoSearch(e) {
  const file = e && e.target && e.target.files && e.target.files[0];
  if (!file) return;

  if (state.photoSearchPreviewUrl) {
    try {
      URL.revokeObjectURL(state.photoSearchPreviewUrl);
    } catch { }
  }
  state.photoSearchPreviewUrl = URL.createObjectURL(file);
  state.photoSearchLoading = true;

  const clearBtn = document.getElementById('home-search-clear-btn');
  if (clearBtn) clearBtn.style.display = 'inline-flex';
  renderHomeSearchPanel();

  try {
    const fd = new FormData();
    fd.append('file', file);
    const res = await apiUpload('/api/v1/search/photo', fd, 'Could not search by photo.');
    state.photoSearchLoading = false;
    if (res && res.query) {
      state.homeSearchQuery = res.query;
      const input = document.getElementById('home-search-input');
      if (input) input.value = res.query;
    }
    renderHomeSearchPanel();
  } catch (err) {
    state.photoSearchLoading = false;
    showToast(err.message || 'Photo search failed.', 'error');
    renderHomeSearchPanel();
  }
}

function buildHomeTiles() {
  const unassignedContainers = getUnassignedContainers();
  const unassignedItems = getUnassignedLooseItems();
  const hasNeedsHome = unassignedContainers.length > 0 || unassignedItems.length > 0;

  let needsHomeBanner = null;
  if (hasNeedsHome) {
    let totalItemsInUnassigned = unassignedItems.length;
    for (const c of unassignedContainers) {
      totalItemsInUnassigned += c.total_item_count || 0;
    }
    const subtitle = unassignedContainers.length > 0
      ? `${pluralize(totalItemsInUnassigned, 'item')} in ${pluralize(unassignedContainers.length, 'container')}`
      : `${pluralize(unassignedItems.length, 'item')} to place`;

    needsHomeBanner = {
      count: unassignedContainers.length > 0 ? unassignedContainers.length : unassignedItems.length,
      subtitle
    };
  }

  const populatedTiles = [];
  const emptyTiles = [];

  for (const room of state.namedRooms) {
    const containersInRoom = (room.children || []).filter(
      c => getLocType(c) === 'container' || (c.children && c.children.length > 0)
    );
    const containerCount = containersInRoom.length;
    const itemCount = room.total_item_count || 0;
    const photo = sanitizePhotoUrl(room.context_photo || room.container_photo || '');

    if (containerCount === 0 && itemCount === 0) {
      emptyTiles.push({
        kind: 'empty-room',
        id: room.id,
        name: room.name,
        iconName: getRoomIconName(room)
      });
    } else {
      populatedTiles.push({
        kind: 'room',
        id: room.id,
        name: room.name,
        iconName: getRoomIconName(room),
        photo,
        itemCount,
        containerCount,
        subtitle: `${pluralize(itemCount, 'item')} · ${pluralize(containerCount, 'container')}`
      });
    }
  }

  populatedTiles.sort((a, b) => {
    if (b.itemCount !== a.itemCount) return b.itemCount - a.itemCount;
    return b.containerCount - a.containerCount;
  });

  const allTiles = [...populatedTiles, ...emptyTiles];
  if (allTiles.length === 0) {
    allTiles.push({ kind: 'add-room' }, { kind: 'add-item' });
  } else if (allTiles.length < 6) {
    allTiles.push({ kind: 'add-room' });
  }

  const pages = [];
  let rest = allTiles.slice();
  while (rest.length > 0) {
    pages.push(rest.slice(0, 6));
    rest = rest.slice(6);
  }
  if (pages.length === 0) {
    pages.push([{ kind: 'add-room' }, { kind: 'add-item' }]);
  }

  return { needsHomeBanner, pages };
}

export function setHomePage(idx) {
  state.homePage = idx;
  renderHomeStageBody();
}

export function renderHomeStageBody() {
  const stageBody = document.getElementById('home-stage-body');
  if (!stageBody) return;

  const { needsHomeBanner, pages } = buildHomeTiles();
  if (state.homePage >= pages.length) state.homePage = 0;
  const activeTiles = pages[state.homePage] || [];

  stageBody.innerHTML = `
    ${needsHomeBanner ? `
      <div class="needs-home-banner" onclick="navToNeedsHome(true)" role="button" tabindex="0">
        <div class="needs-home-banner-left">
          <span class="needs-home-pill">${Number(needsHomeBanner.count) || 0}</span>
          <div class="needs-home-banner-text">
            <h2 class="needs-home-banner-title">Needs a home</h2>
            <span class="needs-home-banner-sub">${escapeHtml(needsHomeBanner.subtitle)}</span>
          </div>
        </div>
        <span class="needs-home-banner-cta">Assign rooms ${lucideIcon('chevronRight', '', 16)}</span>
      </div>
    ` : ''}
    <div class="home-rooms-grid">
      ${activeTiles.map(tile => renderHomeTileHtml(tile)).join('')}
    </div>
    ${pages.length > 1 ? `
      <div class="home-page-dots" role="tablist" aria-label="More rooms">
        ${pages.map((_, idx) => `
          <button
            type="button"
            class="page-dot-btn ${idx === state.homePage ? 'active' : ''}"
            onclick="setHomePage(${idx})"
          >
            Page ${idx + 1}
          </button>
        `).join('')}
      </div>
    ` : ''}
  `;
}

function renderHomeTileHtml(tile) {
  if (tile.kind === 'add-room') {
    const sub = state.namedRooms.length === 0 ? 'No rooms added yet' : 'Create a new space';
    return `
      <div class="room-tile add-room-tile" onclick="openAddStorageModal(null, 'room')" role="button" tabindex="0">
        <div class="room-tile-top"></div>
        <div class="room-tile-center-icon">
          ${lucideIcon('plus', '', 38)}
        </div>
        <div class="room-tile-bottom">
          <h2 class="room-tile-name">Add a room</h2>
          <span class="room-tile-sub">${escapeHtml(sub)}</span>
        </div>
      </div>
    `;
  }

  if (tile.kind === 'add-item') {
    const sub = state.items.length === 0 ? 'No items added yet' : 'Add something new';
    return `
      <div class="room-tile add-room-tile" onclick="chooseAddAction()" role="button" tabindex="0">
        <div class="room-tile-top"></div>
        <div class="room-tile-center-icon">
          ${lucideIcon('package', '', 38)}
        </div>
        <div class="room-tile-bottom">
          <h2 class="room-tile-name">Add an item</h2>
          <span class="room-tile-sub">${escapeHtml(sub)}</span>
        </div>
      </div>
    `;
  }

  if (tile.kind === 'starter') {
    const safeName = escapeJsStr(tile.name);
    const safeEmoji = escapeJsStr(tile.emoji || '🏠');
    return `
      <div class="room-tile is-dimmed-room" onclick="openStarterRoom('${safeName}', '${safeEmoji}')" role="button" tabindex="0">
        <div class="room-tile-center-icon">
          ${lucideIcon(tile.iconName || 'home', '', 38)}
        </div>
        <div class="room-tile-top">
          <button
            type="button"
            class="room-tile-edit-btn"
            title="Remove ${escapeHtml(tile.name)}"
            aria-label="Remove ${escapeHtml(tile.name)}"
            onclick="event.stopPropagation(); dismissStarterRoom('${safeName}')"
          >
            ${lucideIcon('x', '', 15)}
          </button>
          <button
            type="button"
            class="room-tile-plus-btn"
            title="Add to ${escapeHtml(tile.name)}"
            aria-label="Add to ${escapeHtml(tile.name)}"
            onclick="event.stopPropagation(); openStarterRoom('${safeName}', '${safeEmoji}')"
          >
            ${lucideIcon('plus', '', 16)}
          </button>
        </div>
        <div class="room-tile-bottom">
          <h2 class="room-tile-name">${escapeHtml(tile.name)}</h2>
          <span class="room-tile-sub">Nothing here yet</span>
        </div>
      </div>
    `;
  }

  if (tile.kind === 'empty-room') {
    const safeId = Number(tile.id);
    return `
      <div class="room-tile is-dimmed-room" onclick="navToRoom(${safeId}, true)" role="button" tabindex="0">
        <div class="room-tile-center-icon">
          ${lucideIcon(tile.iconName || 'home', '', 38)}
        </div>
        <div class="room-tile-top">
          <button
            type="button"
            class="room-tile-edit-btn"
            title="Edit or remove ${escapeHtml(tile.name)}"
            aria-label="Edit or remove ${escapeHtml(tile.name)}"
            onclick="event.stopPropagation(); openEditStorageModal(${safeId})"
          >
            ${lucideIcon('pencil', '', 15)}
          </button>
          <button
            type="button"
            class="room-tile-plus-btn"
            title="Add container to ${escapeHtml(tile.name)}"
            aria-label="Add container to ${escapeHtml(tile.name)}"
            onclick="event.stopPropagation(); openAddStorageModal(${safeId}, 'unit')"
          >
            ${lucideIcon('plus', '', 16)}
          </button>
        </div>
        <div class="room-tile-bottom">
          <h2 class="room-tile-name">${escapeHtml(tile.name)}</h2>
          <span class="room-tile-sub">Nothing here yet</span>
        </div>
      </div>
    `;
  }

  const safePhoto = sanitizePhotoUrl(tile.photo);
  const hasPhoto = Boolean(safePhoto);
  const safeId = Number(tile.id);

  return `
    <div class="room-tile ${hasPhoto ? 'has-photo' : 'no-photo'}" onclick="navToRoom(${safeId}, true)" role="button" tabindex="0">
      ${hasPhoto ? `
        <img class="room-tile-bg-photo" src="${escapeHtml(safePhoto)}" alt="${escapeHtml(tile.name)}" loading="lazy">
        <div class="room-tile-scrim"></div>
      ` : `
        <div class="room-tile-center-icon">
          ${lucideIcon(tile.iconName || 'home', '', 44)}
        </div>
      `}
      <div class="room-tile-top">
        ${!hasPhoto ? `
          <button
            type="button"
            class="room-tile-add-photo-btn"
            onclick="event.stopPropagation(); openQuickPhotoModal({ kind: 'spot', id: ${safeId}, title: 'Add a photo' })"
          >
            ${lucideIcon('camera', '', 14)}
            <span>Add a photo</span>
          </button>
        ` : '<span></span>'}
        <button
          type="button"
          class="room-tile-edit-btn"
          title="Edit room"
          aria-label="Edit ${escapeHtml(tile.name)}"
          onclick="event.stopPropagation(); openEditStorageModal(${safeId})"
        >
          ${lucideIcon('pencil', '', 15)}
        </button>
      </div>
      <div class="room-tile-bottom">
        <h2 class="room-tile-name">${escapeHtml(tile.name)}</h2>
        <span class="room-tile-sub">${escapeHtml(tile.subtitle)}</span>
      </div>
    </div>
  `;
}

export async function ensureRoomExists(roomName, emoji = '🏠') {
  const existing = state.namedRooms.find(r => r.name.trim().toLowerCase() === roomName.trim().toLowerCase());
  if (existing) return existing;

  const created = await apiPost(
    '/api/v1/locations',
    { name: roomName.trim(), location_type: 'room' },
    'Could not create room.'
  );
  state.roomEmojis[created.id] = emoji;
  state.roomEmojis[created.name.toLowerCase()] = emoji;
  saveJsonStorage('wims_room_emojis', state.roomEmojis);
  return created;
}

export async function openStarterRoom(roomName, emoji) {
  try {
    const room = await ensureRoomExists(roomName, emoji);
    await loadAllData();
    navToRoom(room.id, true);
  } catch (err) {
    showToast(err.message, 'error');
  }
}

export function dismissStarterRoom(roomName) {
  if (!state.dismissedStarters.includes(roomName)) {
    state.dismissedStarters.push(roomName);
    saveJsonStorage('wims_dismissed_starters', state.dismissedStarters);
  }
  renderHomeStageBody();
}

export function renderHomeSearchPanel() {
  const panel = document.getElementById('home-search-panel');
  if (!panel) return;

  const query = (state.homeSearchQuery || '').trim();
  const hasPhoto = Boolean(state.photoSearchPreviewUrl);
  if (!query && !hasPhoto && !state.photoSearchLoading) {
    panel.classList.remove('open');
    panel.innerHTML = '';
    return;
  }

  panel.classList.add('open');

  if (state.photoSearchLoading) {
    panel.innerHTML = `
      <div class="search-panel-photo-bar">
        ${hasPhoto ? `<img class="search-panel-preview-img" src="${escapeHtml(state.photoSearchPreviewUrl)}" alt="Photo search">` : ''}
        <div class="search-panel-status">Looking for matching items...</div>
      </div>
    `;
    return;
  }

  const tokens = query ? tokenizePlainSearch(query) : [];
  const matchingItems = tokens.length > 0
    ? state.items.filter(item => {
      const hier = getLocationHierarchy(item.location_id);
      const haystack = [
        item.name,
        item.serial_or_part_number,
        item.category,
        item.tags,
        item.description,
        hier.searchPath
      ].filter(Boolean).join(' ').toLowerCase();
      return tokens.some(tok => haystack.includes(tok));
    })
    : [];

  const matchingLocations = query
    ? Array.from(state.locationMap.values()).filter(loc => {
      if (isUnknownPlaceholder(loc)) return false;
      const nameLow = loc.name.toLowerCase();
      return tokens.some(tok => nameLow.includes(tok));
    })
    : [];

  const photoHeader = hasPhoto
    ? `
      <div class="search-panel-photo-bar">
        <img class="search-panel-preview-img" src="${escapeHtml(state.photoSearchPreviewUrl)}" alt="Searched photo">
        <div class="search-panel-status">
          ${query ? `Matched keywords: <strong>${escapeHtml(query)}</strong>` : 'Type what is in this photo above to search'}
        </div>
      </div>
    `
    : '';

  if (matchingItems.length === 0 && matchingLocations.length === 0) {
    panel.innerHTML = `
      ${photoHeader}
      <div class="search-panel-empty">
        <div class="search-panel-empty-text">
          No match${query ? ` for "${escapeHtml(query)}"` : ''}.
        </div>
        <button type="button" class="btn btn-primary btn-sm" onclick="openAddItemWithPrefill('${escapeJsStr(query)}')">
          ${lucideIcon('plus', '', 15)} Add it
        </button>
      </div>
    `;
    return;
  }

  panel.innerHTML = `
    ${photoHeader}
    <div class="search-panel-list">
      ${matchingItems.slice(0, 12).map(item => {
    const hier = getLocationHierarchy(item.location_id);
    const safePhoto = sanitizePhotoUrl(item.item_photo);
    const safeItemId = Number(item.id);
    return `
          <button type="button" class="search-result-row" onclick="navToItem(${safeItemId})">
            <span class="search-result-thumb">
              ${safePhoto
        ? `<img src="${escapeHtml(safePhoto)}" alt="${escapeHtml(item.name)}" loading="lazy">`
        : lucideIcon('package', '', 20)
      }
            </span>
            <span class="search-result-info">
              <span class="search-result-name">${escapeHtml(item.name)}</span>
              <span class="search-result-place">${escapeHtml(hier.searchPath)}</span>
            </span>
          </button>
        `;
  }).join('')}
      ${matchingLocations.slice(0, 5).map(loc => {
    const ltype = getLocType(loc);
    const safeLocId = Number(loc.id);
    const clickAction =
      ltype === 'room' || ltype === 'site'
        ? `navToRoom(${safeLocId}, true)`
        : ltype === 'container'
          ? `navToStorageUnit(${safeLocId}, true)`
          : `navToDrawer(${safeLocId}, true)`;
    const placeStr = formatHumanPath(loc.full_path).replace(/\s*›\s*/g, ' > ');
    return `
          <button type="button" class="search-result-row" onclick="${clickAction}">
            <span class="search-result-thumb">${lucideIcon(getRoomIconName(loc), '', 20)}</span>
            <span class="search-result-info">
              <span class="search-result-name">${escapeHtml(loc.name)}</span>
              <span class="search-result-place">${escapeHtml(placeStr)}</span>
            </span>
            <span class="drawer-badge-count">${pluralize(loc.total_item_count || 0, 'item')}</span>
          </button>
        `;
  }).join('')}
    </div>
  `;
}

export function renderHomeRightColumn() {
  const rightCol = document.getElementById('home-right-col');
  if (!rightCol) return;

  const recentItems = state.items
    .slice()
    .sort((a, b) => {
      const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
      const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
      if (tb !== ta) return tb - ta;
      return (b.id || 0) - (a.id || 0);
    })
    .slice(0, 6);

  const renderMiniItemRow = item => {
    const hier = getLocationHierarchy(item.location_id);
    const safePhoto = sanitizePhotoUrl(item.item_photo);
    const safeItemId = Number(item.id);
    return `
      <button type="button" class="right-col-item-row" onclick="navToItem(${safeItemId})">
        <span class="right-col-thumb">
          ${safePhoto
        ? `<img src="${escapeHtml(safePhoto)}" alt="${escapeHtml(item.name)}" loading="lazy">`
        : lucideIcon('package', '', 16)
      }
        </span>
        <span class="right-col-item-text">
          <span class="right-col-item-name">${escapeHtml(item.name)}</span>
          <span class="right-col-item-place">${escapeHtml(hier.searchPath)}</span>
        </span>
      </button>
    `;
  };

  rightCol.innerHTML = `
    <section class="right-col-card">
      <h3 class="right-col-heading">Recently added</h3>
      ${recentItems.length > 0
      ? `<div class="right-col-list">${recentItems.map(renderMiniItemRow).join('')}</div>`
      : `<p class="right-col-empty">No items added yet. Items you add will show up here.</p>`
    }
    </section>
  `;
}

export function openAddItemWithPrefill(nameText) {
  openAddItemModal(null, null);
  const nameInput = document.getElementById('item-name');
  if (nameInput) nameInput.value = nameText;
}

// ============================================================================
// 2. 'NEEDS A HOME' VIEW & 1-CONTAINER-AT-A-TIME ROOM ASSIGNMENT
// ============================================================================
export function skipNeedsHomeContainer(total) {
  if (total > 1) {
    state.needsHomeIndex = (state.needsHomeIndex + 1) % total;
    renderNeedsHomeView();
  }
}

export function renderNeedsHomeView() {
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;
  workspace.classList.remove('is-home-canvas');
  workspace.scrollTop = 0;

  const unassignedContainers = getUnassignedContainers();
  const unassignedItems = getUnassignedLooseItems();

  if (unassignedContainers.length === 0 && unassignedItems.length === 0) {
    state.needsHomeIndex = 0;
    workspace.innerHTML = `
      <div class="workspace-inner">
        ${renderStickyContextBar([{ label: UNASSIGNED_ROOM_LABEL }])}
        <div class="empty-state">
          <h3 class="empty-state-title">Everything has a home</h3>
          <p class="empty-state-desc">All of your containers and items are in a room.</p>
          <button type="button" class="btn btn-primary" onclick="navToHome()">Back to Places</button>
        </div>
      </div>
    `;
    return;
  }

  const roomChoices = getQuickMoveRoomChoices();

  if (unassignedContainers.length > 0) {
    if (state.needsHomeIndex >= unassignedContainers.length) {
      state.needsHomeIndex = 0;
    }
    const current = unassignedContainers[state.needsHomeIndex];
    const safeId = Number(current.id);
    const photo = sanitizePhotoUrl(current.container_photo || current.context_photo || '');
    const itemCount = current.total_item_count || 0;

    workspace.innerHTML = `
      <div class="workspace-inner">
        ${renderStickyContextBar([{ label: UNASSIGNED_ROOM_LABEL }], `
          <span class="needs-home-progress">${state.needsHomeIndex + 1} of ${unassignedContainers.length}</span>
        `)}

        <div class="assign-flow-card">
          <div class="assign-flow-container">
            <div class="assign-flow-thumb">
              ${photo
        ? `<img src="${escapeHtml(photo)}" alt="${escapeHtml(current.name)}">`
        : lucideIcon('package', '', 40)
      }
            </div>
            <div class="assign-flow-meta">
              <h1 class="assign-flow-name">${escapeHtml(current.name)}</h1>
              <p class="assign-flow-count">${pluralize(itemCount, 'item')} inside</p>
            </div>
          </div>

          <h2 class="assign-flow-question">Where does this live?</h2>

          <div class="assign-room-buttons">
            ${roomChoices.map(r => `
              <button
                type="button"
                class="assign-room-btn"
                onclick="quickMoveContainerToRoom(${safeId}, ${r.id ? Number(r.id) : 'null'}, '${escapeJsStr(r.name)}', '${escapeJsStr(r.emoji)}')"
              >
                ${lucideIcon(getRoomIconName(r.name), '', 22)}
                <span>${escapeHtml(r.name)}</span>
              </button>
            `).join('')}
            <button
              type="button"
              class="assign-room-btn assign-room-btn-new"
              onclick="openAddStorageModal(null, 'room')"
            >
              ${lucideIcon('plus', '', 22)}
              <span>New room</span>
            </button>
          </div>

          <div class="assign-flow-footer">
            <button type="button" class="btn btn-secondary btn-sm" onclick="navToStorageUnit(${safeId}, true)">
              Open container
            </button>
            ${unassignedContainers.length > 1 ? `
              <button type="button" class="btn btn-secondary btn-sm" onclick="skipNeedsHomeContainer(${unassignedContainers.length})">
                Skip for now
              </button>
            ` : ''}
          </div>
        </div>
      </div>
    `;
    return;
  }

  workspace.innerHTML = `
    <div class="workspace-inner">
      ${renderStickyContextBar([{ label: UNASSIGNED_ROOM_LABEL }])}
      <div class="detail-section">
        <span class="detail-section-title">Loose Items to Place (${unassignedItems.length})</span>
        <div class="items-grid">
          ${unassignedItems.map(item => renderItemCard(item)).join('')}
        </div>
      </div>
    </div>
  `;
}

export async function quickMoveContainerToRoom(containerId, roomId, roomName, emoji) {
  try {
    let targetRoomId = roomId;
    if (!targetRoomId) {
      const created = await ensureRoomExists(roomName, emoji || '🏠');
      targetRoomId = created.id;
    }

    const container = state.locationMap.get(containerId);
    await apiPatch(
      `/api/v1/locations/${containerId}`,
      { parent_id: targetRoomId },
      'Could not move container.'
    );

    showToast(`Moved "${container ? container.name : 'Container'}" to ${roomName}`);
    await loadAllData();

    if (
      state.currentView === 'needs-home' &&
      getUnassignedContainers().length === 0 &&
      getUnassignedLooseItems().length === 0
    ) {
      navToHome();
    }
  } catch (err) {
    showToast(err.message, 'error');
  }
}

// ============================================================================
// 3. CLEAN DRILL-DOWN VIEWS (ROOM -> CONTAINER -> DRAWER)
// ============================================================================
function renderStickyContextBar(crumbs, rightActionsHtml = '') {
  return `
    <div class="sticky-context-bar no-print">
      <div class="context-crumbs">
        <button type="button" class="context-back-btn" onclick="handleGlobalBack()">
          ← Back
        </button>
        <button type="button" class="context-crumb-link" onclick="navToHome()">Places</button>
        ${crumbs.map((crumb, idx) => {
    const isLast = idx === crumbs.length - 1;
    return `
            <span class="context-sep">/</span>
            ${isLast || !crumb.onclick
        ? `<span class="context-crumb-current">${escapeHtml(crumb.label)}</span>`
        : `<button type="button" class="context-crumb-link" onclick="${crumb.onclick}">${escapeHtml(crumb.label)}</button>`
      }
          `;
  }).join('')}
      </div>
      ${rightActionsHtml ? `<div class="context-actions">${rightActionsHtml}</div>` : ''}
    </div>
  `;
}

export function renderRoomView(roomId) {
  const room = state.locationMap.get(roomId);
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;
  workspace.classList.remove('is-home-canvas');
  workspace.scrollTop = 0;

  if (!room) {
    workspace.innerHTML = `<div class="empty-state"><p>Room not found.</p><button class="btn btn-secondary btn-sm" onclick="navToHome()">Back to Places</button></div>`;
    return;
  }

  const safeRoomId = Number(room.id);
  const children = room.children || [];
  const subRooms = children.filter(c => getLocType(c) === 'room' || getLocType(c) === 'site');
  const containers = children.filter(
    c => !subRooms.includes(c) && (getLocType(c) === 'container' || (c.children && c.children.length > 0))
  );
  const looseBins = children.filter(c => !subRooms.includes(c) && !containers.includes(c));
  const directItems = state.items.filter(i => i.location_id === room.id);
  const roomPhoto = sanitizePhotoUrl(room.context_photo || room.container_photo || '');
  const yellowContainers = containers.filter(c => c.name.toLowerCase().includes('yellow'));
  const regularContainers = containers.filter(c => !c.name.toLowerCase().includes('yellow'));
  const unassignedCount = getUnassignedContainers().length;

  const parentRoom = room.parent_id ? state.locationMap.get(room.parent_id) : null;
  const crumbs = [];
  if (parentRoom && !isUnknownPlaceholder(parentRoom)) {
    crumbs.push({ label: parentRoom.name, onclick: `navToRoom(${Number(parentRoom.id)}, true)` });
  }
  crumbs.push({ label: room.name });

  workspace.innerHTML = `
    <div class="workspace-inner">
      ${renderStickyContextBar(
    crumbs,
    `
          <button type="button" class="btn btn-secondary btn-sm" onclick="openQuickPhotoModal({ kind: 'spot', id: ${safeRoomId}, title: 'Add room photo' })">
            ${lucideIcon('camera', '', 15)} ${roomPhoto ? 'Change photo' : 'Add room photo'}
          </button>
          ${roomPhoto ? `<button type="button" class="btn btn-secondary btn-sm" onclick="editExistingPhoto({ kind: 'spot', id: ${safeRoomId}, photoUrl: '${escapeJsStr(roomPhoto)}', title: 'Edit room photo' })">${lucideIcon('scissors', '', 15)} Crop</button>` : ''}
          <button type="button" class="btn btn-secondary btn-sm" onclick="openEditStorageModal(${safeRoomId})">Edit</button>
          <button type="button" class="btn btn-primary btn-sm" onclick="openAddStorageModal(${safeRoomId}, 'unit')">+ Add container</button>
        `
  )}

      <div class="place-hero-card">
        <div class="place-hero-left">
          ${roomPhoto
      ? `<img class="place-hero-thumb" src="${escapeHtml(roomPhoto)}" alt="${escapeHtml(room.name)}" onclick="openLightbox('${escapeJsStr(roomPhoto)}', '${escapeJsStr(room.name)}')">`
      : `<div class="place-hero-emoji">${escapeHtml(getRoomEmoji(room))}</div>`
    }
          <div>
            <h1 class="place-hero-title">${escapeHtml(room.name)}</h1>
            <p class="place-hero-sub">
              ${pluralize(containers.length, 'container')} · ${pluralize(room.total_item_count || 0, 'item')}
              ${room.description ? ` · ${escapeHtml(room.description)}` : ''}
            </p>
          </div>
        </div>
      </div>

      ${subRooms.length > 0 ? `
        <div class="containers-grid">
          ${subRooms.map(sr => {
      const safeSubId = Number(sr.id);
      const subItemCount = sr.total_item_count || 0;
      const subChildCount = (sr.children && sr.children.length) || sr.child_count || 0;
      return `
              <div class="container-card" onclick="navToRoom(${safeSubId}, true)">
                <div class="container-card-body">
                  <div class="container-card-header">
                    <h4 class="container-card-title">${escapeHtml(sr.name)}</h4>
                    <span class="drawer-badge-count">${pluralize(subItemCount, 'item')}</span>
                  </div>
                  <p class="container-card-meta">${pluralize(subChildCount, 'container')}</p>
                </div>
              </div>
            `;
    }).join('')}
        </div>
      ` : ''}

      ${yellowContainers.length > 0 ? `
        <div class="yellow-group-box">
          <div class="yellow-group-label">⭐ Yellow Organizer System (${yellowContainers.length})</div>
          <div class="containers-grid">
            ${yellowContainers.map(c => renderContainerCard(c, false)).join('')}
          </div>
        </div>
      ` : ''}

      ${regularContainers.length > 0 ? `
        <div class="containers-grid">
          ${regularContainers.map(c => renderContainerCard(c, false)).join('')}
        </div>
      ` : ''}

      ${looseBins.length > 0 ? `
        <div class="drawers-grid">
          ${looseBins.map(b => renderDrawerCard(b)).join('')}
        </div>
      ` : ''}

      ${directItems.length > 0 ? `
        <div class="items-grid">
          ${directItems.map(item => renderItemCard(item)).join('')}
        </div>
      ` : ''}

      ${subRooms.length === 0 && containers.length === 0 && looseBins.length === 0 && directItems.length === 0 ? `
        <div class="empty-state">
          <h3 class="empty-state-title">${escapeHtml(room.name)} is empty</h3>
          <p class="empty-state-desc">Add a container (like a shelf, bin, or drawer unit) or move an existing container into this room.</p>
          <div style="display: flex; gap: 0.6rem; flex-wrap: wrap; justify-content: center;">
            <button type="button" class="btn btn-primary btn-sm" onclick="openAddStorageModal(${safeRoomId}, 'unit')">+ Add container</button>
            ${unassignedCount > 0 ? `
              <button type="button" class="btn btn-secondary btn-sm" onclick="navToNeedsHome(true)">
                Place from Needs a home (${unassignedCount})
              </button>
            ` : ''}
          </div>
        </div>
      ` : ''}
    </div>
  `;
}

function renderContainerCard(container, showQuickMovePills = false) {
  const safeId = Number(container.id);
  const photo = sanitizePhotoUrl(container.container_photo || container.context_photo || '');
  const drawerCount = (container.children && container.children.length) || container.child_count || 0;
  const itemCount = container.total_item_count || 0;
  const moveChoices = showQuickMovePills ? getQuickMoveRoomChoices() : [];

  return `
    <div class="container-card" onclick="navToStorageUnit(${safeId}, true)">
      ${photo ? `<img class="container-card-photo" src="${escapeHtml(photo)}" alt="${escapeHtml(container.name)}" loading="lazy">` : ''}
      <div class="container-card-body">
        <div class="container-card-top">
          <h3 class="container-card-title">${escapeHtml(container.name)}</h3>
          ${!photo ? `
            <button
              type="button"
              class="subtle-photo-pill"
              onclick="event.stopPropagation(); openQuickPhotoModal({ kind: 'bin', id: ${safeId}, title: 'Add photo' })"
            >
              ${lucideIcon('camera', '', 14)} Add photo
            </button>
          ` : ''}
        </div>

        <div class="container-card-counts">
          <span><strong>${drawerCount}</strong> ${drawerCount === 1 ? 'drawer' : 'drawers'}</span>
          <span>·</span>
          <span><strong>${itemCount}</strong> ${itemCount === 1 ? 'item' : 'items'}</span>
        </div>

        ${showQuickMovePills ? `
          <div class="quick-move-strip" onclick="event.stopPropagation()">
            <span class="quick-move-label">Move to room:</span>
            <div class="quick-move-pills">
              ${moveChoices.map(r => `
                <button
                  type="button"
                  class="move-room-pill"
                  onclick="quickMoveContainerToRoom(${safeId}, ${r.id ? Number(r.id) : 'null'}, '${escapeJsStr(r.name)}', '${escapeJsStr(r.emoji)}')"
                >
                  ${escapeHtml(r.emoji)} ${escapeHtml(r.name)}
                </button>
              `).join('')}
            </div>
          </div>
        ` : ''}
      </div>
    </div>
  `;
}

function toSentenceCase(str) {
  if (!str || typeof str !== 'string') return '';
  const trimmed = str.trim();
  if (!trimmed) return '';
  return trimmed.charAt(0).toUpperCase() + trimmed.slice(1).toLowerCase();
}

let activeUnitViewerId = null;
let unitMoreDocBound = false;

export function closeUnitMoreMenu(returnFocus = false) {
  const menu = document.getElementById('unit-more-menu');
  const btn = document.getElementById('unit-more-btn');
  if (menu) menu.hidden = true;
  if (btn) {
    btn.setAttribute('aria-expanded', 'false');
    if (returnFocus) btn.focus();
  }
}

export function toggleUnitMoreMenu(e) {
  if (e) e.stopPropagation();
  const menu = document.getElementById('unit-more-menu');
  const btn = document.getElementById('unit-more-btn');
  if (!menu || !btn) return;

  const willOpen = menu.hidden;
  menu.hidden = !willOpen;
  btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');

  if (willOpen) {
    const firstItem = menu.querySelector('[role="menuitem"]');
    if (firstItem) firstItem.focus();
  }

  if (!unitMoreDocBound) {
    unitMoreDocBound = true;
    document.addEventListener('click', (evt) => {
      const m = document.getElementById('unit-more-menu');
      const wrap = document.querySelector('.unit-more-wrap');
      if (m && !m.hidden && wrap && !wrap.contains(evt.target)) {
        closeUnitMoreMenu(false);
      }
    });
    document.addEventListener('keydown', (evt) => {
      const m = document.getElementById('unit-more-menu');
      if (evt.key === 'Escape' && m && !m.hidden) {
        evt.preventDefault();
        closeUnitMoreMenu(true);
      }
    });
  }
}

export function handleUnitMoreKeydown(e) {
  const menu = document.getElementById('unit-more-menu');
  if (!menu || menu.hidden) return;
  const items = Array.from(menu.querySelectorAll('[role="menuitem"]'));
  if (items.length === 0) return;

  const idx = items.indexOf(document.activeElement);
  if (e.key === 'ArrowDown') {
    e.preventDefault();
    items[(idx + 1) % items.length].focus();
  } else if (e.key === 'ArrowUp') {
    e.preventDefault();
    items[(idx - 1 + items.length) % items.length].focus();
  } else if (e.key === 'Home') {
    e.preventDefault();
    items[0].focus();
  } else if (e.key === 'End') {
    e.preventDefault();
    items[items.length - 1].focus();
  } else if (e.key === 'Tab') {
    closeUnitMoreMenu(false);
  }
}

export function focusDrawerFromViewer(drawerId) {
  closeModal('unit-photo-viewer-modal');
  const card = document.getElementById(`drawer-card-${Number(drawerId)}`);
  if (!card) return;
  card.scrollIntoView({ behavior: 'smooth', block: 'center' });
  card.classList.add('amber-spotlight');
  setTimeout(() => {
    card.classList.remove('amber-spotlight');
  }, 2000);
}

export function openUnitPhotoViewer(unitId) {
  const unit = state.locationMap.get(unitId);
  if (!unit) return;
  const photo = sanitizePhotoUrl(unit.container_photo || unit.context_photo || '');
  if (!photo) return;

  activeUnitViewerId = unitId;
  const titleEl = document.getElementById('unit-viewer-title');
  const imgEl = document.getElementById('unit-viewer-img');
  const hotspotsEl = document.getElementById('unit-viewer-hotspots');

  if (titleEl) titleEl.textContent = toSentenceCase(unit.name);
  if (imgEl) imgEl.src = photo;

  if (hotspotsEl) {
    const drawers = unit.children || [];
    const positioned = drawers.filter(
      (d) => d.photo_x !== undefined && d.photo_x !== null && d.photo_y !== undefined && d.photo_y !== null
    );
    if (positioned.length > 0) {
      hotspotsEl.style.display = 'block';
      hotspotsEl.innerHTML = positioned
        .map(
          (d, idx) => `
            <button
              type="button"
              class="unit-hotspot-pin"
              style="left: ${Number(d.photo_x)}%; top: ${Number(d.photo_y)}%;"
              onclick="focusDrawerFromViewer(${Number(d.id)})"
              title="${escapeHtml(toSentenceCase(d.name))}"
              aria-label="Go to ${escapeHtml(toSentenceCase(d.name))}"
            >${idx + 1}</button>
          `
        )
        .join('');
    } else {
      hotspotsEl.style.display = 'none';
      hotspotsEl.innerHTML = '';
    }
  }

  document.getElementById('unit-photo-viewer-modal')?.classList.add('open');
}

export function unitViewerReplacePhoto() {
  if (!activeUnitViewerId) return;
  const id = activeUnitViewerId;
  closeModal('unit-photo-viewer-modal');
  openQuickPhotoModal({ kind: 'bin', id, title: 'Replace photo' });
}

export function unitViewerCropPhoto() {
  if (!activeUnitViewerId) return;
  const unit = state.locationMap.get(activeUnitViewerId);
  const photo = unit ? sanitizePhotoUrl(unit.container_photo || unit.context_photo || '') : '';
  if (!photo) return;
  const id = activeUnitViewerId;
  closeModal('unit-photo-viewer-modal');
  editExistingPhoto({ kind: 'bin', id, photoUrl: photo, title: 'Crop' });
}

export async function unitViewerRemovePhoto() {
  if (!activeUnitViewerId) return;
  const id = activeUnitViewerId;
  return withSubmitLock('unit-viewer-remove-btn', 'Removing...', async () => {
    try {
      await apiPatch(`/api/v1/locations/${id}`, { container_photo: null, context_photo: null }, 'Could not remove photo.');
      closeModal('unit-photo-viewer-modal');
      showToast('Photo removed.');
      await loadAllData();
    } catch (err) {
      showToast(err.message || 'Could not remove photo.', 'error');
    }
  });
}

export function openMoveContainerModal(unitId) {
  closeUnitMoreMenu(false);
  const unit = state.locationMap.get(unitId);
  if (!unit) return;
  const idInput = document.getElementById('move-container-id');
  const select = document.getElementById('move-container-room-select');
  if (idInput) idInput.value = String(unitId);
  if (select) {
    const rooms = state.namedRooms.filter((r) => r.id !== unitId);
    select.innerHTML = [
      `<option value="">Needs a home (unassigned)</option>`,
      ...rooms.map(
        (r) => `<option value="${Number(r.id)}" ${unit.parent_id === r.id ? 'selected' : ''}>${escapeHtml(r.name)}</option>`
      ),
    ].join('');
  }
  document.getElementById('move-container-modal')?.classList.add('open');
}

export async function submitMoveContainer() {
  const id = parseInt(document.getElementById('move-container-id')?.value || '0', 10);
  const rawParent = document.getElementById('move-container-room-select')?.value;
  const parentId = rawParent ? parseInt(rawParent, 10) : null;
  if (!id) return;

  return withSubmitLock('move-container-save-btn', 'Moving...', async () => {
    try {
      await apiPatch(`/api/v1/locations/${id}`, { parent_id: parentId }, 'Could not move container.');
      closeModal('move-container-modal');
      showToast('Container moved.');
      await loadAllData();
    } catch (err) {
      showToast(err.message || 'Could not move container.', 'error');
    }
  });
}

export async function deleteContainerFromMenu(unitId) {
  closeUnitMoreMenu(false);
  const unit = state.locationMap.get(unitId);
  if (!unit) return;
  if (!window.confirm(`Delete "${unit.name}" and its drawers?`)) return;
  try {
    await apiDelete(`/api/v1/locations/${unitId}`, 'Could not delete container.');
    showToast('Container deleted.');
    const parent = unit.parent_id ? state.locationMap.get(unit.parent_id) : null;
    await loadAllData();
    if (parent && !isUnknownPlaceholder(parent)) {
      navToRoom(parent.id, true);
    } else {
      navToHome();
    }
  } catch (err) {
    showToast(err.message || 'Could not delete container.', 'error');
  }
}

export function renderStorageUnitView(unitId) {
  const unit = state.locationMap.get(unitId);
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;
  workspace.classList.remove('is-home-canvas');
  workspace.classList.add('is-unit-view');

  if (!unit) {
    workspace.classList.remove('is-unit-view');
    workspace.innerHTML = `<div class="empty-state"><p>Container not found.</p><button class="btn btn-secondary btn-sm" onclick="navToHome()">Back to Places</button></div>`;
    return;
  }

  const safeUnitId = Number(unit.id);
  const drawers = unit.children || [];
  const directItems = state.items.filter(i => i.location_id === unit.id);
  const photo = sanitizePhotoUrl(unit.container_photo || unit.context_photo || '');
  const parent = unit.parent_id ? state.locationMap.get(unit.parent_id) : null;
  const isUnassigned = !parent || isUnknownPlaceholder(parent);

  const crumbs = [];
  if (isUnassigned) {
    crumbs.push({ label: UNASSIGNED_ROOM_LABEL, onclick: 'navToNeedsHome(true)' });
  } else {
    crumbs.push({ label: toSentenceCase(parent.name), onclick: `navToRoom(${Number(parent.id)}, true)` });
  }
  crumbs.push({ label: toSentenceCase(unit.name) });

  const moveChoices = isUnassigned ? getQuickMoveRoomChoices() : [];
  const hasGrid = Number(unit.grid_rows) > 0 && Number(unit.grid_cols) > 0;
  const isEditingGrid = isInlineGridEditing(safeUnitId);
  const unplacedDrawers = hasGrid
    ? drawers.filter(d => !(Number(d.grid_row) >= 1 && Number(d.grid_col) >= 1))
    : drawers;

  workspace.innerHTML = `
    <div class="unit-page-shell">
      ${renderStickyContextBar(
    crumbs,
    `
          ${isEditingGrid
      ? `<button type="button" class="btn btn-secondary btn-sm" onclick="cancelInlineGridDesigner()">Done editing</button>`
      : `<button type="button" class="btn btn-secondary btn-sm" onclick="openInlineGridDesigner(${safeUnitId})">${hasGrid ? 'Edit grid' : 'Design grid'}</button>`
    }
          <button type="button" class="btn btn-primary btn-sm" onclick="openAddStorageModal(${safeUnitId}, 'drawer')">Add drawer</button>
          <div class="unit-more-wrap">
            <button
              type="button"
              id="unit-more-btn"
              class="btn btn-secondary btn-sm unit-more-btn"
              aria-haspopup="menu"
              aria-expanded="false"
              aria-label="More container actions"
              onclick="toggleUnitMoreMenu(event)"
            >${lucideIcon('moreHorizontal', '', 16)}</button>
            <div
              id="unit-more-menu"
              class="unit-more-menu"
              role="menu"
              aria-labelledby="unit-more-btn"
              onkeydown="handleUnitMoreKeydown(event)"
              hidden
            >
              <button type="button" class="unit-more-item" role="menuitem" onclick="closeUnitMoreMenu(false); openEditStorageModal(${safeUnitId})">Rename or edit</button>
              <button type="button" class="unit-more-item" role="menuitem" onclick="closeUnitMoreMenu(false); openPrintQrModal(${safeUnitId})">Print QR label</button>
              <button type="button" class="unit-more-item" role="menuitem" onclick="openMoveContainerModal(${safeUnitId})">Move to another room</button>
              <button type="button" class="unit-more-item danger" role="menuitem" onclick="deleteContainerFromMenu(${safeUnitId})">Delete container</button>
            </div>
          </div>
        `
  )}

      <div class="unit-scroll-region" id="unit-scroll-region">
        <div class="place-hero-card">
          <div class="place-hero-left">
            ${photo
      ? `
                <button type="button" class="unit-photo-trigger" onclick="openUnitPhotoViewer(${safeUnitId})" aria-label="View container photo">
                  <img class="place-hero-thumb" src="${escapeHtml(photo)}" alt="${escapeHtml(toSentenceCase(unit.name))}">
                  <span class="unit-photo-badge" aria-hidden="true">${lucideIcon('camera', '', 14)}</span>
                </button>
              `
      : `
                <button type="button" class="unit-photo-empty-box" onclick="openQuickPhotoModal({ kind: 'bin', id: ${safeUnitId}, title: 'Add a photo' })">
                  ${lucideIcon('camera', '', 18)}
                  <span>Add a photo</span>
                </button>
              `
    }
            <div>
              <h1 class="place-hero-title">${escapeHtml(toSentenceCase(unit.name))}</h1>
              <p class="place-hero-sub">
                ${pluralize(drawers.length, 'drawer')} · ${pluralize(unit.total_item_count || 0, 'item')}
                ${isUnassigned ? ' · Currently in Needs a home' : ` · In ${escapeHtml(toSentenceCase(parent.name))}`}
              </p>
              ${!hasGrid && !isEditingGrid ? `
                <div style="margin-top: 0.55rem;">
                  <button type="button" class="btn btn-secondary btn-sm" onclick="openInlineGridDesigner(${safeUnitId})">
                    ${lucideIcon('grid', '', 14)} Design drawer / shelf grid
                  </button>
                </div>
              ` : ''}
              ${isUnassigned && moveChoices.length > 0 ? `
                <div class="quick-move-pills" style="margin-top: 0.55rem;">
                  ${moveChoices.map(r => `
                    <button
                      type="button"
                      class="move-room-pill"
                      onclick="quickMoveContainerToRoom(${safeUnitId}, ${r.id ? Number(r.id) : 'null'}, '${escapeJsStr(r.name)}', '${escapeJsStr(r.emoji)}')"
                    >
                      Move to ${escapeHtml(r.name)}
                    </button>
                  `).join('')}
                </div>
              ` : ''}
            </div>
          </div>
        </div>

        ${isEditingGrid ? `
          <div class="unit-grid-section" id="storage-drawers-container">
            ${renderInlineGridDesignerHtml()}
          </div>
        ` : hasGrid ? `
          <div class="unit-grid-section" id="storage-drawers-container">
            ${renderContainerGridHtml(unit, { highlightDrawerId: state.spotlightDrawerId })}
          </div>
          ${unplacedDrawers.length > 0 ? `
            <div class="drawers-grid" style="margin-top: 0.9rem;">
              ${unplacedDrawers.map(d => renderDrawerCard(d)).join('')}
            </div>
          ` : ''}
        ` : `
          <div class="drawers-grid" id="storage-drawers-container">
            ${drawers.map(d => renderDrawerCard(d)).join('')}
            <button type="button" class="drawer-card drawer-card-add" onclick="openAddStorageModal(${safeUnitId}, 'drawer')">
              ${lucideIcon('plus', '', 20)}
              <span>Add drawer</span>
            </button>
          </div>
        `}

        ${directItems.length > 0 ? `
          <div class="items-grid">
            ${directItems.map(item => renderItemCard(item)).join('')}
          </div>
        ` : ''}
      </div>
    </div>
  `;

  if (isEditingGrid) {
    mountInlineGridDesigner();
  }

  if (state.spotlightDrawerId) {
    setTimeout(() => {
      const el = document.querySelector('.spotlight-highlight');
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 100);
  }
}

function renderDrawerCard(drawer) {
  const safeDrawerId = Number(drawer.id);
  const itemsInDrawer = state.items.filter(item => item.location_id === drawer.id);
  const count = itemsInDrawer.length;
  const previews = itemsInDrawer.slice(0, 2);
  const remaining = count - previews.length;
  const isSpotlight = drawer.id === state.spotlightDrawerId;

  return `
    <div id="drawer-card-${safeDrawerId}" class="drawer-card ${isSpotlight ? 'spotlight-highlight' : ''}" onclick="navToDrawer(${safeDrawerId}, true)">
      <div class="drawer-card-top">
        <div style="display: flex; flex-direction: column; gap: 0.25rem; min-width: 0; flex: 1;">
          <h4 class="drawer-card-title">${escapeHtml(toSentenceCase(drawer.name))}</h4>
          ${isSpotlight ? `<span class="spotlight-badge">Inside here</span>` : ''}
        </div>
        ${count >= 2 ? `<span class="drawer-badge-count">${pluralize(count, 'item')}</span>` : ''}
      </div>

      ${count > 0 ? `
        <ul class="drawer-item-snippets">
          ${previews.map(i => `<li class="drawer-item-snippet">${escapeHtml(toSentenceCase(i.name))}</li>`).join('')}
          ${remaining > 0 ? `<li class="drawer-more-count">+ ${remaining} more</li>` : ''}
        </ul>
      ` : `
        <p class="drawer-empty-hint">Nothing here yet — tap to add items</p>
      `}
    </div>
  `;
}

export function renderDrawerView(drawerId) {
  const drawer = state.locationMap.get(drawerId);
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;
  workspace.classList.remove('is-home-canvas');
  workspace.scrollTop = 0;

  if (!drawer) {
    workspace.innerHTML = `<div class="empty-state"><p>Drawer not found.</p><button class="btn btn-secondary btn-sm" onclick="navToHome()">Back to Places</button></div>`;
    return;
  }

  const safeDrawerId = Number(drawer.id);
  const hier = getLocationHierarchy(drawer.id);
  const crumbs = [];
  if (hier.isNeedsHome) {
    crumbs.push({ label: UNASSIGNED_ROOM_LABEL, onclick: 'navToNeedsHome(true)' });
  } else if (hier.room) {
    crumbs.push({ label: hier.room.name, onclick: `navToRoom(${Number(hier.room.id)}, true)` });
  }
  if (hier.unit && hier.unit.id !== drawer.id) {
    crumbs.push({ label: hier.unit.name, onclick: `navToStorageUnit(${Number(hier.unit.id)}, true)` });
  }
  crumbs.push({ label: drawer.name });

  const items = state.items.filter(item => item.location_id === drawer.id);
  const photo = sanitizePhotoUrl(drawer.container_photo || drawer.context_photo || '');

  workspace.innerHTML = `
    <div class="workspace-inner">
      ${renderStickyContextBar(
    crumbs,
    `
          <button type="button" class="btn btn-primary btn-sm" onclick="openRapidEntryModal(${safeDrawerId})">${lucideIcon('camera', '', 15)} Scan with Camera</button>
          <button type="button" class="btn btn-secondary btn-sm" onclick="openAddItemModal(${safeDrawerId})">${lucideIcon('pencil', '', 15)} Add Item Manually</button>
          <button type="button" class="btn btn-secondary btn-sm" onclick="openQuickPhotoModal({ kind: 'bin', id: ${safeDrawerId}, title: 'Inside photo' })">Inside photo</button>
          ${photo ? `<button type="button" class="btn btn-secondary btn-sm" onclick="editExistingPhoto({ kind: 'bin', id: ${safeDrawerId}, photoUrl: '${escapeJsStr(photo)}', title: 'Edit drawer photo' })">${lucideIcon('scissors', '', 15)} Crop</button>` : ''}
          <button type="button" class="btn btn-secondary btn-sm" onclick="openPrintQrModal(${safeDrawerId})">${lucideIcon('printer', '', 15)} QR label</button>
        `
  )}

      <div class="place-hero-card">
        <div class="place-hero-left">
          ${photo
      ? `<img class="place-hero-thumb" src="${escapeHtml(photo)}" alt="${escapeHtml(drawer.name)}" onclick="openLightbox('${escapeJsStr(photo)}', '${escapeJsStr(drawer.name)}')">`
      : `<div class="place-hero-emoji">${lucideIcon('archive', '', 28)}</div>`
    }
          <div>
            <h1 class="place-hero-title">${escapeHtml(drawer.name)}</h1>
            <p class="place-hero-sub">${escapeHtml(hier.humanPath)} · ${pluralize(items.length, 'item')}</p>
          </div>
        </div>
      </div>

      <div class="items-grid">
        ${items.length > 0 ? items.map(item => renderItemCard(item)).join('') : `
          <div class="empty-state" style="grid-column: 1 / -1;">
            <h3 class="empty-state-title">This drawer is empty</h3>
            <p class="empty-state-desc">Scan an open drawer with your camera or add an item manually.</p>
            <div style="display: flex; gap: 0.6rem;">
              <button type="button" class="btn btn-primary btn-sm" onclick="openRapidEntryModal(${safeDrawerId})">${lucideIcon('camera', '', 15)} Scan with Camera</button>
              <button type="button" class="btn btn-secondary btn-sm" onclick="openAddItemModal(${safeDrawerId})">${lucideIcon('pencil', '', 15)} Add Item Manually</button>
            </div>
          </div>
        `}
      </div>
    </div>
  `;
}

function formatItemStatusLabel(status) {
  if (!status) return 'Stored';
  return String(status)
    .replace(/_/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase());
}

export function renderItemView(itemId) {
  const item = state.items.find(i => i.id === itemId);
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;
  workspace.classList.remove('is-home-canvas');
  workspace.scrollTop = 0;

  if (!item) {
    workspace.innerHTML = `
      <div class="workspace-inner">
        <div class="empty-state">
          <h3 class="empty-state-title">Item not found</h3>
          <p class="empty-state-desc">This item may have been deleted or moved.</p>
          <div style="display: flex; gap: 0.6rem; justify-content: center;">
            <button type="button" class="btn btn-primary btn-sm" onclick="navToHome()">Back to Places</button>
            <button type="button" class="btn btn-secondary btn-sm" onclick="switchAppTab('items')">All Items</button>
          </div>
        </div>
      </div>
    `;
    return;
  }

  const safeItemId = Number(item.id);
  const hier = getLocationHierarchy(item.location_id);
  const loc = item.location_id ? state.locationMap.get(item.location_id) : null;
  const safeLocId = loc ? Number(loc.id) : 0;

  const crumbs = [];
  if (hier.isNeedsHome) {
    crumbs.push({ label: UNASSIGNED_ROOM_LABEL, onclick: 'navToNeedsHome(true)' });
  } else if (hier.room) {
    crumbs.push({ label: hier.room.name, onclick: `navToRoom(${Number(hier.room.id)}, true)` });
  }
  if (hier.unit) {
    crumbs.push({ label: hier.unit.name, onclick: `navToStorageUnit(${Number(hier.unit.id)}, true)` });
  }
  if (hier.drawer && (!hier.unit || hier.drawer.id !== hier.unit.id)) {
    crumbs.push({ label: hier.drawer.name, onclick: `navToDrawer(${Number(hier.drawer.id)}, true)` });
  }
  crumbs.push({ label: item.name });

  const itemPhoto = sanitizePhotoUrl(item.item_photo || '');
  const insidePhoto = sanitizePhotoUrl(
    (loc && loc.container_photo) || (item.location_photos && item.location_photos.container_photo) || ''
  );
  const spotPhoto = sanitizePhotoUrl(
    (loc && loc.context_photo) || (item.location_photos && item.location_photos.context_photo) || ''
  );

  const hasQty = item.quantity !== null && item.quantity !== undefined;
  const tagList = item.tags
    ? String(item.tags).split(',').map(t => t.trim()).filter(Boolean)
    : [];
  const statusLabel = formatItemStatusLabel(item.status);
  const notesHtml = renderItemNotesSection(item);

  workspace.innerHTML = `
    <div class="workspace-inner">
      ${renderStickyContextBar(
        crumbs,
        `
          ${(hier.unit || item.location_id) ? `
            <button
              type="button"
              class="btn btn-primary btn-sm"
              onclick="locateDrawerInUnit(${Number(hier.unit?.id || item.location_id || 0)}, ${Number(hier.drawer?.id || item.location_id || 0)})"
            >
              Show in container →
            </button>
          ` : ''}
          ${loc ? `
            <button type="button" class="btn btn-secondary btn-sm" onclick="openPrintQrModal(${safeLocId})">
              ${lucideIcon('printer', '', 15)} QR label
            </button>
          ` : ''}
          <button type="button" class="btn btn-secondary btn-sm" onclick="editItemFromView(${safeItemId})">Edit</button>
          <button type="button" class="btn btn-danger btn-sm" onclick="deleteItemFromView(${safeItemId})">Delete</button>
          <button type="button" class="btn btn-verify btn-sm" id="item-view-verify-btn" onclick="verifyItemFromView(${safeItemId})">✓ Seen today</button>
        `
      )}

      <div class="place-hero-card item-hero-card">
        <div class="place-hero-left">
          ${itemPhoto
            ? `<img class="place-hero-thumb" src="${escapeHtml(itemPhoto)}" alt="${escapeHtml(item.name)}" onclick="openLightbox('${escapeJsStr(itemPhoto)}', '${escapeJsStr(item.name)}')">`
            : `<div class="place-hero-emoji" style="cursor: pointer;" onclick="openQuickPhotoModal({ kind: 'item', id: ${safeItemId}, title: 'Add photo' })" title="Add photo">${lucideIcon('package', '', 28)}</div>`
          }
          <div style="min-width: 0;">
            <div class="item-hero-eyebrow-row">
              <span class="item-card-category">${escapeHtml(item.category || 'Saved Item')}</span>
              <span class="item-status-badge status-${escapeHtml(item.status || 'stored')}">${escapeHtml(statusLabel)}</span>
            </div>
            <h1 class="place-hero-title">${escapeHtml(item.name)}</h1>
            <p class="place-hero-sub">
              ${item.serial_or_part_number ? `Model: <span style="font-family: var(--font-mono);">${escapeHtml(item.serial_or_part_number)}</span> · ` : ''}
              Seen ${escapeHtml(formatDate(item.last_seen_at))}
            </p>
          </div>
        </div>
        <div class="detail-qty-stepper item-hero-stepper">
          <button type="button" class="qty-stepper-btn" onclick="adjustItemQty(${safeItemId}, -1)">−</button>
          <span class="qty-stepper-val" id="item-view-qty-val">
            ${hasQty ? Number(item.quantity) : 'Not counted'}
          </span>
          <button type="button" class="qty-stepper-btn" onclick="adjustItemQty(${safeItemId}, 1)">+</button>
          <span style="font-size: 0.85rem; color: var(--text-secondary);">${escapeHtml(item.unit || 'pcs')}</span>
        </div>
      </div>

      <section class="item-detail-card">
        <span class="detail-section-title">Where it lives</span>
        <div class="item-where-grid">
          <div
            class="location-step-item ${hier.isNeedsHome ? 'is-needs-home' : ''}"
            onclick="${hier.room ? `navToRoom(${Number(hier.room.id)}, true)` : `navToNeedsHome(true)`}"
          >
            <span class="step-emoji">${hier.room ? escapeHtml(getRoomEmoji(hier.room)) : '📌'}</span>
            <div class="step-content">
              <span class="step-level-tag">Room</span>
              <span class="step-level-val">${escapeHtml(hier.roomName)}</span>
            </div>
            <span style="color: var(--accent-text); font-weight: 700;">→</span>
          </div>

          <div
            class="location-step-item ${!hier.unit ? 'is-muted-step' : ''}"
            ${hier.unit ? `onclick="navToStorageUnit(${Number(hier.unit.id)}, true)"` : ''}
          >
            <span class="step-emoji">${lucideIcon('package', '', 18)}</span>
            <div class="step-content">
              <span class="step-level-tag">Storage Container</span>
              <span class="step-level-val">${escapeHtml(hier.unit ? hier.unitName : 'None / Loose in room')}</span>
            </div>
            ${hier.unit ? `<span style="color: var(--accent-text); font-weight: 700;">→</span>` : ''}
          </div>

          <div
            class="location-step-item ${!(hier.drawer || loc) ? 'is-muted-step' : ''}"
            ${hier.drawer ? `onclick="navToDrawer(${Number(hier.drawer.id)}, true)"` : (loc ? `onclick="navToDrawer(${safeLocId}, true)"` : '')}
          >
            <span class="step-emoji">${lucideIcon('archive', '', 18)}</span>
            <div class="step-content">
              <span class="step-level-tag">Location / Drawer / Bin</span>
              <span class="step-level-val" ${hier.drawer ? 'style="color: var(--accent-text);"' : ''}>${escapeHtml(hier.drawer ? hier.drawerName : (hier.unit ? hier.unitName : 'Unassigned'))}</span>
            </div>
            ${(hier.drawer || loc) ? `<span style="color: var(--accent-text); font-weight: 700;">→</span>` : ''}
          </div>
        </div>
      </section>

      <div class="item-detail-grid">
        <div class="item-detail-col">
          <section class="item-detail-card">
            <span class="detail-section-title">Item Details</span>
            <div class="specs-parsed-grid">
              <div class="spec-parsed-row">
                <span class="spec-parsed-k">Category</span>
                <span class="spec-parsed-v">${escapeHtml(item.category || 'Uncategorized')}</span>
              </div>
              ${item.serial_or_part_number ? `
                <div class="spec-parsed-row">
                  <span class="spec-parsed-k">Brand / Model / Part #</span>
                  <span class="spec-parsed-v" style="font-family: var(--font-mono);">${escapeHtml(item.serial_or_part_number)}</span>
                </div>
              ` : ''}
              <div class="spec-parsed-row">
                <span class="spec-parsed-k">Quantity</span>
                <span class="spec-parsed-v">${hasQty ? `${Number(item.quantity)} ${escapeHtml(item.unit || 'pcs')}` : 'Not counted'}</span>
              </div>
              <div class="spec-parsed-row">
                <span class="spec-parsed-k">Status</span>
                <span class="spec-parsed-v">${escapeHtml(statusLabel)}${item.status_note ? ` — ${escapeHtml(item.status_note)}` : ''}</span>
              </div>
              <div class="spec-parsed-row">
                <span class="spec-parsed-k">Last seen</span>
                <span class="spec-parsed-v">${escapeHtml(formatDate(item.last_seen_at))}</span>
              </div>
              ${item.created_at ? `
                <div class="spec-parsed-row">
                  <span class="spec-parsed-k">Added</span>
                  <span class="spec-parsed-v">${escapeHtml(formatDate(item.created_at))}</span>
                </div>
              ` : ''}
              ${item.updated_at ? `
                <div class="spec-parsed-row">
                  <span class="spec-parsed-k">Updated</span>
                  <span class="spec-parsed-v">${escapeHtml(formatDate(item.updated_at))}</span>
                </div>
              ` : ''}
            </div>

            ${tagList.length > 0 ? `
              <div class="item-tags-wrap">
                <span class="detail-section-title" style="margin-bottom: 0.35rem;">Tags</span>
                <div class="item-tags-list">
                  ${tagList.map(t => `<span class="item-tag-chip">${escapeHtml(t)}</span>`).join('')}
                </div>
              </div>
            ` : ''}
          </section>

          <section class="item-detail-card">
            ${notesHtml || `
              <span class="detail-section-title">Notes</span>
              <div class="item-notes-box" style="color: var(--text-muted); display: flex; justify-content: space-between; align-items: center; gap: 0.75rem;">
                <span>No notes or specs added yet.</span>
                <button type="button" class="btn btn-secondary btn-sm" onclick="editItemFromView(${safeItemId})">Add notes</button>
              </div>
            `}
          </section>
        </div>

        <div class="item-detail-col">
          <section class="item-detail-card">
            <span class="detail-section-title">Photos</span>
            <div class="item-view-photos-grid">
              <div class="detail-photo-card">
                <div
                  class="detail-photo-thumb item-view-photo-thumb"
                  onclick="${itemPhoto ? `openLightbox('${escapeJsStr(itemPhoto)}', '${escapeJsStr(item.name)}')` : `openQuickPhotoModal({ kind: 'item', id: ${safeItemId}, title: 'Add photo' })`}"
                >
                  ${itemPhoto ? `<img src="${escapeHtml(itemPhoto)}" alt="${escapeHtml(item.name)}">` : `<span style="font-size: 0.78rem; color: var(--accent-text); font-weight: 700;">+ Add item photo</span>`}
                </div>
                <span class="detail-photo-caption">Item photo</span>
                ${itemPhoto ? `<button type="button" class="detail-photo-edit-btn" onclick="editExistingPhoto({ kind: 'item', id: ${safeItemId}, photoUrl: '${escapeJsStr(itemPhoto)}', title: 'Edit item photo' })">${lucideIcon('scissors', '', 12)} Crop</button>` : ''}
              </div>

              <div class="detail-photo-card">
                <div
                  class="detail-photo-thumb item-view-photo-thumb"
                  onclick="${insidePhoto ? `openLightbox('${escapeJsStr(insidePhoto)}', 'Inside photo')` : (loc ? `openQuickPhotoModal({ kind: 'bin', id: ${safeLocId}, title: 'Inside photo' })` : '')}"
                >
                  ${insidePhoto ? `<img src="${escapeHtml(insidePhoto)}" alt="Inside">` : `<span style="font-size: 0.78rem; color: var(--accent-text); font-weight: 700;">${loc ? '+ Inside photo' : 'No container'}</span>`}
                </div>
                <span class="detail-photo-caption">Inside photo</span>
                ${insidePhoto && loc ? `<button type="button" class="detail-photo-edit-btn" onclick="editExistingPhoto({ kind: 'bin', id: ${safeLocId}, photoUrl: '${escapeJsStr(insidePhoto)}', title: 'Edit inside photo' })">${lucideIcon('scissors', '', 12)} Crop</button>` : ''}
              </div>

              <div class="detail-photo-card">
                ${(hier.unit && Number(hier.unit.grid_rows) > 0 && Number(hier.unit.grid_cols) > 0) ? `
                  <div class="detail-photo-thumb item-view-photo-thumb is-grid-diagram">
                    ${renderContainerGridHtml(hier.unit, { highlightDrawerId: hier.drawer?.id || item.location_id, diagram: true })}
                  </div>
                ` : `
                  <div
                    class="detail-photo-thumb item-view-photo-thumb"
                    onclick="${spotPhoto ? `openLightbox('${escapeJsStr(spotPhoto)}', 'Where it sits')` : (loc ? `openQuickPhotoModal({ kind: 'spot', id: ${safeLocId}, title: 'Where it sits' })` : '')}"
                  >
                    ${spotPhoto ? `<img src="${escapeHtml(spotPhoto)}" alt="Where it sits">` : `<span style="font-size: 0.78rem; color: var(--accent-text); font-weight: 700;">${loc ? '+ Where it sits' : 'No location'}</span>`}
                  </div>
                `}
                <span class="detail-photo-caption">Where it sits</span>
              </div>
            </div>
          </section>

          <section class="item-detail-card">
            <span class="detail-section-title">Activity History</span>
            <div id="item-audit-history-${safeItemId}" class="item-audit-list">
              <p class="right-col-empty">Loading history…</p>
            </div>
          </section>
        </div>
      </div>
    </div>
  `;

  loadItemAuditHistory(safeItemId);
}

async function loadItemAuditHistory(itemId) {
  const container = document.getElementById(`item-audit-history-${itemId}`);
  if (!container) return;
  try {
    const detail = await apiGet(`/api/v1/items/${itemId}`, 'Could not load item history.');
    const target = document.getElementById(`item-audit-history-${itemId}`);
    if (!target) return;
    const logs = Array.isArray(detail.audit_logs) ? detail.audit_logs : [];
    if (logs.length === 0) {
      target.innerHTML = `<p class="right-col-empty">No activity recorded yet.</p>`;
      return;
    }
    target.innerHTML = logs.slice(0, 10).map(log => {
      const actionLabel = String(log.action || 'updated')
        .replace(/_/g, ' ')
        .replace(/\b\w/g, c => c.toUpperCase());
      return `
        <div class="item-audit-row">
          <div class="item-audit-main">
            <span class="item-audit-action">${escapeHtml(actionLabel)}</span>
            ${log.notes ? `<span class="item-audit-note">${escapeHtml(log.notes)}</span>` : ''}
          </div>
          <span class="item-audit-date">${escapeHtml(formatDate(log.recorded_at))}</span>
        </div>
      `;
    }).join('');
  } catch {
    const target = document.getElementById(`item-audit-history-${itemId}`);
    if (target) target.innerHTML = `<p class="right-col-empty">History unavailable.</p>`;
  }
}

// ============================================================================
// 4. ITEMS TAB & IN-USE TAB
// ============================================================================
export function renderCatalogView() {
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;
  workspace.classList.remove('is-home-canvas');

  let filtered = state.items.slice();

  if (state.itemsSearchQuery) {
    const q = state.itemsSearchQuery.toLowerCase();
    filtered = filtered.filter(item => {
      const hier = getLocationHierarchy(item.location_id);
      return (
        (item.name && item.name.toLowerCase().includes(q)) ||
        (item.serial_or_part_number && item.serial_or_part_number.toLowerCase().includes(q)) ||
        (item.category && item.category.toLowerCase().includes(q)) ||
        (item.tags && item.tags.toLowerCase().includes(q)) ||
        (item.description && item.description.toLowerCase().includes(q)) ||
        hier.humanPath.toLowerCase().includes(q)
      );
    });
  }

  if (state.selectedCategory) {
    filtered = filtered.filter(item => item.category === state.selectedCategory);
  }

  if (state.selectedContainerId) {
    const target = state.locationMap.get(parseInt(state.selectedContainerId, 10));
    if (target) {
      const allowed = new Set([target.id]);
      for (const c of target.children || []) allowed.add(c.id);
      filtered = filtered.filter(item => allowed.has(item.location_id));
    }
  }

  if (state.selectedSort === 'name_asc') {
    filtered.sort((a, b) => a.name.localeCompare(b.name));
  } else if (state.selectedSort === 'recent_seen') {
    filtered.sort((a, b) => new Date(b.last_seen_at || 0) - new Date(a.last_seen_at || 0));
  } else if (state.selectedSort === 'qty_desc') {
    filtered.sort((a, b) => (b.quantity || 0) - (a.quantity || 0));
  }

  const existingResults = document.getElementById('items-catalog-results');
  const existingPills = document.getElementById('items-category-pills');
  if (!existingResults || !existingPills) {
    workspace.innerHTML = `
      <div class="workspace-inner">
        <div class="items-page-controls">
          <div class="items-search-and-sort">
            <div class="items-search-box">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <circle cx="11" cy="11" r="8"></circle>
                <line x1="21" y1="21" x2="16.65" y2="16.65"></line>
              </svg>
              <input
                type="text"
                id="items-search-input"
                placeholder="Search all ${state.items.length} items..."
                value="${escapeHtml(state.itemsSearchQuery)}"
                autocomplete="off"
                oninput="handleItemsSearchInput(event)"
              >
            </div>
            <select id="toolbar-storage-filter" class="select-compact" onchange="handleSubFilterChange()">
              <option value="">All containers</option>
              ${state.containers.map(c => `
                <option value="${Number(c.id)}" ${String(c.id) === String(state.selectedContainerId) ? 'selected' : ''}>
                  ${escapeHtml(c.name)}
                </option>
              `).join('')}
            </select>
            <select id="toolbar-sort-select" class="select-compact" onchange="handleSubFilterChange()">
              <option value="name_asc" ${state.selectedSort === 'name_asc' ? 'selected' : ''}>Name (A–Z)</option>
              <option value="recent_seen" ${state.selectedSort === 'recent_seen' ? 'selected' : ''}>Recently seen</option>
              <option value="qty_desc" ${state.selectedSort === 'qty_desc' ? 'selected' : ''}>Most on hand</option>
            </select>
          </div>
          <div class="category-scroll-strip" id="items-category-pills"></div>
        </div>
        <div id="items-catalog-results"></div>
      </div>
    `;
  }

  const pillsEl = document.getElementById('items-category-pills');
  if (pillsEl) {
    pillsEl.innerHTML = `
      <button type="button" class="category-pill ${!state.selectedCategory ? 'active' : ''}" onclick="selectCategory('')">
        All (${state.items.length})
      </button>
      ${state.categories.map(cat => {
      const count = state.items.filter(i => i.category === cat).length;
      return `
          <button type="button" class="category-pill ${state.selectedCategory === cat ? 'active' : ''}" onclick="selectCategory('${escapeJsStr(cat)}')">
            ${escapeHtml(cat)} (${count})
          </button>
        `;
    }).join('')}
    `;
  }

  const resultsEl = document.getElementById('items-catalog-results');
  if (resultsEl) {
    if (state.items.length === 0) {
      resultsEl.innerHTML = `
        <div class="empty-state">
          <h3 class="empty-state-title">No items added yet</h3>
          <p class="empty-state-desc">Add your first item to start tracking where things live.</p>
          <button type="button" class="btn btn-primary btn-sm" onclick="chooseAddAction()">+ Add item</button>
        </div>
      `;
    } else if (filtered.length === 0) {
      resultsEl.innerHTML = `
        <div class="empty-state">
          <h3 class="empty-state-title">No matching items</h3>
          <p class="empty-state-desc">Try clearing filters or searching with another word.</p>
          <button type="button" class="btn btn-secondary btn-sm" onclick="clearAllCatalogFilters()">Reset filters</button>
        </div>
      `;
    } else {
      resultsEl.innerHTML = `
        <div class="items-grid">
          ${filtered.map(item => renderItemCard(item)).join('')}
        </div>
      `;
    }
  }
}

export function handleItemsSearchInput(e) {
  state.itemsSearchQuery = (e.target.value || '').trim();
  renderCatalogView();
}

export function selectCategory(cat) {
  state.selectedCategory = cat;
  renderCatalogView();
}

export function handleSubFilterChange() {
  const contSelect = document.getElementById('toolbar-storage-filter');
  const sortSelect = document.getElementById('toolbar-sort-select');
  if (contSelect) state.selectedContainerId = contSelect.value;
  if (sortSelect) state.selectedSort = sortSelect.value;
  renderCatalogView();
}

export function clearAllCatalogFilters() {
  state.itemsSearchQuery = '';
  state.selectedCategory = '';
  state.selectedContainerId = '';
  const input = document.getElementById('items-search-input');
  if (input) input.value = '';
  renderCatalogView();
}

export function renderItemCard(item) {
  const safeItemId = Number(item.id);
  const hasQty = item.quantity !== null && item.quantity !== undefined;
  const qtyBadge = hasQty
    ? `<span class="item-qty-badge">${Number(item.quantity)} ${escapeHtml(item.unit || 'pcs')}</span>`
    : '';

  const hier = getLocationHierarchy(item.location_id);
  const parentLine = hier.room
    ? `${hier.roomName}${hier.unit ? ' · ' + hier.unitName : ''}`
    : hier.unit
      ? `Needs a home · ${hier.unitName}`
      : UNASSIGNED_ROOM_LABEL;
  const safeUnitId = Number(hier.unit?.id || item.location_id || 0);
  const safeDrawerId = Number(hier.drawer?.id || item.location_id || 0);

  return `
    <div class="item-card" onclick="navToItem(${safeItemId})">
      <div class="item-card-header">
        <div>
          <div style="display: flex; gap: 0.4rem; align-items: center; margin-bottom: 0.2rem; flex-wrap: wrap;">
            ${item.category ? `<span class="item-card-category">${escapeHtml(item.category)}</span>` : ''}
          </div>
          <h4 class="item-card-name">${escapeHtml(item.name)}</h4>
          ${item.serial_or_part_number ? `<span class="item-card-serial">${escapeHtml(item.serial_or_part_number)}</span>` : ''}
        </div>
        ${qtyBadge}
      </div>

      <div
        class="item-card-loc-box"
        onclick="event.stopPropagation(); locateDrawerInUnit(${safeUnitId}, ${safeDrawerId})"
        title="Show in container"
      >
        <span class="item-card-loc-parent">${escapeHtml(parentLine)}</span>
        <span class="item-card-loc-bin">↳ ${escapeHtml(hier.drawerName)}</span>
      </div>

      <div class="item-card-footer">
        <span>Seen ${escapeHtml(formatDate(item.last_seen_at))}</span>
        <span style="color: var(--accent-text); font-weight: 700;">Open →</span>
      </div>
    </div>
  `;
}

// ============================================================================
// 5. ITEM DETAIL SHEET (3-STEP LOCATION CARD, STEPPER, PHOTOS, IN USE, QR)
// ============================================================================
export function openItemDrawer(itemId) {
  const item = state.items.find(i => i.id === itemId);
  if (!item) return;

  state.activeDrawerItem = item;
  const safeItemId = Number(item.id);

  const drawer = document.getElementById('item-slide-drawer');
  const scrim = document.getElementById('slide-drawer-scrim');
  const catEl = document.getElementById('drawer-item-cat');
  const nameEl = document.getElementById('drawer-item-name');
  const contentEl = document.getElementById('slide-drawer-content');

  if (catEl) catEl.textContent = item.category || 'Saved Item';
  if (nameEl) nameEl.textContent = item.name;

  const hier = getLocationHierarchy(item.location_id);
  const loc = item.location_id ? state.locationMap.get(item.location_id) : null;
  const safeLocId = loc ? Number(loc.id) : 0;

  const itemPhoto = sanitizePhotoUrl(item.item_photo || '');
  const insidePhoto = sanitizePhotoUrl(
    (loc && loc.container_photo) || (item.location_photos && item.location_photos.container_photo) || ''
  );
  const spotPhoto = sanitizePhotoUrl(
    (loc && loc.context_photo) || (item.location_photos && item.location_photos.context_photo) || ''
  );

  contentEl.innerHTML = `
    <div class="detail-section">
      <span class="detail-section-title">Where it lives</span>
      <div class="location-map-card">
        <div
          class="location-step-item ${hier.isNeedsHome ? 'is-needs-home' : ''}"
          onclick="${hier.room ? `closeSlideDrawer(); navToRoom(${Number(hier.room.id)}, true)` : `closeSlideDrawer(); navToNeedsHome(true)`}"
        >
          <span class="step-emoji">${hier.room ? escapeHtml(getRoomEmoji(hier.room)) : '📌'}</span>
          <div class="step-content">
            <span class="step-level-tag">Room</span>
            <span class="step-level-val">${escapeHtml(hier.roomName)}</span>
          </div>
          <span style="color: var(--accent-text); font-weight: 700;">→</span>
        </div>

        ${hier.unit ? `
          <div class="location-step-item" onclick="closeSlideDrawer(); navToStorageUnit(${Number(hier.unit.id)}, true)">
            <span class="step-emoji">${lucideIcon('package', '', 18)}</span>
            <div class="step-content">
              <span class="step-level-tag">Container</span>
              <span class="step-level-val">${escapeHtml(hier.unitName)}</span>
            </div>
            <span style="color: var(--accent-text); font-weight: 700;">→</span>
          </div>
        ` : ''}

        ${hier.drawer ? `
          <div class="location-step-item" onclick="closeSlideDrawer(); navToDrawer(${Number(hier.drawer.id)}, true)">
            <span class="step-emoji">${lucideIcon('archive', '', 18)}</span>
            <div class="step-content">
              <span class="step-level-tag">Drawer / Bin</span>
              <span class="step-level-val" style="color: var(--accent-text);">${escapeHtml(hier.drawerName)}</span>
            </div>
            <span style="color: var(--accent-text); font-weight: 700;">→</span>
          </div>
        ` : ''}

        <div class="location-map-actions">
          ${loc ? `
            <button type="button" class="btn btn-secondary btn-sm" onclick="openPrintQrModal(${safeLocId})">
              ${lucideIcon('printer', '', 15)} QR label
            </button>
          ` : '<span></span>'}
          ${(hier.unit || item.location_id) ? `
            <button
              type="button"
              class="btn btn-primary btn-sm"
              onclick="locateDrawerInUnit(${Number(hier.unit?.id || item.location_id || 0)}, ${Number(hier.drawer?.id || item.location_id || 0)})"
            >
              Show in container →
            </button>
          ` : ''}
        </div>
      </div>
    </div>

    <div class="detail-section">
      <span class="detail-section-title">How many</span>
      <div class="detail-qty-stepper">
        <button type="button" class="qty-stepper-btn" onclick="adjustItemQty(${safeItemId}, -1)">−</button>
        <span class="qty-stepper-val" id="drawer-qty-val">
          ${item.quantity !== null && item.quantity !== undefined ? Number(item.quantity) : 'Not counted'}
        </span>
        <button type="button" class="qty-stepper-btn" onclick="adjustItemQty(${safeItemId}, 1)">+</button>
        <span style="font-size: 0.85rem; color: var(--text-secondary);">${escapeHtml(item.unit || 'pcs')}</span>
      </div>
    </div>

    ${item.serial_or_part_number ? `
      <div class="detail-section">
        <span class="detail-section-title">Brand / Model</span>
        <div class="spec-parsed-row">
          <span class="spec-parsed-k">Model</span>
          <span class="spec-parsed-v" style="font-family: var(--font-mono);">${escapeHtml(item.serial_or_part_number)}</span>
        </div>
      </div>
    ` : ''}

    ${renderItemNotesSection(item)}

    <div class="detail-section">
      <span class="detail-section-title">Photos</span>
      <div class="detail-photos-row">
        <div class="detail-photo-card">
          <div
            class="detail-photo-thumb"
            onclick="${itemPhoto ? `openLightbox('${escapeJsStr(itemPhoto)}', '${escapeJsStr(item.name)}')` : `openQuickPhotoModal({ kind: 'item', id: ${safeItemId}, title: 'Add photo' })`}"
          >
            ${itemPhoto ? `<img src="${escapeHtml(itemPhoto)}" alt="Item">` : `<span style="font-size: 0.74rem; color: var(--accent-text); font-weight: 700;">+ Add photo</span>`}
          </div>
          <span class="detail-photo-caption">Add photo</span>
          ${itemPhoto ? `<button type="button" class="detail-photo-edit-btn" onclick="editExistingPhoto({ kind: 'item', id: ${safeItemId}, photoUrl: '${escapeJsStr(itemPhoto)}', title: 'Edit item photo' })">${lucideIcon('scissors', '', 12)} Crop</button>` : ''}
        </div>

        <div class="detail-photo-card">
          <div
            class="detail-photo-thumb"
            onclick="${insidePhoto ? `openLightbox('${escapeJsStr(insidePhoto)}', 'Inside photo')` : (loc ? `openQuickPhotoModal({ kind: 'bin', id: ${safeLocId}, title: 'Inside photo' })` : '')}"
          >
            ${insidePhoto ? `<img src="${escapeHtml(insidePhoto)}" alt="Inside">` : `<span style="font-size: 0.74rem; color: var(--accent-text); font-weight: 700;">+ Inside photo</span>`}
          </div>
          <span class="detail-photo-caption">Inside photo</span>
          ${insidePhoto && loc ? `<button type="button" class="detail-photo-edit-btn" onclick="editExistingPhoto({ kind: 'bin', id: ${safeLocId}, photoUrl: '${escapeJsStr(insidePhoto)}', title: 'Edit inside photo' })">${lucideIcon('scissors', '', 12)} Crop</button>` : ''}
        </div>

        <div class="detail-photo-card">
          ${(hier.unit && Number(hier.unit.grid_rows) > 0 && Number(hier.unit.grid_cols) > 0) ? `
            <div class="detail-photo-thumb is-grid-diagram">
              ${renderContainerGridHtml(hier.unit, { highlightDrawerId: hier.drawer?.id || item.location_id, diagram: true, closeSlideOnNav: true })}
            </div>
          ` : `
            <div
              class="detail-photo-thumb"
              onclick="${spotPhoto ? `openLightbox('${escapeJsStr(spotPhoto)}', 'Where it sits')` : (loc ? `openQuickPhotoModal({ kind: 'spot', id: ${safeLocId}, title: 'Where it sits' })` : '')}"
            >
              ${spotPhoto ? `<img src="${escapeHtml(spotPhoto)}" alt="Where it sits">` : `<span style="font-size: 0.74rem; color: var(--accent-text); font-weight: 700;">+ Where it sits</span>`}
            </div>
          `}
          <span class="detail-photo-caption">Where it sits</span>
        </div>
      </div>
    </div>
  `;

  drawer?.classList.add('open');
  if (window.innerWidth < 900) {
    scrim?.classList.add('open');
  }
}

function renderItemNotesSection(item) {
  if (!item.description || !item.description.trim()) return '';
  const text = item.description.trim();

  if (text.includes('•') || text.includes('\n')) {
    const lines = text.split(/[•\n]/).map(l => l.trim()).filter(Boolean);
    const kvRows = [];
    const plainLines = [];

    for (const line of lines) {
      const idx = line.indexOf(':');
      if (idx > 0 && idx < 36) {
        kvRows.push({
          k: line.slice(0, idx).trim(),
          v: line.slice(idx + 1).trim()
        });
      } else {
        plainLines.push(line);
      }
    }

    return `
      <div class="detail-section">
        <span class="detail-section-title">Notes</span>
        ${kvRows.length > 0 ? `
          <div class="specs-parsed-grid">
            ${kvRows.map(r => `
              <div class="spec-parsed-row">
                <span class="spec-parsed-k">${escapeHtml(r.k)}</span>
                <span class="spec-parsed-v">${escapeHtml(r.v)}</span>
              </div>
            `).join('')}
          </div>
        ` : ''}
        ${plainLines.length > 0 ? `
          <div class="item-notes-box">
            ${plainLines.map(l => `<p>${escapeHtml(l)}</p>`).join('')}
          </div>
        ` : ''}
      </div>
    `;
  }

  return `
    <div class="detail-section">
      <span class="detail-section-title">Notes</span>
      <div class="item-notes-box">${escapeHtml(text)}</div>
    </div>
  `;
}

export function closeSlideDrawer() {
  document.getElementById('item-slide-drawer')?.classList.remove('open');
  document.getElementById('slide-drawer-scrim')?.classList.remove('open');
  state.activeDrawerItem = null;
}

export async function adjustItemQty(itemId, delta) {
  const item = state.items.find(i => i.id === itemId);
  if (!item) return;

  const current = item.quantity === null || item.quantity === undefined ? 0 : item.quantity;
  const next = Math.max(0, current + delta);

  try {
    const updated = await apiPatch(
      `/api/v1/items/${itemId}`,
      { quantity: next },
      'Could not update count.'
    );
    item.quantity = updated.quantity;

    const qtyEl = document.getElementById('drawer-qty-val');
    if (qtyEl) qtyEl.textContent = String(updated.quantity);
    const viewQtyEl = document.getElementById('item-view-qty-val');
    if (viewQtyEl) viewQtyEl.textContent = String(updated.quantity);

    renderCurrentWorkspace();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

export async function verifyItemFromDrawer() {
  if (!state.activeDrawerItem) return;
  const item = state.activeDrawerItem;
  return withSubmitLock('drawer-verify-btn', 'Saving...', async () => {
    try {
      await apiPost(`/api/v1/items/${item.id}/verify`, undefined, 'Could not update last-seen time.');
      showToast(`Marked "${item.name}" as seen today!`);
      closeSlideDrawer();
      await loadAllData();
    } catch (err) {
      showToast(err.message, 'error');
    }
  });
}

export function editItemFromDrawer() {
  if (!state.activeDrawerItem) return;
  const item = state.activeDrawerItem;
  closeSlideDrawer();
  openAddItemModal(item.location_id, item);
}

export async function deleteItemFromDrawer() {
  if (!state.activeDrawerItem) return;
  const item = state.activeDrawerItem;
  if (!confirm(`Delete "${item.name}"?`)) return;

  try {
    await apiDelete(`/api/v1/items/${item.id}`, 'Could not delete item.');
    closeSlideDrawer();
    showToast(`Deleted "${item.name}".`);
    await loadAllData();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

export async function verifyItemFromView(itemId) {
  const item = state.items.find(i => i.id === itemId);
  if (!item) return;
  return withSubmitLock('item-view-verify-btn', 'Saving...', async () => {
    try {
      await apiPost(`/api/v1/items/${item.id}/verify`, undefined, 'Could not update last-seen time.');
      showToast(`Marked "${item.name}" as seen today!`);
      await loadAllData();
    } catch (err) {
      showToast(err.message, 'error');
    }
  });
}

export function editItemFromView(itemId) {
  const item = state.items.find(i => i.id === itemId);
  if (!item) return;
  openAddItemModal(item.location_id, item);
}

export async function deleteItemFromView(itemId) {
  const item = state.items.find(i => i.id === itemId);
  if (!item) return;
  if (!confirm(`Delete "${item.name}"?`)) return;

  try {
    await apiDelete(`/api/v1/items/${item.id}`, 'Could not delete item.');
    showToast(`Deleted "${item.name}".`);
    await loadAllData();
    handleGlobalBack();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

