/**
 * Application State, Starter Room Presets & Spatial Hierarchy Indexing
 */
import { apiGet } from './api.js';
import { escapeHtml, showToast } from './dom.js';

export const UNASSIGNED_ROOM_LABEL = 'Needs a home';
export const HOME_SEARCH_PLACEHOLDER = 'What are you looking for?';

export const STARTER_ROOMS = [
  { name: 'Garage', emoji: '🔧' },
  { name: 'Office', emoji: '💻' },
  { name: 'Bedroom', emoji: '🛏️' },
  { name: 'Closet', emoji: '🚪' },
  { name: 'Kitchen', emoji: '🍳' }
];

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
  homeSearchHintIndex: 0,
  photoSearchPreviewUrl: '',
  photoSearchLoading: false,
  needsHomeIndex: 0,
  itemsSearchQuery: '',
  selectedCategory: '',
  selectedContainerId: '',
  selectedSort: 'name_asc',

  // Active Modals / Sheets
  activeDrawerItem: null,
  storageTypeChoice: 'unit',
  quickPhotoTarget: null,
  roomEmojis: loadJsonStorage('wims_room_emojis', {}),
  dismissedStarters: loadJsonStorage('wims_dismissed_starters', []),

  // Theme ('system' | 'light' | 'dark')
  themePref: (typeof localStorage !== 'undefined' && localStorage.getItem('wims_theme_pref')) || 'system',
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

  function walk(node) {
    state.locationMap.set(node.id, node);
    const ltype = getLocType(node);

    if (!node.parent_id && (ltype === 'room' || ltype === 'site') && !isUnknownPlaceholder(node)) {
      rawRooms.push(node);
    }

    if (
      ltype === 'container' ||
      (node.parent_id && node.children && node.children.length > 0 && ltype !== 'room' && ltype !== 'site')
    ) {
      rawContainers.push(node);
    }

    if (node.children && node.children.length > 0) {
      for (const child of node.children) {
        walk(child);
      }
    }
  }

  for (const root of state.locations) {
    walk(root);
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

export function getLocationHierarchy(locationId) {
  const loc = state.locationMap.get(locationId);
  if (!loc) {
    return {
      room: null,
      unit: null,
      drawer: null,
      roomName: UNASSIGNED_ROOM_LABEL,
      unitName: 'No container',
      drawerName: 'Unassigned',
      humanPath: UNASSIGNED_ROOM_LABEL,
      isNeedsHome: true
    };
  }

  const ancestors = getLocationAncestors(loc.id);
  let room = null;
  let unit = null;
  let drawer = null;

  for (const node of ancestors) {
    const ltype = getLocType(node);
    if (isUnknownPlaceholder(node)) {
      continue;
    }
    if (!room && (ltype === 'room' || ltype === 'site')) {
      room = node;
    } else if (!unit && (ltype === 'container' || (node.children && node.children.length > 0))) {
      unit = node;
    } else {
      drawer = node;
    }
  }

  const isNeedsHome = !room;
  const roomName = room ? room.name : UNASSIGNED_ROOM_LABEL;
  const unitName = unit ? unit.name : (drawer ? 'Loose spot' : roomName);
  const drawerName = drawer ? drawer.name : (unit ? unit.name : roomName);

  const segments = [roomName];
  if (unit && unit.name !== roomName) segments.push(unit.name);
  if (drawer && drawer.name !== unitName) segments.push(drawer.name);

  return {
    room,
    unit,
    drawer,
    roomName,
    unitName,
    drawerName,
    humanPath: segments.join(' › '),
    searchPath: segments.join(' > '),
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
    itemSelect.innerHTML = '<option value="">-- Choose a drawer, container, or room --</option>';
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
    const sortedForRapid = allLocations.slice().sort((a, b) => {
      const aIsBin = getLocType(a) === 'bin' ? 0 : 1;
      const bIsBin = getLocType(b) === 'bin' ? 0 : 1;
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

    for (const starter of getStarterSuggestions()) {
      const opt = document.createElement('option');
      opt.value = `__starter__:${starter.name}:${starter.emoji}`;
      opt.textContent = `${starter.emoji} ${starter.name}`;
      parentSelect.appendChild(opt);
    }
  } else if (kind === 'drawer') {
    for (const c of state.containers) {
      if (excludeId && c.id === excludeId) continue;
      const opt = document.createElement('option');
      opt.value = c.id;
      opt.textContent = formatHumanPath(c.full_path);
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
      parentSelect.value = String(selectedParentId);
    }
  }
}

export function updateNavStateAndCounts() {
  const isPlaces = state.activeTab === 'places';
  const isItems = state.activeTab === 'items';

  document.getElementById('rail-nav-places')?.classList.toggle('active', isPlaces);
  document.getElementById('rail-nav-items')?.classList.toggle('active', isItems);

  document.getElementById('mobile-nav-places')?.classList.toggle('active', isPlaces);
  document.getElementById('mobile-nav-items')?.classList.toggle('active', isItems);

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

export async function loadAllData(useInitialRoute = false) {
  try {
    const [items, locations] = await Promise.all([
      apiGet('/api/v1/items?limit=500', 'Could not load items.'),
      apiGet('/api/v1/locations/tree', 'Could not load rooms and containers.')
    ]);

    state.items = items;
    state.locations = locations;

    indexLocations();
    extractCategories();
    populateLocationDropdowns();
    updateNavStateAndCounts();

    if (typeof state.onWorkspaceRefresh === 'function') {
      state.onWorkspaceRefresh(useInitialRoute);
    }
  } catch (err) {
    console.error('Data load error:', err);
    showToast('Could not load your items: ' + err.message, 'error');
  }
}
