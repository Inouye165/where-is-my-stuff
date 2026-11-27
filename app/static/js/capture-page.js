import { compressImageFile } from './photo-compress.js';

const params = new URLSearchParams(window.location.search);
const token = params.get('token') || '';
const highRes = params.get('hq') === '1';
const titleEl = document.getElementById('capture-title');
const subtitleEl = document.getElementById('capture-subtitle');
const triggerEl = document.getElementById('capture-trigger');
const galleryTriggerEl = document.getElementById('gallery-trigger');
const btnLabelEl = document.getElementById('capture-btn-label');
const inputEl = document.getElementById('mobile-camera-input');
const galleryInputEl = document.getElementById('mobile-gallery-input');
const previewWrap = document.getElementById('preview-wrap');
const previewImg = document.getElementById('preview-img');
const thumbsStrip = document.getElementById('thumbs-strip');
const statusArea = document.getElementById('status-area');
let uploadedCount = 0;
const createdPreviewUrls = [];

window.addEventListener('pagehide', () => {
  for (const u of createdPreviewUrls) {
    try { URL.revokeObjectURL(u); } catch {}
  }
  createdPreviewUrls.length = 0;
});

function setStatus(msg, kind) {
  statusArea.innerHTML = '';
  if (!msg) return;
  const pill = document.createElement('div');
  pill.className = `status-pill ${kind}`;
  pill.textContent = msg;
  statusArea.appendChild(pill);
}

function showExpired(msg) {
  triggerEl.style.display = 'none';
  galleryTriggerEl.style.display = 'none';
  subtitleEl.textContent = msg || 'This QR code has expired. Open a new one on your computer.';
  setStatus('Link expired', 'error');
}

async function init() {
  if (!token) {
    showExpired('Scan the QR code on your computer screen to start.');
    return;
  }
  try {
    const res = await fetch(`/api/v1/photos/capture-sessions/${encodeURIComponent(token)}`);
    if (!res.ok) {
      showExpired('This link is invalid or has expired.');
      return;
    }
    const data = await res.json();
    if (data.status === 'expired') {
      showExpired();
      return;
    }
    if (data.label) {
      titleEl.textContent = data.label;
    }
  } catch {
    setStatus('Could not reach your computer on Wi-Fi.', 'error');
  }
}

async function handleFiles(fileList) {
  const files = Array.from(fileList || []).filter(Boolean);
  if (!files.length || !token) return;
  triggerEl.classList.add('disabled');
  galleryTriggerEl.style.pointerEvents = 'none';
  btnLabelEl.textContent = `Sending ${files.length > 1 ? files.length + ' photos' : 'photo'}...`;
  setStatus('', '');

  try {
    for (const file of files) {
      const compressed = highRes
        ? await compressImageFile(file, 3200, 0.92)
        : await compressImageFile(file);
      const fd = new FormData();
      fd.append('file', compressed);
      const uploadUrl = highRes
        ? `/api/v1/photos/capture-sessions/${encodeURIComponent(token)}/upload?max_dimension=3200`
        : `/api/v1/photos/capture-sessions/${encodeURIComponent(token)}/upload`;
      const res = await fetch(uploadUrl, {
        method: 'POST',
        body: fd,
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        if (res.status === 410 || res.status === 404) {
          showExpired(body.detail);
          return;
        }
        throw new Error(body.detail || 'Upload failed.');
      }

      uploadedCount += 1;
      const previewUrl = URL.createObjectURL(compressed);
      createdPreviewUrls.push(previewUrl);
      previewImg.src = previewUrl;
      previewWrap.style.display = 'block';
      const thumb = document.createElement('img');
      thumb.src = previewUrl;
      thumb.alt = `Photo ${uploadedCount}`;
      thumbsStrip.appendChild(thumb);
    }

    triggerEl.classList.remove('disabled');
    galleryTriggerEl.style.pointerEvents = 'auto';
    btnLabelEl.textContent = 'Take another photo';
    subtitleEl.textContent = 'Keep snapping or close this tab when finished.';
    setStatus(`✓ ${uploadedCount} ${uploadedCount === 1 ? 'photo' : 'photos'} sent to your computer!`, 'success');
  } catch (err) {
    triggerEl.classList.remove('disabled');
    galleryTriggerEl.style.pointerEvents = 'auto';
    btnLabelEl.textContent = 'Tap to open camera';
    setStatus(err.message || 'Upload failed. Try again.', 'error');
  }
}

inputEl.addEventListener('change', (e) => {
  handleFiles(e.target.files);
  inputEl.value = '';
});

galleryInputEl.addEventListener('change', (e) => {
  handleFiles(e.target.files);
  galleryInputEl.value = '';
});

init();
