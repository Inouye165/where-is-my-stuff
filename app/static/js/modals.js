/**
 * Modals, Unified '+' Sheet, Photo Flows & Printable QR Labels
 */
import { apiDelete, apiGet, apiPatch, apiPost } from './api.js';
import { closeModal, escapeHtml, pluralize, sanitizePhotoUrl, showToast, withSubmitLock } from './dom.js';
import { openInlineGridDesigner } from './grid-designer.js';
import { openLocationPhoneCaptureModal, requestPhotoForInput, registerDesktopPhotoHandler } from './phone-capture.js';
import { compressImageFile, uploadPhotoAndGetPath } from './photo-compress.js';
import { navToHome, navToStorageUnit } from './router.js';
import {
  formatHumanPath,
  getLocType,
  getRoomEmoji,
  loadAllData,
  populateLocationDropdowns,
  populateStorageParentDropdown,
  saveJsonStorage,
  state,
  updateNavStateAndCounts
} from './state.js';
import { ensureRoomExists, openItemDrawer } from './views.js';

// ============================================================================
// 1. UNIFIED '+' ACTION SHEET
// ============================================================================
export function openAddSheet() {
  document.getElementById('add-action-sheet')?.classList.add('open');
}

export function chooseAddAction(choice) {
  closeModal('add-action-sheet');
  const ctxLocId = state.currentLocationId || null;

  if (choice === 'snap') {
    openRapidEntryModal(ctxLocId);
  } else if (choice === 'item') {
    openAddItemModal(ctxLocId, null);
  } else if (choice === 'place') {
    if (state.currentView === 'room' && ctxLocId) {
      openAddStorageModal(ctxLocId, 'unit');
    } else if (state.currentView === 'unit' && ctxLocId) {
      openAddStorageModal(ctxLocId, 'drawer');
    } else {
      openAddStorageModal(null, 'room');
    }
  }
}

// ============================================================================
// 2. ADD / EDIT ITEM MODAL
// ============================================================================
export function openAddItemModal(defaultLocationId = null, existingItem = null) {
  populateLocationDropdowns();
  const modal = document.getElementById('add-item-modal');
  const title = document.getElementById('item-modal-title');
  const submitBtn = document.getElementById('item-submit-btn');

  document.getElementById('edit-item-id').value = existingItem ? existingItem.id : '';
  document.getElementById('item-name').value = existingItem ? existingItem.name : '';
  document.getElementById('item-quantity').value =
    existingItem && existingItem.quantity !== null && existingItem.quantity !== undefined ? existingItem.quantity : '';
  document.getElementById('item-unit').value = existingItem ? (existingItem.unit || 'pcs') : 'pcs';
  document.getElementById('item-category').value = existingItem ? (existingItem.category || '') : '';
  document.getElementById('item-serial').value = existingItem ? (existingItem.serial_or_part_number || '') : '';
  document.getElementById('item-tags').value = existingItem ? (existingItem.tags || '') : '';
  document.getElementById('item-description').value = existingItem ? (existingItem.description || '') : '';

  const photoInput = document.getElementById('item-photo-input');
  if (photoInput) {
    photoInput.value = '';
    photoInput.dataset.remotePhotoPath = '';
  }
  const photoLabel = document.getElementById('item-photo-input-label');
  if (photoLabel) photoLabel.textContent = 'Add photo';

  const locSelect = document.getElementById('item-location-select');
  if (locSelect) {
    const locVal = existingItem ? existingItem.location_id : defaultLocationId;
    locSelect.value = locVal ? String(locVal) : '';
  }

  if (title) title.textContent = existingItem ? 'Edit Item' : 'Add Item Manually';
  if (submitBtn) submitBtn.textContent = existingItem ? 'Save changes' : 'Save item';

  modal?.classList.add('open');
}

export function handleItemPhotoPreview(e) {
  const file = e.target.files && e.target.files[0];
  if (e.target) e.target.dataset.remotePhotoPath = '';
  const label = document.getElementById('item-photo-input-label');
  if (label) label.textContent = file ? file.name : 'Add photo';
}

export async function handleItemFormSubmit(e) {
  e.preventDefault();
  return withSubmitLock('item-submit-btn', 'Saving...', async () => {
    const editId = document.getElementById('edit-item-id').value;
    const name = document.getElementById('item-name').value.trim();
    const locRaw = document.getElementById('item-location-select').value;
    const locationId = locRaw ? parseInt(locRaw, 10) : null;
    const qtyVal = document.getElementById('item-quantity').value;
    const quantity = qtyVal === '' ? null : parseInt(qtyVal, 10);
    const unit = document.getElementById('item-unit').value.trim() || 'pcs';
    const category = document.getElementById('item-category').value.trim() || null;
    const serialOrPartNumber = document.getElementById('item-serial').value.trim() || null;
    const tags = document.getElementById('item-tags').value.trim() || null;
    const description = document.getElementById('item-description').value.trim() || null;
    const photoInput = document.getElementById('item-photo-input');

    try {
      let uploadedPhotoPath = photoInput?.dataset?.remotePhotoPath || undefined;
      if (!uploadedPhotoPath && photoInput && photoInput.files && photoInput.files[0]) {
        uploadedPhotoPath = await uploadPhotoAndGetPath(photoInput.files[0]);
      }

      const payload = {
        name,
        location_id: locationId,
        quantity,
        unit,
        category,
        serial_or_part_number: serialOrPartNumber,
        tags,
        description
      };
      if (uploadedPhotoPath) payload.item_photo = uploadedPhotoPath;

      if (editId) {
        await apiPatch(`/api/v1/items/${editId}`, payload, 'Could not update item.');
      } else {
        await apiPost('/api/v1/items', payload, 'Could not save item.');
      }

      closeModal('add-item-modal', true);
      showToast(editId ? `Updated "${name}".` : `Saved "${name}".`);
      await loadAllData();
    } catch (err) {
      showToast(err.message, 'error');
    }
  });
}

// ============================================================================
// 3. ADD / EDIT ROOM, CONTAINER, OR DRAWER MODAL
// ============================================================================
export function openAddStorageModal(parentLocationId = null, kind = 'unit') {
  const modal = document.getElementById('add-storage-modal');
  document.getElementById('edit-storage-id').value = '';
  document.getElementById('storage-name').value = '';
  document.getElementById('storage-desc').value = '';
  document.getElementById('auto-drawers-toggle').checked = false;
  toggleAutoDrawersUi();
  const useGridEl = document.getElementById('use-grid-toggle');
  if (useGridEl) {
    useGridEl.checked = false;
    toggleUseGridUi();
  }

  const binPhoto = document.getElementById('storage-bin-photo');
  const spotPhoto = document.getElementById('storage-spot-photo');
  if (binPhoto) {
    binPhoto.value = '';
    binPhoto.dataset.remotePhotoPath = '';
  }
  if (spotPhoto) {
    spotPhoto.value = '';
    spotPhoto.dataset.remotePhotoPath = '';
  }
  document.getElementById('storage-bin-photo-label').textContent = 'Add photo';
  document.getElementById('storage-spot-photo-label').textContent = 'Add photo';
  document.getElementById('storage-delete-btn').style.display = 'none';
  document.getElementById('storage-type-segmented').style.display = 'flex';
  document.getElementById('storage-inline-room-field').style.display = 'none';

  setStorageTypeChoice(kind, parentLocationId);
  selectRoomEmoji('🔧');

  modal?.classList.add('open');
}

export function openEditStorageModal(locationId) {
  const loc = state.locationMap.get(locationId);
  if (!loc) return;

  const ltype = getLocType(loc);
  const kind = ltype === 'room' || ltype === 'site' ? 'room' : ltype === 'container' ? 'unit' : 'drawer';

  document.getElementById('edit-storage-id').value = String(loc.id);
  document.getElementById('storage-type-segmented').style.display = 'none';
  setStorageTypeChoice(kind, loc.parent_id, loc.id);

  document.getElementById('storage-modal-title').textContent = `Edit ${loc.name}`;
  document.getElementById('storage-name').value = loc.name;
  document.getElementById('storage-desc').value = loc.description || '';
  document.getElementById('multi-drawer-box').style.display = 'none';
  document.getElementById('storage-delete-btn').style.display = 'inline-flex';

  if (kind === 'room') {
    selectRoomEmoji(getRoomEmoji(loc));
  }

  document.getElementById('add-storage-modal')?.classList.add('open');
}

export function setStorageTypeChoice(kind, selectedParentId = null, excludeId = null) {
  state.storageTypeChoice = kind;
  document.getElementById('seg-room')?.classList.toggle('active', kind === 'room');
  document.getElementById('seg-unit')?.classList.toggle('active', kind === 'unit');
  document.getElementById('seg-drawer')?.classList.toggle('active', kind === 'drawer');

  const isEditing = Boolean(document.getElementById('edit-storage-id').value);
  const title = document.getElementById('storage-modal-title');
  const emojiField = document.getElementById('room-emoji-field');
  const parentField = document.getElementById('storage-parent-field');
  const parentLabel = document.getElementById('storage-parent-label');
  const nameLabel = document.getElementById('storage-name-label');
  const nameInput = document.getElementById('storage-name');
  const multiBox = document.getElementById('multi-drawer-box');
  const binPhotoField = document.getElementById('storage-bin-photo-field');
  const spotPhotoTitle = document.getElementById('storage-spot-photo-title');

  if (kind === 'room') {
    if (!isEditing) title.textContent = 'Add a room';
    emojiField.style.display = 'flex';
    parentField.style.display = 'flex';
    parentLabel.textContent = 'Inside another room? (Optional)';
    document.getElementById('storage-inline-room-field').style.display = 'none';
    nameLabel.textContent = 'Room name *';
    nameInput.placeholder = 'e.g. Garage, Office, Bedroom, Closet';
    multiBox.style.display = 'none';
    binPhotoField.style.display = 'none';
    spotPhotoTitle.textContent = 'Room photo (Optional)';
    populateStorageParentDropdown('room', selectedParentId, excludeId);
  } else if (kind === 'unit') {
    if (!isEditing) title.textContent = 'Add a container';
    emojiField.style.display = 'none';
    parentField.style.display = 'flex';
    parentLabel.textContent = 'Which room is it in?';
    nameLabel.textContent = 'Container name *';
    nameInput.placeholder = 'e.g. Yellow Organizer, Clear Tote, Tool Chest';
    multiBox.style.display = isEditing ? 'none' : 'block';
    binPhotoField.style.display = 'flex';
    spotPhotoTitle.textContent = 'Where it sits';
    populateStorageParentDropdown('unit', selectedParentId, excludeId);
  } else {
    if (!isEditing) title.textContent = 'Add a drawer or bin';
    emojiField.style.display = 'none';
    parentField.style.display = 'flex';
    parentLabel.textContent = 'Inside which container?';
    nameLabel.textContent = 'Drawer or bin label *';
    nameInput.placeholder = 'e.g. Drawer 1, Top Bin, Batteries';
    multiBox.style.display = 'none';
    binPhotoField.style.display = 'flex';
    spotPhotoTitle.textContent = 'Where it sits';
    populateStorageParentDropdown('drawer', selectedParentId, excludeId);
  }
}

export function selectRoomEmoji(emoji) {
  const input = document.getElementById('room-emoji-input');
  if (input) input.value = emoji;
  const buttons = document.querySelectorAll('#room-emoji-picker .emoji-choice');
  for (const b of buttons) {
    b.classList.toggle('active', b.dataset.emoji === emoji);
  }
}

export function handleStorageParentChange() {
  const sel = document.getElementById('storage-parent-select');
  const inlineField = document.getElementById('storage-inline-room-field');
  if (sel && inlineField) {
    inlineField.style.display = sel.value === '__new__' ? 'flex' : 'none';
  }
}

export function toggleAutoDrawersUi() {
  const checked = document.getElementById('auto-drawers-toggle').checked;
  const inputs = document.getElementById('auto-drawers-inputs');
  if (inputs) inputs.style.display = checked ? 'block' : 'none';
  if (checked) {
    const gridToggle = document.getElementById('use-grid-toggle');
    const gridInputs = document.getElementById('use-grid-inputs');
    if (gridToggle) gridToggle.checked = false;
    if (gridInputs) gridInputs.style.display = 'none';
  }
  updateDrawerBatchPreview();
}

export function toggleUseGridUi() {
  const checked = Boolean(document.getElementById('use-grid-toggle')?.checked);
  const inputs = document.getElementById('use-grid-inputs');
  if (inputs) inputs.style.display = checked ? 'block' : 'none';
  if (checked) {
    const autoToggle = document.getElementById('auto-drawers-toggle');
    const autoInputs = document.getElementById('auto-drawers-inputs');
    if (autoToggle) autoToggle.checked = false;
    if (autoInputs) autoInputs.style.display = 'none';
  }
}

export function updateDrawerBatchPreview() {
  const prefix = document.getElementById('auto-prefix').value;
  const start = parseInt(document.getElementById('auto-start').value, 10) || 1;
  const end = parseInt(document.getElementById('auto-end').value, 10) || start;
  const count = Math.max(0, end - start + 1);
  const preview = document.getElementById('auto-drawers-preview-text');
  if (preview) {
    preview.textContent = `Creates ${pluralize(count, 'drawer')} (${prefix} ${start} through ${prefix} ${end})`;
  }
}

export function handleStoragePhotoChange(e, kind) {
  const file = e.target.files && e.target.files[0];
  if (e.target) e.target.dataset.remotePhotoPath = '';
  const labelId = kind === 'bin' ? 'storage-bin-photo-label' : 'storage-spot-photo-label';
  const el = document.getElementById(labelId);
  if (el) el.textContent = file ? file.name : 'Add photo';
}

export async function handleStorageFormSubmit(e) {
  e.preventDefault();
  return withSubmitLock('storage-submit-btn', 'Saving...', async () => {
    const editId = document.getElementById('edit-storage-id').value;
    const name = document.getElementById('storage-name').value.trim();
    const description = document.getElementById('storage-desc').value.trim() || null;
    const kind = state.storageTypeChoice;

    try {
      let parentId = null;
      const rawParent = document.getElementById('storage-parent-select').value;
      if (rawParent === '__new__') {
        const inlineName = document.getElementById('inline-room-name').value.trim();
        if (!inlineName) {
          showToast('Please enter a name for the new room.', 'error');
          return;
        }
        const createdRoom = await ensureRoomExists(inlineName, '🏠');
        parentId = createdRoom.id;
      } else if (rawParent && rawParent.startsWith('__starter__:')) {
        const parts = rawParent.split(':');
        const createdRoom = await ensureRoomExists(parts[1], parts[2] || '🏠');
        parentId = createdRoom.id;
      } else if (rawParent) {
        parentId = parseInt(rawParent, 10);
      }

      const binInput = document.getElementById('storage-bin-photo');
      const spotInput = document.getElementById('storage-spot-photo');
      const binFile = binInput?.files?.[0];
      const spotFile = spotInput?.files?.[0];
      const containerPhotoPath = binInput?.dataset?.remotePhotoPath || (binFile ? await uploadPhotoAndGetPath(binFile) : undefined);
      const contextPhotoPath = spotInput?.dataset?.remotePhotoPath || (spotFile ? await uploadPhotoAndGetPath(spotFile) : undefined);

      const locationType = kind === 'room' ? 'room' : kind === 'unit' ? 'container' : 'bin';
      const payload = {
        name,
        description,
        location_type: locationType,
        parent_id: parentId
      };
      if (containerPhotoPath) payload.container_photo = containerPhotoPath;
      if (contextPhotoPath) payload.context_photo = contextPhotoPath;

      if (!editId && kind === 'unit' && document.getElementById('auto-drawers-toggle').checked) {
        payload.auto_generate_children = {
          prefix: document.getElementById('auto-prefix').value,
          start: parseInt(document.getElementById('auto-start').value, 10) || 1,
          end: parseInt(document.getElementById('auto-end').value, 10) || 10,
          child_type: 'bin'
        };
      }

      const saved = editId
        ? await apiPatch(`/api/v1/locations/${editId}`, payload, 'Could not save place.')
        : await apiPost('/api/v1/locations', payload, 'Could not save place.');

      if (kind === 'room') {
        const emoji = document.getElementById('room-emoji-input').value || '🏠';
        state.roomEmojis[saved.id] = emoji;
        state.roomEmojis[saved.name.toLowerCase()] = emoji;
        saveJsonStorage('wims_room_emojis', state.roomEmojis);
      }

      const openGridAfterSave = !editId && kind === 'unit' && Boolean(document.getElementById('use-grid-toggle')?.checked);
      const initialGridRows = openGridAfterSave ? (parseInt(document.getElementById('new-grid-rows')?.value, 10) || 3) : null;
      const initialGridCols = openGridAfterSave ? (parseInt(document.getElementById('new-grid-cols')?.value, 10) || 6) : null;

      closeModal('add-storage-modal', true);
      showToast(editId ? `Updated "${name}".` : `Added "${name}".`);
      await loadAllData();

      if (openGridAfterSave && saved?.id) {
        navToStorageUnit(saved.id, true);
        openInlineGridDesigner(saved.id, initialGridRows, initialGridCols);
      }
    } catch (err) {
      showToast(err.message, 'error');
    }
  });
}

export async function handleStorageDelete() {
  const editId = document.getElementById('edit-storage-id').value;
  if (!editId) return;
  const loc = state.locationMap.get(parseInt(editId, 10));
  if (!confirm(`Remove "${loc ? loc.name : 'this place'}"? Any boxes or items inside will be moved to "Needs a home" so nothing is lost.`)) return;

  return withSubmitLock('storage-delete-btn', 'Deleting...', async () => {
    try {
      await apiDelete(`/api/v1/locations/${editId}`, 'Could not delete place.');
      closeModal('add-storage-modal', true);
      showToast('Deleted.');
      await loadAllData();
      navToHome();
    } catch (err) {
      showToast(err.message, 'error');
    }
  });
}

// ============================================================================
// 4. SNAP & BATCH ENTRY MODAL
// ============================================================================
let rapidSelectedFiles = [];
let rapidRemotePhotoPath = null;
let rapidPhotoId = null;
let rapidScannedPhotoUrl = null;
let rapidPreviewUrl = null;
let rapidVisionMeta = null;
let rapidDetectedItems = [];

function updateVisionStatusUI() {
  const select = document.getElementById('rapid-vision-provider');
  const statusEl = document.getElementById('rapid-vision-status');
  const modelInput = document.getElementById('rapid-vision-model');
  if (!select || !statusEl) return;

  const provider = select.value || 'local';
  const info = rapidVisionMeta?.providers?.[provider];
  if (!info) {
    statusEl.textContent = 'Ready';
    statusEl.className = 'ai-provider-pill';
    return;
  }
  if (modelInput && !modelInput.value) {
    modelInput.placeholder = info.model ? `Default: ${info.model}` : 'Model name (optional)';
  }
  if (info.available) {
    statusEl.textContent = provider === 'local' ? `Ollama ready (${info.model})` : `API key ready (${info.model})`;
    statusEl.className = 'ai-provider-pill ready';
  } else {
    statusEl.textContent = provider === 'local'
      ? 'Ollama not detected locally'
      : 'API key not set in .env';
    statusEl.className = 'ai-provider-pill warn';
  }
}

export function onVisionProviderChange() {
  const modelInput = document.getElementById('rapid-vision-model');
  if (modelInput) modelInput.value = '';
  updateVisionStatusUI();
}

async function loadVisionProviderStatus() {
  const statusEl = document.getElementById('rapid-vision-status');
  if (statusEl) {
    statusEl.textContent = 'Checking models...';
    statusEl.className = 'ai-provider-pill';
  }
  try {
    rapidVisionMeta = await apiGet('/api/v1/photos/vision-providers');
    const select = document.getElementById('rapid-vision-provider');
    if (select && rapidVisionMeta?.default_provider) {
      select.value = rapidVisionMeta.default_provider;
    }
  } catch {
    rapidVisionMeta = null;
  }
  updateVisionStatusUI();
}

function renderRapidAiResults(items, message) {
  const box = document.getElementById('rapid-ai-results');
  if (!box) return;
  rapidDetectedItems = items || [];

  if (!rapidDetectedItems.length) {
    box.style.display = 'block';
    box.innerHTML = `<p class="ai-results-msg">${escapeHtml(message || 'No items detected. Enter items manually below.')}</p>`;
    return;
  }

  box.style.display = 'block';
  box.innerHTML = `
    <div class="ai-results-header">
      <span>${escapeHtml(message || `Detected ${rapidDetectedItems.length} items`)}</span>
      <div style="display: flex; gap: 0.4rem; flex-wrap: wrap;">
        <button type="button" class="btn btn-secondary btn-sm" onclick="applyDetectedItemsToText()">Copy to text</button>
        <button type="button" class="btn btn-primary btn-sm" id="rapid-ai-save-direct-btn" onclick="saveDetectedItemsDirect()">Save selected to bin</button>
      </div>
    </div>
    <div class="ai-results-list">
      ${rapidDetectedItems.map((it, idx) => `
        <label class="ai-detected-row" style="align-items: flex-start;">
          <input type="checkbox" class="ai-detected-chk" data-idx="${idx}" checked style="margin-top: 0.2rem;">
          <div style="flex: 1; min-width: 0;">
            <div style="display: flex; justify-content: space-between; gap: 0.5rem;">
              <span class="ai-detected-name">${escapeHtml(it.name)}</span>
              <span class="ai-detected-meta">×${Number(it.estimated_qty || 1)}${it.category ? ` · ${escapeHtml(it.category)}` : ''}</span>
            </div>
            ${it.brief_specs ? `<div class="ai-detected-specs">${escapeHtml(it.brief_specs)}</div>` : ''}
          </div>
        </label>
      `).join('')}
    </div>
  `;
}

export async function saveDetectedItemsDirect() {
  const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10);
  if (!locId) {
    showToast('Please choose a bin or drawer first.', 'error');
    return;
  }
  const checks = Array.from(document.querySelectorAll('.ai-detected-chk:checked'));
  const selected = checks.map(chk => rapidDetectedItems[Number(chk.dataset.idx)]).filter(Boolean);
  if (!selected.length) {
    showToast('Select at least one detected item.', 'error');
    return;
  }

  const fallbackCat = document.getElementById('rapid-category-input')?.value.trim() || null;
  const loc = state.locationMap.get(locId);
  const photoUrl = rapidScannedPhotoUrl || (loc?.photos?.[0]?.photo_path) || loc?.container_photo || null;

  return withSubmitLock('rapid-ai-save-direct-btn', 'Saving...', async () => {
    try {
      for (const it of selected) {
        await apiPost(
          '/api/v1/items',
          {
            name: it.name,
            category: it.category || fallbackCat,
            quantity: Math.max(1, Number(it.estimated_qty || 1)),
            description: it.brief_specs || null,
            item_photo: photoUrl,
            location_id: locId,
          },
          'Could not save detected item.'
        );
      }
      showToast(`Saved ${pluralize(selected.length, 'item')} with specs & photo!`);
      closeModal('drawer-rapid-entry-modal', true);
      await loadAllData();
    } catch (err) {
      showToast(err.message || 'Could not save items.', 'error');
    }
  });
}

export function applyDetectedItemsToText() {
  const checks = Array.from(document.querySelectorAll('.ai-detected-chk:checked'));
  const lines = [];
  for (const chk of checks) {
    const idx = Number(chk.dataset.idx);
    const it = rapidDetectedItems[idx];
    if (!it) continue;
    const qty = Math.max(1, Number(it.estimated_qty || 1));
    lines.push(qty > 1 ? `${qty}x ${it.name}` : it.name);
  }
  if (!lines.length) {
    showToast('Select at least one detected item.', 'error');
    return;
  }
  const textarea = document.getElementById('rapid-items-text');
  if (textarea) {
    const existing = textarea.value.trim();
    textarea.value = existing ? `${existing}\n${lines.join('\n')}` : lines.join('\n');
  }
  const catInput = document.getElementById('rapid-category-input');
  if (catInput && !catInput.value.trim()) {
    const firstCat = rapidDetectedItems.find(it => it.category)?.category;
    if (firstCat) catInput.value = firstCat;
  }
  showToast(`Added ${pluralize(lines.length, 'item')} to list — click Save items to store.`);
}

export async function openRapidEntryModal(defaultLocationId = null, options = {}) {
  populateLocationDropdowns();
  rapidSelectedFiles = [];
  rapidRemotePhotoPath = null;
  rapidPhotoId = options.photoId || null;
  rapidScannedPhotoUrl = options.photoUrl ? sanitizePhotoUrl(options.photoUrl) : null;
  rapidDetectedItems = [];
  if (rapidPreviewUrl) {
    URL.revokeObjectURL(rapidPreviewUrl);
    rapidPreviewUrl = null;
  }

  const locSelect = document.getElementById('rapid-location-select');
  if (locSelect && defaultLocationId) {
    locSelect.value = String(defaultLocationId);
  }

  const fileInput = document.getElementById('rapid-photo-input');
  if (fileInput) fileInput.value = '';

  const resultsBox = document.getElementById('rapid-ai-results');
  if (resultsBox) {
    resultsBox.style.display = 'none';
    resultsBox.innerHTML = '';
  }

  const safeExistingUrl = options.photoUrl ? sanitizePhotoUrl(options.photoUrl) : '';
  if (safeExistingUrl) {
    document.getElementById('rapid-photo-filename').textContent = 'Using selected drawer photo';
    document.getElementById('rapid-photo-preview').src = safeExistingUrl;
    document.getElementById('rapid-photo-preview-wrap').style.display = 'block';
  } else {
    document.getElementById('rapid-photo-filename').textContent = 'No photo selected';
    document.getElementById('rapid-photo-preview-wrap').style.display = 'none';
  }

  document.getElementById('rapid-items-text').value = '';
  document.getElementById('rapid-category-input').value = '';
  document.getElementById('drawer-rapid-entry-modal')?.classList.add('open');

  await loadVisionProviderStatus();
  if (options.autoScan && (rapidPhotoId || safeExistingUrl)) {
    await runRapidAiScan();
  }
}

export function triggerRapidPhotoInput() {
  const input = document.getElementById('rapid-photo-input');
  if (input) input.click();
}

export function startRapidPhoneCapture() {
  const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10);
  if (!locId) {
    showToast('Please choose a bin or drawer first.', 'error');
    return;
  }
  const loc = state.locationMap.get(locId);
  openLocationPhoneCaptureModal({
    locationId: locId,
    locationName: loc ? loc.name : 'Drawer',
    onDone: async ({ photos, shouldScan }) => {
      await loadAllData();
      if (photos && photos.length > 0) {
        const latest = photos[photos.length - 1];
        const safeUrl = sanitizePhotoUrl(latest.original_path || '');
        if (safeUrl) {
          rapidRemotePhotoPath = safeUrl;
          rapidSelectedFiles = [];
          rapidPhotoId = null;
          document.getElementById('rapid-photo-filename').textContent = `${photos.length} photo(s) from phone`;
          document.getElementById('rapid-photo-preview').src = safeUrl;
          document.getElementById('rapid-photo-preview-wrap').style.display = 'block';
        }
        if (shouldScan) {
          const updatedLoc = state.locationMap.get(locId);
          const latestSaved = updatedLoc?.photos?.[0];
          if (latestSaved) rapidPhotoId = latestSaved.id;
          await runRapidAiScan();
        }
      }
    },
  });
}

export function handleRapidPhotoSelected(e) {
  const files = Array.from(e.target.files || []).filter(Boolean);
  if (!files.length) return;
  rapidSelectedFiles = files;
  rapidRemotePhotoPath = null;
  rapidPhotoId = null;
  if (rapidPreviewUrl) URL.revokeObjectURL(rapidPreviewUrl);
  rapidPreviewUrl = URL.createObjectURL(files[0]);
  document.getElementById('rapid-photo-filename').textContent = files.length === 1
    ? files[0].name
    : `${files.length} photos selected`;
  document.getElementById('rapid-photo-preview').src = rapidPreviewUrl;
  document.getElementById('rapid-photo-preview-wrap').style.display = 'block';
}

export async function runRapidAiScan() {
  const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10);
  if (!locId) {
    showToast('Please choose a bin or drawer first.', 'error');
    return;
  }

  const provider = document.getElementById('rapid-vision-provider')?.value || 'local';
  const model = document.getElementById('rapid-vision-model')?.value.trim() || '';

  return withSubmitLock('rapid-ai-scan-btn', 'Scanning...', async () => {
    try {
      const fd = new FormData();
      fd.append('provider', provider);
      if (model) fd.append('model', model);

      if (rapidSelectedFiles.length > 0) {
        const compressed = await compressImageFile(rapidSelectedFiles[0]);
        fd.append('file', compressed);
      } else if (rapidPhotoId) {
        fd.append('photo_id', String(rapidPhotoId));
      }

      const res = await fetch(`/api/v1/locations/${locId}/analyze-photo`, {
        method: 'POST',
        body: fd,
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'AI scan failed.');
      }
      if (rapidSelectedFiles.length > 0) {
        rapidSelectedFiles.shift();
        await loadAllData();
        const updatedLoc = state.locationMap.get(locId);
        if (updatedLoc?.photos?.[0]) {
          rapidPhotoId = updatedLoc.photos[0].id;
          rapidScannedPhotoUrl = updatedLoc.photos[0].photo_path;
        }
      }
      renderRapidAiResults(data.items || [], data.message);
      document.getElementById('rapid-ai-results')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
      if (data.status !== 'success') {
        showToast(data.message || 'AI scan did not return items.', 'error');
      } else if (!data.items || data.items.length === 0) {
        showToast('AI scan finished, but no items were detected in this photo.', 'info');
      } else {
        showToast(`AI detected ${pluralize(data.items.length, 'item')} — review and click "Add selected to list".`);
      }
    } catch (err) {
      showToast(err.message || 'AI scan failed.', 'error');
    }
  });
}

export async function submitRapidEntry() {
  const locId = parseInt(document.getElementById('rapid-location-select').value, 10);
  if (!locId) {
    showToast('Please choose where these items go first.', 'error');
    return;
  }

  const text = document.getElementById('rapid-items-text').value.trim();
  const category = document.getElementById('rapid-category-input').value.trim() || null;

  if (!text && !rapidSelectedFiles.length && !rapidRemotePhotoPath && !rapidPhotoId) {
    showToast('Take a photo or list at least one item.', 'error');
    return;
  }

  return withSubmitLock('rapid-submit-btn', 'Saving...', async () => {
    try {
      if (rapidSelectedFiles.length > 0) {
        for (const file of rapidSelectedFiles) {
          const compressed = await compressImageFile(file);
          const fd = new FormData();
          fd.append('file', compressed);
          const upRes = await fetch(`/api/v1/locations/${locId}/photos`, { method: 'POST', body: fd });
          if (!upRes.ok) {
            const errBody = await upRes.json().catch(() => ({}));
            throw new Error(errBody.detail || 'Could not save drawer photo.');
          }
        }
      }

      if (text) {
        const created = await apiPost(
          '/api/v1/items/bulk',
          { location_id: locId, raw_lines: text, category },
          'Could not save items.'
        );
        showToast(`Added ${pluralize(created.length, 'item')}!`);
      } else {
        showToast('Saved photo(s)!');
      }

      closeModal('drawer-rapid-entry-modal', true);
      await loadAllData();
    } catch (err) {
      showToast(err.message, 'error');
    }
  });
}

// ============================================================================
// 5. QUICK PHOTO UPLOAD MODAL
// ============================================================================
export function openQuickPhotoModal(target) {
  state.quickPhotoTarget = target;
  document.getElementById('quick-photo-title').textContent = target.title || 'Add photo';
  const input = document.getElementById('quick-photo-input');
  if (input) input.value = '';
  document.getElementById('quick-photo-input-label').textContent = 'Tap to take or choose photo';
  document.getElementById('quick-photo-modal')?.classList.add('open');
}

export function handleQuickPhotoSelected(e) {
  const file = e.target.files && e.target.files[0];
  document.getElementById('quick-photo-input-label').textContent = file
    ? file.name
    : 'Tap to take or choose photo';
}

export async function attachQuickPhotoPath(uploadedPath) {
  const target = state.quickPhotoTarget;
  if (!target || !uploadedPath) return;
  if (target.kind === 'item') {
    await apiPatch(`/api/v1/items/${target.id}`, { item_photo: uploadedPath }, 'Could not attach photo.');
  } else {
    const body = target.kind === 'spot' ? { context_photo: uploadedPath } : { container_photo: uploadedPath };
    await apiPatch(`/api/v1/locations/${target.id}`, body, 'Could not attach photo.');
  }
  closeModal('quick-photo-modal');
  showToast('Photo saved!');
  await loadAllData();
  if (state.activeDrawerItem && target.kind === 'item') {
    openItemDrawer(state.activeDrawerItem.id);
  }
}

export async function submitQuickPhoto() {
  const file = document.getElementById('quick-photo-input')?.files?.[0];
  if (!file || !state.quickPhotoTarget) {
    showToast('Please select a photo first.', 'error');
    return;
  }

  return withSubmitLock('quick-photo-save-btn', 'Saving...', async () => {
    try {
      const uploadedPath = await uploadPhotoAndGetPath(file);
      await attachQuickPhotoPath(uploadedPath);
    } catch (err) {
      showToast(err.message, 'error');
    }
  });
}

// Register desktop QR camera handoff for modal photo inputs
registerDesktopPhotoHandler('item-photo-input', {
  label: 'Item photo',
  onPhotoReceived: (photo) => {
    const input = document.getElementById('item-photo-input');
    if (input) {
      input.value = '';
      input.dataset.remotePhotoPath = photo.original_path;
    }
    const label = document.getElementById('item-photo-input-label');
    if (label) label.textContent = 'Photo from phone ready';
    showToast('Photo received from phone!');
  },
});

registerDesktopPhotoHandler('storage-bin-photo', {
  label: 'Inside photo',
  onPhotoReceived: (photo) => {
    const input = document.getElementById('storage-bin-photo');
    if (input) {
      input.value = '';
      input.dataset.remotePhotoPath = photo.original_path;
    }
    const label = document.getElementById('storage-bin-photo-label');
    if (label) label.textContent = 'Photo from phone ready';
    showToast('Photo received from phone!');
  },
});

registerDesktopPhotoHandler('storage-spot-photo', {
  label: 'Where it sits',
  onPhotoReceived: (photo) => {
    const input = document.getElementById('storage-spot-photo');
    if (input) {
      input.value = '';
      input.dataset.remotePhotoPath = photo.original_path;
    }
    const label = document.getElementById('storage-spot-photo-label');
    if (label) label.textContent = 'Photo from phone ready';
    showToast('Photo received from phone!');
  },
});

registerDesktopPhotoHandler('rapid-photo-input', {
  label: 'Scan with Camera',
  onPhotoReceived: (photo) => {
    rapidSelectedFile = null;
    rapidRemotePhotoPath = photo.original_path;
    if (rapidPreviewUrl) {
      URL.revokeObjectURL(rapidPreviewUrl);
      rapidPreviewUrl = null;
    }
    const safeUrl = sanitizePhotoUrl(photo.thumbnail_path || photo.original_path);
    document.getElementById('rapid-photo-filename').textContent = 'Photo from phone';
    if (safeUrl) {
      document.getElementById('rapid-photo-preview').src = safeUrl;
      document.getElementById('rapid-photo-preview-wrap').style.display = 'block';
    }
    showToast('Photo received from phone!');
  },
});

registerDesktopPhotoHandler('quick-photo-input', {
  getLabel: () => state.quickPhotoTarget?.title || 'Add photo',
  onPhotoReceived: async (photo) => {
    try {
      await attachQuickPhotoPath(photo.original_path);
    } catch (err) {
      showToast(err.message || 'Could not attach photo.', 'error');
    }
  },
});

// ============================================================================
// 6. MORE MODAL, SINGLE QR & BULK QR PRINT
// ============================================================================
let qrPrintTargetIds = [];
let detectedNetworkBaseUrl = '';

export async function fetchNetworkInfo() {
  if (detectedNetworkBaseUrl) return detectedNetworkBaseUrl;
  try {
    const data = await apiGet('/api/v1/locations/network-info');
    detectedNetworkBaseUrl = data.base_url || `http://${data.lan_ip}:${data.port}`;
    return detectedNetworkBaseUrl;
  } catch {
    return window.location.origin;
  }
}

export async function openMoreModal(onThemeSync) {
  updateNavStateAndCounts();
  if (typeof onThemeSync === 'function') {
    onThemeSync();
  } else if (typeof window.syncThemeButtons === 'function') {
    window.syncThemeButtons();
  }
  document.getElementById('more-modal')?.classList.add('open');
  const lanUrl = await fetchNetworkInfo();
  const lanEl = document.getElementById('more-lan-url');
  if (lanEl) lanEl.textContent = lanUrl;
}

export async function openPrintQrModal(locationId) {
  const loc = state.locationMap.get(locationId);
  if (!loc) return;

  qrPrintTargetIds = [locationId];
  document.getElementById('print-qr-modal-title').textContent = 'Print QR Label';

  const baseInput = document.getElementById('print-qr-base-input');
  if (baseInput && !baseInput.value) {
    baseInput.value = await fetchNetworkInfo();
  }

  renderQrPrintSheet();
  document.getElementById('print-qr-modal')?.classList.add('open');
}

export async function openBulkQrPrintModal() {
  closeModal('more-modal');
  qrPrintTargetIds = state.containers.map(c => c.id);
  document.getElementById('print-qr-modal-title').textContent = `Bulk QR Print (${qrPrintTargetIds.length} Containers)`;

  const baseInput = document.getElementById('print-qr-base-input');
  if (baseInput && !baseInput.value) {
    baseInput.value = await fetchNetworkInfo();
  }

  renderQrPrintSheet();
  document.getElementById('print-qr-modal')?.classList.add('open');
}

export function renderQrPrintSheet() {
  const container = document.getElementById('print-qr-sheet-body');
  if (!container) return;

  const baseInput = document.getElementById('print-qr-base-input');
  const baseUrl = (baseInput?.value?.trim() || window.location.origin).replace(/\/+$/, '');

  if (qrPrintTargetIds.length === 0) {
    container.innerHTML = `<p style="color: var(--text-secondary); text-align: center;">No containers to print yet.</p>`;
    return;
  }

  container.innerHTML = `
    <div class="qr-badges-grid">
      ${qrPrintTargetIds.map(id => {
        const loc = state.locationMap.get(id);
        if (!loc) return '';
        const safeLocId = Number(loc.id);
        const ltype = getLocType(loc);
        const route =
          ltype === 'room' || ltype === 'site'
            ? `/#/room/${safeLocId}`
            : ltype === 'container'
            ? `/#/unit/${safeLocId}`
            : `/#/drawer/${safeLocId}`;
        const deepLink = `${baseUrl}${route}`;
        return `
          <div class="printable-label-badge">
            <div class="label-header">
              <span>WHEREISMYSTUFF</span>
              <span>#${safeLocId}</span>
            </div>
            <div class="label-body">
              <div class="label-qr-wrap">
                <img class="label-qr-code" src="/api/v1/locations/${safeLocId}/qr?base_url=${encodeURIComponent(baseUrl)}" alt="QR Code">
              </div>
              <div style="min-width: 0; flex: 1;">
                <div class="label-title">${escapeHtml(loc.name)}</div>
                <div class="label-path">${escapeHtml(formatHumanPath(loc.full_path))}</div>
              </div>
            </div>
            <div class="label-footer">${escapeHtml(deepLink)}</div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

export function handlePrintQrBaseChange() {
  renderQrPrintSheet();
}

export async function resetPrintQrBaseUrl() {
  detectedNetworkBaseUrl = '';
  const baseInput = document.getElementById('print-qr-base-input');
  if (baseInput) baseInput.value = await fetchNetworkInfo();
  renderQrPrintSheet();
}

export function triggerBrowserPrint() {
  window.print();
}

