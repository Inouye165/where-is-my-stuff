/**
 * Shared Compartment & Grid Cell Actions
 * Single source of truth for:
 *  - Clicking an unoccupied grid coordinate to create & open its compartment page
 *  - Inline grid-cell rename & quick item entry directly on the container view
 *  - Shared compartment label updates, bulk item quick-add, photo upload, and AI photo scan
 */
import { apiPatch, apiPost, apiUpload } from './api.js';
import { pluralize, sanitizePhotoUrl, showToast } from './dom.js';
import { openRapidEntryModal, parseJsonInventoryInput } from './modals.js';
import { compressImageFile } from './photo-compress.js';
import { navToDrawer, renderCurrentWorkspace } from './router.js';
import { getLocType, loadAllData, state } from './state.js';

const NOUN_BY_LOC_TYPE = {
  compartment: 'compartment',
  drawer: 'drawer',
  bin: 'bin',
  shelf: 'shelf',
  cubby: 'cubby',
  box: 'box',
  kit: 'kit',
  container: 'container',
};

const gridInlineState = {
  renameId: null,
  quickAddId: null,
};

const inFlightSlotKeys = new Set();

export function getCompartmentNoun(loc, capitalized = false) {
  const rawType = String(getLocType(loc) || '').toLowerCase();
  const noun = NOUN_BY_LOC_TYPE[rawType] || 'drawer';
  return capitalized ? noun.charAt(0).toUpperCase() + noun.slice(1) : noun;
}

export function getGridInlineEditState() {
  return {
    renameId: gridInlineState.renameId,
    quickAddId: gridInlineState.quickAddId,
  };
}

export function clearGridInlineEditState() {
  gridInlineState.renameId = null;
  gridInlineState.quickAddId = null;
}

export function inferUnitChildSlotType(unit) {
  const children = Array.isArray(unit?.children) ? unit.children : [];
  const placed = children.filter(c => Number(c.grid_row) >= 1 && Number(c.grid_col) >= 1);
  if (!placed.length) return 'drawer';
  const counts = new Map();
  for (const ch of placed) {
    const t = getLocType(ch) || 'drawer';
    counts.set(t, (counts.get(t) || 0) + 1);
  }
  let bestType = 'drawer';
  let bestCount = -1;
  for (const [t, cnt] of counts.entries()) {
    if (cnt > bestCount) {
      bestType = t;
      bestCount = cnt;
    }
  }
  return bestType;
}

export function findFirstOpenGridCoord(unit) {
  const rows = Number(unit?.grid_rows) || 0;
  const cols = Number(unit?.grid_cols) || 0;
  if (!rows || !cols) return null;

  const occupied = new Set();
  for (const ch of (unit?.children || [])) {
    const r0 = Number(ch.grid_row);
    const c0 = Number(ch.grid_col);
    if (!(r0 >= 1 && c0 >= 1)) continue;
    const rs = Math.max(1, Number(ch.grid_row_span) || 1);
    const cs = Math.max(1, Number(ch.grid_col_span) || 1);
    for (let r = r0; r < r0 + rs; r++) {
      for (let c = c0; c < c0 + cs; c++) {
        occupied.add(`${r},${c}`);
      }
    }
  }

  for (let r = 1; r <= rows; r++) {
    for (let c = 1; c <= cols; c++) {
      if (!occupied.has(`${r},${c}`)) {
        return { grid_row: r, grid_col: c };
      }
    }
  }
  return null;
}

export async function openOrCreateGridCompartment(unitId, row, col) {
  const safeUnitId = Number(unitId);
  const r = Number(row);
  const c = Number(col);
  if (!safeUnitId || !(r >= 1 && c >= 1)) return null;

  const unit = state.locationMap.get(safeUnitId);
  if (unit && Array.isArray(unit.children)) {
    const existing = unit.children.find(ch => {
      const r0 = Number(ch.grid_row);
      const c0 = Number(ch.grid_col);
      if (!(r0 >= 1 && c0 >= 1)) return false;
      const rs = Math.max(1, Number(ch.grid_row_span) || 1);
      const cs = Math.max(1, Number(ch.grid_col_span) || 1);
      return r >= r0 && r < r0 + rs && c >= c0 && c < c0 + cs;
    });
    if (existing) {
      clearGridInlineEditState();
      navToDrawer(Number(existing.id), true);
      return existing;
    }
  }

  const lockKey = `${safeUnitId}:${r},${c}`;
  if (inFlightSlotKeys.has(lockKey)) return null;
  inFlightSlotKeys.add(lockKey);

  try {
    const slotType = inferUnitChildSlotType(unit);
    let slot = null;
    try {
      slot = await apiPost(
        `/api/v1/locations/${safeUnitId}/grid/slot`,
        {
          grid_row: r,
          grid_col: c,
          location_type: slotType,
        },
        'Could not open compartment.'
      );
    } catch (slotErr) {
      if (slotErr && (slotErr.status === 404 || slotErr.status === 405)) {
        const colLetter = c >= 1 && c <= 26 ? String.fromCharCode(64 + c) : String(c);
        slot = await apiPost(
          '/api/v1/locations',
          {
            name: `${colLetter}${r}`,
            location_type: slotType,
            parent_id: safeUnitId,
            grid_row: r,
            grid_col: c,
            grid_row_span: 1,
            grid_col_span: 1,
          },
          'Could not open compartment.'
        );
      } else {
        throw slotErr;
      }
    }
    clearGridInlineEditState();
    await loadAllData();
    if (slot && slot.id) {
      navToDrawer(Number(slot.id), true);
    }
    return slot;
  } catch (err) {
    showToast(err.message || 'Could not open compartment.', 'error');
    return null;
  } finally {
    inFlightSlotKeys.delete(lockKey);
  }
}

export async function updateCompartmentLabel(locationId, { name, label, description } = {}) {
  const safeId = Number(locationId);
  const trimmedName = String(name || '').trim();
  if (!safeId || !trimmedName) {
    showToast('Name cannot be empty.', 'error');
    return false;
  }
  const payload = { name: trimmedName };
  if (label !== undefined) {
    payload.label = String(label || '').trim().slice(0, 40) || null;
  }
  if (description !== undefined) {
    payload.description = String(description || '').trim() || null;
  }
  try {
    await apiPatch(`/api/v1/locations/${safeId}`, payload, 'Could not update name.');
    showToast('Name updated!');
    await loadAllData();
    return true;
  } catch (err) {
    showToast(err.message || 'Could not update name.', 'error');
    return false;
  }
}

export async function quickAddItemsToCompartment(locationId, rawInput) {
  const safeId = Number(locationId);
  const raw = String(rawInput || '').trim();
  if (!safeId || !raw) {
    showToast('Type an item name or paste a list first.', 'error');
    return null;
  }

  const jsonResult = parseJsonInventoryInput(raw);
  try {
    if (jsonResult && jsonResult.items.length > 0) {
      const existing = state.items.filter(i => Number(i.location_id) === safeId);
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
          location_id: safeId,
        });
      }
      if (!itemsToCreate.length) {
        showToast('All of those items are already in this compartment.');
        return { created: [], skipped };
      }
      const created = await apiPost('/api/v1/items/bulk', { location_id: safeId, items: itemsToCreate }, 'Could not add items.');
      showToast(`Added ${pluralize(created.length, 'item')}${skipped ? ` (${skipped} already here skipped)` : ''}!`);
      await loadAllData();
      return { created: Array.isArray(created) ? created : [], skipped };
    }

    const lines = raw.includes('\n') ? raw : raw.split(',').map(s => s.trim()).filter(Boolean).join('\n');
    const created = await apiPost('/api/v1/items/bulk', { location_id: safeId, raw_lines: lines }, 'Could not add items.');
    showToast(`Added ${pluralize(created.length, 'item')}!`);
    await loadAllData();
    return { created: Array.isArray(created) ? created : [], skipped: 0 };
  } catch (err) {
    showToast(err.message || 'Could not add items.', 'error');
    return null;
  }
}

export async function uploadPhotosToCompartment(locationId, rawFiles, { autoScan = false } = {}) {
  const safeId = Number(locationId);
  const files = Array.from(rawFiles || []).filter(Boolean);
  if (!safeId || !files.length) return [];

  const uploadedPhotos = [];
  try {
    for (const file of files) {
      const compressed = await compressImageFile(file);
      const fd = new FormData();
      fd.append('file', compressed);
      const saved = await apiUpload(`/api/v1/locations/${safeId}/photos`, fd, 'Photo upload failed.');
      if (saved) uploadedPhotos.push(saved);
    }
    showToast(`Added ${pluralize(uploadedPhotos.length, 'photo')}!`);
    await loadAllData();

    if (autoScan && uploadedPhotos.length > 0) {
      const photoIds = uploadedPhotos.map(p => Number(p.id)).filter(Boolean);
      await scanCompartmentPhotosWithAi(safeId, {
        photoIds,
        photoUrl: uploadedPhotos[0]?.photo_path || null,
      });
    }
    return uploadedPhotos;
  } catch (err) {
    const msg = err.message || 'Could not upload photo.';
    showToast(
      uploadedPhotos.length > 0 ? `${msg} (${uploadedPhotos.length} of ${files.length} saved)` : msg,
      'error'
    );
    if (uploadedPhotos.length > 0) await loadAllData();
    return uploadedPhotos;
  }
}

export async function scanCompartmentPhotosWithAi(locationId, options = {}) {
  const safeId = Number(locationId);
  if (!safeId) return;
  const loc = state.locationMap.get(safeId);
  const savedPhotos = Array.isArray(loc?.photos) ? loc.photos : [];

  const explicitIds = Array.isArray(options.photoIds)
    ? options.photoIds.map(Number).filter(Boolean)
    : (options.photoId ? [Number(options.photoId)] : []);

  const resolvedIds = explicitIds.length > 0
    ? explicitIds
    : savedPhotos.map(p => Number(p.id)).filter(Boolean);

  const fallbackUrl = sanitizePhotoUrl(
    options.photoUrl || savedPhotos[0]?.photo_path || loc?.container_photo || ''
  );

  const saveMode = options.saveMode || 'separate';
  const sessionPhotoIds = Array.isArray(options.sessionPhotoIds)
    ? options.sessionPhotoIds.map(Number).filter(Boolean)
    : undefined;

  if (resolvedIds.length > 0 || fallbackUrl) {
    await openRapidEntryModal(safeId, {
      photoId: resolvedIds[0] || null,
      photoIds: resolvedIds,
      sessionPhotoIds,
      photoUrl: fallbackUrl || null,
      autoScan: true,
      saveMode,
      initialBoxName: options.initialBoxName || '',
      initialBoxLabel: options.initialBoxLabel || '',
    });
  } else {
    await openRapidEntryModal(safeId, {
      saveMode,
      initialBoxName: options.initialBoxName || '',
      initialBoxLabel: options.initialBoxLabel || '',
    });
  }
}

export function triggerCompartmentUploadAndScan(locationId) {
  const input = document.getElementById(`compartment-upload-scan-${Number(locationId)}`);
  if (input) input.click();
}

export async function handleCompartmentUploadAndScan(locationId, e) {
  const files = Array.from(e?.target?.files || []).filter(Boolean);
  if (e?.target) e.target.value = '';
  if (!files.length) return;
  await uploadPhotosToCompartment(locationId, files, { autoScan: true });
}

// =========================================================================
// Inline Grid-Cell Rename & Quick-Add Actions (Organizer Screen)
// =========================================================================

export function startGridCellRename(locationId, e = null) {
  if (e) e.stopPropagation();
  const safeId = Number(locationId);
  if (!safeId) return;
  gridInlineState.renameId = safeId;
  gridInlineState.quickAddId = null;
  renderCurrentWorkspace();
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(() => {
      const input = document.getElementById(`grid-cell-rename-${safeId}`);
      if (input) {
        input.focus();
        input.select();
      }
    });
  }
}

export function startGridCellQuickAdd(locationId, e = null) {
  if (e) e.stopPropagation();
  const safeId = Number(locationId);
  if (!safeId) return;
  gridInlineState.quickAddId = safeId;
  gridInlineState.renameId = null;
  renderCurrentWorkspace();
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(() => {
      const input = document.getElementById(`grid-cell-add-${safeId}`);
      if (input) input.focus();
    });
  }
}

export function cancelGridCellInlineEdit(e = null) {
  if (e) e.stopPropagation();
  clearGridInlineEditState();
  renderCurrentWorkspace();
}

export async function saveGridCellRename(locationId, e = null) {
  if (e) e.stopPropagation();
  const safeId = Number(locationId);
  const input = document.getElementById(`grid-cell-rename-${safeId}`);
  const nextName = (input?.value || '').trim();
  if (!nextName) {
    showToast('Enter a compartment name first.', 'error');
    input?.focus();
    return false;
  }
  gridInlineState.renameId = null;
  return updateCompartmentLabel(safeId, { name: nextName });
}

export async function submitGridCellQuickAdd(locationId, e = null) {
  if (e) e.stopPropagation();
  const safeId = Number(locationId);
  const input = document.getElementById(`grid-cell-add-${safeId}`);
  const raw = (input?.value || '').trim();
  if (!raw) {
    showToast('Enter an item name (e.g., "2x AA batteries") first.', 'error');
    input?.focus();
    return null;
  }
  gridInlineState.quickAddId = null;
  return quickAddItemsToCompartment(safeId, raw);
}
