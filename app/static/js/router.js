/**
 * Hash Router & View Navigation Engine
 */
import { isUnknownPlaceholder, state, updateNavStateAndCounts } from './state.js';
import { guardNavigation, isGridDirty } from './unsaved-guard.js';

let committedHash = typeof window !== 'undefined' ? (window.location.hash || '') : '';

function runGuarded(fn) {
  const run = () => {
    fn();
    committedHash = window.location.hash || '';
    if (committedHash) saveLastRoute(committedHash);
    if (typeof requestAnimationFrame === 'function') {
      requestAnimationFrame(() => window.applyGlobalViewOptions?.());
    } else {
      window.applyGlobalViewOptions?.();
    }
  };
  if (!isGridDirty()) {
    run();
    return;
  }
  Promise.resolve(guardNavigation(run)).then((ok) => {
    if (ok === false && (window.location.hash || '') !== committedHash) {
      history.replaceState(null, '', committedHash || window.location.pathname);
    }
  });
}
import {
  renderCatalogView,
  renderDrawerView,
  renderHomePlacesView,
  renderItemView,
  renderNeedsHomeView,
  renderRoomView,
  renderStorageUnitView
} from './views.js';
import { isPhoneViewport, navToTouchCapture, renderTouchCaptureView } from './mobile-views.js';

const LAST_ROUTE_KEY = 'wims_last_route';
const LAST_SCROLL_KEY = 'wims_last_scroll';

export function saveLastRoute(hash) {
  try {
    if (hash) localStorage.setItem(LAST_ROUTE_KEY, hash);
  } catch {}
}

export function getSavedLastRoute() {
  try {
    return localStorage.getItem(LAST_ROUTE_KEY) || '';
  } catch {
    return '';
  }
}

export function saveLastScroll(scrollTop) {
  try {
    localStorage.setItem(LAST_SCROLL_KEY, String(Math.max(0, Math.round(scrollTop || 0))));
  } catch {}
}

export function restoreLastScroll() {
  try {
    const raw = localStorage.getItem(LAST_SCROLL_KEY);
    const val = raw ? parseInt(raw, 10) : 0;
    if (!val || Number.isNaN(val)) return;
    requestAnimationFrame(() => {
      const scroller = document.getElementById('unit-scroll-region') || document.getElementById('app-workspace');
      if (scroller) scroller.scrollTop = val;
    });
  } catch {}
}

export function updateHash(hash) {
  saveLastRoute(hash);
  if (window.location.hash !== hash) {
    history.pushState(null, '', hash);
  }
}

export function handleHashRouting() {
  const hash = window.location.hash || '';
  if (!hash || hash === '#' || hash === '#/' || hash === '#/places' || hash === '#/rooms' || hash === '#/storage') {
    switchAppTab('places', false);
  } else if (hash === '#/needs-home') {
    navToNeedsHome(false);
  } else if (hash === '#/items') {
    switchAppTab('items', false);
  } else if (hash.startsWith('#/unit/')) {
    const id = parseInt(hash.replace('#/unit/', ''), 10);
    if (id) navToStorageUnit(id, false);
  } else if (hash.startsWith('#/drawer/')) {
    const id = parseInt(hash.replace('#/drawer/', ''), 10);
    if (id) navToDrawer(id, false);
  } else if (hash.startsWith('#/room/')) {
    const id = parseInt(hash.replace('#/room/', ''), 10);
    if (id) navToRoom(id, false);
  } else if (hash.startsWith('#/item/')) {
    const id = parseInt(hash.replace('#/item/', ''), 10);
    if (id) navToItem(id, false);
  } else if (hash === '#/touch-capture' || hash.startsWith('#/touch-capture/')) {
    const rest = hash.replace('#/touch-capture', '').replace(/^\//, '');
    saveLastRoute(hash);
    const parts = rest ? rest.split('/') : [];
    const rId = parts[0] === 'needs-home' ? 'needs-home' : (parts[0] ? parseInt(parts[0], 10) : null);
    const uId = parts[1] ? parseInt(parts[1], 10) : null;
    const dId = parts[2] ? parseInt(parts[2], 10) : null;
    navToTouchCapture(rId, uId, dId, false);
  }
}

export function updatePageTitle(label = '') {
  if (typeof document === 'undefined') return;
  document.title = label ? `${label} — WhereIsMyStuff` : 'WhereIsMyStuff';
}

export function switchAppTab(tabName, recordHistory = true) {
  runGuarded(() => {
    const normalized = tabName === 'storage' || tabName === 'rooms' ? 'places' : tabName;

    state.activeTab = normalized;
    state.currentView = normalized === 'places' ? 'home' : normalized;
    state.currentLocationId = null;
    state.spotlightDrawerId = null;

    if (normalized === 'places') updatePageTitle('');
    else if (normalized === 'items') updatePageTitle('Items');

    if (recordHistory) {
      state.historyStack.push({ tab: normalized, view: state.currentView, id: null });
      updateHash(normalized === 'places' ? '#/' : `#/${normalized}`);
    }

    updateNavStateAndCounts();
    renderCurrentWorkspace();
  });
}

export function navToHome() {
  runGuarded(() => {
    state.homeSearchQuery = '';
    state.homeSearchResults = null;
    state.homeSearchLoading = false;
    switchAppTab('places', true);
  });
}

export function navToNeedsHome(recordHistory = true) {
  runGuarded(() => {
    state.activeTab = 'places';
    state.currentView = 'needs-home';
    state.currentLocationId = null;
    state.spotlightDrawerId = null;
    updatePageTitle('Needs a home');

    if (recordHistory) {
      state.historyStack.push({ tab: 'places', view: 'needs-home', id: null });
      updateHash('#/needs-home');
    }

    updateNavStateAndCounts();
    renderNeedsHomeView();
  });
}

export function navToRoom(roomId, recordHistory = true) {
  runGuarded(() => {
    state.activeTab = 'places';
    state.currentView = 'room';
    state.currentLocationId = roomId;
    state.spotlightDrawerId = null;
    const room = state.locationMap.get(roomId);
    updatePageTitle(room ? room.name : 'Space');

    if (recordHistory) {
      state.historyStack.push({ tab: 'places', view: 'room', id: roomId });
      updateHash(`#/room/${roomId}`);
    }

    updateNavStateAndCounts();
    renderRoomView(roomId);
  });
}

export function navToStorageUnit(unitId, recordHistory = true, spotlightDrawerId = null) {
  runGuarded(() => {
    state.activeTab = 'places';
    state.currentView = 'unit';
    state.currentLocationId = unitId;
    state.spotlightDrawerId = spotlightDrawerId;
    const unit = state.locationMap.get(unitId);
    updatePageTitle(unit ? unit.name : 'Container');

    if (recordHistory) {
      state.historyStack.push({ tab: 'places', view: 'unit', id: unitId, spotlightDrawerId });
      updateHash(`#/unit/${unitId}`);
    }

    updateNavStateAndCounts();
    renderStorageUnitView(unitId);
  });
}

export function navToDrawer(drawerId, recordHistory = true) {
  runGuarded(() => {
    state.activeTab = 'places';
    state.currentView = 'drawer';
    state.currentLocationId = drawerId;
    state.spotlightDrawerId = null;
    const drawer = state.locationMap.get(drawerId);
    updatePageTitle(drawer ? drawer.name : 'Drawer');

    if (recordHistory) {
      state.historyStack.push({ tab: 'places', view: 'drawer', id: drawerId });
      updateHash(`#/drawer/${drawerId}`);
    }

    updateNavStateAndCounts();
    renderDrawerView(drawerId);
  });
}

export function navToItem(itemId, recordHistory = true) {
  runGuarded(() => {
    state.activeTab = 'items';
    state.currentView = 'item';
    state.currentLocationId = itemId;
    state.spotlightDrawerId = null;
    const item = state.items.find(i => i.id === itemId);
    updatePageTitle(item ? item.name : 'Item');

    if (recordHistory) {
      state.historyStack.push({ tab: 'items', view: 'item', id: itemId });
      updateHash(`#/item/${itemId}`);
    }

    updateNavStateAndCounts();
    renderItemView(itemId);
  });
}

function clearActiveSpotlight() {
  state.spotlightDrawerId = null;
  if (typeof document === 'undefined') return;
  document.querySelectorAll('.spotlight-highlight').forEach(el => {
    el.classList.remove('spotlight-highlight', 'is-highlighted');
  });
  document.querySelectorAll('.spotlight-badge, .cgrid-here-badge').forEach(el => el.remove());
}

function resolveUnitChildDrawerId(unitId, targetLocId) {
  let curr = state.locationMap.get(Number(targetLocId));
  const visited = new Set();
  while (curr && curr.parent_id && !visited.has(curr.id)) {
    visited.add(curr.id);
    if (Number(curr.parent_id) === Number(unitId)) {
      return Number(curr.id);
    }
    curr = state.locationMap.get(Number(curr.parent_id));
  }
  return Number(targetLocId);
}

export function locateDrawerInUnit(unitId, drawerId) {
  let resolvedUnitId = unitId ? Number(unitId) : null;
  if (!resolvedUnitId && drawerId) {
    const d = state.locationMap.get(Number(drawerId));
    if (d && d.parent_id) resolvedUnitId = Number(d.parent_id);
  }
  if (!resolvedUnitId) return;

  const targetDrawerId = resolveUnitChildDrawerId(resolvedUnitId, drawerId);
  const alreadyOnUnit = state.currentView === 'unit' && Number(state.currentLocationId) === resolvedUnitId;
  const phone = isPhoneViewport();

  if (alreadyOnUnit && typeof document !== 'undefined') {
    clearActiveSpotlight();
    state.spotlightDrawerId = targetDrawerId;
    if (phone) {
      renderStorageUnitView(resolvedUnitId);
    } else {
      const el = document.getElementById(`drawer-card-${targetDrawerId}`);
      if (el) {
        el.classList.add('spotlight-highlight');
        if (el.classList.contains('cgrid-slot')) {
          el.classList.add('is-highlighted');
        }
        el.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'center' });
      }
    }
  } else {
    navToStorageUnit(resolvedUnitId, true, targetDrawerId);
  }

  clearTimeout(state.spotlightTimer);
  if (!phone) {
    state.spotlightTimer = setTimeout(() => {
      if (state.currentView !== 'unit' || Number(state.currentLocationId) !== resolvedUnitId) return;
      clearActiveSpotlight();
    }, 10000);
  }
}

export function handleGlobalBack() {
  runGuarded(() => {
    if (state.historyStack.length > 1) {
      state.historyStack.pop();
      const prev = state.historyStack[state.historyStack.length - 1];
      const prevHash = prev.view === 'needs-home' ? '#/needs-home'
        : (prev.id && ['room', 'unit', 'drawer', 'item'].includes(prev.view)) ? `#/${prev.view}/${prev.id}`
        : (prev.view === 'touch-capture' ? null : (prev.tab === 'items' ? '#/items' : '#/'));
      if (prevHash) updateHash(prevHash);
      if (prev.view === 'needs-home') {
        navToNeedsHome(false);
      } else if (prev.view === 'room' && prev.id) {
        navToRoom(prev.id, false);
      } else if (prev.view === 'unit' && prev.id) {
        navToStorageUnit(prev.id, false, prev.spotlightDrawerId || null);
      } else if (prev.view === 'drawer' && prev.id) {
        navToDrawer(prev.id, false);
      } else if (prev.view === 'item' && prev.id) {
        navToItem(prev.id, false);
      } else if (prev.view === 'touch-capture') {
        navToTouchCapture(prev.roomId || null, prev.unitId || null, prev.drawerId || null, false);
      } else {
        switchAppTab(prev.tab || 'places', false);
      }
      return;
    }

    if (state.currentView === 'item' && state.currentLocationId) {
      const item = state.items.find(i => i.id === state.currentLocationId);
      if (item && item.location_id) {
        navToDrawer(item.location_id, true);
        return;
      }
      switchAppTab('items', true);
      return;
    } else if (state.currentView === 'drawer' && state.currentLocationId) {
      const d = state.locationMap.get(state.currentLocationId);
      if (d && d.parent_id) {
        navToStorageUnit(d.parent_id, true);
        return;
      }
    } else if (state.currentView === 'unit' && state.currentLocationId) {
      if (isPhoneViewport()) {
        navToHome();
        return;
      }
      const u = state.locationMap.get(state.currentLocationId);
      const parent = u && u.parent_id ? state.locationMap.get(u.parent_id) : null;
      if (parent && !isUnknownPlaceholder(parent)) {
        navToRoom(parent.id, true);
        return;
      }
      navToNeedsHome(true);
      return;
    }

    navToHome();
  });
}

export function renderCurrentWorkspace() {
  const workspace = document.getElementById('app-workspace');
  if (!workspace) return;

  const isHomeCanvas = state.activeTab === 'places' && state.currentView === 'home';
  workspace.classList.toggle('is-home-canvas', isHomeCanvas);
  workspace.classList.toggle('is-unit-view', state.currentView === 'unit');

  if (state.currentView === 'needs-home') {
    renderNeedsHomeView();
  } else if (state.currentView === 'room' && state.currentLocationId) {
    renderRoomView(state.currentLocationId);
  } else if (state.currentView === 'unit' && state.currentLocationId) {
    renderStorageUnitView(state.currentLocationId);
  } else if (state.currentView === 'drawer' && state.currentLocationId) {
    renderDrawerView(state.currentLocationId);
  } else if (state.currentView === 'item' && state.currentLocationId) {
    renderItemView(state.currentLocationId);
  } else if (state.currentView === 'touch-capture') {
    renderTouchCaptureView();
  } else if (state.activeTab === 'items') {
    renderCatalogView();
  } else {
    renderHomePlacesView();
  }
}

