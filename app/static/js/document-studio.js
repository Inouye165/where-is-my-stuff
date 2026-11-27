/**
 * Item Document Studio — 4 Capture Sources (File, Scanner, Webcam, Phone QR),
 * Interactive 4-Corner Straightening & CV Enhancement, and Dual-PDF Export
 * (Enhanced Scan PDF + Crisp Vector Typeset PDF).
 */
import { apiGet, apiPost, apiUpload } from './api.js';
import {
  closeModal,
  registerModalForceCloseHook,
  sanitizePhotoUrl,
  showToast,
  withSubmitLock,
} from './dom.js';
import { compressImageFile } from './photo-compress.js';
import { loadAllData, state } from './state.js';

const IMAGE_EXT_RE = /\.(jpe?g|png|webp|gif|bmp|tiff?)$/i;
const QUAD_LABELS = ['TL', 'TR', 'BR', 'BL'];

const DEFAULT_OPTIONS = () => ({
  preset: 'auto_document',
  auto_perspective: true,
  quad: null,
  auto_orient: false,
  auto_deskew: true,
  rotation_deg: 0,
  fine_angle_deg: 0,
  aspect_mode: 'auto',
  remove_shading: true,
  shading_strength: 0.85,
  denoise_strength: 0.45,
  sharpen_strength: 0.4,
  white_balance: true,
  max_dimension: 4096,
});

const studioState = {
  itemId: null,
  itemName: 'Item',
  stage: 'capture', // 'capture' | 'enhance'
  pages: [],
  activePageIndex: -1,
  captureSource: 'file', // 'file' | 'scanner' | 'camera' | 'phone'
  viewMode: 'enhanced', // 'enhanced' | 'original' | 'compare' | 'quad' | 'typeset'
  compareSplit: 0.5,
  zoom: 1.0,
  panX: 0.5, // normalized 0..1 center X of visible viewport
  panY: 0.5, // normalized 0..1 center Y of visible viewport
  panStart: null,
  dragTarget: null, // 'split' | 'pan' | 'quad-0'..'quad-3'
  cameraStream: null,
  stitchStream: null,
  stitchHighlightBbox: null,
  stitchHighlightTimer: null,
  cameraCrop: { x: 0, y: 0, w: 1, h: 1 }, // normalized 0..1 of actual video frame
  cameraCropDrag: null,
  qrToken: null,
  qrPollTimer: null,
  qrSeenCount: 0,
  typesetPreviewUrl: null,
  domBound: false,
  canvasSeq: 0,
  scannersLoaded: false,
};

let nextPageId = 1;

function stopWebcam() {
  if (studioState.cameraStream) {
    try {
      for (const track of studioState.cameraStream.getTracks()) {
        track.stop();
      }
    } catch { }
    studioState.cameraStream = null;
  }
  const video = document.getElementById('doc-studio-video');
  if (video) {
    video.srcObject = null;
  }
}

function stopQrPolling() {
  if (studioState.qrPollTimer) {
    clearInterval(studioState.qrPollTimer);
    studioState.qrPollTimer = null;
  }
  studioState.qrToken = null;
  studioState.qrSeenCount = 0;
}

function clearTypesetPreview() {
  if (studioState.typesetPreviewUrl) {
    try { URL.revokeObjectURL(studioState.typesetPreviewUrl); } catch { }
    studioState.typesetPreviewUrl = null;
  }
  const frame = document.getElementById('doc-studio-pdf-preview');
  if (frame) {
    frame.src = 'about:blank';
    frame.style.display = 'none';
  }
}

function stopStitchCamera() {
  if (studioState.stitchStream) {
    try {
      for (const track of studioState.stitchStream.getTracks()) {
        track.stop();
      }
    } catch { }
    studioState.stitchStream = null;
  }
  const video = document.getElementById('doc-stitch-video');
  if (video) video.srcObject = null;
  const panel = document.getElementById('doc-stitch-panel');
  if (panel) panel.style.display = 'none';
}

function cleanupStudioResources() {
  stopWebcam();
  stopStitchCamera();
  stopQrPolling();
  clearTypesetPreview();
  if (studioState.stitchHighlightTimer) {
    clearTimeout(studioState.stitchHighlightTimer);
    studioState.stitchHighlightTimer = null;
  }
  studioState.stitchHighlightBbox = null;
}

registerModalForceCloseHook('document-studio-modal', cleanupStudioResources);

function activePage() {
  if (studioState.activePageIndex < 0 || studioState.activePageIndex >= studioState.pages.length) {
    return null;
  }
  return studioState.pages[studioState.activePageIndex];
}

function clamp(v, lo, hi) {
  return Math.max(lo, Math.min(hi, v));
}

function loadImageElement(url) {
  return new Promise((resolve, reject) => {
    const safe = sanitizePhotoUrl(url);
    if (!safe) {
      reject(new Error('Invalid image URL'));
      return;
    }
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('Could not load image'));
    img.src = safe;
  });
}

async function addSourcePhotoPage(
  uploadMeta,
  { autoEnhance = true, autoAdvance = false, autoPerspective = true } = {}
) {
  const opts = DEFAULT_OPTIONS();
  opts.auto_perspective = Boolean(autoPerspective);
  const page = {
    id: nextPageId++,
    sourcePath: uploadMeta.original_path,
    sourceThumb: uploadMeta.thumbnail_path || uploadMeta.original_path,
    enhancedPath: null,
    enhancedThumb: null,
    quad: [
      [0.02, 0.02],
      [0.98, 0.02],
      [0.98, 0.98],
      [0.02, 0.98],
    ],
    detectedQuad: false,
    options: opts,
    width: uploadMeta.width || 0,
    height: uploadMeta.height || 0,
    deskewDeg: 0,
    enhancing: false,
  };
  studioState.pages.push(page);
  studioState.activePageIndex = studioState.pages.length - 1;
  if (autoAdvance && studioState.stage !== 'enhance') {
    await setStudioStage('enhance');
  } else {
    renderPagesStrip();
    syncControlsFromActivePage();
    await renderCanvas();
  }

  if (autoEnhance) {
    if (opts.auto_perspective) {
      await runCornerDetectAndEnhance(page.id);
    } else {
      await enhancePageById(page.id);
    }
  }
}

async function runCornerDetectAndEnhance(pageId) {
  const page = studioState.pages.find((p) => p.id === pageId);
  if (!page) return;
  page.enhancing = true;
  renderPagesStrip();
  syncStatusBadge();

  try {
    const corners = await apiPost(
      '/api/v1/documents/detect-corners',
      { photo_path: page.sourcePath },
      'Could not detect document edges.',
      { timeoutMs: 30000 }
    );
    if (Array.isArray(corners?.quad) && corners.quad.length === 4) {
      page.quad = corners.quad;
      page.detectedQuad = Boolean(corners.detected);
      if (corners.detected) {
        page.options.quad = corners.quad;
      }
    }
  } catch {
    // Non-fatal: proceed with default enhancement
  }

  await enhancePageById(pageId);
}

async function enhancePageById(pageId) {
  const page = studioState.pages.find((p) => p.id === pageId);
  if (!page) return;
  page.enhancing = true;
  renderPagesStrip();
  syncStatusBadge();

  try {
    const payloadOptions = { ...page.options };
    if (payloadOptions.auto_perspective && page.quad && page.detectedQuad) {
      payloadOptions.quad = page.quad;
    } else if (!payloadOptions.auto_perspective) {
      payloadOptions.quad = null;
    }

    const res = await apiPost(
      '/api/v1/documents/enhance',
      {
        photo_path: page.sourcePath,
        options: payloadOptions,
      },
      'Could not enhance page.',
      { timeoutMs: 45000 }
    );

    page.enhancedPath = res.enhanced_path;
    page.enhancedThumb = res.thumbnail_path || res.enhanced_path;
    page.width = res.width;
    page.height = res.height;
    page.appliedRotationDeg = Number(res.applied_rotation_deg || 0);
    page.deskewDeg = res.applied_deskew_deg || 0;
    if (Array.isArray(res.detected_quad) && res.detected_quad.length === 4) {
      page.quad = res.detected_quad;
    }
  } catch (err) {
    showToast(err.message || 'Page enhancement failed.', 'error');
  } finally {
    page.enhancing = false;
    renderPagesStrip();
    syncStatusBadge();
    if (activePage()?.id === pageId) {
      await renderCanvas();
    }
  }
}

function syncStatusBadge() {
  const el = document.getElementById('doc-studio-page-meta');
  if (!el) return;
  const page = activePage();
  if (!page) {
    el.textContent = 'Add a page from File, Scanner, Computer Camera, or Phone QR.';
    return;
  }
  if (page.enhancing) {
    el.textContent = 'Enhancing page (straightening, removing shadows & cleaning noise)…';
    return;
  }
  const dims = page.width && page.height ? `${page.width}×${page.height}px` : '';
  const skew = Math.abs(page.deskewDeg || 0) >= 0.1 ? ` · Deskew ${page.deskewDeg > 0 ? '+' : ''}${page.deskewDeg}°` : '';
  const status = page.enhancedPath ? 'Enhanced' : 'Original';
  el.textContent = `${status}${dims ? ` · ${dims}` : ''}${skew}`;
}

function renderPagesStrip() {
  const strip = document.getElementById('doc-studio-pages-strip');
  const countEl = document.getElementById('doc-studio-page-count');
  const saveBtn = document.getElementById('doc-studio-save-btn');
  const nextBtn = document.getElementById('doc-goto-enhance-btn');
  const stepEnhBtn = document.getElementById('doc-step-enhance-btn');
  const hasPages = studioState.pages.length > 0;
  const anyEnhancing = studioState.pages.some((p) => p.enhancing);

  if (countEl) {
    countEl.textContent = `${studioState.pages.length} ${studioState.pages.length === 1 ? 'page' : 'pages'}`;
  }
  if (saveBtn) {
    saveBtn.disabled = !hasPages || anyEnhancing;
  }
  if (nextBtn) {
    nextBtn.disabled = !hasPages;
    nextBtn.textContent = hasPages
      ? `Next: Enhance & Save (${studioState.pages.length}) →`
      : 'Next: Enhance & Save →';
  }
  if (stepEnhBtn) {
    stepEnhBtn.disabled = !hasPages;
  }
  if (!strip) return;

  if (!hasPages) {
    strip.innerHTML = '<div class="doc-studio-pages-empty">No pages yet. Capture or upload a page below.</div>';
    return;
  }

  strip.innerHTML = '';
  studioState.pages.forEach((p, idx) => {
    const card = document.createElement('div');
    card.className = `doc-studio-page-chip${idx === studioState.activePageIndex ? ' active' : ''}`;
    card.addEventListener('click', () => selectStudioPage(idx));

    const img = document.createElement('img');
    img.src = sanitizePhotoUrl(p.enhancedThumb || p.sourceThumb);
    img.alt = `Page ${idx + 1}`;
    card.appendChild(img);

    const badge = document.createElement('span');
    badge.className = 'doc-studio-page-num';
    badge.textContent = p.enhancing ? '…' : String(idx + 1);
    card.appendChild(badge);

    const actions = document.createElement('div');
    actions.className = 'doc-studio-page-chip-actions';

    if (idx > 0) {
      const leftBtn = document.createElement('button');
      leftBtn.type = 'button';
      leftBtn.title = 'Move page left';
      leftBtn.textContent = '‹';
      leftBtn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        moveStudioPage(idx, -1);
      });
      actions.appendChild(leftBtn);
    }

    if (idx < studioState.pages.length - 1) {
      const rightBtn = document.createElement('button');
      rightBtn.type = 'button';
      rightBtn.title = 'Move page right';
      rightBtn.textContent = '›';
      rightBtn.addEventListener('click', (ev) => {
        ev.stopPropagation();
        moveStudioPage(idx, 1);
      });
      actions.appendChild(rightBtn);
    }

    const delBtn = document.createElement('button');
    delBtn.type = 'button';
    delBtn.title = 'Remove page';
    delBtn.textContent = '×';
    delBtn.addEventListener('click', (ev) => {
      ev.stopPropagation();
      removeStudioPage(idx);
    });
    actions.appendChild(delBtn);

    card.appendChild(actions);
    strip.appendChild(card);
  });
}

async function selectStudioPage(idx) {
  if (idx < 0 || idx >= studioState.pages.length) return;
  studioState.activePageIndex = idx;
  if (studioState.stage !== 'enhance') {
    await setStudioStage('enhance');
    return;
  }
  renderPagesStrip();
  syncControlsFromActivePage();
  await renderCanvas();
}

async function moveStudioPage(idx, delta) {
  const target = idx + delta;
  if (target < 0 || target >= studioState.pages.length) return;
  const arr = studioState.pages;
  [arr[idx], arr[target]] = [arr[target], arr[idx]];
  if (studioState.activePageIndex === idx) studioState.activePageIndex = target;
  else if (studioState.activePageIndex === target) studioState.activePageIndex = idx;
  renderPagesStrip();
  await renderCanvas();
}

async function removeStudioPage(idx) {
  if (idx < 0 || idx >= studioState.pages.length) return;
  studioState.pages.splice(idx, 1);
  if (studioState.pages.length === 0) {
    studioState.activePageIndex = -1;
    if (studioState.stage === 'enhance') {
      await setStudioStage('capture');
      return;
    }
  } else if (studioState.activePageIndex >= studioState.pages.length) {
    studioState.activePageIndex = studioState.pages.length - 1;
  }
  renderPagesStrip();
  syncControlsFromActivePage();
  await renderCanvas();
}

function syncControlsFromActivePage() {
  const page = activePage();
  const opts = page ? page.options : DEFAULT_OPTIONS();

  const setVal = (id, v) => {
    const el = document.getElementById(id);
    if (el) el.value = String(v);
  };
  const setChk = (id, v) => {
    const el = document.getElementById(id);
    if (el) el.checked = Boolean(v);
  };

  setVal('doc-opt-preset', opts.preset);
  setVal('doc-opt-aspect', opts.aspect_mode);
  setChk('doc-opt-orient', opts.auto_orient);
  setChk('doc-opt-perspective', opts.auto_perspective);
  setChk('doc-opt-deskew', opts.auto_deskew);
  setChk('doc-opt-shading', opts.remove_shading);
  setChk('doc-opt-wb', opts.white_balance);
  setVal('doc-opt-shading-str', Math.round(opts.shading_strength * 100));
  setVal('doc-opt-denoise-str', Math.round(opts.denoise_strength * 100));
  setVal('doc-opt-sharpen-str', Math.round(opts.sharpen_strength * 100));

  const shVal = document.getElementById('doc-opt-shading-val');
  if (shVal) shVal.textContent = `${Math.round(opts.shading_strength * 100)}%`;
  const dnVal = document.getElementById('doc-opt-denoise-val');
  if (dnVal) dnVal.textContent = `${Math.round(opts.denoise_strength * 100)}%`;
  const spVal = document.getElementById('doc-opt-sharpen-val');
  if (spVal) spVal.textContent = `${Math.round(opts.sharpen_strength * 100)}%`;

  for (const mode of ['enhanced', 'original', 'compare', 'quad', 'typeset']) {
    const btn = document.getElementById(`doc-view-${mode}`);
    if (btn) btn.classList.toggle('active', studioState.viewMode === mode);
  }
  const applyQuadBtn = document.getElementById('doc-view-apply-quad-btn');
  if (applyQuadBtn) {
    applyQuadBtn.style.display = studioState.viewMode === 'quad' && Boolean(page) ? 'inline-flex' : 'none';
  }
  const zoomResetBtn = document.getElementById('doc-zoom-reset-btn');
  if (zoomResetBtn) {
    zoomResetBtn.textContent = `${Math.round(studioState.zoom * 100)}%`;
  }
  const undoStitchBtn = document.getElementById('doc-stitch-undo-btn');
  if (undoStitchBtn) {
    undoStitchBtn.style.display = page && Array.isArray(page.stitchHistory) && page.stitchHistory.length > 0 ? 'inline-flex' : 'none';
  }
  syncStatusBadge();
}

function resetPreviewZoom() {
  studioState.zoom = 1.0;
  studioState.panX = 0.5;
  studioState.panY = 0.5;
  const zoomResetBtn = document.getElementById('doc-zoom-reset-btn');
  if (zoomResetBtn) zoomResetBtn.textContent = '100%';
}

async function setPreviewZoom(nextZoom) {
  studioState.zoom = clamp(Math.round(nextZoom * 20) / 20, 1.0, 6.0);
  if (studioState.zoom <= 1.01) {
    studioState.zoom = 1.0;
    studioState.panX = 0.5;
    studioState.panY = 0.5;
  } else {
    const half = 0.5 / studioState.zoom;
    studioState.panX = clamp(studioState.panX, half, 1 - half);
    studioState.panY = clamp(studioState.panY, half, 1 - half);
  }
  const zoomResetBtn = document.getElementById('doc-zoom-reset-btn');
  if (zoomResetBtn) zoomResetBtn.textContent = `${Math.round(studioState.zoom * 100)}%`;
  await renderCanvas();
}

function drawRotatedImage(ctx, img, deg, targetW, targetH, zoom = 1.0, panX = 0.5, panY = 0.5) {
  const norm = ((Number(deg || 0) % 360) + 360) % 360;
  ctx.save();
  if (zoom > 1.01) {
    ctx.translate(targetW * 0.5, targetH * 0.5);
    ctx.scale(zoom, zoom);
    ctx.translate(-panX * targetW, -panY * targetH);
  }
  ctx.translate(targetW * 0.5, targetH * 0.5);
  ctx.rotate((norm * Math.PI) / 180);
  if (norm === 90 || norm === 270) {
    ctx.drawImage(img, -targetH * 0.5, -targetW * 0.5, targetH, targetW);
  } else {
    ctx.drawImage(img, -targetW * 0.5, -targetH * 0.5, targetW, targetH);
  }
  ctx.restore();
}

async function renderTypesetPdfPreview(page, seq) {
  const canvas = document.getElementById('doc-studio-canvas');
  const emptyEl = document.getElementById('doc-studio-canvas-empty');
  const frame = document.getElementById('doc-studio-pdf-preview');
  const metaEl = document.getElementById('doc-studio-page-meta');
  if (canvas) canvas.style.display = 'none';
  if (emptyEl) emptyEl.style.display = 'none';
  if (metaEl) metaEl.textContent = 'Building live Typeset Vector PDF preview (OCR + TrueType fonts)…';

  const photoPath = page.enhancedPath || page.sourcePath;
  const typesetMode = document.getElementById('doc-save-typeset-mode')?.value || 'layout_matched';
  const includeGraphics = Boolean(document.getElementById('doc-save-include-graphics')?.checked);

  try {
    const resp = await fetch('/api/v1/documents/preview-typeset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        photo_path: photoPath,
        typeset_mode: typesetMode,
        include_graphics: includeGraphics,
      }),
    });
    if (seq !== studioState.canvasSeq) return;
    if (!resp.ok) {
      throw new Error('Could not generate Typeset PDF preview.');
    }
    const words = Number(resp.headers.get('X-Wims-Ocr-Words') || 0);
    const blob = await resp.blob();
    if (seq !== studioState.canvasSeq) return;
    clearTypesetPreview();
    studioState.typesetPreviewUrl = URL.createObjectURL(blob);
    if (frame) {
      frame.src = `${studioState.typesetPreviewUrl}#view=FitH&toolbar=1&navpanes=0`;
      frame.style.display = 'block';
    }
    if (metaEl) {
      metaEl.textContent = words > 0
        ? `Typeset Vector PDF · ${words} words converted to crisp zoomable fonts`
        : 'Typeset Vector PDF · Visual page preserved (no printed text detected)';
    }
  } catch (err) {
    if (seq !== studioState.canvasSeq) return;
    if (metaEl) metaEl.textContent = err.message || 'Typeset preview failed.';
    showToast(err.message || 'Could not generate Typeset PDF preview.', 'error');
  }
}

async function renderCanvas() {
  const canvas = document.getElementById('doc-studio-canvas');
  const emptyEl = document.getElementById('doc-studio-canvas-empty');
  const frame = document.getElementById('doc-studio-pdf-preview');
  if (!canvas) return;
  const page = activePage();
  if (!page) {
    clearTypesetPreview();
    canvas.style.display = 'none';
    if (emptyEl) emptyEl.style.display = 'flex';
    return;
  }

  const seq = ++studioState.canvasSeq;
  if (studioState.viewMode === 'typeset') {
    await renderTypesetPdfPreview(page, seq);
    return;
  }

  if (frame) frame.style.display = 'none';
  canvas.style.display = 'block';
  if (emptyEl) emptyEl.style.display = 'none';

  try {
    const showQuad = studioState.viewMode === 'quad';
    const wantOriginal = showQuad || studioState.viewMode === 'original' || !page.enhancedPath;
    const primaryUrl = wantOriginal ? page.sourcePath : page.enhancedPath;
    const primaryImg = await loadImageElement(primaryUrl);
    if (seq !== studioState.canvasSeq) return;

    let compareOrigImg = null;
    if (studioState.viewMode === 'compare' && page.enhancedPath) {
      compareOrigImg = await loadImageElement(page.sourcePath).catch(() => null);
      if (seq !== studioState.canvasSeq) return;
    }

    const activeRotDeg = Number(page.appliedRotationDeg ?? page.options.rotation_deg ?? 0) % 360;
    const rotatePrimaryOrig = studioState.viewMode === 'original' && activeRotDeg !== 0;
    const effW = (rotatePrimaryOrig && (activeRotDeg === 90 || activeRotDeg === 270))
      ? primaryImg.naturalHeight
      : primaryImg.naturalWidth;
    const effH = (rotatePrimaryOrig && (activeRotDeg === 90 || activeRotDeg === 270))
      ? primaryImg.naturalWidth
      : primaryImg.naturalHeight;

    const stage = canvas.parentElement;
    const stageW = stage && stage.clientWidth > 120 ? stage.clientWidth - 16 : 760;
    const stageH = stage && stage.clientHeight > 120 ? stage.clientHeight - 16 : 560;
    const maxW = Math.max(240, stageW);
    const maxH = Math.max(240, stageH);
    const scale = Math.min(maxW / effW, maxH / effH, 1.5);
    const cw = Math.max(180, Math.round(effW * scale));
    const ch = Math.max(180, Math.round(effH * scale));
    canvas.width = cw;
    canvas.height = ch;

    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, cw, ch);
    const useZoom = showQuad ? 1.0 : studioState.zoom;
    const pX = studioState.panX;
    const pY = studioState.panY;

    if (rotatePrimaryOrig) {
      drawRotatedImage(ctx, primaryImg, activeRotDeg, cw, ch, useZoom, pX, pY);
    } else if (useZoom > 1.01) {
      // Draw exact zoomed crop directly from full-res primaryImg for razor-sharp fine print inspection
      const sw = primaryImg.naturalWidth / useZoom;
      const sh = primaryImg.naturalHeight / useZoom;
      const sx = clamp(pX * primaryImg.naturalWidth - sw * 0.5, 0, Math.max(0, primaryImg.naturalWidth - sw));
      const sy = clamp(pY * primaryImg.naturalHeight - sh * 0.5, 0, Math.max(0, primaryImg.naturalHeight - sh));
      ctx.drawImage(primaryImg, sx, sy, sw, sh, 0, 0, cw, ch);
    } else {
      ctx.drawImage(primaryImg, 0, 0, cw, ch);
    }

    if (studioState.viewMode === 'compare' && compareOrigImg) {
      const splitX = Math.round(studioState.compareSplit * cw);
      ctx.save();
      ctx.beginPath();
      ctx.rect(0, 0, splitX, ch);
      ctx.clip();
      drawRotatedImage(ctx, compareOrigImg, activeRotDeg, cw, ch, useZoom, pX, pY);
      ctx.restore();

      ctx.save();
      ctx.strokeStyle = '#38bdf8';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(splitX, 0);
      ctx.lineTo(splitX, ch);
      ctx.stroke();

      ctx.fillStyle = 'rgba(15, 23, 42, 0.8)';
      ctx.fillRect(8, 8, 62, 20);
      ctx.fillRect(Math.max(76, cw - 78), 8, 70, 20);
      ctx.fillStyle = '#f8fafc';
      ctx.font = 'bold 11px sans-serif';
      ctx.fillText('ORIGINAL', 14, 22);
      ctx.fillText('ENHANCED', Math.max(82, cw - 72), 22);
      ctx.restore();
    } else if (showQuad && Array.isArray(page.quad) && page.quad.length === 4) {
      const pts = page.quad.map((pt) => ({ x: pt[0] * cw, y: pt[1] * ch }));
      ctx.save();
      ctx.fillStyle = 'rgba(0, 0, 0, 0.42)';
      ctx.beginPath();
      ctx.rect(0, 0, cw, ch);
      ctx.moveTo(pts[0].x, pts[0].y);
      ctx.lineTo(pts[3].x, pts[3].y);
      ctx.lineTo(pts[2].x, pts[2].y);
      ctx.lineTo(pts[1].x, pts[1].y);
      ctx.closePath();
      ctx.fill('evenodd');

      ctx.strokeStyle = '#f59e0b';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(pts[0].x, pts[0].y);
      ctx.lineTo(pts[1].x, pts[1].y);
      ctx.lineTo(pts[2].x, pts[2].y);
      ctx.lineTo(pts[3].x, pts[3].y);
      ctx.closePath();
      ctx.stroke();

      pts.forEach((pt, idx) => {
        ctx.fillStyle = '#f59e0b';
        ctx.strokeStyle = '#ffffff';
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(pt.x, pt.y, 7, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
        ctx.fillStyle = '#ffffff';
        ctx.font = 'bold 10px sans-serif';
        ctx.textAlign = 'center';
        ctx.fillText(QUAD_LABELS[idx], pt.x, pt.y - 12);
      });
      ctx.restore();
    } else if (!showQuad && Array.isArray(studioState.stitchHighlightBbox) && studioState.stitchHighlightBbox.length === 4 && useZoom <= 1.01) {
      const [bx1, by1, bx2, by2] = studioState.stitchHighlightBbox;
      ctx.save();
      ctx.strokeStyle = '#22d3ee';
      ctx.lineWidth = 3;
      ctx.setLineDash([8, 5]);
      ctx.strokeRect(bx1 * cw, by1 * ch, (bx2 - bx1) * cw, (by2 - by1) * ch);
      ctx.restore();
    }
  } catch {
    // Ignore transient load error
  }
}

async function uploadImageFileForStudio(file, { skipCompress = false } = {}) {
  const prepared = skipCompress ? file : await compressImageFile(file, 4096, 0.96);
  const fd = new FormData();
  fd.append('file', prepared);
  return apiUpload(
    '/api/v1/photos/upload?max_dimension=4096&jpeg_quality=96',
    fd,
    'Could not upload page image.'
  );
}

function shouldAutoAdvance() {
  return !Boolean(document.getElementById('doc-multi-page-toggle')?.checked);
}

async function handleStudioFiles(fileList) {
  const files = Array.from(fileList || []).filter(Boolean);
  if (!files.length || !studioState.itemId) return;

  const imageFiles = files.filter((f) => (f.type && f.type.startsWith('image/')) || IMAGE_EXT_RE.test(f.name || ''));
  const docFiles = files.filter((f) => !imageFiles.includes(f));

  if (docFiles.length > 0) {
    try {
      for (const f of docFiles) {
        const fd = new FormData();
        fd.append('file', f);
        await apiUpload(`/api/v1/items/${studioState.itemId}/attachments`, fd, 'Attachment upload failed.');
      }
      showToast(`Attached ${docFiles.length} ${docFiles.length === 1 ? 'file' : 'files'} directly to item!`);
      await loadAllData();
    } catch (err) {
      showToast(err.message || 'Could not attach file.', 'error');
    }
  }

  for (const imgFile of imageFiles) {
    try {
      const uploaded = await uploadImageFileForStudio(imgFile);
      await addSourcePhotoPage(uploaded, { autoEnhance: true, autoAdvance: shouldAutoAdvance() });
    } catch (err) {
      showToast(err.message || `Failed to load ${imgFile.name}.`, 'error');
    }
  }
}

async function refreshScannersList() {
  const select = document.getElementById('doc-scanner-select');
  const statusEl = document.getElementById('doc-scanner-status');
  const scanBtn = document.getElementById('doc-scanner-scan-btn');
  if (statusEl) statusEl.textContent = 'Checking connected scanners…';

  try {
    const res = await apiGet('/api/v1/documents/scanners', 'Could not check scanners.');
    studioState.scannersLoaded = true;
    const devices = Array.isArray(res.devices) ? res.devices : [];
    if (select) {
      select.innerHTML = '';
      if (devices.length === 0) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'No scanners found';
        select.appendChild(opt);
      } else {
        for (const d of devices) {
          const opt = document.createElement('option');
          opt.value = d.id;
          opt.textContent = d.name;
          select.appendChild(opt);
        }
      }
    }
    if (scanBtn) scanBtn.disabled = devices.length === 0;
    if (statusEl) {
      statusEl.textContent = devices.length
        ? `${devices.length} ${devices.length === 1 ? 'scanner' : 'scanners'} ready (${res.backend.toUpperCase()}).`
        : (res.message || 'No hardware scanner found.');
    }
  } catch (err) {
    if (statusEl) statusEl.textContent = err.message || 'Scanner lookup failed.';
    if (scanBtn) scanBtn.disabled = true;
  }
}

async function scanFromHardwareScanner() {
  const deviceId = document.getElementById('doc-scanner-select')?.value || null;
  const dpi = Number(document.getElementById('doc-scanner-dpi')?.value || 300);
  const colorMode = document.getElementById('doc-scanner-color')?.value || 'color';
  const source = document.getElementById('doc-scanner-source')?.value || 'flatbed';
  const statusEl = document.getElementById('doc-scanner-status');

  return withSubmitLock('doc-scanner-scan-btn', 'Scanning…', async () => {
    if (statusEl) statusEl.textContent = 'Scanning page from hardware scanner…';
    try {
      const res = await apiPost(
        '/api/v1/documents/scan',
        { device_id: deviceId || null, dpi, color_mode: colorMode, source },
        'Hardware scan failed.',
        { timeoutMs: 90000 }
      );
      if (statusEl) statusEl.textContent = `Scanned from ${res.scanner_name} (${res.width}×${res.height}px)`;
      await addSourcePhotoPage(res, { autoEnhance: true, autoAdvance: shouldAutoAdvance() });
      showToast('Page scanned and enhanced!');
    } catch (err) {
      if (statusEl) statusEl.textContent = err.message || 'Scan failed.';
      showToast(err.message || 'Hardware scan failed.', 'error');
    }
  });
}

function getRenderedVideoRect() {
  const video = document.getElementById('doc-studio-video');
  const box = document.getElementById('doc-studio-video-box');
  if (!box) return { left: 0, top: 0, width: 1, height: 1 };
  const bw = box.clientWidth || 1;
  const bh = box.clientHeight || 1;
  const vw = video?.videoWidth || bw;
  const vh = video?.videoHeight || bh;
  const scale = Math.min(bw / vw, bh / vh);
  const rw = vw * scale;
  const rh = vh * scale;
  return {
    left: (bw - rw) * 0.5,
    top: (bh - rh) * 0.5,
    width: Math.max(1, rw),
    height: Math.max(1, rh),
  };
}

function syncCameraCropOverlay() {
  const overlay = document.getElementById('doc-camera-crop-overlay');
  const cropBox = document.getElementById('doc-camera-crop-box');
  if (!overlay || !cropBox) return;
  const r = getRenderedVideoRect();
  overlay.style.left = `${r.left}px`;
  overlay.style.top = `${r.top}px`;
  overlay.style.width = `${r.width}px`;
  overlay.style.height = `${r.height}px`;
  overlay.style.right = 'auto';
  overlay.style.bottom = 'auto';

  const c = studioState.cameraCrop;
  cropBox.style.left = `${c.x * 100}%`;
  cropBox.style.top = `${c.y * 100}%`;
  cropBox.style.width = `${c.w * 100}%`;
  cropBox.style.height = `${c.h * 100}%`;
}

function resetCameraCropToFullFrame() {
  studioState.cameraCrop = { x: 0, y: 0, w: 1, h: 1 };
  syncCameraCropOverlay();
}

function bindCameraCropPointerEvents() {
  const overlay = document.getElementById('doc-camera-crop-overlay');
  const cropBox = document.getElementById('doc-camera-crop-box');
  if (!overlay || !cropBox || cropBox.dataset.bound === '1') return;
  cropBox.dataset.bound = '1';

  const MIN_FRAC = 0.15;

  cropBox.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    e.stopPropagation();
    const handleEl = e.target && typeof e.target.closest === 'function' ? e.target.closest('[data-handle]') : null;
    const mode = handleEl ? handleEl.dataset.handle : 'move';
    const rect = overlay.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    cropBox.setPointerCapture(e.pointerId);
    studioState.cameraCropDrag = {
      mode,
      startX: (e.clientX - rect.left) / rect.width,
      startY: (e.clientY - rect.top) / rect.height,
      startCrop: { ...studioState.cameraCrop },
    };
  });

  cropBox.addEventListener('pointermove', (e) => {
    const drag = studioState.cameraCropDrag;
    if (!drag) return;
    const rect = overlay.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    const px = clamp((e.clientX - rect.left) / rect.width, 0, 1);
    const py = clamp((e.clientY - rect.top) / rect.height, 0, 1);
    const dx = px - drag.startX;
    const dy = py - drag.startY;
    const s = drag.startCrop;
    let { x, y, w, h } = s;

    if (drag.mode === 'move') {
      x = clamp(s.x + dx, 0, 1 - s.w);
      y = clamp(s.y + dy, 0, 1 - s.h);
    } else {
      let left = s.x;
      let top = s.y;
      let right = s.x + s.w;
      let bottom = s.y + s.h;
      if (drag.mode.includes('w')) left = clamp(s.x + dx, 0, right - MIN_FRAC);
      if (drag.mode.includes('e')) right = clamp(s.x + s.w + dx, left + MIN_FRAC, 1);
      if (drag.mode.includes('n')) top = clamp(s.y + dy, 0, bottom - MIN_FRAC);
      if (drag.mode.includes('s')) bottom = clamp(s.y + s.h + dy, top + MIN_FRAC, 1);
      x = left;
      y = top;
      w = right - left;
      h = bottom - top;
    }

    studioState.cameraCrop = { x, y, w, h };
    syncCameraCropOverlay();
  });

  const endDrag = () => {
    studioState.cameraCropDrag = null;
  };
  cropBox.addEventListener('pointerup', endDrag);
  cropBox.addEventListener('pointercancel', endDrag);
}

async function startComputerWebcam(selectedDeviceId = null) {
  stopWebcam();
  const statusEl = document.getElementById('doc-camera-status');
  const video = document.getElementById('doc-studio-video');
  const select = document.getElementById('doc-camera-select');
  const captureBtn = document.getElementById('doc-camera-capture-btn');

  if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') {
    if (statusEl) {
      statusEl.textContent = window.isSecureContext
        ? 'Browser camera API is not available on this device.'
        : 'Camera access requires HTTPS or localhost.';
    }
    if (captureBtn) captureBtn.disabled = true;
    return;
  }

  try {
    if (statusEl) statusEl.textContent = 'Starting computer camera…';
    const resPref = document.getElementById('doc-camera-res')?.value || 'auto';
    const baseDevice = selectedDeviceId ? { deviceId: { exact: selectedDeviceId } } : { facingMode: 'environment' };

    let candidates;
    if (resPref === '3840x2160') {
      candidates = [{ ...baseDevice, width: { ideal: 3840 }, height: { ideal: 2160 } }, { ...baseDevice, width: { ideal: 1920 }, height: { ideal: 1080 } }, baseDevice];
    } else if (resPref === '2560x1440') {
      candidates = [{ ...baseDevice, width: { ideal: 2560 }, height: { ideal: 1440 } }, { ...baseDevice, width: { ideal: 1920 }, height: { ideal: 1080 } }, baseDevice];
    } else if (resPref === '1920x1080') {
      candidates = [{ ...baseDevice, width: { ideal: 1920 }, height: { ideal: 1080 } }, baseDevice];
    } else {
      // Progressive fallback: try 4K UHD -> 2K QHD -> 1080p -> driver default so USB cameras never time out
      candidates = [
        { ...baseDevice, width: { ideal: 3840 }, height: { ideal: 2160 } },
        { ...baseDevice, width: { ideal: 2560 }, height: { ideal: 1440 } },
        { ...baseDevice, width: { ideal: 1920 }, height: { ideal: 1080 } },
        baseDevice,
      ];
    }

    let stream = null;
    let lastErr = null;
    for (const vc of candidates) {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ video: vc, audio: false });
        break;
      } catch (err) {
        lastErr = err;
        // Stop immediately on permission denial; otherwise try next lower resolution
        if (err && (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError')) {
          throw err;
        }
      }
    }
    if (!stream) {
      throw lastErr || new Error('Could not open computer camera.');
    }

    if (studioState.captureSource !== 'camera') {
      for (const t of stream.getTracks()) t.stop();
      return;
    }

    studioState.cameraStream = stream;
    const updateCamResText = () => {
      syncCameraCropOverlay();
      if (statusEl && video?.videoWidth && video?.videoHeight) {
        statusEl.textContent = `Camera ready (${video.videoWidth}×${video.videoHeight}px). Drag edges/corners to crop, then click Capture Page.`;
      }
    };
    if (video) {
      video.srcObject = stream;
      video.onloadedmetadata = updateCamResText;
      await video.play().catch(() => {});
      updateCamResText();
    }
    if (captureBtn) captureBtn.disabled = false;
    if (statusEl && (!video || !video.videoWidth)) {
      statusEl.textContent = 'Drag edges/corners to resize or drag inside to move capture area, then click Capture Page.';
    }

    if (select && typeof navigator.mediaDevices.enumerateDevices === 'function') {
      const allDevs = await navigator.mediaDevices.enumerateDevices();
      const cams = allDevs.filter((d) => d.kind === 'videoinput');
      if (cams.length > 0) {
        const activeId = stream.getVideoTracks()[0]?.getSettings?.().deviceId || selectedDeviceId || '';
        select.innerHTML = '';
        cams.forEach((cam, idx) => {
          const opt = document.createElement('option');
          opt.value = cam.deviceId;
          opt.textContent = cam.label || `Camera ${idx + 1}`;
          if (cam.deviceId === activeId) opt.selected = true;
          select.appendChild(opt);
        });
      }
    }
  } catch (err) {
    if (statusEl) statusEl.textContent = err.message || 'Could not open computer camera.';
    if (captureBtn) captureBtn.disabled = true;
  }
}

async function captureFromComputerWebcam() {
  const video = document.getElementById('doc-studio-video');
  if (!video || !video.videoWidth || !video.videoHeight) {
    showToast('Camera feed is not ready yet.', 'error');
    return;
  }

  return withSubmitLock('doc-camera-capture-btn', 'Capturing…', async () => {
    const c = studioState.cameraCrop;
    let sourceEl = video;
    let srcW = video.videoWidth;
    let srcH = video.videoHeight;
    let photoBitmap = null;

    // Try native high-res still capture via ImageCapture API with a short timeout guard
    const track = studioState.cameraStream?.getVideoTracks?.()[0];
    if (track && typeof window !== 'undefined' && typeof window.ImageCapture === 'function') {
      try {
        const ic = new window.ImageCapture(track);
        if (typeof ic.takePhoto === 'function') {
          const photoBlob = await Promise.race([
            ic.takePhoto(),
            new Promise((_, reject) => setTimeout(() => reject(new Error('takePhoto timeout')), 2500)),
          ]);
          if (photoBlob && typeof createImageBitmap === 'function') {
            photoBitmap = await createImageBitmap(photoBlob);
            if (photoBitmap.width >= srcW && photoBitmap.height >= srcH) {
              sourceEl = photoBitmap;
              srcW = photoBitmap.width;
              srcH = photoBitmap.height;
            }
          }
        }
      } catch {
        // Fall back seamlessly to full-res video frame
      }
    }

    const sx = Math.max(0, Math.round(c.x * srcW));
    const sy = Math.max(0, Math.round(c.y * srcH));
    const sw = Math.max(32, Math.min(srcW - sx, Math.round(c.w * srcW)));
    const sh = Math.max(32, Math.min(srcH - sy, Math.round(c.h * srcH)));
    const tmpCanvas = document.createElement('canvas');
    tmpCanvas.width = sw;
    tmpCanvas.height = sh;
    const ctx = tmpCanvas.getContext('2d');
    ctx.drawImage(sourceEl, sx, sy, sw, sh, 0, 0, sw, sh);
    if (photoBitmap && typeof photoBitmap.close === 'function') {
      photoBitmap.close();
    }

    // Export lossless PNG (falling back to 97% JPEG only if > 12MB) and skip client re-compression
    let blob = await new Promise((resolve) => tmpCanvas.toBlob(resolve, 'image/png'));
    let fileExt = 'png';
    let mimeType = 'image/png';
    if (!blob || blob.size > 12 * 1024 * 1024) {
      blob = await new Promise((resolve) => tmpCanvas.toBlob(resolve, 'image/jpeg', 0.97));
      fileExt = 'jpg';
      mimeType = 'image/jpeg';
    }
    if (!blob) {
      showToast('Could not capture frame from camera.', 'error');
      return;
    }
    const file = new File([blob], `camera-page-${Date.now()}.${fileExt}`, { type: mimeType });
    const wasManuallyCropped = c.w < 0.98 || c.h < 0.98 || c.x > 0.01 || c.y > 0.01;
    const uploaded = await uploadImageFileForStudio(file, { skipCompress: true });
    await addSourcePhotoPage(uploaded, {
      autoEnhance: true,
      autoAdvance: shouldAutoAdvance(),
      autoPerspective: !wasManuallyCropped,
    });
    showToast(`Captured page (${sw}×${sh}px)!`);
  });
}

async function startPhoneQrSession() {
  stopQrPolling();
  const qrImg = document.getElementById('doc-phone-qr-img');
  const urlEl = document.getElementById('doc-phone-qr-url');
  const statusEl = document.getElementById('doc-phone-qr-status');
  if (statusEl) statusEl.textContent = 'Generating high-res QR session…';
  if (qrImg) qrImg.style.display = 'none';

  try {
    const sess = await apiPost(
      '/api/v1/photos/capture-sessions',
      { label: `Document: ${studioState.itemName}`.slice(0, 80), high_res: true },
      'Could not start phone QR session.'
    );
    if (studioState.captureSource !== 'phone') return;

    studioState.qrToken = sess.token;
    studioState.qrSeenCount = 0;
    if (qrImg) {
      qrImg.src = `${sess.qr_url}${sess.qr_url.includes('?') ? '&' : '?'}t=${Date.now()}`;
      qrImg.style.display = 'block';
    }
    if (urlEl) urlEl.textContent = sess.capture_url;
    if (statusEl) statusEl.textContent = 'Scan with your phone — each photo streams straight into your pages.';

    const token = sess.token;
    let inFlight = false;
    studioState.qrPollTimer = setInterval(async () => {
      if (studioState.qrToken !== token || inFlight) return;
      inFlight = true;
      try {
        const res = await apiGet(`/api/v1/photos/capture-sessions/${encodeURIComponent(token)}`);
        if (studioState.qrToken !== token) return;
        const photos = Array.isArray(res.photos) ? res.photos : [];
        if (photos.length > studioState.qrSeenCount) {
          const newOnes = photos.slice(studioState.qrSeenCount);
          studioState.qrSeenCount = photos.length;
          if (statusEl) {
            statusEl.textContent = `Received ${photos.length} ${photos.length === 1 ? 'page' : 'pages'} from phone. Keep snapping or save below.`;
          }
          for (const p of newOnes) {
            await addSourcePhotoPage(p, { autoEnhance: true, autoAdvance: shouldAutoAdvance() });
          }
        }
      } catch {
        // Keep polling on transient network hiccups
      } finally {
        inFlight = false;
      }
    }, 900);
  } catch (err) {
    if (statusEl) statusEl.textContent = err.message || 'Could not start QR session.';
  }
}

async function setStudioStage(stage) {
  const target = stage === 'enhance' && studioState.pages.length > 0 ? 'enhance' : 'capture';
  studioState.stage = target;

  const capEl = document.getElementById('doc-stage-capture');
  const enhEl = document.getElementById('doc-stage-enhance');
  if (capEl) capEl.style.display = target === 'capture' ? 'flex' : 'none';
  if (enhEl) enhEl.style.display = target === 'enhance' ? 'flex' : 'none';

  document.getElementById('doc-step-capture-btn')?.classList.toggle('active', target === 'capture');
  document.getElementById('doc-step-enhance-btn')?.classList.toggle('active', target === 'enhance');

  const gotoEnhBtn = document.getElementById('doc-goto-enhance-btn');
  const saveBtn = document.getElementById('doc-studio-save-btn');
  const backBtn = document.getElementById('doc-back-to-capture-btn');
  const barAddBtn = document.getElementById('doc-bar-add-more-btn');
  if (gotoEnhBtn) gotoEnhBtn.style.display = target === 'capture' ? 'inline-flex' : 'none';
  if (saveBtn) saveBtn.style.display = target === 'enhance' ? 'inline-flex' : 'none';
  if (backBtn) backBtn.style.display = target === 'enhance' ? 'inline-flex' : 'none';
  if (barAddBtn) barAddBtn.style.display = target === 'enhance' ? 'inline-flex' : 'none';

  renderPagesStrip();

  if (target === 'enhance') {
    stopWebcam();
    stopQrPolling();
    syncControlsFromActivePage();
    await renderCanvas();
  } else {
    await setCaptureSource(studioState.captureSource);
  }
}

async function setCaptureSource(source) {
  if (!['file', 'scanner', 'camera', 'phone'].includes(source)) return;
  if (studioState.captureSource !== source) {
    stopWebcam();
    stopQrPolling();
  }
  studioState.captureSource = source;

  for (const key of ['file', 'scanner', 'camera', 'phone']) {
    document.getElementById(`doc-tab-${key}`)?.classList.toggle('active', key === source);
    const panel = document.getElementById(`doc-panel-${key}`);
    if (panel) panel.style.display = key === source ? 'block' : 'none';
  }

  if (source === 'scanner' && !studioState.scannersLoaded) {
    await refreshScannersList();
  } else if (source === 'camera') {
    await startComputerWebcam();
  } else if (source === 'phone') {
    await startPhoneQrSession();
  }
}

async function setViewMode(mode) {
  studioState.viewMode = mode;
  syncControlsFromActivePage();
  await renderCanvas();
}

function readControlsIntoActivePage() {
  const page = activePage();
  if (!page) return;
  const o = page.options;
  o.preset = document.getElementById('doc-opt-preset')?.value || 'auto_document';
  o.aspect_mode = document.getElementById('doc-opt-aspect')?.value || 'auto';
  o.auto_orient = Boolean(document.getElementById('doc-opt-orient')?.checked);
  o.auto_perspective = Boolean(document.getElementById('doc-opt-perspective')?.checked);
  o.auto_deskew = Boolean(document.getElementById('doc-opt-deskew')?.checked);
  o.remove_shading = Boolean(document.getElementById('doc-opt-shading')?.checked);
  o.white_balance = Boolean(document.getElementById('doc-opt-wb')?.checked);
  o.shading_strength = clamp(Number(document.getElementById('doc-opt-shading-str')?.value || 85) / 100, 0, 1);
  o.denoise_strength = clamp(Number(document.getElementById('doc-opt-denoise-str')?.value || 45) / 100, 0, 1);
  o.sharpen_strength = clamp(Number(document.getElementById('doc-opt-sharpen-str')?.value || 40) / 100, 0, 1);

  const shVal = document.getElementById('doc-opt-shading-val');
  if (shVal) shVal.textContent = `${Math.round(o.shading_strength * 100)}%`;
  const dnVal = document.getElementById('doc-opt-denoise-val');
  if (dnVal) dnVal.textContent = `${Math.round(o.denoise_strength * 100)}%`;
  const spVal = document.getElementById('doc-opt-sharpen-val');
  if (spVal) spVal.textContent = `${Math.round(o.sharpen_strength * 100)}%`;
}

async function applySettingsToAllPages() {
  const src = activePage();
  if (!src || studioState.pages.length === 0) return;
  readControlsIntoActivePage();
  const template = { ...src.options };
  for (const p of studioState.pages) {
    p.options = {
      ...template,
      quad: p.quad,
      rotation_deg: p.options.rotation_deg,
    };
    await enhancePageById(p.id);
  }
  showToast(`Applied enhancement settings to all ${studioState.pages.length} pages!`);
}

async function saveDocumentToItem() {
  if (!studioState.itemId || studioState.pages.length === 0) {
    showToast('Add at least one document page first.', 'error');
    return;
  }
  const saveEnhanced = Boolean(document.getElementById('doc-save-enhanced-pdf')?.checked);
  const saveTypeset = Boolean(document.getElementById('doc-save-typeset-pdf')?.checked);
  if (!saveEnhanced && !saveTypeset) {
    showToast('Select at least one PDF output (Enhanced Scan PDF or Typeset PDF).', 'error');
    return;
  }

  const title = (document.getElementById('doc-save-title')?.value || '').trim() || null;
  const typesetMode = document.getElementById('doc-save-typeset-mode')?.value || 'layout_matched';
  const includeGraphics = Boolean(document.getElementById('doc-save-include-graphics')?.checked);

  return withSubmitLock('doc-studio-save-btn', 'Generating PDFs…', async () => {
    try {
      const res = await apiPost(
        `/api/v1/items/${studioState.itemId}/documents`,
        {
          title,
          pages: studioState.pages.map((p) => ({
            source_path: p.sourcePath,
            enhanced_path: p.enhancedPath,
            enhance_options: p.options,
          })),
          save_enhanced_pdf: saveEnhanced,
          save_typeset_pdf: saveTypeset,
          typeset_mode: typesetMode,
          include_graphics: includeGraphics,
          auto_enhance_unprocessed: true,
        },
        'Could not save document PDFs.',
        { timeoutMs: 120000 }
      );

      const savedNames = [];
      if (res.enhanced_pdf) savedNames.push(res.enhanced_pdf.title || res.enhanced_pdf.filename);
      if (res.typeset_pdf) savedNames.push(res.typeset_pdf.title || res.typeset_pdf.filename);
      const ocrNote = res.ocr_word_count > 0 ? ` · ${res.ocr_word_count} words typeset` : '';
      showToast(`Saved ${savedNames.join(' & ')} (${res.page_count}p${ocrNote})!`);
      closeDocumentStudioModal();
      await loadAllData();
    } catch (err) {
      showToast(err.message || 'Could not save document.', 'error');
    }
  });
}

function bindCanvasPointerEvents(canvas) {
  if (!canvas || canvas.dataset.docBound === '1') return;
  canvas.dataset.docBound = '1';

  const getNormPt = (e) => {
    const rect = canvas.getBoundingClientRect();
    return {
      x: clamp((e.clientX - rect.left) / Math.max(1, rect.width), 0, 1),
      y: clamp((e.clientY - rect.top) / Math.max(1, rect.height), 0, 1),
      px: e.clientX - rect.left,
      py: e.clientY - rect.top,
      w: rect.width,
      h: rect.height,
    };
  };

  canvas.addEventListener('wheel', (e) => {
    const page = activePage();
    if (!page || studioState.viewMode === 'quad' || studioState.viewMode === 'typeset') return;
    e.preventDefault();
    const delta = e.deltaY < 0 ? 0.35 : -0.35;
    void setPreviewZoom(studioState.zoom + delta);
  }, { passive: false });

  canvas.addEventListener('pointerdown', (e) => {
    const page = activePage();
    if (!page) return;
    const pt = getNormPt(e);
    if (studioState.viewMode === 'compare') {
      const nearDivider = Math.abs(pt.x - studioState.compareSplit) * pt.w <= 24;
      if (nearDivider || studioState.zoom <= 1.01) {
        canvas.setPointerCapture(e.pointerId);
        studioState.dragTarget = 'split';
        studioState.compareSplit = pt.x;
        renderCanvas();
        return;
      }
    }
    if (studioState.viewMode === 'quad' && Array.isArray(page.quad)) {
      for (let i = 0; i < 4; i++) {
        const hx = page.quad[i][0] * pt.w;
        const hy = page.quad[i][1] * pt.h;
        if (Math.hypot(pt.px - hx, pt.py - hy) <= 18) {
          canvas.setPointerCapture(e.pointerId);
          studioState.dragTarget = `quad-${i}`;
          return;
        }
      }
      return;
    }
    if (studioState.zoom > 1.01) {
      canvas.setPointerCapture(e.pointerId);
      studioState.dragTarget = 'pan';
      studioState.panStart = { x: pt.x, y: pt.y, panX: studioState.panX, panY: studioState.panY };
    }
  });

  canvas.addEventListener('pointermove', (e) => {
    if (!studioState.dragTarget) return;
    const page = activePage();
    if (!page) return;
    const pt = getNormPt(e);
    if (studioState.dragTarget === 'split') {
      studioState.compareSplit = pt.x;
      renderCanvas();
    } else if (studioState.dragTarget === 'pan' && studioState.panStart) {
      const half = 0.5 / studioState.zoom;
      const dx = (pt.x - studioState.panStart.x) / studioState.zoom;
      const dy = (pt.y - studioState.panStart.y) / studioState.zoom;
      studioState.panX = clamp(studioState.panStart.panX - dx, half, 1 - half);
      studioState.panY = clamp(studioState.panStart.panY - dy, half, 1 - half);
      renderCanvas();
    } else if (studioState.dragTarget.startsWith('quad-')) {
      const idx = Number(studioState.dragTarget.split('-')[1]);
      page.quad[idx] = [pt.x, pt.y];
      page.detectedQuad = true;
      page.options.quad = page.quad;
      renderCanvas();
    }
  });

  const endDrag = () => {
    studioState.dragTarget = null;
    studioState.panStart = null;
  };
  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);
}

async function openStitchCloseupPanel() {
  const page = activePage();
  if (!page) return;
  const panel = document.getElementById('doc-stitch-panel');
  const video = document.getElementById('doc-stitch-video');
  const statusEl = document.getElementById('doc-stitch-status');
  const capBtn = document.getElementById('doc-stitch-capture-btn');
  if (!panel) return;
  panel.style.display = 'flex';

  stopStitchCamera();
  panel.style.display = 'flex';

  if (!navigator.mediaDevices || typeof navigator.mediaDevices.getUserMedia !== 'function') {
    if (statusEl) statusEl.textContent = 'Camera unavailable — use "Upload Close-Up" to stitch a close-up photo.';
    if (capBtn) capBtn.disabled = true;
    return;
  }

  const selectedDeviceId = document.getElementById('doc-camera-select')?.value || null;
  const baseDevice = selectedDeviceId ? { deviceId: { exact: selectedDeviceId } } : { facingMode: 'environment' };
  const candidates = [
    { ...baseDevice, width: { ideal: 3840 }, height: { ideal: 2160 } },
    { ...baseDevice, width: { ideal: 2560 }, height: { ideal: 1440 } },
    { ...baseDevice, width: { ideal: 1920 }, height: { ideal: 1080 } },
    baseDevice,
  ];
  let stream = null;
  for (const vc of candidates) {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: vc, audio: false });
      break;
    } catch (err) {
      if (err && (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError')) break;
    }
  }
  if (!stream) {
    if (statusEl) statusEl.textContent = 'Could not open camera — use "Upload Close-Up" instead.';
    if (capBtn) capBtn.disabled = true;
    return;
  }
  studioState.stitchStream = stream;
  if (video) {
    video.srcObject = stream;
    await video.play().catch(() => {});
  }
  if (capBtn) capBtn.disabled = false;
  if (statusEl) {
    statusEl.textContent = 'Move camera closer to the unclear text (include a little surrounding text) and click Capture & Stitch.';
  }
}

async function runStitchWithUploadedPatch(patchPath) {
  const page = activePage();
  if (!page) return;
  const statusEl = document.getElementById('doc-stitch-status');
  if (statusEl) statusEl.textContent = 'Aligning features & seamlessly stitching high-res detail…';

  const basePath = page.enhancedPath || page.sourcePath;
  const res = await apiPost(
    '/api/v1/documents/stitch-patch',
    {
      base_photo_path: basePath,
      patch_photo_path: patchPath,
      enhance_patch: true,
      max_dimension: 4096,
    },
    'Could not align and stitch close-up patch.',
    { timeoutMs: 60000 }
  );

  if (!Array.isArray(page.stitchHistory)) page.stitchHistory = [];
  page.stitchHistory.push({
    enhancedPath: page.enhancedPath,
    enhancedThumb: page.enhancedThumb,
    width: page.width,
    height: page.height,
  });
  page.enhancedPath = res.stitched_path;
  page.enhancedThumb = res.thumbnail_path || res.stitched_path;
  page.width = res.width;
  page.height = res.height;

  studioState.stitchHighlightBbox = res.patch_bbox_norm || null;
  if (studioState.stitchHighlightTimer) clearTimeout(studioState.stitchHighlightTimer);
  studioState.stitchHighlightTimer = setTimeout(() => {
    studioState.stitchHighlightBbox = null;
    void renderCanvas();
  }, 3200);

  stopStitchCamera();
  if (studioState.viewMode === 'quad') {
    studioState.viewMode = 'enhanced';
  }
  renderPagesStrip();
  syncControlsFromActivePage();
  await renderCanvas();

  const detailNote = res.upscale_factor > 1.05 ? ` · ${res.upscale_factor}× detail` : '';
  showToast(`Stitched close-up (${res.inliers} matches${detailNote})!`);
}

async function captureAndStitchCloseup() {
  const video = document.getElementById('doc-stitch-video');
  if (!video || !video.videoWidth || !video.videoHeight) {
    showToast('Close-up camera is not ready yet.', 'error');
    return;
  }
  return withSubmitLock('doc-stitch-capture-btn', 'Stitching…', async () => {
    try {
      const tmp = document.createElement('canvas');
      tmp.width = video.videoWidth;
      tmp.height = video.videoHeight;
      tmp.getContext('2d').drawImage(video, 0, 0, tmp.width, tmp.height);
      let blob = await new Promise((resolve) => tmp.toBlob(resolve, 'image/png'));
      let ext = 'png';
      let mime = 'image/png';
      if (!blob || blob.size > 12 * 1024 * 1024) {
        blob = await new Promise((resolve) => tmp.toBlob(resolve, 'image/jpeg', 0.97));
        ext = 'jpg';
        mime = 'image/jpeg';
      }
      if (!blob) throw new Error('Could not capture close-up frame.');
      const file = new File([blob], `closeup-${Date.now()}.${ext}`, { type: mime });
      const uploaded = await uploadImageFileForStudio(file, { skipCompress: true });
      await runStitchWithUploadedPatch(uploaded.original_path);
    } catch (err) {
      const statusEl = document.getElementById('doc-stitch-status');
      if (statusEl) statusEl.textContent = err.message || 'Close-up stitch failed.';
      showToast(err.message || 'Could not stitch close-up.', 'error');
    }
  });
}

async function uploadAndStitchCloseup(file) {
  if (!file) return;
  const statusEl = document.getElementById('doc-stitch-status');
  if (statusEl) statusEl.textContent = 'Uploading close-up photo…';
  try {
    const uploaded = await uploadImageFileForStudio(file, { skipCompress: file.size < 12 * 1024 * 1024 });
    await runStitchWithUploadedPatch(uploaded.original_path);
  } catch (err) {
    if (statusEl) statusEl.textContent = err.message || 'Close-up stitch failed.';
    showToast(err.message || 'Could not stitch close-up.', 'error');
  }
}

async function undoLastStitch() {
  const page = activePage();
  if (!page || !Array.isArray(page.stitchHistory) || page.stitchHistory.length === 0) return;
  const prev = page.stitchHistory.pop();
  page.enhancedPath = prev.enhancedPath;
  page.enhancedThumb = prev.enhancedThumb;
  page.width = prev.width;
  page.height = prev.height;
  studioState.stitchHighlightBbox = null;
  renderPagesStrip();
  syncControlsFromActivePage();
  await renderCanvas();
  showToast('Reverted last stitched close-up.');
}

function bindStudioDomEventsOnce() {
  if (studioState.domBound || typeof document === 'undefined') return;
  studioState.domBound = true;

  for (const src of ['file', 'scanner', 'camera', 'phone']) {
    document.getElementById(`doc-tab-${src}`)?.addEventListener('click', () => {
      void setCaptureSource(src);
    });
  }

  for (const mode of ['enhanced', 'original', 'compare', 'quad', 'typeset']) {
    document.getElementById(`doc-view-${mode}`)?.addEventListener('click', () => {
      void setViewMode(mode);
    });
  }

  for (const id of ['doc-save-typeset-mode', 'doc-save-include-graphics']) {
    document.getElementById(id)?.addEventListener('change', () => {
      if (studioState.viewMode === 'typeset') {
        void renderCanvas();
      }
    });
  }

  const fileInput = document.getElementById('doc-file-input');
  fileInput?.addEventListener('change', (e) => {
    const list = e.target.files;
    void handleStudioFiles(list);
    fileInput.value = '';
  });

  const dropZone = document.getElementById('doc-file-dropzone');
  if (dropZone) {
    dropZone.addEventListener('dragover', (e) => {
      e.preventDefault();
      dropZone.classList.add('is-dragover');
    });
    dropZone.addEventListener('dragleave', () => dropZone.classList.remove('is-dragover'));
    dropZone.addEventListener('drop', (e) => {
      e.preventDefault();
      dropZone.classList.remove('is-dragover');
      if (e.dataTransfer?.files?.length) {
        void handleStudioFiles(e.dataTransfer.files);
      }
    });
  }

  document.addEventListener('paste', (e) => {
    const modal = document.getElementById('document-studio-modal');
    if (!modal || !modal.classList.contains('open')) return;
    const files = [];
    for (const item of e.clipboardData?.items || []) {
      if (item.kind === 'file') {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
    if (files.length > 0) {
      e.preventDefault();
      void handleStudioFiles(files);
    }
  });

  document.getElementById('doc-scanner-refresh-btn')?.addEventListener('click', () => void refreshScannersList());
  document.getElementById('doc-scanner-scan-btn')?.addEventListener('click', () => void scanFromHardwareScanner());

  document.getElementById('doc-camera-select')?.addEventListener('change', (e) => {
    void startComputerWebcam(e.target.value || null);
  });
  document.getElementById('doc-camera-res')?.addEventListener('change', () => {
    const devId = document.getElementById('doc-camera-select')?.value || null;
    void startComputerWebcam(devId);
  });
  document.getElementById('doc-camera-retry-btn')?.addEventListener('click', () => {
    const devId = document.getElementById('doc-camera-select')?.value || null;
    void startComputerWebcam(devId);
  });
  document.getElementById('doc-camera-capture-btn')?.addEventListener('click', () => void captureFromComputerWebcam());
  document.getElementById('doc-camera-reset-crop-btn')?.addEventListener('click', () => resetCameraCropToFullFrame());
  document.getElementById('doc-studio-expand-btn')?.addEventListener('click', () => void toggleDocumentStudioFullscreen());
  document.getElementById('doc-step-capture-btn')?.addEventListener('click', () => void setStudioStage('capture'));
  document.getElementById('doc-step-enhance-btn')?.addEventListener('click', () => void setStudioStage('enhance'));
  document.getElementById('doc-goto-enhance-btn')?.addEventListener('click', () => void setStudioStage('enhance'));
  document.getElementById('doc-back-to-capture-btn')?.addEventListener('click', () => void setStudioStage('capture'));
  document.getElementById('doc-bar-add-more-btn')?.addEventListener('click', () => void setStudioStage('capture'));
  document.getElementById('doc-view-apply-quad-btn')?.addEventListener('click', async () => {
    const p = activePage();
    if (!p) return;
    p.options.auto_perspective = true;
    p.detectedQuad = true;
    p.options.quad = p.quad;
    studioState.viewMode = 'enhanced';
    syncControlsFromActivePage();
    await enhancePageById(p.id);
  });
  document.getElementById('doc-phone-qr-refresh-btn')?.addEventListener('click', () => void startPhoneQrSession());
  bindCameraCropPointerEvents();

  if (typeof window !== 'undefined') {
    window.addEventListener('resize', () => {
      const modal = document.getElementById('document-studio-modal');
      if (!modal || !modal.classList.contains('open')) return;
      if (studioState.captureSource === 'camera') syncCameraCropOverlay();
      void renderCanvas();
    });
  }

  document.getElementById('doc-opt-rot-ccw')?.addEventListener('click', () => {
    const p = activePage();
    if (!p) return;
    const baseRot = p.options.auto_orient && p.options.rotation_deg === 0
      ? Number(p.appliedRotationDeg || 0)
      : Number(p.options.rotation_deg || 0);
    p.options.auto_orient = false;
    p.options.rotation_deg = (baseRot + 270) % 360;
    syncControlsFromActivePage();
    void enhancePageById(p.id);
  });
  document.getElementById('doc-opt-rot-cw')?.addEventListener('click', () => {
    const p = activePage();
    if (!p) return;
    const baseRot = p.options.auto_orient && p.options.rotation_deg === 0
      ? Number(p.appliedRotationDeg || 0)
      : Number(p.options.rotation_deg || 0);
    p.options.auto_orient = false;
    p.options.rotation_deg = (baseRot + 90) % 360;
    syncControlsFromActivePage();
    void enhancePageById(p.id);
  });
  document.getElementById('doc-opt-rot-180')?.addEventListener('click', () => {
    const p = activePage();
    if (!p) return;
    const baseRot = p.options.auto_orient && p.options.rotation_deg === 0
      ? Number(p.appliedRotationDeg || 0)
      : Number(p.options.rotation_deg || 0);
    p.options.auto_orient = false;
    p.options.rotation_deg = (baseRot + 180) % 360;
    syncControlsFromActivePage();
    void enhancePageById(p.id);
  });

  for (const id of [
    'doc-opt-preset',
    'doc-opt-aspect',
    'doc-opt-orient',
    'doc-opt-perspective',
    'doc-opt-deskew',
    'doc-opt-shading',
    'doc-opt-wb',
  ]) {
    document.getElementById(id)?.addEventListener('change', () => {
      const p = activePage();
      if (!p) return;
      readControlsIntoActivePage();
      void enhancePageById(p.id);
    });
  }

  for (const id of ['doc-opt-shading-str', 'doc-opt-denoise-str', 'doc-opt-sharpen-str']) {
    document.getElementById(id)?.addEventListener('input', () => readControlsIntoActivePage());
    document.getElementById(id)?.addEventListener('change', () => {
      const p = activePage();
      if (!p) return;
      readControlsIntoActivePage();
      void enhancePageById(p.id);
    });
  }

  document.getElementById('doc-opt-reapply-btn')?.addEventListener('click', () => {
    const p = activePage();
    if (!p) return;
    readControlsIntoActivePage();
    void enhancePageById(p.id);
  });
  document.getElementById('doc-opt-apply-all-btn')?.addEventListener('click', () => void applySettingsToAllPages());
  document.getElementById('doc-studio-save-btn')?.addEventListener('click', () => void saveDocumentToItem());

  document.getElementById('doc-zoom-in-btn')?.addEventListener('click', () => void setPreviewZoom(studioState.zoom + 0.5));
  document.getElementById('doc-zoom-out-btn')?.addEventListener('click', () => void setPreviewZoom(studioState.zoom - 0.5));
  document.getElementById('doc-zoom-reset-btn')?.addEventListener('click', () => void setPreviewZoom(1.0));
  document.getElementById('doc-stitch-open-btn')?.addEventListener('click', () => void openStitchCloseupPanel());
  document.getElementById('doc-stitch-close-btn')?.addEventListener('click', () => stopStitchCamera());
  document.getElementById('doc-stitch-capture-btn')?.addEventListener('click', () => void captureAndStitchCloseup());
  document.getElementById('doc-stitch-undo-btn')?.addEventListener('click', () => void undoLastStitch());
  const stitchFileInput = document.getElementById('doc-stitch-file-input');
  stitchFileInput?.addEventListener('change', (e) => {
    const f = e.target?.files?.[0];
    stitchFileInput.value = '';
    if (f) void uploadAndStitchCloseup(f);
  });

  bindCanvasPointerEvents(document.getElementById('doc-studio-canvas'));
}

export async function openDocumentStudioModal(itemId, { initialPhotoUrls = [], defaultTitle = '' } = {}) {
  const safeId = Number(itemId);
  if (!safeId) return;
  bindStudioDomEventsOnce();
  cleanupStudioResources();

  const item = state.items.find((it) => Number(it.id) === safeId);
  studioState.itemId = safeId;
  studioState.itemName = item ? item.name : 'Item';
  studioState.pages = [];
  studioState.activePageIndex = -1;
  studioState.viewMode = 'enhanced';
  studioState.compareSplit = 0.5;
  resetPreviewZoom();

  const subtitleEl = document.getElementById('doc-studio-subtitle');
  if (subtitleEl) {
    subtitleEl.textContent = `Capture, straighten, enhance & build crisp PDFs for ${studioState.itemName}`;
  }
  const titleInput = document.getElementById('doc-save-title');
  if (titleInput) {
    titleInput.value = defaultTitle || `${studioState.itemName} Document`;
  }
  const enhCheck = document.getElementById('doc-save-enhanced-pdf');
  if (enhCheck) enhCheck.checked = true;
  const typCheck = document.getElementById('doc-save-typeset-pdf');
  if (typCheck) typCheck.checked = true;

  const card = document.getElementById('doc-studio-card');
  const expandBtn = document.getElementById('doc-studio-expand-btn');
  if (card) card.classList.remove('is-fullscreen');
  if (expandBtn) expandBtn.textContent = 'Full screen';
  resetCameraCropToFullFrame();

  renderPagesStrip();
  syncControlsFromActivePage();
  await renderCanvas();

  document.getElementById('document-studio-modal')?.classList.add('open');

  const validInitial = (initialPhotoUrls || []).map((u) => sanitizePhotoUrl(u)).filter(Boolean);
  if (validInitial.length > 0) {
    for (const safeUrl of validInitial) {
      await addSourcePhotoPage({ original_path: safeUrl, thumbnail_path: safeUrl }, { autoEnhance: true });
    }
    await setStudioStage('enhance');
  } else {
    await setStudioStage('capture');
  }
}

export async function toggleDocumentStudioFullscreen() {
  const card = document.getElementById('doc-studio-card');
  const expandBtn = document.getElementById('doc-studio-expand-btn');
  if (!card) return;
  const isFull = card.classList.toggle('is-fullscreen');
  if (expandBtn) {
    expandBtn.textContent = isFull ? 'Windowed' : 'Full screen';
  }
  if (studioState.captureSource === 'camera') {
    syncCameraCropOverlay();
  }
  await renderCanvas();
}

export function closeDocumentStudioModal() {
  cleanupStudioResources();
  closeModal('document-studio-modal', true);
}

export function closeDocumentStudioOnScrim(e) {
  if (e && e.target && e.target.id === 'document-studio-modal') {
    closeDocumentStudioModal();
  }
}

export async function createTypesetPdfFromAttachment(itemId, attachmentId) {
  const safeItemId = Number(itemId);
  const safeAttId = Number(attachmentId);
  if (!safeItemId || !safeAttId) return;

  showToast('Generating crisp Typeset PDF…');
  try {
    const res = await apiPost(
      `/api/v1/items/${safeItemId}/attachments/${safeAttId}/typeset`,
      { typeset_mode: 'layout_matched', include_graphics: true, enhance_first: true },
      'Could not generate Typeset PDF.',
      { timeoutMs: 120000 }
    );
    const createdTitle = res.typeset_pdf?.title || res.typeset_pdf?.filename || 'Typeset PDF';
    const wordNote = res.ocr_word_count > 0 ? ` (${res.ocr_word_count} words)` : '';
    showToast(`Created "${createdTitle}"${wordNote}!`);
    await loadAllData();
  } catch (err) {
    showToast(err.message || 'Could not create Typeset PDF.', 'error');
  }
}
