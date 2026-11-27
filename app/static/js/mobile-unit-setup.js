/**
 * Guided Storage Unit Mobile & Tablet Setup Flow (/setup.html?token=...)
 * Features:
 *  - Step 1: Define storage unit (presets, count, columns, label, live diagram preview)
 *  - Step 2: Exterior photo
 *  - Step 3: Clean single-drawer focus (top-left -> right, top -> bottom) with continuous
 *            multi-photo live camera (Done / Retake / Shutter) & background uploads
 *  - Step 4: Handoff to desktop for AI scan & item review
 */
import { escapeHtml, pluralize, sanitizePhotoUrl } from './dom.js';
import { compressImageFile } from './photo-compress.js';

const UNIT_PRESETS = [
  { kind: 'box', label: 'Box / Tote', defaultLabel: 'Box', defaultType: 'box', defaultCols: 1, defaultCount: 0 },
  { kind: 'dresser', label: 'Dresser', defaultLabel: 'Drawer', defaultType: 'drawer', defaultCols: 1, defaultCount: 5 },
  { kind: 'cabinet', label: 'Cabinet', defaultLabel: 'Drawer', defaultType: 'drawer', defaultCols: 1, defaultCount: 5 },
  { kind: 'desk', label: 'Desk', defaultLabel: 'Drawer', defaultType: 'drawer', defaultCols: 1, defaultCount: 3 },
  { kind: 'closet', label: 'Closet', defaultLabel: 'Section', defaultType: 'shelf', defaultCols: 1, defaultCount: 4 },
  { kind: 'shelving', label: 'Shelving', defaultLabel: 'Shelf', defaultType: 'shelf', defaultCols: 1, defaultCount: 4 },
  { kind: 'organizer', label: 'Organizer', defaultLabel: 'Bin', defaultType: 'bin', defaultCols: 4, defaultCount: 12 },
];

const COMPARTMENT_LABELS = [
  { label: 'Drawer', type: 'drawer' },
  { label: 'Shelf', type: 'shelf' },
  { label: 'Section', type: 'compartment' },
  { label: 'Cubby', type: 'cubby' },
  { label: 'Bin', type: 'bin' },
];

const params = new URLSearchParams(window.location.search);
const token = params.get('token') || '';
const returnMode = params.get('return') || '';

const canUseLiveCamera = Boolean(
  typeof navigator !== 'undefined'
  && navigator.mediaDevices
  && typeof navigator.mediaDevices.getUserMedia === 'function'
);

let localUploadSeq = 1;

const setupState = {
  step: 1, // 1 = Define Unit, 2 = Exterior Photo, 3 = Drawer Photos, 4 = Done
  session: null,
  unitKind: 'dresser',
  unitName: '',
  unitLabel: '',
  unitDesc: '',
  compartmentCount: 5,
  compartmentLabel: 'Drawer',
  compartmentType: 'drawer',
  gridCols: 1,
  userEditedLayout: false,
  existingContainerMode: false,
  labeledOnly: false,
  jumpPickerOpen: false,
  currentDrawerIdx: 0,
  renamingDrawer: false,
  uploadingExterior: false,
  creatingUnit: false,
  errorMessage: '',

  // Non-blocking per-drawer transfer queue: Array<{ localId, drawerId, previewUrl, status: 'uploading'|'error', error? }>
  pendingUploads: [],
  // Per-drawer recent capture stack for instant Retake: Record<drawerId, Array<{ localId, serverPhotoId: number|null, previewUrl, deleted: boolean }>>
  shotStacks: {},
  // Live continuous camera overlay state
  liveCameraOpen: false,
  liveCameraStarting: false,
  liveStream: null,
  lastSnapPreview: '',
};

let toastTimer = null;

function showSetupToast(msg, isError = false) {
  const el = document.getElementById('setup-toast');
  if (!el) return;
  el.textContent = msg;
  el.className = `setup-toast is-open${isError ? ' is-error' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => {
    el.classList.remove('is-open');
  }, 2400);
}

async function apiJson(url, options = {}) {
  const res = await fetch(url, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(body.detail || body.message || `Request failed (${res.status})`);
    err.status = res.status;
    throw err;
  }
  return body;
}

function isCustomDrawerName(drawer) {
  const name = String(drawer?.name || '').trim();
  if (!name) return false;
  if (/^(drawer|bin|shelf|compartment|cubby|slot)\s+\d+$/i.test(name)) return false;
  if (/^[A-Z]{1,2}\d+(?:[\u2013\u2014-][A-Z]{1,2}\d+)?$/i.test(name)) return false;
  return true;
}

function getCaptureTargets() {
  const drawers = setupState.session?.drawers || [];
  if (drawers.length > 0) {
    if (setupState.labeledOnly) {
      const labeled = drawers.filter(isCustomDrawerName);
      if (labeled.length > 0) return labeled;
    }
    return drawers;
  }
  const unit = setupState.session?.unit;
  if (!unit) return [];
  const photos = Array.isArray(unit.photos) ? unit.photos : [];
  return [{
    id: unit.id,
    name: unit.name || setupState.session?.unit_name || 'Box',
    location_type: unit.location_type || 'box',
    photo_count: photos.length,
    photos,
    isSingleContainer: true,
  }];
}

function getPendingForDrawer(drawerId) {
  return setupState.pendingUploads.filter((u) => Number(u.drawerId) === Number(drawerId));
}

function getActiveUploadingCount(drawerId = null) {
  return setupState.pendingUploads.filter(
    (u) => u.status === 'uploading' && (drawerId === null || Number(u.drawerId) === Number(drawerId))
  ).length;
}

function getDrawerShotStack(drawerId) {
  const key = String(Number(drawerId));
  if (!setupState.shotStacks[key]) {
    setupState.shotStacks[key] = [];
  }
  return setupState.shotStacks[key];
}

function getDrawerTotalPhotoCount(drawer) {
  if (!drawer) return 0;
  const saved = Number(drawer.photo_count) || 0;
  const uploading = getActiveUploadingCount(drawer.id);
  return saved + uploading;
}

function getLatestPreviewForDrawer(drawer) {
  if (!drawer) return '';
  const stack = getDrawerShotStack(drawer.id);
  for (let i = stack.length - 1; i >= 0; i--) {
    if (!stack[i].deleted && stack[i].previewUrl) {
      return stack[i].previewUrl;
    }
  }
  const photos = Array.isArray(drawer.photos) ? drawer.photos : [];
  if (photos.length > 0) {
    const last = photos[photos.length - 1];
    return sanitizePhotoUrl(last.thumbnail_path || last.photo_path || '');
  }
  return '';
}

function getDrawerDisplayLabel(drawer) {
  if (!drawer) return 'Drawer';
  const name = String(drawer.name || 'Drawer').trim();
  const coord = String(drawer.coord || '').trim();
  if (coord && !name.toLowerCase().includes(coord.toLowerCase())) {
    return `${coord} — ${name}`;
  }
  return name || coord || 'Drawer';
}

function syncHeaderAndStepper() {
  const roomPill = document.getElementById('setup-room-pill');
  const finishEarlyBtn = document.getElementById('setup-finish-early-btn');
  const roomName = setupState.session?.room_name;
  if (roomPill) {
    if (roomName) {
      roomPill.textContent = roomName;
      roomPill.hidden = false;
    } else {
      roomPill.hidden = true;
    }
  }
  if (finishEarlyBtn) {
    finishEarlyBtn.hidden = setupState.step !== 3 || !setupState.session?.unit_id;
  }

  const stepper = document.getElementById('setup-stepper');
  if (!stepper) return;
  stepper.hidden = Boolean(setupState.existingContainerMode);
  if (stepper.hidden) return;
  const items = stepper.querySelectorAll('.setup-step-item');
  const lines = stepper.querySelectorAll('.setup-step-line');
  items.forEach((item) => {
    const s = Number(item.dataset.step);
    item.classList.toggle('is-active', s === setupState.step);
    item.classList.toggle('is-done', s < setupState.step);
  });
  lines.forEach((line, idx) => {
    line.classList.toggle('is-done', idx + 1 < setupState.step);
  });
}

function setFooterHtml(html) {
  const footer = document.getElementById('setup-footer');
  if (!footer) return;
  if (!html) {
    footer.hidden = true;
    footer.innerHTML = '';
  } else {
    footer.hidden = false;
    footer.innerHTML = `<div class="setup-footer-inner">${html}</div>`;
  }
}

function renderPreviewDiagram() {
  const count = Math.max(0, Math.min(50, Number(setupState.compartmentCount) || 0));
  if (count === 0) {
    return `
      <div class="setup-diagram-frame">
        <div class="setup-preview-slot">Single Box / Container — snap exterior photo, then as many contents photos as you like</div>
      </div>
    `;
  }
  const cols = Math.max(1, Math.min(count, Number(setupState.gridCols) || 1));
  const previewMax = Math.min(count, 24);
  const cells = [];
  for (let i = 1; i <= previewMax; i++) {
    cells.push(`<div class="setup-preview-slot">${escapeHtml(setupState.compartmentLabel)} ${i}</div>`);
  }
  if (count > previewMax) {
    cells.push(`<div class="setup-preview-slot">+${count - previewMax} more</div>`);
  }
  return `
    <div class="setup-diagram-frame">
      <div class="setup-diagram-grid" style="grid-template-columns: repeat(${cols}, minmax(0, 1fr));">
        ${cells.join('')}
      </div>
    </div>
  `;
}

function renderStep1DefineUnit() {
  const roomLabel = setupState.session?.room_name || 'Needs a home';
  return `
    <section class="setup-card">
      <h1 class="setup-heading">Set up storage unit</h1>
      <p class="setup-sub">Adding to <strong>${escapeHtml(roomLabel)}</strong>. Name it and choose how it's divided.</p>

      <div class="setup-field">
        <span class="setup-section-label">Storage Type</span>
        <div class="setup-chips-grid">
          ${UNIT_PRESETS.map((p) => `
            <button
              type="button"
              class="setup-chip-btn ${setupState.unitKind === p.kind ? 'is-active' : ''}"
              data-preset-kind="${escapeHtml(p.kind)}"
            >${escapeHtml(p.label)}</button>
          `).join('')}
        </div>
      </div>

      <div class="setup-field">
        <label class="setup-section-label" for="setup-unit-name">Storage Unit / Box Name *</label>
        <input
          type="text"
          id="setup-unit-name"
          class="setup-input"
          placeholder="e.g. Hardware Tote, 5 Drawer Cabinet"
          value="${escapeHtml(setupState.unitName)}"
          maxlength="120"
          autocomplete="off"
        >
      </div>

      <div class="setup-field">
        <label class="setup-section-label" for="setup-unit-label">Physical Label / Code (Optional)</label>
        <input
          type="text"
          id="setup-unit-label"
          class="setup-input"
          placeholder="e.g. M2, A1, Bin 4"
          value="${escapeHtml(setupState.unitLabel)}"
          maxlength="40"
          autocomplete="off"
        >
      </div>

      <div class="setup-two-col">
        <div class="setup-field">
          <span class="setup-section-label">How Many Compartments?</span>
          <div class="setup-counter">
            <button type="button" id="setup-count-dec" class="setup-counter-btn" aria-label="Decrease count" ${setupState.compartmentCount <= 0 ? 'disabled' : ''}>−</button>
            <input
              type="number"
              id="setup-count-input"
              class="setup-counter-input"
              min="0"
              max="50"
              value="${Number(setupState.compartmentCount)}"
            >
            <button type="button" id="setup-count-inc" class="setup-counter-btn" aria-label="Increase count" ${setupState.compartmentCount >= 50 ? 'disabled' : ''}>+</button>
          </div>
        </div>

        <div class="setup-field">
          <span class="setup-section-label">Columns Across</span>
          <div class="setup-pill-row">
            ${[1, 2, 3, 4, 6].map((c) => `
              <button
                type="button"
                class="setup-pill-btn ${Number(setupState.gridCols) === c ? 'is-active' : ''}"
                data-grid-cols="${c}"
              >${c} ${c === 1 ? 'col' : 'cols'}</button>
            `).join('')}
          </div>
        </div>
      </div>

      <div class="setup-field">
        <span class="setup-section-label">Compartment Label</span>
        <div class="setup-pill-row">
          ${COMPARTMENT_LABELS.map((item) => `
            <button
              type="button"
              class="setup-pill-btn ${setupState.compartmentLabel === item.label ? 'is-active' : ''}"
              data-comp-label="${escapeHtml(item.label)}"
              data-comp-type="${escapeHtml(item.type)}"
            >${escapeHtml(item.label)}</button>
          `).join('')}
        </div>
      </div>

      <div class="setup-field" style="margin-bottom: 0;">
        <span class="setup-section-label">Layout Preview</span>
        <div id="setup-preview-wrap">${renderPreviewDiagram()}</div>
      </div>
    </section>
  `;
}

function renderStep2ExteriorPhoto() {
  const unit = setupState.session?.unit;
  const drawers = setupState.session?.drawers || [];
  const unitName = unit?.name || setupState.session?.unit_name || 'Storage Unit';
  const extPhoto = sanitizePhotoUrl(unit?.container_photo || '');
  const hasDrawers = drawers.length > 0;

  return `
    <section class="setup-card">
      <h1 class="setup-heading">Photo of ${escapeHtml(unitName)}</h1>
      <p class="setup-sub">Take an exterior photo so it's easy to spot at a glance, or skip ahead.</p>

      ${extPhoto ? `
        <div class="setup-exterior-preview">
          <img src="${escapeHtml(extPhoto)}" alt="${escapeHtml(unitName)}">
        </div>
      ` : ''}

      <div class="setup-camera-grid">
        <label class="setup-capture-btn">
          <input type="file" id="setup-exterior-camera" accept="image/*" capture="environment" style="display: none;">
          <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"></path><circle cx="12" cy="13" r="3"></circle></svg>
          <span>${setupState.uploadingExterior ? 'Transferring…' : (extPhoto ? 'Retake Photo' : 'Take Photo')}</span>
        </label>

        <label class="setup-library-btn">
          <input type="file" id="setup-exterior-gallery" accept="image/*" style="display: none;">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path><polyline points="17 8 12 3 7 8"></polyline><line x1="12" x2="12" y1="3" y2="15"></line></svg>
          <span>Choose Photo</span>
        </label>
      </div>

      ${!hasDrawers ? `
        <p class="setup-sub" style="margin-bottom: 0;">Next, take as many photos of the contents inside this box/container as you like.</p>
      ` : ''}
    </section>
  `;
}

function renderJumpPickerPanel(unit, allDrawers, drawers, activeIdx) {
  const labeledCount = allDrawers.filter(isCustomDrawerName).length;
  const showFilterBar = labeledCount > 0 && labeledCount < allDrawers.length;
  const cols = Math.max(1, Number(unit?.grid_cols) || 1);

  return `
    <div class="setup-jump-drawer-panel" role="region" aria-label="Jump to drawer">
      <div class="setup-jump-panel-head">
        <span class="setup-section-label" style="margin: 0;">Jump to Drawer (${allDrawers.length})</span>
        <button type="button" id="setup-jump-close-btn" class="setup-btn setup-btn-secondary setup-btn-xs">Close</button>
      </div>
      ${showFilterBar ? `
        <div class="setup-filter-bar" role="group" aria-label="Filter drawers">
          <button type="button" id="setup-filter-all-btn" class="setup-filter-pill ${!setupState.labeledOnly ? 'is-active' : ''}">
            All (${allDrawers.length})
          </button>
          <button type="button" id="setup-filter-labeled-btn" class="setup-filter-pill ${setupState.labeledOnly ? 'is-active' : ''}">
            Labeled only (${labeledCount})
          </button>
        </div>
      ` : ''}
      <div class="setup-jump-scroll">
        <div class="setup-diagram-grid" style="grid-template-columns: repeat(${Math.min(cols, 4)}, minmax(0, 1fr));">
          ${drawers.map((d, idx) => {
            const isActive = idx === activeIdx;
            const totalCount = getDrawerTotalPhotoCount(d);
            const hasPhotos = totalCount > 0;
            return `
              <button
                type="button"
                class="setup-drawer-slot ${isActive ? 'is-active' : ''} ${hasPhotos ? 'has-photos' : ''}"
                data-drawer-idx="${idx}"
              >
                <span class="setup-drawer-slot-name">${d.coord ? `<span class="setup-slot-coord">${escapeHtml(d.coord)}</span> ` : ''}${escapeHtml(d.name)}</span>
                <span class="setup-drawer-badge">${hasPhotos ? `✓ ${totalCount}` : '0'}</span>
              </button>
            `;
          }).join('')}
        </div>
      </div>
    </div>
  `;
}

function renderStep3DrawerPhotos() {
  const unit = setupState.session?.unit;
  const allDrawers = setupState.session?.drawers || [];
  const drawers = getCaptureTargets();
  if (!drawers.length) {
    return `
      <section class="setup-card">
        <h1 class="setup-heading">No compartments to photograph</h1>
        <p class="setup-sub">Finish below to review and add items on your computer.</p>
      </section>
    `;
  }

  const idx = Math.max(0, Math.min(drawers.length - 1, setupState.currentDrawerIdx));
  setupState.currentDrawerIdx = idx;
  const drawer = drawers[idx];
  const isSingle = Boolean(drawer.isSingleContainer);
  const savedPhotos = Array.isArray(drawer.photos) ? drawer.photos : [];
  const pendingForThis = getPendingForDrawer(drawer.id);
  const uploadingCount = getActiveUploadingCount(drawer.id);
  const totalPhotos = savedPhotos.length + uploadingCount;

  return `
    <section class="setup-card setup-focus-card">
      <div class="setup-focus-meta-row">
        <div class="setup-step-pill">
          ${isSingle ? 'Container Contents' : `Drawer ${idx + 1} of ${drawers.length}`}
        </div>
        <div class="setup-focus-meta-actions">
          ${!isSingle ? `
            <button
              type="button"
              id="setup-jump-toggle-btn"
              class="setup-mini-action-btn ${setupState.jumpPickerOpen ? 'is-active' : ''}"
              aria-expanded="${setupState.jumpPickerOpen ? 'true' : 'false'}"
            >
              Jump ▾
            </button>
          ` : ''}
          <button type="button" id="setup-rename-toggle-btn" class="setup-mini-action-btn">
            ${setupState.renamingDrawer ? 'Cancel' : 'Rename'}
          </button>
        </div>
      </div>

      ${setupState.jumpPickerOpen && !isSingle ? renderJumpPickerPanel(unit, allDrawers, drawers, idx) : ''}

      <div class="setup-drawer-hero">
        <h2 class="setup-drawer-title">
          ${drawer.coord ? `<span class="setup-coord-badge">${escapeHtml(drawer.coord)}</span>` : ''}
          <span class="setup-drawer-name-text">${isSingle ? `Contents of ${escapeHtml(drawer.name)}` : escapeHtml(drawer.name)}</span>
        </h2>
        ${drawer.description ? `<p class="setup-drawer-desc">${escapeHtml(drawer.description)}</p>` : ''}
      </div>

      ${setupState.renamingDrawer ? `
        <form id="setup-rename-form" class="setup-rename-row">
          <input type="text" id="setup-rename-input" class="setup-input" value="${escapeHtml(drawer.name)}" maxlength="120" required>
          <button type="submit" class="setup-btn setup-btn-primary setup-btn-sm" style="flex: 0 0 auto;">Save</button>
        </form>
      ` : ''}

      ${!canUseLiveCamera && setupState.session?.https_url && /^https:\/\//i.test(setupState.session.https_url) && window.location.protocol === 'http:' ? `
        <div class="setup-transfer-banner" style="justify-content: space-between; margin-bottom: 0.75rem;">
          <span>Need multi-shot live camera?</span>
          <a href="${escapeHtml(setupState.session.https_url)}" class="setup-btn setup-btn-primary setup-btn-xs" style="flex: 0 0 auto; text-decoration: none;">
            Switch to Live Camera (HTTPS)
          </a>
        </div>
      ` : ''}

      ${canUseLiveCamera ? `
        <div class="setup-camera-primary-wrap">
          <button type="button" id="setup-start-live-cam-btn" class="setup-capture-btn setup-capture-btn-hero">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"></path><circle cx="12" cy="13" r="3"></circle></svg>
            <span>Open Camera</span>
            <span class="setup-btn-hint">Tap shutter for 1 or many photos, then Done</span>
          </button>

          <div class="setup-secondary-actions-row">
            <label class="setup-secondary-action">
              <input type="file" id="setup-drawer-gallery" accept="image/*" multiple style="display: none;">
              <span>Upload from library</span>
            </label>
            ${totalPhotos > 0 ? `
              <button type="button" id="setup-retake-last-btn" class="setup-secondary-action is-danger">
                Retake Last
              </button>
            ` : ''}
          </div>
        </div>
      ` : `
        <div class="setup-camera-primary-wrap">
          <label class="setup-capture-btn setup-capture-btn-hero">
            <input type="file" id="setup-drawer-camera" accept="image/*" capture="environment" style="display: none;">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"></path><circle cx="12" cy="13" r="3"></circle></svg>
            <span>Take Photo</span>
            <span class="setup-btn-hint">Tap again to add more angles</span>
          </label>

          <div class="setup-secondary-actions-row">
            <label class="setup-secondary-action">
              <input type="file" id="setup-drawer-gallery" accept="image/*" multiple style="display: none;">
              <span>Select Multiple</span>
            </label>
            ${totalPhotos > 0 ? `
              <button type="button" id="setup-retake-last-btn" class="setup-secondary-action is-danger">
                Retake Last
              </button>
            ` : ''}
          </div>
        </div>
      `}

      ${uploadingCount > 0 ? `
        <div class="setup-transfer-banner">
          <span class="setup-mini-spinner" aria-hidden="true"></span>
          <span>Uploading ${pluralize(uploadingCount, 'photo')}…</span>
        </div>
      ` : ''}

      ${(pendingForThis.length > 0 || savedPhotos.length > 0) ? `
        <div class="setup-thumbs-header">
          <span class="setup-section-label" style="margin: 0;">
            ${pluralize(savedPhotos.length, 'photo')} saved${uploadingCount > 0 ? ` · ${uploadingCount} uploading` : ''}
          </span>
        </div>
        <div class="setup-thumbs-grid">
          ${pendingForThis.map((p) => `
            <div class="setup-thumb-card is-transferring">
              ${p.previewUrl ? `<img src="${escapeHtml(p.previewUrl)}" alt="Uploading photo">` : ''}
              <span class="setup-thumb-status ${p.status === 'error' ? 'is-error' : ''}">
                ${p.status === 'error' ? 'Failed' : 'Sending…'}
              </span>
              ${p.status === 'error' ? `<button type="button" class="setup-thumb-remove" data-dismiss-upload="${Number(p.localId)}" aria-label="Dismiss failed upload" title="Dismiss">×</button>` : ''}
            </div>
          `).join('')}
          ${savedPhotos.map((p) => {
            const thumbSrc = sanitizePhotoUrl(p.thumbnail_path || p.photo_path || '');
            return `
              <div class="setup-thumb-card is-sent">
                ${thumbSrc ? `<img src="${escapeHtml(thumbSrc)}" alt="${escapeHtml(drawer.name)} photo">` : ''}
                <span class="setup-thumb-sent-pill">✓</span>
                <button
                  type="button"
                  class="setup-thumb-remove"
                  data-delete-photo-id="${Number(p.id)}"
                  aria-label="Delete photo"
                  title="Delete photo"
                >×</button>
              </div>
            `;
          }).join('')}
        </div>
      ` : ''}
    </section>
  `;
}

function renderStep4Done() {
  const drawers = setupState.session?.drawers || [];
  const unitName = setupState.session?.unit?.name || setupState.session?.unit_name || 'Storage Unit';
  const drawersWithPhotos = drawers.filter((d) => Number(d.photo_count) > 0).length;
  const totalPhotos = drawers.reduce((acc, d) => acc + (Number(d.photo_count) || 0), 0)
    + (setupState.session?.unit?.container_photo ? 1 : 0);

  return `
    <section class="setup-card setup-done-hero">
      <div class="setup-done-badge" aria-hidden="true">✓</div>
      <h1 class="setup-heading">${escapeHtml(unitName)} is ready!</h1>
      <p class="setup-sub">
        ${returnMode === 'app'
          ? 'All photos are saved. Return to the app to scan contents with AI and edit items.'
          : 'Continue on your computer to scan all drawer photos and save identified items.'}
      </p>

      <div class="setup-stats-row">
        <div class="setup-stat-box">
          <div class="setup-stat-val">${drawers.length}</div>
          <div class="setup-stat-lbl">Drawers</div>
        </div>
        <div class="setup-stat-box">
          <div class="setup-stat-val">${drawersWithPhotos}</div>
          <div class="setup-stat-lbl">With Photos</div>
        </div>
        <div class="setup-stat-box">
          <div class="setup-stat-val">${totalPhotos}</div>
          <div class="setup-stat-lbl">Total Photos</div>
        </div>
      </div>

      <div style="display: flex; gap: 0.65rem; flex-wrap: wrap; justify-content: center;">
        <button type="button" id="setup-back-to-drawers-btn" class="setup-btn setup-btn-secondary">
          Add More Photos
        </button>
        ${returnMode === 'app' && setupState.session?.unit_id ? `
          <a href="/#/unit/${Number(setupState.session.unit_id)}" class="setup-btn setup-btn-primary" style="text-decoration: none;">
            Open in App →
          </a>
        ` : ''}
      </div>
    </section>
  `;
}

function isSafeHttpUrl(url) {
  return typeof url === 'string' && /^https?:\/\/[^/\s]+/i.test(url);
}

async function fetchActiveSetupSession() {
  try {
    const res = await fetch('/api/v1/unit-setup/active');
    if (!res.ok) return null;
    const data = await res.json().catch(() => null);
    if (!data || !data.token || !isSafeHttpUrl(data.mobile_url)) return null;
    return data;
  } catch {
    return null;
  }
}

function renderErrorCard(message) {
  return `
    <section class="setup-card" style="text-align: center; padding: 2rem 1.25rem;">
      <h1 class="setup-heading">Setup link unavailable</h1>
      <p class="setup-sub">${escapeHtml(message)}</p>
      <div style="display: flex; gap: 0.65rem; flex-wrap: wrap; justify-content: center;">
        <button type="button" id="setup-reconnect-active-btn" class="setup-btn setup-btn-primary" style="flex: 0 1 auto;">
          Check for Active Session
        </button>
        <a href="/" class="setup-btn setup-btn-secondary" style="text-decoration: none;">Open WhereIsMyStuff</a>
      </div>
    </section>
  `;
}

function renderFooterForStep() {
  if (setupState.errorMessage || setupState.liveCameraOpen) {
    setFooterHtml('');
    return;
  }
  if (setupState.step === 1) {
    setFooterHtml(`
      <button type="button" id="setup-create-unit-btn" class="setup-btn setup-btn-primary" ${setupState.creatingUnit ? 'disabled' : ''}>
        ${setupState.creatingUnit ? 'Creating…' : 'Create & Continue →'}
      </button>
    `);
  } else if (setupState.step === 2) {
    const drawers = setupState.session?.drawers || [];
    const hasDrawers = drawers.length > 0;
    const hasExtPhoto = Boolean(setupState.session?.unit?.container_photo);
    setFooterHtml(`
      <button type="button" id="setup-step2-next-btn" class="setup-btn ${hasExtPhoto ? 'setup-btn-primary' : 'setup-btn-secondary'}">
        ${hasDrawers
          ? (hasExtPhoto ? `Start ${pluralize(drawers.length, 'Drawer')} →` : 'Skip Exterior Photo →')
          : (hasExtPhoto ? 'Add Contents Photos →' : 'Skip Exterior Photo →')
        }
      </button>
    `);
  } else if (setupState.step === 3) {
    const drawers = getCaptureTargets();
    const idx = setupState.currentDrawerIdx;
    const isSingle = Boolean(drawers[0]?.isSingleContainer);
    const isLast = drawers.length === 0 || idx >= drawers.length - 1;
    const uploadingAny = getActiveUploadingCount() > 0;
    setFooterHtml(`
      ${!isSingle ? `
        <button type="button" id="setup-prev-drawer-btn" class="setup-btn setup-btn-secondary" ${idx <= 0 ? 'disabled' : ''}>
          ← Prev
        </button>
      ` : ''}
      ${isLast ? `
        <button type="button" id="setup-finish-drawers-btn" class="setup-btn setup-btn-success" ${uploadingAny ? 'disabled' : ''}>
          ${uploadingAny ? 'Uploading…' : 'Finish ✓'}
        </button>
      ` : `
        <button type="button" id="setup-next-drawer-btn" class="setup-btn setup-btn-primary">
          Done — Next Drawer →
        </button>
      `}
    `);
  } else {
    setFooterHtml('');
  }
}

function syncLiveCameraHud() {
  const drawers = getCaptureTargets();
  const idx = Math.max(0, Math.min(drawers.length - 1, setupState.currentDrawerIdx));
  setupState.currentDrawerIdx = idx;
  const drawer = drawers[idx];
  if (!drawer) return;

  const isSingle = Boolean(drawer.isSingleContainer);
  const isLast = drawers.length === 0 || idx >= drawers.length - 1;
  const savedCnt = Number(drawer.photo_count) || 0;
  const pendingCnt = getActiveUploadingCount(drawer.id);
  const total = savedCnt + pendingCnt;

  const titleEl = document.getElementById('live-cam-drawer-title');
  if (titleEl) {
    titleEl.textContent = getDrawerDisplayLabel(drawer);
  }
  const descEl = document.getElementById('live-cam-drawer-desc');
  if (descEl) {
    if (drawer.description) {
      descEl.textContent = drawer.description;
      descEl.hidden = false;
    } else {
      descEl.textContent = '';
      descEl.hidden = true;
    }
  }
  const metaEl = document.getElementById('live-cam-transfer-pill');
  if (metaEl) {
    const posText = isSingle ? '' : `${idx + 1}/${drawers.length} · `;
    const photoWord = total === 1 ? '1 photo' : `${total} photos`;
    metaEl.textContent = pendingCnt > 0
      ? `${posText}${photoWord} (uploading…)`
      : `${posText}${photoWord}`;
  }

  const prevBtn = document.getElementById('live-cam-prev-btn');
  if (prevBtn) {
    prevBtn.hidden = isSingle;
    prevBtn.disabled = idx <= 0;
  }

  const retakeBtn = document.getElementById('live-cam-retake-btn');
  if (retakeBtn) {
    retakeBtn.disabled = total <= 0;
  }

  const doneBtn = document.getElementById('live-cam-done-btn');
  if (doneBtn) {
    if (isLast) {
      doneBtn.textContent = 'Finish ✓';
      doneBtn.classList.add('is-finish');
    } else {
      doneBtn.textContent = total > 0 ? 'Done →' : 'Skip →';
      doneBtn.classList.remove('is-finish');
    }
  }

  const thumbUrl = setupState.lastSnapPreview || getLatestPreviewForDrawer(drawer);
  const thumbImg = document.getElementById('live-cam-last-thumb');
  const thumbWrap = document.getElementById('live-cam-thumb-slot');
  if (thumbImg && thumbWrap) {
    if (thumbUrl) {
      thumbImg.src = thumbUrl;
      thumbImg.style.display = 'block';
      thumbWrap.classList.add('has-thumb');
    } else {
      thumbImg.removeAttribute('src');
      thumbImg.style.display = 'none';
      thumbWrap.classList.remove('has-thumb');
    }
  }
}

async function advanceDrawerInCamera(delta) {
  const drawers = getCaptureTargets();
  if (!drawers.length) return;
  const nextIdx = Math.max(0, Math.min(drawers.length - 1, setupState.currentDrawerIdx + delta));
  if (nextIdx === setupState.currentDrawerIdx) return;

  setupState.currentDrawerIdx = nextIdx;
  const target = drawers[nextIdx];
  setupState.lastSnapPreview = getLatestPreviewForDrawer(target);
  syncLiveCameraHud();

  const allDrawers = setupState.session?.drawers || [];
  const fullIdx = target ? allDrawers.findIndex((d) => Number(d.id) === Number(target.id)) : nextIdx;
  const progressIdx = fullIdx >= 0 ? fullIdx : nextIdx;
  try {
    await apiJson(`/api/v1/unit-setup/sessions/${encodeURIComponent(token)}/progress`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_drawer_index: progressIdx, status: 'capturing_drawers' }),
    });
  } catch {
    // Non-fatal progress sync error
  }
}

async function retakeLastPhoto(drawer) {
  if (!drawer) return;
  const stack = getDrawerShotStack(drawer.id);
  let targetShot = null;
  for (let i = stack.length - 1; i >= 0; i--) {
    if (!stack[i].deleted) {
      targetShot = stack[i];
      break;
    }
  }

  if (targetShot) {
    targetShot.deleted = true;
    setupState.pendingUploads = setupState.pendingUploads.filter((u) => u.localId !== targetShot.localId);
    const remainingPreview = getLatestPreviewForDrawer(drawer);
    setupState.lastSnapPreview = remainingPreview;
    if (targetShot.previewUrl && targetShot.previewUrl.startsWith('blob:') && targetShot.previewUrl !== remainingPreview) {
      try { URL.revokeObjectURL(targetShot.previewUrl); } catch {}
    }

    if (setupState.liveCameraOpen) syncLiveCameraHud();
    else render();

    if (targetShot.serverPhotoId) {
      try {
        const res = await fetch(`/api/v1/locations/${drawer.id}/photos/${targetShot.serverPhotoId}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('Could not remove photo');
        await refreshSession();
      } catch (err) {
        showSetupToast(err?.message || 'Could not remove photo', true);
      }
    }
    showSetupToast('Last photo removed');
    if (setupState.liveCameraOpen) syncLiveCameraHud();
    else render();
    return;
  }

  const savedPhotos = Array.isArray(drawer.photos) ? drawer.photos : [];
  if (savedPhotos.length > 0) {
    const lastSaved = savedPhotos[savedPhotos.length - 1];
    try {
      const res = await fetch(`/api/v1/locations/${drawer.id}/photos/${lastSaved.id}`, { method: 'DELETE' });
      if (!res.ok) throw new Error('Could not remove photo');
      await refreshSession();
      const updatedDrawers = getCaptureTargets();
      const updatedDrawer = updatedDrawers[setupState.currentDrawerIdx];
      setupState.lastSnapPreview = getLatestPreviewForDrawer(updatedDrawer);
      showSetupToast('Last photo removed');
      if (setupState.liveCameraOpen) syncLiveCameraHud();
      else render();
    } catch (err) {
      showSetupToast(err?.message || 'Could not remove photo', true);
    }
  }
}

function stopLiveCamera() {
  if (setupState.liveStream) {
    for (const track of setupState.liveStream.getTracks()) {
      try { track.stop(); } catch {}
    }
    setupState.liveStream = null;
  }
  setupState.liveCameraOpen = false;
  setupState.lastSnapPreview = '';
  const overlay = document.getElementById('live-camera-overlay');
  if (overlay) overlay.remove();
  renderFooterForStep();
  bindFooterHandlers();
}

async function startLiveCamera() {
  if (setupState.liveCameraOpen || setupState.liveCameraStarting) return;
  const drawers = getCaptureTargets();
  const drawer = drawers[setupState.currentDrawerIdx];
  if (!drawer) return;

  setupState.liveCameraStarting = true;
  try {
    const stream = await navigator.mediaDevices.getUserMedia({
      video: {
        facingMode: { ideal: 'environment' },
        width: { ideal: 1920 },
        height: { ideal: 1080 },
      },
      audio: false,
    });
    setupState.liveStream = stream;
    setupState.liveCameraOpen = true;
    setupState.lastSnapPreview = getLatestPreviewForDrawer(drawer);
    setFooterHtml('');

    const overlay = document.createElement('div');
    overlay.id = 'live-camera-overlay';
    overlay.className = 'live-cam-overlay';
    overlay.innerHTML = `
      <div class="live-cam-topbar">
        <div class="live-cam-label-block">
          <div id="live-cam-drawer-title" class="live-cam-drawer-title"></div>
          <div id="live-cam-drawer-desc" class="live-cam-drawer-desc" hidden></div>
          <div id="live-cam-transfer-pill" class="live-cam-pill"></div>
        </div>
        <div class="live-cam-top-actions">
          <button type="button" id="live-cam-prev-btn" class="live-cam-top-btn" aria-label="Previous drawer">‹ Prev</button>
          <button type="button" id="live-cam-close-btn" class="live-cam-top-btn live-cam-close-btn" aria-label="Close camera">✕</button>
        </div>
      </div>
      <div class="live-cam-stage">
        <video id="live-cam-video" autoplay playsinline muted></video>
        <div id="live-cam-flash" class="live-cam-flash"></div>
      </div>
      <div class="live-cam-controls">
        <button type="button" id="live-cam-retake-btn" class="live-cam-retake-btn" aria-label="Retake last photo" disabled>
          <span id="live-cam-thumb-slot" class="live-cam-thumb-slot">
            <img id="live-cam-last-thumb" src="" alt="" style="display: none;">
          </span>
          <span class="live-cam-retake-label">Retake</span>
        </button>
        <button type="button" id="live-cam-shutter-btn" class="live-cam-shutter" aria-label="Take photo">
          <span class="live-cam-shutter-inner"></span>
        </button>
        <button type="button" id="live-cam-done-btn" class="live-cam-done-btn">Done →</button>
      </div>
    `;
    document.body.appendChild(overlay);

    const videoEl = document.getElementById('live-cam-video');
    if (videoEl) {
      videoEl.srcObject = stream;
      await videoEl.play().catch(() => {});
    }

    syncLiveCameraHud();

    document.getElementById('live-cam-close-btn')?.addEventListener('click', () => {
      stopLiveCamera();
      render();
    });
    document.getElementById('live-cam-prev-btn')?.addEventListener('click', () => {
      advanceDrawerInCamera(-1);
    });
    document.getElementById('live-cam-retake-btn')?.addEventListener('click', () => {
      const curDrawers = getCaptureTargets();
      const cur = curDrawers[setupState.currentDrawerIdx];
      retakeLastPhoto(cur);
    });
    document.getElementById('live-cam-done-btn')?.addEventListener('click', () => {
      const curDrawers = getCaptureTargets();
      const isLast = curDrawers.length === 0 || setupState.currentDrawerIdx >= curDrawers.length - 1;
      if (isLast) {
        stopLiveCamera();
        finishToComputer();
      } else {
        advanceDrawerInCamera(1);
      }
    });
    document.getElementById('live-cam-shutter-btn')?.addEventListener('click', snapLiveFrame);
  } catch (err) {
    showSetupToast(err?.message || 'Camera access denied — use Take Photo or library instead.', true);
  } finally {
    setupState.liveCameraStarting = false;
  }
}

async function snapLiveFrame() {
  const videoEl = document.getElementById('live-cam-video');
  const drawers = getCaptureTargets();
  const drawer = drawers[setupState.currentDrawerIdx];
  if (!videoEl || !drawer || !videoEl.videoWidth || !videoEl.videoHeight) return;

  const flash = document.getElementById('live-cam-flash');
  if (flash) {
    flash.classList.add('is-flashing');
    setTimeout(() => flash.classList.remove('is-flashing'), 110);
  }

  const maxDim = 1600;
  let w = videoEl.videoWidth;
  let h = videoEl.videoHeight;
  if (w > maxDim || h > maxDim) {
    if (w > h) {
      h = Math.round((h * maxDim) / w);
      w = maxDim;
    } else {
      w = Math.round((w * maxDim) / h);
      h = maxDim;
    }
  }
  const canvas = document.createElement('canvas');
  canvas.width = w;
  canvas.height = h;
  canvas.getContext('2d').drawImage(videoEl, 0, 0, w, h);

  const blob = await new Promise((resolve) => canvas.toBlob(resolve, 'image/jpeg', 0.84));
  if (!blob) return;
  const file = new File([blob], `drawer_${drawer.id}_${Date.now()}.jpg`, { type: 'image/jpeg' });
  const previewUrl = URL.createObjectURL(blob);
  setupState.lastSnapPreview = previewUrl;
  syncLiveCameraHud();
  enqueueDrawerUpload(drawer.id, file, previewUrl, true);
}

async function enqueueDrawerUpload(drawerId, rawFile, existingPreviewUrl = null, alreadyCompressed = false) {
  const localId = ++localUploadSeq;
  const previewUrl = existingPreviewUrl || URL.createObjectURL(rawFile);
  const entry = { localId, drawerId, previewUrl, status: 'uploading' };
  const shotEntry = { localId, serverPhotoId: null, previewUrl, deleted: false };
  getDrawerShotStack(drawerId).push(shotEntry);
  setupState.pendingUploads.unshift(entry);

  if (setupState.liveCameraOpen) {
    syncLiveCameraHud();
  } else {
    render();
  }

  try {
    const fileToSend = alreadyCompressed ? rawFile : await compressImageFile(rawFile);
    const fd = new FormData();
    fd.append('file', fileToSend);
    fd.append('photo_type', 'container');
    const saved = await apiJson(`/api/v1/locations/${drawerId}/photos`, { method: 'POST', body: fd });
    shotEntry.serverPhotoId = Number(saved?.id) || null;

    setupState.pendingUploads = setupState.pendingUploads.filter((u) => u.localId !== localId);

    // If user clicked Retake while upload was still in flight, immediately remove on server
    if (shotEntry.deleted && shotEntry.serverPhotoId) {
      await fetch(`/api/v1/locations/${drawerId}/photos/${shotEntry.serverPhotoId}`, { method: 'DELETE' }).catch(() => {});
    }

    await refreshSession();
    if (setupState.liveCameraOpen) {
      syncLiveCameraHud();
    } else {
      render();
    }
  } catch (err) {
    entry.status = 'error';
    entry.error = err?.message || 'Upload failed';
    if (!shotEntry.deleted) {
      showSetupToast(entry.error, true);
    }
    if (setupState.liveCameraOpen) {
      syncLiveCameraHud();
    } else {
      render();
    }
  }
}

function render() {
  syncHeaderAndStepper();
  const main = document.getElementById('setup-main');
  if (!main) return;
  const renameDraft = document.getElementById('setup-rename-input')?.value;
  try {
    renderMain(main);
  } finally {
    const renameInput = document.getElementById('setup-rename-input');
    if (renameInput && renameDraft !== undefined) renameInput.value = renameDraft;
  }
}

function renderMain(main) {
  if (setupState.errorMessage) {
    main.innerHTML = renderErrorCard(setupState.errorMessage);
    document.getElementById('setup-reconnect-active-btn')?.addEventListener('click', async () => {
      const active = await fetchActiveSetupSession();
      if (active && isSafeHttpUrl(active.mobile_url)) {
        window.location.replace(active.mobile_url);
      } else {
        showSetupToast('No active session open on your computer yet', true);
      }
    });
    renderFooterForStep();
    return;
  }

  if (setupState.step === 1) {
    main.innerHTML = renderStep1DefineUnit();
    bindStep1Handlers();
  } else if (setupState.step === 2) {
    main.innerHTML = renderStep2ExteriorPhoto();
    bindStep2Handlers();
  } else if (setupState.step === 3) {
    main.innerHTML = renderStep3DrawerPhotos();
    bindStep3Handlers();
  } else {
    main.innerHTML = renderStep4Done();
    bindStep4Handlers();
  }
  renderFooterForStep();
  bindFooterHandlers();
}

function updatePreviewInPlace() {
  const wrap = document.getElementById('setup-preview-wrap');
  if (wrap) wrap.innerHTML = renderPreviewDiagram();
}

function bindStep1Handlers() {
  const nameInput = document.getElementById('setup-unit-name');
  if (nameInput) {
    nameInput.addEventListener('input', (e) => {
      setupState.unitName = e.target.value;
    });
  }
  const labelInput = document.getElementById('setup-unit-label');
  if (labelInput) {
    labelInput.addEventListener('input', (e) => {
      setupState.unitLabel = e.target.value;
    });
  }

  document.querySelectorAll('[data-preset-kind]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const kind = btn.getAttribute('data-preset-kind');
      const preset = UNIT_PRESETS.find((p) => p.kind === kind);
      if (!preset) return;
      setupState.unitKind = preset.kind;
      if (!setupState.userEditedLayout) {
        setupState.compartmentCount = preset.defaultCount;
        setupState.compartmentLabel = preset.defaultLabel;
        setupState.compartmentType = preset.defaultType;
        setupState.gridCols = preset.defaultCols;
      }
      render();
    });
  });

  const decBtn = document.getElementById('setup-count-dec');
  const incBtn = document.getElementById('setup-count-inc');
  const countInput = document.getElementById('setup-count-input');
  const setCount = (val) => {
    setupState.userEditedLayout = true;
    setupState.compartmentCount = Math.max(0, Math.min(50, Number(val) || 0));
    render();
  };
  decBtn?.addEventListener('click', () => setCount(setupState.compartmentCount - 1));
  incBtn?.addEventListener('click', () => setCount(setupState.compartmentCount + 1));
  countInput?.addEventListener('change', (e) => setCount(e.target.value));

  document.querySelectorAll('[data-grid-cols]').forEach((btn) => {
    btn.addEventListener('click', () => {
      setupState.userEditedLayout = true;
      setupState.gridCols = Number(btn.getAttribute('data-grid-cols')) || 1;
      render();
    });
  });

  document.querySelectorAll('[data-comp-label]').forEach((btn) => {
    btn.addEventListener('click', () => {
      setupState.userEditedLayout = true;
      setupState.compartmentLabel = btn.getAttribute('data-comp-label') || 'Drawer';
      setupState.compartmentType = btn.getAttribute('data-comp-type') || 'drawer';
      render();
    });
  });

  updatePreviewInPlace();
}

async function uploadPhotoToLocation(locationId, file, photoType = 'container') {
  const compressed = await compressImageFile(file);
  const fd = new FormData();
  fd.append('file', compressed);
  fd.append('photo_type', photoType);
  return apiJson(`/api/v1/locations/${locationId}/photos`, {
    method: 'POST',
    body: fd,
  });
}

async function refreshSession() {
  const data = await apiJson(`/api/v1/unit-setup/sessions/${encodeURIComponent(token)}`);
  setupState.session = data;
  if (data.status === 'expired') {
    setupState.errorMessage = 'This setup QR code has expired. Generate a new one on your computer.';
  }
  return data;
}

function bindStep2Handlers() {
  const handleExtPick = async (e) => {
    const file = e.target?.files?.[0];
    if (e.target) e.target.value = '';
    const unitId = setupState.session?.unit_id;
    if (!file || !unitId || setupState.uploadingExterior) return;
    setupState.uploadingExterior = true;
    render();
    try {
      await uploadPhotoToLocation(unitId, file, 'container');
      await refreshSession();
      showSetupToast('Exterior photo sent to desktop');
    } catch (err) {
      showSetupToast(err.message || 'Could not upload photo', true);
    } finally {
      setupState.uploadingExterior = false;
      render();
    }
  };
  document.getElementById('setup-exterior-camera')?.addEventListener('change', handleExtPick);
  document.getElementById('setup-exterior-gallery')?.addEventListener('change', handleExtPick);
}

async function selectDrawerIndex(nextIdx) {
  const drawers = getCaptureTargets();
  if (!drawers.length) return;
  const clamped = Math.max(0, Math.min(drawers.length - 1, nextIdx));
  setupState.currentDrawerIdx = clamped;
  setupState.renamingDrawer = false;
  setupState.jumpPickerOpen = false;
  render();
  const target = drawers[clamped];
  const allDrawers = setupState.session?.drawers || [];
  const fullIdx = target ? allDrawers.findIndex((d) => Number(d.id) === Number(target.id)) : clamped;
  const progressIdx = fullIdx >= 0 ? fullIdx : clamped;
  try {
    await apiJson(`/api/v1/unit-setup/sessions/${encodeURIComponent(token)}/progress`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ current_drawer_index: progressIdx, status: 'capturing_drawers' }),
    });
  } catch {
    // Non-fatal progress sync error
  }
}

function bindStep3Handlers() {
  document.getElementById('setup-jump-toggle-btn')?.addEventListener('click', () => {
    setupState.jumpPickerOpen = !setupState.jumpPickerOpen;
    render();
  });
  document.getElementById('setup-jump-close-btn')?.addEventListener('click', () => {
    setupState.jumpPickerOpen = false;
    render();
  });

  document.getElementById('setup-filter-labeled-btn')?.addEventListener('click', () => {
    setupState.labeledOnly = true;
    setupState.currentDrawerIdx = 0;
    render();
  });
  document.getElementById('setup-filter-all-btn')?.addEventListener('click', () => {
    setupState.labeledOnly = false;
    setupState.currentDrawerIdx = 0;
    render();
  });

  document.querySelectorAll('[data-drawer-idx]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const idx = Number(btn.getAttribute('data-drawer-idx'));
      selectDrawerIndex(idx);
    });
  });

  document.getElementById('setup-start-live-cam-btn')?.addEventListener('click', startLiveCamera);

  document.getElementById('setup-retake-last-btn')?.addEventListener('click', () => {
    const drawers = getCaptureTargets();
    const drawer = drawers[setupState.currentDrawerIdx];
    retakeLastPhoto(drawer);
  });

  document.querySelectorAll('[data-dismiss-upload]').forEach((btn) => {
    btn.addEventListener('click', () => {
      const localId = Number(btn.getAttribute('data-dismiss-upload'));
      setupState.pendingUploads = setupState.pendingUploads.filter((u) => u.localId !== localId);
      render();
    });
  });

  document.getElementById('setup-rename-toggle-btn')?.addEventListener('click', () => {
    setupState.renamingDrawer = !setupState.renamingDrawer;
    render();
    if (setupState.renamingDrawer) {
      const input = document.getElementById('setup-rename-input');
      input?.focus();
      input?.select();
    }
  });

  document.getElementById('setup-rename-form')?.addEventListener('submit', async (e) => {
    e.preventDefault();
    const drawers = getCaptureTargets();
    const drawer = drawers[setupState.currentDrawerIdx];
    const nextName = (document.getElementById('setup-rename-input')?.value || '').trim();
    if (!drawer || !nextName) return;
    try {
      await apiJson(`/api/v1/locations/${drawer.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: nextName }),
      });
      setupState.renamingDrawer = false;
      await refreshSession();
      showSetupToast(drawer.isSingleContainer ? 'Renamed' : 'Drawer renamed');
      render();
    } catch (err) {
      showSetupToast(err.message || 'Could not rename', true);
    }
  });

  const handleDrawerFiles = (e) => {
    const files = Array.from(e.target?.files || []).filter(Boolean);
    if (e.target) e.target.value = '';
    const drawers = getCaptureTargets();
    const drawer = drawers[setupState.currentDrawerIdx];
    if (!files.length || !drawer) return;
    for (const file of files) {
      enqueueDrawerUpload(drawer.id, file);
    }
  };
  document.getElementById('setup-drawer-camera')?.addEventListener('change', handleDrawerFiles);
  document.getElementById('setup-drawer-gallery')?.addEventListener('change', handleDrawerFiles);

  document.querySelectorAll('[data-delete-photo-id]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const photoId = Number(btn.getAttribute('data-delete-photo-id'));
      const drawers = getCaptureTargets();
      const drawer = drawers[setupState.currentDrawerIdx];
      if (!drawer || !photoId) return;
      try {
        const res = await fetch(`/api/v1/locations/${drawer.id}/photos/${photoId}`, { method: 'DELETE' });
        if (!res.ok) throw new Error('Could not delete photo');
        await refreshSession();
        showSetupToast('Photo removed');
        render();
      } catch (err) {
        showSetupToast(err.message || 'Could not delete photo', true);
      }
    });
  });
}

function bindStep4Handlers() {
  document.getElementById('setup-back-to-drawers-btn')?.addEventListener('click', async () => {
    setupState.step = 3;
    try {
      await apiJson(`/api/v1/unit-setup/sessions/${encodeURIComponent(token)}/progress`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status: 'capturing_drawers' }),
      });
    } catch {}
    render();
  });
}

async function finishToComputer() {
  if (getActiveUploadingCount() > 0) {
    showSetupToast('Waiting for remaining photos to finish uploading…');
    return;
  }
  stopLiveCamera();
  try {
    const updated = await apiJson(`/api/v1/unit-setup/sessions/${encodeURIComponent(token)}/progress`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: 'ready_for_review' }),
    });
    setupState.session = updated;
    setupState.step = 4;
    render();
  } catch (err) {
    showSetupToast(err.message || 'Could not finish setup', true);
  }
}

function bindFooterHandlers() {
  document.getElementById('setup-create-unit-btn')?.addEventListener('click', async () => {
    const cleanName = (setupState.unitName || '').trim();
    if (!cleanName) {
      showSetupToast('Please enter a name for this storage unit', true);
      document.getElementById('setup-unit-name')?.focus();
      return;
    }
    if (setupState.creatingUnit) return;
    setupState.creatingUnit = true;
    render();
    try {
      const created = await apiJson(`/api/v1/unit-setup/sessions/${encodeURIComponent(token)}/unit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: cleanName,
          label: (setupState.unitLabel || '').trim().slice(0, 40) || null,
          unit_kind: setupState.unitKind,
          compartment_count: Number(setupState.compartmentCount) || 0,
          compartment_label: setupState.compartmentLabel,
          compartment_type: setupState.compartmentType,
          grid_cols: Math.max(1, Number(setupState.gridCols) || 1),
        }),
      });
      setupState.session = created;
      setupState.step = 2;
      showSetupToast(`Created "${cleanName}"`);
    } catch (err) {
      showSetupToast(err.message || 'Could not create unit', true);
    } finally {
      setupState.creatingUnit = false;
      render();
    }
  });

  document.getElementById('setup-step2-next-btn')?.addEventListener('click', () => {
    setupState.step = 3;
    setupState.currentDrawerIdx = 0;
    render();
  });

  document.getElementById('setup-prev-drawer-btn')?.addEventListener('click', () => {
    selectDrawerIndex(setupState.currentDrawerIdx - 1);
  });

  document.getElementById('setup-next-drawer-btn')?.addEventListener('click', () => {
    selectDrawerIndex(setupState.currentDrawerIdx + 1);
  });

  document.getElementById('setup-finish-drawers-btn')?.addEventListener('click', finishToComputer);
}

function bindGlobalHeaderControls() {
  document.getElementById('setup-finish-early-btn')?.addEventListener('click', finishToComputer);
  document.getElementById('setup-theme-btn')?.addEventListener('click', () => {
    const cur = document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
    const next = cur === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try {
      localStorage.setItem('wims_theme_pref', next);
    } catch {}
  });
  window.addEventListener('pagehide', stopLiveCamera);
}

async function init() {
  bindGlobalHeaderControls();
  if (!token) {
    const active = await fetchActiveSetupSession();
    if (active && isSafeHttpUrl(active.mobile_url)) {
      window.location.replace(active.mobile_url);
      return;
    }
    setupState.errorMessage = 'Missing setup token. Scan the QR code on your computer screen.';
    render();
    return;
  }

  try {
    const data = await apiJson(`/api/v1/unit-setup/sessions/${encodeURIComponent(token)}/connect`, {
      method: 'POST',
    });
    setupState.session = data;

    if (data.status === 'expired') {
      const active = await fetchActiveSetupSession();
      if (active && active.token !== token && isSafeHttpUrl(active.mobile_url)) {
        window.location.replace(active.mobile_url);
        return;
      }
      setupState.errorMessage = 'This setup QR code has expired. Generate a new one on your computer.';
      render();
      return;
    }

    if (data.status === 'ready_for_review' || data.status === 'completed') {
      setupState.step = 4;
    } else if (data.unit_id) {
      const drawers = data.drawers || [];
      const anyDrawerPhotos = drawers.some((d) => Number(d.photo_count) > 0);
      if (data.status === 'capturing_drawers') {
        setupState.existingContainerMode = true;
        setupState.labeledOnly = false;
        setupState.step = 3;
        setupState.currentDrawerIdx = Math.min(Math.max(0, data.current_drawer_index || 0), Math.max(0, getCaptureTargets().length - 1));
      } else if (anyDrawerPhotos || data.unit?.container_photo) {
        setupState.step = drawers.length > 0 ? 3 : 2;
        setupState.currentDrawerIdx = Math.min(Math.max(0, data.current_drawer_index || 0), Math.max(0, drawers.length - 1));
      } else {
        setupState.step = 2;
      }
    } else {
      setupState.step = 1;
    }
  } catch (err) {
    if (err?.status === 404 || err?.status === 410) {
      const active = await fetchActiveSetupSession();
      if (active && active.token !== token && isSafeHttpUrl(active.mobile_url)) {
        window.location.replace(active.mobile_url);
        return;
      }
    }
    setupState.errorMessage = err.message || 'This setup link is invalid or has expired.';
  }
  render();
}

init();
