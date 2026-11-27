/**
 * Sub-Container (Box / Kit) Creation, Item-to-Box Conversion & Box Moving
 */
import { apiPatch, apiPost } from './api.js';
import { closeModal, escapeHtml, sanitizePhotoUrl, showToast, withSubmitLock } from './dom.js';
import { navToDrawer } from './router.js';
import { formatHumanPath, getLocationHierarchy, isUnknownPlaceholder, loadAllData, state } from './state.js';

/**
 * Creates a child Box / Container (`location_type: "box"`) inside `parentLocationId`
 * from an AI-detected or manually entered row, preserving its thumbnail/crop photo.
 */
export async function createSubBoxFromDetectedRow(parentLocationId, row) {
  const cleanName = String(row?.name || '').trim().slice(0, 120);
  if (!parentLocationId || !cleanName) {
    throw new Error('Box name and location are required.');
  }

  const descParts = [];
  if (row?.part_number && String(row.part_number).trim()) {
    descParts.push(String(row.part_number).trim());
  }
  const noteText = row?.brief_specs ?? row?.notes ?? '';
  if (noteText && String(noteText).trim()) {
    descParts.push(String(noteText).trim());
  }
  const description = descParts.join(' — ').slice(0, 2000) || null;
  const photoRef = sanitizePhotoUrl(row?.crop_photo_path || row?._photoPath || row?.cropPhotoPath || '') || null;

  const payload = {
    name: cleanName,
    description,
    location_type: 'box',
    parent_id: Number(parentLocationId),
  };
  const cleanLabel = String(row?.box_label ?? row?.label ?? '').trim().slice(0, 40);
  if (cleanLabel) {
    payload.label = cleanLabel;
  }
  if (photoRef) {
    payload.container_photo = photoRef;
  }

  return apiPost('/api/v1/locations', payload, `Could not create container "${cleanName}".`);
}

export function openConvertItemToBoxModal(itemId) {
  const item = state.items.find((i) => Number(i.id) === Number(itemId));
  if (!item) return;

  const idEl = document.getElementById('convert-box-item-id');
  const nameEl = document.getElementById('convert-box-name-input');
  const labelEl = document.getElementById('convert-box-label-input');
  const keepEl = document.getElementById('convert-box-keep-item');
  const previewEl = document.getElementById('convert-box-preview');

  if (idEl) idEl.value = String(item.id);
  if (nameEl) nameEl.value = item.name || '';
  if (labelEl) labelEl.value = '';
  if (keepEl) keepEl.checked = false;

  if (previewEl) {
    const thumb = sanitizePhotoUrl(item.item_photo || '');
    const hier = getLocationHierarchy(item.location_id);
    previewEl.innerHTML = `
      ${thumb ? `<img class="convert-box-preview-img" src="${escapeHtml(thumb)}" alt="${escapeHtml(item.name)}">` : ''}
      <div style="min-width: 0;">
        <div class="convert-box-preview-name">${escapeHtml(item.name)}</div>
        <div class="convert-box-preview-path">${escapeHtml(hier.humanPath || 'Needs a home')}</div>
      </div>
    `;
  }

  document.getElementById('convert-to-box-modal')?.classList.add('open');
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(() => {
      nameEl?.focus();
      nameEl?.select();
    });
  }
}

export async function submitConvertItemToBox(e) {
  if (e && typeof e.preventDefault === 'function') e.preventDefault();
  const itemId = parseInt(document.getElementById('convert-box-item-id')?.value || '0', 10);
  const boxName = (document.getElementById('convert-box-name-input')?.value || '').trim();
  const boxLabel = (document.getElementById('convert-box-label-input')?.value || '').trim() || null;
  const keepInside = Boolean(document.getElementById('convert-box-keep-item')?.checked);

  if (!itemId || !boxName) {
    showToast('Please enter a name for the box / container.', 'error');
    return;
  }

  return withSubmitLock('convert-box-submit-btn', 'Converting...', async () => {
    try {
      const box = await apiPost(
        `/api/v1/items/${itemId}/convert-to-box`,
        { box_name: boxName, box_label: boxLabel, keep_item_inside: keepInside },
        'Could not convert item to box.'
      );
      const wasItemView = state.currentView === 'item' && Number(state.currentLocationId) === itemId;
      closeModal('convert-to-box-modal', true);
      if (typeof window !== 'undefined' && typeof window.expandSubBox === 'function' && box?.id) {
        window.expandSubBox(box.id);
      }
      await loadAllData();
      showToast(`Converted to container "${box?.name || boxName}" — open it to add photos & items.`);
      if (wasItemView && box?.id) {
        navToDrawer(Number(box.id), true);
      }
    } catch (err) {
      showToast(err?.message || 'Could not convert item to box.', 'error');
    }
  });
}

function collectDescendantIds(loc, out = new Set()) {
  if (!loc) return out;
  out.add(Number(loc.id));
  for (const ch of (loc.children || [])) {
    collectDescendantIds(ch, out);
  }
  return out;
}

export function openMoveBoxModal(boxId) {
  const box = state.locationMap.get(Number(boxId));
  if (!box) return;

  const excludedIds = collectDescendantIds(box, new Set());
  const idInput = document.getElementById('move-box-id');
  const titleEl = document.getElementById('move-box-title');
  const pathEl = document.getElementById('move-box-current-path');
  const selectEl = document.getElementById('move-box-dest-select');

  if (idInput) idInput.value = String(box.id);
  if (titleEl) titleEl.textContent = `Move "${box.name}"`;
  if (pathEl) pathEl.textContent = formatHumanPath(box.full_path || box.name);

  if (selectEl) {
    const candidates = Array.from(state.locationMap.values())
      .filter((loc) => !excludedIds.has(Number(loc.id)) && !isUnknownPlaceholder(loc))
      .sort((a, b) => (a.full_path || a.name).localeCompare(b.full_path || b.name, undefined, { numeric: true, sensitivity: 'base' }));

    selectEl.innerHTML = [
      `<option value="">Needs a home</option>`,
      ...candidates.map((loc) => {
        const label = formatHumanPath(loc.full_path || loc.name);
        const isCurrent = Number(box.parent_id) === Number(loc.id);
        return `<option value="${Number(loc.id)}" ${isCurrent ? 'selected' : ''}>${escapeHtml(label)}</option>`;
      }),
    ].join('');
  }

  document.getElementById('move-box-modal')?.classList.add('open');
}

export async function submitMoveBox() {
  const boxId = parseInt(document.getElementById('move-box-id')?.value || '0', 10);
  const rawDest = document.getElementById('move-box-dest-select')?.value;
  const parentId = rawDest ? parseInt(rawDest, 10) : null;
  if (!boxId) return;

  const box = state.locationMap.get(boxId);
  const destLoc = parentId ? state.locationMap.get(parentId) : null;

  return withSubmitLock('move-box-save-btn', 'Moving...', async () => {
    try {
      await apiPatch(`/api/v1/locations/${boxId}`, { parent_id: parentId }, 'Could not move box.');
      closeModal('move-box-modal', true);
      showToast(`Moved "${box?.name || 'Box'}" to ${destLoc ? destLoc.name : 'Needs a home'}.`);
      await loadAllData();
    } catch (err) {
      showToast(err?.message || 'Could not move box.', 'error');
    }
  });
}
