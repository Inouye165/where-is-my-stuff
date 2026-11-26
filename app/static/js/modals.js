/**
 * Modals, Unified '+' Sheet, Photo Flows & Printable QR Labels
 */
import { apiDelete, apiGet, apiPatch, apiPost, apiUpload } from './api.js';
import { createSubBoxFromDetectedRow } from './box-actions.js';
import { findFirstOpenGridCoord, inferUnitChildSlotType } from './compartment-actions.js';
import { closeModal, escapeHtml, pluralize, registerModalForceCloseHook, sanitizePhotoUrl, showToast, withSubmitLock } from './dom.js';
import { openInlineGridDesigner } from './grid-designer.js';
import { openDesktopFilePickerDirect, openLocationPhoneCaptureModal, requestPhotoForInput, registerDesktopPhotoHandler } from './phone-capture.js';
import { compressImageFile, uploadPhotoAndGetPath } from './photo-compress.js';
import { navToDrawer, navToHome, navToRoom, navToStorageUnit } from './router.js';
import {
  formatHumanPath,
  getLocType,
  getRoomEmoji,
  isUnknownPlaceholder,
  loadAllData,
  populateLocationDropdowns,
  populateStorageParentDropdown,
  saveJsonStorage,
  state,
  updateNavStateAndCounts
} from './state.js';
import { markModalClean, showScanAbortPrompt } from './unsaved-guard.js';
import { openUnitSetupWizard } from './unit-setup-wizard.js';
import { ensureVisionModelReady } from './vision-guard.js';
import { ensureRoomExists, expandSubBox } from './views.js';

const AI_SCAN_TIMEOUT_MS = 130000;

// ============================================================================
// 1. UNIFIED '+' ACTION SHEET
// ============================================================================
export function openAddSheet() {
  syncAddSheetContext();
  document.getElementById('add-action-sheet')?.classList.add('open');
}

function getAddSheetContextLocation() {
  const locId = state.currentView === 'item'
    ? (state.items.find(i => i.id === state.currentLocationId)?.location_id || null)
    : (state.currentLocationId || null);
  const loc = locId ? state.locationMap.get(locId) : null;
  return loc && !isUnknownPlaceholder(loc) ? loc : null;
}

// Context-aware labels so the sheet says exactly where a new thing will land.
function syncAddSheetContext() {
  const loc = getAddSheetContextLocation();
  const itemDesc = document.getElementById('add-sheet-item-desc');
  const placeTitle = document.getElementById('add-sheet-place-title');
  const placeDesc = document.getElementById('add-sheet-place-desc');
  const locType = loc ? getLocType(loc) : null;

  if (itemDesc) {
    itemDesc.textContent = loc
      ? `Log a tool, cable, or component in ${loc.name}.`
      : 'Quickly log an individual tool, cable, household item, or component.';
  }
  if (!placeTitle || !placeDesc) return;
  if (loc && (locType === 'room' || locType === 'site')) {
    placeTitle.textContent = `New Container in ${loc.name}`;
    placeDesc.textContent = 'Set up a bin, shelf, or multi-drawer organizer here.';
  } else if (loc) {
    placeTitle.textContent = `New Drawer or Box in ${loc.name}`;
    placeDesc.textContent = 'Add a drawer, bin, or box inside this container.';
  } else {
    placeTitle.textContent = 'New Space or Container';
    placeDesc.textContent = 'Create a room, storage area, bin, or multi-drawer organizer.';
  }
}

export function chooseAddAction(choice) {
  closeModal('add-action-sheet');
  const ctxLocId = state.currentView === 'item'
    ? (state.items.find(i => i.id === state.currentLocationId)?.location_id || null)
    : (state.currentLocationId || null);

  if (choice === 'snap') {
    openRapidEntryModal(ctxLocId);
  } else if (choice === 'item' || choice === 'manual') {
    openAddItemModal(ctxLocId, null);
  } else if (choice === 'container' || choice === 'location') {
    const parentId = state.currentView === 'room' && ctxLocId ? ctxLocId : null;
    openUnitSetupWizard(parentId);
  } else if (choice === 'space') {
    openAddStorageModal(null, 'room', { lockType: true });
  } else if (choice === 'place') {
    const ctxLoc = getAddSheetContextLocation();
    const ctxType = ctxLoc ? getLocType(ctxLoc) : null;
    if (ctxLoc && (ctxType === 'room' || ctxType === 'site')) {
      openUnitSetupWizard(ctxLoc.id);
    } else if (ctxLoc) {
      openAddStorageModal(ctxLoc.id, 'drawer');
    } else {
      openAddStorageModal(null, 'room');
    }
  }
}

// ============================================================================
// 2. ADD / EDIT ITEM MODAL
// ============================================================================
export function openAddItemModal(defaultLocationId = null, existingItem = null, options = {}) {
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
  itemLocationPickerExpanded = false;
  syncItemLocationPill();

  const moreDetails = document.getElementById('item-more-details');
  if (moreDetails) moreDetails.open = Boolean((existingItem && itemHasOptionalDetails(existingItem)) || options.focusId);

  if (title) title.textContent = existingItem ? 'Edit Item' : 'Add Item Manually';
  if (submitBtn) submitBtn.textContent = existingItem ? 'Save changes' : 'Save item';

  markModalClean('add-item-modal');
  modal?.classList.add('open');
  if (options.focusId) {
    setTimeout(() => document.getElementById(options.focusId)?.focus(), 50);
  }
}

let itemLocationPickerExpanded = false;

function itemHasOptionalDetails(item) {
  const unit = (item.unit || '').trim().toLowerCase();
  return Boolean(
    (unit && unit !== 'pcs')
    || item.category
    || item.serial_or_part_number
    || item.tags
    || item.description
  );
}

function syncItemLocationPill() {
  const locSelect = document.getElementById('item-location-select');
  const pillBar = document.getElementById('item-location-pill-bar');
  const pillName = document.getElementById('item-location-pill-name');
  const locField = document.getElementById('item-location-field');
  const locId = parseInt(locSelect?.value, 10) || null;
  const loc = locId ? state.locationMap.get(locId) : null;
  const showPill = Boolean(loc && !itemLocationPickerExpanded);

  if (pillName && loc) pillName.textContent = formatHumanPath(loc.full_path || loc.name);
  if (pillBar) pillBar.style.display = showPill ? 'flex' : 'none';
  if (locField) locField.style.display = showPill ? 'none' : '';
}

export function toggleItemLocationPicker() {
  itemLocationPickerExpanded = true;
  syncItemLocationPill();
  document.getElementById('item-location-select')?.focus();
}

export function handleItemLocationChange() {
  itemLocationPickerExpanded = false;
  syncItemLocationPill();
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
export function openAddStorageModal(parentLocationId = null, kind = 'unit', options = {}) {
  const modal = document.getElementById('add-storage-modal');
  document.getElementById('edit-storage-id').value = '';
  document.getElementById('storage-name').value = '';
  const labelInput = document.getElementById('storage-label');
  if (labelInput) labelInput.value = '';
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
  document.getElementById('storage-inline-room-field').style.display = 'none';
  const moreDetails = document.getElementById('storage-more-details');
  if (moreDetails) moreDetails.open = false;

  storageContextLocked = Boolean(parentLocationId || options.lockType);
  storageParentPickerExpanded = false;
  document.getElementById('storage-type-segmented').style.display = storageContextLocked ? 'none' : 'flex';

  setStorageTypeChoice(kind, parentLocationId);
  selectRoomEmoji('🔧');

  markModalClean('add-storage-modal');
  modal?.classList.add('open');
}

export function openEditStorageModal(locationId) {
  const loc = state.locationMap.get(locationId);
  if (!loc) return;

  const ltype = getLocType(loc);
  const kind = ltype === 'room' || ltype === 'site' ? 'room' : ltype === 'container' ? 'unit' : 'drawer';

  document.getElementById('edit-storage-id').value = String(loc.id);
  for (const [inputId, labelId] of [['storage-bin-photo', 'storage-bin-photo-label'], ['storage-spot-photo', 'storage-spot-photo-label']]) {
    const photoEl = document.getElementById(inputId);
    if (photoEl) {
      photoEl.value = '';
      photoEl.dataset.remotePhotoPath = '';
    }
    const photoLabelEl = document.getElementById(labelId);
    if (photoLabelEl) photoLabelEl.textContent = 'Add photo';
  }
  document.getElementById('storage-type-segmented').style.display = 'none';
  storageContextLocked = true;
  storageParentPickerExpanded = false;
  setStorageTypeChoice(kind, loc.parent_id, loc.id);

  document.getElementById('storage-modal-title').textContent = `Edit ${loc.name}`;
  document.getElementById('storage-name').value = loc.name;
  const labelInput = document.getElementById('storage-label');
  if (labelInput) labelInput.value = loc.label || '';
  document.getElementById('storage-desc').value = loc.description || '';
  document.getElementById('multi-drawer-box').style.display = 'none';
  document.getElementById('storage-delete-btn').style.display = 'inline-flex';
  const moreDetails = document.getElementById('storage-more-details');
  if (moreDetails) moreDetails.open = Boolean(loc.description || (kind !== 'room' && loc.label));

  if (kind === 'room') {
    selectRoomEmoji(getRoomEmoji(loc));
  }

  markModalClean('add-storage-modal');
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

  const labelField = document.getElementById('storage-label-field');
  if (kind === 'room') {
    if (!isEditing) title.textContent = 'Add a space';
    emojiField.style.display = 'flex';
    parentField.style.display = 'flex';
    parentLabel.textContent = 'Inside another space? (Optional)';
    document.getElementById('storage-inline-room-field').style.display = 'none';
    nameLabel.textContent = 'Space name *';
    nameInput.placeholder = 'e.g. Garage, Office, Bedroom, Closet';
    if (labelField) labelField.style.display = 'none';
    multiBox.style.display = 'none';
    binPhotoField.style.display = 'none';
    spotPhotoTitle.textContent = 'Space photo (Optional)';
    populateStorageParentDropdown('room', selectedParentId, excludeId);
  } else if (kind === 'unit') {
    if (!isEditing) title.textContent = 'Add a container';
    emojiField.style.display = 'none';
    parentField.style.display = 'flex';
    parentLabel.textContent = 'Which space is it in?';
    nameLabel.textContent = 'Container name *';
    nameInput.placeholder = 'e.g. Yellow Organizer, Clear Tote, Tool Chest';
    if (labelField) labelField.style.display = 'flex';
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
    if (labelField) labelField.style.display = 'flex';
    multiBox.style.display = 'none';
    binPhotoField.style.display = 'flex';
    spotPhotoTitle.textContent = 'Where it sits';
    populateStorageParentDropdown('drawer', selectedParentId, excludeId);
  }

  placeStorageSpotPhotoField(kind === 'room');
  syncStorageParentPill();
}

let storageContextLocked = false;
let storageParentPickerExpanded = false;

function placeStorageSpotPhotoField(upfront) {
  const spotField = document.getElementById('storage-spot-photo-field');
  const slot = document.getElementById(upfront ? 'storage-primary-photo-slot' : 'storage-spot-photo-slot');
  if (spotField && slot && spotField.parentElement !== slot) {
    slot.appendChild(spotField);
  }
}

export function syncStorageParentPill() {
  const sel = document.getElementById('storage-parent-select');
  const pillBar = document.getElementById('storage-parent-pill-bar');
  const pillName = document.getElementById('storage-parent-pill-name');
  const parentField = document.getElementById('storage-parent-field');
  const rawVal = sel?.value || '';
  const parentId = parseInt(rawVal, 10) || null;
  const parentLoc = parentId ? state.locationMap.get(parentId) : null;
  const topLevelLocked = storageContextLocked && rawVal === '';
  const showPill = Boolean((parentLoc || topLevelLocked) && !storageParentPickerExpanded);

  if (pillName) {
    if (parentLoc) {
      pillName.textContent = formatHumanPath(parentLoc.full_path || parentLoc.name);
    } else {
      pillName.textContent = state.storageTypeChoice === 'room' ? 'Places (top level)' : 'Needs a home';
    }
  }
  if (pillBar) pillBar.style.display = showPill ? 'flex' : 'none';
  if (parentField) parentField.style.display = showPill ? 'none' : 'flex';
}

export function toggleStorageParentPicker() {
  storageParentPickerExpanded = true;
  syncStorageParentPill();
  document.getElementById('storage-parent-select')?.focus();
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
  storageParentPickerExpanded = false;
  syncStorageParentPill();
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
      } else if (rawParent) {
        parentId = parseInt(rawParent, 10);
      }

      const binInput = document.getElementById('storage-bin-photo');
      const spotInput = document.getElementById('storage-spot-photo');
      const binFile = binInput?.files?.[0];
      const spotFile = spotInput?.files?.[0];
      const containerPhotoPath = binInput?.dataset?.remotePhotoPath || (binFile ? await uploadPhotoAndGetPath(binFile) : undefined);
      const contextPhotoPath = spotInput?.dataset?.remotePhotoPath || (spotFile ? await uploadPhotoAndGetPath(spotFile) : undefined);

      const existingLoc = editId ? state.locationMap.get(parseInt(editId, 10)) : null;
      const parentLoc = parentId ? state.locationMap.get(parentId) : null;
      const parentType = parentLoc ? getLocType(parentLoc) : null;
      let locationType = 'bin';
      if (kind === 'room') {
        locationType = 'room';
      } else if (kind === 'unit') {
        locationType = 'container';
      } else if (existingLoc && !['room', 'site', 'container'].includes(getLocType(existingLoc))) {
        locationType = getLocType(existingLoc);
      } else if (parentLoc && !['room', 'site', 'container'].includes(parentType)) {
        locationType = 'box';
      }
      const parentHasGrid = Boolean(parentLoc && Number(parentLoc.grid_rows) > 0 && Number(parentLoc.grid_cols) > 0);
      if (!editId && kind === 'drawer' && parentHasGrid) {
        locationType = inferUnitChildSlotType(parentLoc);
      }
      const storageLabel = (document.getElementById('storage-label')?.value || '').trim() || null;
      const payload = {
        name,
        label: kind === 'room' ? null : storageLabel,
        description,
        location_type: locationType,
        parent_id: parentId
      };
      if (!editId && kind === 'drawer' && parentHasGrid) {
        const openCell = findFirstOpenGridCoord(parentLoc);
        if (openCell) {
          payload.grid_row = openCell.grid_row;
          payload.grid_col = openCell.grid_col;
          payload.grid_row_span = 1;
          payload.grid_col_span = 1;
        }
      }
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
      const parentId = loc?.parent_id || null;
      await apiDelete(`/api/v1/locations/${editId}`, 'Could not delete place.');
      closeModal('add-storage-modal', true);
      showToast('Deleted.');
      await loadAllData();
      const parentNode = parentId ? state.locationMap.get(parentId) : null;
      if (parentNode && !isUnknownPlaceholder(parentNode)) {
        const pType = getLocType(parentNode);
        if (pType === 'room' || pType === 'site') {
          navToRoom(parentNode.id, true);
        } else if (pType === 'container' || state.containers.some(c => c.id === parentNode.id)) {
          navToStorageUnit(parentNode.id, true);
        } else {
          navToDrawer(parentNode.id, true);
        }
      } else {
        navToHome();
      }
    } catch (err) {
      showToast(err.message, 'error');
    }
  });
}

// ============================================================================
// 4. SNAP & BATCH ENTRY MODAL (2-Step Progressive Disclosure)
// ============================================================================
let rapidSelectedFiles = [];
let rapidRemotePhotoPath = null;
let rapidPhotoId = null;
let rapidPhotoIds = [];
let rapidSessionUploadedPhotoIds = [];
let rapidPreviewUrls = [];
let rapidScannedPhotoUrl = null;
let rapidPreviewUrl = null;
let rapidVisionMeta = null;
let rapidDetectedItems = [];
let rapidSaveMode = 'separate';
let rapidUserPickedSaveMode = false;
let rapidAiMessage = '';
let rapidAutoScanNextPick = false;
let rapidManualMode = false;
let rapidLlmMode = false;
let rapidLocationPickerExpanded = false;
let rapidIsScanning = false;
let rapidOpenSeq = 0;
let rapidScanController = null;

function abortRapidAiScan() {
  if (rapidScanController) {
    try { rapidScanController.abort(); } catch {}
    rapidScanController = null;
  }
  rapidIsScanning = false;
  const rapidModal = document.getElementById('drawer-rapid-entry-modal');
  if (rapidModal && rapidModal.dataset) {
    rapidModal.dataset.scanning = 'false';
  }
}

registerModalForceCloseHook('drawer-rapid-entry-modal', abortRapidAiScan);

function renderRapidPhotoPreviews(urls, activeIndex = -1) {
  const wrap = document.getElementById('rapid-photo-preview-wrap');
  if (!wrap) return;
  const cleanUrls = (urls || []).map(u => sanitizePhotoUrl(u) || (String(u || '').startsWith('blob:') ? u : '')).filter(Boolean);
  if (!cleanUrls.length) {
    wrap.style.display = 'none';
    wrap.classList.remove('is-multi');
    wrap.innerHTML = '<img id="rapid-photo-preview" src="" alt="Preview">';
    return;
  }
  wrap.style.display = cleanUrls.length > 1 ? 'flex' : 'block';
  wrap.classList.toggle('is-multi', cleanUrls.length > 1);
  wrap.innerHTML = cleanUrls.map((url, idx) => `
    <img${idx === 0 ? ' id="rapid-photo-preview"' : ''} src="${escapeHtml(url)}" alt="Preview ${idx + 1}" class="${idx === activeIndex ? 'is-scanning' : ''}">
  `).join('');
}

function updateVisionStatusUI() {
  const select = document.getElementById('rapid-vision-provider');
  const statusEl = document.getElementById('rapid-vision-status');
  const modelInput = document.getElementById('rapid-vision-model');
  const quietBtn = document.getElementById('rapid-quiet-ai-btn');
  if (!select || !statusEl) return;

  const provider = select.value || 'local';
  const info = rapidVisionMeta?.providers?.[provider];
  if (!info) {
    statusEl.textContent = 'Ready';
    statusEl.className = 'ai-provider-pill';
    if (quietBtn) quietBtn.textContent = 'AI: Auto';
    return;
  }
  if (modelInput && !modelInput.value) {
    modelInput.placeholder = info.model ? `Default: ${info.model}` : 'Model name (optional)';
  }
  if (info.available) {
    statusEl.textContent = provider === 'local' ? `Ollama ready (${info.model})` : `API key ready (${info.model})`;
    statusEl.className = 'ai-provider-pill ready';
    if (quietBtn) {
      const label = provider === 'local' ? 'Local AI' : (provider === 'gemini' ? 'Gemini' : 'OpenAI');
      quietBtn.textContent = `AI: ${label} ready`;
    }
  } else {
    statusEl.textContent = provider === 'local'
      ? 'Ollama not detected locally'
      : 'API key not set in .env';
    statusEl.className = 'ai-provider-pill warn';
    if (quietBtn) quietBtn.textContent = 'AI: Configure';
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

function normalizeItemTokens(name) {
  const stopWords = new Set(['a', 'an', 'the', 'with', 'and', 'for', 'of', 'in', 'on']);
  const cleaned = String(name || '')
    .toLowerCase()
    .replace(/^\s*\d+\s*x?\s+/i, '')
    .replace(/(\d+(?:\.\d+)?)\s+(uf|nf|pf|mf|f|v|kv|mv|a|ma|ua|w|mw|kw|ohm|kohm|mohm|hz|khz|mhz|ghz|mm|cm|pcs|pc)\b/gi, '$1$2')
    .replace(/[^a-z0-9.\s]/g, ' ')
    .trim();
  const tokens = cleaned
    .split(/\s+/)
    .map(w => w.replace(/^\.+|\.+$/g, ''))
    .filter(w => w && !stopWords.has(w))
    .map(w => (w.length > 3 && w.endsWith('s') && !w.endsWith('ss') ? w.slice(0, -1) : w));
  return Array.from(new Set(tokens));
}

function areLikelyDuplicateItemNames(nameA, nameB) {
  const tokensA = normalizeItemTokens(nameA);
  const tokensB = normalizeItemTokens(nameB);
  if (!tokensA.length || !tokensB.length) return false;
  if (tokensA.length === tokensB.length && tokensA.every((t, i) => t === tokensB[i])) return true;

  const numTokensA = tokensA.filter(t => /\d/.test(t)).sort();
  const numTokensB = tokensB.filter(t => /\d/.test(t)).sort();
  if (numTokensA.length > 0 && numTokensB.length > 0) {
    if (
      numTokensA.length !== numTokensB.length
      || !numTokensA.every((t, i) => t === numTokensB[i])
    ) {
      return false;
    }
    const wordTokensA = tokensA.filter(t => !/\d/.test(t));
    const wordTokensB = tokensB.filter(t => !/\d/.test(t));
    if (!wordTokensA.length || !wordTokensB.length) return true;
    const [smallerW, largerW] = wordTokensA.length <= wordTokensB.length
      ? [wordTokensA, new Set(wordTokensB)]
      : [wordTokensB, new Set(wordTokensA)];
    return smallerW.every(w => largerW.has(w));
  }

  const setA = new Set(tokensA);
  const setB = new Set(tokensB);
  const [smaller, larger] = setA.size <= setB.size ? [setA, setB] : [setB, setA];
  let overlap = 0;
  for (const t of smaller) {
    if (larger.has(t)) overlap += 1;
  }
  if (smaller.size >= 2 && overlap === smaller.size) return true;
  return false;
}

function syncRapidLocationUI() {
  const locSelect = document.getElementById('rapid-location-select');
  const pillBar = document.getElementById('rapid-location-pill-bar');
  const pillName = document.getElementById('rapid-location-pill-name');
  const locField = document.getElementById('rapid-location-field');
  const existingBtn = document.getElementById('rapid-existing-photos-btn');

  const locId = parseInt(locSelect?.value, 10) || null;
  const loc = locId ? state.locationMap.get(locId) : null;

  if (loc && !rapidLocationPickerExpanded) {
    if (pillName) pillName.textContent = formatHumanPath(loc.full_path || loc.name);
    if (pillBar) pillBar.style.display = 'flex';
    if (locField) locField.style.display = 'none';
  } else {
    if (pillBar) pillBar.style.display = 'none';
    if (locField) locField.style.display = '';
  }

  if (existingBtn) {
    const savedPhotos = Array.isArray(loc?.photos) ? loc.photos : [];
    if (savedPhotos.length > 0) {
      existingBtn.style.display = 'inline-flex';
      existingBtn.textContent = savedPhotos.length > 1
        ? `Or scan ${savedPhotos.length} existing photos`
        : 'Or scan existing photo';
    } else {
      existingBtn.style.display = 'none';
    }
  }
}

function syncRapidStageUI() {
  const textVal = (document.getElementById('rapid-items-text')?.value || '').trim();
  const hasPhotos = Boolean(
    rapidPreviewUrls.length > 0
    || rapidSelectedFiles.length > 0
    || rapidRemotePhotoPath
    || rapidPhotoId
  );
  const isReview = Boolean(
    rapidManualMode
    || rapidIsScanning
    || rapidDetectedItems.length > 0
    || rapidAiMessage
    || hasPhotos
    || textVal
  );

  const stageCapture = document.getElementById('rapid-stage-capture');
  const stageLlm = document.getElementById('rapid-stage-llm');
  const stageReview = document.getElementById('rapid-stage-review');
  const showLlmStage = Boolean(rapidLlmMode && !isReview);
  if (stageCapture) stageCapture.style.display = (isReview || showLlmStage) ? 'none' : 'flex';
  if (stageLlm) stageLlm.style.display = showLlmStage ? 'flex' : 'none';
  if (stageReview) stageReview.style.display = isReview ? 'flex' : 'none';

  const titleEl = document.getElementById('rapid-modal-title');
  const subtitleEl = document.getElementById('rapid-modal-subtitle');
  if (titleEl && subtitleEl) {
    if (showLlmStage) {
      titleEl.textContent = 'Import from Gemini / ChatGPT';
      subtitleEl.textContent = 'Copy the prompt, send it with your photo, then paste the JSON reply';
    } else {
      titleEl.textContent = 'Scan with Camera';
      subtitleEl.textContent = 'Snap or upload a photo — AI lists the parts for you';
    }
  }

  const photoStrip = document.getElementById('rapid-photo-strip');
  if (photoStrip) photoStrip.style.display = hasPhotos ? 'flex' : 'none';

  const scanBanner = document.getElementById('rapid-scanning-banner');
  if (scanBanner) scanBanner.style.display = rapidIsScanning ? 'flex' : 'none';

  const rapidModal = document.getElementById('drawer-rapid-entry-modal');
  if (rapidModal && rapidModal.dataset) {
    rapidModal.dataset.scanning = rapidIsScanning ? 'true' : 'false';
  }

  const submitBtn = document.getElementById('rapid-submit-btn');
  if (submitBtn) submitBtn.style.display = isReview ? '' : 'none';

  syncRapidLocationUI();
}

export function toggleRapidLocationPicker() {
  rapidLocationPickerExpanded = true;
  syncRapidLocationUI();
  document.getElementById('rapid-location-select')?.focus();
}

export function handleRapidLocationChange() {
  const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10) || null;
  if (locId) {
    rapidLocationPickerExpanded = false;
  }
  syncRapidLocationUI();
  if (rapidDetectedItems.length > 0) {
    renderRapidAiResults(rapidDetectedItems, rapidAiMessage, locId);
  }
}

export function openRapidManualMode() {
  rapidLlmMode = false;
  rapidManualMode = true;
  syncRapidStageUI();
  document.getElementById('rapid-add-part-input')?.focus();
}

export function openRapidLlmMode() {
  rapidLlmMode = true;
  rapidManualMode = false;
  refreshLlmPromptPreview();
  syncRapidStageUI();
  document.getElementById('rapid-llm-json-input')?.focus();
}

export function closeRapidLlmMode() {
  rapidLlmMode = false;
  syncRapidStageUI();
}

export async function scanExistingLocationPhotos() {
  const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10) || null;
  if (!locId) {
    showToast('Please choose a bin or drawer first.', 'error');
    return;
  }
  const loc = state.locationMap.get(locId);
  const savedPhotos = Array.isArray(loc?.photos) ? loc.photos : [];
  if (!savedPhotos.length) {
    showToast('No saved photos on this location yet.', 'info');
    return;
  }
  revokeRapidBlobUrls();
  rapidPhotoIds = savedPhotos.map(p => Number(p.id)).filter(Boolean);
  rapidPhotoId = rapidPhotoIds[0] || null;
  rapidPreviewUrls = savedPhotos.map(p => sanitizePhotoUrl(p.photo_path || '')).filter(Boolean);
  rapidScannedPhotoUrl = rapidPreviewUrls[0] || null;
  const filenameEl = document.getElementById('rapid-photo-filename');
  if (filenameEl) {
    filenameEl.textContent = rapidPhotoIds.length > 1
      ? `Using ${rapidPhotoIds.length} photos`
      : 'Using saved photo';
  }
  renderRapidPhotoPreviews(rapidPreviewUrls);
  syncRapidStageUI();
  await runRapidAiScan();
}

export function toggleRapidAdvancedOptions() {
  const detailsEl = document.getElementById('rapid-advanced-details');
  if (!detailsEl) return;
  detailsEl.open = !detailsEl.open;
  if (detailsEl.open) {
    detailsEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }
}

function syncRapidSubmitButtonLabel() {
  const btn = document.getElementById('rapid-submit-btn');
  const includedCount = rapidDetectedItems.filter(
    it => it && it.included !== false && String(it.name || '').trim()
  ).length;

  if (includedCount < 2 && !rapidUserPickedSaveMode && rapidSaveMode !== 'separate') {
    setRapidSaveMode('separate', false);
    return;
  }

  const saveModeBox = document.getElementById('rapid-save-mode-box');
  if (saveModeBox) {
    saveModeBox.style.display = (rapidIsScanning || includedCount >= 2 || rapidSaveMode === 'box' || rapidSaveMode === 'assortment') ? '' : 'none';
  }
  const sepBtn = document.getElementById('rapid-mode-separate');
  if (sepBtn) {
    sepBtn.textContent = includedCount >= 2 ? `Individual (${includedCount} items)` : 'Individual items';
  }

  if (btn) {
    const hasOnlyPhoto = includedCount === 0
      && !(document.getElementById('rapid-items-text')?.value || '').trim()
      && (rapidSelectedFiles.length > 0 || Boolean(rapidRemotePhotoPath) || Boolean(rapidPhotoId));

    if (rapidSaveMode === 'box') {
      btn.textContent = includedCount > 0 ? `Save Box / Kit (${includedCount} items)` : 'Save Box / Kit';
    } else if (rapidSaveMode === 'assortment') {
      btn.textContent = includedCount > 0 ? `Save Checklist (${includedCount} items)` : 'Save Checklist';
    } else if (includedCount > 0) {
      btn.textContent = `Save ${pluralize(includedCount, 'item')}`;
    } else if (hasOnlyPhoto) {
      btn.textContent = 'Save photo';
    } else {
      btn.textContent = 'Save items';
    }
  }
  syncRapidStageUI();
}

export function setRapidSaveMode(mode, explicit = true) {
  rapidSaveMode = (mode === 'box' || mode === 'assortment') ? mode : 'separate';
  if (explicit) rapidUserPickedSaveMode = true;

  const modalTitle = document.getElementById('rapid-modal-title');
  const modalSub = document.getElementById('rapid-modal-subtitle');
  if (modalTitle) {
    modalTitle.textContent = rapidSaveMode === 'box' ? 'Scan a Box / Kit' : 'Scan with Camera';
  }
  if (modalSub) {
    modalSub.textContent = rapidSaveMode === 'box'
      ? 'Saves your photo(s) as 1 Box / Kit & crops each item inside'
      : 'Snap or upload a photo — AI lists the items for you';
  }

  const sepBtn = document.getElementById('rapid-mode-separate');
  const boxBtn = document.getElementById('rapid-mode-box');
  const kitBtn = document.getElementById('rapid-mode-assortment');
  const fieldsWrap = document.getElementById('rapid-assortment-fields');
  const nameLabel = document.getElementById('rapid-assortment-name-label');
  const hintEl = document.getElementById('rapid-save-mode-hint');

  if (sepBtn) sepBtn.classList.toggle('active', rapidSaveMode === 'separate');
  if (boxBtn) boxBtn.classList.toggle('active', rapidSaveMode === 'box');
  if (kitBtn) kitBtn.classList.toggle('active', rapidSaveMode === 'assortment');
  if (fieldsWrap) fieldsWrap.style.display = (rapidSaveMode === 'box' || rapidSaveMode === 'assortment') ? 'flex' : 'none';
  const labelWrap = document.getElementById('rapid-assortment-label-wrap');
  if (labelWrap) labelWrap.style.display = rapidSaveMode === 'box' ? 'flex' : 'none';
  if (nameLabel) {
    nameLabel.textContent = rapidSaveMode === 'box' ? 'Box / container / kit name' : 'Checklist item name';
  }
  if (hintEl) {
    if (rapidSaveMode === 'box') {
      hintEl.textContent = 'Saves all scanned photo(s) on 1 Box / Kit in this location, and saves each checked item inside with its cropped photo.';
    } else if (rapidSaveMode === 'assortment') {
      hintEl.textContent = 'Saves 1 grouped checklist item (e.g. Assorted Tools or Resistor Kit) with a searchable, editable contents list.';
    } else {
      hintEl.textContent = 'Saves each checked item individually in this location.';
    }
  }
  syncRapidSubmitButtonLabel();
}

export function parseJsonInventoryInput(rawText) {
  let cleaned = String(rawText || '').trim();
  if (!cleaned) return null;
  const fenceMatch = cleaned.match(/```(?:json)?\s*([\s\S]*?)```/i);
  if (fenceMatch) cleaned = fenceMatch[1].trim();
  if (!cleaned.startsWith('{') && !cleaned.startsWith('[')) return null;

  let parsed;
  try {
    parsed = JSON.parse(cleaned);
  } catch {
    return null;
  }

  let groupName = null;
  let groupCategory = null;
  let rawItems = [];
  if (Array.isArray(parsed)) {
    rawItems = parsed;
  } else if (parsed && typeof parsed === 'object') {
    groupName = String(parsed.suggested_group_name || parsed.group_name || parsed.box_name || '').trim() || null;
    groupCategory = String(parsed.suggested_category || parsed.category || '').trim() || null;
    if (Array.isArray(parsed.items)) rawItems = parsed.items;
  }

  const items = [];
  for (const row of rawItems) {
    if (!row || typeof row !== 'object') continue;
    const name = String(row.name || '').trim();
    if (!name) continue;
    const rawTags = Array.isArray(row.tags)
      ? row.tags.map(t => String(t).trim()).filter(Boolean)
      : (typeof row.tags === 'string' ? row.tags.split(',').map(t => t.trim()).filter(Boolean) : []);
    const cond = String(row.condition || '').trim().toLowerCase();
    const validCond = ['new', 'good', 'worn', 'broken', 'missing_parts'].includes(cond) ? cond : null;
    items.push({
      name: name.slice(0, 160),
      part_number: String(row.part_number || row.serial_or_part_number || '').trim().slice(0, 120) || null,
      category: String(row.category || groupCategory || '').trim().slice(0, 80) || null,
      estimated_qty: Math.max(1, parseInt(row.estimated_qty ?? row.quantity ?? 1, 10) || 1),
      brief_specs: String(row.brief_specs || row.description || '').trim().slice(0, 2000) || null,
      condition: validCond,
      tags: rawTags.length ? rawTags : null,
      _manual: true,
    });
  }
  if (!items.length) return null;
  return { groupName, groupCategory, items };
}

export function buildLlmInventoryPrompt() {
  const expectedVal = parseInt(document.getElementById('rapid-expected-count')?.value || '', 10);
  const countLine = expectedVal > 0
    ? `- There are ${expectedVal} distinct item types/values here. List all ${expectedVal} without skipping or merging.\n`
    : '';
  return (
`List every distinct item/value/compartment from the attached photo(s) as JSON for my inventory app.

Rules:
- Put ONE entry in "items" for each distinct value or compartment (never collapse a kit into 1 item).
${countLine}- Only include part numbers, values, or specs you can clearly read.
- Reply with ONLY valid JSON matching this schema (all fields optional except "name"):
{
  "suggested_group_name": "Box or kit name (optional)",
  "suggested_category": "Category (e.g. Electrolytic Capacitors)",
  "items": [
    {
      "name": "Item name with value/rating (e.g. 10uF 25V Electrolytic Capacitor)",
      "part_number": "Part or value code (e.g. 10uF 25V, L7824)",
      "category": "Category",
      "estimated_qty": 10,
      "brief_specs": "Size, voltage, package, or notes",
      "condition": "new | good | worn | broken | missing_parts",
      "tags": ["optional", "tags"]
    }
  ]
}`
  );
}

export function refreshLlmPromptPreview() {
  const preview = document.getElementById('rapid-llm-prompt-preview');
  if (preview) preview.value = buildLlmInventoryPrompt();
}

export async function copyLlmInventoryPrompt() {
  const promptText = buildLlmInventoryPrompt();
  refreshLlmPromptPreview();
  try {
    await navigator.clipboard.writeText(promptText);
    showToast('Prompt copied! Paste it + your photo into Gemini or ChatGPT.');
  } catch {
    window.prompt('Copy this LLM prompt:', promptText);
  }
}

export function importLlmJsonToRapidList() {
  const textarea = document.getElementById('rapid-llm-json-input');
  const raw = (textarea?.value || '').trim();
  if (!raw) {
    showToast('Paste the JSON reply from Gemini/ChatGPT first.', 'error');
    textarea?.focus();
    return;
  }
  const jsonResult = parseJsonInventoryInput(raw);
  if (!jsonResult || !jsonResult.items.length) {
    showToast('Could not find valid JSON items. Make sure you pasted the JSON from Gemini/ChatGPT.', 'error');
    textarea?.focus();
    return;
  }

  const assortmentInput = document.getElementById('rapid-assortment-name');
  if (assortmentInput && !assortmentInput.value.trim() && jsonResult.groupName) {
    assortmentInput.value = jsonResult.groupName;
  }
  const catInput = document.getElementById('rapid-category-input');
  if (catInput && !catInput.value.trim() && jsonResult.groupCategory) {
    catInput.value = jsonResult.groupCategory;
  }

  const beforeLen = rapidDetectedItems.length;
  rapidDetectedItems = mergeDetectedItems([...rapidDetectedItems, ...jsonResult.items]);
  const collapsedDupes = Math.max(0, (beforeLen + jsonResult.items.length) - rapidDetectedItems.length);
  if (textarea) textarea.value = '';
  rapidLlmMode = false;
  rapidManualMode = true;

  const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10) || null;
  const targetLoc = locId ? state.locationMap.get(locId) : null;
  const targetIsAlreadyBox = ['box', 'kit'].includes(getLocType(targetLoc));
  if (!rapidUserPickedSaveMode && jsonResult.groupName && rapidDetectedItems.length >= 2 && !targetIsAlreadyBox) {
    setRapidSaveMode('box', false);
  }

  markModalDirty('drawer-rapid-entry-modal');
  const dupNote = collapsedDupes > 0 ? ` (${collapsedDupes} duplicate${collapsedDupes === 1 ? '' : 's'} merged)` : '';
  renderRapidAiResults(
    rapidDetectedItems,
    `Loaded ${pluralize(rapidDetectedItems.length, 'item')}${dupNote}`,
    locId
  );
  syncRapidStageUI();
  showToast(`Loaded ${pluralize(jsonResult.items.length, 'item')} from JSON!`);
}

function parseManualPartEntries(rawText) {
  const jsonResult = parseJsonInventoryInput(rawText);
  if (jsonResult) {
    const assortmentInput = document.getElementById('rapid-assortment-name');
    if (assortmentInput && !assortmentInput.value.trim() && jsonResult.groupName) {
      assortmentInput.value = jsonResult.groupName;
    }
    const catInput = document.getElementById('rapid-category-input');
    if (catInput && !catInput.value.trim() && jsonResult.groupCategory) {
      catInput.value = jsonResult.groupCategory;
    }
    return jsonResult.items;
  }

  const raw = String(rawText || '').trim();
  if (!raw) return [];
  const chunks = raw.includes('\n')
    ? raw.split(/\r?\n/)
    : raw.split(',');

  const parsed = [];
  for (const chunk of chunks) {
    let line = chunk.trim().replace(/^(?:\d+[\.\)]\s*|[-*•]\s*)/, '').trim();
    if (!line) continue;

    let qty = 1;
    const qtyMatch = line.match(/^(\d+)\s*[xX]\s+(.*)$/);
    if (qtyMatch) {
      qty = Math.max(1, parseInt(qtyMatch[1], 10) || 1);
      line = qtyMatch[2].trim();
    } else {
      const unitMatch = line.match(/^(\d+)\s+(?:pcs|pieces|sets?|kits?|packs?|rolls?|boxes?)\s+(.*)$/i);
      if (unitMatch) {
        qty = Math.max(1, parseInt(unitMatch[1], 10) || 1);
        line = unitMatch[2].trim();
      }
    }

    let briefSpecs = '';
    if (line.includes(' — ')) {
      const [head, tail] = line.split(' — ', 2);
      if (head.trim()) {
        line = head.trim();
        briefSpecs = (tail || '').trim();
      }
    }

    let partNumber = '';
    const bracketMatch = line.match(/\[([^\]]+)\]\s*$/);
    if (bracketMatch) {
      partNumber = bracketMatch[1].trim();
      line = line.slice(0, bracketMatch.index).trim();
    }

    if (line) {
      parsed.push({
        name: line.slice(0, 160),
        part_number: partNumber.slice(0, 120) || null,
        category: null,
        estimated_qty: qty,
        brief_specs: briefSpecs.slice(0, 200) || null,
        included: true,
        _manual: true,
      });
    }
  }
  return parsed;
}

export function handleRapidAddPartKeydown(e) {
  if (e && e.key === 'Enter') {
    e.preventDefault();
    addPartToRapidList();
  }
}

export function handleRapidAddPartPaste(e) {
  const text = e?.clipboardData?.getData ? e.clipboardData.getData('text') : '';
  const trimmed = String(text || '').trim();
  const looksLikeJson = trimmed.startsWith('{') || trimmed.startsWith('[') || trimmed.startsWith('```');
  if (text && (looksLikeJson || /[\r\n]/.test(text))) {
    e.preventDefault();
    const input = document.getElementById('rapid-add-part-input');
    const prefix = (!looksLikeJson && input?.value) ? `${input.value}\n` : '';
    if (input) input.value = '';
    const added = addPartToRapidList(`${prefix}${text}`);
    if (looksLikeJson && added > 0) {
      showToast(`Imported ${pluralize(added, 'item')} from JSON!`);
    }
  }
}

export function addPartToRapidList(rawOverride = null) {
  const input = document.getElementById('rapid-add-part-input');
  const raw = rawOverride !== null ? String(rawOverride) : (input?.value || '');
  const entries = parseManualPartEntries(raw);
  if (!entries.length) {
    if (rawOverride === null) {
      showToast('Type a part name or paste a JSON/text list first.', 'error');
      input?.focus();
    }
    return 0;
  }

  const beforeLen = rapidDetectedItems.length;
  rapidDetectedItems = mergeDetectedItems([...rapidDetectedItems, ...entries]);
  const addedCount = Math.max(0, rapidDetectedItems.length - beforeLen);
  if (rawOverride === null && input) {
    input.value = '';
  }
  markModalDirty('drawer-rapid-entry-modal');
  const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10) || null;
  renderRapidAiResults(
    rapidDetectedItems,
    rapidAiMessage || `Parts list (${pluralize(rapidDetectedItems.length, 'part')})`,
    locId
  );
  if (rawOverride === null && input) {
    input.focus();
  }
  return addedCount || entries.length;
}

export function toggleRapidDetectedItem(idx, checked) {
  const item = rapidDetectedItems[Number(idx)];
  if (!item) return;
  item.included = Boolean(checked);
  markModalDirty('drawer-rapid-entry-modal');
  syncRapidSubmitButtonLabel();
}

export function toggleRapidItemAsBox(idx) {
  const item = rapidDetectedItems[Number(idx)];
  if (!item) return;
  item.save_as_box = !item.save_as_box;
  if (item.save_as_box) item.included = true;
  markModalDirty('drawer-rapid-entry-modal');
  const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10) || null;
  renderRapidAiResults(rapidDetectedItems, rapidAiMessage, locId);
}

export function toggleAllRapidDetectedItems(checked) {
  const flag = Boolean(checked);
  for (const item of rapidDetectedItems) {
    if (item) item.included = flag;
  }
  markModalDirty('drawer-rapid-entry-modal');
  const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10) || null;
  renderRapidAiResults(rapidDetectedItems, rapidAiMessage, locId);
}

export function clearRapidAiCrops() {
  for (const item of rapidDetectedItems) {
    if (item) {
      item.crop_photo_path = null;
      item._photoPath = null;
    }
  }
  markModalDirty('drawer-rapid-entry-modal');
  const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10) || null;
  renderRapidAiResults(rapidDetectedItems, rapidAiMessage, locId);
  showToast('Cleared AI crops — items will use the drawer/box photo instead.');
}

export function toggleRapidItemDetails(idx) {
  const item = rapidDetectedItems[Number(idx)];
  if (!item) return;
  item._expanded = !item._expanded;
  const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10) || null;
  renderRapidAiResults(rapidDetectedItems, rapidAiMessage, locId);
}

export function updateRapidDetectedItemField(idx, field, value) {
  const item = rapidDetectedItems[Number(idx)];
  if (!item) return;
  if (field === 'estimated_qty') {
    const n = parseInt(value, 10);
    item.estimated_qty = Number.isFinite(n) && n >= 1 ? n : 1;
  } else if (field === 'name') {
    item.name = String(value || '');
  } else if (field === 'part_number') {
    item.part_number = String(value || '').trim() || null;
  } else if (field === 'brief_specs') {
    item.brief_specs = String(value || '').trim() || null;
  } else if (field === 'category') {
    item.category = String(value || '').trim() || null;
  } else if (field === 'box_label') {
    item.box_label = String(value || '').trim().slice(0, 40) || null;
  }
  markModalDirty('drawer-rapid-entry-modal');
  syncRapidSubmitButtonLabel();
}

export function removeRapidDetectedItem(idx) {
  const index = Number(idx);
  if (index < 0 || index >= rapidDetectedItems.length) return;
  rapidDetectedItems.splice(index, 1);
  markModalDirty('drawer-rapid-entry-modal');
  const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10) || null;
  renderRapidAiResults(
    rapidDetectedItems,
    rapidDetectedItems.length ? (rapidAiMessage || `Parts list (${pluralize(rapidDetectedItems.length, 'part')})`) : 'All parts removed from list. Add parts below or re-scan.',
    locId
  );
}

function renderRapidAiResults(items, message, locId = null) {
  const box = document.getElementById('rapid-ai-results');
  if (!box) return;
  rapidDetectedItems = items || [];
  rapidAiMessage = message || '';

  if (!rapidDetectedItems.length) {
    box.style.display = message ? 'block' : 'none';
    box.innerHTML = message
      ? `<p class="ai-results-msg">${escapeHtml(message)}</p>`
      : '';
    syncRapidSubmitButtonLabel();
    return;
  }

  const targetLocId = locId || parseInt(document.getElementById('rapid-location-select')?.value, 10) || null;
  const existingInBin = (targetLocId && rapidSaveMode === 'separate')
    ? state.items.filter(it => Number(it.location_id) === Number(targetLocId))
    : [];

  const isDupInBin = (it) => {
    if (!existingInBin.length) return false;
    const partKey = normalizePartKey(it.part_number);
    return existingInBin.some(ex => {
      if (partKey && normalizePartKey(ex.serial_or_part_number) === partKey) return true;
      return areLikelyDuplicateItemNames(ex.name, it.name);
    });
  };

  let dupInBoxCount = 0;
  for (const it of rapidDetectedItems) {
    const already = isDupInBin(it);
    if (already) dupInBoxCount += 1;
    if (it && it.included === undefined) {
      it.included = !already;
    }
  }

  const baseMessage = message || `Parts list (${pluralize(rapidDetectedItems.length, 'part')})`;
  const boxDupSummary = existingInBin.length > 0
    ? (dupInBoxCount > 0
      ? ` · ${dupInBoxCount} already in box (unchecked — check to add anyway)`
      : ' · 0 already in box')
    : '';

  box.style.display = 'block';
  if (rapidDetectedItems.length > 0) {
    markModalDirty('drawer-rapid-entry-modal');
  }
  const hasAnyCrop = rapidDetectedItems.some(it => it && it.crop_photo_path);
  box.innerHTML = `
    <div class="ai-results-header">
      <span>${escapeHtml(`${baseMessage}${boxDupSummary}`)}</span>
      <div style="display: flex; gap: 0.35rem; flex-wrap: wrap;">
        ${hasAnyCrop ? `<button type="button" class="btn btn-secondary btn-xs" onclick="clearRapidAiCrops()" title="Remove AI crop thumbnails if boxes were misaligned">Clear crops</button>` : ''}
        <button type="button" class="btn btn-secondary btn-xs" onclick="toggleAllRapidDetectedItems(true)">Select all</button>
        <button type="button" class="btn btn-secondary btn-xs" onclick="toggleAllRapidDetectedItems(false)">Clear</button>
      </div>
    </div>
    <div class="ai-results-list">
      ${rapidDetectedItems.map((it, idx) => {
        const alreadyInBin = isDupInBin(it);
        const itemThumb = sanitizePhotoUrl(it.crop_photo_path || it._photoPath || '');
        const isChecked = it.included !== false;
        const isBox = Boolean(it.save_as_box) && rapidSaveMode === 'separate';
        const isExpanded = Boolean(it._expanded);
        const hasExtra = Boolean(it.part_number || it.brief_specs);
        return `
        <div class="ai-detected-row ai-editable-row ${isBox ? 'ai-row-is-box' : ''}">
          <input
            type="checkbox"
            class="ai-detected-chk"
            data-idx="${idx}"
            ${isChecked ? 'checked' : ''}
            onchange="toggleRapidDetectedItem(${idx}, this.checked)"
            style="margin-top: 0.42rem;"
          >
          ${itemThumb ? `<img src="${escapeHtml(itemThumb)}" alt="${escapeHtml(it.name || 'Part')}" class="ai-editable-thumb">` : ''}
          <div class="ai-editable-fields">
            <div class="ai-editable-top">
              <input
                type="text"
                class="ai-edit-input ai-edit-name"
                value="${escapeHtml(it.name || '')}"
                placeholder="${isBox ? 'Box / container name' : 'Part or item name'}"
                aria-label="Part name"
                oninput="updateRapidDetectedItemField(${idx}, 'name', this.value)"
              >
              ${alreadyInBin ? `<span class="drawer-badge-count" title="Already exists in this container — check to add anyway">Already in box</span>` : ''}
              ${!isBox ? `
                <div class="ai-edit-qty-wrap" title="Quantity">
                  <span class="ai-edit-qty-prefix">×</span>
                  <input
                    type="number"
                    class="ai-edit-input ai-edit-qty"
                    min="1"
                    max="1000000"
                    value="${Math.max(1, Number(it.estimated_qty || 1))}"
                    aria-label="Quantity"
                    oninput="updateRapidDetectedItemField(${idx}, 'estimated_qty', this.value)"
                  >
                </div>
              ` : ''}
              ${rapidSaveMode === 'separate' ? `
                <button
                  type="button"
                  class="ai-box-toggle-btn ${isBox ? 'active' : ''}"
                  title="Convert this entry into an openable Box / Container using its photo"
                  onclick="toggleRapidItemAsBox(${idx})"
                >${isBox ? '📦 Box' : '→ Box'}</button>
              ` : ''}
              <button
                type="button"
                class="ai-edit-more-btn ${isExpanded ? 'active' : ''}"
                title="${isExpanded ? 'Hide part # and specs' : 'Edit part # or specs'}"
                aria-label="Toggle part details"
                onclick="toggleRapidItemDetails(${idx})"
              >⋯</button>
              <button
                type="button"
                class="ai-edit-del-btn"
                title="Remove part"
                aria-label="Remove part"
                onclick="removeRapidDetectedItem(${idx})"
              >×</button>
            </div>
            ${isBox ? `
              <div class="ai-box-meta-row">
                <span class="ai-box-badge">Saves as Box / Container inside this drawer (using this photo)</span>
                <input
                  type="text"
                  class="ai-edit-input ai-box-label-input"
                  value="${escapeHtml(it.box_label || '')}"
                  maxlength="40"
                  placeholder="Label (e.g. M2)"
                  aria-label="Box label"
                  oninput="updateRapidDetectedItemField(${idx}, 'box_label', this.value)"
                >
              </div>
            ` : ''}
            ${isExpanded ? `
              <div class="ai-editable-sub">
                <input
                  type="text"
                  class="ai-edit-input ai-edit-partno"
                  value="${escapeHtml(it.part_number || '')}"
                  placeholder="Part # / value (e.g. 2N2222, 10kΩ)"
                  aria-label="Part number or value"
                  oninput="updateRapidDetectedItemField(${idx}, 'part_number', this.value)"
                >
                <input
                  type="text"
                  class="ai-edit-input ai-edit-specs"
                  value="${escapeHtml(it.brief_specs || '')}"
                  placeholder="Specs / notes (optional)"
                  aria-label="Specs or notes"
                  oninput="updateRapidDetectedItemField(${idx}, 'brief_specs', this.value)"
                >
              </div>
            ` : (hasExtra ? `
              <button type="button" class="ai-row-specs-pill" onclick="toggleRapidItemDetails(${idx})" title="Click to edit part # or specs">
                ${it.part_number ? `<span class="ai-row-partno-tag">${escapeHtml(it.part_number)}</span>` : ''}
                ${it.brief_specs ? `<span class="ai-row-specs-text">${escapeHtml(it.brief_specs)}</span>` : ''}
                <span class="ai-row-edit-hint">Edit</span>
              </button>
            ` : '')}
            ${alreadyInBin ? `<div class="ai-already-in-bin-hint">Already in this bin (unchecked by default)</div>` : ''}
          </div>
        </div>
      `;
      }).join('')}
    </div>
  `;
  syncRapidSubmitButtonLabel();
}

export async function saveDetectedItemsDirect() {
  return submitRapidEntry();
}

export function applyDetectedItemsToText() {
  const checks = Array.from(document.querySelectorAll('.ai-detected-chk:checked'));
  const textarea = document.getElementById('rapid-items-text');
  const existingLines = textarea
    ? textarea.value.split(/\r?\n/).map(l => l.trim()).filter(Boolean)
    : [];

  const lines = [];
  let skippedDupes = 0;
  for (const chk of checks) {
    const idx = Number(chk.dataset.idx);
    const it = rapidDetectedItems[idx];
    if (!it || !String(it.name || '').trim()) continue;
    const alreadyListed = existingLines.some(line => areLikelyDuplicateItemNames(line, it.name))
      || lines.some(line => areLikelyDuplicateItemNames(line, it.name));
    if (alreadyListed) {
      skippedDupes += 1;
      continue;
    }
    const qty = Math.max(1, Number(it.estimated_qty || 1));
    const head = qty > 1 ? `${qty}x ${it.name.trim()}` : it.name.trim();
    const specs = it.brief_specs ? ` — ${String(it.brief_specs).trim()}` : '';
    lines.push(`${head}${specs}`);
  }
  if (!lines.length) {
    showToast(
      skippedDupes > 0 ? 'All selected items are already in the list.' : 'Select at least one detected item.',
      skippedDupes > 0 ? 'info' : 'error'
    );
    return;
  }
  if (textarea) {
    const existing = textarea.value.trim();
    textarea.value = existing ? `${existing}\n${lines.join('\n')}` : lines.join('\n');
  }
  const catInput = document.getElementById('rapid-category-input');
  if (catInput && !catInput.value.trim()) {
    const firstCat = rapidDetectedItems.find(it => it.category)?.category;
    if (firstCat) catInput.value = firstCat;
  }
  const dupeNote = skippedDupes > 0 ? ` (${skippedDupes} duplicate skipped)` : '';
  showToast(`Copied ${pluralize(lines.length, 'part')} to text lines${dupeNote}.`);
}

function normalizePartKey(val) {
  return String(val || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

function mergeDetectedItems(items) {
  const merged = [];
  for (const raw of items) {
    const name = String(raw?.name || '').trim();
    if (!name) continue;
    const qty = Math.max(1, Number(raw.estimated_qty || 1));
    const preferredPhoto = raw.crop_photo_path || raw._photoPath || null;
    const rawPartKey = normalizePartKey(raw.part_number);
    const existing = merged.find(item => {
      if (rawPartKey && normalizePartKey(item.part_number) === rawPartKey) return true;
      return areLikelyDuplicateItemNames(item.name, name);
    });
    if (!existing) {
      merged.push({ ...raw, name, estimated_qty: qty, _photoPath: preferredPhoto });
      continue;
    }
    if (normalizeItemTokens(name).length > normalizeItemTokens(existing.name).length) {
      existing.name = name;
    }
    existing.estimated_qty = Math.max(existing.estimated_qty, qty);
    if (!existing.part_number && raw.part_number) existing.part_number = raw.part_number;
    if (!existing.category && raw.category) existing.category = raw.category;
    if (!existing.condition && raw.condition) existing.condition = raw.condition;
    if ((!existing.tags || !existing.tags.length) && Array.isArray(raw.tags) && raw.tags.length) existing.tags = raw.tags;
    if (raw.brief_specs && (!existing.brief_specs || raw.brief_specs.length > existing.brief_specs.length)) {
      existing.brief_specs = raw.brief_specs;
    }
    if (raw.crop_photo_path && !existing.crop_photo_path) {
      existing.crop_photo_path = raw.crop_photo_path;
      existing._photoPath = raw.crop_photo_path;
    } else if (!existing._photoPath && preferredPhoto) {
      existing._photoPath = preferredPhoto;
    }
  }
  return merged;
}

function revokeRapidBlobUrls() {
  for (const url of rapidPreviewUrls) {
    if (typeof url === 'string' && url.startsWith('blob:')) {
      try { URL.revokeObjectURL(url); } catch {}
    }
  }
  if (rapidPreviewUrl && !rapidPreviewUrls.includes(rapidPreviewUrl)) {
    try { URL.revokeObjectURL(rapidPreviewUrl); } catch {}
  }
  rapidPreviewUrl = null;
}

function markModalDirty(modalId) {
  const modal = document.getElementById(modalId);
  if (modal && modal.classList.contains('open')) {
    modal.dataset.dirty = 'true';
  }
}

export async function openRapidEntryModal(defaultLocationId = null, options = {}) {
  abortRapidAiScan();
  populateLocationDropdowns();
  revokeRapidBlobUrls();
  rapidSelectedFiles = [];
  rapidRemotePhotoPath = null;
  rapidDetectedItems = [];
  rapidAiMessage = '';
  rapidManualMode = Boolean(options.manual);
  rapidLlmMode = Boolean(options.openLlmPanel);
  rapidIsScanning = false;
  rapidLocationPickerExpanded = !defaultLocationId;
  rapidSessionUploadedPhotoIds = Array.isArray(options.sessionPhotoIds)
    ? options.sessionPhotoIds.map(Number).filter(Boolean)
    : [];

  const detailsEl = document.getElementById('rapid-advanced-details');
  if (detailsEl) detailsEl.open = false;
  const llmInput = document.getElementById('rapid-llm-json-input');
  if (llmInput) llmInput.value = '';
  const expectedInput = document.getElementById('rapid-expected-count');
  if (expectedInput) expectedInput.value = '';
  refreshLlmPromptPreview();

  const optIds = Array.isArray(options.photoIds) ? options.photoIds.map(Number).filter(Boolean) : [];
  const hasExplicitPhotos = Boolean(optIds.length > 0 || options.photoId || options.photoUrl || options.autoScan);
  const defaultLoc = defaultLocationId ? state.locationMap.get(Number(defaultLocationId)) : null;
  const savedPhotos = Array.isArray(defaultLoc?.photos) ? defaultLoc.photos : [];

  if (optIds.length > 0) {
    rapidPhotoIds = optIds;
    rapidPhotoId = optIds[0];
  } else if (options.photoId) {
    rapidPhotoIds = [Number(options.photoId)];
    rapidPhotoId = Number(options.photoId);
  } else if (options.autoScan && savedPhotos.length > 0) {
    rapidPhotoIds = savedPhotos.map(p => Number(p.id)).filter(Boolean);
    rapidPhotoId = rapidPhotoIds[0] || null;
  } else {
    rapidPhotoIds = [];
    rapidPhotoId = null;
  }

  if (hasExplicitPhotos) {
    const photoUrlMap = new Map(savedPhotos.map(p => [Number(p.id), p.photo_path]));
    const drawerPreviewUrls = rapidPhotoIds.length > 0
      ? rapidPhotoIds.map(id => photoUrlMap.get(id)).filter(Boolean)
      : [];
    const fallbackPhotoUrl = savedPhotos[0]?.photo_path || defaultLoc?.container_photo || '';
    const safeExistingUrl = sanitizePhotoUrl(options.photoUrl || drawerPreviewUrls[0] || fallbackPhotoUrl);
    rapidPreviewUrls = drawerPreviewUrls.length > 0
      ? drawerPreviewUrls
      : (safeExistingUrl ? [safeExistingUrl] : []);
    rapidScannedPhotoUrl = safeExistingUrl || null;
  } else {
    rapidPreviewUrls = [];
    rapidScannedPhotoUrl = null;
  }

  const locSelect = document.getElementById('rapid-location-select');
  if (locSelect && defaultLocationId) {
    locSelect.value = String(defaultLocationId);
  }

  const fileInput = document.getElementById('rapid-photo-input');
  if (fileInput) fileInput.value = '';

  const addPartInput = document.getElementById('rapid-add-part-input');
  if (addPartInput) addPartInput.value = '';

  const assortmentNameInput = document.getElementById('rapid-assortment-name');
  if (assortmentNameInput) assortmentNameInput.value = options.initialBoxName ? String(options.initialBoxName) : '';
  const assortmentLabelInput = document.getElementById('rapid-assortment-label');
  if (assortmentLabelInput) assortmentLabelInput.value = options.initialBoxLabel ? String(options.initialBoxLabel) : '';

  const resultsBox = document.getElementById('rapid-ai-results');
  if (resultsBox) {
    resultsBox.style.display = 'none';
    resultsBox.innerHTML = '';
  }

  const filenameEl = document.getElementById('rapid-photo-filename');
  if (rapidPreviewUrls.length > 0) {
    if (filenameEl) {
      filenameEl.textContent = rapidPhotoIds.length > 1
        ? `Using ${rapidPhotoIds.length} photos`
        : 'Using selected photo';
    }
    renderRapidPhotoPreviews(rapidPreviewUrls);
  } else {
    if (filenameEl) filenameEl.textContent = 'No photo selected';
    renderRapidPhotoPreviews([]);
  }

  const itemsTextEl = document.getElementById('rapid-items-text');
  if (itemsTextEl) itemsTextEl.value = '';
  const catInputEl = document.getElementById('rapid-category-input');
  if (catInputEl) catInputEl.value = '';

  rapidUserPickedSaveMode = Boolean(options.saveMode);
  setRapidSaveMode(options.saveMode || 'separate', Boolean(options.saveMode));
  markModalClean('drawer-rapid-entry-modal');
  document.getElementById('drawer-rapid-entry-modal')?.classList.add('open');

  const openSeq = ++rapidOpenSeq;
  await loadVisionProviderStatus();
  const stillOpen = openSeq === rapidOpenSeq
    && document.getElementById('drawer-rapid-entry-modal')?.classList.contains('open');
  if (stillOpen && options.autoScan && (rapidPhotoIds.length > 0 || rapidScannedPhotoUrl)) {
    await runRapidAiScan();
  }
}

export function triggerRapidPhotoInput() {
  rapidAutoScanNextPick = false;
  openDesktopFilePickerDirect('rapid-photo-input');
}

export function triggerRapidPhotoAndScan() {
  rapidAutoScanNextPick = true;
  openDesktopFilePickerDirect('rapid-photo-input');
}

export function startRapidPhoneCapture() {
  const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10);
  if (!locId) {
    showToast('Please choose a bin or drawer first.', 'error');
    rapidLocationPickerExpanded = true;
    syncRapidLocationUI();
    return;
  }
  const loc = state.locationMap.get(locId);
  const isBoxAlready = Boolean(loc && ['box', 'kit'].includes(String(loc.location_type || '').toLowerCase()));
  openLocationPhoneCaptureModal({
    locationId: locId,
    locationName: loc ? loc.name : 'Drawer',
    defaultMode: isBoxAlready ? 'separate' : 'box',
    onDone: async ({ photos, shouldScan, saveMode, userPickedMode }) => {
      await loadAllData();
      if (photos && photos.length > 0) {
        revokeRapidBlobUrls();
        const coverPhoto = photos[0];
        const safeUrl = sanitizePhotoUrl(coverPhoto.original_path || '');
        const updatedLoc = state.locationMap.get(locId);
        const savedList = Array.isArray(updatedLoc?.photos) ? updatedLoc.photos : [];
        const capturedPaths = new Set(photos.map(p => p.original_path).filter(Boolean));
        const matchedIds = savedList
          .filter(p => capturedPaths.has(p.photo_path))
          .map(p => Number(p.id))
          .filter(Boolean);
        rapidPhotoIds = matchedIds.length > 0
          ? matchedIds
          : savedList.slice(0, photos.length).map(p => Number(p.id)).filter(Boolean);
        rapidPhotoId = rapidPhotoIds[0] || null;
        rapidSessionUploadedPhotoIds = [...rapidPhotoIds];

        if (safeUrl) {
          rapidScannedPhotoUrl = safeUrl;
          rapidRemotePhotoPath = rapidPhotoIds.length > 0 ? null : safeUrl;
          rapidSelectedFiles = [];
          rapidPreviewUrls = photos.map(p => sanitizePhotoUrl(p.original_path || '')).filter(Boolean);
          if (!rapidPreviewUrls.length) rapidPreviewUrls = [safeUrl];
          const filenameEl = document.getElementById('rapid-photo-filename');
          if (filenameEl) filenameEl.textContent = `${photos.length} photo(s) from phone`;
          renderRapidPhotoPreviews(rapidPreviewUrls);
        }
        if (saveMode && !isBoxAlready) {
          setRapidSaveMode(saveMode, Boolean(userPickedMode));
        }
        syncRapidStageUI();
        if (shouldScan) {
          await runRapidAiScan();
        }
      }
    },
  });
}

export function handleRapidPhotoSelected(e) {
  const files = Array.from(e.target.files || []).filter(Boolean);
  const shouldAutoScan = rapidAutoScanNextPick;
  rapidAutoScanNextPick = false;
  if (!files.length) return;
  revokeRapidBlobUrls();
  rapidSelectedFiles = files;
  rapidRemotePhotoPath = null;
  rapidPhotoId = null;
  rapidPhotoIds = [];
  rapidPreviewUrls = files.map(f => URL.createObjectURL(f));
  rapidPreviewUrl = rapidPreviewUrls[0] || null;
  const filenameEl = document.getElementById('rapid-photo-filename');
  if (filenameEl) {
    filenameEl.textContent = files.length === 1
      ? files[0].name
      : `${files.length} photos selected`;
  }
  renderRapidPhotoPreviews(rapidPreviewUrls);
  markModalDirty('drawer-rapid-entry-modal');
  syncRapidSubmitButtonLabel();
  if (shouldAutoScan) {
    const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10);
    if (locId) {
      runRapidAiScan();
    }
  }
}

export async function runRapidAiScan() {
  const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10);
  if (!locId) {
    showToast('Please choose a bin or drawer first.', 'error');
    rapidLocationPickerExpanded = true;
    syncRapidLocationUI();
    return;
  }

  const providerSelect = document.getElementById('rapid-vision-provider');
  let provider = providerSelect?.value || 'local';
  const readiness = await ensureVisionModelReady({
    provider,
    onSwitchProvider: (nextProv) => {
      if (providerSelect && nextProv) {
        providerSelect.value = nextProv;
        onVisionProviderChange();
      }
    },
    onRetry: () => runRapidAiScan(),
    onManual: () => {
      rapidManualMode = true;
      syncRapidStageUI();
      document.getElementById('rapid-add-part-input')?.focus();
    },
  });
  if (!readiness.ok) {
    if (readiness.meta) {
      rapidVisionMeta = readiness.meta;
      updateVisionStatusUI();
    }
    return;
  }
  provider = readiness.provider;
  const model = document.getElementById('rapid-vision-model')?.value.trim() || '';
  const scanBtn = document.getElementById('rapid-ai-scan-btn');
  const scanBannerText = document.getElementById('rapid-scanning-text');

  if (rapidIsScanning) return;
  const scanController = new AbortController();
  rapidScanController = scanController;
  rapidIsScanning = true;
  if (scanBannerText) scanBannerText.textContent = 'Scanning photo for items...';
  syncRapidStageUI();

  return withSubmitLock('rapid-ai-scan-btn', 'Scanning...', async () => {
    try {
      const priorItems = rapidDetectedItems.filter(it => it && String(it.name || '').trim());
      const expectedCountRaw = parseInt(document.getElementById('rapid-expected-count')?.value || '', 10);
      const expectedCount = expectedCountRaw > 0 ? expectedCountRaw : null;
      const collectedItems = [];
      let successCount = 0;
      let totalScanned = 0;
      let lastMessage = '';
      let lastFailureMessage = '';
      let suggestedGroupName = '';
      let suggestedCategory = '';

      if (!rapidSelectedFiles.length && !rapidPhotoIds.length && rapidRemotePhotoPath) {
        const attached = await apiPost(
          `/api/v1/locations/${locId}/photos/attach`,
          { photo_path: rapidRemotePhotoPath, photo_type: 'container' },
          'Could not attach phone photo.',
          { signal: scanController.signal }
        );
        if (scanController.signal.aborted) return;
        await loadAllData();
        rapidScannedPhotoUrl = rapidRemotePhotoPath;
        rapidRemotePhotoPath = null;
        if (attached && attached.id) {
          rapidPhotoIds = [Number(attached.id)];
          rapidPhotoId = Number(attached.id);
        }
      }

      if (rapidSelectedFiles.length > 0) {
        const filesToScan = [...rapidSelectedFiles];
        totalScanned = filesToScan.length;
        let idx = 0;
        for (const file of filesToScan) {
          if (scanController.signal.aborted) return;
          idx += 1;
          if (totalScanned > 1) {
            if (scanBtn) scanBtn.textContent = `Scanning ${idx}/${totalScanned}...`;
            if (scanBannerText) scanBannerText.textContent = `Scanning photo ${idx} of ${totalScanned}...`;
          }
          if (rapidPreviewUrls.length > 1) renderRapidPhotoPreviews(rapidPreviewUrls, idx - 1);
          const fd = new FormData();
          fd.append('provider', provider);
          if (model) fd.append('model', model);
          if (expectedCount) fd.append('expected_count', String(expectedCount));
          const compressed = await compressImageFile(file);
          if (scanController.signal.aborted) return;
          fd.append('file', compressed);

          const data = await apiUpload(
            `/api/v1/locations/${locId}/analyze-photo`,
            fd,
            'AI scan failed.',
            { timeoutMs: AI_SCAN_TIMEOUT_MS, signal: scanController.signal }
          );
          if (scanController.signal.aborted) return;
          rapidSelectedFiles.shift();
          lastMessage = data.message || lastMessage;
          if (data.status === 'success') {
            successCount += 1;
            if (!suggestedGroupName && data.suggested_group_name) suggestedGroupName = String(data.suggested_group_name).trim();
            if (!suggestedCategory && data.suggested_category) suggestedCategory = String(data.suggested_category).trim();
            for (const it of data.items || []) collectedItems.push(it);
          } else if (data.message) {
            lastFailureMessage = data.message;
          }
        }
        await loadAllData();
        if (scanController.signal.aborted) return;
        const updatedLoc = state.locationMap.get(locId);
        const savedPhotos = Array.isArray(updatedLoc?.photos) ? updatedLoc.photos : [];
        if (savedPhotos.length > 0) {
          revokeRapidBlobUrls();
          rapidPhotoIds = savedPhotos.slice(0, totalScanned).map(p => Number(p.id)).filter(Boolean);
          rapidPhotoId = rapidPhotoIds[0] || null;
          rapidSessionUploadedPhotoIds = [...rapidPhotoIds];
          rapidScannedPhotoUrl = savedPhotos[0].photo_path || rapidScannedPhotoUrl;
          rapidPreviewUrls = savedPhotos.slice(0, totalScanned).map(p => p.photo_path).filter(Boolean);
        }
      } else if (rapidPhotoIds.length > 0) {
        const loc = state.locationMap.get(locId);
        const photoMap = new Map((loc?.photos || []).map(p => [Number(p.id), p.photo_path]));
        totalScanned = rapidPhotoIds.length;
        let idx = 0;
        for (const pid of rapidPhotoIds) {
          if (scanController.signal.aborted) return;
          idx += 1;
          if (totalScanned > 1) {
            if (scanBtn) scanBtn.textContent = `Scanning ${idx}/${totalScanned}...`;
            if (scanBannerText) scanBannerText.textContent = `Scanning photo ${idx} of ${totalScanned}...`;
          }
          if (rapidPreviewUrls.length > 1) renderRapidPhotoPreviews(rapidPreviewUrls, idx - 1);
          const fd = new FormData();
          fd.append('provider', provider);
          if (model) fd.append('model', model);
          if (expectedCount) fd.append('expected_count', String(expectedCount));
          fd.append('photo_id', String(pid));

          const data = await apiUpload(
            `/api/v1/locations/${locId}/analyze-photo`,
            fd,
            'AI scan failed.',
            { timeoutMs: AI_SCAN_TIMEOUT_MS, signal: scanController.signal }
          );
          if (scanController.signal.aborted) return;
          lastMessage = data.message || lastMessage;
          if (data.status === 'success') {
            successCount += 1;
            if (!suggestedGroupName && data.suggested_group_name) suggestedGroupName = String(data.suggested_group_name).trim();
            if (!suggestedCategory && data.suggested_category) suggestedCategory = String(data.suggested_category).trim();
            const photoPath = photoMap.get(Number(pid)) || rapidScannedPhotoUrl || null;
            for (const it of data.items || []) {
              const bestPhoto = it.crop_photo_path || photoPath;
              collectedItems.push(bestPhoto ? { ...it, _photoPath: bestPhoto } : it);
            }
          } else if (data.message) {
            lastFailureMessage = data.message;
          }
        }
      } else {
        totalScanned = 1;
        const fd = new FormData();
        fd.append('provider', provider);
        if (model) fd.append('model', model);
        if (expectedCount) fd.append('expected_count', String(expectedCount));
        const data = await apiUpload(
          `/api/v1/locations/${locId}/analyze-photo`,
          fd,
          'AI scan failed.',
          { timeoutMs: AI_SCAN_TIMEOUT_MS, signal: scanController.signal }
        );
        if (scanController.signal.aborted) return;
        lastMessage = data.message || '';
        if (data.status === 'success') {
          successCount = 1;
          if (data.suggested_group_name) suggestedGroupName = String(data.suggested_group_name).trim();
          if (data.suggested_category) suggestedCategory = String(data.suggested_category).trim();
          for (const it of data.items || []) collectedItems.push(it);
        } else if (data.message) {
          lastFailureMessage = data.message;
        }
      }

      if (scanController.signal.aborted) return;
      if (rapidPreviewUrls.length > 1) renderRapidPhotoPreviews(rapidPreviewUrls, -1);

      const mergedAiItems = mergeDetectedItems(collectedItems);
      const combinedItems = mergeDetectedItems([...priorItems, ...mergedAiItems]);

      const assortmentInput = document.getElementById('rapid-assortment-name');
      if (assortmentInput && !assortmentInput.value.trim() && suggestedGroupName) {
        assortmentInput.value = suggestedGroupName;
      }
      const targetLoc = state.locationMap.get(locId);
      const targetIsAlreadyBox = ['box', 'kit'].includes(getLocType(targetLoc));
      if (!rapidUserPickedSaveMode && suggestedGroupName && combinedItems.length >= 2 && !targetIsAlreadyBox) {
        setRapidSaveMode('box', false);
      }
      const catInput = document.getElementById('rapid-category-input');
      if (catInput && !catInput.value.trim()) {
        const autoCat = suggestedCategory || mergedAiItems.find(it => it.category)?.category || '';
        if (autoCat) catInput.value = autoCat;
      }

      const failedCount = totalScanned - successCount;
      let summaryMsg = totalScanned > 1
        ? `Scanned ${successCount}/${totalScanned} photos — ${pluralize(combinedItems.length, 'unique item')}.`
        : (lastMessage || lastFailureMessage);
      if (expectedCount && combinedItems.length < expectedCount) {
        summaryMsg = `Found ${combinedItems.length} of ${expectedCount} expected types — add another photo or paste an LLM JSON list below.`;
      }
      if (totalScanned > 1 && failedCount > 0 && successCount > 0) {
        summaryMsg += ` (${failedCount} failed: ${lastFailureMessage || 'check logs/wims.log'})`;
      }

      if (rapidScanController === scanController) {
        rapidScanController = null;
      }
      rapidIsScanning = false;
      renderRapidAiResults(
        combinedItems,
        successCount === 0 && lastFailureMessage ? lastFailureMessage : summaryMsg,
        locId
      );
      document.getElementById('rapid-ai-results')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

      if (successCount === 0) {
        showToast(lastFailureMessage || 'AI scan did not return items.', 'error');
      } else if (failedCount > 0) {
        showToast(`Scanned ${successCount}/${totalScanned} photos (${failedCount} failed).`, 'error');
      } else if (mergedAiItems.length === 0) {
        showToast(
          totalScanned > 1
            ? `Scanned ${totalScanned} photos, but no items were detected.`
            : 'AI scan finished, but no items were detected in this photo.',
          'info'
        );
      } else {
        showToast(`AI found ${pluralize(mergedAiItems.length, 'item')} — review or add items below, then click Save.`);
      }
    } catch (err) {
      if (err?.name === 'UserAbortError' || scanController.signal.aborted) {
        return;
      }
      showToast(err.message || 'AI scan failed.', 'error');
    } finally {
      if (rapidScanController === scanController) {
        rapidScanController = null;
        rapidIsScanning = false;
        syncRapidStageUI();
      }
    }
  });
}

export async function submitRapidEntry() {
  if (rapidIsScanning) {
    const choice = await showScanAbortPrompt(
      'The AI scan is still running. Do you want to abort the scan and save now without waiting for the remaining results?'
    );
    if (choice !== 'abort') {
      return;
    }
    abortRapidAiScan();
    syncRapidStageUI();
  }

  const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10);
  if (!locId) {
    showToast('Please choose where these items go first.', 'error');
    return;
  }

  const pendingPartInput = document.getElementById('rapid-add-part-input');
  if (pendingPartInput && pendingPartInput.value.trim()) {
    addPartToRapidList();
  }

  const checkedParts = rapidDetectedItems.filter(
    it => it && it.included !== false && String(it.name || '').trim()
  );
  const text = (document.getElementById('rapid-items-text')?.value || '').trim();
  const category = (document.getElementById('rapid-category-input')?.value || '').trim() || null;
  const assortmentName = (document.getElementById('rapid-assortment-name')?.value || '').trim() || null;
  const assortmentLabel = (document.getElementById('rapid-assortment-label')?.value || '').trim().slice(0, 40) || null;

  if (!checkedParts.length && !text && !rapidSelectedFiles.length && !rapidRemotePhotoPath && !rapidPhotoId) {
    showToast('Scan a photo or add at least one part to save.', 'error');
    return;
  }

  return withSubmitLock('rapid-submit-btn', 'Saving...', async () => {
    try {
      let latestUploadedPhotoPath = null;
      if (rapidSelectedFiles.length > 0) {
        // Drop each file once saved so a retry after a mid-batch failure doesn't duplicate it.
        while (rapidSelectedFiles.length > 0) {
          const compressed = await compressImageFile(rapidSelectedFiles[0]);
          const fd = new FormData();
          fd.append('file', compressed);
          const savedPhoto = await apiUpload(`/api/v1/locations/${locId}/photos`, fd, 'Could not save photo.');
          rapidSelectedFiles.shift();
          if (savedPhoto && savedPhoto.photo_path) {
            latestUploadedPhotoPath = savedPhoto.photo_path;
          }
          if (savedPhoto && savedPhoto.id) {
            rapidSessionUploadedPhotoIds.push(Number(savedPhoto.id));
          }
        }
      } else if (rapidRemotePhotoPath && !rapidPhotoId) {
        const attached = await apiPost(
          `/api/v1/locations/${locId}/photos/attach`,
          { photo_path: rapidRemotePhotoPath, photo_type: 'container' },
          'Could not save photo.'
        );
        latestUploadedPhotoPath = attached?.photo_path || rapidRemotePhotoPath;
        if (attached && attached.id) {
          rapidSessionUploadedPhotoIds.push(Number(attached.id));
        }
        rapidRemotePhotoPath = null;
      }

      const loc = state.locationMap.get(locId);
      const defaultPhotoUrl = sanitizePhotoUrl(
        latestUploadedPhotoPath
        || rapidScannedPhotoUrl
        || loc?.photos?.[0]?.photo_path
        || loc?.container_photo
        || ''
      ) || null;

      const boxParts = rapidSaveMode === 'separate'
        ? checkedParts.filter(it => Boolean(it.save_as_box))
        : [];
      const regularParts = rapidSaveMode === 'separate'
        ? checkedParts.filter(it => !it.save_as_box)
        : checkedParts;

      const createdBoxes = [];
      for (const bp of boxParts) {
        const boxRes = await createSubBoxFromDetectedRow(locId, {
          ...bp,
          crop_photo_path: bp.crop_photo_path || bp._photoPath || defaultPhotoUrl,
        });
        if (boxRes && boxRes.id) {
          createdBoxes.push(boxRes);
          expandSubBox(boxRes.id);
        }
      }

      const structuredItems = regularParts.map(it => {
        const candidateName = String(it.name || '').trim().slice(0, 160);
        const candidatePart = (it.part_number && String(it.part_number).trim().slice(0, 120)) || null;
        const cropPhoto = sanitizePhotoUrl(it.crop_photo_path || '') || null;
        const fallbackItemPhoto = rapidSaveMode === 'box'
          ? null
          : (sanitizePhotoUrl(it._photoPath || defaultPhotoUrl || '') || null);
        const itemPhoto = cropPhoto || fallbackItemPhoto;
        const itemPayload = {
          name: candidateName,
          quantity: Math.min(1000000, Math.max(1, Math.round(Number(it.estimated_qty || 1)) || 1)),
          category: ((it.category && String(it.category).trim()) || category || '').slice(0, 80) || null,
          serial_or_part_number: candidatePart,
          description: (it.brief_specs && String(it.brief_specs).trim().slice(0, 20000)) || null,
          item_photo: itemPhoto,
          location_id: locId,
        };
        if (Array.isArray(it.tags) && it.tags.length) {
          const tagStr = it.tags.map(t => String(t || '').trim()).filter(Boolean).join(', ').slice(0, 300);
          if (tagStr) itemPayload.tags = tagStr;
        } else if (typeof it.tags === 'string' && it.tags.trim()) {
          itemPayload.tags = it.tags.trim().slice(0, 300);
        }
        return itemPayload;
      });

      if (structuredItems.length > 0 || text) {
        const bulkPayload = {
          location_id: locId,
          save_mode: rapidSaveMode,
          category: category ? category.slice(0, 80) : null,
        };
        if (structuredItems.length > 0) bulkPayload.items = structuredItems;
        if (text) bulkPayload.raw_lines = text;
        if (rapidSaveMode === 'box' || rapidSaveMode === 'assortment') {
          bulkPayload.assortment_name = assortmentName ? assortmentName.slice(0, 160) : null;
          bulkPayload.assortment_photo = defaultPhotoUrl;
          if (rapidSaveMode === 'box') {
            if (assortmentLabel) bulkPayload.assortment_label = assortmentLabel;
            if (rapidSessionUploadedPhotoIds.length > 0) {
              bulkPayload.source_photo_ids = rapidSessionUploadedPhotoIds;
            }
          }
        }

        const created = await apiPost('/api/v1/items/bulk', bulkPayload, 'Could not save items.');
        if (rapidSaveMode === 'box') {
          if (created.length > 0 && created[0].location_id) {
            expandSubBox(created[0].location_id);
          }
          showToast(`Created "${assortmentName || 'Box / Kit'}" with ${pluralize(created.length, 'item')}!`);
        } else if (rapidSaveMode === 'assortment' && created.length === 1) {
          showToast(`Saved "${created[0].name}" with searchable contents list!`);
        } else if (createdBoxes.length > 0) {
          showToast(`Added ${pluralize(created.length, 'item')} and ${pluralize(createdBoxes.length, 'container')}!`);
        } else {
          showToast(`Added ${pluralize(created.length, 'item')}!`);
        }

        const needsAutoPhoto =
          (rapidSaveMode === 'box' || rapidSaveMode === 'separate') &&
          created.some((it) => !it.item_photo);
        const targetAutoLocId =
          rapidSaveMode === 'box' && created.length > 0 && created[0].location_id
            ? created[0].location_id
            : locId;
        if (needsAutoPhoto && targetAutoLocId) {
          // Only fill items without a photo; preserve real user photo crops from AI scan.
          apiPost(`/api/v1/locations/${targetAutoLocId}/find-item-images?include_crops=false`, {}, null, { timeoutMs: 300000 })
            .then((res) => {
              if (res && res.updated_count > 0) {
                loadAllData();
              }
            })
            .catch(() => {});
        }
      } else if (createdBoxes.length > 0) {
        showToast(`Created ${pluralize(createdBoxes.length, 'container')}!`);
      } else {
        showToast('Saved photo(s)!');
      }

      revokeRapidBlobUrls();
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
let pendingQuickPhotoFile = null;
let pendingQuickPhotoPreviewUrl = null;
let quickPhotoEventsBound = false;

function setQuickPhotoFile(file) {
  if (pendingQuickPhotoPreviewUrl) {
    URL.revokeObjectURL(pendingQuickPhotoPreviewUrl);
    pendingQuickPhotoPreviewUrl = null;
  }
  pendingQuickPhotoFile = file || null;
  const label = document.getElementById('quick-photo-input-label');
  const preview = document.getElementById('quick-photo-preview');
  const icon = document.getElementById('quick-photo-icon');

  if (file) {
    if (label) label.textContent = file.name || 'Pasted image';
    if (preview) {
      pendingQuickPhotoPreviewUrl = URL.createObjectURL(file);
      preview.src = pendingQuickPhotoPreviewUrl;
      preview.style.display = 'block';
    }
    if (icon) icon.style.display = 'none';
  } else {
    if (label) label.textContent = 'Tap, choose, or paste photo (Ctrl+V)';
    if (preview) {
      preview.removeAttribute('src');
      preview.style.display = 'none';
    }
    if (icon) icon.style.display = '';
  }
}

function extractImageFromClipboard(clipboardData) {
  if (!clipboardData) return null;
  const items = clipboardData.items ? Array.from(clipboardData.items) : [];
  for (const item of items) {
    if (item.kind === 'file' && item.type && item.type.startsWith('image/')) {
      const blob = item.getAsFile();
      if (blob) {
        const ext = (blob.type.split('/')[1] || 'png').replace(/[^a-z0-9]/gi, '') || 'png';
        return blob.name ? blob : new File([blob], `pasted-image.${ext}`, { type: blob.type || 'image/png' });
      }
    }
  }
  const files = clipboardData.files ? Array.from(clipboardData.files) : [];
  for (const file of files) {
    if (file && (!file.type || file.type.startsWith('image/'))) {
      return file;
    }
  }
  return null;
}

function bindQuickPhotoPasteAndDrop() {
  if (quickPhotoEventsBound) return;
  quickPhotoEventsBound = true;

  document.addEventListener('paste', (e) => {
    const modal = document.getElementById('quick-photo-modal');
    if (!modal || !modal.classList.contains('open')) return;
    const imgFile = extractImageFromClipboard(e.clipboardData);
    if (!imgFile) return;
    e.preventDefault();
    setQuickPhotoFile(imgFile);
    showToast('Image pasted! Click Save photo.');
  });

  const dropzone = document.getElementById('quick-photo-dropzone');
  if (dropzone) {
    dropzone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropzone.classList.add('dragover');
    });
    dropzone.addEventListener('dragleave', () => {
      dropzone.classList.remove('dragover');
    });
    dropzone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropzone.classList.remove('dragover');
      const file = e.dataTransfer?.files?.[0];
      if (file && (!file.type || file.type.startsWith('image/'))) {
        setQuickPhotoFile(file);
      }
    });
  }
  const existingGrid = document.getElementById('quick-photo-existing-grid');
  if (existingGrid) {
    existingGrid.addEventListener('click', (e) => {
      const btn = e.target?.closest?.('.cover-picker-option[data-photo-url]');
      if (!btn) return;
      const url = btn.getAttribute('data-photo-url');
      if (url) {
        attachQuickPhotoPath(url).catch((err) => showToast(err.message || 'Could not attach photo.', 'error'));
      }
    });
  }
}

function collectLocationImportedPhotos(locId, { activeUrl = null, includeParent = true } = {}) {
  const loc = state.locationMap.get(locId);
  if (!loc) return [];
  const seen = new Set();
  const list = [];
  const pushUrl = (rawUrl, thumbUrl, label) => {
    const url = sanitizePhotoUrl(rawUrl || '');
    if (!url || seen.has(url)) return;
    seen.add(url);
    list.push({ url, thumb: sanitizePhotoUrl(thumbUrl || '') || url, label: label || loc.name });
  };
  const resolvedActive = sanitizePhotoUrl(activeUrl !== null ? activeUrl : (loc.container_photo || ''));
  pushUrl(loc.container_photo, null, loc.name);
  if (Array.isArray(loc.photos)) {
    for (const p of loc.photos) pushUrl(p.photo_path, p.thumbnail_path, p.caption || loc.name);
  }
  pushUrl(loc.context_photo, null, loc.name);
  if (includeParent && loc.parent_id) {
    const parent = state.locationMap.get(loc.parent_id);
    if (parent && Array.isArray(parent.photos)) {
      for (const p of parent.photos) pushUrl(p.photo_path, p.thumbnail_path, p.caption || parent.name);
    }
  }
  return list.map(p => ({ ...p, isActive: Boolean(resolvedActive && p.url === resolvedActive) }));
}

export function openQuickPhotoModal(target) {
  bindQuickPhotoPasteAndDrop();
  state.quickPhotoTarget = target;
  document.getElementById('quick-photo-title').textContent = target.title || 'Add photo';
  const input = document.getElementById('quick-photo-input');
  if (input) input.value = '';
  setQuickPhotoFile(null);

  const existingWrap = document.getElementById('quick-photo-existing-wrap');
  const existingGrid = document.getElementById('quick-photo-existing-grid');
  if (existingWrap && existingGrid) {
    let candidates = [];
    if (target.kind === 'item') {
      const item = (state.items || []).find(it => Number(it.id) === Number(target.id));
      if (item && item.location_id) {
        candidates = collectLocationImportedPhotos(Number(item.location_id), {
          activeUrl: item.item_photo || '',
          includeParent: false,
        });
      }
    } else {
      candidates = collectLocationImportedPhotos(Number(target.id));
    }
    if (candidates.length > 0) {
      existingGrid.innerHTML = candidates.map((p) => `
        <button
          type="button"
          class="cover-picker-option ${p.isActive ? 'is-active' : ''}"
          data-photo-url="${escapeHtml(p.url)}"
          title="Use this photo"
        >
          <img src="${escapeHtml(p.thumb)}" alt="${escapeHtml(p.label)}">
          ${p.isActive ? '<span class="cover-picker-active-tag">Current</span>' : ''}
        </button>
      `).join('');
      existingWrap.style.display = 'block';
    } else {
      existingGrid.innerHTML = '';
      existingWrap.style.display = 'none';
    }
  }

  document.getElementById('quick-photo-modal')?.classList.add('open');
}

export function handleQuickPhotoSelected(e) {
  const file = e.target.files && e.target.files[0];
  setQuickPhotoFile(file || null);
}

export async function attachQuickPhotoPath(uploadedPath) {
  const target = state.quickPhotoTarget;
  if (!target || !uploadedPath) return;
  return withSubmitLock('quick-photo-attach', '', async () => {
    if (target.kind === 'item') {
      await apiPatch(`/api/v1/items/${target.id}`, { item_photo: uploadedPath }, 'Could not attach photo.');
    } else if (target.kind === 'location-photo') {
      await apiPost(
        `/api/v1/locations/${target.id}/photos/attach`,
        { photo_path: uploadedPath, photo_type: 'container' },
        'Could not attach photo.'
      );
    } else {
      const body = target.kind === 'spot' ? { context_photo: uploadedPath } : { container_photo: uploadedPath };
      await apiPatch(`/api/v1/locations/${target.id}`, body, 'Could not attach photo.');
    }
    setQuickPhotoFile(null);
    closeModal('quick-photo-modal');
    showToast('Photo saved!');
    await loadAllData();
  });
}

export async function submitQuickPhoto() {
  const file = pendingQuickPhotoFile || document.getElementById('quick-photo-input')?.files?.[0];
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
    markModalDirty('add-item-modal');
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
    markModalDirty('add-storage-modal');
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
    markModalDirty('add-storage-modal');
    showToast('Photo received from phone!');
  },
});

registerDesktopPhotoHandler('rapid-photo-input', {
  label: 'Scan with Camera',
  onPhotoReceived: (photo) => {
    const shouldAutoScan = rapidAutoScanNextPick;
    rapidAutoScanNextPick = false;
    revokeRapidBlobUrls();
    rapidSelectedFiles = [];
    rapidPhotoIds = [];
    rapidPhotoId = null;
    rapidRemotePhotoPath = sanitizePhotoUrl(photo.original_path || '') || null;
    const safeUrl = sanitizePhotoUrl(photo.thumbnail_path || photo.original_path || '');
    rapidPreviewUrls = safeUrl ? [safeUrl] : [];
    const filenameEl = document.getElementById('rapid-photo-filename');
    if (filenameEl) filenameEl.textContent = 'Photo from phone';
    renderRapidPhotoPreviews(rapidPreviewUrls);
    markModalDirty('drawer-rapid-entry-modal');
    syncRapidSubmitButtonLabel();
    showToast('Photo received from phone!');
    if (shouldAutoScan) {
      const locId = parseInt(document.getElementById('rapid-location-select')?.value, 10);
      if (locId) {
        runRapidAiScan();
      }
    }
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
  if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') {
    window.dispatchEvent(new CustomEvent('wims:more-opened'));
  }
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

// ============================================================================
// 7. SUB-CONTAINER BOX / KIT GROUPING & CREATION MODAL
// ============================================================================
let groupBoxModalState = {
  parentId: null,
  selectedItemIds: new Set(),
  parentPhotoUrl: null,
  createOnly: false,
};

function syncGroupBoxModalUI() {
  const targetSelect = document.getElementById('group-box-target-select');
  const isNewBox = !targetSelect || !targetSelect.value || targetSelect.value === '__new__';
  const nameField = document.getElementById('group-box-name-field');
  const photoRow = document.getElementById('group-box-photo-row');
  const submitBtn = document.getElementById('group-box-submit-btn');

  if (nameField) nameField.style.display = isNewBox ? 'flex' : 'none';
  if (photoRow) photoRow.style.display = (isNewBox && groupBoxModalState.parentPhotoUrl) ? 'flex' : 'none';

  const count = groupBoxModalState.selectedItemIds.size;
  if (submitBtn) {
    if (!isNewBox) {
      submitBtn.textContent = count > 0 ? `Move ${pluralize(count, 'item')} into Box` : 'Select items to move';
    } else if (count > 0) {
      submitBtn.textContent = `Create Box & Move ${pluralize(count, 'item')}`;
    } else {
      submitBtn.textContent = 'Create Box';
    }
  }
}

function renderGroupBoxItemsChecklist(looseItems) {
  const listEl = document.getElementById('group-box-items-list');
  if (!listEl) return;
  listEl.innerHTML = looseItems.map(item => {
    const safeId = Number(item.id);
    const checked = groupBoxModalState.selectedItemIds.has(safeId);
    const qty = item.quantity !== null && item.quantity !== undefined ? Number(item.quantity) : 1;
    const thumb = sanitizePhotoUrl(item.item_photo || '');
    return `
      <label class="group-box-item-row">
        <input
          type="checkbox"
          class="group-box-item-chk"
          data-item-id="${safeId}"
          ${checked ? 'checked' : ''}
          onchange="toggleGroupBoxItem(${safeId}, this.checked)"
        >
        ${thumb ? `<img src="${escapeHtml(thumb)}" alt="${escapeHtml(item.name)}" class="group-box-item-thumb">` : ''}
        <span class="group-box-item-name">${escapeHtml(item.name)}</span>
        <span class="group-box-item-qty">×${qty}</span>
      </label>
    `;
  }).join('');
}

export function openGroupIntoBoxModal(parentLocationId, options = {}) {
  const parentId = Number(parentLocationId);
  const parentLoc = state.locationMap.get(parentId);
  if (!parentLoc) {
    showToast('Location not found.', 'error');
    return;
  }

  const createOnly = Boolean(options.createOnly);
  const looseItems = state.items.filter(i => i.location_id === parentId);
  const childBoxes = (parentLoc.children || []).filter(c => !isUnknownPlaceholder(c));
  const parentPhotoUrl = sanitizePhotoUrl(
    parentLoc.photos?.[0]?.photo_path || parentLoc.container_photo || ''
  ) || null;

  groupBoxModalState = {
    parentId,
    selectedItemIds: new Set(createOnly ? [] : looseItems.map(i => Number(i.id))),
    parentPhotoUrl,
    createOnly,
  };

  const titleEl = document.getElementById('group-box-modal-title');
  if (titleEl) {
    titleEl.textContent = (createOnly || looseItems.length === 0)
      ? 'Add a Box / Kit'
      : 'Put items in a Box / Kit';
  }
  const parentNameEl = document.getElementById('group-box-parent-name');
  if (parentNameEl) {
    parentNameEl.textContent = formatHumanPath(parentLoc.full_path || parentLoc.name);
  }

  const targetField = document.getElementById('group-box-target-field');
  const targetSelect = document.getElementById('group-box-target-select');
  if (targetSelect) {
    targetSelect.innerHTML = '';
    const newOpt = document.createElement('option');
    newOpt.value = '__new__';
    newOpt.textContent = '+ Create a new Box / Kit';
    targetSelect.appendChild(newOpt);
    if (!createOnly && childBoxes.length > 0) {
      for (const box of childBoxes) {
        const opt = document.createElement('option');
        opt.value = String(box.id);
        opt.textContent = box.name;
        targetSelect.appendChild(opt);
      }
      if (targetField) targetField.style.display = 'flex';
    } else if (targetField) {
      targetField.style.display = 'none';
    }
    targetSelect.value = '__new__';
  }

  const nameInput = document.getElementById('group-box-name-input');
  if (nameInput) {
    nameInput.value = '';
    const cats = Array.from(new Set(looseItems.map(i => (i.category || '').trim()).filter(Boolean)));
    nameInput.placeholder = cats.length === 1
      ? `e.g. Assorted ${cats[0]}s, ${cats[0]} Box`
      : 'e.g. Assorted Voltage Regulators, M3 Fasteners Box';
  }

  const usePhotoChk = document.getElementById('group-box-use-photo');
  if (usePhotoChk) {
    usePhotoChk.checked = Boolean(parentPhotoUrl && !createOnly);
  }

  const scanSection = document.getElementById('group-box-scan-section');
  if (scanSection) {
    scanSection.style.display = (createOnly || looseItems.length === 0) ? 'flex' : 'none';
  }

  const itemsSection = document.getElementById('group-box-items-section');
  if (itemsSection) {
    if (looseItems.length > 0) {
      itemsSection.style.display = 'flex';
      renderGroupBoxItemsChecklist(looseItems);
    } else {
      itemsSection.style.display = 'none';
    }
  }

  syncGroupBoxModalUI();
  markModalClean('group-into-box-modal');
  document.getElementById('group-into-box-modal')?.classList.add('open');
  setTimeout(() => nameInput?.focus(), 40);
}

export function handleGroupBoxTargetChange() {
  syncGroupBoxModalUI();
}

export function handleGroupBoxNameKeydown(e) {
  if (e && e.key === 'Enter') {
    e.preventDefault();
    submitGroupIntoBox();
  }
}

export function toggleGroupBoxItem(itemId, checked) {
  const id = Number(itemId);
  if (checked) {
    groupBoxModalState.selectedItemIds.add(id);
  } else {
    groupBoxModalState.selectedItemIds.delete(id);
  }
  syncGroupBoxModalUI();
}

export function toggleAllGroupBoxItems(checked) {
  const parentId = groupBoxModalState.parentId;
  const looseItems = state.items.filter(i => i.location_id === parentId);
  groupBoxModalState.selectedItemIds = new Set(checked ? looseItems.map(i => Number(i.id)) : []);
  renderGroupBoxItemsChecklist(looseItems);
  syncGroupBoxModalUI();
}

export async function submitGroupIntoBox() {
  const parentId = groupBoxModalState.parentId;
  if (!parentId) return;

  const targetSelect = document.getElementById('group-box-target-select');
  const targetVal = targetSelect?.value || '__new__';
  const isNewBox = targetVal === '__new__';
  const boxName = (document.getElementById('group-box-name-input')?.value || '').trim();
  const selectedIds = Array.from(groupBoxModalState.selectedItemIds);
  const usePhoto = Boolean(document.getElementById('group-box-use-photo')?.checked);
  const boxPhoto = (isNewBox && usePhoto && groupBoxModalState.parentPhotoUrl)
    ? groupBoxModalState.parentPhotoUrl
    : null;

  if (isNewBox && !boxName) {
    showToast('Enter a name for the box or kit.', 'error');
    document.getElementById('group-box-name-input')?.focus();
    return;
  }
  if (!isNewBox && selectedIds.length === 0) {
    showToast('Select at least one loose item to move into that box.', 'error');
    return;
  }

  return withSubmitLock('group-box-submit-btn', 'Saving...', async () => {
    try {
      if (selectedIds.length > 0) {
        const payload = {
          parent_location_id: parentId,
          item_ids: selectedIds,
        };
        if (isNewBox) {
          payload.box_name = boxName;
          if (boxPhoto) payload.box_photo = boxPhoto;
        } else {
          payload.target_box_id = parseInt(targetVal, 10);
        }
        const res = await apiPost('/api/v1/items/group-into-box', payload, 'Could not move items into box.');
        if (res.box?.id) expandSubBox(res.box.id);
        closeModal('group-into-box-modal', true);
        showToast(`Moved ${pluralize(res.moved_count, 'item')} into "${res.box.name}"!`);
        await loadAllData();
      } else {
        const payload = {
          name: boxName,
          location_type: 'box',
          parent_id: parentId,
        };
        if (boxPhoto) payload.container_photo = boxPhoto;
        const createdBox = await apiPost('/api/v1/locations', payload, 'Could not create box.');
        if (createdBox?.id) expandSubBox(createdBox.id);
        closeModal('group-into-box-modal', true);
        showToast(`Added "${createdBox.name}"!`);
        await loadAllData();
      }
    } catch (err) {
      showToast(err.message || 'Could not save box.', 'error');
    }
  });
}

export async function startBoxPhoneScanFromModal() {
  const parentId = groupBoxModalState.parentId;
  if (!parentId) return;
  const typedName = (document.getElementById('group-box-name-input')?.value || '').trim();
  closeModal('group-into-box-modal', true);
  await openRapidEntryModal(parentId, { saveMode: 'box', initialBoxName: typedName });
  startRapidPhoneCapture();
}

export async function startBoxUploadScanFromModal() {
  const parentId = groupBoxModalState.parentId;
  if (!parentId) return;
  const typedName = (document.getElementById('group-box-name-input')?.value || '').trim();
  closeModal('group-into-box-modal', true);
  const opened = openRapidEntryModal(parentId, { saveMode: 'box', initialBoxName: typedName });
  // Open the file picker synchronously so the browser still honors the user gesture.
  triggerRapidPhotoAndScan();
  await opened;
}


