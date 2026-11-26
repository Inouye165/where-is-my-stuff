/**
 * Application State, Starter Room Presets & Spatial Hierarchy Indexing
 */
import { apiGet } from './api.js';
import { escapeHtml, showToast } from './dom.js';

export const UNASSIGNED_ROOM_LABEL = 'Needs a home';

export const ROOM_EMOJI_KEYWORDS = [
  { match: /garage|workshop|shop|tool|shed/i, emoji: '🔧' },
  { match: /office|desk|study|computer/i, emoji: '💻' },
  { match: /bed|guest|nursery|kids/i, emoji: '🛏️' },
  { match: /closet|hall|coat|wardrobe|pantry/i, emoji: '🚪' },
  { match: /kitchen|dining/i, emoji: '🍳' },
  { match: /living|family|den|lounge/i, emoji: '🛋️' },
  { match: /laundry|utility|mud/i, emoji: '🧺' },
  { match: /car|truck|van|rv/i, emoji: '🚗' },
  { match: /yard|patio|garden|porch/i, emoji: '🌿' },
  { match: /attic|basement|cellar|storage|locker/i, emoji: '📦' }
];

export function loadJsonStorage(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch {
    return fallback;
  }
}

export function saveJsonStorage(key, val) {
  try {
    localStorage.setItem(key, JSON.stringify(val));
  } catch {}
}

export const state = {
  items: [],
  itemsTotal: 0,
  locations: [],
  locationMap: new Map(),
  namedRooms: [],
  containers: [],
  categories: [],

  // Navigation State
  activeTab: 'places',
  currentView: 'home',
  currentLocationId: null,
  spotlightDrawerId: null,
  historyStack: [],
  homePage: 0,

  // Search & Filter State
  homeSearchQuery: '',
  homeSearchResults: null,
  homeSearchLoading: false,
  homeSearchHintIndex: 0,
  photoSearchPreviewUrl: '',
  photoSearchLoading: false,
  needsHomeIndex: 0,
  itemsSearchQuery: '',
  selectedCategory: '',
  selectedContainerId: '',
  selectedSort: 'name_asc',

  // Active Modals / Sheets
  storageTypeChoice: 'unit',
  quickPhotoTarget: null,
  roomEmojis: loadJsonStorage('wims_room_emojis', {}),

  // Theme ('system' | 'light' | 'dark')
  themePref: (typeof localStorage !== 'undefined' && localStorage.getItem('wims_theme_pref')) || 'dark',
  spotlightTimer: null,
  onWorkspaceRefresh: null
};

export function isUnknownPlaceholder(loc) {
  if (!loc || !loc.name) return false;
  return loc.name.trim().toLowerCase() === 'unknown location';
}

export function getLocType(loc) {
  return (loc && (loc.location_type || loc.type)) || 'room';
}

export function indexLocations() {
  state.locationMap.clear();
  const rawRooms = [];
  const rawContainers = [];

  function walk(node, parentNode = null) {
    state.locationMap.set(node.id, node);
    const ltype = getLocType(node);
    const parentType = parentNode ? getLocType(parentNode) : null;
    const isTopOrRoomChild = !parentNode
      || parentType === 'room'
      || parentType === 'site'
      || isUnknownPlaceholder(parentNode);

    if (!node.parent_id && (ltype === 'room' || ltype === 'site') && !isUnknownPlaceholder(node)) {
      rawRooms.push(node);
    }

    const isUnassignedTopLevel = (!parentNode || isUnknownPlaceholder(parentNode))
      && !isUnknownPlaceholder(node)
      && ltype !== 'room'
      && ltype !== 'site';

    if (
      ltype === 'container' ||
      isUnassignedTopLevel ||
      (isTopOrRoomChild && node.children && node.children.length > 0 && ltype !== 'room' && ltype !== 'site')
    ) {
      rawContainers.push(node);
    }

    if (node.children && node.children.length > 0) {
      for (const child of node.children) {
        walk(child, node);
      }
    }
  }

  for (const root of state.locations) {
    walk(root, null);
  }

  const byName = new Map();
  for (const r of rawRooms) {
    const key = r.name.trim().toLowerCase();
    if (!byName.has(key)) {
      byName.set(key, [r]);
    } else {
      byName.get(key).push(r);
    }
  }

  state.namedRooms = [];
  for (const group of byName.values()) {
    if (group.length === 1) {
      state.namedRooms.push(group[0]);
    } else {
      const nonEmpty = group.filter(
        r => (r.child_count || 0) > 0 || (r.total_item_count || 0) > 0 || r.context_photo || r.container_photo
      );
      if (nonEmpty.length > 0) {
        for (const r of nonEmpty) state.namedRooms.push(r);
      } else {
        state.namedRooms.push(group[0]);
      }
    }
  }

  state.containers = rawContainers;
}

export function getRoomEmoji(roomOrName) {
  if (!roomOrName) return '🏠';
  if (typeof roomOrName === 'object') {
    if (state.roomEmojis[roomOrName.id]) return state.roomEmojis[roomOrName.id];
    if (state.roomEmojis[roomOrName.name.toLowerCase()]) return state.roomEmojis[roomOrName.name.toLowerCase()];
  }
  const name = typeof roomOrName === 'string' ? roomOrName : roomOrName.name;
  if (state.roomEmojis[name.toLowerCase()]) return state.roomEmojis[name.toLowerCase()];

  for (const entry of ROOM_EMOJI_KEYWORDS) {
    if (entry.match.test(name)) return entry.emoji;
  }
  return '🏠';
}

export function getUnassignedContainers() {
  return state.containers.filter(c => {
    if (!c.parent_id) return true;
    const parent = state.locationMap.get(c.parent_id);
    return !parent || isUnknownPlaceholder(parent);
  });
}

export function getUnassignedLooseItems() {
  return state.items.filter(item => {
    if (!item.location_id) return true;
    const loc = state.locationMap.get(item.location_id);
    return !loc || isUnknownPlaceholder(loc);
  });
}

export function getStarterSuggestions() {
  return [];
}

export function formatHumanPath(fullPath) {
  if (!fullPath) return UNASSIGNED_ROOM_LABEL;
  const parts = fullPath
    .split(/\s*>\s*/)
    .map(p => (p.trim().toLowerCase() === 'unknown location' ? UNASSIGNED_ROOM_LABEL : p.trim()))
    .filter(Boolean);
  return parts.join(' › ') || UNASSIGNED_ROOM_LABEL;
}

export function getLocationAncestors(locationId) {
  const trail = [];
  let curr = state.locationMap.get(locationId);
  const visited = new Set();
  while (curr && !visited.has(curr.id)) {
    visited.add(curr.id);
    trail.unshift(curr);
    curr = curr.parent_id ? state.locationMap.get(curr.parent_id) : null;
  }
  return trail;
}

export function getSubtreeItems(locationId) {
  const root = state.locationMap.get(Number(locationId));
  if (!root) return [];
  const ids = new Set();
  function collect(node) {
    if (!node || ids.has(node.id)) return;
    ids.add(node.id);
    if (Array.isArray(node.children)) {
      for (const child of node.children) {
        collect(child);
      }
    }
  }
  collect(root);
  return state.items.filter(item => item.location_id && ids.has(item.location_id));
}

export function getLocationHierarchy(locationId) {
  const loc = state.locationMap.get(locationId);
  if (!loc) {
    return {
      room: null,
      unit: null,
      drawer: null,
      box: null,
      subLocations: [],
      trail: [],
      roomName: UNASSIGNED_ROOM_LABEL,
      unitName: 'No container',
      drawerName: 'Unassigned',
      boxName: null,
      humanPath: UNASSIGNED_ROOM_LABEL,
      searchPath: UNASSIGNED_ROOM_LABEL,
      isNeedsHome: true
    };
  }

  const containerIdSet = new Set(state.containers.map(c => c.id));
  const trail = getLocationAncestors(loc.id).filter(n => !isUnknownPlaceholder(n));
  let room = null;
  let unit = null;
  const subNodes = [];

  for (const node of trail) {
    const ltype = getLocType(node);
    if (!unit && subNodes.length === 0 && (ltype === 'room' || ltype === 'site')) {
      room = node;
    } else if (!unit && subNodes.length === 0 && (ltype === 'container' || containerIdSet.has(node.id))) {
      unit = node;
    } else {
      subNodes.push(node);
    }
  }

  const drawer = subNodes[0] || null;
  const subLocations = subNodes.slice(1);
  const box = subLocations.length > 0 ? subLocations[subLocations.length - 1] : null;

  const isNeedsHome = !room;
  const roomName = room ? room.name : UNASSIGNED_ROOM_LABEL;
  const unitName = unit ? unit.name : (drawer ? 'Loose spot' : roomName);
  const drawerName = drawer ? drawer.name : (unit ? unit.name : roomName);
  const boxName = box ? box.name : null;

  const segments = isNeedsHome
    ? [UNASSIGNED_ROOM_LABEL, ...trail.map(n => n.name)]
    : trail.map(n => n.name);

  return {
    room,
    unit,
    drawer,
    box,
    subLocations,
    trail,
    roomName,
    unitName,
    drawerName,
    boxName,
    humanPath: segments.length > 0 ? segments.join(' › ') : UNASSIGNED_ROOM_LABEL,
    searchPath: segments.length > 0 ? segments.join(' > ') : UNASSIGNED_ROOM_LABEL,
    isNeedsHome
  };
}

export function getQuickMoveRoomChoices() {
  const choices = [];
  const seen = new Set();

  for (const r of state.namedRooms) {
    const key = r.name.trim().toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      choices.push({ id: r.id, name: r.name, emoji: getRoomEmoji(r) });
    }
  }
  return choices;
}

export function extractCategories() {
  const set = new Set();
  for (const item of state.items) {
    if (item.category && item.category.trim()) {
      set.add(item.category.trim());
    }
  }
  state.categories = Array.from(set).sort((a, b) => a.localeCompare(b));
}

export function populateLocationDropdowns() {
  const allLocations = Array.from(state.locationMap.values())
    .filter(l => !isUnknownPlaceholder(l))
    .sort((a, b) => formatHumanPath(a.full_path).localeCompare(formatHumanPath(b.full_path)));

  const itemSelect = document.getElementById('item-location-select');
  if (itemSelect) {
    const prev = itemSelect.value;
    itemSelect.innerHTML = '<option value="">-- Choose a box, drawer, container, or room --</option>';
    for (const loc of allLocations) {
      const opt = document.createElement('option');
      opt.value = loc.id;
      opt.textContent = formatHumanPath(loc.full_path);
      itemSelect.appendChild(opt);
    }
    if (prev) itemSelect.value = prev;
  }

  const rapidSelect = document.getElementById('rapid-location-select');
  if (rapidSelect) {
    const prev = rapidSelect.value;
    rapidSelect.innerHTML = '';
    const leafTypes = new Set(['box', 'kit', 'bin', 'drawer', 'compartment', 'cubby']);
    const sortedForRapid = allLocations.slice().sort((a, b) => {
      const aIsBin = leafTypes.has(getLocType(a)) ? 0 : 1;
      const bIsBin = leafTypes.has(getLocType(b)) ? 0 : 1;
      if (aIsBin !== bIsBin) return aIsBin - bIsBin;
      return formatHumanPath(a.full_path).localeCompare(formatHumanPath(b.full_path));
    });
    for (const loc of sortedForRapid) {
      const opt = document.createElement('option');
      opt.value = loc.id;
      opt.textContent = formatHumanPath(loc.full_path);
      rapidSelect.appendChild(opt);
    }
    if (prev) rapidSelect.value = prev;
  }

  const itemCatDatalist = document.getElementById('item-category-datalist');
  if (itemCatDatalist) {
    itemCatDatalist.innerHTML = state.categories.map(c => `<option value="${escapeHtml(c)}">`).join('');
  }

  const rapidCatDatalist = document.getElementById('rapid-category-datalist');
  if (rapidCatDatalist) {
    rapidCatDatalist.innerHTML = state.categories.map(c => `<option value="${escapeHtml(c)}">`).join('');
  }
}

export function populateStorageParentDropdown(kind, selectedParentId = null, excludeId = null) {
  const parentSelect = document.getElementById('storage-parent-select');
  if (!parentSelect) return;

  parentSelect.innerHTML = '';

  if (kind === 'room') {
    const topOpt = document.createElement('option');
    topOpt.value = '';
    topOpt.textContent = 'Top-level room (on Places)';
    parentSelect.appendChild(topOpt);

    const newRoomOpt = document.createElement('option');
    newRoomOpt.value = '__new__';
    newRoomOpt.textContent = '+ Create a new room...';
    parentSelect.appendChild(newRoomOpt);

    const allRooms = Array.from(state.locationMap.values())
      .filter(l => !isUnknownPlaceholder(l) && (getLocType(l) === 'room' || getLocType(l) === 'site'))
      .sort((a, b) => a.name.localeCompare(b.name));

    for (const room of allRooms) {
      if (excludeId && (room.id === excludeId || getLocationAncestors(room.id).some(a => a.id === excludeId))) {
        continue;
      }
      const opt = document.createElement('option');
      opt.value = room.id;
      opt.textContent = formatHumanPath(room.full_path);
      parentSelect.appendChild(opt);
    }
  } else if (kind === 'unit') {
    const noneOpt = document.createElement('option');
    noneOpt.value = '';
    noneOpt.textContent = 'Decide later (Needs a home)';
    parentSelect.appendChild(noneOpt);

    const newRoomOpt = document.createElement('option');
    newRoomOpt.value = '__new__';
    newRoomOpt.textContent = '+ Create a new room...';
    parentSelect.appendChild(newRoomOpt);

    for (const room of state.namedRooms) {
      if (excludeId && room.id === excludeId) continue;
      const opt = document.createElement('option');
      opt.value = room.id;
      opt.textContent = `${getRoomEmoji(room)} ${room.name}`;
      parentSelect.appendChild(opt);
    }

    const namedRoomIds = new Set(state.namedRooms.map(r => r.id));
    const nestedRooms = Array.from(state.locationMap.values())
      .filter(l => l.parent_id && !namedRoomIds.has(l.id) && !isUnknownPlaceholder(l)
        && (getLocType(l) === 'room' || getLocType(l) === 'site'))
      .sort((a, b) => formatHumanPath(a.full_path).localeCompare(formatHumanPath(b.full_path)));
    for (const room of nestedRooms) {
      if (excludeId && (room.id === excludeId || getLocationAncestors(room.id).some(a => a.id === excludeId))) continue;
      const opt = document.createElement('option');
      opt.value = room.id;
      opt.textContent = `${getRoomEmoji(room)} ${formatHumanPath(room.full_path)}`;
      parentSelect.appendChild(opt);
    }
  } else if (kind === 'drawer') {
    const containerIds = new Set(state.containers.map(c => c.id));
    for (const c of state.containers) {
      if (excludeId && (c.id === excludeId || getLocationAncestors(c.id).some(a => a.id === excludeId))) continue;
      const opt = document.createElement('option');
      opt.value = c.id;
      opt.textContent = formatHumanPath(c.full_path);
      parentSelect.appendChild(opt);
    }
    const subParents = Array.from(state.locationMap.values())
      .filter(l => {
        if (isUnknownPlaceholder(l) || containerIds.has(l.id)) return false;
        const lt = getLocType(l);
        return lt !== 'room' && lt !== 'site';
      })
      .sort((a, b) => formatHumanPath(a.full_path).localeCompare(formatHumanPath(b.full_path)));
    for (const sp of subParents) {
      if (excludeId && (sp.id === excludeId || getLocationAncestors(sp.id).some(a => a.id === excludeId))) continue;
      const opt = document.createElement('option');
      opt.value = sp.id;
      opt.textContent = formatHumanPath(sp.full_path);
      parentSelect.appendChild(opt);
    }
    for (const room of state.namedRooms) {
      const opt = document.createElement('option');
      opt.value = room.id;
      opt.textContent = `${getRoomEmoji(room)} ${room.name}`;
      parentSelect.appendChild(opt);
    }
  }

  if (selectedParentId) {
    const parentNode = state.locationMap.get(selectedParentId);
    if (parentNode && !isUnknownPlaceholder(parentNode)) {
      const value = String(selectedParentId);
      // Keep the current parent selectable even if the filters above excluded it;
      // otherwise the select falls back to '' and saving silently detaches the location.
      if (!Array.from(parentSelect.options).some(o => o.value === value)) {
        const opt = document.createElement('option');
        opt.value = value;
        opt.textContent = formatHumanPath(parentNode.full_path);
        parentSelect.appendChild(opt);
      }
      parentSelect.value = value;
    }
  }
}

export function updateNavStateAndCounts() {
  const isPlaces = state.activeTab === 'places';
  const isItems = state.activeTab === 'items';
  const isCapture = state.activeTab === 'touch-capture';

  document.getElementById('rail-nav-places')?.classList.toggle('active', isPlaces);
  document.getElementById('rail-nav-items')?.classList.toggle('active', isItems);

  document.getElementById('mobile-nav-places')?.classList.toggle('active', !isCapture);
  document.getElementById('mobile-nav-capture')?.classList.toggle('active', isCapture);

  let totalDrawers = 0;
  for (const c of state.containers) {
    totalDrawers += c.children ? c.children.length : (c.child_count || 0);
  }
  const itemsEl = document.getElementById('more-total-items');
  const contEl = document.getElementById('more-total-containers');
  const drawEl = document.getElementById('more-total-drawers');
  if (itemsEl) itemsEl.textContent = String(state.items.length);
  if (contEl) contEl.textContent = String(state.containers.length);
  if (drawEl) drawEl.textContent = String(totalDrawers);
}

let loadAllDataSeq = 0;
let hasCompletedInitialLoad = false;
let initialRetryTimer = null;
let initialRetryCount = 0;
const MAX_INITIAL_RETRIES = 20;
const INITIAL_RETRY_DELAY_MS = 1500;

function renderBootLoadingState(title, subtitle, showRetry = false) {
  if (typeof document === 'undefined') return;
  const ws = document.getElementById('app-workspace');
  if (!ws) return;

  let container = document.getElementById('app-boot-loading');
  if (!container) {
    ws.innerHTML = '';
    container = document.createElement('div');
    container.id = 'app-boot-loading';
    container.className = 'app-boot-loading';
    container.setAttribute('role', 'status');
    container.setAttribute('aria-live', 'polite');

    const spinner = document.createElement('div');
    spinner.className = 'app-boot-spinner';
    spinner.setAttribute('aria-hidden', 'true');

    const titleEl = document.createElement('div');
    titleEl.className = 'app-boot-title';

    const subEl = document.createElement('div');
    subEl.id = 'app-boot-sub';
    subEl.className = 'app-boot-sub';

    container.append(spinner, titleEl, subEl);
    ws.appendChild(container);
  }

  const spinnerEl = container.querySelector('.app-boot-spinner');
  if (spinnerEl) spinnerEl.style.display = showRetry ? 'none' : '';
  const titleEl = container.querySelector('.app-boot-title');
  if (titleEl) titleEl.textContent = title;
  const subEl = container.querySelector('.app-boot-sub');
  if (subEl) subEl.textContent = subtitle;

  let retryBtn = container.querySelector('.app-boot-retry');
  if (showRetry) {
    if (!retryBtn) {
      retryBtn = document.createElement('button');
      retryBtn.type = 'button';
      retryBtn.className = 'btn btn-primary app-boot-retry';
      retryBtn.textContent = 'Retry now';
      retryBtn.addEventListener('click', () => {
        initialRetryCount = 0;
        renderBootLoadingState('Loading inventory...', 'Connecting to WIMS server', false);
        loadAllData();
      });
      container.appendChild(retryBtn);
    }
  } else if (retryBtn) {
    retryBtn.remove();
  }
}

async function loadAllItemsPaginated(reqSeq) {
  const collected = [];
  let cursor = null;
  let total = 0;

  for (let pageIdx = 0; pageIdx < 200; pageIdx++) {
    const qs = cursor
      ? `/api/v1/items/page?limit=200&cursor=${encodeURIComponent(cursor)}`
      : '/api/v1/items/page?limit=200';
    const page = await apiGet(qs, 'Could not load items.');
    if (reqSeq !== loadAllDataSeq) return null;

    if (Array.isArray(page)) {
      return { items: page, total: page.length };
    }
    const batch = Array.isArray(page?.items) ? page.items : [];
    collected.push(...batch);
    total = typeof page?.total === 'number' ? page.total : collected.length;
    if (!page?.has_more || !page?.next_cursor) {
      break;
    }
    cursor = page.next_cursor;
  }
  return { items: collected, total };
}

export async function loadAllData() {
  const reqSeq = ++loadAllDataSeq;
  if (initialRetryTimer) {
    clearTimeout(initialRetryTimer);
    initialRetryTimer = null;
  }

  try {
    const [itemPage, locations] = await Promise.all([
      loadAllItemsPaginated(reqSeq),
      apiGet('/api/v1/locations/tree', 'Could not load rooms and containers.')
    ]);
    if (reqSeq !== loadAllDataSeq || !itemPage) return;

    hasCompletedInitialLoad = true;
    initialRetryCount = 0;
    state.items = itemPage.items;
    state.itemsTotal = itemPage.total;
    state.locations = locations;

    indexLocations();
    extractCategories();
    populateLocationDropdowns();
    updateNavStateAndCounts();

    if (typeof state.onWorkspaceRefresh === 'function') {
      state.onWorkspaceRefresh();
    }
  } catch (err) {
    if (reqSeq !== loadAllDataSeq) return;
    console.error('Data load error:', err);

    const msg = (err && err.message) ? err.message : '';
    const isConnectionError = msg.startsWith('Cannot reach the server') || msg.startsWith('Request timed out');

    if (!hasCompletedInitialLoad && isConnectionError && initialRetryCount < MAX_INITIAL_RETRIES) {
      initialRetryCount += 1;
      renderBootLoadingState(
        'Starting WIMS...',
        `Waiting for server to come up (attempt ${initialRetryCount}/${MAX_INITIAL_RETRIES})`,
        false
      );
      initialRetryTimer = setTimeout(() => {
        initialRetryTimer = null;
        loadAllData();
      }, INITIAL_RETRY_DELAY_MS);
      return;
    }

    if (!hasCompletedInitialLoad) {
      renderBootLoadingState(
        'Unable to connect to WIMS server',
        msg || 'Make sure the server is running and try again.',
        true
      );
    }
    showToast('Could not load your items: ' + msg, 'error');
  }
}
