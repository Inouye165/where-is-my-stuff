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
import { renderSlotCarouselHtml, syncAllSlotCarousels } from './carousel.js';
import { getRoomIconName, lucideIcon } from './icons.js';
import {
  getSlotPhotos,
  isInlineGridEditing,
  mountInlineGridDesigner,
  renderContainerGridHtml,
  renderInlineGridDesignerHtml
} from './grid-designer.js';
import { chooseAddAction, openAddItemModal, openEditStorageModal, openGroupIntoBoxModal, openQuickPhotoModal, openRapidEntryModal, parseJsonInventoryInput } from './modals.js';
import { openLocationPhoneCaptureModal, registerDesktopPhotoHandler, requestPhotoForInput } from './phone-capture.js';
import { compressImageFile } from './photo-compress.js';
import { openPhotoEditorModal } from './photo-editor.js';
import {
  renderLimitedItemView,
  renderMobileFindView,
  shouldUseLimitedItemView,
  shouldUseMobileCompanion
} from './mobile-views.js';
import { handleGlobalBack, navToHome, navToItem, navToRoom, renderCurrentWorkspace } from './router.js';
import {
  formatHumanPath,
  getLocationHierarchy,
  getLocType,
  getQuickMoveRoomChoices,
  getRoomEmoji,
  getSubtreeItems,
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

// ============================================================================
// 1. ZERO-SCROLL HOME VIEW ('Places') + HERO SEARCH + 'NEEDS A HOME' TILE
// ============================================================================
export function renderHomePlacesView() {
  if (shouldUseMobileCompanion()) {
    renderMobileFindView();
    return;
  }
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;
  workspace.classList.remove('is-unit-view');
  workspace.classList.add('is-home-canvas');

  const totalItemsCount = state.items.length;
  const totalContainersCount = state.containers.length;
  const totalRoomsCount = state.namedRooms.length;
  const unassignedContainers = getUnassignedContainers();
  const unassignedLoose = getUnassignedLooseItems();
  let unassignedItemsCount = unassignedLoose.length;
  for (const c of unassignedContainers) {
    unassignedItemsCount += c.total_item_count || 0;
  }
  const placedItemsCount = Math.max(0, totalItemsCount - unassignedItemsCount);
  const organizedPct = totalItemsCount > 0 ? Math.round((placedItemsCount / totalItemsCount) * 100) : 100;

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
          <div class="home-stats-pills" id="home-total-line">
            <span class="home-stat-pill">${escapeHtml(pluralize(totalItemsCount, 'item'))}</span>
            <span class="home-stat-pill">${escapeHtml(pluralize(totalContainersCount, 'container'))}</span>
            <span class="home-stat-pill">${escapeHtml(pluralize(totalRoomsCount, 'space'))}</span>
            ${totalItemsCount > 0 ? `
              <span class="home-stat-pill ${organizedPct === 100 ? 'is-success' : 'is-progress'}" title="${placedItemsCount} of ${totalItemsCount} items placed in a space">
                <span class="home-progress-dot"></span>
                ${organizedPct === 100 ? 'All organized' : `${organizedPct}% put away`}
              </span>
            ` : ''}
          </div>
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

let homePhotoSearchSeq = 0;
let homeTextSearchSeq = 0;
let homeTextSearchTimer = null;

async function runHomeTextSearch(query) {
  const reqSeq = ++homeTextSearchSeq;
  state.homeSearchLoading = true;
  renderHomeSearchPanel();
  try {
    const res = await apiGet(`/api/v1/search?q=${encodeURIComponent(query)}&limit=50`, 'Could not search items.');
    if (reqSeq !== homeTextSearchSeq) return;
    state.homeSearchResults = Array.isArray(res?.items) ? res.items : [];
    state.homeSearchLoading = false;
    renderHomeSearchPanel();
  } catch {
    if (reqSeq !== homeTextSearchSeq) return;
    state.homeSearchResults = [];
    state.homeSearchLoading = false;
    renderHomeSearchPanel();
  }
}

export function handleHomeSearchInput(e) {
  const val = e.target.value || '';
  state.homeSearchQuery = val.trim();
  if (homeTextSearchTimer) {
    clearTimeout(homeTextSearchTimer);
    homeTextSearchTimer = null;
  }
  if (!state.homeSearchQuery && !state.photoSearchPreviewUrl) {
    state.photoSearchLoading = false;
  }

  const clearBtn = document.getElementById('home-search-clear-btn');
  if (clearBtn) {
    clearBtn.style.display = (val.length > 0 || state.photoSearchPreviewUrl) ? 'inline-flex' : 'none';
  }

  if (!state.homeSearchQuery) {
    homeTextSearchSeq += 1;
    state.homeSearchResults = null;
    state.homeSearchLoading = false;
    renderHomeSearchPanel();
    return;
  }

  state.homeSearchLoading = true;
  renderHomeSearchPanel();
  const q = state.homeSearchQuery;
  homeTextSearchTimer = setTimeout(() => {
    homeTextSearchTimer = null;
    runHomeTextSearch(q);
  }, 100);
}

export function clearHomeSearch() {
  homePhotoSearchSeq += 1;
  homeTextSearchSeq += 1;
  if (homeTextSearchTimer) {
    clearTimeout(homeTextSearchTimer);
    homeTextSearchTimer = null;
  }
  state.homeSearchQuery = '';
  state.homeSearchResults = null;
  state.homeSearchLoading = false;
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

  const reqSeq = ++homePhotoSearchSeq;

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
    const res = await apiUpload('/api/v1/search/photo', fd, 'Could not search by photo.', { timeoutMs: 70000 });
    if (reqSeq !== homePhotoSearchSeq) return;
    homeTextSearchSeq += 1;
    state.photoSearchLoading = false;
    state.homeSearchLoading = false;
    state.homeSearchResults = Array.isArray(res?.items) ? res.items : [];
    if (res && res.query) {
      state.homeSearchQuery = res.query;
      const input = document.getElementById('home-search-input');
      if (input) input.value = res.query;
    }
    renderHomeSearchPanel();
  } catch (err) {
    if (reqSeq !== homePhotoSearchSeq) return;
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
      const containerPreviews = containersInRoom.slice(0, 3).map(c => ({
        id: c.id,
        name: c.name,
        photo: sanitizePhotoUrl(c.container_photo || c.context_photo || ''),
        itemCount: c.total_item_count || 0
      }));
      populatedTiles.push({
        kind: 'room',
        id: room.id,
        name: room.name,
        iconName: getRoomIconName(room),
        photo,
        itemCount,
        containerCount,
        containerPreviews,
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
            <span class="needs-home-banner-sub">${escapeHtml(needsHomeBanner.subtitle)} waiting for a space</span>
          </div>
        </div>
        <span class="needs-home-banner-cta">Assign spaces ${lucideIcon('chevronRight', '', 16)}</span>
      </div>
    ` : ''}

    <div class="home-section-hdr">
      <div class="home-section-title-wrap">
        <h2 class="home-section-title">Your spaces</h2>
        <span class="home-section-sub">Rooms, garages, sheds, and other areas where your containers live</span>
      </div>
      <button type="button" class="btn btn-secondary btn-sm" onclick="openAddStorageModal(null, 'room')">
        ${lucideIcon('plus', '', 15)} New space
      </button>
    </div>

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
    const sub = state.namedRooms.length === 0 ? 'No spaces added yet' : 'Create a new space';
    return `
      <div class="room-tile add-room-tile" onclick="openAddStorageModal(null, 'room')" role="button" tabindex="0">
        <div class="room-tile-visual is-empty-visual">
          <div class="room-tile-empty-badge">${lucideIcon('plus', '', 24)}</div>
        </div>
        <div class="room-tile-footer">
          <div class="room-tile-title-row">
            <h2 class="room-tile-name">Add a space</h2>
          </div>
          <span class="room-tile-sub">${escapeHtml(sub)}</span>
        </div>
      </div>
    `;
  }

  if (tile.kind === 'add-item') {
    const sub = state.items.length === 0 ? 'Snap a photo or type an item' : 'Add something new';
    return `
      <div class="room-tile add-room-tile" onclick="chooseAddAction()" role="button" tabindex="0">
        <div class="room-tile-visual is-empty-visual">
          <div class="room-tile-empty-badge">${lucideIcon('package', '', 24)}</div>
        </div>
        <div class="room-tile-footer">
          <div class="room-tile-title-row">
            <h2 class="room-tile-name">Add an item</h2>
          </div>
          <span class="room-tile-sub">${escapeHtml(sub)}</span>
        </div>
      </div>
    `;
  }

  if (tile.kind === 'empty-room') {
    const safeId = Number(tile.id);
    return `
      <div
        class="room-tile is-dimmed-room"
        onclick="openAddStorageModal(${safeId}, 'unit')"
        onkeydown="if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); openAddStorageModal(${safeId}, 'unit'); }"
        role="button"
        tabindex="0"
      >
        <div class="room-tile-visual is-empty-visual">
          <div class="room-tile-empty-badge">${lucideIcon(tile.iconName || 'home', '', 24)}</div>
          <div class="room-tile-floating-actions">
            <button
              type="button"
              class="room-tile-add-photo-btn"
              onclick="event.stopPropagation(); openQuickPhotoModal({ kind: 'spot', id: ${safeId}, title: 'Add a photo' })"
            >
              ${lucideIcon('camera', '', 13)}
              <span>Add a photo</span>
            </button>
            <div style="display: flex; align-items: center; gap: 0.35rem;">
              <button
                type="button"
                class="room-tile-edit-btn"
                title="Edit or remove ${escapeHtml(tile.name)}"
                aria-label="Edit or remove ${escapeHtml(tile.name)}"
                onclick="event.stopPropagation(); openEditStorageModal(${safeId})"
              >
                ${lucideIcon('pencil', '', 14)}
              </button>
              <button
                type="button"
                class="room-tile-plus-btn"
                title="Add container to ${escapeHtml(tile.name)}"
                aria-label="Add container to ${escapeHtml(tile.name)}"
                onclick="event.stopPropagation(); openAddStorageModal(${safeId}, 'unit')"
              >
                ${lucideIcon('plus', '', 15)}
              </button>
            </div>
          </div>
        </div>
        <div class="room-tile-footer">
          <div class="room-tile-title-row">
            <h2 class="room-tile-name">${escapeHtml(tile.name)}</h2>
          </div>
          <span class="room-tile-sub">Empty so far — add your first container</span>
        </div>
      </div>
    `;
  }

  const safePhoto = sanitizePhotoUrl(tile.photo);
  const hasPhoto = Boolean(safePhoto);
  const safeId = Number(tile.id);
  const previews = Array.isArray(tile.containerPreviews) ? tile.containerPreviews : [];
  const previewPhotos = previews.filter(p => p.photo);

  return `
    <div class="room-tile ${hasPhoto ? 'has-photo' : 'no-photo'}" onclick="navToRoom(${safeId}, true)" role="button" tabindex="0">
      <div class="room-tile-visual">
        ${hasPhoto ? `
          <img class="room-tile-cover-img" src="${escapeHtml(safePhoto)}" alt="${escapeHtml(tile.name)}" loading="lazy">
        ` : previewPhotos.length > 0 ? `
          <div class="room-tile-collage count-${Math.min(3, previewPhotos.length)}">
            ${previewPhotos.slice(0, 3).map(p => `
              <div class="room-tile-collage-cell">
                <img src="${escapeHtml(p.photo)}" alt="${escapeHtml(p.name)}" loading="lazy">
              </div>
            `).join('')}
          </div>
        ` : `
          <div class="room-tile-empty-badge">${lucideIcon(tile.iconName || 'home', '', 26)}</div>
        `}
        <div class="room-tile-floating-actions">
          ${!hasPhoto ? `
            <button
              type="button"
              class="room-tile-add-photo-btn"
              onclick="event.stopPropagation(); openQuickPhotoModal({ kind: 'spot', id: ${safeId}, title: 'Add a photo' })"
            >
              ${lucideIcon('camera', '', 13)}
              <span>Add a photo</span>
            </button>
          ` : ''}
          <button
            type="button"
            class="room-tile-edit-btn"
            title="Edit room"
            aria-label="Edit ${escapeHtml(tile.name)}"
            onclick="event.stopPropagation(); openEditStorageModal(${safeId})"
          >
            ${lucideIcon('pencil', '', 14)}
          </button>
        </div>
      </div>

      <div class="room-tile-footer">
        <div class="room-tile-title-row">
          <span class="room-tile-icon-chip">${lucideIcon(tile.iconName || 'home', '', 16)}</span>
          <h2 class="room-tile-name">${escapeHtml(tile.name)}</h2>
        </div>
        <span class="room-tile-sub">${escapeHtml(tile.subtitle)}</span>
        ${previews.length > 0 ? `
          <div class="room-tile-chips">
            ${previews.slice(0, 2).map(c => `
              <button
                type="button"
                class="room-container-chip"
                onclick="event.stopPropagation(); navToStorageUnit(${Number(c.id)}, true)"
                title="Open ${escapeHtml(c.name)}"
              >
                ${escapeHtml(c.name)}
              </button>
            `).join('')}
            ${tile.containerCount > 2 ? `<span class="room-container-more">+${tile.containerCount - 2}</span>` : ''}
          </div>
        ` : ''}
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

  if (state.photoSearchLoading || state.homeSearchLoading) {
    panel.innerHTML = `
      <div class="search-panel-photo-bar">
        ${hasPhoto ? `<img class="search-panel-preview-img" src="${escapeHtml(state.photoSearchPreviewUrl)}" alt="Photo search">` : ''}
        <div class="search-panel-status">Looking for matching items...</div>
      </div>
    `;
    return;
  }

  const matchingItems = Array.isArray(state.homeSearchResults) ? state.homeSearchResults : [];
  const locWords = query ? query.toLowerCase().split(/\s+/).filter(w => w.length > 1) : [];
  const matchingLocations = locWords.length > 0
    ? Array.from(state.locationMap.values()).filter(loc => {
      if (isUnknownPlaceholder(loc)) return false;
      const nameLow = loc.name.toLowerCase();
      return locWords.some(tok => nameLow.includes(tok));
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
    <section class="right-col-card right-col-recent-card">
      <div class="right-col-heading-row">
        <h3 class="right-col-heading">Recently added</h3>
        <button type="button" class="right-col-link-btn" onclick="switchAppTab('items')">View all →</button>
      </div>
      ${recentItems.length > 0
      ? `<div class="right-col-list">${recentItems.map(renderMiniItemRow).join('')}</div>`
      : `<p class="right-col-empty">No items added yet. Items you add will show up here.</p>`
    }
    </section>

    <section class="right-col-card right-col-actions-card">
      <h3 class="right-col-heading">Quick actions</h3>
      <div class="right-col-actions-grid">
        <button type="button" class="quick-action-tile" onclick="chooseAddAction('snap')">
          <span class="quick-action-icon">${lucideIcon('camera', '', 16)}</span>
          <span class="quick-action-text">
            <strong>Scan with Camera</strong>
            <span>Snap items or a drawer</span>
          </span>
        </button>
        <button type="button" class="quick-action-tile" onclick="chooseAddAction('manual')">
          <span class="quick-action-icon">${lucideIcon('package', '', 16)}</span>
          <span class="quick-action-text">
            <strong>Add Item</strong>
            <span>Save a single item quickly</span>
          </span>
        </button>
        <button type="button" class="quick-action-tile" onclick="chooseAddAction('location')">
          <span class="quick-action-icon">${lucideIcon('archive', '', 16)}</span>
          <span class="quick-action-text">
            <strong>Add Container</strong>
            <span>Box, cabinet, or shelf</span>
          </span>
        </button>
      </div>
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
  workspace.classList.remove('is-home-canvas', 'is-unit-view');
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
          <button type="button" class="btn btn-primary" onclick="navToHome()">Back to Spaces</button>
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
        <button type="button" class="context-crumb-link" onclick="navToHome()">Spaces</button>
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
  workspace.classList.remove('is-home-canvas', 'is-unit-view');
  workspace.scrollTop = 0;

  if (!room) {
    workspace.innerHTML = `<div class="empty-state"><p>Space not found.</p><button class="btn btn-secondary btn-sm" onclick="navToHome()">Back to Spaces</button></div>`;
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
  const unassignedCount = getUnassignedContainers().length;
  const roomIconName = getRoomIconName(room);

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
          <button type="button" class="btn btn-secondary btn-sm" onclick="openQuickPhotoModal({ kind: 'spot', id: ${safeRoomId}, title: 'Add space photo' })">
            ${lucideIcon('camera', '', 15)} ${roomPhoto ? 'Change photo' : 'Add space photo'}
          </button>
          ${roomPhoto ? `<button type="button" class="btn btn-secondary btn-sm" onclick="editExistingPhoto({ kind: 'spot', id: ${safeRoomId}, photoUrl: '${escapeJsStr(roomPhoto)}', title: 'Edit space photo' })">${lucideIcon('scissors', '', 15)} Crop</button>` : ''}
          <button type="button" class="btn btn-secondary btn-sm" onclick="openEditStorageModal(${safeRoomId})">Edit</button>
          <button type="button" class="btn btn-primary btn-sm" onclick="openAddStorageModal(${safeRoomId}, 'unit')">+ Add container</button>
        `
  )}

      <div class="place-hero-card room-hero-card">
        <div class="place-hero-left">
          ${roomPhoto
      ? `<img class="place-hero-thumb" src="${escapeHtml(roomPhoto)}" alt="${escapeHtml(room.name)}" onclick="openLightbox('${escapeJsStr(roomPhoto)}', '${escapeJsStr(room.name)}')">`
      : `<div class="room-hero-icon-badge">${lucideIcon(roomIconName, '', 26)}</div>`
    }
          <div style="min-width: 0;">
            <h1 class="place-hero-title">${escapeHtml(room.name)}</h1>
            ${room.description ? `<p class="place-hero-sub" style="margin-top: 0.15rem;">${escapeHtml(room.description)}</p>` : ''}
            <div class="home-stats-pills" style="margin-top: 0.45rem;">
              <span class="home-stat-pill">${escapeHtml(pluralize(containers.length, 'container'))}</span>
              <span class="home-stat-pill">${escapeHtml(pluralize(room.total_item_count || 0, 'item'))}</span>
              ${subRooms.length > 0 ? `<span class="home-stat-pill">${escapeHtml(pluralize(subRooms.length, 'sub-space'))}</span>` : ''}
            </div>
          </div>
        </div>

        <div class="room-hero-actions">
          <button type="button" class="btn btn-secondary btn-sm" onclick="scanPhoneForLocation(${safeRoomId})">
            ${lucideIcon('qrCode', '', 15)} Scan with Phone
          </button>
          <button type="button" class="btn btn-secondary btn-sm" onclick="openAddItemModal(${safeRoomId})">
            ${lucideIcon('package', '', 15)} Add item
          </button>
          ${unassignedCount > 0 ? `
            <button type="button" class="btn btn-secondary btn-sm" onclick="navToNeedsHome(true)">
              Place unassigned (${unassignedCount})
            </button>
          ` : ''}
        </div>
      </div>

      ${subRooms.length > 0 ? `
        <div class="home-section-hdr">
          <div class="home-section-title-wrap">
            <h2 class="home-section-title">Sub-spaces</h2>
            <span class="home-section-sub">${escapeHtml(pluralize(subRooms.length, 'area'))} inside ${escapeHtml(room.name)}</span>
          </div>
        </div>
        <div class="containers-grid">
          ${subRooms.map(sr => {
      const safeSubId = Number(sr.id);
      const subItemCount = sr.total_item_count || 0;
      const subChildCount = (sr.children && sr.children.length) || sr.child_count || 0;
      return `
              <div class="container-card is-rich" onclick="navToRoom(${safeSubId}, true)">
                <div class="container-card-body">
                  <div class="container-card-top">
                    <h4 class="container-card-title">${escapeHtml(sr.name)}</h4>
                    <span class="room-tile-count-badge">${escapeHtml(pluralize(subItemCount, 'item'))}</span>
                  </div>
                  <p class="container-card-meta">${escapeHtml(pluralize(subChildCount, 'container'))}</p>
                </div>
              </div>
            `;
    }).join('')}
        </div>
      ` : ''}

      ${containers.length > 0 ? `
        <div class="home-section-hdr">
          <div class="home-section-title-wrap">
            <h2 class="home-section-title">Storage containers</h2>
            <span class="home-section-sub">${escapeHtml(pluralize(containers.length, 'container'))} in ${escapeHtml(room.name)}</span>
          </div>
          <button type="button" class="btn btn-secondary btn-sm" onclick="openAddStorageModal(${safeRoomId}, 'unit')">
            ${lucideIcon('plus', '', 15)} New container
          </button>
        </div>
        <div class="containers-grid">
          ${containers.map(c => renderContainerCard(c)).join('')}
        </div>
      ` : ''}

      ${looseBins.length > 0 ? `
        <div class="home-section-hdr">
          <div class="home-section-title-wrap">
            <h2 class="home-section-title">Bins &amp; shelves</h2>
            <span class="home-section-sub">${escapeHtml(pluralize(looseBins.length, 'spot'))}</span>
          </div>
        </div>
        <div class="drawers-grid">
          ${looseBins.map(b => renderDrawerCard(b)).join('')}
        </div>
      ` : ''}

      ${directItems.length > 0 ? `
        <div class="home-section-hdr">
          <div class="home-section-title-wrap">
            <h2 class="home-section-title">Loose items in ${escapeHtml(room.name)}</h2>
            <span class="home-section-sub">${escapeHtml(pluralize(directItems.length, 'item'))} not inside a container</span>
          </div>
        </div>
        <div class="items-grid">
          ${directItems.map(item => renderItemCard(item)).join('')}
        </div>
      ` : ''}

      ${subRooms.length === 0 && containers.length === 0 && looseBins.length === 0 && directItems.length === 0 ? `
        <div class="empty-state">
          <h3 class="empty-state-title">${escapeHtml(room.name)} is empty</h3>
          <p class="empty-state-desc">Add a container (like a shelf, bin, or drawer unit) or move an existing container into this space.</p>
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

function renderContainerCard(container) {
  const safeId = Number(container.id);
  const photo = sanitizePhotoUrl(container.container_photo || container.context_photo || '');
  const drawers = Array.isArray(container.children) ? container.children : [];
  const drawerCount = drawers.length || container.child_count || 0;
  const itemCount = container.total_item_count || 0;
  const quickDrawers = drawers.slice(0, 3);

  return `
    <div class="container-card is-rich" onclick="navToStorageUnit(${safeId}, true)">
      <div class="container-card-media">
        ${photo ? `
          <img class="container-card-thumb" src="${escapeHtml(photo)}" alt="${escapeHtml(container.name)}" loading="lazy">
        ` : `
          <button
            type="button"
            class="container-card-add-photo"
            onclick="event.stopPropagation(); openQuickPhotoModal({ kind: 'bin', id: ${safeId}, title: 'Add photo' })"
            title="Add photo"
          >
            ${lucideIcon('camera', '', 18)}
            <span>Add photo</span>
          </button>
        `}
      </div>
      <div class="container-card-body">
        <div class="container-card-top">
          <h3 class="container-card-title">${escapeHtml(container.name)}</h3>
          <span class="room-tile-count-badge">${escapeHtml(pluralize(itemCount, 'item'))}</span>
        </div>

        <div class="container-card-counts">
          <span><strong>${drawerCount}</strong> ${drawerCount === 1 ? 'drawer' : 'drawers'}</span>
          <span>·</span>
          <span><strong>${itemCount}</strong> ${itemCount === 1 ? 'item' : 'items'}</span>
        </div>

        ${quickDrawers.length > 0 ? `
          <div class="room-tile-chips">
            ${quickDrawers.map(d => `
              <button
                type="button"
                class="room-container-chip"
                onclick="event.stopPropagation(); navToDrawer(${Number(d.id)}, true)"
                title="Open ${escapeHtml(d.name)}"
              >
                ${escapeHtml(d.name)}
              </button>
            `).join('')}
            ${drawerCount > 3 ? `<span class="room-container-more">+${drawerCount - 3}</span>` : ''}
          </div>
        ` : ''}
      </div>
    </div>
  `;
}

export function toSentenceCase(str) {
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
    hotspotsEl.style.display = 'none';
    hotspotsEl.innerHTML = '';
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
    workspace.innerHTML = `<div class="empty-state"><p>Container not found.</p><button class="btn btn-secondary btn-sm" onclick="navToHome()">Back to Spaces</button></div>`;
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
          <button type="button" class="btn btn-secondary btn-sm" onclick="openRapidEntryModal(${safeUnitId})">${lucideIcon('camera', '', 15)} Scan Items</button>
          <button type="button" class="btn btn-secondary btn-sm" onclick="openAddItemModal(${safeUnitId})">${lucideIcon('plus', '', 15)} Add item</button>
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
              <button type="button" class="unit-more-item" role="menuitem" onclick="closeUnitMoreMenu(false); openGroupIntoBoxModal(${safeUnitId}, { createOnly: true })">Add a Box / Kit</button>
              <button type="button" class="unit-more-item" role="menuitem" onclick="closeUnitMoreMenu(false); openEditStorageModal(${safeUnitId})">Rename or edit</button>
              <button type="button" class="unit-more-item" role="menuitem" onclick="closeUnitMoreMenu(false); scanPhoneForLocation(${safeUnitId})">Add photos via Phone (QR)</button>
              <button type="button" class="unit-more-item" role="menuitem" onclick="closeUnitMoreMenu(false); openPrintQrModal(${safeUnitId})">Print QR label</button>
              <button type="button" class="unit-more-item" role="menuitem" onclick="openMoveContainerModal(${safeUnitId})">Move to another space</button>
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
          ${unplacedDrawers.filter(d => !['box', 'kit'].includes(getLocType(d))).length > 0 ? `
            <div class="drawers-grid" style="margin-top: 0.9rem;">
              ${unplacedDrawers.filter(d => !['box', 'kit'].includes(getLocType(d))).map(d => renderDrawerCard(d)).join('')}
            </div>
          ` : ''}
          ${unplacedDrawers.filter(d => ['box', 'kit'].includes(getLocType(d))).length > 0 ? `
            <div class="drawer-subboxes-section" style="margin-top: 0.9rem;">
              <div class="drawer-loose-header">
                <span class="drawer-section-label">Boxes &amp; Kits in ${escapeHtml(unit.name)}</span>
              </div>
              <div class="drawer-subboxes-stack">
                ${unplacedDrawers.filter(d => ['box', 'kit'].includes(getLocType(d))).map(b => renderExpandableSubBoxCard(b)).join('')}
              </div>
            </div>
          ` : ''}
        ` : `
          <div class="drawers-grid" id="storage-drawers-container">
            ${drawers.filter(d => !['box', 'kit'].includes(getLocType(d))).map(d => renderDrawerCard(d)).join('')}
            <button type="button" class="drawer-card drawer-card-add" onclick="openAddStorageModal(${safeUnitId}, 'drawer')">
              ${lucideIcon('plus', '', 20)}
              <span>Add drawer</span>
            </button>
          </div>
          ${drawers.filter(d => ['box', 'kit'].includes(getLocType(d))).length > 0 ? `
            <div class="drawer-subboxes-section" style="margin-top: 0.9rem;">
              <div class="drawer-loose-header">
                <span class="drawer-section-label">Boxes &amp; Kits in ${escapeHtml(unit.name)}</span>
                <button type="button" class="btn btn-secondary btn-xs" onclick="openGroupIntoBoxModal(${safeUnitId}, { createOnly: true })">
                  ${lucideIcon('plus', '', 12)} Add Box / Kit
                </button>
              </div>
              <div class="drawer-subboxes-stack">
                ${drawers.filter(d => ['box', 'kit'].includes(getLocType(d))).map(b => renderExpandableSubBoxCard(b)).join('')}
              </div>
            </div>
          ` : ''}
        `}

        ${directItems.length > 0 ? `
          <div class="drawer-loose-header" style="margin-top: 1rem;">
            <span class="drawer-section-label">Loose items in ${escapeHtml(unit.name)} (${directItems.length})</span>
            <button type="button" class="btn btn-secondary btn-xs" onclick="openGroupIntoBoxModal(${safeUnitId})">
              ${lucideIcon('package', '', 13)} Put items in a Box / Kit
            </button>
          </div>
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
  const childBoxes = (drawer.children || []).filter(c => !isUnknownPlaceholder(c));
  const subtreeItems = getSubtreeItems(drawer.id);
  const count = subtreeItems.length || Number(drawer.total_item_count || 0);
  const snippetEntries = [
    ...childBoxes.map(b => `📦 ${toSentenceCase(b.name)}`),
    ...subtreeItems.map(i => toSentenceCase(i.name)),
  ];
  const previews = snippetEntries.slice(0, 2);
  const remaining = Math.max(0, (childBoxes.length + subtreeItems.length) - previews.length);
  const isSpotlight = drawer.id === state.spotlightDrawerId;
  const isBoxType = ['box', 'kit'].includes(getLocType(drawer));
  const photos = isBoxType ? getDrawerPhotosList(drawer) : getSlotPhotos(drawer);

  let badgeLabel = '';
  if (childBoxes.length > 0 && count > 0) {
    badgeLabel = `${pluralize(childBoxes.length, 'box')} · ${pluralize(count, 'item')}`;
  } else if (childBoxes.length > 0) {
    badgeLabel = pluralize(childBoxes.length, 'box');
  } else if (count >= (isBoxType ? 1 : 2)) {
    badgeLabel = pluralize(count, 'item');
  }

  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(syncAllSlotCarousels);
  }

  return `
    <div id="drawer-card-${safeDrawerId}" class="drawer-card ${isBoxType ? 'is-subbox-card' : ''} ${isSpotlight ? 'spotlight-highlight' : ''}" onclick="navToDrawer(${safeDrawerId}, true)">
      <div class="drawer-card-top">
        <div style="display: flex; flex-direction: column; gap: 0.25rem; min-width: 0; flex: 1;">
          <h4 class="drawer-card-title">${isBoxType ? '📦 ' : ''}${escapeHtml(toSentenceCase(drawer.name))}</h4>
          ${isSpotlight ? `<span class="spotlight-badge">Inside here</span>` : ''}
        </div>
        ${badgeLabel ? `<span class="drawer-badge-count">${escapeHtml(badgeLabel)}</span>` : ''}
      </div>

      ${renderSlotCarouselHtml(photos)}

      ${previews.length > 0 ? `
        <ul class="drawer-item-snippets">
          ${previews.map(label => `<li class="drawer-item-snippet">${escapeHtml(label)}</li>`).join('')}
          ${remaining > 0 ? `<li class="drawer-more-count">+ ${remaining} more</li>` : ''}
        </ul>
      ` : `
        <p class="drawer-empty-hint">Nothing here yet — tap to add items</p>
      `}
    </div>
  `;
}

let drawerLabelEditId = null;
let itemRenameEditId = null;
let itemPartsEditorState = null;

export function renderCabinetHighlightVisual(unit, drawer, options = {}) {
  const { compact = false } = options;
  const unitPhoto = sanitizePhotoUrl((unit && (unit.container_photo || unit.context_photo)) || '');
  const hasGrid = Boolean(unit && Number(unit.grid_rows) > 0 && Number(unit.grid_cols) > 0);
  const r0 = Number(drawer?.grid_row) || 0;
  const c0 = Number(drawer?.grid_col) || 0;
  const rs = Math.max(1, Number(drawer?.grid_row_span) || 1);
  const cs = Math.max(1, Number(drawer?.grid_col_span) || 1);
  const hasPlacement = hasGrid && r0 >= 1 && c0 >= 1;

  if (unitPhoto && hasPlacement) {
    const rows = Number(unit.grid_rows);
    const cols = Number(unit.grid_cols);
    const topPct = (((r0 - 1) / rows) * 100).toFixed(2);
    const heightPct = ((rs / rows) * 100).toFixed(2);
    const leftPct = (((c0 - 1) / cols) * 100).toFixed(2);
    const widthPct = ((cs / cols) * 100).toFixed(2);
    const safeUnitId = Number(unit.id);
    const safeDrawerId = Number(drawer.id);
    return `
      <div
        class="cabinet-highlight-frame ${compact ? 'is-compact' : ''}"
        title="Where ${escapeHtml(drawer.name)} sits in ${escapeHtml(unit.name)}"
        onclick="locateDrawerInUnit(${safeUnitId}, ${safeDrawerId})"
      >
        <div class="cabinet-highlight-media">
          <img src="${escapeHtml(unitPhoto)}" alt="${escapeHtml(unit.name)}">
          <div
            class="cabinet-highlight-box"
            style="top: ${topPct}%; height: ${heightPct}%; left: ${leftPct}%; width: ${widthPct}%;"
            aria-hidden="true"
          ></div>
        </div>
      </div>
    `;
  }

  if (unitPhoto) {
    return `
      <img
        class="place-hero-thumb ${compact ? 'drawer-hero-cabinet-thumb' : ''}"
        src="${escapeHtml(unitPhoto)}"
        alt="${escapeHtml(unit ? unit.name : 'Cabinet')}"
        title="${escapeHtml(unit ? unit.name : 'Cabinet')}"
        onclick="openLightbox('${escapeJsStr(unitPhoto)}', '${escapeJsStr(unit ? unit.name : 'Cabinet')}')"
        style="cursor: pointer;"
      >
    `;
  }

  if (hasGrid && drawer) {
    return `
      <div
        class="drawer-hero-grid-thumb"
        title="Where ${escapeHtml(drawer.name)} sits in ${escapeHtml(unit.name)}"
        onclick="locateDrawerInUnit(${Number(unit.id)}, ${Number(drawer.id)})"
      >
        ${renderContainerGridHtml(unit, { highlightDrawerId: Number(drawer.id), diagram: true })}
      </div>
    `;
  }

  return '';
}

export function getDrawerPhotosList(drawer) {
  const list = [];
  const seen = new Set();
  const rawPhotos = Array.isArray(drawer?.photos) ? drawer.photos : [];
  const primarySafe = sanitizePhotoUrl(drawer?.container_photo || '');

  if (primarySafe) {
    const matched = rawPhotos.find(p => sanitizePhotoUrl(p.photo_path || '') === primarySafe);
    seen.add(primarySafe);
    list.push({
      id: matched ? matched.id : null,
      url: primarySafe,
      thumb: matched ? (sanitizePhotoUrl(matched.thumbnail_path || '') || primarySafe) : primarySafe,
      caption: (matched && matched.caption) || drawer?.name || 'Photo',
    });
  }

  for (const p of rawPhotos) {
    const safe = sanitizePhotoUrl(p.photo_path || '');
    if (safe && !seen.has(safe)) {
      seen.add(safe);
      list.push({
        id: p.id,
        url: safe,
        thumb: sanitizePhotoUrl(p.thumbnail_path || '') || safe,
        caption: p.caption || drawer?.name,
      });
    }
  }

  const contextSafe = sanitizePhotoUrl(drawer?.context_photo || '');
  if (contextSafe && !seen.has(contextSafe)) {
    seen.add(contextSafe);
    list.push({ id: null, url: contextSafe, thumb: contextSafe, caption: drawer?.name || 'Photo' });
  }
  return list;
}

export function toggleDrawerLabelEdit(drawerId, editing) {
  drawerLabelEditId = editing ? drawerId : null;
  renderDrawerView(drawerId);
}

export async function saveDrawerLabel(drawerId) {
  const nameEl = document.getElementById(`drawer-label-input-${drawerId}`);
  const descEl = document.getElementById(`drawer-desc-input-${drawerId}`);
  const name = (nameEl?.value || '').trim();
  const description = (descEl?.value || '').trim() || null;
  if (!name) {
    showToast('Label cannot be empty.', 'error');
    return;
  }
  try {
    await apiPatch(`/api/v1/locations/${drawerId}`, { name, description }, 'Could not update label.');
    drawerLabelEditId = null;
    showToast('Label updated!');
    await loadAllData();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

export function scanPhoneForLocation(locationId) {
  const loc = state.locationMap.get(locationId);
  const isBoxAlready = Boolean(loc && ['box', 'kit'].includes(getLocType(loc)));
  openLocationPhoneCaptureModal({
    locationId,
    locationName: loc ? loc.name : 'Drawer',
    defaultMode: isBoxAlready ? 'separate' : 'box',
    onDone: async ({ photos, shouldScan, saveMode, userPickedMode }) => {
      await loadAllData();
      if (shouldScan && photos && photos.length > 0) {
        const updatedLoc = state.locationMap.get(locationId);
        const savedList = Array.isArray(updatedLoc?.photos) ? updatedLoc.photos : [];
        const capturedPaths = new Set(photos.map(p => p.original_path).filter(Boolean));
        const matchedIds = savedList
          .filter(p => capturedPaths.has(p.photo_path))
          .map(p => Number(p.id))
          .filter(Boolean);
        const finalPhotoIds = matchedIds.length > 0
          ? matchedIds
          : savedList.slice(0, photos.length).map(p => Number(p.id)).filter(Boolean);
        const coverPath = photos[0]?.original_path || savedList[0]?.photo_path;
        openRapidEntryModal(locationId, {
          photoId: finalPhotoIds[0] || null,
          photoIds: finalPhotoIds,
          sessionPhotoIds: finalPhotoIds,
          photoUrl: coverPath,
          saveMode: isBoxAlready ? 'separate' : (saveMode || 'box'),
          userPickedMode: Boolean(userPickedMode),
          autoScan: true,
        });
      }
    },
  });
}

export function triggerDrawerDirectPhotoUpload(drawerId) {
  const input = document.getElementById(`drawer-direct-upload-${drawerId}`);
  if (input) input.click();
}

export async function handleDrawerDirectPhotoUpload(drawerId, e) {
  const files = Array.from(e.target?.files || []).filter(Boolean);
  if (!files.length) return;
  let uploaded = 0;
  try {
    for (const file of files) {
      const compressed = await compressImageFile(file);
      const fd = new FormData();
      fd.append('file', compressed);
      await apiUpload(`/api/v1/locations/${drawerId}/photos`, fd, 'Photo upload failed.');
      uploaded += 1;
    }
    showToast(`Added ${pluralize(files.length, 'photo')}!`);
    await loadAllData();
  } catch (err) {
    const msg = err.message || 'Could not upload photo.';
    showToast(uploaded > 0 ? `${msg} (${uploaded} of ${files.length} saved before the error)` : msg, 'error');
    if (uploaded > 0) await loadAllData();
  } finally {
    if (e.target) e.target.value = '';
  }
}

export async function deleteDrawerPhoto(drawerId, photoId) {
  if (!photoId) return;
  try {
    await apiDelete(`/api/v1/locations/${drawerId}/photos/${photoId}`, 'Could not delete photo.');
    showToast('Photo removed.');
    await loadAllData();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

export async function setLocationCoverPhoto(locId, photoUrl) {
  if (!locId || !photoUrl) return;
  try {
    await apiPatch(`/api/v1/locations/${locId}`, { container_photo: photoUrl }, 'Could not update cover photo.');
    closeModal('cover-photo-picker-modal');
    showToast('Cover photo updated!');
    await loadAllData();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

const overallViewerState = {
  locationId: null,
  index: 0,
  editMode: false,
};

function renderOverallPhotoViewerContent() {
  const loc = state.locationMap.get(Number(overallViewerState.locationId));
  const photos = loc ? getDrawerPhotosList(loc) : [];
  if (!loc || photos.length === 0) {
    closeModal('overall-photo-viewer-modal');
    return;
  }

  if (overallViewerState.index < 0) overallViewerState.index = photos.length - 1;
  if (overallViewerState.index >= photos.length) overallViewerState.index = 0;

  const current = photos[overallViewerState.index];
  const titleEl = document.getElementById('overall-viewer-title');
  const counterEl = document.getElementById('overall-viewer-counter');
  const imgEl = document.getElementById('overall-viewer-img');
  const prevBtn = document.getElementById('overall-viewer-prev-btn');
  const nextBtn = document.getElementById('overall-viewer-next-btn');
  const thumbsEl = document.getElementById('overall-viewer-thumbs');
  const editBtn = document.getElementById('overall-viewer-edit-btn');
  const editBar = document.getElementById('overall-viewer-edit-bar');
  const coverBtn = document.getElementById('overall-viewer-cover-btn');
  const removeBtn = document.getElementById('overall-viewer-remove-btn');

  if (titleEl) titleEl.textContent = current.caption || loc.name || 'Overall photo';
  if (counterEl) counterEl.textContent = `${overallViewerState.index + 1} / ${photos.length}`;
  if (imgEl) imgEl.src = current.url;
  if (prevBtn) prevBtn.style.display = photos.length > 1 ? 'inline-flex' : 'none';
  if (nextBtn) nextBtn.style.display = photos.length > 1 ? 'inline-flex' : 'none';

  if (thumbsEl) {
    if (photos.length > 1) {
      thumbsEl.style.display = 'flex';
      thumbsEl.innerHTML = photos.map((p, idx) => `
        <button
          type="button"
          class="overall-viewer-thumb-btn ${idx === overallViewerState.index ? 'active' : ''}"
          onclick="openOverallPhotoViewer(${Number(loc.id)}, ${idx}, ${overallViewerState.editMode ? 'true' : 'false'})"
          title="${escapeHtml(p.caption || `Photo ${idx + 1}`)}"
        >
          <img src="${escapeHtml(p.thumb || p.url)}" alt="${escapeHtml(p.caption || `Photo ${idx + 1}`)}">
        </button>
      `).join('');
    } else {
      thumbsEl.style.display = 'none';
      thumbsEl.innerHTML = '';
    }
  }

  const editing = Boolean(overallViewerState.editMode);
  if (editBtn) {
    editBtn.textContent = editing ? 'Editing' : 'Edit';
    editBtn.classList.toggle('btn-primary', editing);
    editBtn.classList.toggle('btn-secondary', !editing);
  }
  if (editBar) editBar.hidden = !editing;
  if (coverBtn) {
    const isCover = sanitizePhotoUrl(loc.container_photo || '') === current.url;
    coverBtn.style.display = photos.length > 1 && !isCover ? 'inline-flex' : 'none';
  }
  if (removeBtn) {
    removeBtn.disabled = false;
  }
}

export function openOverallPhotoViewer(locationId, index = 0, keepEditMode = false) {
  const loc = state.locationMap.get(Number(locationId));
  const photos = loc ? getDrawerPhotosList(loc) : [];
  if (!loc || photos.length === 0) return;

  overallViewerState.locationId = Number(locationId);
  overallViewerState.index = Number(index) || 0;
  overallViewerState.editMode = Boolean(keepEditMode);

  renderOverallPhotoViewerContent();
  document.getElementById('overall-photo-viewer-modal')?.classList.add('open');
}

export function stepOverallPhotoViewer(delta) {
  overallViewerState.index += Number(delta) || 0;
  renderOverallPhotoViewerContent();
}

export function toggleOverallPhotoEditMode(forceState) {
  overallViewerState.editMode = typeof forceState === 'boolean'
    ? forceState
    : !overallViewerState.editMode;
  renderOverallPhotoViewerContent();
}

export function overallViewerAddPhoto() {
  const locId = Number(overallViewerState.locationId);
  if (!locId) return;
  closeModal('overall-photo-viewer-modal');
  openQuickPhotoModal({ kind: 'location-photo', id: locId, title: 'Add overall photo' });
}

export function overallViewerCropPhoto() {
  const loc = state.locationMap.get(Number(overallViewerState.locationId));
  const photos = loc ? getDrawerPhotosList(loc) : [];
  const current = photos[overallViewerState.index];
  if (!loc || !current) return;
  closeModal('overall-photo-viewer-modal');
  editExistingPhoto({ kind: 'bin', id: Number(loc.id), photoUrl: current.url, title: 'Edit overall photo' });
}

export async function overallViewerSetCover() {
  const loc = state.locationMap.get(Number(overallViewerState.locationId));
  const photos = loc ? getDrawerPhotosList(loc) : [];
  const current = photos[overallViewerState.index];
  if (!loc || !current) return;
  await setLocationCoverPhoto(Number(loc.id), current.url);
  overallViewerState.index = 0;
  renderOverallPhotoViewerContent();
}

export async function overallViewerRemovePhoto() {
  const loc = state.locationMap.get(Number(overallViewerState.locationId));
  const photos = loc ? getDrawerPhotosList(loc) : [];
  const current = photos[overallViewerState.index];
  if (!loc || !current) return;

  return withSubmitLock('overall-viewer-remove-btn', 'Removing...', async () => {
    try {
      if (current.id) {
        await deleteDrawerPhoto(Number(loc.id), Number(current.id));
      } else {
        const patch = {};
        if (sanitizePhotoUrl(loc.container_photo || '') === current.url) patch.container_photo = null;
        if (sanitizePhotoUrl(loc.context_photo || '') === current.url) patch.context_photo = null;
        await apiPatch(`/api/v1/locations/${Number(loc.id)}`, patch, 'Could not remove photo.');
        showToast('Photo removed.');
        await loadAllData();
      }
      const updatedLoc = state.locationMap.get(Number(loc.id));
      const remaining = updatedLoc ? getDrawerPhotosList(updatedLoc) : [];
      if (remaining.length === 0) {
        closeModal('overall-photo-viewer-modal');
      } else {
        overallViewerState.index = Math.min(overallViewerState.index, remaining.length - 1);
        renderOverallPhotoViewerContent();
      }
    } catch (err) {
      showToast(err.message || 'Could not remove photo.', 'error');
    }
  });
}

export function openBoxCoverPhotoPicker(boxId) {
  const box = state.locationMap.get(boxId);
  if (!box) return;
  const photos = getDrawerPhotosList(box);
  const titleEl = document.getElementById('cover-photo-picker-title');
  const gridEl = document.getElementById('cover-photo-picker-grid');
  if (titleEl) titleEl.textContent = `Choose cover photo — ${box.name}`;
  if (gridEl) {
    const activeUrl = sanitizePhotoUrl(box.container_photo || '') || (photos[0]?.url || '');
    gridEl.innerHTML = photos.map((p) => {
      const isActive = p.url === activeUrl;
      return `
        <button
          type="button"
          class="cover-picker-option ${isActive ? 'is-active' : ''}"
          onclick="setLocationCoverPhoto(${Number(box.id)}, '${escapeJsStr(p.url)}')"
        >
          <img src="${escapeHtml(p.thumb || p.url)}" alt="${escapeHtml(p.caption || box.name)}">
          ${isActive ? '<span class="cover-picker-active-tag">Current</span>' : ''}
        </button>
      `;
    }).join('');
  }
  document.getElementById('cover-photo-picker-modal')?.classList.add('open');
}

let drawerItemsViewMode = (typeof localStorage !== 'undefined' && localStorage.getItem('wims_drawer_view_mode')) === 'list' ? 'list' : 'grid';
let drawerPhotosExpanded = false;
let lastQuickAddFeedback = null;
let drawerInlineRenameId = null;
let drawerGenericReviewMode = false;

export function setDrawerItemsViewMode(mode, drawerId) {
  drawerItemsViewMode = mode === 'grid' ? 'grid' : 'list';
  try {
    if (typeof localStorage !== 'undefined') localStorage.setItem('wims_drawer_view_mode', drawerItemsViewMode);
  } catch { /* ignore storage errors */ }
  const targetId = Number(drawerId) || state.currentLocationId || lastRenderedDrawerId;
  if (targetId) renderDrawerView(targetId);
}

export function toggleDrawerPhotosStrip(drawerId) {
  drawerPhotosExpanded = !drawerPhotosExpanded;
  const targetId = Number(drawerId) || state.currentLocationId || lastRenderedDrawerId;
  if (targetId) renderDrawerView(targetId);
}

export function dismissQuickAddFeedback(drawerId) {
  lastQuickAddFeedback = null;
  const targetId = Number(drawerId) || state.currentLocationId || lastRenderedDrawerId;
  if (targetId) renderDrawerView(targetId);
}

export function toggleQuickAddMoreMenu(e) {
  if (e) e.stopPropagation();
  const menu = document.getElementById('drawer-quick-add-more-menu');
  const btn = document.getElementById('drawer-quick-add-more-btn');
  if (!menu) return;
  const willOpen = Boolean(menu.hidden);
  menu.hidden = !willOpen;
  if (btn) btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
}

export function startInlineItemRename(itemId) {
  const safeId = Number(itemId);
  if (!safeId) return;
  drawerInlineRenameId = safeId;
  const targetId = state.currentLocationId || lastRenderedDrawerId;
  if (targetId) renderDrawerView(targetId);
  requestAnimationFrame(() => {
    const input = document.getElementById(`inline-item-rename-${safeId}`);
    if (input) {
      input.focus();
      input.select();
    }
  });
}

export function cancelInlineItemRename() {
  drawerInlineRenameId = null;
  const targetId = state.currentLocationId || lastRenderedDrawerId;
  if (targetId) renderDrawerView(targetId);
}

export async function saveInlineItemRename(itemId) {
  const safeId = Number(itemId);
  const input = document.getElementById(`inline-item-rename-${safeId}`);
  const nextName = (input?.value || '').trim();
  if (!nextName) {
    showToast('Item name cannot be empty.', 'error');
    return;
  }
  try {
    await apiPut(`/api/v1/items/${safeId}`, { name: nextName }, 'Could not rename item.');
    drawerInlineRenameId = null;
    await loadAllData();
    if (drawerGenericReviewMode) {
      const targetId = Number(state.currentLocationId || lastRenderedDrawerId);
      const remaining = state.items.filter(
        it => Number(it.location_id) === targetId && isGenericAiItemName(it.name)
      );
      if (remaining.length > 0) {
        startInlineItemRename(remaining[0].id);
      } else {
        drawerGenericReviewMode = false;
        if (targetId) renderDrawerView(targetId);
      }
    }
    showToast('Item renamed.');
  } catch (err) {
    showToast(err.message || 'Could not rename item.', 'error');
  }
}

export function toggleGenericReviewMode(drawerId) {
  const targetId = Number(drawerId) || state.currentLocationId || lastRenderedDrawerId;
  drawerGenericReviewMode = !drawerGenericReviewMode;
  if (drawerGenericReviewMode && targetId) {
    const firstGeneric = state.items.find(
      it => Number(it.location_id) === Number(targetId) && isGenericAiItemName(it.name)
    );
    drawerInlineRenameId = firstGeneric ? Number(firstGeneric.id) : null;
  } else {
    drawerInlineRenameId = null;
  }
  if (targetId) renderDrawerView(targetId);
  if (drawerGenericReviewMode && drawerInlineRenameId) {
    const focusId = drawerInlineRenameId;
    requestAnimationFrame(() => {
      const input = document.getElementById(`inline-item-rename-${focusId}`);
      if (input) {
        input.focus();
        input.select();
      }
    });
  }
}

export async function deleteDrawerFromMenu(drawerId) {
  const loc = state.locationMap.get(Number(drawerId));
  if (!loc) return;
  if (!window.confirm(`Delete "${loc.name}"?`)) return;
  try {
    await apiDelete(`/api/v1/locations/${loc.id}`, 'Could not delete drawer.');
    showToast(`${loc.name} deleted.`);
    const parent = loc.parent_id ? state.locationMap.get(loc.parent_id) : null;
    await loadAllData();
    if (parent && getLocType(parent) === 'container') {
      navToStorageUnit(parent.id, true);
    } else if (parent && !isUnknownPlaceholder(parent)) {
      navToDrawer(parent.id, true);
    } else {
      navToHome();
    }
  } catch (err) {
    showToast(err.message || 'Could not delete drawer.', 'error');
  }
}

export async function quickAddDrawerItems(drawerId) {
  const input = document.getElementById(`drawer-quick-add-${drawerId}`);
  const raw = (input?.value || '').trim();
  if (!raw) {
    showToast('Type an item name, comma/line list, or paste JSON first.', 'error');
    return;
  }
  const jsonResult = parseJsonInventoryInput(raw);
  try {
    if (jsonResult && jsonResult.items.length > 0) {
      const existing = state.items.filter(i => Number(i.location_id) === Number(drawerId));
      const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
      const itemsToCreate = [];
      let skipped = 0;
      for (const it of jsonResult.items) {
        const partKey = norm(it.part_number);
        const nameKey = norm(it.name);
        const isDup = existing.some(ex =>
          (partKey && norm(ex.serial_or_part_number) === partKey) ||
          (nameKey && norm(ex.name) === nameKey)
        );
        if (isDup) {
          skipped += 1;
          continue;
        }
        const tagList = Array.isArray(it.tags) ? it.tags : (typeof it.tags === 'string' ? [it.tags] : []);
        const tagStr = tagList.map(t => String(t || '').trim()).filter(Boolean).join(', ').slice(0, 300);
        itemsToCreate.push({
          name: String(it.name).trim().slice(0, 160),
          quantity: Math.min(1000000, Math.max(1, Math.round(Number(it.estimated_qty || 1)) || 1)),
          category: (String(it.category || jsonResult.groupCategory || '').trim()).slice(0, 80) || null,
          serial_or_part_number: (it.part_number && String(it.part_number).trim().slice(0, 120)) || null,
          description: (it.brief_specs && String(it.brief_specs).trim().slice(0, 20000)) || null,
          tags: tagStr || null,
          location_id: drawerId,
        });
      }
      if (!itemsToCreate.length) {
        if (input) input.value = '';
        showToast('All items in that JSON list are already in this container.');
        return;
      }
      const created = await apiPost('/api/v1/items/bulk', { location_id: drawerId, items: itemsToCreate }, 'Could not add items.');
      if (input) input.value = '';
      lastQuickAddFeedback = Array.isArray(created) && created.length > 0
        ? { drawerId: Number(drawerId), items: created.map(i => ({ name: i.name, quantity: Number(i.quantity) || 1 })) }
        : null;
      showToast(`Added ${pluralize(created.length, 'item')}${skipped ? ` (${skipped} already in container skipped)` : ''}!`);
      await loadAllData();
      return;
    }

    const lines = raw.includes('\n') ? raw : raw.split(',').map(s => s.trim()).filter(Boolean).join('\n');
    const created = await apiPost('/api/v1/items/bulk', { location_id: drawerId, raw_lines: lines }, 'Could not add items.');
    if (input) input.value = '';
    lastQuickAddFeedback = Array.isArray(created) && created.length > 0
      ? { drawerId: Number(drawerId), items: created.map(i => ({ name: i.name, quantity: Number(i.quantity) || 1 })) }
      : null;
    showToast(`Added ${pluralize(created.length, 'item')}!`);
    await loadAllData();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

const itemQtySyncState = new Map();

export async function adjustDrawerItemQty(itemId, delta) {
  const item = state.items.find(i => i.id === itemId);
  if (!item) return;

  let entry = itemQtySyncState.get(itemId);
  if (entry && entry.deleting) return;

  const baseQty = entry
    ? entry.targetQty
    : (item.quantity === null || item.quantity === undefined ? 1 : Number(item.quantity));
  const nextQty = baseQty + delta;

  if (nextQty <= 0 && !window.confirm(`Remove "${item.name}" from this bin?`)) return;

  if (!entry) {
    entry = { targetQty: nextQty, running: false, deleting: false };
    itemQtySyncState.set(itemId, entry);
  } else {
    entry.targetQty = nextQty;
  }

  if (nextQty > 0) {
    item.quantity = nextQty;
    renderCurrentWorkspace();
  }

  if (entry.running) return;
  entry.running = true;

  try {
    while (true) {
      const desiredQty = entry.targetQty;
      if (desiredQty <= 0) {
        entry.deleting = true;
        await apiDelete(`/api/v1/items/${itemId}`, 'Could not remove item.');
        showToast(`Removed ${item.name} from bin.`);
        break;
      }
      await apiPatch(`/api/v1/items/${itemId}`, { quantity: desiredQty }, 'Could not update quantity.');
      if (entry.targetQty === desiredQty) break;
    }
    itemQtySyncState.delete(itemId);
    await loadAllData();
  } catch (err) {
    itemQtySyncState.delete(itemId);
    showToast(err.message, 'error');
    await loadAllData();
  }
}

export async function removeDrawerItem(itemId) {
  const item = state.items.find(i => i.id === itemId);
  if (!item) return;
  if (!window.confirm(`Remove "${item.name}"?`)) return;
  try {
    await apiDelete(`/api/v1/items/${itemId}`, 'Could not remove item.');
    showToast(`Removed ${item.name}.`);
    await loadAllData();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function summarizeDrawerItemDescription(description) {
  if (!description) return '';
  const parsed = parseItemDescriptionAndParts(description);
  if (parsed.parts.length > 0) {
    const preview = parsed.parts.slice(0, 3).map(p => (p.qty > 1 ? `${p.qty}x ${p.name}` : p.name)).join(', ');
    const more = parsed.parts.length > 3 ? ` +${parsed.parts.length - 3} more` : '';
    return `${pluralize(parsed.parts.length, 'part')}: ${preview}${more}`;
  }
  return String(description).trim();
}

export function toggleItemRowMenu(itemId, e) {
  if (e) e.stopPropagation();
  const targetMenu = document.getElementById(`item-row-menu-${itemId}`);
  const wasOpen = Boolean(targetMenu && !targetMenu.hidden);
  document.querySelectorAll('.drawer-row-menu').forEach(m => { m.hidden = true; });
  if (targetMenu) {
    targetMenu.hidden = wasOpen;
  }
}

export function toggleBoxRowMenu(boxId, e) {
  if (e) e.stopPropagation();
  const targetMenu = document.getElementById(`box-row-menu-${boxId}`);
  const wasOpen = Boolean(targetMenu && !targetMenu.hidden);
  document.querySelectorAll('.drawer-row-menu').forEach(m => { m.hidden = true; });
  if (targetMenu) {
    targetMenu.hidden = wasOpen;
  }
}

if (typeof document !== 'undefined') {
  document.addEventListener('click', (e) => {
    if (!e.target?.closest?.('.drawer-row-more-wrap')) {
      document.querySelectorAll('.drawer-row-menu').forEach(m => { m.hidden = true; });
    }
    if (!e.target?.closest?.('.unit-more-wrap')) {
      const dm = document.getElementById('drawer-more-menu');
      if (dm) dm.hidden = true;
      const qm = document.getElementById('drawer-quick-add-more-menu');
      if (qm) qm.hidden = true;
    }
  });
}

function isGenericAiItemName(name) {
  const n = String(name || '').trim().toLowerCase();
  if (!n) return false;
  if (n.startsWith('unknown ')) return true;
  return /^(silver|black|yellow|white|gray|grey|red|blue|green|orange|brown|clear|plastic|metal)\s+(electronic\s+)?(module|component|part|item|device|board|box|bag|tool|adapter|cable)$/.test(n);
}

function getCleanDrawerItemMeta(item) {
  const rawSnippet = summarizeDrawerItemDescription(item.description);
  if (!rawSnippet) return [];
  const norm = (s) => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  const isUnknownDesc = /^unknown\b/i.test(rawSnippet.trim());
  const isDuplicateName = norm(rawSnippet) === norm(item.name);
  if (isUnknownDesc || isDuplicateName) return [];
  return [rawSnippet];
}

function renderDrawerItemCard(item, mode = 'list') {
  const safeId = Number(item.id);
  const qty = item.quantity === null || item.quantity === undefined ? 1 : Number(item.quantity);
  const thumb = sanitizePhotoUrl(item.item_photo || '');
  const metaParts = getCleanDrawerItemMeta(item, qty);
  const isInlineRenaming = drawerInlineRenameId === safeId;
  const qtyChipHtml = qty > 1 ? `<span class="drawer-item-qty-chip">Qty ${qty}</span>` : '';

  const inlineRenameHtml = `
    <div class="drawer-item-inline-rename" onclick="event.stopPropagation()">
      <input
        type="text"
        id="inline-item-rename-${safeId}"
        class="drawer-item-inline-rename-input"
        value="${escapeHtml(item.name)}"
        aria-label="Item name"
        onkeydown="if (event.key === 'Enter') { event.preventDefault(); saveInlineItemRename(${safeId}); } else if (event.key === 'Escape') { event.preventDefault(); cancelInlineItemRename(); }"
      >
      <button type="button" class="btn btn-primary btn-xs" onclick="saveInlineItemRename(${safeId})">Save</button>
      <button type="button" class="btn btn-secondary btn-xs" onclick="cancelInlineItemRename()">Cancel</button>
    </div>
  `;

  const menuHtml = `
    <div class="drawer-item-actions drawer-row-more-wrap" onclick="event.stopPropagation()">
      <button
        type="button"
        class="btn btn-secondary btn-xs drawer-row-more-btn"
        aria-haspopup="menu"
        aria-label="Item actions"
        title="More actions"
        onclick="toggleItemRowMenu(${safeId}, event)"
      >${lucideIcon('moreHorizontal', '', 15)}</button>
      <div id="item-row-menu-${safeId}" class="unit-more-menu drawer-row-menu" role="menu" hidden>
        <button type="button" class="unit-more-item" role="menuitem" onclick="startInlineItemRename(${safeId})">
          ${lucideIcon('pencil', '', 14)} Rename
        </button>
        <div class="drawer-row-menu-qty">
          <span class="drawer-row-menu-qty-label">Qty</span>
          <div class="drawer-row-menu-qty-controls">
            <button type="button" class="btn btn-secondary btn-xs" title="Decrease quantity" onclick="adjustDrawerItemQty(${safeId}, -1)">−</button>
            <span class="drawer-item-qty">${qty}</span>
            <button type="button" class="btn btn-secondary btn-xs" title="Increase quantity" onclick="adjustDrawerItemQty(${safeId}, 1)">+</button>
          </div>
        </div>
        <button type="button" class="unit-more-item" role="menuitem" onclick="editItemFromView(${safeId})">
          ${lucideIcon('pencil', '', 14)} Edit item
        </button>
        <button type="button" class="unit-more-item" role="menuitem" onclick="openQuickPhotoModal({ kind: 'item', id: ${safeId}, title: 'Replace item photo' })">
          ${lucideIcon('camera', '', 14)} Replace photo
        </button>
        <button type="button" class="unit-more-item" role="menuitem" onclick="findAndImportItemPhoto(${safeId})">
          ${lucideIcon('search', '', 14)} Auto-find photo
        </button>
        <button type="button" class="unit-more-item unit-more-item-danger" role="menuitem" onclick="removeDrawerItem(${safeId})">
          ${lucideIcon('x', '', 14)} Remove
        </button>
      </div>
    </div>
  `;

  if (mode === 'grid') {
    return `
      <div class="drawer-item-grid-card" onclick="${isInlineRenaming ? '' : `navToItem(${safeId}, true)`}">
        <div class="drawer-item-grid-media">
          ${thumb
            ? `<img class="drawer-item-grid-img" src="${escapeHtml(thumb)}" alt="${escapeHtml(item.name)}" loading="lazy">`
            : `<div class="drawer-item-grid-empty">${lucideIcon('package', '', 28)}</div>`}
          ${qty > 1 ? `<span class="drawer-item-grid-qty">×${qty}</span>` : ''}
          ${menuHtml}
        </div>
        <div class="drawer-item-grid-body">
          ${isInlineRenaming ? inlineRenameHtml : `<h4 class="drawer-item-title">${escapeHtml(toSentenceCase(item.name))}</h4>`}
          ${metaParts.length > 0 ? `<span class="drawer-item-inline-meta">${escapeHtml(metaParts.join(' · '))}</span>` : ''}
        </div>
      </div>
    `;
  }

  return `
    <div class="drawer-item-row">
      <div class="drawer-item-row-main" onclick="${isInlineRenaming ? '' : `navToItem(${safeId}, true)`}">
        ${thumb
          ? `<img class="drawer-item-thumb" src="${escapeHtml(thumb)}" alt="${escapeHtml(item.name)}" loading="lazy">`
          : `<div class="drawer-item-thumb drawer-item-thumb-empty">${lucideIcon('package', '', 22)}</div>`
        }
        <div class="drawer-item-info">
          ${isInlineRenaming ? inlineRenameHtml : `
            <div class="drawer-item-title-line">
              <h4 class="drawer-item-title">${escapeHtml(toSentenceCase(item.name))}</h4>
              ${qtyChipHtml}
            </div>
          `}
          ${metaParts.length > 0 ? `<span class="drawer-item-inline-meta">${escapeHtml(metaParts.join(' · '))}</span>` : ''}
        </div>
      </div>
      ${menuHtml}
    </div>
  `;
}

export async function findAndImportItemPhoto(itemId) {
  showToast('Searching for part photo...');
  try {
    await apiPost(`/api/v1/items/${itemId}/find-image`, {}, 'No matching photo found online.', { timeoutMs: 120000 });
    showToast('Imported part photo!');
    await loadAllData();
  } catch (err) {
    showToast(err.message || 'No matching photo found online.', 'error');
  }
}

function buildLocationBreadcrumbs(hier, currentLocId = null) {
  const crumbs = [];
  if (hier.isNeedsHome) {
    crumbs.push({ label: UNASSIGNED_ROOM_LABEL, onclick: 'navToNeedsHome(true)' });
  }
  const trail = Array.isArray(hier.trail) ? hier.trail : [];
  for (const node of trail) {
    const nid = Number(node.id);
    if (currentLocId !== null && nid === Number(currentLocId)) {
      crumbs.push({ label: node.name });
      continue;
    }
    const nt = getLocType(node);
    if (nt === 'room' || nt === 'site') {
      crumbs.push({ label: node.name, onclick: `navToRoom(${nid}, true)` });
    } else if (nt === 'container' || (hier.unit && nid === Number(hier.unit.id))) {
      crumbs.push({ label: node.name, onclick: `navToStorageUnit(${nid}, true)` });
    } else {
      crumbs.push({ label: node.name, onclick: `navToDrawer(${nid}, true)` });
    }
  }
  return crumbs;
}

const expandedSubBoxIds = new Set();
let lastRenderedDrawerId = null;

export function expandSubBox(boxId) {
  if (boxId) expandedSubBoxIds.add(Number(boxId));
}

export function toggleExpandSubBox(boxId) {
  const id = Number(boxId);
  if (!id) return;
  if (expandedSubBoxIds.has(id)) {
    expandedSubBoxIds.delete(id);
  } else {
    expandedSubBoxIds.add(id);
  }
  if (state.currentView === 'unit' && state.currentLocationId) {
    renderStorageUnitView(state.currentLocationId);
  } else if (state.currentLocationId || lastRenderedDrawerId) {
    renderDrawerView(state.currentLocationId || lastRenderedDrawerId);
  } else {
    renderCurrentWorkspace();
  }
}

function renderExpandableSubBoxCard(box) {
  const safeBoxId = Number(box.id);
  const isExpanded = expandedSubBoxIds.has(safeBoxId);
  const boxPhotos = getDrawerPhotosList(box);
  const mainBoxPhoto = boxPhotos[0] || null;
  const directBoxItems = state.items.filter(item => item.location_id === box.id);
  const nestedBoxes = (box.children || []).filter(c => !isUnknownPlaceholder(c));
  const subtreeItems = getSubtreeItems(box.id);
  const partCount = subtreeItems.length || Number(box.total_item_count || 0);

  return `
    <div id="drawer-card-${safeBoxId}" class="subbox-expand-card ${isExpanded ? 'is-expanded' : ''}">
      <div class="subbox-expand-header" onclick="toggleExpandSubBox(${safeBoxId})">
        <span class="subbox-expand-chevron ${isExpanded ? 'is-open' : ''}" aria-hidden="true">
          ${lucideIcon('chevronRight', '', 16)}
        </span>
        ${mainBoxPhoto ? `
          <div class="subbox-thumb-wrap">
            <img
              class="subbox-expand-thumb"
              src="${escapeHtml(mainBoxPhoto.thumb || mainBoxPhoto.url)}"
              alt="${escapeHtml(box.name)}"
              title="View full box photo"
              onclick="event.stopPropagation(); openLightbox('${escapeJsStr(mainBoxPhoto.url)}', '${escapeJsStr(box.name)}')"
            >
          </div>
        ` : `
          <div class="subbox-expand-thumb subbox-expand-thumb-empty">${lucideIcon('package', '', 18)}</div>
        `}
        <div class="subbox-expand-info">
          <div class="subbox-expand-title-row">
            <h4 class="subbox-expand-title">${escapeHtml(box.name)}</h4>
            <span class="drawer-badge-count">${escapeHtml(pluralize(partCount, 'item'))}</span>
          </div>
        </div>
        <div class="subbox-expand-actions" onclick="event.stopPropagation()">
          <button type="button" class="btn btn-secondary btn-xs" onclick="navToDrawer(${safeBoxId}, true)" title="Open full box view">
            Open →
          </button>
          <div class="drawer-row-more-wrap">
            <button
              type="button"
              class="btn btn-secondary btn-xs drawer-row-more-btn"
              aria-haspopup="menu"
              aria-label="Box actions"
              onclick="toggleBoxRowMenu(${safeBoxId}, event)"
            >${lucideIcon('moreHorizontal', '', 15)}</button>
            <div id="box-row-menu-${safeBoxId}" class="unit-more-menu drawer-row-menu" role="menu" hidden>
              <button type="button" class="unit-more-item" role="menuitem" onclick="toggleBoxRowMenu(${safeBoxId}, event); openRapidEntryModal(${safeBoxId})">
                ${lucideIcon('camera', '', 14)} Scan / paste items
              </button>
              <button type="button" class="unit-more-item" role="menuitem" onclick="toggleBoxRowMenu(${safeBoxId}, event); openAddItemModal(${safeBoxId})">
                ${lucideIcon('plus', '', 14)} Add item
              </button>
              <button type="button" class="unit-more-item" role="menuitem" onclick="toggleBoxRowMenu(${safeBoxId}, event); openQuickPhotoModal({ kind: 'box', id: ${safeBoxId}, title: 'Replace container photo' })">
                ${lucideIcon('upload', '', 14)} Replace photo
              </button>
              <button type="button" class="unit-more-item" role="menuitem" onclick="toggleBoxRowMenu(${safeBoxId}, event); openEditStorageModal(${safeBoxId})">
                ${lucideIcon('pencil', '', 14)} Edit box
              </button>
            </div>
          </div>
        </div>
      </div>

      ${isExpanded ? `
        <div class="subbox-expand-body">
          ${nestedBoxes.length > 0 ? `
            <div class="drawer-subboxes-stack" style="margin-bottom: 0.5rem;">
              ${nestedBoxes.map(nb => renderExpandableSubBoxCard(nb)).join('')}
            </div>
          ` : ''}
          ${directBoxItems.length > 0 ? `
            <div class="drawer-items-list subbox-items-list">
              ${directBoxItems.map(item => renderDrawerItemCard(item)).join('')}
            </div>
          ` : `
            <div class="subbox-empty-row">
              <span>No items in this box yet.</span>
              <div style="display: flex; gap: 0.4rem;">
                <button type="button" class="btn btn-primary btn-xs" onclick="openRapidEntryModal(${safeBoxId})">${lucideIcon('camera', '', 12)} Scan Items</button>
                <button type="button" class="btn btn-secondary btn-xs" onclick="openAddItemModal(${safeBoxId})">+ Add Item</button>
              </div>
            </div>
          `}
        </div>
      ` : ''}
    </div>
  `;
}

export function toggleDrawerMoreMenu(e) {
  if (e) e.stopPropagation();
  const menu = document.getElementById('drawer-more-menu');
  const btn = document.getElementById('drawer-more-btn');
  if (!menu) return;
  const willOpen = Boolean(menu.hidden);
  menu.hidden = !willOpen;
  if (btn) btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
}

export function renderDrawerView(drawerId) {
  const drawer = state.locationMap.get(drawerId);
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;
  workspace.classList.remove('is-home-canvas', 'is-unit-view');
  workspace.scrollTop = 0;

  if (!drawer) {
    workspace.innerHTML = `<div class="empty-state"><p>Drawer not found.</p><button class="btn btn-secondary btn-sm" onclick="navToHome()">Back to Spaces</button></div>`;
    return;
  }

  const safeDrawerId = Number(drawer.id);
  if (lastQuickAddFeedback && Number(lastQuickAddFeedback.drawerId) !== safeDrawerId) {
    lastQuickAddFeedback = null;
  }
  if (lastRenderedDrawerId !== safeDrawerId) {
    drawerInlineRenameId = null;
    drawerGenericReviewMode = false;
  }
  lastRenderedDrawerId = safeDrawerId;
  const hier = getLocationHierarchy(drawer.id);
  const crumbs = buildLocationBreadcrumbs(hier, safeDrawerId);
  if (!crumbs.length) crumbs.push({ label: drawer.name });

  const childBoxes = (drawer.children || []).filter(c => !isUnknownPlaceholder(c));
  const items = state.items.filter(item => item.location_id === drawer.id);
  const genericItems = items.filter(it => isGenericAiItemName(it.name));
  if (drawerGenericReviewMode && genericItems.length === 0) {
    drawerGenericReviewMode = false;
  }
  const visibleItems = drawerGenericReviewMode ? genericItems : items;
  const visibleBoxes = drawerGenericReviewMode ? [] : childBoxes;
  const totalSubtreeCount = getSubtreeItems(drawer.id).length || items.length;
  const isBoxLoc = ['box', 'kit'].includes(getLocType(drawer)) || Boolean(hier.box && Number(hier.box.id) === safeDrawerId);
  const photos = getDrawerPhotosList(drawer);
  const activePhoto = photos[0] || null;
  const isEditingLabel = drawerLabelEditId === safeDrawerId;
  const spotPhoto = sanitizePhotoUrl(drawer.context_photo || '');
  const cabinetDrawerRef = hier.drawer || drawer;
  const cabinetVisualHtml = isBoxLoc
    ? (activePhoto
      ? `<img class="place-hero-thumb drawer-hero-cabinet-thumb" src="${escapeHtml(activePhoto.thumb || activePhoto.url)}" alt="${escapeHtml(drawer.name)}" onclick="openLightbox('${escapeJsStr(activePhoto.url)}', '${escapeJsStr(drawer.name)}')" style="cursor: pointer;">`
      : '')
    : (renderCabinetHighlightVisual(hier.unit, cabinetDrawerRef, { compact: true })
      || (spotPhoto
        ? `<img class="place-hero-thumb drawer-hero-cabinet-thumb" src="${escapeHtml(spotPhoto)}" alt="${escapeHtml(drawer.name)}" onclick="openLightbox('${escapeJsStr(spotPhoto)}', '${escapeJsStr(drawer.name)}')" style="cursor: pointer;">`
        : ''));

  const overallCarouselEntries = photos.map((p, idx) => ({
    url: p.url,
    thumb: p.thumb,
    label: p.caption || drawer.name,
    photoId: p.id,
    navType: 'overall',
    locationId: safeDrawerId,
    overallIndex: idx,
  }));

  const countSummaryParts = [];
  if (childBoxes.length > 0) countSummaryParts.push(pluralize(childBoxes.length, 'container'));
  countSummaryParts.push(pluralize(totalSubtreeCount, 'item'));

  const feedbackItems = (lastQuickAddFeedback && Number(lastQuickAddFeedback.drawerId) === safeDrawerId)
    ? lastQuickAddFeedback.items
    : [];
  const parentLoc = drawer.parent_id ? state.locationMap.get(Number(drawer.parent_id)) : null;
  const parentContainerName = (parentLoc && !isUnknownPlaceholder(parentLoc))
    ? parentLoc.name
    : (hier.unit && Number(hier.unit.id) !== safeDrawerId ? hier.unit.name : '');
  const collapsedPreviewPhotos = overallCarouselEntries.slice(0, 3);

  workspace.innerHTML = `
    <div class="workspace-inner is-drawer-view">
      ${renderStickyContextBar(
    crumbs,
    `
          <button type="button" class="btn btn-secondary btn-sm" onclick="openPrintQrModal(${safeDrawerId})">${lucideIcon('qrCode', '', 15)} Print label</button>
          <div class="unit-more-wrap">
            <button
              type="button"
              id="drawer-more-btn"
              class="btn btn-secondary btn-sm unit-more-btn"
              aria-haspopup="menu"
              aria-expanded="false"
              aria-label="Drawer actions"
              title="Drawer actions"
              onclick="toggleDrawerMoreMenu(event)"
            >${lucideIcon('moreHorizontal', '', 16)}</button>
            <div id="drawer-more-menu" class="unit-more-menu" role="menu" hidden>
              <button
                type="button"
                class="unit-more-item"
                role="menuitem"
                onclick="toggleDrawerMoreMenu(event); openQuickPhotoModal({ kind: 'location-photo', id: ${safeDrawerId}, title: 'Add drawer photo' })"
              >${lucideIcon('camera', '', 14)} Add drawer photo</button>
              <button
                type="button"
                class="unit-more-item"
                role="menuitem"
                onclick="toggleDrawerMoreMenu(event); toggleDrawerLabelEdit(${safeDrawerId}, true)"
              >${lucideIcon('pencil', '', 14)} Rename drawer</button>
              <button
                type="button"
                class="unit-more-item"
                role="menuitem"
                onclick="toggleDrawerMoreMenu(event); openPrintQrModal(${safeDrawerId})"
              >${lucideIcon('qrCode', '', 14)} Print label</button>
              <button
                type="button"
                class="unit-more-item"
                role="menuitem"
                onclick="toggleDrawerMoreMenu(event); openGroupIntoBoxModal(${safeDrawerId}, { createOnly: true })"
              >${lucideIcon('package', '', 14)} + New container</button>
              ${items.length > 0 ? `
                <button
                  type="button"
                  class="unit-more-item"
                  role="menuitem"
                  onclick="toggleDrawerMoreMenu(event); openGroupIntoBoxModal(${safeDrawerId})"
                >${lucideIcon('package', '', 14)} Combine items into a container</button>
              ` : ''}
              <button
                type="button"
                class="unit-more-item"
                role="menuitem"
                onclick="toggleDrawerMoreMenu(event); openEditStorageModal(${safeDrawerId})"
              >${lucideIcon('arrowRight', '', 14)} Move</button>
              <button
                type="button"
                class="unit-more-item unit-more-item-danger"
                role="menuitem"
                onclick="toggleDrawerMoreMenu(event); deleteDrawerFromMenu(${safeDrawerId})"
              >${lucideIcon('x', '', 14)} Delete</button>
            </div>
          </div>
        `
  )}

      <div class="drawer-title-row">
        <div class="drawer-title-thumb-wrap">
          ${cabinetVisualHtml || `<div class="drawer-title-emoji">${lucideIcon(isBoxLoc ? 'package' : 'archive', '', 22)}</div>`}
        </div>
        <div class="drawer-hero-title-wrap">
          ${isEditingLabel ? `
            <div class="drawer-label-editor">
              <input type="text" id="drawer-label-input-${safeDrawerId}" value="${escapeHtml(drawer.name)}" placeholder="Container or drawer label" style="margin-bottom: 0.4rem;">
              <input type="text" id="drawer-desc-input-${safeDrawerId}" value="${escapeHtml(drawer.description || '')}" placeholder="Optional notes">
              <div style="display: flex; gap: 0.45rem; margin-top: 0.5rem;">
                <button type="button" class="btn btn-primary btn-sm" onclick="saveDrawerLabel(${safeDrawerId})">Save label</button>
                <button type="button" class="btn btn-secondary btn-sm" onclick="toggleDrawerLabelEdit(${safeDrawerId}, false)">Cancel</button>
              </div>
            </div>
          ` : `
            <div class="drawer-title-heading-line">
              <h1 class="drawer-title-heading">${escapeHtml(drawer.name)}</h1>
              ${isBoxLoc ? `<span class="subbox-type-pill">Container</span>` : ''}
            </div>
            ${parentContainerName ? `<p class="drawer-title-subtitle">${escapeHtml(parentContainerName)}</p>` : ''}
          `}
        </div>
        <input type="file" id="drawer-direct-upload-${safeDrawerId}" accept="image/*" multiple style="display: none;" onchange="handleDrawerDirectPhotoUpload(${safeDrawerId}, event)">
      </div>

      <div class="drawer-quick-add-wrap">
        <div class="drawer-quick-add-bar">
          <span class="drawer-quick-add-icon" aria-hidden="true">${lucideIcon('plus', '', 18)}</span>
          <input
            type="text"
            id="drawer-quick-add-${safeDrawerId}"
            placeholder="Add to this drawer, e.g. 'AA batteries x4'"
            onkeydown="if (event.key === 'Enter') { event.preventDefault(); quickAddDrawerItems(${safeDrawerId}); }"
          >
          <button type="button" class="btn btn-primary btn-sm drawer-quick-add-submit" onclick="quickAddDrawerItems(${safeDrawerId})">Add</button>
          <button type="button" class="btn btn-secondary btn-sm" onclick="scanPhoneForLocation(${safeDrawerId})" title="Photograph items with your phone to add them">
            ${lucideIcon('camera', '', 15)} Photograph items
          </button>
          <div class="unit-more-wrap">
            <button
              type="button"
              id="drawer-quick-add-more-btn"
              class="btn btn-secondary btn-sm unit-more-btn"
              aria-haspopup="menu"
              aria-expanded="false"
              aria-label="Import and more add options"
              title="Import and more add options"
              onclick="toggleQuickAddMoreMenu(event)"
            >${lucideIcon('moreHorizontal', '', 15)}</button>
            <div id="drawer-quick-add-more-menu" class="unit-more-menu" role="menu" hidden>
              <button type="button" class="unit-more-item" role="menuitem" onclick="toggleQuickAddMoreMenu(event); openRapidEntryModal(${safeDrawerId}, { openLlmPanel: true })">
                ${lucideIcon('list', '', 14)} Bulk import
              </button>
              <button type="button" class="unit-more-item" role="menuitem" onclick="toggleQuickAddMoreMenu(event); openAddItemModal(${safeDrawerId})">
                ${lucideIcon('plus', '', 14)} Detailed add form
              </button>
            </div>
          </div>
        </div>

        ${feedbackItems.length > 0 ? `
          <div class="drawer-quick-add-feedback" role="status">
            <span class="drawer-quick-add-feedback-label">Added</span>
            <div class="drawer-quick-add-feedback-chips">
              ${feedbackItems.map(it => `
                <span class="drawer-parsed-chip">
                  <span class="drawer-parsed-name">${escapeHtml(it.name)}</span>
                  <span class="drawer-parsed-qty">Qty ${it.quantity}</span>
                </span>
              `).join('')}
            </div>
            <button type="button" class="drawer-quick-add-feedback-close" aria-label="Dismiss" onclick="dismissQuickAddFeedback(${safeDrawerId})">×</button>
          </div>
        ` : ''}
      </div>

      <div class="drawer-photos-strip ${drawerPhotosExpanded ? 'is-expanded' : ''}">
        ${overallCarouselEntries.length > 0 ? `
          <button
            type="button"
            class="drawer-photos-strip-toggle drawer-photos-strip-row"
            aria-expanded="${drawerPhotosExpanded ? 'true' : 'false'}"
            onclick="toggleDrawerPhotosStrip(${safeDrawerId})"
          >
            <span class="drawer-photos-strip-left">
              ${lucideIcon('camera', '', 14)}
              <span>Drawer photos</span>
              <span class="drawer-carousel-count">${overallCarouselEntries.length}</span>
              <span class="drawer-photos-chevron">${drawerPhotosExpanded ? '▾' : '▸'}</span>
            </span>
            ${!drawerPhotosExpanded ? `
              <span class="drawer-photos-preview-thumbs" aria-hidden="true">
                ${collapsedPreviewPhotos.map(p => `
                  <img class="drawer-photos-preview-thumb" src="${escapeHtml(p.thumb)}" alt="" loading="lazy">
                `).join('')}
              </span>
            ` : ''}
          </button>
          ${drawerPhotosExpanded ? `
            <div class="drawer-photos-strip-body">
              ${renderSlotCarouselHtml(overallCarouselEntries, {
                trailingAddAction: {
                  onclick: `openQuickPhotoModal({ kind: 'location-photo', id: ${safeDrawerId}, title: 'Add drawer photo' })`,
                  label: 'Add drawer photo',
                },
              })}
            </div>
          ` : ''}
        ` : `
          <button
            type="button"
            class="drawer-photos-strip-toggle drawer-photos-strip-row is-empty"
            onclick="openQuickPhotoModal({ kind: 'location-photo', id: ${safeDrawerId}, title: 'Add drawer photo' })"
          >
            <span class="drawer-photos-strip-left">
              ${lucideIcon('camera', '', 14)}
              <span>Add a photo of this drawer</span>
            </span>
          </button>
        `}
      </div>

      ${(childBoxes.length > 0 || items.length > 0) ? `
        <div class="drawer-items-toolbar">
          <div class="drawer-items-toolbar-left">
            <span class="drawer-items-toolbar-label">${escapeHtml(countSummaryParts.join(' · '))}</span>
            ${genericItems.length > 0 ? `
              <span class="drawer-generic-nudge">
                · ${drawerGenericReviewMode
                  ? `Reviewing ${genericItems.length} generic · <button type="button" class="drawer-generic-review-link" onclick="toggleGenericReviewMode(${safeDrawerId})">Show all</button>`
                  : `${pluralize(genericItems.length, 'item')} ${genericItems.length === 1 ? 'has a generic name' : 'have generic names'} · <button type="button" class="drawer-generic-review-link" onclick="toggleGenericReviewMode(${safeDrawerId})">Review</button>`}
              </span>
            ` : ''}
          </div>
          <div class="drawer-view-toggle" role="group" aria-label="View mode">
            <button
              type="button"
              class="drawer-view-toggle-btn ${drawerItemsViewMode === 'list' ? 'is-active' : ''}"
              aria-pressed="${drawerItemsViewMode === 'list' ? 'true' : 'false'}"
              onclick="setDrawerItemsViewMode('list', ${safeDrawerId})"
              title="List view"
            >${lucideIcon('list', '', 14)} List</button>
            <button
              type="button"
              class="drawer-view-toggle-btn ${drawerItemsViewMode === 'grid' ? 'is-active' : ''}"
              aria-pressed="${drawerItemsViewMode === 'grid' ? 'true' : 'false'}"
              onclick="setDrawerItemsViewMode('grid', ${safeDrawerId})"
              title="Grid view"
            >${lucideIcon('grid', '', 14)} Grid</button>
          </div>
        </div>
      ` : ''}

      <div class="drawer-items-list ${drawerItemsViewMode === 'grid' ? 'is-grid' : ''}">
        ${visibleBoxes.map(b => renderExpandableSubBoxCard(b)).join('')}
        ${visibleItems.map(item => renderDrawerItemCard(item, drawerItemsViewMode)).join('')}
        ${(childBoxes.length === 0 && items.length === 0) ? `
          <div class="empty-state">
            <h3 class="empty-state-title">This container is empty</h3>
            <p class="empty-state-desc">Type items in the bar above or click Photograph items.</p>
          </div>
        ` : ''}
      </div>
    </div>
  `;
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(syncAllSlotCarousels);
  }
}

function formatItemStatusLabel(status) {
  if (!status) return 'Stored';
  return String(status)
    .replace(/_/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase());
}

function formatBytes(bytes) {
  const n = Number(bytes || 0);
  if (!n) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function triggerItemAttachmentUpload(itemId) {
  const input = document.getElementById(`item-attachment-input-${itemId}`);
  if (input) input.click();
}

export async function handleItemAttachmentUpload(itemId, e) {
  const files = Array.from(e.target?.files || []).filter(Boolean);
  if (!files.length) return;
  try {
    for (const file of files) {
      const fd = new FormData();
      fd.append('file', file);
      await apiUpload(`/api/v1/items/${itemId}/attachments`, fd, 'Attachment upload failed.');
    }
    showToast(`Added ${pluralize(files.length, 'file')}!`);
    await loadAllData();
    await loadItemAttachmentsIntoView(itemId);
  } catch (err) {
    showToast(err.message || 'Could not upload file.', 'error');
  } finally {
    if (e.target) e.target.value = '';
  }
}

export async function deleteItemAttachment(itemId, attachmentId) {
  try {
    await apiDelete(`/api/v1/items/${itemId}/attachments/${attachmentId}`, 'Could not delete file.');
    showToast('File removed.');
    await loadAllData();
    await loadItemAttachmentsIntoView(itemId);
  } catch (err) {
    showToast(err.message, 'error');
  }
}

let itemAttachmentsLoadSeq = 0;

async function loadItemAttachmentsIntoView(itemId) {
  const container = document.getElementById(`item-attachments-list-${itemId}`);
  if (!container) return;
  const reqSeq = ++itemAttachmentsLoadSeq;
  try {
    const attachments = await apiGet(`/api/v1/items/${itemId}/attachments`);
    if (reqSeq !== itemAttachmentsLoadSeq) return;
    const target = document.getElementById(`item-attachments-list-${itemId}`);
    if (!target) return;
    if (!Array.isArray(attachments) || attachments.length === 0) {
      target.innerHTML = `<p class="drawer-empty-hint" style="margin: 0.25rem 0 0;">No datasheets, charts, or files attached yet.</p>`;
      return;
    }
    target.innerHTML = attachments.map(att => {
      const safeUrl = sanitizePhotoUrl(att.file_path || '');
      const safeThumb = sanitizePhotoUrl(att.thumbnail_path || '');
      const isImg = att.attachment_type === 'image' && safeUrl;
      const badge = String(att.attachment_type || 'doc').toUpperCase();
      const sizeStr = formatBytes(att.size_bytes);
      return `
        <div class="item-attachment-row">
          <div class="item-attachment-left">
            ${isImg
          ? `<img class="item-attachment-thumb" src="${escapeHtml(safeThumb || safeUrl)}" alt="${escapeHtml(att.filename)}" onclick="openLightbox('${escapeJsStr(safeUrl)}', '${escapeJsStr(att.filename)}')">`
          : `<span class="item-attachment-badge">${escapeHtml(badge)}</span>`
        }
            <div style="min-width: 0;">
              <div class="item-attachment-name">${escapeHtml(att.title || att.filename)}</div>
              <div class="item-attachment-meta">${escapeHtml(att.filename)}${sizeStr ? ` · ${escapeHtml(sizeStr)}` : ''}</div>
            </div>
          </div>
          <div style="display: flex; gap: 0.35rem; flex-shrink: 0;">
            ${isImg
          ? `<button type="button" class="btn btn-secondary btn-xs" onclick="openLightbox('${escapeJsStr(safeUrl)}', '${escapeJsStr(att.filename)}')">View</button>`
          : (safeUrl ? `<a class="btn btn-secondary btn-xs" href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener">Open</a>` : '')
        }
            <button type="button" class="btn btn-secondary btn-xs danger-text" onclick="deleteItemAttachment(${Number(itemId)}, ${Number(att.id)})">Delete</button>
          </div>
        </div>
      `;
    }).join('');
  } catch {
    if (reqSeq !== itemAttachmentsLoadSeq) return;
    const target = document.getElementById(`item-attachments-list-${itemId}`);
    if (target) target.innerHTML = `<p class="drawer-empty-hint" style="margin: 0.25rem 0 0;">Could not load attachments.</p>`;
  }
}

export function renderItemView(itemId) {
  if (shouldUseLimitedItemView(itemId)) {
    renderLimitedItemView(itemId);
    return;
  }
  const item = state.items.find(i => i.id === itemId);
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;
  workspace.classList.remove('is-home-canvas', 'is-unit-view');
  workspace.scrollTop = 0;

  if (!item) {
    workspace.innerHTML = `
      <div class="workspace-inner">
        <div class="empty-state">
          <h3 class="empty-state-title">Item not found</h3>
          <p class="empty-state-desc">This item may have been deleted or moved.</p>
          <div style="display: flex; gap: 0.6rem; justify-content: center;">
            <button type="button" class="btn btn-primary btn-sm" onclick="navToHome()">Back to Spaces</button>
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

  const crumbs = buildLocationBreadcrumbs(hier, null);
  crumbs.push({ label: item.name });

  const itemPhoto = sanitizePhotoUrl(item.item_photo || '');
  const insidePhoto = sanitizePhotoUrl(
    (hier.unit && (hier.unit.container_photo || hier.unit.context_photo)) || ''
  );
  const spotPhoto = sanitizePhotoUrl(
    (loc && loc.context_photo) || (item.location_photos && item.location_photos.context_photo) || ''
  );

  const hasQty = item.quantity !== null && item.quantity !== undefined;
  const tagList = item.tags
    ? String(item.tags).split(',').map(t => t.trim()).filter(Boolean)
    : [];
  const statusLabel = formatItemStatusLabel(item.status);
  const partsSectionHtml = renderItemPartsSection(item);
  const notesHtml = renderItemNotesSection(item);
  const isRenamingItem = itemRenameEditId === safeItemId;

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
            ${isRenamingItem ? `
              <form class="mobile-limited-rename-form" style="margin-top: 0.35rem;" onsubmit="event.preventDefault(); saveItemRename(${safeItemId});">
                <input
                  type="text"
                  id="item-rename-input-${safeItemId}"
                  class="mobile-limited-rename-input"
                  value="${escapeHtml(item.name)}"
                  placeholder="Item name"
                  onkeydown="if (event.key === 'Escape') { event.preventDefault(); toggleItemRenameEdit(${safeItemId}, false); }"
                >
                <div class="mobile-limited-rename-actions">
                  <button type="submit" class="btn btn-primary btn-sm">Save</button>
                  <button type="button" class="btn btn-secondary btn-sm" onclick="toggleItemRenameEdit(${safeItemId}, false)">Cancel</button>
                </div>
              </form>
            ` : `
              <div style="display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap;">
                <h1 class="place-hero-title">${escapeHtml(item.name)}</h1>
                <button type="button" class="btn btn-secondary btn-xs" onclick="toggleItemRenameEdit(${safeItemId}, true)">${lucideIcon('pencil', '', 12)} Rename</button>
              </div>
            `}
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
        <span class="detail-section-title">Where it's at &amp; what it looks like</span>
        <div class="item-view-photos-grid" style="margin-bottom: 0.85rem;">
          <div class="detail-photo-card">
            <div
              class="detail-photo-thumb item-view-photo-thumb"
              onclick="${itemPhoto ? `openLightbox('${escapeJsStr(itemPhoto)}', '${escapeJsStr(item.name)}')` : `openQuickPhotoModal({ kind: 'item', id: ${safeItemId}, title: 'Add photo' })`}"
            >
              ${itemPhoto ? `<img src="${escapeHtml(itemPhoto)}" alt="${escapeHtml(item.name)}">` : `<span style="font-size: 0.78rem; color: var(--accent-text); font-weight: 700;">+ Add item photo</span>`}
            </div>
            <span class="detail-photo-caption">What it looks like</span>
            ${itemPhoto ? `<button type="button" class="detail-photo-edit-btn" onclick="editExistingPhoto({ kind: 'item', id: ${safeItemId}, photoUrl: '${escapeJsStr(itemPhoto)}', title: 'Edit item photo' })">${lucideIcon('scissors', '', 12)} Crop</button>` : ''}
            <button type="button" class="detail-photo-edit-btn" onclick="openQuickPhotoModal({ kind: 'item', id: ${safeItemId}, title: 'Replace item photo' })">${lucideIcon('camera', '', 12)} Replace</button>
            <button type="button" class="detail-photo-edit-btn" onclick="findAndImportItemPhoto(${safeItemId})">${lucideIcon('search', '', 12)} Auto-find</button>
          </div>

          <div class="detail-photo-card">
            <div
              class="detail-photo-thumb item-view-photo-thumb"
              ${insidePhoto ? `onclick="openLightbox('${escapeJsStr(insidePhoto)}', '${escapeJsStr(hier.unit ? hier.unit.name : 'Container')}')"` : 'style="cursor: default;"'}
            >
              ${insidePhoto ? `<img src="${escapeHtml(insidePhoto)}" alt="${escapeHtml(hier.unit ? hier.unit.name : 'Container')}">` : `<span style="font-size: 0.78rem; color: var(--text-muted);">No container photo</span>`}
            </div>
            <span class="detail-photo-caption">${escapeHtml(hier.unit ? hier.unit.name : 'Container')}</span>
          </div>

          <div class="detail-photo-card">
            ${(hier.unit && insidePhoto && Number(hier.unit.grid_rows) > 0 && Number(hier.unit.grid_cols) > 0 && Number((hier.drawer || loc)?.grid_row) >= 1) ? `
              <div class="detail-photo-thumb item-view-photo-thumb is-cabinet-highlight">
                ${renderCabinetHighlightVisual(hier.unit, hier.drawer || loc)}
              </div>
            ` : (hier.unit && Number(hier.unit.grid_rows) > 0 && Number(hier.unit.grid_cols) > 0) ? `
              <div
                class="detail-photo-thumb item-view-photo-thumb is-grid-diagram"
                style="cursor: pointer;"
                title="Show in container"
                onclick="locateDrawerInUnit(${Number(hier.unit.id)}, ${Number(hier.drawer?.id || item.location_id || 0)})"
              >
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
            <span class="detail-photo-caption">Where in ${escapeHtml(hier.unit ? hier.unit.name : 'container')}</span>
          </div>
        </div>

        <div class="item-where-grid">
          <div
            class="location-step-item ${hier.isNeedsHome ? 'is-needs-home' : ''}"
            onclick="${hier.room ? `navToRoom(${Number(hier.room.id)}, true)` : `navToNeedsHome(true)`}"
          >
            <span class="step-emoji">${hier.room ? escapeHtml(getRoomEmoji(hier.room)) : '📌'}</span>
            <div class="step-content">
              <span class="step-level-tag">Space</span>
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
              <span class="step-level-val">${escapeHtml(hier.unit ? hier.unitName : 'None / Loose in space')}</span>
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
              <span class="step-level-val" ${(hier.drawer && !hier.box) ? 'style="color: var(--accent-text);"' : ''}>${escapeHtml(hier.drawer ? hier.drawerName : (hier.unit ? hier.unitName : 'Unassigned'))}</span>
            </div>
            ${(hier.drawer || loc) ? `<span style="color: var(--accent-text); font-weight: 700;">→</span>` : ''}
          </div>

          ${(hier.subLocations || []).map((sub, idx, arr) => `
            <div
              class="location-step-item"
              onclick="navToDrawer(${Number(sub.id)}, true)"
            >
              <span class="step-emoji">${lucideIcon('package', '', 18)}</span>
              <div class="step-content">
                <span class="step-level-tag">Box / Kit</span>
                <span class="step-level-val" ${idx === arr.length - 1 ? 'style="color: var(--accent-text);"' : ''}>${escapeHtml(sub.name)}</span>
              </div>
              <span style="color: var(--accent-text); font-weight: 700;">→</span>
            </div>
          `).join('')}
        </div>
      </section>

      <div class="item-detail-grid" id="item-detail-grid-${safeItemId}">
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

          ${partsSectionHtml}

          <section class="item-detail-card">
            ${notesHtml || `
              <span class="detail-section-title">Notes</span>
              <div class="item-notes-box" style="color: var(--text-muted); display: flex; justify-content: space-between; align-items: center; gap: 0.75rem; flex-wrap: wrap;">
                <span>No notes or specs added yet.</span>
                <div style="display: flex; gap: 0.45rem; flex-wrap: wrap;">
                  <button type="button" class="btn btn-secondary btn-sm" onclick="editItemFromView(${safeItemId})">Add notes</button>
                </div>
              </div>
            `}
          </section>
        </div>

        <div class="item-detail-col">
          <section class="item-detail-card">
            <div style="display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.6rem;">
              <span class="detail-section-title" style="margin-bottom: 0;">Datasheets, Charts &amp; Files</span>
              <div>
                <input type="file" id="item-attachment-input-${safeItemId}" multiple accept="image/*,.pdf,.svg,.csv,.txt,.md,.json,.xlsx,.xls,.doc,.docx,.zip" style="display: none;" onchange="handleItemAttachmentUpload(${safeItemId}, event)">
                <button type="button" class="btn btn-secondary btn-sm" onclick="triggerItemAttachmentUpload(${safeItemId})">+ Add PDF / chart / file</button>
              </div>
            </div>
            <div id="item-attachments-list-${safeItemId}" class="item-attachments-list">
              <p class="drawer-empty-hint" style="margin: 0.25rem 0 0;">Loading files...</p>
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
  loadItemAttachmentsIntoView(safeItemId);
}

let itemAuditLoadSeq = 0;

async function loadItemAuditHistory(itemId) {
  const container = document.getElementById(`item-audit-history-${itemId}`);
  if (!container) return;
  const reqSeq = ++itemAuditLoadSeq;
  try {
    const detail = await apiGet(`/api/v1/items/${itemId}`, 'Could not load item history.');
    if (reqSeq !== itemAuditLoadSeq) return;
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
    if (reqSeq !== itemAuditLoadSeq) return;
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
  workspace.classList.remove('is-home-canvas', 'is-unit-view');

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
      const allowed = new Set(getSubtreeItems(target.id).map(item => item.id));
      filtered = filtered.filter(item => allowed.has(item.id));
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
  } else {
    // Toolbar persists across re-renders to keep input focus; refresh data-derived parts.
    const searchInput = document.getElementById('items-search-input');
    if (searchInput) searchInput.placeholder = `Search all ${state.items.length} items...`;
    const containerSelect = document.getElementById('toolbar-storage-filter');
    if (containerSelect && document.activeElement !== containerSelect) {
      containerSelect.innerHTML = `
        <option value="">All containers</option>
        ${state.containers.map(c => `
          <option value="${Number(c.id)}" ${String(c.id) === String(state.selectedContainerId) ? 'selected' : ''}>
            ${escapeHtml(c.name)}
          </option>
        `).join('')}
      `;
    }
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
// 5. ITEM DETAIL HELPERS & ACTIONS
// ============================================================================
function parsePartBulletLine(rawLine) {
  let line = String(rawLine || '').trim().replace(/^(?:[•\-*]|\d+[\.\)])\s*/, '').trim();
  if (!line) return null;

  let qty = 1;
  const qtyMatch = line.match(/^(\d+)\s*[xX]\s+(.*)$/);
  if (qtyMatch) {
    qty = Math.max(1, parseInt(qtyMatch[1], 10) || 1);
    line = qtyMatch[2].trim();
  }

  let specs = '';
  if (line.includes(' — ')) {
    const [head, tail] = line.split(' — ', 2);
    if (head.trim()) {
      line = head.trim();
      specs = (tail || '').trim();
    }
  }

  let partNumber = '';
  const pnMatch = line.match(/\[([^\]]+)\]\s*$/);
  if (pnMatch) {
    partNumber = pnMatch[1].trim();
    line = line.slice(0, pnMatch.index).trim();
  }

  if (!line) return null;
  return { qty, name: line, partNumber, specs };
}

function parseItemDescriptionAndParts(description) {
  const raw = String(description || '');
  if (!raw.trim()) {
    return { parts: [], otherNotes: '', hasPartsHeader: false };
  }

  const lines = raw.split(/\r?\n/);
  const headerIdx = lines.findIndex(l => /^parts\s+list\s*:/i.test(l.trim()));
  if (headerIdx === -1) {
    return { parts: [], otherNotes: raw.trim(), hasPartsHeader: false };
  }

  const beforeLines = lines.slice(0, headerIdx);
  const parts = [];
  const afterLines = [];
  let inParts = true;

  for (let i = headerIdx + 1; i < lines.length; i++) {
    const trimmed = lines[i].trim();
    if (inParts) {
      if (!trimmed) {
        continue;
      }
      if (/^notes\s*:/i.test(trimmed)) {
        inParts = false;
        const remainder = trimmed.replace(/^notes\s*:\s*/i, '').trim();
        if (remainder) afterLines.push(remainder);
        continue;
      }
      const parsed = parsePartBulletLine(trimmed);
      if (parsed) {
        parts.push(parsed);
      } else {
        inParts = false;
        afterLines.push(lines[i]);
      }
    } else {
      afterLines.push(lines[i]);
    }
  }

  const otherNotes = [...beforeLines, ...afterLines].join('\n').trim();
  return { parts, otherNotes, hasPartsHeader: true };
}

function renderItemPartsSection(item) {
  const safeItemId = Number(item.id);
  const parsed = parseItemDescriptionAndParts(item.description);
  const isEditing = itemPartsEditorState && itemPartsEditorState.itemId === safeItemId;

  if (!isEditing && parsed.parts.length === 0) {
    return `
      <section class="item-detail-card">
        <div style="display: flex; justify-content: space-between; align-items: center; gap: 0.6rem; flex-wrap: wrap;">
          <div>
            <span class="detail-section-title" style="margin-bottom: 0.15rem;">Parts List / Assortment Contents</span>
            <p class="drawer-empty-hint" style="margin: 0;">Track individual components or values inside this container or kit.</p>
          </div>
          <button type="button" class="btn btn-secondary btn-sm" onclick="toggleItemPartsEditor(${safeItemId}, true)">
            ${lucideIcon('plus', '', 14)} Add parts list
          </button>
        </div>
      </section>
    `;
  }

  if (isEditing) {
    const editParts = itemPartsEditorState.parts || [];
    return `
      <section class="item-detail-card item-parts-editor">
        <div style="display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.65rem;">
          <span class="detail-section-title" style="margin-bottom: 0;">Edit Parts List (${pluralize(editParts.length, 'part')})</span>
          <div style="display: flex; gap: 0.4rem;">
            <button type="button" class="btn btn-secondary btn-sm" onclick="toggleItemPartsEditor(${safeItemId}, false)">Cancel</button>
            <button type="button" class="btn btn-primary btn-sm" id="save-item-parts-btn-${safeItemId}" onclick="saveItemPartsList(${safeItemId})">Save parts list</button>
          </div>
        </div>
        <div class="ai-results-list" style="margin-bottom: 0.65rem;">
          ${editParts.length > 0 ? editParts.map((part, idx) => `
            <div class="ai-detected-row ai-editable-row">
              <div class="ai-editable-fields">
                <div class="ai-editable-top">
                  <input
                    type="text"
                    class="ai-edit-input ai-edit-name"
                    value="${escapeHtml(part.name || '')}"
                    placeholder="Part name (e.g. 2N2222 NPN Transistor)"
                    aria-label="Part name"
                    oninput="updateItemPartField(${safeItemId}, ${idx}, 'name', this.value)"
                  >
                  <div class="ai-edit-qty-wrap" title="Quantity">
                    <span class="ai-edit-qty-prefix">×</span>
                    <input
                      type="number"
                      class="ai-edit-input ai-edit-qty"
                      min="1"
                      max="1000000"
                      value="${Math.max(1, Number(part.qty || 1))}"
                      aria-label="Quantity"
                      oninput="updateItemPartField(${safeItemId}, ${idx}, 'qty', this.value)"
                    >
                  </div>
                  <button
                    type="button"
                    class="ai-edit-del-btn"
                    title="Remove part"
                    aria-label="Remove part"
                    onclick="removeItemPartFromEditor(${safeItemId}, ${idx})"
                  >×</button>
                </div>
                <div class="ai-editable-sub">
                  <input
                    type="text"
                    class="ai-edit-input ai-edit-partno"
                    value="${escapeHtml(part.partNumber || '')}"
                    placeholder="Part # / value (optional)"
                    aria-label="Part number"
                    oninput="updateItemPartField(${safeItemId}, ${idx}, 'partNumber', this.value)"
                  >
                  <input
                    type="text"
                    class="ai-edit-input ai-edit-specs"
                    value="${escapeHtml(part.specs || '')}"
                    placeholder="Specs / notes (optional)"
                    aria-label="Specs"
                    oninput="updateItemPartField(${safeItemId}, ${idx}, 'specs', this.value)"
                  >
                </div>
              </div>
            </div>
          `).join('') : `<p class="drawer-empty-hint" style="margin: 0.2rem 0;">No parts listed yet. Add parts below.</p>`}
        </div>
        <div class="rapid-add-part-row">
          <input
            type="text"
            id="item-part-quick-add-${safeItemId}"
            placeholder="Add part(s) (e.g. 20x BC547, 10k resistor, #1 Phillips)..."
            onkeydown="if (event.key === 'Enter') { event.preventDefault(); addItemPartToEditor(${safeItemId}); }"
          >
          <button type="button" class="btn btn-secondary btn-sm" onclick="addItemPartToEditor(${safeItemId})">+ Add part</button>
        </div>
      </section>
    `;
  }

  return `
    <section class="item-detail-card">
      <div style="display: flex; justify-content: space-between; align-items: center; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.55rem;">
        <span class="detail-section-title" style="margin-bottom: 0;">Parts in Container (${pluralize(parsed.parts.length, 'part')})</span>
        <button type="button" class="btn btn-secondary btn-xs" onclick="toggleItemPartsEditor(${safeItemId}, true)">
          ${lucideIcon('pencil', '', 12)} Edit / add parts
        </button>
      </div>
      <div class="item-parts-readonly-list">
        ${parsed.parts.map(p => `
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
  `;
}

export function toggleItemPartsEditor(itemId, open) {
  const safeId = Number(itemId);
  const item = state.items.find(i => i.id === safeId);
  if (!item) return;

  if (open) {
    const parsed = parseItemDescriptionAndParts(item.description);
    itemPartsEditorState = {
      itemId: safeId,
      parts: parsed.parts.map(p => ({ ...p })),
      otherNotes: parsed.otherNotes,
    };
  } else {
    itemPartsEditorState = null;
  }
  renderItemView(safeId);
  if (open) {
    requestAnimationFrame(() => {
      document.getElementById(`item-part-quick-add-${safeId}`)?.focus();
    });
  }
}

export function updateItemPartField(itemId, idx, field, value) {
  const safeId = Number(itemId);
  if (!itemPartsEditorState || itemPartsEditorState.itemId !== safeId) return;
  const part = itemPartsEditorState.parts[Number(idx)];
  if (!part) return;
  if (field === 'qty') {
    const n = parseInt(value, 10);
    part.qty = Number.isFinite(n) && n >= 1 ? n : 1;
  } else if (field === 'name') {
    part.name = String(value || '');
  } else if (field === 'partNumber') {
    part.partNumber = String(value || '').trim();
  } else if (field === 'specs') {
    part.specs = String(value || '').trim();
  }
}

export function addItemPartToEditor(itemId) {
  const safeId = Number(itemId);
  if (!itemPartsEditorState || itemPartsEditorState.itemId !== safeId) return;
  const input = document.getElementById(`item-part-quick-add-${safeId}`);
  const raw = (input?.value || '').trim();
  if (!raw) {
    itemPartsEditorState.parts.push({ qty: 1, name: '', partNumber: '', specs: '' });
    renderItemView(safeId);
    return;
  }
  const chunks = raw.includes('\n') ? raw.split(/\r?\n/) : raw.split(',');
  for (const chunk of chunks) {
    const parsed = parsePartBulletLine(chunk);
    if (parsed) {
      itemPartsEditorState.parts.push(parsed);
    }
  }
  if (input) input.value = '';
  renderItemView(safeId);
  requestAnimationFrame(() => {
    document.getElementById(`item-part-quick-add-${safeId}`)?.focus();
  });
}

export function removeItemPartFromEditor(itemId, idx) {
  const safeId = Number(itemId);
  if (!itemPartsEditorState || itemPartsEditorState.itemId !== safeId) return;
  const index = Number(idx);
  if (index < 0 || index >= itemPartsEditorState.parts.length) return;
  itemPartsEditorState.parts.splice(index, 1);
  renderItemView(safeId);
}

export async function saveItemPartsList(itemId) {
  const safeId = Number(itemId);
  const item = state.items.find(i => i.id === safeId);
  if (!item || !itemPartsEditorState || itemPartsEditorState.itemId !== safeId) return;

  const quickInput = document.getElementById(`item-part-quick-add-${safeId}`);
  if (quickInput && quickInput.value.trim()) {
    const chunks = quickInput.value.includes('\n')
      ? quickInput.value.split(/\r?\n/)
      : quickInput.value.split(',');
    for (const chunk of chunks) {
      const parsed = parsePartBulletLine(chunk);
      if (parsed) itemPartsEditorState.parts.push(parsed);
    }
    quickInput.value = '';
  }

  const validParts = itemPartsEditorState.parts.filter(p => p && String(p.name || '').trim());
  const bulletLines = validParts.map(p => {
    const qty = Math.max(1, Number(p.qty || 1));
    const name = String(p.name).trim();
    const pn = String(p.partNumber || '').trim();
    const partSuffix = pn && !name.toLowerCase().includes(pn.toLowerCase()) ? ` [${pn}]` : '';
    const specSuffix = String(p.specs || '').trim() ? ` — ${String(p.specs).trim()}` : '';
    return `• ${qty}x ${name}${partSuffix}${specSuffix}`;
  });

  const otherNotes = String(itemPartsEditorState.otherNotes || '').trim();
  let newDescription = null;
  if (bulletLines.length > 0 && otherNotes) {
    newDescription = `Parts List:\n${bulletLines.join('\n')}\n\nNotes:\n${otherNotes}`.slice(0, 20000);
  } else if (bulletLines.length > 0) {
    newDescription = `Parts List:\n${bulletLines.join('\n')}`.slice(0, 20000);
  } else if (otherNotes) {
    newDescription = otherNotes.slice(0, 20000);
  }

  const seenTags = new Set();
  const tagTokens = [];
  const existingTags = item.tags ? String(item.tags).split(',').map(t => t.trim()).filter(Boolean) : [];
  for (const p of validParts) {
    if (p.partNumber) tagTokens.push(p.partNumber.trim());
    if (p.name) tagTokens.push(p.name.trim());
  }
  for (const t of existingTags) tagTokens.push(t);

  let mergedTags = '';
  for (const rawTok of tagTokens) {
    const tok = String(rawTok || '').trim();
    if (!tok) continue;
    const norm = tok.toLowerCase();
    if (seenTags.has(norm)) continue;
    const candidate = mergedTags ? `${mergedTags}, ${tok}` : tok;
    if (candidate.length > 300) break;
    seenTags.add(norm);
    mergedTags = candidate;
  }

  return withSubmitLock(`save-item-parts-btn-${safeId}`, 'Saving...', async () => {
    try {
      await apiPatch(
        `/api/v1/items/${safeId}`,
        { description: newDescription, tags: mergedTags || null },
        'Could not save parts list.'
      );
      itemPartsEditorState = null;
      showToast('Parts list updated!');
      await loadAllData();
    } catch (err) {
      showToast(err.message || 'Could not save parts list.', 'error');
    }
  });
}

function renderItemNotesSection(item) {
  if (!item.description || !item.description.trim()) return '';
  const parsed = parseItemDescriptionAndParts(item.description);
  const text = parsed.hasPartsHeader ? parsed.otherNotes : item.description.trim();
  if (!text) return '';

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

export async function adjustItemQty(itemId, delta) {
  const item = state.items.find(i => i.id === itemId);
  if (!item) return;

  let entry = itemQtySyncState.get(itemId);
  if (entry && entry.deleting) return;

  const baseQty = entry
    ? entry.targetQty
    : (item.quantity === null || item.quantity === undefined ? 0 : Number(item.quantity));
  const next = Math.max(0, baseQty + delta);

  if (!entry) {
    entry = { targetQty: next, running: false, deleting: false };
    itemQtySyncState.set(itemId, entry);
  } else {
    entry.targetQty = next;
  }

  item.quantity = next;
  const viewQtyEl = document.getElementById('item-view-qty-val');
  if (viewQtyEl) viewQtyEl.textContent = String(next);

  if (entry.running) return;
  entry.running = true;

  try {
    let updated = null;
    while (true) {
      const desiredQty = entry.targetQty;
      updated = await apiPatch(
        `/api/v1/items/${itemId}`,
        { quantity: desiredQty },
        'Could not update count.'
      );
      if (entry.targetQty === desiredQty) break;
    }
    itemQtySyncState.delete(itemId);
    if (updated) {
      item.quantity = updated.quantity;
      const finalViewEl = document.getElementById('item-view-qty-val');
      if (finalViewEl) finalViewEl.textContent = String(updated.quantity);
    }
    renderCurrentWorkspace();
  } catch (err) {
    itemQtySyncState.delete(itemId);
    showToast(err.message, 'error');
    await loadAllData();
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

export function toggleItemRenameEdit(itemId, editing) {
  itemRenameEditId = editing ? Number(itemId) : null;
  renderItemView(Number(itemId));
  if (editing) {
    requestAnimationFrame(() => {
      const input = document.getElementById(`item-rename-input-${Number(itemId)}`);
      if (input) {
        input.focus();
        input.select();
      }
    });
  }
}

export async function saveItemRename(itemId) {
  const input = document.getElementById(`item-rename-input-${Number(itemId)}`);
  const name = (input?.value || '').trim();
  if (!name) {
    showToast('Item name cannot be empty.', 'error');
    if (input) input.focus();
    return;
  }
  try {
    await apiPatch(`/api/v1/items/${Number(itemId)}`, { name }, 'Could not rename item.');
    itemRenameEditId = null;
    showToast('Item renamed.');
    await loadAllData();
  } catch (err) {
    showToast(err.message || 'Could not rename item.', 'error');
  }
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

