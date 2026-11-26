/**
 * Auto-Label Compartments from Photo (QR Handoff + 4-Corner Align + Traffic-Light Review)
 */
import { apiGet, apiPost, apiUpload } from './api.js';
import {
  closeModal,
  escapeHtml,
  openLightbox,
  registerModalForceCloseHook,
  sanitizePhotoUrl,
  showToast,
} from './dom.js';
import { registerActions } from './events.js';
import { colToLetter, parseGridConfig } from './grid-designer.js';
import { lucideIcon } from './icons.js';
import { compressImageFile } from './photo-compress.js';
import { loadAllData, state } from './state.js';

const POLL_MS = 1200;
const QUAD_LABELS = ['TL', 'TR', 'BR', 'BL'];
const DEFAULT_QUAD = () => [
  { x: 0.06, y: 0.06 },
  { x: 0.94, y: 0.06 },
  { x: 0.94, y: 0.94 },
  { x: 0.06, y: 0.94 },
];

const scanState = {
  open: false,
  seq: 0,
  containerId: null,
  containerName: '',
  hasExistingGrid: false,
  gridRows: 4,
  gridCols: 4,
  token: null,
  qrUrl: '',
  mobileUrl: '',
  stage: 'qr', // 'qr' | 'align' | 'review'
  pollTimer: null,
  pollInFlight: false,
  activePhotoPath: '',
  activeZone: null, // null for full overview, or CloseupZone object
  knownCloseupPaths: new Set(),
  quad: DEFAULT_QUAD(),
  alignStep: 'corners', // 'corners' | 'grid'
  alignZoom: 1,
  loupePoint: null, // { x, y } normalized 0..1 on image while dragging
  rowBoundaries: [], // length subRows + 1, values 0..1
  colBoundaries: [], // length subCols + 1, values 0..1
  pinnedRowBreaks: new Set(),
  pinnedColBreaks: new Set(),
  dragIdx: -1,
  dragRowIdx: -1,
  dragColIdx: -1,
  alignImg: null,
  results: [],
  closeupZones: [],
  summary: { total: 0, green: 0, yellow: 0, red: 0, unlabeled: 0 },
  engineUsed: '',
  tierFilter: 'all',
  overwriteExisting: false,
  createStarterItems: false,
  selectedCoords: new Set(),
  drafts: new Map(), // coord -> { name, description }
  busy: false,
  busyMessage: '',
};

function stopPolling() {
  if (scanState.pollTimer) {
    clearInterval(scanState.pollTimer);
    scanState.pollTimer = null;
  }
  scanState.pollInFlight = false;
}

registerModalForceCloseHook('label-scan-modal', () => {
  scanState.open = false;
  scanState.seq += 1;
  stopPolling();
  scanState.alignImg = null;
  scanState.dragIdx = -1;
});

export function closeLabelScanModal() {
  closeModal('label-scan-modal', true);
}

function getContainer() {
  return state.locationMap.get(Number(scanState.containerId)) || null;
}

function inferDefaultDimensions(container) {
  const r = Number(container?.grid_rows) || 0;
  const c = Number(container?.grid_cols) || 0;
  if (r > 0 && c > 0) {
    return { rows: r, cols: c, hasGrid: true };
  }
  const childCount = Array.isArray(container?.children) ? container.children.length : 0;
  if (childCount > 0) {
    return { rows: Math.min(26, childCount), cols: 1, hasGrid: false };
  }
  return { rows: 4, cols: 4, hasGrid: false };
}

function syncDraftsFromResults(results) {
  for (const r of results) {
    const existingDraft = scanState.drafts.get(r.coord);
    const hasName = Boolean((r.proposed_name || '').trim());
    if (!existingDraft || (!existingDraft.dirty && hasName)) {
      scanState.drafts.set(r.coord, {
        name: r.proposed_name || '',
        description: r.proposed_description || '',
        dirty: false,
      });
    }
    if (r.confidence_tier === 'green' || (r.confidence_tier === 'yellow' && hasName)) {
      if (!r.has_custom_existing_name || scanState.overwriteExisting) {
        scanState.selectedCoords.add(r.coord);
      }
    } else if (r.confidence_tier === 'red' || r.confidence_tier === 'unlabeled') {
      scanState.selectedCoords.delete(r.coord);
    }
  }
}

function startPolling(token, seq) {
  stopPolling();
  scanState.pollTimer = setInterval(async () => {
    if (!scanState.open || scanState.seq !== seq || scanState.token !== token) {
      stopPolling();
      return;
    }
    if (scanState.pollInFlight || scanState.busy) return;
    scanState.pollInFlight = true;
    try {
      const sess = await apiGet(`/api/v1/label-scan/sessions/${encodeURIComponent(token)}`);
      if (!scanState.open || scanState.seq !== seq) return;

      // Overview photo arrived while waiting in QR stage
      if (scanState.stage === 'qr' && sess.overview_photo_path && sess.overview_photo_path !== scanState.activePhotoPath) {
        scanState.activeZone = null;
        await enterAlignStage(sess.overview_photo_path, seq);
        return;
      }

      // Close-up photo arrived while in review stage
      const closeups = Array.isArray(sess.closeup_photos) ? sess.closeup_photos : [];
      const freshCloseup = closeups.find(
        (cp) => cp && cp.original_path && !scanState.knownCloseupPaths.has(cp.original_path)
      );
      if (freshCloseup) {
        scanState.knownCloseupPaths.add(freshCloseup.original_path);
        const matchingZone =
          (scanState.closeupZones || []).find((z) => z.zone_id === freshCloseup.zone_id) ||
          sess.active_zone ||
          scanState.activeZone;
        if (matchingZone) {
          scanState.activeZone = matchingZone;
        }
        await enterAlignStage(freshCloseup.original_path, seq);
      }
    } catch {
      // Transient polling errors are ignored
    } finally {
      scanState.pollInFlight = false;
    }
  }, POLL_MS);
}

function resetAxisBoundaries(count) {
  const n = Math.max(1, Number(count) || 1);
  return {
    bounds: Array.from({ length: n + 1 }, (_, i) => i / n),
    pinned: new Set([0, n]),
  };
}

function resetGridBoundaries(subRows, subCols) {
  const r = resetAxisBoundaries(subRows);
  const c = resetAxisBoundaries(subCols);
  scanState.rowBoundaries = r.bounds;
  scanState.pinnedRowBreaks = r.pinned;
  scanState.colBoundaries = c.bounds;
  scanState.pinnedColBreaks = c.pinned;
}

function interpolateAxisDrag(bounds, pinned, idx, count, rawPos) {
  let prevPin = 0;
  for (let i = idx - 1; i >= 0; i--) {
    if (pinned.has(i)) { prevPin = i; break; }
  }
  let nextPin = count;
  for (let i = idx + 1; i <= count; i++) {
    if (pinned.has(i)) { nextPin = i; break; }
  }
  const minGap = 0.015;
  const minPos = bounds[prevPin] + (idx - prevPin) * minGap;
  const maxPos = bounds[nextPin] - (nextPin - idx) * minGap;
  const clamped = Math.max(minPos, Math.min(maxPos, rawPos));
  bounds[idx] = clamped;

  for (let i = prevPin + 1; i < idx; i++) {
    const frac = (i - prevPin) / (idx - prevPin);
    bounds[i] = bounds[prevPin] + frac * (clamped - bounds[prevPin]);
  }
  for (let i = idx + 1; i < nextPin; i++) {
    const frac = (i - idx) / (nextPin - idx);
    bounds[i] = clamped + frac * (bounds[nextPin] - clamped);
  }
}

function getActiveSubGrid() {
  const z = scanState.activeZone;
  return {
    subRows: z ? (z.max_row - z.min_row + 1) : scanState.gridRows,
    subCols: z ? (z.max_col - z.min_col + 1) : scanState.gridCols,
  };
}

function getActiveSubRows() {
  return getActiveSubGrid().subRows;
}

async function enterAlignStage(photoPath, seq = scanState.seq) {
  const safePath = sanitizePhotoUrl(photoPath);
  if (!safePath) return;
  scanState.activePhotoPath = safePath;
  scanState.quad = DEFAULT_QUAD();
  scanState.alignStep = 'corners';
  scanState.alignZoom = 1;
  scanState.loupePoint = null;
  const { subRows, subCols } = getActiveSubGrid();
  resetGridBoundaries(subRows, subCols);
  scanState.stage = 'align';
  scanState.alignImg = null;
  renderModal();

  const img = new Image();
  img.onload = () => {
    if (!scanState.open || scanState.seq !== seq) return;
    scanState.alignImg = img;
    drawAlignCanvas();
  };
  img.onerror = () => {
    if (!scanState.open || scanState.seq !== seq) return;
    showToast('Could not load uploaded photo for alignment.', 'error');
  };
  img.src = safePath;
}

export async function openLabelScanModal(containerId) {
  const safeId = Number(containerId);
  const container = state.locationMap.get(safeId);
  if (!container) {
    showToast('Container not found.', 'error');
    return;
  }

  stopPolling();
  const seq = ++scanState.seq;
  const dims = inferDefaultDimensions(container);

  Object.assign(scanState, {
    open: true,
    containerId: safeId,
    containerName: container.name || 'Container',
    hasExistingGrid: dims.hasGrid,
    gridRows: dims.rows,
    gridCols: dims.cols,
    token: null,
    qrUrl: '',
    mobileUrl: '',
    stage: 'qr',
    activePhotoPath: '',
    activeZone: null,
    knownCloseupPaths: new Set(),
    quad: DEFAULT_QUAD(),
    dragIdx: -1,
    alignImg: null,
    results: [],
    closeupZones: [],
    summary: { total: 0, green: 0, yellow: 0, red: 0, unlabeled: 0 },
    engineUsed: '',
    tierFilter: 'all',
    overwriteExisting: false,
    createStarterItems: false,
    selectedCoords: new Set(),
    drafts: new Map(),
    busy: false,
    busyMessage: '',
  });

  const modal = document.getElementById('label-scan-modal');
  modal?.classList.add('open');
  renderModal();

  try {
    const res = await apiPost(
      '/api/v1/label-scan/sessions',
      { container_id: safeId },
      'Could not start label scan session.'
    );
    if (!scanState.open || scanState.seq !== seq) return;
    scanState.token = res.token;
    scanState.qrUrl = res.qr_url;
    scanState.mobileUrl = res.mobile_url;
    renderModal();
    startPolling(res.token, seq);
  } catch (err) {
    if (!scanState.open || scanState.seq !== seq) return;
    showToast(err.message || 'Could not start label scan session.', 'error');
  }
}

function bilinearPoint(quad, u, v) {
  const [tl, tr, br, bl] = quad;
  const topX = tl.x + (tr.x - tl.x) * u;
  const topY = tl.y + (tr.y - tl.y) * u;
  const botX = bl.x + (br.x - bl.x) * u;
  const botY = bl.y + (br.y - bl.y) * u;
  return {
    x: topX + (botX - topX) * v,
    y: topY + (botY - topY) * v,
  };
}

const ALIGN_PAD = 18;

function drawLoupe(ctx, img, ptNorm, cw, ch, iw, ih) {
  if (!ptNorm || !img) return;
  const natW = img.naturalWidth || img.width;
  const natH = img.naturalHeight || img.height;
  const radius = 64;
  const zoom = 2.6;

  // Place loupe in the opposite corner of the canvas from the cursor
  const cursorCanvasX = ALIGN_PAD + ptNorm.x * iw;
  const cursorCanvasY = ALIGN_PAD + ptNorm.y * ih;
  const lx = cursorCanvasX < cw * 0.5 ? cw - radius - 14 : radius + 14;
  const ly = cursorCanvasY < ch * 0.5 ? ch - radius - 14 : radius + 14;

  const srcSampleW = (radius * 2) / (zoom * (iw / natW));
  const srcSampleH = (radius * 2) / (zoom * (ih / natH));
  const sx = ptNorm.x * natW - srcSampleW / 2;
  const sy = ptNorm.y * natH - srcSampleH / 2;

  ctx.save();
  ctx.beginPath();
  ctx.arc(lx, ly, radius, 0, Math.PI * 2);
  ctx.closePath();
  ctx.fillStyle = '#060a13';
  ctx.fill();
  ctx.clip();

  ctx.drawImage(img, sx, sy, srcSampleW, srcSampleH, lx - radius, ly - radius, radius * 2, radius * 2);

  // Crosshair
  ctx.strokeStyle = 'rgba(245, 158, 11, 0.9)';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.moveTo(lx - 14, ly);
  ctx.lineTo(lx + 14, ly);
  ctx.moveTo(lx, ly - 14);
  ctx.lineTo(lx, ly + 14);
  ctx.stroke();
  ctx.restore();

  ctx.save();
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 2.5;
  ctx.beginPath();
  ctx.arc(lx, ly, radius, 0, Math.PI * 2);
  ctx.stroke();
  ctx.restore();
}

function drawAlignCanvas() {
  const canvas = document.getElementById('lscan-align-canvas');
  const img = scanState.alignImg;
  if (!canvas || !img) return;

  const maxW = Math.min(680, Math.max(280, window.innerWidth - 96)) - ALIGN_PAD * 2;
  const maxH = Math.min(480, Math.max(240, Math.round(window.innerHeight * 0.52))) - ALIGN_PAD * 2;
  const natW = img.naturalWidth || img.width;
  const natH = img.naturalHeight || img.height;
  const baseScale = Math.min(maxW / natW, maxH / natH, 1);
  const scale = baseScale * (scanState.alignZoom || 1);
  const iw = Math.max(140, Math.round(natW * scale));
  const ih = Math.max(120, Math.round(natH * scale));
  const cw = iw + ALIGN_PAD * 2;
  const ch = ih + ALIGN_PAD * 2;

  if (canvas.width !== cw || canvas.height !== ch) {
    canvas.width = cw;
    canvas.height = ch;
  }

  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, cw, ch);
  ctx.drawImage(img, ALIGN_PAD, ALIGN_PAD, iw, ih);

  const pxQuad = scanState.quad.map((p) => ({
    x: ALIGN_PAD + p.x * iw,
    y: ALIGN_PAD + p.y * ih,
  }));
  const z = scanState.activeZone;
  const subRows = z ? z.max_row - z.min_row + 1 : scanState.gridRows;
  const subCols = z ? z.max_col - z.min_col + 1 : scanState.gridCols;
  if (!Array.isArray(scanState.rowBoundaries) || scanState.rowBoundaries.length !== subRows + 1) {
    const r = resetAxisBoundaries(subRows);
    scanState.rowBoundaries = r.bounds;
    scanState.pinnedRowBreaks = r.pinned;
  }
  if (!Array.isArray(scanState.colBoundaries) || scanState.colBoundaries.length !== subCols + 1) {
    const c = resetAxisBoundaries(subCols);
    scanState.colBoundaries = c.bounds;
    scanState.pinnedColBreaks = c.pinned;
  }
  const rowBounds = scanState.rowBoundaries;
  const colBounds = scanState.colBoundaries;
  const isGridStep = scanState.alignStep === 'grid';

  // Dim area outside the 4 corners
  ctx.save();
  ctx.fillStyle = 'rgba(2, 6, 23, 0.45)';
  ctx.beginPath();
  ctx.rect(ALIGN_PAD, ALIGN_PAD, iw, ih);
  ctx.moveTo(pxQuad[0].x, pxQuad[0].y);
  ctx.lineTo(pxQuad[3].x, pxQuad[3].y);
  ctx.lineTo(pxQuad[2].x, pxQuad[2].y);
  ctx.lineTo(pxQuad[1].x, pxQuad[1].y);
  ctx.closePath();
  ctx.fill('evenodd');
  ctx.restore();

  // Draw vertical & horizontal grid lines inside the quad
  ctx.save();
  for (let c = 1; c < subCols; c++) {
    const u = colBounds[c];
    const isPinned = scanState.pinnedColBreaks.has(c);
    const p0 = bilinearPoint(pxQuad, u, 0);
    const p1 = bilinearPoint(pxQuad, u, 1);
    ctx.strokeStyle = isGridStep && isPinned ? 'rgba(16, 185, 129, 0.92)' : isGridStep ? 'rgba(56, 189, 248, 0.78)' : 'rgba(56, 189, 248, 0.42)';
    ctx.lineWidth = isGridStep && isPinned ? 2.2 : 1.2;
    ctx.beginPath();
    ctx.moveTo(p0.x, p0.y);
    ctx.lineTo(p1.x, p1.y);
    ctx.stroke();

    if (isGridStep) {
      ctx.fillStyle = scanState.dragColIdx === c ? '#f59e0b' : isPinned ? '#10b981' : '#0284c7';
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(p1.x, p1.y, 5.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
  }

  const container = getContainer();
  const breaks = !z ? parseGridConfig(container?.grid_config).section_breaks : [];
  for (let r = 1; r < subRows; r++) {
    const v = rowBounds[r];
    const isBreak = breaks.includes(r);
    const isPinned = scanState.pinnedRowBreaks.has(r);
    const p0 = bilinearPoint(pxQuad, 0, v);
    const p1 = bilinearPoint(pxQuad, 1, v);
    ctx.strokeStyle = isBreak
      ? 'rgba(245, 158, 11, 0.92)'
      : isGridStep && isPinned
        ? 'rgba(16, 185, 129, 0.92)'
        : isGridStep
          ? 'rgba(56, 189, 248, 0.78)'
          : 'rgba(56, 189, 248, 0.42)';
    ctx.lineWidth = isBreak || (isGridStep && isPinned) ? 2.2 : 1.2;
    ctx.beginPath();
    ctx.moveTo(p0.x, p0.y);
    ctx.lineTo(p1.x, p1.y);
    ctx.stroke();

    if (isGridStep) {
      ctx.fillStyle = scanState.dragRowIdx === r ? '#f59e0b' : isBreak ? '#f59e0b' : isPinned ? '#10b981' : '#0284c7';
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(p1.x, p1.y, 5.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
  }

  // Outer quad border
  ctx.strokeStyle = isGridStep ? 'rgba(56, 189, 248, 0.65)' : '#38bdf8';
  ctx.lineWidth = 2.2;
  ctx.beginPath();
  ctx.moveTo(pxQuad[0].x, pxQuad[0].y);
  for (let i = 1; i < 4; i++) ctx.lineTo(pxQuad[i].x, pxQuad[i].y);
  ctx.closePath();
  ctx.stroke();

  // Corner handles shown ONLY during 'corners' step so BR never collides with row/col handles
  if (!isGridStep) {
    pxQuad.forEach((pt, idx) => {
      ctx.fillStyle = scanState.dragIdx === idx ? '#f59e0b' : '#0ea5e9';
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, 10, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = '#ffffff';
      ctx.font = 'bold 10px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(QUAD_LABELS[idx], pt.x, pt.y);
    });
  }
  ctx.restore();

  if (scanState.loupePoint) {
    drawLoupe(ctx, img, scanState.loupePoint, cw, ch, iw, ih);
  }
}

function bindAlignCanvasEvents() {
  const canvas = document.getElementById('lscan-align-canvas');
  if (!canvas || canvas.dataset.bound === 'true') return;
  canvas.dataset.bound = 'true';

  const getNormPoint = (evt) => {
    const rect = canvas.getBoundingClientRect();
    const clientX = evt.touches ? evt.touches[0].clientX : evt.clientX;
    const clientY = evt.touches ? evt.touches[0].clientY : evt.clientY;
    const scaleX = canvas.width / Math.max(1, rect.width);
    const scaleY = canvas.height / Math.max(1, rect.height);
    const px = (clientX - rect.left) * scaleX;
    const py = (clientY - rect.top) * scaleY;
    const iw = Math.max(1, canvas.width - ALIGN_PAD * 2);
    const ih = Math.max(1, canvas.height - ALIGN_PAD * 2);
    return {
      x: Math.max(0.0, Math.min(1.0, (px - ALIGN_PAD) / iw)),
      y: Math.max(0.0, Math.min(1.0, (py - ALIGN_PAD) / ih)),
    };
  };

  const onDown = (evt) => {
    const pt = getNormPoint(evt);
    if (scanState.alignStep === 'corners') {
      let bestIdx = -1;
      let bestDist = 0.08 / Math.max(1, scanState.alignZoom * 0.6);
      scanState.quad.forEach((corner, idx) => {
        const d = Math.hypot(corner.x - pt.x, corner.y - pt.y);
        if (d < bestDist) {
          bestDist = d;
          bestIdx = idx;
        }
      });
      if (bestIdx !== -1) {
        evt.preventDefault();
        scanState.dragIdx = bestIdx;
        scanState.dragRowIdx = -1;
        scanState.dragColIdx = -1;
        scanState.loupePoint = pt;
        drawAlignCanvas();
      }
      return;
    }

    // 'grid' step: check row-divider handles (right edge) & col-divider handles (bottom edge)
    const { subRows, subCols } = getActiveSubGrid();
    const hitTol = 0.045 / Math.max(1, scanState.alignZoom * 0.6);
    let bestRow = -1;
    let bestRowDist = hitTol;
    for (let r = 1; r < subRows; r++) {
      const handlePt = bilinearPoint(scanState.quad, 1, scanState.rowBoundaries[r]);
      const d = Math.hypot(handlePt.x - pt.x, handlePt.y - pt.y);
      if (d < bestRowDist) {
        bestRowDist = d;
        bestRow = r;
      }
    }

    let bestCol = -1;
    let bestColDist = hitTol;
    for (let c = 1; c < subCols; c++) {
      const handlePt = bilinearPoint(scanState.quad, scanState.colBoundaries[c], 1);
      const d = Math.hypot(handlePt.x - pt.x, handlePt.y - pt.y);
      if (d < bestColDist) {
        bestColDist = d;
        bestCol = c;
      }
    }

    if (bestRow !== -1 && (bestCol === -1 || bestRowDist <= bestColDist)) {
      evt.preventDefault();
      scanState.dragRowIdx = bestRow;
      scanState.dragColIdx = -1;
      scanState.pinnedRowBreaks.add(bestRow);
      scanState.loupePoint = pt;
      drawAlignCanvas();
    } else if (bestCol !== -1) {
      evt.preventDefault();
      scanState.dragColIdx = bestCol;
      scanState.dragRowIdx = -1;
      scanState.pinnedColBreaks.add(bestCol);
      scanState.loupePoint = pt;
      drawAlignCanvas();
    }
  };

  const onMove = (evt) => {
    if (scanState.dragIdx >= 0) {
      evt.preventDefault();
      const pt = getNormPoint(evt);
      scanState.quad[scanState.dragIdx] = pt;
      scanState.loupePoint = pt;
      drawAlignCanvas();
      return;
    }
    if (scanState.dragRowIdx >= 1) {
      evt.preventDefault();
      const pt = getNormPoint(evt);
      const r = scanState.dragRowIdx;
      const { subRows } = getActiveSubGrid();
      const topY = scanState.quad[1].y;
      const botY = scanState.quad[2].y;
      const rawV = (pt.y - topY) / Math.max(0.05, botY - topY);
      interpolateAxisDrag(scanState.rowBoundaries, scanState.pinnedRowBreaks, r, subRows, rawV);
      scanState.loupePoint = pt;
      drawAlignCanvas();
      return;
    }
    if (scanState.dragColIdx >= 1) {
      evt.preventDefault();
      const pt = getNormPoint(evt);
      const c = scanState.dragColIdx;
      const { subCols } = getActiveSubGrid();
      const leftX = scanState.quad[3].x;
      const rightX = scanState.quad[2].x;
      const rawU = (pt.x - leftX) / Math.max(0.05, rightX - leftX);
      interpolateAxisDrag(scanState.colBoundaries, scanState.pinnedColBreaks, c, subCols, rawU);
      scanState.loupePoint = pt;
      drawAlignCanvas();
    }
  };

  const onUp = () => {
    if (scanState.dragIdx >= 0 || scanState.dragRowIdx >= 0 || scanState.dragColIdx >= 0) {
      scanState.dragIdx = -1;
      scanState.dragRowIdx = -1;
      scanState.dragColIdx = -1;
      scanState.loupePoint = null;
      drawAlignCanvas();
    }
  };

  canvas.addEventListener('mousedown', onDown);
  window.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);
  canvas.addEventListener('touchstart', onDown, { passive: false });
  window.addEventListener('touchmove', onMove, { passive: false });
  window.addEventListener('touchend', onUp);
}

async function handleDesktopPhotoUpload(file, zoneId = null) {
  if (!file || !scanState.token) return;
  scanState.busy = true;
  scanState.busyMessage = 'Uploading high-res photo...';
  renderModal();
  try {
    const prepared = await compressImageFile(file, 4096, 0.9);
    const fd = new FormData();
    fd.append('file', prepared);
    if (zoneId) fd.append('zone_id', zoneId);
    const res = await apiUpload(
      `/api/v1/label-scan/sessions/${encodeURIComponent(scanState.token)}/upload`,
      fd,
      'Could not upload photo.'
    );
    scanState.busy = false;
    scanState.busyMessage = '';
    if (zoneId && res.original_path) {
      scanState.knownCloseupPaths.add(res.original_path);
    }
    await enterAlignStage(res.original_path);
  } catch (err) {
    scanState.busy = false;
    scanState.busyMessage = '';
    renderModal();
    showToast(err.message || 'Could not upload photo.', 'error');
  }
}

async function runAnalyzeFromAlign() {
  if (!scanState.token || !scanState.activePhotoPath || scanState.busy) return;
  const seq = scanState.seq;
  scanState.busy = true;
  scanState.busyMessage = 'Reading compartment labels (OCR + AI verification)...';
  renderModal();

  const z = scanState.activeZone;
  const payload = {
    photo_path: scanState.activePhotoPath,
    quad: scanState.quad,
    grid_rows: scanState.gridRows,
    grid_cols: scanState.gridCols,
    zone_id: z ? z.zone_id : null,
    min_row: z ? z.min_row : null,
    max_row: z ? z.max_row : null,
    min_col: z ? z.min_col : null,
    max_col: z ? z.max_col : null,
    row_boundaries: scanState.rowBoundaries,
    col_boundaries: scanState.colBoundaries,
    use_ollama_refinement: true,
  };

  try {
    const res = await apiPost(
      `/api/v1/label-scan/sessions/${encodeURIComponent(scanState.token)}/analyze`,
      payload,
      'Could not analyze compartment labels.',
      { timeoutMs: 240000 }
    );
    if (!scanState.open || scanState.seq !== seq) return;
    scanState.results = Array.isArray(res.results) ? res.results : [];
    scanState.closeupZones = Array.isArray(res.closeup_zones) ? res.closeup_zones : [];
    scanState.summary = res.summary || { total: 0, green: 0, yellow: 0, red: 0, unlabeled: 0 };
    scanState.engineUsed = res.engine_used || '';
    scanState.activeZone = null;
    syncDraftsFromResults(scanState.results);
    scanState.stage = 'review';
    scanState.busy = false;
    scanState.busyMessage = '';
    renderModal();
    showToast(res.message || 'Labels analyzed!');
  } catch (err) {
    if (!scanState.open || scanState.seq !== seq) return;
    scanState.busy = false;
    scanState.busyMessage = '';
    renderModal();
    drawAlignCanvas();
    showToast(err.message || 'Label analysis failed.', 'error');
  }
}

async function sendZoneToPhone(zoneId) {
  const zone = scanState.closeupZones.find((z) => z.zone_id === zoneId);
  if (!zone || !scanState.token) return;
  try {
    await apiPost(
      `/api/v1/label-scan/sessions/${encodeURIComponent(scanState.token)}/request-closeup`,
      {
        zone_id: zone.zone_id,
        min_row: zone.min_row,
        max_row: zone.max_row,
        min_col: zone.min_col,
        max_col: zone.max_col,
        slot_coords: zone.slot_coords,
      },
      'Could not send close-up prompt to phone.'
    );
    scanState.activeZone = zone;
    renderModal();
    showToast(`Phone prompted for close-up of ${zone.label}.`);
  } catch (err) {
    showToast(err.message || 'Could not send close-up prompt to phone.', 'error');
  }
}

async function applyReviewedLabels() {
  if (!scanState.token || scanState.busy) return;
  const updates = [];
  for (const r of scanState.results) {
    if (!scanState.selectedCoords.has(r.coord)) continue;
    const draft = scanState.drafts.get(r.coord) || { name: r.proposed_name, description: r.proposed_description };
    const cleanName = String(draft.name || '').trim();
    if (!cleanName) continue;
    updates.push({
      slot_id: r.slot_id || null,
      grid_row: r.grid_row,
      grid_col: r.grid_col,
      grid_row_span: r.grid_row_span || 1,
      grid_col_span: r.grid_col_span || 1,
      name: cleanName.slice(0, 120),
      description: draft.description ? String(draft.description).trim().slice(0, 2000) : null,
      create_starter_item: Boolean(scanState.createStarterItems),
    });
  }

  if (updates.length === 0) {
    showToast('Select at least one compartment with a label before applying.', 'error');
    return;
  }

  scanState.busy = true;
  scanState.busyMessage = `Applying ${updates.length} labels...`;
  renderModal();

  try {
    const res = await apiPost(
      `/api/v1/label-scan/sessions/${encodeURIComponent(scanState.token)}/apply`,
      {
        updates,
        overwrite_existing: Boolean(scanState.overwriteExisting),
        save_grid_rows: !scanState.hasExistingGrid ? scanState.gridRows : null,
        save_grid_cols: !scanState.hasExistingGrid ? scanState.gridCols : null,
      },
      'Could not apply compartment labels.'
    );
    scanState.busy = false;
    scanState.busyMessage = '';
    await loadAllData();
    closeLabelScanModal();
    window.renderCurrentWorkspace?.();
    const parts = [`Updated ${res.updated_count} compartment(s)`];
    if (res.created_items_count > 0) parts.push(`${res.created_items_count} starter item(s) added`);
    if (res.skipped_existing_count > 0) parts.push(`${res.skipped_existing_count} custom name(s) kept`);
    showToast(parts.join(' · '));
  } catch (err) {
    scanState.busy = false;
    scanState.busyMessage = '';
    renderModal();
    showToast(err.message || 'Could not apply compartment labels.', 'error');
  }
}

function renderTierBadge(r) {
  if (r.confidence_tier === 'green') {
    return `<span class="lscan-badge is-green">Verified · ${r.confidence}%</span>`;
  }
  if (r.confidence_tier === 'yellow') {
    return `<span class="lscan-badge is-yellow">Check · ${r.confidence}%</span>`;
  }
  if (r.confidence_tier === 'red') {
    return `<span class="lscan-badge is-red">Needs close-up</span>`;
  }
  return `<span class="lscan-badge is-empty">Unlabeled</span>`;
}

function renderModal() {
  const titleEl = document.getElementById('label-scan-title');
  const subtitleEl = document.getElementById('label-scan-subtitle');
  const bodyEl = document.getElementById('label-scan-body');
  const footerEl = document.getElementById('label-scan-footer');
  if (!bodyEl || !footerEl) return;

  if (titleEl) {
    titleEl.textContent = `Auto-Label: ${scanState.containerName}`;
  }
  if (subtitleEl) {
    if (scanState.stage === 'qr') {
      subtitleEl.textContent = 'Take one clear, straight-on photo using your phone or computer.';
    } else if (scanState.stage === 'align') {
      if (scanState.alignStep === 'corners') {
        subtitleEl.textContent = scanState.activeZone
          ? `Step 1/2: Drag TL, TR, BR, BL around close-up zone ${scanState.activeZone.label}.`
          : 'Step 1/2: Drag TL, TR, BR, BL to the 4 outer corners of the compartments.';
      } else {
        subtitleEl.textContent = 'Step 2/2: Drag right-edge dots for row heights & bottom-edge dots for column widths.';
      }
    } else {
      subtitleEl.textContent = 'Green = high confidence · Yellow = verify · Red = needs close-up (never guessed).';
    }
  }

  if (scanState.busy) {
    bodyEl.innerHTML = `
      <div class="lscan-busy-box" role="status" aria-live="polite">
        <div class="app-boot-spinner" aria-hidden="true"></div>
        <div class="lscan-busy-text">${escapeHtml(scanState.busyMessage || 'Working...')}</div>
      </div>
    `;
    footerEl.innerHTML = `<button type="button" class="btn btn-secondary" data-action="lscan-close">Cancel</button>`;
    return;
  }

  if (scanState.stage === 'qr') {
    const safeQr = /^\/api\/v1\/label-scan\/sessions\/[A-Za-z0-9_-]+\/qr$/.test(scanState.qrUrl || '')
      ? scanState.qrUrl
      : '';
    bodyEl.innerHTML = `
      <div class="lscan-qr-stage">
        ${!scanState.hasExistingGrid ? `
          <div class="lscan-grid-setup-bar">
            <span>Grid layout for this container:</span>
            <label class="lscan-dim-label">Rows
              <input type="number" min="1" max="26" value="${scanState.gridRows}" data-change-action="lscan-set-rows">
            </label>
            <label class="lscan-dim-label">Cols
              <input type="number" min="1" max="26" value="${scanState.gridCols}" data-change-action="lscan-set-cols">
            </label>
          </div>
        ` : `
          <div class="lscan-grid-pill">${lucideIcon('grid', '', 14)} Using container grid (${scanState.gridCols} × ${scanState.gridRows})</div>
        `}

        <div class="lscan-qr-card">
          ${safeQr
            ? `<img class="lscan-qr-img" src="${escapeHtml(safeQr)}" alt="QR code for phone camera">`
            : `<div class="lscan-qr-placeholder">Generating QR code...</div>`}
          <div class="lscan-qr-copy">
            <strong>Scan with your phone camera</strong>
            <p>Your photo is kept at high resolution (up to 4096px) so tiny drawer stickers stay readable. Keep the phone tab open for guided close-ups if any labels are blurry.</p>
            ${scanState.mobileUrl ? `<code class="lscan-url-hint">${escapeHtml(scanState.mobileUrl)}</code>` : ''}
          </div>
        </div>

        <div class="lscan-desktop-fallback">
          <label class="btn btn-secondary btn-sm">
            ${lucideIcon('upload', '', 14)} Upload photo from this computer
            <input type="file" accept="image/*" style="display: none;" data-change-action="lscan-upload-overview">
          </label>
        </div>
      </div>
    `;
    footerEl.innerHTML = `<button type="button" class="btn btn-secondary" data-action="lscan-close">Cancel</button>`;
    return;
  }

  if (scanState.stage === 'align') {
    const isGridStep = scanState.alignStep === 'grid';
    bodyEl.innerHTML = `
      <div class="lscan-align-stage">
        ${!scanState.hasExistingGrid && !scanState.activeZone ? `
          <div class="lscan-grid-setup-bar">
            <span>Grid dimensions:</span>
            <label class="lscan-dim-label">Rows
              <input type="number" min="1" max="26" value="${scanState.gridRows}" data-change-action="lscan-set-rows">
            </label>
            <label class="lscan-dim-label">Cols
              <input type="number" min="1" max="26" value="${scanState.gridCols}" data-change-action="lscan-set-cols">
            </label>
          </div>
        ` : ''}
        <div class="lscan-align-topbar">
          <div class="lscan-align-step-pills">
            <button type="button" class="lscan-pill ${!isGridStep ? 'active' : ''}" data-action="lscan-align-step" data-args='["corners"]'>1. Corners</button>
            <button type="button" class="lscan-pill ${isGridStep ? 'active' : ''}" data-action="lscan-align-step" data-args='["grid"]'>2. Grid Lines</button>
          </div>
          <div class="lscan-zoom-group" role="group" aria-label="Zoom">
            ${[1, 1.5, 2, 3].map((z) => `
              <button type="button" class="btn btn-secondary btn-xs ${scanState.alignZoom === z ? 'active' : ''}" data-action="lscan-zoom" data-args='${JSON.stringify([z])}'>${z}×</button>
            `).join('')}
          </div>
        </div>
        <div class="lscan-canvas-frame">
          <canvas id="lscan-align-canvas"></canvas>
        </div>
        <div class="lscan-align-tools">
          <button type="button" class="btn btn-secondary btn-xs" data-action="lscan-reset-quad">Reset ${isGridStep ? 'grid lines' : 'corners'}</button>
          <span class="lscan-align-hint">${isGridStep
            ? 'Drag right-edge dots (rows) and bottom-edge dots (columns). A 2.6× magnifier follows while dragging.'
            : 'Drag TL, TR, BR, BL to the outer edges (a 2.6× magnifier follows while dragging).'}</span>
        </div>
      </div>
    `;
    footerEl.innerHTML = isGridStep ? `
      <button type="button" class="btn btn-secondary" data-action="lscan-align-step" data-args='["corners"]'>← Edit Corners</button>
      <button type="button" class="btn btn-primary" data-action="lscan-analyze">Read Labels</button>
    ` : `
      <button type="button" class="btn btn-secondary" data-action="lscan-back-prev">Back</button>
      <button type="button" class="btn btn-primary" data-action="lscan-align-step" data-args='["grid"]'>Next: Adjust Grid →</button>
    `;
    requestAnimationFrame(() => {
      bindAlignCanvasEvents();
      drawAlignCanvas();
    });
    return;
  }

  // Stage 3: Review
  const s = scanState.summary;
  const filtered = scanState.results.filter((r) => {
    if (scanState.tierFilter === 'all') return r.confidence_tier !== 'unlabeled';
    return r.confidence_tier === scanState.tierFilter;
  });

  const zonesHtml = scanState.closeupZones.length > 0 ? `
    <div class="lscan-zones-panel">
      <div class="lscan-zones-hdr">
        <strong>${lucideIcon('camera', '', 15)} ${scanState.closeupZones.length} close-up zone(s) needed</strong>
        <span>Unreadable stickers are left blank — snap a closer photo of each highlighted zone.</span>
      </div>
      <div class="lscan-zones-list">
        ${scanState.closeupZones.map((z) => {
          const isSent = scanState.activeZone && scanState.activeZone.zone_id === z.zone_id;
          return `
            <div class="lscan-zone-chip ${isSent ? 'is-active' : ''}">
              <div>
                <strong>${escapeHtml(z.label)}</strong>
                <span>${z.slot_count} ${z.slot_count === 1 ? 'compartment' : 'compartments'}</span>
              </div>
              <div class="lscan-zone-actions">
                <button type="button" class="btn btn-secondary btn-xs" data-action="lscan-send-zone" data-args='${escapeHtml(JSON.stringify([z.zone_id]))}'>
                  ${isSent ? 'Waiting on phone…' : 'Send to Phone'}
                </button>
                <label class="btn btn-secondary btn-xs">
                  Upload
                  <input type="file" accept="image/*" style="display: none;" data-change-action="lscan-upload-closeup" data-args='${escapeHtml(JSON.stringify([z.zone_id]))}'>
                </label>
              </div>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  ` : '';

  const rowsHtml = filtered.length > 0 ? filtered.map((r) => {
    const draft = scanState.drafts.get(r.coord) || { name: r.proposed_name || '', description: r.proposed_description || '' };
    const cropSafe = sanitizePhotoUrl(r.crop_photo_path || '');
    const checked = scanState.selectedCoords.has(r.coord);
    const keepWarn = r.has_custom_existing_name && !scanState.overwriteExisting;
    return `
      <div class="lscan-row tier-${escapeHtml(r.confidence_tier)}">
        <label class="lscan-row-check">
          <input type="checkbox" ${checked ? 'checked' : ''} data-change-action="lscan-toggle-row" data-args='${escapeHtml(JSON.stringify([r.coord]))}'>
        </label>
        <div class="lscan-row-thumb">
          ${cropSafe
            ? `<button type="button" class="lscan-thumb-btn" data-action="lscan-zoom-crop" data-args='${escapeHtml(JSON.stringify([cropSafe, r.coord]))}' title="Zoom sticker crop">
                 <img src="${escapeHtml(cropSafe)}" alt="${escapeHtml(r.coord)} sticker">
               </button>`
            : `<div class="lscan-thumb-empty">${escapeHtml(r.coord)}</div>`}
        </div>
        <div class="lscan-row-main">
          <div class="lscan-row-meta">
            <span class="lscan-coord">${escapeHtml(r.coord)}</span>
            ${renderTierBadge(r)}
            ${r.existing_name ? `<span class="lscan-existing">Current: ${escapeHtml(r.existing_name)}${keepWarn ? ' (kept)' : ''}</span>` : ''}
            ${r.reason ? `<span class="lscan-reason">${escapeHtml(r.reason)}</span>` : ''}
          </div>
          <div class="lscan-row-inputs">
            <input
              type="text"
              class="lscan-name-input"
              maxlength="120"
              value="${escapeHtml(draft.name || '')}"
              placeholder="${r.confidence_tier === 'red' ? 'Type manually or snap close-up…' : `Name for ${escapeHtml(r.coord)}…`}"
              data-input-action="lscan-edit-name"
              data-args='${escapeHtml(JSON.stringify([r.coord]))}'
            >
            <input
              type="text"
              class="lscan-desc-input"
              maxlength="500"
              value="${escapeHtml(draft.description || '')}"
              placeholder="Optional specs / notes…"
              data-input-action="lscan-edit-desc"
              data-args='${escapeHtml(JSON.stringify([r.coord]))}'
            >
          </div>
        </div>
      </div>
    `;
  }).join('') : `<div class="lscan-empty-filter">No compartments in this filter.</div>`;

  const selectedReadyCount = Array.from(scanState.selectedCoords).filter((coord) => {
    const d = scanState.drafts.get(coord);
    return Boolean((d?.name || '').trim());
  }).length;

  bodyEl.innerHTML = `
    <div class="lscan-review-stage">
      <div class="lscan-filter-bar">
        <button type="button" class="lscan-pill ${scanState.tierFilter === 'all' ? 'active' : ''}" data-action="lscan-filter" data-args='["all"]'>Labeled (${s.green + s.yellow + s.red})</button>
        <button type="button" class="lscan-pill is-green ${scanState.tierFilter === 'green' ? 'active' : ''}" data-action="lscan-filter" data-args='["green"]'>Green (${s.green})</button>
        <button type="button" class="lscan-pill is-yellow ${scanState.tierFilter === 'yellow' ? 'active' : ''}" data-action="lscan-filter" data-args='["yellow"]'>Check (${s.yellow})</button>
        <button type="button" class="lscan-pill is-red ${scanState.tierFilter === 'red' ? 'active' : ''}" data-action="lscan-filter" data-args='["red"]'>Close-up (${s.red})</button>
        <button type="button" class="lscan-pill ${scanState.tierFilter === 'unlabeled' ? 'active' : ''}" data-action="lscan-filter" data-args='["unlabeled"]'>Empty (${s.unlabeled})</button>
      </div>

      ${zonesHtml}

      <div class="lscan-rows-list">${rowsHtml}</div>

      <div class="lscan-options-bar">
        <label class="lscan-option-check">
          <input type="checkbox" ${scanState.overwriteExisting ? 'checked' : ''} data-change-action="lscan-toggle-overwrite">
          <span>Overwrite existing custom drawer names</span>
        </label>
        <label class="lscan-option-check">
          <input type="checkbox" ${scanState.createStarterItems ? 'checked' : ''} data-change-action="lscan-toggle-starter">
          <span>Also add a starter item inside each labeled compartment</span>
        </label>
      </div>
    </div>
  `;

  footerEl.innerHTML = `
    <button type="button" class="btn btn-secondary" data-action="lscan-realign">Re-align Photo</button>
    <button type="button" id="lscan-apply-btn" class="btn btn-primary" data-action="lscan-apply" ${selectedReadyCount === 0 ? 'disabled' : ''}>
      Apply ${selectedReadyCount} Label${selectedReadyCount === 1 ? '' : 's'}
    </button>
  `;
}

registerActions({
  'lscan-close': () => closeLabelScanModal(),
  'lscan-set-rows': (evt, el) => {
    const val = Math.max(1, Math.min(26, Number(el?.value || evt?.target?.value) || 1));
    scanState.gridRows = val;
    if (scanState.stage === 'align') drawAlignCanvas();
  },
  'lscan-set-cols': (evt, el) => {
    const val = Math.max(1, Math.min(26, Number(el?.value || evt?.target?.value) || 1));
    scanState.gridCols = val;
    if (scanState.stage === 'align') drawAlignCanvas();
  },
  'lscan-upload-overview': (evt, el) => {
    const file = el?.files?.[0] || evt?.target?.files?.[0];
    if (el) el.value = '';
    if (file) void handleDesktopPhotoUpload(file, null);
  },
  'lscan-upload-closeup': (zoneId, evt, el) => {
    const file = el?.files?.[0] || evt?.target?.files?.[0];
    if (el) el.value = '';
    const zone = scanState.closeupZones.find((z) => z.zone_id === zoneId) || null;
    scanState.activeZone = zone;
    if (file) void handleDesktopPhotoUpload(file, zoneId);
  },
  'lscan-align-step': (step) => {
    scanState.alignStep = step === 'grid' ? 'grid' : 'corners';
    scanState.dragIdx = -1;
    scanState.dragRowIdx = -1;
    scanState.dragColIdx = -1;
    scanState.loupePoint = null;
    renderModal();
  },
  'lscan-zoom': (z) => {
    scanState.alignZoom = Math.max(1, Math.min(3, Number(z) || 1));
    renderModal();
  },
  'lscan-reset-quad': () => {
    if (scanState.alignStep === 'grid') {
      const { subRows, subCols } = getActiveSubGrid();
      resetGridBoundaries(subRows, subCols);
    } else {
      scanState.quad = DEFAULT_QUAD();
    }
    drawAlignCanvas();
  },
  'lscan-back-prev': () => {
    scanState.stage = scanState.results.length > 0 ? 'review' : 'qr';
    scanState.activeZone = null;
    renderModal();
  },
  'lscan-realign': () => {
    scanState.activeZone = null;
    if (scanState.activePhotoPath) {
      void enterAlignStage(scanState.activePhotoPath);
    } else {
      scanState.stage = 'qr';
      renderModal();
    }
  },
  'lscan-analyze': () => void runAnalyzeFromAlign(),
  'lscan-send-zone': (zoneId) => void sendZoneToPhone(zoneId),
  'lscan-filter': (tier) => {
    scanState.tierFilter = tier || 'all';
    renderModal();
  },
  'lscan-toggle-row': (coord, evt, el) => {
    const checked = Boolean((el || evt?.target)?.checked);
    if (checked) scanState.selectedCoords.add(coord);
    else scanState.selectedCoords.delete(coord);
    renderModal();
  },
  'lscan-edit-name': (coord, evt, el) => {
    const input = el || evt?.target;
    const val = input?.value ?? '';
    const curr = scanState.drafts.get(coord) || { name: '', description: '', dirty: false };
    curr.name = val;
    curr.dirty = true;
    scanState.drafts.set(coord, curr);
    if (val.trim()) {
      scanState.selectedCoords.add(coord);
      const rowEl = input?.closest?.('.lscan-row');
      const chk = rowEl?.querySelector?.('input[type="checkbox"]');
      if (chk) chk.checked = true;
    }
    const applyBtn = document.getElementById('lscan-apply-btn');
    if (applyBtn) {
      const count = Array.from(scanState.selectedCoords).filter((c) => Boolean((scanState.drafts.get(c)?.name || '').trim())).length;
      applyBtn.disabled = count === 0;
      applyBtn.textContent = `Apply ${count} Label${count === 1 ? '' : 's'}`;
    }
  },
  'lscan-edit-desc': (coord, evt, el) => {
    const val = (el || evt?.target)?.value ?? '';
    const curr = scanState.drafts.get(coord) || { name: '', description: '', dirty: false };
    curr.description = val;
    curr.dirty = true;
    scanState.drafts.set(coord, curr);
  },
  'lscan-zoom-crop': (cropUrl, coord) => {
    openLightbox(cropUrl, `Compartment ${coord} label crop`);
  },
  'lscan-toggle-overwrite': (evt, el) => {
    scanState.overwriteExisting = Boolean((el || evt?.target)?.checked);
    syncDraftsFromResults(scanState.results);
    renderModal();
  },
  'lscan-toggle-starter': (evt, el) => {
    scanState.createStarterItems = Boolean((el || evt?.target)?.checked);
  },
  'lscan-apply': () => void applyReviewedLabels(),
});
