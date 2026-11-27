/**
 * Desktop Guided Storage Unit Setup Wizard & Mobile Handoff Launcher
 * Phases:
 *  1. choose -> Pick Mobile QR Guided Setup vs Manual Keyboard/Mouse
 *  2. phone  -> Display QR code & live phone setup progress
 *  3. review -> AI-scan drawer photos, edit/add/remove items per drawer, finish into Unit view
 */
import { apiDelete, apiGet, apiPatch, apiPost, apiUpload } from './api.js';
import { createSubBoxFromDetectedRow } from './box-actions.js';
import { closeModal, escapeHtml, escapeJsStr, openLightbox, pluralize, registerModalForceCloseHook, sanitizePhotoUrl, showToast } from './dom.js';
import { lucideIcon } from './icons.js';
import { openAddStorageModal } from './modals.js';
import { compressImageFile } from './photo-compress.js';
import { navToStorageUnit } from './router.js';
import { loadAllData, state } from './state.js';
import { ensureVisionModelReady } from './vision-guard.js';

const AI_SCAN_TIMEOUT_MS = 130000;
const POLL_INTERVAL_MS = 850;

const wizardState = {
  open: false,
  phase: 'choose', // 'choose' | 'phone' | 'review'
  roomId: null,
  existingUnitId: null,
  token: null,
  mobileUrl: '',
  qrUrl: '',
  baseQrUrl: '',
  useHttpQrFallback: false,
  session: null,
  sessionSeq: 0,
  pollTimer: null,
  pollInFlight: false,

  // Phase 3 review state
  reviewDrawers: [], // Array<DrawerReviewState>
  activeDrawerIdx: 0,
  visionMeta: null,
  selectedProvider: '',
  batchScanning: false,
  batchProgressText: '',
  scanAbortController: null,
  finishing: false,
};

let uidCounter = 1;
function nextUid() {
  uidCounter += 1;
  return `usw-item-${uidCounter}`;
}

function stopPolling() {
  if (wizardState.pollTimer) {
    clearInterval(wizardState.pollTimer);
    wizardState.pollTimer = null;
  }
  wizardState.pollInFlight = false;
}

function abortActiveScans() {
  if (wizardState.scanAbortController) {
    try { wizardState.scanAbortController.abort(); } catch {}
    wizardState.scanAbortController = null;
  }
  wizardState.batchScanning = false;
  wizardState.batchProgressText = '';
  const modal = document.getElementById('unit-setup-modal');
  if (modal?.dataset) modal.dataset.scanning = 'false';
}

registerModalForceCloseHook('unit-setup-modal', () => {
  wizardState.open = false;
  wizardState.sessionSeq += 1;
  stopPolling();
  abortActiveScans();
});

export function closeUnitSetupWizard() {
  closeModal('unit-setup-modal', true);
}

function getRoomDisplayName(roomId) {
  if (!roomId) return 'Needs a home';
  const loc = state.locationMap.get(Number(roomId));
  return loc ? loc.name : 'Selected space';
}

function normalizeToken(val) {
  return String(val || '').toLowerCase().replace(/[^a-z0-9]/g, '');
}

function buildReviewDrawerState(drawerObj) {
  const photos = Array.isArray(drawerObj.photos) ? drawerObj.photos : [];
  return {
    id: Number(drawerObj.id),
    coord: drawerObj.coord || '',
    description: drawerObj.description || '',
    originalName: drawerObj.name || 'Drawer',
    draftName: drawerObj.name || 'Drawer',
    photos,
    scanning: false,
    saving: false,
    scanned: false,
    saved: false,
    dirty: false,
    aiMessage: '',
    items: [], // { uid, savedItemId, included, name, partNumber, quantity, category, notes, cropPhotoPath, dirty }
    quickAddDraft: '',
  };
}

async function preloadExistingItemsForReview(seq) {
  for (const d of wizardState.reviewDrawers) {
    try {
      const existing = await apiGet(`/api/v1/locations/${d.id}/items`);
      if (!wizardState.open || wizardState.sessionSeq !== seq) return;
      if (Array.isArray(existing) && existing.length > 0 && d.items.length === 0) {
        d.items = existing.map((it) => ({
          uid: nextUid(),
          savedItemId: Number(it.id),
          savedBoxId: null,
          saveAsBox: false,
          included: true,
          name: it.name || '',
          partNumber: it.serial_or_part_number || '',
          quantity: it.quantity !== null && it.quantity !== undefined ? Number(it.quantity) : 1,
          category: it.category || '',
          notes: it.description || '',
          cropPhotoPath: sanitizePhotoUrl(it.item_photo || ''),
          dirty: false,
        }));
        d.saved = true;
      }
    } catch {
      // Ignore preload errors
    }
  }
}

async function loadVisionProviders() {
  try {
    wizardState.visionMeta = await apiGet('/api/v1/photos/vision-providers');
    if (!wizardState.selectedProvider && wizardState.visionMeta?.default_provider) {
      wizardState.selectedProvider = wizardState.visionMeta.default_provider;
    }
  } catch {
    wizardState.visionMeta = null;
  }
}

export function openUnitSetupWizard(roomId = null) {
  stopPolling();
  abortActiveScans();
  wizardState.sessionSeq += 1;
  wizardState.open = true;
  wizardState.phase = 'choose';
  wizardState.roomId = roomId ? Number(roomId) : null;
  wizardState.existingUnitId = null;
  wizardState.token = null;
  wizardState.mobileUrl = '';
  wizardState.qrUrl = '';
  wizardState.baseQrUrl = '';
  wizardState.useHttpQrFallback = false;
  wizardState.session = null;
  wizardState.reviewDrawers = [];
  wizardState.activeDrawerIdx = 0;
  wizardState.finishing = false;

  renderWizard();
  document.getElementById('unit-setup-modal')?.classList.add('open');
}

export async function openExistingUnitCaptureWizard(unitId) {
  const safeId = Number(unitId);
  if (!safeId) return;
  const unit = state.locationMap.get(safeId);
  stopPolling();
  abortActiveScans();
  wizardState.sessionSeq += 1;
  wizardState.open = true;
  wizardState.phase = 'phone';
  wizardState.roomId = unit?.parent_id ? Number(unit.parent_id) : null;
  wizardState.existingUnitId = safeId;
  wizardState.token = null;
  wizardState.mobileUrl = '';
  wizardState.qrUrl = '';
  wizardState.baseQrUrl = '';
  wizardState.useHttpQrFallback = false;
  wizardState.session = null;
  wizardState.reviewDrawers = [];
  wizardState.activeDrawerIdx = 0;
  wizardState.finishing = false;

  document.getElementById('unit-setup-modal')?.classList.add('open');
  await startPhoneQrSession();
}

export async function launchMobileUnitSetup(roomId = null) {
  try {
    const payload = roomId ? { room_id: Number(roomId) } : {};
    const sess = await apiPost('/api/v1/unit-setup/sessions', payload, 'Could not start mobile setup.');
    window.location.href = `/setup.html?token=${encodeURIComponent(sess.token)}&return=app`;
  } catch (err) {
    showToast(err?.message || 'Could not start mobile setup.', 'error');
  }
}

async function startPhoneQrSession() {
  const seq = ++wizardState.sessionSeq;
  wizardState.phase = 'phone';
  wizardState.session = null;
  wizardState.qrUrl = '';
  wizardState.mobileUrl = '';
  renderWizard();

  try {
    const payload = wizardState.existingUnitId
      ? { unit_id: wizardState.existingUnitId }
      : (wizardState.roomId ? { room_id: wizardState.roomId } : {});
    const created = await apiPost('/api/v1/unit-setup/sessions', payload, 'Could not start QR setup session.');
    if (seq !== wizardState.sessionSeq || !wizardState.open) return;

    wizardState.token = created.token;
    wizardState.mobileUrl = created.mobile_url;
    wizardState.baseQrUrl = created.qr_url;
    wizardState.useHttpQrFallback = false;
    wizardState.qrUrl = `${created.qr_url}?t=${Date.now()}`;
    wizardState.session = created;
    renderWizard();
    startSessionPolling(created.token, seq);
  } catch (err) {
    if (seq !== wizardState.sessionSeq) return;
    showToast(err?.message || 'Could not start QR setup session.', 'error');
    wizardState.phase = 'choose';
    renderWizard();
  }
}

function startSessionPolling(tokenForPoll, seq) {
  stopPolling();
  const timer = setInterval(async () => {
    if (!wizardState.open || wizardState.phase !== 'phone' || wizardState.sessionSeq !== seq) {
      clearInterval(timer);
      if (wizardState.pollTimer === timer) wizardState.pollTimer = null;
      return;
    }
    if (wizardState.pollInFlight) return;
    wizardState.pollInFlight = true;
    try {
      const data = await apiGet(`/api/v1/unit-setup/sessions/${encodeURIComponent(tokenForPoll)}`);
      if (!wizardState.open || wizardState.phase !== 'phone' || wizardState.sessionSeq !== seq) return;
      wizardState.session = data;

      if (data.status === 'ready_for_review') {
        stopPolling();
        await enterReviewPhase();
        return;
      }
      if (data.status === 'expired') {
        stopPolling();
      }
      renderWizard();
    } catch (err) {
      if (err?.status === 404 || err?.status === 410) {
        stopPolling();
        if (wizardState.session) wizardState.session.status = 'expired';
        renderWizard();
      }
    } finally {
      wizardState.pollInFlight = false;
    }
  }, POLL_INTERVAL_MS);
  wizardState.pollTimer = timer;
}

async function enterReviewPhase() {
  stopPolling();
  const sess = wizardState.session;
  if (!sess || !sess.unit_id) return;

  const drawers = Array.isArray(sess.drawers) ? sess.drawers : [];
  if (drawers.length > 0) {
    wizardState.reviewDrawers = drawers.map(buildReviewDrawerState);
  } else if (sess.unit) {
    wizardState.reviewDrawers = [buildReviewDrawerState(sess.unit)];
  } else {
    wizardState.reviewDrawers = [];
  }
  wizardState.activeDrawerIdx = 0;
  wizardState.phase = 'review';
  renderWizard();

  const reviewSeq = wizardState.sessionSeq;
  await Promise.all([loadVisionProviders(), preloadExistingItemsForReview(reviewSeq)]);
  if (!wizardState.open || wizardState.sessionSeq !== reviewSeq) return;
  renderWizard();
}

function mergeDetectedItemsIntoDrawer(drawerState, detectedItems) {
  const existingKeys = new Set(
    drawerState.items.map((it) => normalizeToken(it.name)).filter(Boolean)
  );
  let addedCount = 0;
  for (const det of detectedItems || []) {
    const cleanName = String(det?.name || '').trim();
    if (!cleanName) continue;
    const key = normalizeToken(cleanName);
    if (key && existingKeys.has(key)) continue;
    if (key) existingKeys.add(key);

    const qty = det.estimated_qty !== null && det.estimated_qty !== undefined
      ? Math.max(1, Number(det.estimated_qty) || 1)
      : 1;
    drawerState.items.push({
      uid: nextUid(),
      savedItemId: null,
      savedBoxId: null,
      saveAsBox: false,
      included: true,
      name: cleanName,
      partNumber: det.part_number ? String(det.part_number).trim() : '',
      quantity: qty,
      category: det.category ? String(det.category).trim() : '',
      notes: det.brief_specs ? String(det.brief_specs).trim() : '',
      cropPhotoPath: sanitizePhotoUrl(det.crop_photo_path || ''),
      dirty: true,
    });
    addedCount += 1;
  }
  if (addedCount > 0) {
    drawerState.dirty = true;
    drawerState.saved = false;
  }
  return addedCount;
}

async function scanSingleDrawer(drawerIdx, signal, skipReadinessCheck = false) {
  const d = wizardState.reviewDrawers[drawerIdx];
  if (!d) return;
  if (!d.photos.length) {
    d.aiMessage = 'No photos in this drawer yet — add items manually or attach a photo.';
    return;
  }

  let provider = wizardState.selectedProvider || wizardState.visionMeta?.default_provider || '';
  if (!skipReadinessCheck) {
    const readiness = await ensureVisionModelReady({
      provider,
      onSwitchProvider: (nextProv) => {
        wizardState.selectedProvider = nextProv;
        renderWizard();
      },
      onRetry: async (nextProv) => {
        if (nextProv) wizardState.selectedProvider = nextProv;
        const controller = new AbortController();
        wizardState.scanAbortController = controller;
        await scanSingleDrawer(drawerIdx, controller.signal, true);
        wizardState.scanAbortController = null;
      },
      onManual: () => {
        document.getElementById('usw-quick-add-input')?.focus();
      },
    });
    if (!readiness.ok) {
      if (readiness.meta) wizardState.visionMeta = readiness.meta;
      renderWizard();
      return;
    }
    provider = readiness.provider;
    if (readiness.meta) wizardState.visionMeta = readiness.meta;
  }

  d.scanning = true;
  d.aiMessage = '';
  renderWizard();
  let totalDetected = 0;
  let lastNotice = '';

  try {
    const photoIds = d.photos.map((p) => Number(p.id)).filter(Boolean);
    for (let offset = 0; offset < photoIds.length; offset += 8) {
      if (signal?.aborted) break;
      const chunk = photoIds.slice(offset, offset + 8);
      const fd = new FormData();
      fd.append('photo_ids', chunk.join(','));
      if (provider) fd.append('provider', provider);
      const hintParts = [d.draftName, d.description].map((s) => String(s || '').trim()).filter(Boolean);
      if (hintParts.length > 0) {
        fd.append('context_hint', hintParts.join(' — ').slice(0, 280));
      }

      const res = await apiUpload(
        `/api/v1/locations/${d.id}/analyze-photo`,
        fd,
        'AI scan failed.',
        { timeoutMs: AI_SCAN_TIMEOUT_MS, signal }
      );
      if (res?.items?.length) {
        mergeDetectedItemsIntoDrawer(d, res.items);
        totalDetected += res.items.length;
      }
      if (res?.message) lastNotice = res.message;
    }
    d.scanned = true;
    if (totalDetected === 0) {
      d.aiMessage = lastNotice || 'No items found automatically — add items below.';
    } else {
      d.aiMessage = lastNotice || `Found ${pluralize(totalDetected, 'item')}. Review or edit below.`;
    }
  } catch (err) {
    if (err?.name === 'UserAbortError') {
      d.aiMessage = 'Scan stopped.';
    } else {
      d.aiMessage = err?.message || 'Could not scan photo — you can still enter items manually.';
    }
  } finally {
    d.scanning = false;
    renderWizard();
  }
}

async function scanAllDrawers({ autoSave = false } = {}) {
  if (wizardState.batchScanning) return;
  const targets = wizardState.reviewDrawers
    .map((d, idx) => ({ d, idx }))
    .filter(({ d }) => d.photos.length > 0);

  if (!targets.length) {
    showToast('No drawer photos to scan yet.', 'info');
    return;
  }

  const readiness = await ensureVisionModelReady({
    provider: wizardState.selectedProvider || wizardState.visionMeta?.default_provider || '',
    onSwitchProvider: (nextProv) => {
      wizardState.selectedProvider = nextProv;
      renderWizard();
    },
    onRetry: (nextProv) => {
      if (nextProv) wizardState.selectedProvider = nextProv;
      return scanAllDrawers({ autoSave });
    },
    onManual: () => {
      document.getElementById('usw-quick-add-input')?.focus();
    },
  });
  if (!readiness.ok) {
    if (readiness.meta) wizardState.visionMeta = readiness.meta;
    renderWizard();
    return;
  }
  wizardState.selectedProvider = readiness.provider;
  if (readiness.meta) wizardState.visionMeta = readiness.meta;

  const controller = new AbortController();
  wizardState.scanAbortController = controller;
  wizardState.batchScanning = true;
  const modal = document.getElementById('unit-setup-modal');
  if (modal?.dataset) modal.dataset.scanning = 'true';

  let savedDrawers = 0;
  let savedItems = 0;
  try {
    for (let i = 0; i < targets.length; i++) {
      if (controller.signal.aborted) break;
      const { d, idx } = targets[i];
      wizardState.batchProgressText = `${autoSave ? 'Scanning & saving' : 'Scanning'} ${d.draftName} (${i + 1} of ${targets.length})…`;
      wizardState.activeDrawerIdx = idx;
      renderWizard();
      await scanSingleDrawer(idx, controller.signal, true);
      if (autoSave && !controller.signal.aborted && d.items.some((it) => it.included && (!it.savedItemId || it.dirty))) {
        const ok = await saveDrawerItems(idx);
        if (ok) {
          savedDrawers += 1;
          savedItems += d.items.filter((it) => it.included && it.savedItemId).length;
        }
      }
    }
    if (!controller.signal.aborted) {
      if (autoSave) {
        showToast(`Saved ${pluralize(savedItems, 'item')} across ${pluralize(savedDrawers, 'drawer')}.`);
      } else {
        showToast(`Finished scanning ${pluralize(targets.length, 'drawer')}.`);
      }
    }
  } finally {
    wizardState.batchScanning = false;
    wizardState.batchProgressText = '';
    wizardState.scanAbortController = null;
    if (modal?.dataset) modal.dataset.scanning = 'false';
    renderWizard();
  }
}

async function saveDrawerItems(drawerIdx) {
  const d = wizardState.reviewDrawers[drawerIdx];
  if (!d || d.saving) return true;

  d.saving = true;
  renderWizard();

  try {
    const cleanDrawerName = (d.draftName || '').trim();
    if (cleanDrawerName && cleanDrawerName !== d.originalName) {
      await apiPatch(`/api/v1/locations/${d.id}`, { name: cleanDrawerName }, 'Could not rename drawer.');
      d.originalName = cleanDrawerName;
    }

    // 1. Delete saved items/boxes that are now unchecked, converted, or have blank names
    const boxesToDelete = d.items.filter(
      (it) => it.savedBoxId && (!it.included || !it.saveAsBox || !String(it.name || '').trim())
    );
    for (const it of boxesToDelete) {
      await apiDelete(`/api/v1/locations/${it.savedBoxId}`, 'Could not remove box.');
      it.savedBoxId = null;
      it.dirty = true;
    }
    const toDelete = d.items.filter(
      (it) => it.savedItemId && (!it.included || it.saveAsBox || !String(it.name || '').trim())
    );
    for (const it of toDelete) {
      await apiDelete(`/api/v1/items/${it.savedItemId}`, 'Could not remove item.');
      it.savedItemId = null;
      it.dirty = false;
    }

    // 2. Create or update rows marked as Box / Container
    const boxRows = d.items.filter(
      (it) => it.saveAsBox && it.included && String(it.name || '').trim()
    );
    for (const it of boxRows) {
      if (!it.savedBoxId) {
        const createdBox = await createSubBoxFromDetectedRow(d.id, it);
        if (createdBox?.id) {
          it.savedBoxId = Number(createdBox.id);
          it.dirty = false;
        }
      } else if (it.dirty) {
        await apiPatch(
          `/api/v1/locations/${it.savedBoxId}`,
          {
            name: String(it.name).trim().slice(0, 120),
            description: String(it.notes || '').trim().slice(0, 2000) || null,
          },
          'Could not update box.'
        );
        it.dirty = false;
      }
    }

    // 3. Patch existing saved items that were modified
    const toPatch = d.items.filter(
      (it) => !it.saveAsBox && it.savedItemId && it.included && it.dirty && String(it.name || '').trim()
    );
    for (const it of toPatch) {
      const qtyVal = it.quantity === '' || it.quantity === null || it.quantity === undefined
        ? null
        : Math.max(0, Number(it.quantity) || 0);
      await apiPatch(
        `/api/v1/items/${it.savedItemId}`,
        {
          name: String(it.name).trim(),
          serial_or_part_number: String(it.partNumber || '').trim().slice(0, 120) || null,
          quantity: qtyVal,
          category: String(it.category || '').trim() || null,
          description: String(it.notes || '').trim() || null,
        },
        'Could not update item.'
      );
      it.dirty = false;
    }

    // 4. Create new checked items in bulk
    const toCreate = d.items.filter(
      (it) => !it.saveAsBox && !it.savedItemId && !it.savedBoxId && it.included && String(it.name || '').trim()
    );
    if (toCreate.length > 0) {
      const payloadItems = toCreate.map((it) => {
        const qtyVal = it.quantity === '' || it.quantity === null || it.quantity === undefined
          ? 1
          : Math.max(0, Number(it.quantity) || 0);
        const entry = {
          name: String(it.name).trim().slice(0, 160),
          serial_or_part_number: String(it.partNumber || '').trim().slice(0, 120) || null,
          quantity: qtyVal,
          category: String(it.category || '').trim().slice(0, 80) || null,
          description: String(it.notes || '').trim().slice(0, 20000) || null,
          location_id: d.id,
        };
        const safeCrop = sanitizePhotoUrl(it.cropPhotoPath || '');
        if (safeCrop) entry.item_photo = safeCrop;
        return entry;
      });
      const created = await apiPost(
        '/api/v1/items/bulk',
        { location_id: d.id, save_mode: 'separate', items: payloadItems },
        'Could not save drawer items.'
      );
      if (Array.isArray(created)) {
        toCreate.forEach((row, i) => {
          if (created[i]?.id) {
            row.savedItemId = Number(created[i].id);
            row.dirty = false;
          }
        });
      }
    }

    d.items = d.items.filter((it) => it.included && String(it.name || '').trim());
    d.dirty = false;
    d.saved = true;
    showToast(`Saved ${d.originalName}`);
    return true;
  } catch (err) {
    showToast(err?.message || 'Could not save drawer.', 'error');
    return false;
  } finally {
    d.saving = false;
    renderWizard();
  }
}

async function finishWizard() {
  if (wizardState.finishing) return;
  wizardState.finishing = true;
  renderWizard();

  try {
    // Save any drawers that have unsaved changes or unsaved included items
    for (let i = 0; i < wizardState.reviewDrawers.length; i++) {
      const d = wizardState.reviewDrawers[i];
      const hasUnsaved = d.dirty
        || d.draftName.trim() !== d.originalName
        || d.items.some((it) => it.included && (!it.savedItemId || it.dirty) && String(it.name || '').trim());
      if (hasUnsaved) {
        const ok = await saveDrawerItems(i);
        if (!ok) {
          wizardState.finishing = false;
          renderWizard();
          return;
        }
      }
    }

    if (wizardState.token) {
      try {
        await apiPatch(`/api/v1/unit-setup/sessions/${encodeURIComponent(wizardState.token)}/progress`, {
          status: 'completed',
        });
      } catch {}
    }

    const unitId = wizardState.session?.unit_id;
    closeUnitSetupWizard();
    await loadAllData();
    showToast('Storage unit setup complete!');
    if (unitId) {
      navToStorageUnit(Number(unitId), true);
    }
  } finally {
    wizardState.finishing = false;
  }
}

function getDrawerStatusPill(d) {
  if (d.scanning) return { label: 'Scanning…', cls: 'is-scanning' };
  if (d.saved && !d.dirty) {
    const cnt = d.items.filter((it) => it.included).length;
    return { label: cnt > 0 ? `Saved (${cnt})` : 'Saved ✓', cls: 'is-saved' };
  }
  if (d.items.length > 0) {
    return { label: `${d.items.length} ready`, cls: 'is-ready' };
  }
  if (!d.photos.length) return { label: 'No photos', cls: 'is-muted' };
  return { label: 'Not scanned', cls: 'is-pending' };
}

function renderPhase1Choose() {
  const rooms = state.namedRooms || [];
  return {
    title: 'Add a Storage Container',
    subtitle: 'Dresser, cabinet, desk, closet, shelving, or parts organizer',
    body: `
      <div class="form-field" style="margin-bottom: 1rem;">
        <label for="usw-room-select">Space</label>
        <select id="usw-room-select" class="form-select">
          <option value="">Decide later (Needs a home)</option>
          ${rooms.map((r) => `
            <option value="${Number(r.id)}" ${Number(wizardState.roomId) === Number(r.id) ? 'selected' : ''}>
              ${escapeHtml(r.name)}
            </option>
          `).join('')}
        </select>
      </div>

      <div class="usw-choice-grid">
        <button type="button" id="usw-choose-phone-btn" class="usw-choice-card is-recommended">
          <span class="usw-choice-badge">Recommended</span>
          <span class="usw-choice-icon">${lucideIcon('qrCode', '', 28)}</span>
          <strong class="usw-choice-title">Use Phone (QR Guided Setup)</strong>
          <span class="usw-choice-desc">
            Scan a QR code to name your dresser/cabinet, snap an exterior photo, take 1 or more photos per drawer, then AI-scan &amp; edit items back here.
          </span>
        </button>

        <button type="button" id="usw-choose-manual-btn" class="usw-choice-card">
          <span class="usw-choice-icon">${lucideIcon('pencil', '', 26)}</span>
          <strong class="usw-choice-title">Add Manually (Keyboard &amp; Mouse)</strong>
          <span class="usw-choice-desc">
            Set up the container, numbered drawers, or custom grid layout directly on your computer.
          </span>
        </button>
      </div>
    `,
    footer: `
      <button type="button" class="btn btn-secondary" id="usw-cancel-btn">Cancel</button>
    `,
  };
}

function renderPhase2Phone() {
  const sess = wizardState.session;
  const st = sess?.status || 'waiting_for_phone';
  const unit = sess?.unit;
  const drawers = Array.isArray(sess?.drawers) ? sess.drawers : [];
  const isExpired = st === 'expired';

  let statusHeadline = 'Waiting for your phone…';
  let statusSub = 'Point your phone camera at the QR code to open Guided Setup.';
  if (st === 'creating_unit') {
    statusHeadline = 'Phone connected!';
    statusSub = 'Setting up the storage unit name and compartments on your phone…';
  } else if (st === 'capturing_drawers') {
    statusHeadline = `Adding photos to ${sess?.unit_name || 'storage unit'}…`;
    statusSub = 'Keep snapping drawer photos on your phone, or click Continue when ready to scan & edit items.';
  } else if (isExpired) {
    statusHeadline = 'QR code expired';
    statusSub = 'Generate a fresh QR code to continue on your phone.';
  }

  const isHttpsPrimary = wizardState.mobileUrl.startsWith('https://');
  let displayedUrl = wizardState.mobileUrl;
  let displayedQrUrl = wizardState.qrUrl;
  if (isHttpsPrimary && wizardState.useHttpQrFallback && wizardState.token) {
    try {
      const u = new URL(wizardState.mobileUrl);
      const httpBase = `http://${u.hostname}:${window.location.port || '8000'}`;
      displayedUrl = `${httpBase}/setup.html?token=${wizardState.token}`;
      const qrBase = wizardState.baseQrUrl || `/api/v1/unit-setup/sessions/${encodeURIComponent(wizardState.token)}/qr`;
      displayedQrUrl = `${qrBase}?base_url=${encodeURIComponent(httpBase)}`;
    } catch {
      displayedUrl = wizardState.mobileUrl;
      displayedQrUrl = wizardState.qrUrl;
    }
  }

  return {
    title: `Mobile Guided Setup — ${getRoomDisplayName(wizardState.roomId)}`,
    subtitle: 'Set up the unit and snap drawer photos on your phone; everything syncs here live.',
    body: `
      <div class="usw-phone-layout">
        <div class="usw-qr-col">
          <div class="phone-qr-frame">
            ${displayedQrUrl && !isExpired
              ? `<img src="${escapeHtml(displayedQrUrl)}" alt="Scan with your phone camera" style="display: block;">`
              : `<div class="usw-qr-placeholder">${isExpired ? 'Expired' : 'Generating…'}</div>`
            }
          </div>
          ${displayedUrl ? `
            <div class="usw-url-row">
              <span class="phone-qr-url">${escapeHtml(displayedUrl)}</span>
              <button type="button" id="usw-copy-url-btn" class="btn btn-secondary btn-xs">Copy link</button>
            </div>
            ${isHttpsPrimary ? `
              <p class="usw-live-status-sub" style="margin-top: 0.45rem; font-size: 0.78rem;">
                Phone cameras often show only the IP (e.g. 10.0.0.126) in the QR preview — tap it to open. First time on HTTPS: tap Advanced → Proceed (or Show Details → visit this website).
              </p>
              <button type="button" id="usw-toggle-http-qr-btn" class="btn btn-secondary btn-xs" style="margin-top: 0.35rem;">
                ${wizardState.useHttpQrFallback ? 'Switch Back to Live Camera QR (HTTPS)' : 'Trouble with HTTPS? Use Plain HTTP QR'}
              </button>
            ` : ''}
          ` : ''}
          ${isExpired ? `
            <button type="button" id="usw-refresh-qr-btn" class="btn btn-primary btn-sm">Generate New QR</button>
          ` : ''}
        </div>

        <div class="usw-live-col">
          <div class="usw-live-status-card ${isExpired ? 'is-expired' : ''}">
            <div class="usw-live-status-head">
              ${!isExpired ? '<span class="usw-pulse-dot" aria-hidden="true"></span>' : ''}
              <strong>${escapeHtml(statusHeadline)}</strong>
            </div>
            <p class="usw-live-status-sub">${escapeHtml(statusSub)}</p>
          </div>

          ${unit ? `
            <div class="usw-live-unit-card">
              <div class="usw-live-unit-head">
                ${sanitizePhotoUrl(unit.container_photo || '')
                  ? `<img class="usw-live-unit-thumb" src="${escapeHtml(sanitizePhotoUrl(unit.container_photo))}" alt="${escapeHtml(unit.name)}">`
                  : `<div class="usw-live-unit-icon">${lucideIcon('archive', '', 20)}</div>`
                }
                <div>
                  <div class="usw-live-unit-name">${unit.label ? `<span class="box-label-badge">${escapeHtml(unit.label)}</span> ` : ''}${escapeHtml(unit.name)}</div>
                  <div class="usw-live-unit-meta">
                    ${drawers.length > 0 ? pluralize(drawers.length, 'compartment') : 'Single Box / Container'}
                    ${unit.container_photo ? ' · Exterior photo ✓' : ''}
                    · <strong>${
                      drawers.length > 0
                        ? `${pluralize(drawers.reduce((sum, d) => sum + (Number(d.photo_count) || 0), 0), 'drawer photo')} received live`
                        : `${pluralize((Array.isArray(unit.photos) ? unit.photos.length : 0), 'photo')} received live`
                    }</strong>
                  </div>
                </div>
              </div>

              ${drawers.length === 0 && Array.isArray(unit.photos) && unit.photos.length > 0 ? `
                <div class="usw-live-drawers-list">
                  <div class="usw-live-drawer-card is-current has-photos">
                    <div class="usw-live-drawer-top">
                      <span class="usw-live-drawer-name">${escapeHtml(unit.name)} · Live photos</span>
                      <span class="usw-pill is-saved">✓ ${pluralize(unit.photos.length, 'photo')}</span>
                    </div>
                    <div class="usw-live-drawer-thumbs">
                      ${unit.photos.map((p, pIdx) => {
                        const thumb = sanitizePhotoUrl(p.thumbnail_path || p.photo_path || '');
                        const full = sanitizePhotoUrl(p.photo_path || '');
                        if (!thumb) return '';
                        return `
                          <button
                            type="button"
                            class="usw-live-thumb-btn"
                            data-lightbox-url="${escapeHtml(full)}"
                            data-lightbox-cap="${escapeHtml(`${unit.name} — Photo ${pIdx + 1}`)}"
                            title="Click to enlarge"
                          >
                            <img src="${escapeHtml(thumb)}" alt="${escapeHtml(unit.name)} photo ${pIdx + 1}">
                          </button>
                        `;
                      }).join('')}
                    </div>
                  </div>
                </div>
              ` : ''}

              ${drawers.length > 0 ? `
                <div class="usw-live-drawers-list">
                  ${drawers.map((d, idx) => {
                    const isActive = idx === Number(sess?.current_drawer_index);
                    const hasPhotos = Number(d.photo_count) > 0;
                    const photos = Array.isArray(d.photos) ? d.photos : [];
                    return `
                      <div class="usw-live-drawer-card ${isActive ? 'is-current' : ''} ${hasPhotos ? 'has-photos' : ''}">
                        <div class="usw-live-drawer-top">
                          <span class="usw-live-drawer-name">${d.coord ? `[${escapeHtml(d.coord)}] ` : ''}${escapeHtml(d.name)}${isActive ? ' · Active on phone' : ''}</span>
                          <span class="usw-pill ${hasPhotos ? 'is-saved' : 'is-muted'}">
                            ${hasPhotos ? `✓ ${pluralize(d.photo_count, 'photo')}` : 'Waiting'}
                          </span>
                        </div>
                        ${photos.length > 0 ? `
                          <div class="usw-live-drawer-thumbs">
                            ${photos.map((p, pIdx) => {
                              const thumb = sanitizePhotoUrl(p.thumbnail_path || p.photo_path || '');
                              const full = sanitizePhotoUrl(p.photo_path || '');
                              if (!thumb) return '';
                              return `
                                <button
                                  type="button"
                                  class="usw-live-thumb-btn"
                                  data-lightbox-url="${escapeHtml(full)}"
                                  data-lightbox-cap="${escapeHtml(`${d.name} — Photo ${pIdx + 1}`)}"
                                  title="Click to enlarge"
                                >
                                  <img src="${escapeHtml(thumb)}" alt="${escapeHtml(d.name)} photo ${pIdx + 1}">
                                </button>
                              `;
                            }).join('')}
                          </div>
                        ` : ''}
                      </div>
                    `;
                  }).join('')}
                </div>
              ` : ''}
            </div>
          ` : ''}
        </div>
      </div>
    `,
    footer: `
      ${wizardState.existingUnitId ? `
        <button type="button" class="btn btn-secondary" id="usw-cancel-btn">Cancel</button>
      ` : `
        <button type="button" class="btn btn-secondary" id="usw-back-choose-btn">← Back</button>
      `}
      <div style="flex: 1;"></div>
      ${unit ? `
        <button type="button" class="btn btn-primary" id="usw-go-review-btn">
          Continue to AI Scan &amp; Review →
        </button>
      ` : ''}
    `,
  };
}

function renderPhase3Review() {
  const sess = wizardState.session;
  const drawers = wizardState.reviewDrawers;
  const idx = Math.max(0, Math.min(drawers.length - 1, wizardState.activeDrawerIdx));
  wizardState.activeDrawerIdx = idx;
  const active = drawers[idx];
  const savedCount = drawers.filter((d) => d.saved && !d.dirty).length;
  const unitThumb = sanitizePhotoUrl(sess?.unit?.container_photo || '');
  const providers = wizardState.visionMeta?.providers || {};

  const drawerItemsHtml = active ? active.items.map((it, itemIdx) => {
    const safeCrop = sanitizePhotoUrl(it.cropPhotoPath || '');
    const isBox = Boolean(it.saveAsBox);
    return `
      <div class="usw-item-row ${it.included ? '' : 'is-excluded'} ${isBox ? 'ai-row-is-box' : ''}">
        <label class="usw-item-check" title="Include this entry">
          <input type="checkbox" data-item-toggle="${itemIdx}" ${it.included ? 'checked' : ''}>
        </label>
        ${safeCrop ? `<img class="usw-item-crop" src="${escapeHtml(safeCrop)}" alt="">` : ''}
        <div class="usw-item-fields">
          <div class="usw-item-top-inputs">
            <input
              type="text"
              class="usw-field-name"
              data-item-field="name"
              data-item-idx="${itemIdx}"
              value="${escapeHtml(it.name)}"
              placeholder="${isBox ? 'Box / container name *' : 'Item name *'}"
            >
            ${!isBox ? `
              <input
                type="number"
                class="usw-field-qty"
                data-item-field="quantity"
                data-item-idx="${itemIdx}"
                value="${it.quantity !== null && it.quantity !== undefined ? Number(it.quantity) : ''}"
                min="0"
                placeholder="Qty"
                aria-label="Quantity"
              >
            ` : ''}
          </div>
          <div class="usw-item-sub-inputs">
            <input
              type="text"
              class="usw-field-cat"
              data-item-field="partNumber"
              data-item-idx="${itemIdx}"
              value="${escapeHtml(it.partNumber || '')}"
              placeholder="Part / Model # (optional)"
            >
            <input
              type="text"
              class="usw-field-cat"
              data-item-field="category"
              data-item-idx="${itemIdx}"
              value="${escapeHtml(it.category)}"
              placeholder="Category (optional)"
            >
            <input
              type="text"
              class="usw-field-notes"
              data-item-field="notes"
              data-item-idx="${itemIdx}"
              value="${escapeHtml(it.notes)}"
              placeholder="Specs or notes (optional)"
            >
          </div>
          ${isBox ? `<span class="ai-box-badge">Saves as Box / Container inside ${escapeHtml(active.draftName)}</span>` : ''}
        </div>
        <button
          type="button"
          class="ai-box-toggle-btn ${isBox ? 'active' : ''}"
          data-item-toggle-box="${itemIdx}"
          title="Save as an openable Box / Container inside this drawer"
        >${isBox ? '📦 Box' : '→ Box'}</button>
        <button
          type="button"
          class="usw-item-remove"
          data-item-remove="${itemIdx}"
          aria-label="Remove item"
          title="Remove item"
        >×</button>
      </div>
    `;
  }).join('') : '';

  return {
    title: `Review & Scan — ${sess?.unit_name || 'Storage Unit'}`,
    subtitle: 'AI scans your drawer photos; fix any mislabels or add missed items before saving.',
    body: `
      <div class="usw-review-shell">
        <aside class="usw-review-rail">
          <div class="usw-rail-unit">
            ${unitThumb
              ? `<img class="usw-rail-unit-img" src="${escapeHtml(unitThumb)}" alt="">`
              : `<div class="usw-rail-unit-icon">${lucideIcon('archive', '', 18)}</div>`
            }
            <div style="min-width: 0;">
              <div class="usw-rail-unit-name">${escapeHtml(sess?.unit_name || 'Storage Unit')}</div>
              <div class="usw-rail-unit-sub">${savedCount} of ${drawers.length} saved</div>
            </div>
          </div>

          <div class="usw-rail-actions">
            ${wizardState.batchScanning ? `
              <button type="button" id="usw-cancel-scan-btn" class="btn btn-danger btn-sm" style="width: 100%;">
                Cancel Scan
              </button>
              <div class="usw-batch-status">${escapeHtml(wizardState.batchProgressText)}</div>
            ` : `
              <button type="button" id="usw-scan-autoinsert-btn" class="btn btn-primary btn-sm" style="width: 100%;">
                ${lucideIcon('camera', '', 15)} Scan &amp; Save All
              </button>
              <button type="button" id="usw-scan-all-btn" class="btn btn-secondary btn-sm" style="width: 100%; margin-top: 0.35rem;">
                Scan All (Review Only)
              </button>
            `}
            <select id="usw-provider-select" class="usw-provider-select" aria-label="AI Vision Provider">
              <option value="local" ${wizardState.selectedProvider === 'local' ? 'selected' : ''}>
                AI: Local (${providers.local?.available ? 'Ready' : 'Ollama'})
              </option>
              <option value="gemini" ${wizardState.selectedProvider === 'gemini' ? 'selected' : ''}>
                AI: Google Gemini ${providers.gemini?.available ? '✓' : '(No key)'}
              </option>
              <option value="openai" ${wizardState.selectedProvider === 'openai' ? 'selected' : ''}>
                AI: OpenAI ${providers.openai?.available ? '✓' : '(No key)'}
              </option>
            </select>
          </div>

          <div class="usw-drawer-nav-list">
            ${drawers.map((d, dIdx) => {
              const pill = getDrawerStatusPill(d);
              return `
                <button
                  type="button"
                  class="usw-drawer-nav-item ${dIdx === idx ? 'is-active' : ''}"
                  data-review-drawer-idx="${dIdx}"
                >
                  <div class="usw-drawer-nav-top">
                    <span class="usw-drawer-nav-name">${d.coord ? `[${escapeHtml(d.coord)}] ` : ''}${escapeHtml(d.draftName)}</span>
                    <span class="usw-pill ${pill.cls}">${escapeHtml(pill.label)}</span>
                  </div>
                  <span class="usw-drawer-nav-meta">${pluralize(d.photos.length, 'photo')}</span>
                </button>
              `;
            }).join('')}
          </div>
        </aside>

        <section class="usw-review-main">
          ${active ? `
            <div class="usw-drawer-header">
              <div class="usw-drawer-name-wrap">
                <label class="usw-mini-label" for="usw-drawer-name-input">Drawer / Compartment Name</label>
                <input
                  type="text"
                  id="usw-drawer-name-input"
                  class="usw-drawer-name-input"
                  value="${escapeHtml(active.draftName)}"
                  maxlength="120"
                >
              </div>
              <div class="usw-drawer-header-btns">
                <label class="btn btn-secondary btn-sm" style="cursor: pointer;">
                  + Add photo
                  <input type="file" id="usw-computer-photo-input" accept="image/*" multiple style="display: none;">
                </label>
                <button
                  type="button"
                  id="usw-scan-one-btn"
                  class="btn btn-primary btn-sm"
                  ${active.scanning || wizardState.batchScanning || active.photos.length === 0 ? 'disabled' : ''}
                >
                  ${active.scanning ? 'Scanning…' : (active.scanned ? 'Re-scan with AI' : 'Scan with AI')}
                </button>
              </div>
            </div>

            ${active.photos.length > 0 ? `
              <div class="usw-photo-strip">
                ${active.photos.map((p, pIdx) => {
                  const thumb = sanitizePhotoUrl(p.thumbnail_path || p.photo_path || '');
                  const full = sanitizePhotoUrl(p.photo_path || '');
                  return `
                    <button
                      type="button"
                      class="usw-photo-thumb-btn"
                      data-lightbox-url="${escapeHtml(full)}"
                      data-lightbox-cap="${escapeHtml(`${active.draftName} — Photo ${pIdx + 1}`)}"
                      title="Click to enlarge"
                    >
                      <img src="${escapeHtml(thumb)}" alt="${escapeHtml(active.draftName)} photo ${pIdx + 1}">
                    </button>
                  `;
                }).join('')}
              </div>
            ` : `
              <div class="usw-info-banner">No photos added for this compartment yet. You can add items manually below or attach a photo.</div>
            `}

            ${active.aiMessage ? `
              <div class="usw-info-banner">${escapeHtml(active.aiMessage)}</div>
            ` : ''}

            <div class="usw-items-list">
              ${active.items.length > 0
                ? drawerItemsHtml
                : '<p class="usw-empty-items">No items listed yet. Click <strong>Scan with AI</strong> above or type an item below.</p>'
              }
            </div>

            <form id="usw-quick-add-form" class="usw-quick-add-row">
              <input
                type="text"
                id="usw-quick-add-input"
                value="${escapeHtml(active.quickAddDraft)}"
                placeholder="Add missed item manually (e.g. 2x AA Batteries or 10mm Socket)..."
                autocomplete="off"
              >
              <button type="submit" class="btn btn-secondary btn-sm">+ Add</button>
            </form>
          ` : '<p>No compartments to review.</p>'}
        </section>
      </div>
    `,
    footer: `
      <button type="button" class="btn btn-secondary" id="usw-review-prev-btn" ${idx <= 0 ? 'disabled' : ''}>
        ← Previous
      </button>
      <button
        type="button"
        class="btn btn-secondary"
        id="usw-review-save-next-btn"
        ${!active || active.saving ? 'disabled' : ''}
      >
        ${active?.saving ? 'Saving…' : (idx < drawers.length - 1 ? 'Save Drawer & Next →' : 'Save Drawer ✓')}
      </button>
      <div style="flex: 1;"></div>
      <button
        type="button"
        class="btn btn-primary"
        id="usw-review-finish-btn"
        ${wizardState.finishing ? 'disabled' : ''}
      >
        ${wizardState.finishing ? 'Finishing…' : 'Finish & Open Storage Unit →'}
      </button>
    `,
  };
}

function renderWizard() {
  const modal = document.getElementById('unit-setup-modal');
  const card = document.getElementById('unit-setup-card');
  const titleEl = document.getElementById('unit-setup-title');
  const subEl = document.getElementById('unit-setup-subtitle');
  const bodyEl = document.getElementById('unit-setup-body');
  const footerEl = document.getElementById('unit-setup-footer');
  if (!modal || !bodyEl || !footerEl) return;

  if (card) {
    card.style.maxWidth = wizardState.phase === 'review' ? '980px' : (wizardState.phase === 'phone' ? '740px' : '600px');
  }

  let view;
  if (wizardState.phase === 'phone') {
    view = renderPhase2Phone();
  } else if (wizardState.phase === 'review') {
    view = renderPhase3Review();
  } else {
    view = renderPhase1Choose();
  }

  if (titleEl) titleEl.textContent = view.title;
  if (subEl) subEl.textContent = view.subtitle;
  bodyEl.innerHTML = view.body;
  footerEl.innerHTML = view.footer;

  bindWizardEvents();
}

function bindWizardEvents() {
  document.getElementById('usw-cancel-btn')?.addEventListener('click', closeUnitSetupWizard);

  document.getElementById('usw-room-select')?.addEventListener('change', (e) => {
    wizardState.roomId = e.target.value ? Number(e.target.value) : null;
  });

  document.getElementById('usw-choose-phone-btn')?.addEventListener('click', startPhoneQrSession);

  document.getElementById('usw-choose-manual-btn')?.addEventListener('click', () => {
    const rId = wizardState.roomId;
    closeUnitSetupWizard();
    openAddStorageModal(rId, 'unit');
  });

  document.getElementById('usw-copy-url-btn')?.addEventListener('click', async () => {
    let urlToCopy = wizardState.mobileUrl;
    if (wizardState.useHttpQrFallback && wizardState.mobileUrl.startsWith('https://') && wizardState.token) {
      try {
        const u = new URL(wizardState.mobileUrl);
        urlToCopy = `http://${u.hostname}:${window.location.port || '8000'}/setup.html?token=${wizardState.token}`;
      } catch {}
    }
    if (!urlToCopy) return;
    try {
      await navigator.clipboard.writeText(urlToCopy);
      showToast('Link copied to clipboard');
    } catch {
      showToast('Copy failed — select and copy the URL manually.', 'info');
    }
  });

  document.getElementById('usw-toggle-http-qr-btn')?.addEventListener('click', () => {
    wizardState.useHttpQrFallback = !wizardState.useHttpQrFallback;
    renderWizard();
  });

  document.getElementById('usw-refresh-qr-btn')?.addEventListener('click', startPhoneQrSession);

  document.getElementById('usw-back-choose-btn')?.addEventListener('click', () => {
    stopPolling();
    wizardState.sessionSeq += 1;
    wizardState.phase = 'choose';
    renderWizard();
  });

  document.getElementById('usw-go-review-btn')?.addEventListener('click', enterReviewPhase);

  // Phase 3 handlers
  document.getElementById('usw-scan-autoinsert-btn')?.addEventListener('click', () => scanAllDrawers({ autoSave: true }));
  document.getElementById('usw-scan-all-btn')?.addEventListener('click', () => scanAllDrawers({ autoSave: false }));
  document.getElementById('usw-cancel-scan-btn')?.addEventListener('click', () => {
    abortActiveScans();
    renderWizard();
  });

  document.getElementById('usw-provider-select')?.addEventListener('change', (e) => {
    wizardState.selectedProvider = e.target.value;
  });

  document.querySelectorAll('[data-review-drawer-idx]').forEach((btn) => {
    btn.addEventListener('click', () => {
      wizardState.activeDrawerIdx = Number(btn.getAttribute('data-review-drawer-idx')) || 0;
      renderWizard();
    });
  });

  const active = wizardState.reviewDrawers[wizardState.activeDrawerIdx];

  document.getElementById('usw-drawer-name-input')?.addEventListener('input', (e) => {
    if (!active) return;
    active.draftName = e.target.value;
    active.dirty = true;
  });

  document.getElementById('usw-scan-one-btn')?.addEventListener('click', async () => {
    if (wizardState.scanAbortController || wizardState.batchScanning) return;
    const controller = new AbortController();
    wizardState.scanAbortController = controller;
    const modalEl = document.getElementById('unit-setup-modal');
    if (modalEl?.dataset) modalEl.dataset.scanning = 'true';
    try {
      await scanSingleDrawer(wizardState.activeDrawerIdx, controller.signal);
    } finally {
      if (wizardState.scanAbortController === controller) wizardState.scanAbortController = null;
      if (modalEl?.dataset && !wizardState.batchScanning) modalEl.dataset.scanning = 'false';
    }
  });

  document.getElementById('usw-computer-photo-input')?.addEventListener('change', async (e) => {
    const files = Array.from(e.target?.files || []).filter(Boolean);
    if (e.target) e.target.value = '';
    if (!files.length || !active) return;
    try {
      for (const file of files) {
        const compressed = await compressImageFile(file);
        const fd = new FormData();
        fd.append('file', compressed);
        const saved = await apiUpload(`/api/v1/locations/${active.id}/photos`, fd, 'Photo upload failed.');
        if (saved) active.photos.unshift(saved);
      }
      showToast(`Added ${pluralize(files.length, 'photo')} to ${active.draftName}`);
      renderWizard();
    } catch (err) {
      showToast(err?.message || 'Photo upload failed.', 'error');
    }
  });

  document.querySelectorAll('[data-lightbox-url]').forEach((btn) => {
    btn.addEventListener('click', () => {
      openLightbox(btn.getAttribute('data-lightbox-url'), btn.getAttribute('data-lightbox-cap') || '');
    });
  });

  document.querySelectorAll('[data-item-toggle]').forEach((chk) => {
    chk.addEventListener('change', () => {
      if (!active) return;
      const itemIdx = Number(chk.getAttribute('data-item-toggle'));
      if (active.items[itemIdx]) {
        active.items[itemIdx].included = chk.checked;
        active.items[itemIdx].dirty = true;
        active.dirty = true;
        renderWizard();
      }
    });
  });

  document.querySelectorAll('[data-item-field]').forEach((input) => {
    input.addEventListener('input', () => {
      if (!active) return;
      const itemIdx = Number(input.getAttribute('data-item-idx'));
      const field = input.getAttribute('data-item-field');
      if (active.items[itemIdx] && field) {
        active.items[itemIdx][field] = input.value;
        active.items[itemIdx].dirty = true;
        active.dirty = true;
      }
    });
  });

  document.querySelectorAll('[data-item-toggle-box]').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (!active) return;
      const itemIdx = Number(btn.getAttribute('data-item-toggle-box'));
      if (active.items[itemIdx]) {
        active.items[itemIdx].saveAsBox = !active.items[itemIdx].saveAsBox;
        if (active.items[itemIdx].saveAsBox) active.items[itemIdx].included = true;
        active.items[itemIdx].dirty = true;
        active.dirty = true;
        active.saved = false;
        renderWizard();
      }
    });
  });

  document.querySelectorAll('[data-item-remove]').forEach((btn) => {
    btn.addEventListener('click', () => {
      if (!active) return;
      const itemIdx = Number(btn.getAttribute('data-item-remove'));
      const target = active.items[itemIdx];
      if (!target) return;
      if (target.savedItemId || target.savedBoxId) {
        // Mark excluded so saving deletes it from the backend
        target.included = false;
        target.dirty = true;
      } else {
        active.items.splice(itemIdx, 1);
      }
      active.dirty = true;
      renderWizard();
    });
  });

  document.getElementById('usw-quick-add-input')?.addEventListener('input', (e) => {
    if (active) active.quickAddDraft = e.target.value;
  });

  document.getElementById('usw-quick-add-form')?.addEventListener('submit', (e) => {
    e.preventDefault();
    if (!active) return;
    const raw = (active.quickAddDraft || '').trim();
    if (!raw) return;

    let qty = 1;
    let name = raw;
    const m = raw.match(/^(\d+)\s*[x×]\s+(.+)$/i);
    if (m) {
      qty = Math.max(1, parseInt(m[1], 10) || 1);
      name = m[2].trim();
    }
    active.items.push({
      uid: nextUid(),
      savedItemId: null,
      savedBoxId: null,
      saveAsBox: false,
      included: true,
      name,
      partNumber: '',
      quantity: qty,
      category: '',
      notes: '',
      cropPhotoPath: '',
      dirty: true,
    });
    active.quickAddDraft = '';
    active.dirty = true;
    active.saved = false;
    renderWizard();
    document.getElementById('usw-quick-add-input')?.focus();
  });

  document.getElementById('usw-review-prev-btn')?.addEventListener('click', () => {
    if (wizardState.activeDrawerIdx > 0) {
      wizardState.activeDrawerIdx -= 1;
      renderWizard();
    }
  });

  document.getElementById('usw-review-save-next-btn')?.addEventListener('click', async () => {
    const ok = await saveDrawerItems(wizardState.activeDrawerIdx);
    if (ok && wizardState.activeDrawerIdx < wizardState.reviewDrawers.length - 1) {
      wizardState.activeDrawerIdx += 1;
      renderWizard();
    }
  });

  document.getElementById('usw-review-finish-btn')?.addEventListener('click', finishWizard);
}
