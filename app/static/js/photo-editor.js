/**
 * Client-Side Crop & Rotate Photo Editor
 */
import { apiUpload } from './api.js';
import { sanitizePhotoUrl, showToast, withSubmitLock } from './dom.js';

let editorState = {
  img: null,
  objectUrl: null,
  rotation: 0, // 0, 90, 180, 270
  crop: { x: 0, y: 0, w: 1, h: 1 }, // normalized 0..1 in rotated space
  dragMode: null, // 'move' | 'nw' | 'ne' | 'sw' | 'se' | 'new'
  dragStart: null,
  cropStart: null,
  sourcePhoto: null, // optional { original_path, thumbnail_path, filename }
  onApply: null,
};

const HANDLE_RADIUS = 10;
const MIN_CROP_FRAC = 0.08;

function getRotatedDimensions(img, rotation) {
  if (rotation === 90 || rotation === 270) {
    return { w: img.naturalHeight, h: img.naturalWidth };
  }
  return { w: img.naturalWidth, h: img.naturalHeight };
}

function clamp(val, min, max) {
  return Math.max(min, Math.min(max, val));
}

function drawRotatedImage(ctx, img, rotation, targetW, targetH) {
  ctx.save();
  ctx.translate(targetW / 2, targetH / 2);
  ctx.rotate((rotation * Math.PI) / 180);
  if (rotation === 90 || rotation === 270) {
    ctx.drawImage(img, -targetH / 2, -targetW / 2, targetH, targetW);
  } else {
    ctx.drawImage(img, -targetW / 2, -targetH / 2, targetW, targetH);
  }
  ctx.restore();
}

function renderEditorCanvas() {
  const canvas = document.getElementById('photo-editor-canvas');
  if (!canvas || !editorState.img) return;

  const { img, rotation, crop } = editorState;
  const rot = getRotatedDimensions(img, rotation);
  const maxW = Math.min(520, window.innerWidth - 64);
  const maxH = Math.min(380, Math.round(window.innerHeight * 0.48));
  const scale = Math.min(maxW / rot.w, maxH / rot.h, 1);

  const cw = Math.max(120, Math.round(rot.w * scale));
  const ch = Math.max(120, Math.round(rot.h * scale));
  if (canvas.width !== cw || canvas.height !== ch) {
    canvas.width = cw;
    canvas.height = ch;
  }

  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, cw, ch);
  drawRotatedImage(ctx, img, rotation, cw, ch);

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

let pointerBound = false;
function bindCanvasPointers() {
  if (pointerBound) return;
  const canvas = document.getElementById('photo-editor-canvas');
  if (!canvas) return;
  pointerBound = true;

  canvas.addEventListener('pointerdown', (e) => {
    if (!editorState.img) return;
    e.preventDefault();
    canvas.setPointerCapture(e.pointerId);
    const pt = getCanvasNormalizedPoint(e, canvas);
    editorState.dragMode = hitTestCrop(pt, editorState.crop);
    editorState.dragStart = { x: pt.x, y: pt.y };
    editorState.cropStart = { ...editorState.crop };
  });

  canvas.addEventListener('pointermove', (e) => {
    if (!editorState.dragMode || !editorState.img) return;
    const pt = getCanvasNormalizedPoint(e, canvas);
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

export function rotatePhotoEditor(deltaDeg) {
  editorState.rotation = (editorState.rotation + deltaDeg + 360) % 360;
  editorState.crop = { x: 0, y: 0, w: 1, h: 1 };
  renderEditorCanvas();
}

export function resetPhotoEditorCrop() {
  editorState.crop = { x: 0, y: 0, w: 1, h: 1 };
  renderEditorCanvas();
}

export function closePhotoEditorModal() {
  if (editorState.objectUrl) {
    URL.revokeObjectURL(editorState.objectUrl);
    editorState.objectUrl = null;
  }
  editorState.img = null;
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
    editorState.rotation === 0 &&
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
      await onApply(sourcePhoto);
    }
    return;
  }

  return withSubmitLock('photo-editor-apply-btn', 'Applying...', async () => {
    try {
      const { img, rotation, crop } = editorState;
      const rot = getRotatedDimensions(img, rotation);

      const fullCanvas = document.createElement('canvas');
      fullCanvas.width = rot.w;
      fullCanvas.height = rot.h;
      const fullCtx = fullCanvas.getContext('2d');
      drawRotatedImage(fullCtx, img, rotation, rot.w, rot.h);

      const sx = Math.round(crop.x * rot.w);
      const sy = Math.round(crop.y * rot.h);
      const sw = Math.max(1, Math.round(crop.w * rot.w));
      const sh = Math.max(1, Math.round(crop.h * rot.h));

      const maxDim = 1600;
      const scale = Math.min(1, maxDim / Math.max(sw, sh));
      const outW = Math.max(1, Math.round(sw * scale));
      const outH = Math.max(1, Math.round(sh * scale));

      const outCanvas = document.createElement('canvas');
      outCanvas.width = outW;
      outCanvas.height = outH;
      const outCtx = outCanvas.getContext('2d');
      outCtx.drawImage(fullCanvas, sx, sy, sw, sh, 0, 0, outW, outH);

      const blob = await new Promise((resolve) => outCanvas.toBlob(resolve, 'image/jpeg', 0.88));
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

export async function openPhotoEditorModal({ imageUrl = null, file = null, sourcePhoto = null, title = 'Crop & Rotate', onApply }) {
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

  editorState.rotation = 0;
  editorState.crop = { x: 0, y: 0, w: 1, h: 1 };
  editorState.sourcePhoto = sourcePhoto;
  editorState.onApply = onApply;

  const img = new Image();
  img.onload = () => {
    editorState.img = img;
    document.getElementById('photo-editor-modal')?.classList.add('open');
    renderEditorCanvas();
  };
  img.onerror = () => {
    showToast('Could not load photo for editing.', 'error');
  };
  img.src = src;
}

