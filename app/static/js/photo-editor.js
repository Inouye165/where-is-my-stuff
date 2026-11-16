/**
 * Client-Side Crop, Fine Rotate, Scale & 4-Corner Perspective Straighten Editor
 */
import { apiUpload } from './api.js';
import { sanitizePhotoUrl, showToast, withSubmitLock } from './dom.js';

let editorState = {
  img: null,
  originalImg: null,
  objectUrl: null,
  mode: 'crop', // 'crop' | 'quad'
  rotation: 0, // 0, 90, 180, 270
  fineRotation: 0, // -15..15 degrees (0.1 deg precision)
  scaleX: 1, // 0.7..1.3
  scaleY: 1, // 0.7..1.3
  crop: { x: 0, y: 0, w: 1, h: 1 }, // normalized 0..1 in canvas space
  quad: [
    { x: 0.1, y: 0.1 }, // TL
    { x: 0.9, y: 0.1 }, // TR
    { x: 0.9, y: 0.9 }, // BR
    { x: 0.1, y: 0.9 }, // BL
  ],
  quadPickIndex: -1, // 0..3 when actively clicking 4 corners
  quadApplied: false,
  dragMode: null, // 'move' | 'nw' | 'ne' | 'sw' | 'se' | 'new' | 'quad-0'..'quad-3'
  dragStart: null,
  cropStart: null,
  sourcePhoto: null, // optional { original_path, thumbnail_path, filename }
  onApply: null,
};

const HANDLE_RADIUS = 10;
const MIN_CROP_FRAC = 0.08;
const QUAD_LABELS = ['TL', 'TR', 'BR', 'BL'];

function getRotatedDimensions(img, rotation) {
  if (rotation === 90 || rotation === 270) {
    return { w: img.naturalHeight || img.height, h: img.naturalWidth || img.width };
  }
  return { w: img.naturalWidth || img.width, h: img.naturalHeight || img.height };
}

function clamp(val, min, max) {
  return Math.max(min, Math.min(max, val));
}

function drawTransformedImage(ctx, img, rotation, fineRotation, scaleX, scaleY, targetW, targetH) {
  ctx.save();
  ctx.translate(targetW / 2, targetH / 2);
  ctx.scale(scaleX, scaleY);
  ctx.rotate(((rotation + fineRotation) * Math.PI) / 180);
  if (rotation === 90 || rotation === 270) {
    ctx.drawImage(img, -targetH / 2, -targetW / 2, targetH, targetW);
  } else {
    ctx.drawImage(img, -targetW / 2, -targetH / 2, targetW, targetH);
  }
  ctx.restore();
}

function syncEditorControlsUI() {
  const isQuad = editorState.mode === 'quad';
  const cropBtn = document.getElementById('photo-editor-mode-crop');
  const quadBtn = document.getElementById('photo-editor-mode-quad');
  const quadPanel = document.getElementById('photo-editor-quad-controls');
  const finePanel = document.getElementById('photo-editor-fine-controls');
  const hintEl = document.getElementById('photo-editor-hint');

  if (cropBtn) cropBtn.classList.toggle('btn-primary', !isQuad);
  if (cropBtn) cropBtn.classList.toggle('btn-secondary', isQuad);
  if (quadBtn) quadBtn.classList.toggle('btn-primary', isQuad);
  if (quadBtn) quadBtn.classList.toggle('btn-secondary', !isQuad);
  if (quadPanel) quadPanel.style.display = isQuad ? 'block' : 'none';
  if (finePanel) finePanel.style.display = isQuad ? 'none' : 'block';

  const rotInput = document.getElementById('photo-editor-fine-rot');
  const rotVal = document.getElementById('photo-editor-fine-rot-val');
  if (rotInput) rotInput.value = String(editorState.fineRotation);
  if (rotVal) rotVal.textContent = `${editorState.fineRotation >= 0 ? '+' : ''}${editorState.fineRotation.toFixed(1)}°`;

  const sxInput = document.getElementById('photo-editor-scale-x');
  const sxVal = document.getElementById('photo-editor-scale-x-val');
  if (sxInput) sxInput.value = String(Math.round(editorState.scaleX * 100));
  if (sxVal) sxVal.textContent = `${Math.round(editorState.scaleX * 100)}%`;

  const syInput = document.getElementById('photo-editor-scale-y');
  const syVal = document.getElementById('photo-editor-scale-y-val');
  if (syInput) syInput.value = String(Math.round(editorState.scaleY * 100));
  if (syVal) syVal.textContent = `${Math.round(editorState.scaleY * 100)}%`;

  const quadStatus = document.getElementById('photo-editor-quad-status');
  if (quadStatus) {
    if (editorState.quadPickIndex >= 0 && editorState.quadPickIndex < 4) {
      const names = ['Top-Left', 'Top-Right', 'Bottom-Right', 'Bottom-Left'];
      quadStatus.textContent = `Click corner ${editorState.quadPickIndex + 1}/4: ${names[editorState.quadPickIndex]}`;
    } else {
      quadStatus.textContent = 'Drag the 4 corners to match the cabinet edges, or click "Click 4 Corners".';
    }
  }

  if (hintEl) {
    hintEl.textContent = isQuad
      ? 'Align TL, TR, BR, BL with the 4 corners of the cabinet, then click "Flatten & Preview" or "Use Photo".'
      : 'Drag corners to crop, use Tilt/Width/Height to align, or switch to 4-Corner Straighten.';
  }
}

function renderEditorCanvas() {
  const canvas = document.getElementById('photo-editor-canvas');
  if (!canvas || !editorState.img) return;

  const { img, rotation, fineRotation, scaleX, scaleY, crop, mode, quad } = editorState;
  const rot = getRotatedDimensions(img, rotation);
  const maxW = Math.min(560, window.innerWidth - 64);
  const maxH = Math.min(380, Math.round(window.innerHeight * 0.46));
  const scale = Math.min(maxW / rot.w, maxH / rot.h, 1);

  const cw = Math.max(120, Math.round(rot.w * scale));
  const ch = Math.max(120, Math.round(rot.h * scale));
  if (canvas.width !== cw || canvas.height !== ch) {
    canvas.width = cw;
    canvas.height = ch;
  }

  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, cw, ch);
  ctx.fillStyle = '#0b1120';
  ctx.fillRect(0, 0, cw, ch);

  if (mode === 'quad') {
    drawTransformedImage(ctx, img, rotation, 0, 1, 1, cw, ch);
    const pts = quad.map(p => ({ x: p.x * cw, y: p.y * ch }));

    // Subtle dark scrim outside the quad
    ctx.save();
    ctx.fillStyle = 'rgba(0, 0, 0, 0.5)';
    ctx.beginPath();
    ctx.rect(0, 0, cw, ch);
    ctx.moveTo(pts[0].x, pts[0].y);
    ctx.lineTo(pts[3].x, pts[3].y);
    ctx.lineTo(pts[2].x, pts[2].y);
    ctx.lineTo(pts[1].x, pts[1].y);
    ctx.closePath();
    ctx.fill('evenodd');

    // Quad outline
    ctx.strokeStyle = '#f59e0b';
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(pts[0].x, pts[0].y);
    ctx.lineTo(pts[1].x, pts[1].y);
    ctx.lineTo(pts[2].x, pts[2].y);
    ctx.lineTo(pts[3].x, pts[3].y);
    ctx.closePath();
    ctx.stroke();

    // Internal 3x3 alignment lines
    ctx.strokeStyle = 'rgba(245, 158, 11, 0.4)';
    ctx.lineWidth = 1;
    for (const t of [1 / 3, 2 / 3]) {
      const lx1 = pts[0].x + (pts[3].x - pts[0].x) * t;
      const ly1 = pts[0].y + (pts[3].y - pts[0].y) * t;
      const lx2 = pts[1].x + (pts[2].x - pts[1].x) * t;
      const ly2 = pts[1].y + (pts[2].y - pts[1].y) * t;
      ctx.beginPath();
      ctx.moveTo(lx1, ly1);
      ctx.lineTo(lx2, ly2);
      ctx.stroke();

      const tx1 = pts[0].x + (pts[1].x - pts[0].x) * t;
      const ty1 = pts[0].y + (pts[1].y - pts[0].y) * t;
      const tx2 = pts[3].x + (pts[2].x - pts[3].x) * t;
      const ty2 = pts[3].y + (pts[2].y - pts[3].y) * t;
      ctx.beginPath();
      ctx.moveTo(tx1, ty1);
      ctx.lineTo(tx2, ty2);
      ctx.stroke();
    }

    // Corner handles
    pts.forEach((p, idx) => {
      const isNextPick = editorState.quadPickIndex === idx;
      ctx.fillStyle = isNextPick ? '#ef4444' : '#f59e0b';
      ctx.strokeStyle = '#ffffff';
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.arc(p.x, p.y, 7, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();

      ctx.fillStyle = '#ffffff';
      ctx.font = 'bold 10px sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(QUAD_LABELS[idx], p.x, p.y - 14);
    });
    ctx.restore();
    return;
  }

  drawTransformedImage(ctx, img, rotation, fineRotation, scaleX, scaleY, cw, ch);

  const rx = crop.x * cw;
  const ry = crop.y * ch;
  const rw = crop.w * cw;
  const rh = crop.h * ch;

  // Dim area outside crop box
  ctx.save();
  ctx.fillStyle = 'rgba(0, 0, 0, 0.55)';
  ctx.fillRect(0, 0, cw, ry);
  ctx.fillRect(0, ry + rh, cw, ch - (ry + rh));
  ctx.fillRect(0, ry, rx, rh);
  ctx.fillRect(rx + rw, ry, cw - (rx + rw), rh);
  ctx.restore();

  // Crop border & rule-of-thirds guides
  ctx.save();
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 2;
  ctx.strokeRect(rx, ry, rw, rh);

  ctx.strokeStyle = 'rgba(255, 255, 255, 0.28)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(rx + rw / 3, ry);
  ctx.lineTo(rx + rw / 3, ry + rh);
  ctx.moveTo(rx + (2 * rw) / 3, ry);
  ctx.lineTo(rx + (2 * rw) / 3, ry + rh);
  ctx.moveTo(rx, ry + rh / 3);
  ctx.lineTo(rx + rw, ry + rh / 3);
  ctx.moveTo(rx, ry + (2 * rh) / 3);
  ctx.lineTo(rx + rw, ry + (2 * rh) / 3);
  ctx.stroke();

  // Corner handles
  const corners = [
    [rx, ry],
    [rx + rw, ry],
    [rx, ry + rh],
    [rx + rw, ry + rh],
  ];
  ctx.fillStyle = '#3b82f6';
  ctx.strokeStyle = '#ffffff';
  ctx.lineWidth = 2;
  for (const [hx, hy] of corners) {
    ctx.beginPath();
    ctx.arc(hx, hy, 6, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
  }
  ctx.restore();
}

function getCanvasNormalizedPoint(e, canvas) {
  const rect = canvas.getBoundingClientRect();
  const x = clamp((e.clientX - rect.left) / rect.width, 0, 1);
  const y = clamp((e.clientY - rect.top) / rect.height, 0, 1);
  return { x, y, px: e.clientX - rect.left, py: e.clientY - rect.top, rect };
}

function hitTestCrop(pt, crop) {
  const rx = crop.x * pt.rect.width;
  const ry = crop.y * pt.rect.height;
  const rw = crop.w * pt.rect.width;
  const rh = crop.h * pt.rect.height;

  const corners = [
    ['nw', rx, ry],
    ['ne', rx + rw, ry],
    ['sw', rx, ry + rh],
    ['se', rx + rw, ry + rh],
  ];
  for (const [name, hx, hy] of corners) {
    if (Math.hypot(pt.px - hx, pt.py - hy) <= HANDLE_RADIUS + 6) {
      return name;
    }
  }
  if (pt.px >= rx && pt.px <= rx + rw && pt.py >= ry && pt.py <= ry + rh) {
    return 'move';
  }
  return 'new';
}

function hitTestQuad(pt, quad) {
  for (let i = 0; i < 4; i++) {
    const hx = quad[i].x * pt.rect.width;
    const hy = quad[i].y * pt.rect.height;
    if (Math.hypot(pt.px - hx, pt.py - hy) <= HANDLE_RADIUS + 8) {
      return `quad-${i}`;
    }
  }
  return null;
}

/**
 * Solve 8x8 linear system A * x = b via Gaussian elimination with partial pivoting.
 */
function solveLinear8(A, b) {
  const n = 8;
  const M = A.map((row, i) => [...row, b[i]]);
  for (let col = 0; col < n; col++) {
    let pivotRow = col;
    for (let r = col + 1; r < n; r++) {
      if (Math.abs(M[r][col]) > Math.abs(M[pivotRow][col])) pivotRow = r;
    }
    const tmp = M[col];
    M[col] = M[pivotRow];
    M[pivotRow] = tmp;

    const pivot = M[col][col];
    if (Math.abs(pivot) < 1e-12) return null;
    for (let j = col; j <= n; j++) M[col][j] /= pivot;
    for (let r = 0; r < n; r++) {
      if (r === col) continue;
      const factor = M[r][col];
      for (let j = col; j <= n; j++) M[r][j] -= factor * M[col][j];
    }
  }
  return M.map(row => row[n]);
}

/**
 * Compute homography mapping destination rect (0,0)-(W,H) to source quad [TL, TR, BR, BL].
 */
function computeInverseHomography(outW, outH, srcPts) {
  const dstPts = [
    { x: 0, y: 0 },
    { x: outW, y: 0 },
    { x: outW, y: outH },
    { x: 0, y: outH },
  ];
  const A = [];
  const b = [];
  for (let i = 0; i < 4; i++) {
    const u = dstPts[i].x;
    const v = dstPts[i].y;
    const x = srcPts[i].x;
    const y = srcPts[i].y;
    A.push([u, v, 1, 0, 0, 0, -u * x, -v * x]);
    b.push(x);
    A.push([0, 0, 0, u, v, 1, -u * y, -v * y]);
    b.push(y);
  }
  return solveLinear8(A, b);
}

function warpQuadToCanvas(img, rotation, quad) {
  const rot = getRotatedDimensions(img, rotation);
  const srcCanvas = document.createElement('canvas');
  srcCanvas.width = rot.w;
  srcCanvas.height = rot.h;
  const srcCtx = srcCanvas.getContext('2d');
  drawTransformedImage(srcCtx, img, rotation, 0, 1, 1, rot.w, rot.h);

  const srcPts = quad.map(p => ({ x: p.x * rot.w, y: p.y * rot.h }));
  const topW = Math.hypot(srcPts[1].x - srcPts[0].x, srcPts[1].y - srcPts[0].y);
  const botW = Math.hypot(srcPts[2].x - srcPts[3].x, srcPts[2].y - srcPts[3].y);
  const leftH = Math.hypot(srcPts[3].x - srcPts[0].x, srcPts[3].y - srcPts[0].y);
  const rightH = Math.hypot(srcPts[2].x - srcPts[1].x, srcPts[2].y - srcPts[1].y);

  let outW = Math.max(16, Math.round((topW + botW) / 2));
  let outH = Math.max(16, Math.round((leftH + rightH) / 2));
  const maxDim = 1600;
  const scale = Math.min(1, maxDim / Math.max(outW, outH));
  outW = Math.max(16, Math.round(outW * scale));
  outH = Math.max(16, Math.round(outH * scale));

  const h = computeInverseHomography(outW, outH, srcPts);
  if (!h) throw new Error('Corners cannot form a valid rectangle.');

  const srcData = srcCtx.getImageData(0, 0, rot.w, rot.h).data;
  const outCanvas = document.createElement('canvas');
  outCanvas.width = outW;
  outCanvas.height = outH;
  const outCtx = outCanvas.getContext('2d');
  const outImgData = outCtx.createImageData(outW, outH);
  const dstData = outImgData.data;

  const [h0, h1, h2, h3, h4, h5, h6, h7] = h;
  const sw = rot.w;
  const sh = rot.h;

  for (let v = 0; v < outH; v++) {
    for (let u = 0; u < outW; u++) {
      const denom = h6 * u + h7 * v + 1;
      const sx = (h0 * u + h1 * v + h2) / denom;
      const sy = (h3 * u + h4 * v + h5) / denom;
      const dstIdx = (v * outW + u) * 4;

      if (sx < 0 || sy < 0 || sx >= sw - 1 || sy >= sh - 1) {
        const cx = clamp(Math.round(sx), 0, sw - 1);
        const cy = clamp(Math.round(sy), 0, sh - 1);
        const sIdx = (cy * sw + cx) * 4;
        dstData[dstIdx] = srcData[sIdx];
        dstData[dstIdx + 1] = srcData[sIdx + 1];
        dstData[dstIdx + 2] = srcData[sIdx + 2];
        dstData[dstIdx + 3] = 255;
        continue;
      }

      const x0 = Math.floor(sx);
      const y0 = Math.floor(sy);
      const dx = sx - x0;
      const dy = sy - y0;
      const idx00 = (y0 * sw + x0) * 4;
      const idx10 = idx00 + 4;
      const idx01 = idx00 + sw * 4;
      const idx11 = idx01 + 4;

      const w00 = (1 - dx) * (1 - dy);
      const w10 = dx * (1 - dy);
      const w01 = (1 - dx) * dy;
      const w11 = dx * dy;

      dstData[dstIdx] = srcData[idx00] * w00 + srcData[idx10] * w10 + srcData[idx01] * w01 + srcData[idx11] * w11;
      dstData[dstIdx + 1] = srcData[idx00 + 1] * w00 + srcData[idx10 + 1] * w10 + srcData[idx01 + 1] * w01 + srcData[idx11 + 1] * w11;
      dstData[dstIdx + 2] = srcData[idx00 + 2] * w00 + srcData[idx10 + 2] * w10 + srcData[idx01 + 2] * w01 + srcData[idx11 + 2] * w11;
      dstData[dstIdx + 3] = 255;
    }
  }

  outCtx.putImageData(outImgData, 0, 0);
  return outCanvas;
}

let pointerBound = false;
function bindCanvasPointers() {
  if (pointerBound) return;
  const canvas = document.getElementById('photo-editor-canvas');
  if (!canvas) return;
  pointerBound = true;

  canvas.addEventListener('pointerdown', (e) => {
    if (!editorState.img) return;
    e.preventDefault();
    const pt = getCanvasNormalizedPoint(e, canvas);

    if (editorState.mode === 'quad') {
      if (editorState.quadPickIndex >= 0 && editorState.quadPickIndex < 4) {
        editorState.quad[editorState.quadPickIndex] = { x: pt.x, y: pt.y };
        editorState.quadPickIndex += 1;
        if (editorState.quadPickIndex >= 4) {
          editorState.quadPickIndex = -1;
        }
        syncEditorControlsUI();
        renderEditorCanvas();
        return;
      }
      const hit = hitTestQuad(pt, editorState.quad);
      if (hit) {
        canvas.setPointerCapture(e.pointerId);
        editorState.dragMode = hit;
      }
      return;
    }

    canvas.setPointerCapture(e.pointerId);
    editorState.dragMode = hitTestCrop(pt, editorState.crop);
    editorState.dragStart = { x: pt.x, y: pt.y };
    editorState.cropStart = { ...editorState.crop };
  });

  canvas.addEventListener('pointermove', (e) => {
    if (!editorState.dragMode || !editorState.img) return;
    const pt = getCanvasNormalizedPoint(e, canvas);

    if (editorState.dragMode.startsWith('quad-')) {
      const idx = Number(editorState.dragMode.split('-')[1]);
      editorState.quad[idx] = { x: pt.x, y: pt.y };
      renderEditorCanvas();
      return;
    }

    const dx = pt.x - editorState.dragStart.x;
    const dy = pt.y - editorState.dragStart.y;
    const s = editorState.cropStart;
    let { x, y, w, h } = s;

    if (editorState.dragMode === 'move') {
      x = clamp(s.x + dx, 0, 1 - s.w);
      y = clamp(s.y + dy, 0, 1 - s.h);
    } else if (editorState.dragMode === 'new') {
      const x1 = Math.min(editorState.dragStart.x, pt.x);
      const y1 = Math.min(editorState.dragStart.y, pt.y);
      const x2 = Math.max(editorState.dragStart.x, pt.x);
      const y2 = Math.max(editorState.dragStart.y, pt.y);
      x = x1;
      y = y1;
      w = Math.max(MIN_CROP_FRAC, x2 - x1);
      h = Math.max(MIN_CROP_FRAC, y2 - y1);
      if (x + w > 1) x = 1 - w;
      if (y + h > 1) y = 1 - h;
    } else {
      let left = s.x;
      let top = s.y;
      let right = s.x + s.w;
      let bottom = s.y + s.h;

      if (editorState.dragMode.includes('w')) left = clamp(s.x + dx, 0, right - MIN_CROP_FRAC);
      if (editorState.dragMode.includes('e')) right = clamp(s.x + s.w + dx, left + MIN_CROP_FRAC, 1);
      if (editorState.dragMode.includes('n')) top = clamp(s.y + dy, 0, bottom - MIN_CROP_FRAC);
      if (editorState.dragMode.includes('s')) bottom = clamp(s.y + s.h + dy, top + MIN_CROP_FRAC, 1);

      x = left;
      y = top;
      w = right - left;
      h = bottom - top;
    }

    editorState.crop = { x, y, w, h };
    renderEditorCanvas();
  });

  const endDrag = () => {
    if (editorState.dragMode === 'new' && (editorState.crop.w < 0.03 || editorState.crop.h < 0.03)) {
      editorState.crop = { x: 0, y: 0, w: 1, h: 1 };
      renderEditorCanvas();
    }
    editorState.dragMode = null;
  };

  canvas.addEventListener('pointerup', endDrag);
  canvas.addEventListener('pointercancel', endDrag);
}

export function setPhotoEditorMode(mode) {
  editorState.mode = mode === 'quad' ? 'quad' : 'crop';
  editorState.quadPickIndex = -1;
  syncEditorControlsUI();
  renderEditorCanvas();
}

export function startPhotoEditorCornerPick() {
  editorState.mode = 'quad';
  editorState.quadPickIndex = 0;
  syncEditorControlsUI();
  renderEditorCanvas();
}

export async function flattenPhotoEditorQuad() {
  if (!editorState.img) return;
  try {
    const warpedCanvas = warpQuadToCanvas(editorState.img, editorState.rotation, editorState.quad);
    const dataUrl = warpedCanvas.toDataURL('image/jpeg', 0.94);
    const warpedImg = new Image();
    await new Promise((resolve, reject) => {
      warpedImg.onload = resolve;
      warpedImg.onerror = reject;
      warpedImg.src = dataUrl;
    });
    editorState.img = warpedImg;
    editorState.rotation = 0;
    editorState.fineRotation = 0;
    editorState.scaleX = 1;
    editorState.scaleY = 1;
    editorState.crop = { x: 0, y: 0, w: 1, h: 1 };
    editorState.quadApplied = true;
    editorState.mode = 'crop';
    editorState.quadPickIndex = -1;
    syncEditorControlsUI();
    renderEditorCanvas();
  } catch (err) {
    showToast(err.message || 'Could not straighten corners.', 'error');
  }
}

export function setPhotoEditorFineRotation(deg) {
  editorState.fineRotation = clamp(Math.round(Number(deg) * 10) / 10, -15, 15);
  syncEditorControlsUI();
  renderEditorCanvas();
}

export function nudgePhotoEditorFineRotation(deltaDeg) {
  setPhotoEditorFineRotation(editorState.fineRotation + Number(deltaDeg));
}

export function setPhotoEditorScale(axis, pct) {
  const factor = clamp(Number(pct) / 100, 0.7, 1.3);
  if (axis === 'x') editorState.scaleX = factor;
  if (axis === 'y') editorState.scaleY = factor;
  syncEditorControlsUI();
  renderEditorCanvas();
}

export function rotatePhotoEditor(deltaDeg) {
  editorState.rotation = (editorState.rotation + deltaDeg + 360) % 360;
  editorState.crop = { x: 0, y: 0, w: 1, h: 1 };
  renderEditorCanvas();
}

export function resetPhotoEditorCrop() {
  if (editorState.originalImg) {
    editorState.img = editorState.originalImg;
  }
  editorState.rotation = 0;
  editorState.fineRotation = 0;
  editorState.scaleX = 1;
  editorState.scaleY = 1;
  editorState.crop = { x: 0, y: 0, w: 1, h: 1 };
  editorState.quad = [
    { x: 0.1, y: 0.1 },
    { x: 0.9, y: 0.1 },
    { x: 0.9, y: 0.9 },
    { x: 0.1, y: 0.9 },
  ];
  editorState.quadApplied = false;
  editorState.quadPickIndex = -1;
  syncEditorControlsUI();
  renderEditorCanvas();
}

export function closePhotoEditorModal() {
  if (editorState.objectUrl) {
    URL.revokeObjectURL(editorState.objectUrl);
    editorState.objectUrl = null;
  }
  editorState.img = null;
  editorState.originalImg = null;
  editorState.dragMode = null;
  document.getElementById('photo-editor-modal')?.classList.remove('open');
}

export function closePhotoEditorOnScrim(e) {
  if (e && e.target && e.target.id === 'photo-editor-modal') {
    closePhotoEditorModal();
  }
}

function isUnmodified() {
  const c = editorState.crop;
  return (
    !editorState.quadApplied &&
    editorState.mode === 'crop' &&
    editorState.rotation === 0 &&
    Math.abs(editorState.fineRotation) < 0.05 &&
    Math.abs(editorState.scaleX - 1) < 0.005 &&
    Math.abs(editorState.scaleY - 1) < 0.005 &&
    Math.abs(c.x) < 0.005 &&
    Math.abs(c.y) < 0.005 &&
    Math.abs(c.w - 1) < 0.005 &&
    Math.abs(c.h - 1) < 0.005
  );
}

export async function applyPhotoEditor() {
  if (!editorState.img) return;
  const onApply = editorState.onApply;
  const sourcePhoto = editorState.sourcePhoto;

  if (isUnmodified() && sourcePhoto && sourcePhoto.original_path) {
    closePhotoEditorModal();
    if (typeof onApply === 'function') {
      try {
        await onApply(sourcePhoto);
      } catch (err) {
        showToast(err.message || 'Could not save photo.', 'error');
      }
    }
    return;
  }

  return withSubmitLock('photo-editor-apply-btn', 'Applying...', async () => {
    try {
      let outCanvas;
      if (editorState.mode === 'quad') {
        outCanvas = warpQuadToCanvas(editorState.img, editorState.rotation, editorState.quad);
      } else {
        const { img, rotation, fineRotation, scaleX, scaleY, crop } = editorState;
        const rot = getRotatedDimensions(img, rotation);

        const fullCanvas = document.createElement('canvas');
        fullCanvas.width = rot.w;
        fullCanvas.height = rot.h;
        const fullCtx = fullCanvas.getContext('2d');
        fullCtx.fillStyle = '#0b1120';
        fullCtx.fillRect(0, 0, rot.w, rot.h);
        drawTransformedImage(fullCtx, img, rotation, fineRotation, scaleX, scaleY, rot.w, rot.h);

        const sx = Math.round(crop.x * rot.w);
        const sy = Math.round(crop.y * rot.h);
        const sw = Math.max(1, Math.round(crop.w * rot.w));
        const sh = Math.max(1, Math.round(crop.h * rot.h));

        const maxDim = 1600;
        const scale = Math.min(1, maxDim / Math.max(sw, sh));
        const outW = Math.max(1, Math.round(sw * scale));
        const outH = Math.max(1, Math.round(sh * scale));

        outCanvas = document.createElement('canvas');
        outCanvas.width = outW;
        outCanvas.height = outH;
        const outCtx = outCanvas.getContext('2d');
        outCtx.drawImage(fullCanvas, sx, sy, sw, sh, 0, 0, outW, outH);
      }

      const blob = await new Promise((resolve) => outCanvas.toBlob(resolve, 'image/jpeg', 0.9));
      if (!blob) throw new Error('Could not export edited photo.');

      const file = new File([blob], 'edited-photo.jpg', { type: 'image/jpeg' });
      const fd = new FormData();
      fd.append('file', file);
      const uploaded = await apiUpload('/api/v1/photos/upload', fd, 'Could not upload edited photo.');

      closePhotoEditorModal();
      if (typeof onApply === 'function') {
        await onApply(uploaded, file);
      }
    } catch (err) {
      showToast(err.message || 'Could not apply photo edits.', 'error');
    }
  });
}

export async function openPhotoEditorModal({ imageUrl = null, file = null, sourcePhoto = null, title = 'Crop & Straighten', onApply }) {
  bindCanvasPointers();

  if (editorState.objectUrl) {
    URL.revokeObjectURL(editorState.objectUrl);
    editorState.objectUrl = null;
  }

  let src = '';
  if (file) {
    editorState.objectUrl = URL.createObjectURL(file);
    src = editorState.objectUrl;
  } else if (imageUrl) {
    src = sanitizePhotoUrl(imageUrl);
  }

  if (!src) {
    showToast('No photo available to edit.', 'error');
    return;
  }

  const titleEl = document.getElementById('photo-editor-title');
  if (titleEl) titleEl.textContent = title;

  editorState.mode = 'crop';
  editorState.rotation = 0;
  editorState.fineRotation = 0;
  editorState.scaleX = 1;
  editorState.scaleY = 1;
  editorState.crop = { x: 0, y: 0, w: 1, h: 1 };
  editorState.quad = [
    { x: 0.1, y: 0.1 },
    { x: 0.9, y: 0.1 },
    { x: 0.9, y: 0.9 },
    { x: 0.1, y: 0.9 },
  ];
  editorState.quadApplied = false;
  editorState.quadPickIndex = -1;
  editorState.sourcePhoto = sourcePhoto;
  editorState.onApply = onApply;

  const img = new Image();
  img.onload = () => {
    editorState.img = img;
    editorState.originalImg = img;
    document.getElementById('photo-editor-modal')?.classList.add('open');
    syncEditorControlsUI();
    renderEditorCanvas();
  };
  img.onerror = () => {
    showToast('Could not load photo for editing.', 'error');
  };
  img.src = src;
}
