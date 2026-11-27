/**
 * Multi-Source Smart Item Import Modal
 * Lets users stage PDFs, files, pasted screenshots, photos (file or phone),
 * web links (Amazon, Adafruit, etc.), and copied text — then click
 * "Scan & Autofill" so AI populates the item (including quantity, use case,
 * and setup info) while placing all images in Photos and all PDFs/docs in Documents.
 */
import { apiGet, apiPost, apiUpload } from './api.js';
import { closeModal, escapeHtml, pluralize, sanitizePhotoUrl, showToast, withSubmitLock } from './dom.js';
import { openItemPhoneCaptureModal } from './phone-capture.js';
import { compressImageFile, uploadPhotoAndGetPath } from './photo-compress.js';
import { navToItem } from './router.js';
import { loadAllData, populateLocationDropdowns, state } from './state.js';
import { markModalClean } from './unsaved-guard.js';
import { ensureVisionModelReady } from './vision-guard.js';

function markModalDirty(modalId) {
  const modal = document.getElementById(modalId);
  if (modal && modal.classList.contains('open')) {
    modal.dataset.dirty = 'true';
  }
}

const BUNDLE_SCAN_TIMEOUT_MS = 130000;
const IMAGE_EXT_RE = /\.(jpg|jpeg|png|webp|gif|bmp|tif|tiff)$/i;

let stagedImages = []; // { kind: 'file'|'uploaded', file?, previewUrl, originalPath?, thumbnailPath?, name }
let mainImageIndex = 0;
let stagedDocs = []; // { kind: 'file'|'uploaded', file?, filePath?, filename, attachmentType, sizeBytes, title? }
let bundleStage = 'gather'; // 'gather' | 'review'
let bundlePasteBound = false;
let bundleVisionMeta = null;

function revokeBundlePreviewUrls() {
  for (const img of stagedImages) {
    if (img.previewUrl && String(img.previewUrl).startsWith('blob:')) {
      try { URL.revokeObjectURL(img.previewUrl); } catch {}
    }
  }
}

function isImageFile(file) {
  if (!file) return false;
  if (file.type && file.type.startsWith('image/')) return true;
  return IMAGE_EXT_RE.test(file.name || '');
}

function formatBytes(bytes) {
  const n = Number(bytes || 0);
  if (!n) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function addFilesToBundleStaging(rawFiles) {
  const files = Array.from(rawFiles || []).filter(Boolean);
  if (!files.length) return;
  let addedImgs = 0;
  let addedDocs = 0;
  for (const file of files) {
    if (isImageFile(file)) {
      stagedImages.push({
        kind: 'file',
        file,
        previewUrl: URL.createObjectURL(file),
        originalPath: null,
        thumbnailPath: null,
        name: file.name || 'image.png',
      });
      addedImgs += 1;
    } else {
      const isPdf = (file.type === 'application/pdf') || /\.pdf$/i.test(file.name || '');
      stagedDocs.push({
        kind: 'file',
        file,
        filePath: null,
        filename: file.name || 'document',
        attachmentType: isPdf ? 'pdf' : 'document',
        sizeBytes: file.size || 0,
        title: (file.name || '').replace(/\.[^.]+$/, '').replace(/[_-]+/g, ' ').trim() || null,
      });
      addedDocs += 1;
    }
  }
  renderBundleStagedLists();
  markModalDirty('item-bundle-modal');
  const parts = [];
  if (addedImgs) parts.push(pluralize(addedImgs, 'image'));
  if (addedDocs) parts.push(pluralize(addedDocs, 'file'));
  if (parts.length) showToast(`Added ${parts.join(' and ')} to import.`);
}

function extractClipboardImage(clipboardData) {
  if (!clipboardData) return null;
  const items = clipboardData.items ? Array.from(clipboardData.items) : [];
  for (const it of items) {
    if (it.kind === 'file' && it.type && it.type.startsWith('image/')) {
      const blob = it.getAsFile();
      if (blob) {
        const ext = (blob.type.split('/')[1] || 'png').replace(/[^a-z0-9]/gi, '') || 'png';
        return blob.name ? blob : new File([blob], `pasted-screenshot.${ext}`, { type: blob.type || 'image/png' });
      }
    }
  }
  const files = clipboardData.files ? Array.from(clipboardData.files) : [];
  for (const f of files) {
    if (isImageFile(f)) return f;
  }
  return null;
}

function bindBundleModalEvents() {
  if (bundlePasteBound || typeof document === 'undefined') return;
  bundlePasteBound = true;

  document.addEventListener('paste', (e) => {
    const modal = document.getElementById('item-bundle-modal');
    if (!modal || !modal.classList.contains('open')) return;
    const imgFile = extractClipboardImage(e.clipboardData);
    if (!imgFile) return;
    e.preventDefault();
    addFilesToBundleStaging([imgFile]);
  });

  const dropzone = document.getElementById('bundle-dropzone');
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
      if (e.dataTransfer?.files?.length) {
        addFilesToBundleStaging(e.dataTransfer.files);
      }
    });
  }
}

function renderBundleStagedLists() {
  const imgCountEl = document.getElementById('bundle-images-count');
  const docCountEl = document.getElementById('bundle-docs-count');
  const imgGrid = document.getElementById('bundle-staged-images-grid');
  const docList = document.getElementById('bundle-staged-docs-list');
  const reviewImgGrid = document.getElementById('bundle-review-images-grid');
  const reviewDocList = document.getElementById('bundle-review-docs-list');

  if (mainImageIndex >= stagedImages.length) {
    mainImageIndex = Math.max(0, stagedImages.length - 1);
  }
  if (imgCountEl) imgCountEl.textContent = String(stagedImages.length);
  if (docCountEl) docCountEl.textContent = String(stagedDocs.length);

  const renderImgHtml = (interactiveMain) => {
    if (!stagedImages.length) {
      return `<p class="drawer-empty-hint" style="margin: 0.25rem 0; grid-column: 1 / -1;">No images added yet — paste (Ctrl+V), drop files, or snap with phone.</p>`;
    }
    return stagedImages.map((img, idx) => {
      const isMain = idx === mainImageIndex;
      const src = sanitizePhotoUrl(img.previewUrl) || (String(img.previewUrl || '').startsWith('blob:') ? img.previewUrl : '');
      return `
        <div class="bundle-thumb-card ${isMain ? 'is-main' : ''}">
          <img src="${escapeHtml(src)}" alt="${escapeHtml(img.name || `Image ${idx + 1}`)}" onclick="setBundleMainImage(${idx})" title="Click to set as main item photo">
          ${isMain ? `<span class="bundle-main-pill">Main</span>` : ''}
          <div class="bundle-thumb-actions">
            ${(!isMain && interactiveMain) ? `<button type="button" class="bundle-thumb-btn" onclick="setBundleMainImage(${idx})">Set main</button>` : ''}
            <button type="button" class="bundle-thumb-btn danger-text" onclick="removeBundleImage(${idx})" title="Remove">✕</button>
          </div>
        </div>
      `;
    }).join('');
  };

  const renderDocsHtml = () => {
    if (!stagedDocs.length) {
      return `<p class="drawer-empty-hint" style="margin: 0.25rem 0;">No PDFs or files added yet.</p>`;
    }
    return stagedDocs.map((doc, idx) => {
      const badge = String(doc.attachmentType || 'file').toUpperCase();
      const sizeStr = formatBytes(doc.sizeBytes);
      return `
        <div class="bundle-doc-row">
          <div class="bundle-doc-left">
            <span class="item-attachment-badge">${escapeHtml(badge)}</span>
            <span class="bundle-doc-name">${escapeHtml(doc.filename || 'Document')}</span>
            ${sizeStr ? `<span class="bundle-doc-size">${escapeHtml(sizeStr)}</span>` : ''}
          </div>
          <button type="button" class="btn btn-secondary btn-xs danger-text" onclick="removeBundleDoc(${idx})">Remove</button>
        </div>
      `;
    }).join('');
  };

  if (imgGrid) imgGrid.innerHTML = renderImgHtml(true);
  if (reviewImgGrid) reviewImgGrid.innerHTML = renderImgHtml(true);
  if (docList) docList.innerHTML = renderDocsHtml();
  if (reviewDocList) reviewDocList.innerHTML = renderDocsHtml();
}

function syncBundleStageUI() {
  const gatherEl = document.getElementById('bundle-stage-gather');
  const reviewEl = document.getElementById('bundle-stage-review');
  const scanBtn = document.getElementById('bundle-scan-btn');
  const manualBtn = document.getElementById('bundle-skip-ai-btn');
  const backBtn = document.getElementById('bundle-back-btn');
  const saveBtn = document.getElementById('bundle-save-btn');
  const subtitle = document.getElementById('bundle-modal-subtitle');

  const isReview = bundleStage === 'review';
  if (gatherEl) gatherEl.style.display = isReview ? 'none' : 'flex';
  if (reviewEl) reviewEl.style.display = isReview ? 'flex' : 'none';
  if (scanBtn) scanBtn.style.display = isReview ? 'none' : '';
  if (manualBtn) manualBtn.style.display = isReview ? 'none' : '';
  if (backBtn) backBtn.style.display = isReview ? '' : 'none';
  if (saveBtn) saveBtn.style.display = isReview ? '' : 'none';
  if (subtitle) {
    subtitle.textContent = isReview
      ? 'Review AI-populated details, pick the main image, and save'
      : 'Add PDFs, files, pasted screenshots, photos, links & text — then click Scan & Autofill';
  }
}

async function loadBundleProviderStatus() {
  const select = document.getElementById('bundle-vision-provider');
  const pill = document.getElementById('bundle-vision-status');
  try {
    bundleVisionMeta = await apiGet('/api/v1/photos/vision-providers');
    if (select && bundleVisionMeta?.default_provider) {
      select.value = bundleVisionMeta.default_provider;
    }
    const prov = select?.value || 'local';
    const info = bundleVisionMeta?.providers?.[prov];
    if (pill && info) {
      pill.textContent = info.available
        ? (prov === 'local' ? `Ollama ready (${info.model})` : `Ready (${info.model})`)
        : 'AI not running';
      pill.className = `ai-provider-pill ${info.available ? 'ready' : 'warn'}`;
    }
  } catch {
    bundleVisionMeta = null;
    if (pill) {
      pill.textContent = 'Auto';
      pill.className = 'ai-provider-pill';
    }
  }
}

export async function openItemBundleModal(defaultLocationId = null) {
  bindBundleModalEvents();
  revokeBundlePreviewUrls();
  stagedImages = [];
  stagedDocs = [];
  mainImageIndex = 0;
  bundleStage = 'gather';

  populateLocationDropdowns();
  const locSelect = document.getElementById('bundle-location-select');
  if (locSelect) {
    locSelect.value = defaultLocationId ? String(defaultLocationId) : '';
  }

  for (const id of [
    'bundle-urls-input',
    'bundle-text-input',
    'bundle-item-name',
    'bundle-item-qty',
    'bundle-item-category',
    'bundle-item-part',
    'bundle-item-tags',
    'bundle-item-specs',
    'bundle-item-usecase',
    'bundle-item-setup',
    'bundle-item-links',
  ]) {
    const el = document.getElementById(id);
    if (el) el.value = '';
  }
  const unitEl = document.getElementById('bundle-item-unit');
  if (unitEl) unitEl.value = 'pcs';
  const fileInput = document.getElementById('bundle-files-input');
  if (fileInput) fileInput.value = '';
  const statusBanner = document.getElementById('bundle-scan-banner');
  if (statusBanner) statusBanner.style.display = 'none';
  const gatherErr = document.getElementById('bundle-gather-error');
  if (gatherErr) {
    gatherErr.style.display = 'none';
    gatherErr.textContent = '';
  }
  const reviewAlert = document.getElementById('bundle-review-alert');
  if (reviewAlert) {
    reviewAlert.style.display = 'none';
    reviewAlert.textContent = '';
  }

  renderBundleStagedLists();
  syncBundleStageUI();
  markModalClean('item-bundle-modal');
  document.getElementById('item-bundle-modal')?.classList.add('open');
  await loadBundleProviderStatus();
}

export function triggerBundleFileInput() {
  document.getElementById('bundle-files-input')?.click();
}

export function handleBundleFilesSelected(e) {
  const files = Array.from(e?.target?.files || []).filter(Boolean);
  if (e?.target) e.target.value = '';
  addFilesToBundleStaging(files);
}

export async function pasteBundleImage() {
  if (navigator.clipboard && typeof navigator.clipboard.read === 'function') {
    try {
      const items = await navigator.clipboard.read();
      for (const clipItem of items) {
        const imgType = (clipItem.types || []).find(t => t.startsWith('image/'));
        if (imgType) {
          const blob = await clipItem.getType(imgType);
          if (blob) {
            const ext = (imgType.split('/')[1] || 'png').replace(/[^a-z0-9]/gi, '') || 'png';
            const f = new File([blob], `pasted-screenshot.${ext}`, { type: imgType });
            addFilesToBundleStaging([f]);
            return;
          }
        }
      }
    } catch {
      // Clipboard read permission denied over HTTP — fall back to Ctrl+V prompt
    }
  }
  showToast('Press Ctrl+V to paste a screenshot or image into this window.');
}

export function startBundlePhoneCapture() {
  openItemPhoneCaptureModal({
    itemId: 0,
    itemName: 'New Item Import',
    onDone: async ({ photos }) => {
      if (!photos || !photos.length) return;
      for (const p of photos) {
        if (!p || !p.original_path) continue;
        stagedImages.push({
          kind: 'uploaded',
          file: null,
          previewUrl: p.thumbnail_path || p.original_path,
          originalPath: p.original_path,
          thumbnailPath: p.thumbnail_path || null,
          name: 'Phone photo',
        });
      }
      renderBundleStagedLists();
      markModalDirty('item-bundle-modal');
      showToast(`Added ${pluralize(photos.length, 'phone photo')}!`);
    },
  });
}

export function setBundleMainImage(idx) {
  if (idx >= 0 && idx < stagedImages.length) {
    mainImageIndex = idx;
    renderBundleStagedLists();
  }
}

export function removeBundleImage(idx) {
  if (idx < 0 || idx >= stagedImages.length) return;
  const [removed] = stagedImages.splice(idx, 1);
  if (removed?.previewUrl && String(removed.previewUrl).startsWith('blob:')) {
    try { URL.revokeObjectURL(removed.previewUrl); } catch {}
  }
  if (mainImageIndex >= stagedImages.length) {
    mainImageIndex = Math.max(0, stagedImages.length - 1);
  }
  renderBundleStagedLists();
}

export function removeBundleDoc(idx) {
  if (idx < 0 || idx >= stagedDocs.length) return;
  stagedDocs.splice(idx, 1);
  renderBundleStagedLists();
}

export function backToBundleSources() {
  bundleStage = 'gather';
  syncBundleStageUI();
}

function parseUrlLines(raw) {
  return String(raw || '')
    .split(/[\s,]+/)
    .map(s => s.trim())
    .filter(s => /^https?:\/\//i.test(s));
}

async function buildBundleFormData(provider) {
  const fd = new FormData();
  const locId = document.getElementById('bundle-location-select')?.value;
  if (locId) fd.append('location_id', String(locId));
  const urlsVal = (document.getElementById('bundle-urls-input')?.value || '').trim();
  const textVal = (document.getElementById('bundle-text-input')?.value || '').trim();
  if (urlsVal) fd.append('urls', urlsVal);
  if (textVal) fd.append('pasted_text', textVal);
  if (provider) fd.append('provider', provider);

  for (const img of stagedImages) {
    if (img.kind === 'uploaded' && img.originalPath) {
      fd.append('photo_paths', img.originalPath);
    } else if (img.kind === 'file' && img.file) {
      const compressed = await compressImageFile(img.file);
      fd.append('files', compressed, img.name || compressed.name || 'image.jpg');
    }
  }
  for (const doc of stagedDocs) {
    if (doc.kind === 'file' && doc.file) {
      fd.append('files', doc.file, doc.filename || doc.file.name || 'document');
    }
  }
  return fd;
}

function applyServerSavedMedia(res) {
  if (Array.isArray(res?.saved_images) && res.saved_images.length > 0) {
    const prevMainName = stagedImages[mainImageIndex]?.name;
    revokeBundlePreviewUrls();
    stagedImages = res.saved_images.map(s => ({
      kind: 'uploaded',
      file: null,
      previewUrl: s.thumbnail_path || s.file_path,
      originalPath: s.file_path,
      thumbnailPath: s.thumbnail_path || null,
      name: s.filename || 'Image',
    }));
    const matchIdx = prevMainName ? stagedImages.findIndex(i => i.name === prevMainName) : -1;
    mainImageIndex = matchIdx >= 0 ? matchIdx : Math.min(mainImageIndex, Math.max(0, stagedImages.length - 1));
  }
  if (Array.isArray(res?.saved_documents) && res.saved_documents.length > 0) {
    const existingUploaded = stagedDocs.filter(d => d.kind === 'uploaded');
    const seen = new Set(existingUploaded.map(d => d.filePath));
    const merged = [...existingUploaded];
    for (const s of res.saved_documents) {
      if (s.file_path && !seen.has(s.file_path)) {
        seen.add(s.file_path);
        merged.push({
          kind: 'uploaded',
          file: null,
          filePath: s.file_path,
          filename: s.filename,
          attachmentType: s.attachment_type || 'document',
          sizeBytes: s.size_bytes || 0,
          title: s.title || null,
        });
      }
    }
    stagedDocs = merged;
  }
  renderBundleStagedLists();
}

export async function runItemBundleScan() {
  const urlsVal = (document.getElementById('bundle-urls-input')?.value || '').trim();
  const textVal = (document.getElementById('bundle-text-input')?.value || '').trim();
  if (!stagedImages.length && !stagedDocs.length && !urlsVal && !textVal) {
    showToast('Add at least one PDF, image, link, or copied text first.', 'error');
    return;
  }

  const providerSelect = document.getElementById('bundle-vision-provider');
  let provider = providerSelect?.value || 'local';
  const readiness = await ensureVisionModelReady({
    provider,
    onSwitchProvider: (nextProv) => {
      if (providerSelect && nextProv) providerSelect.value = nextProv;
    },
    onRetry: () => runItemBundleScan(),
    onManual: () => skipBundleAiToManual(),
  });
  if (!readiness.ok) return;
  provider = readiness.provider;

  const banner = document.getElementById('bundle-scan-banner');
  const gatherErr = document.getElementById('bundle-gather-error');
  if (gatherErr) {
    gatherErr.style.display = 'none';
    gatherErr.textContent = '';
  }
  if (banner) banner.style.display = 'flex';

  return withSubmitLock('bundle-scan-btn', 'Scanning...', async () => {
    try {
      const fd = await buildBundleFormData(provider);
      const res = await apiUpload('/api/v1/items/analyze-bundle', fd, 'AI scan failed.', {
        timeoutMs: BUNDLE_SCAN_TIMEOUT_MS,
      });
      applyServerSavedMedia(res);

      if (res.status !== 'success') {
        const errMsg = res.message || 'AI scan could not read these sources. Try another AI model or click Fill Manually.';
        if (gatherErr) {
          gatherErr.textContent = errMsg;
          gatherErr.style.display = 'block';
        }
        showToast(errMsg, 'error');
        return;
      }

      document.getElementById('bundle-item-name').value = res.name || '';
      document.getElementById('bundle-item-qty').value =
        res.quantity !== null && res.quantity !== undefined ? String(res.quantity) : '';
      document.getElementById('bundle-item-unit').value = res.unit || 'pcs';
      document.getElementById('bundle-item-category').value = res.category || '';
      document.getElementById('bundle-item-part').value = res.part_number || '';
      document.getElementById('bundle-item-tags').value = res.tags || '';
      document.getElementById('bundle-item-specs').value = res.specs || '';
      document.getElementById('bundle-item-usecase').value = res.use_case || '';
      document.getElementById('bundle-item-setup').value = res.setup_info || '';
      const linksList = Array.isArray(res.reference_links) && res.reference_links.length
        ? res.reference_links
        : parseUrlLines(urlsVal);
      document.getElementById('bundle-item-links').value = linksList.join('\n');

      const alertEl = document.getElementById('bundle-review-alert');
      if (alertEl) {
        const notes = [...(res.warnings || [])];
        if (notes.length > 0) {
          alertEl.textContent = notes.join(' ');
          alertEl.style.display = 'block';
        } else {
          alertEl.style.display = 'none';
        }
      }

      bundleStage = 'review';
      syncBundleStageUI();
      const scrollBody = document.querySelector('#item-bundle-modal .modal-body-scroll');
      if (scrollBody) scrollBody.scrollTop = 0;
      showToast('AI populated item details! Review and click Save Item.');
    } catch (err) {
      const msg = err.message || 'Smart import scan failed.';
      if (gatherErr) {
        gatherErr.textContent = msg;
        gatherErr.style.display = 'block';
      }
      showToast(msg, 'error');
    } finally {
      if (banner) banner.style.display = 'none';
    }
  });
}

export async function skipBundleAiToManual() {
  const urlsVal = (document.getElementById('bundle-urls-input')?.value || '').trim();
  const textVal = (document.getElementById('bundle-text-input')?.value || '').trim();
  if (textVal && !document.getElementById('bundle-item-setup').value.trim()) {
    document.getElementById('bundle-item-setup').value = textVal;
  }
  const links = parseUrlLines(urlsVal);
  if (links.length && !document.getElementById('bundle-item-links').value.trim()) {
    document.getElementById('bundle-item-links').value = links.join('\n');
  }
  bundleStage = 'review';
  syncBundleStageUI();
}

export async function submitItemBundle() {
  const name = (document.getElementById('bundle-item-name')?.value || '').trim();
  if (!name) {
    showToast('Please enter an item name.', 'error');
    document.getElementById('bundle-item-name')?.focus();
    return;
  }

  return withSubmitLock('bundle-save-btn', 'Saving...', async () => {
    try {
      // Upload any local staged images/docs that haven't been uploaded yet (e.g. if user skipped AI)
      const imagePaths = [];
      for (let i = 0; i < stagedImages.length; i += 1) {
        const img = stagedImages[i];
        if (img.kind === 'uploaded' && img.originalPath) {
          imagePaths.push(img.originalPath);
        } else if (img.kind === 'file' && img.file) {
          const uploadedPath = await uploadPhotoAndGetPath(img.file);
          img.kind = 'uploaded';
          img.originalPath = uploadedPath;
          imagePaths.push(uploadedPath);
        }
      }
      const mainPhotoPath = imagePaths[mainImageIndex] || imagePaths[0] || null;

      // Upload any still-local docs into a staging attachment? Wait: we only have /items/{id}/attachments for raw files,
      // so for still-local docs when user skipped AI, attach them right after item creation!
      const uploadedDocs = [];
      const pendingLocalDocs = [];
      for (const doc of stagedDocs) {
        if (doc.kind === 'uploaded' && doc.filePath) {
          uploadedDocs.push({
            file_path: doc.filePath,
            filename: doc.filename || null,
            title: doc.title || null,
          });
        } else if (doc.kind === 'file' && doc.file) {
          pendingLocalDocs.push(doc);
        }
      }

      const locRaw = document.getElementById('bundle-location-select')?.value;
      const qtyRaw = (document.getElementById('bundle-item-qty')?.value || '').trim();
      const quantity = qtyRaw === '' ? null : parseInt(qtyRaw, 10);

      const payload = {
        name,
        location_id: locRaw ? parseInt(locRaw, 10) : null,
        quantity: Number.isNaN(quantity) ? null : quantity,
        unit: (document.getElementById('bundle-item-unit')?.value || '').trim() || 'pcs',
        category: (document.getElementById('bundle-item-category')?.value || '').trim() || null,
        serial_or_part_number: (document.getElementById('bundle-item-part')?.value || '').trim() || null,
        tags: (document.getElementById('bundle-item-tags')?.value || '').trim() || null,
        specs: (document.getElementById('bundle-item-specs')?.value || '').trim() || null,
        use_case: (document.getElementById('bundle-item-usecase')?.value || '').trim() || null,
        setup_info: (document.getElementById('bundle-item-setup')?.value || '').trim() || null,
        reference_links: parseUrlLines(document.getElementById('bundle-item-links')?.value || ''),
        main_photo_path: mainPhotoPath,
        image_paths: imagePaths,
        documents: uploadedDocs,
      };

      const created = await apiPost('/api/v1/items/from-bundle', payload, 'Could not save item.');

      for (const doc of pendingLocalDocs) {
        const fd = new FormData();
        fd.append('file', doc.file, doc.filename || doc.file.name || 'document');
        if (doc.title) fd.append('title', doc.title);
        await apiUpload(`/api/v1/items/${created.id}/attachments`, fd, 'Could not attach document.');
      }

      revokeBundlePreviewUrls();
      closeModal('item-bundle-modal', true);
      showToast(`Saved "${created.name}"!`);
      await loadAllData();
      if (created && created.id) {
        navToItem(created.id, true);
      }
    } catch (err) {
      showToast(err.message || 'Could not save item.', 'error');
    }
  });
}
