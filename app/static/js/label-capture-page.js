import { compressImageFile } from './photo-compress.js';

const params = new URLSearchParams(window.location.search);
const token = params.get('token') || '';
const titleEl = document.getElementById('capture-title');
const subtitleEl = document.getElementById('capture-subtitle');
const zoneBanner = document.getElementById('zone-banner');
const zoneTitle = document.getElementById('zone-title');
const zoneSub = document.getElementById('zone-sub');
const zoneMinigrid = document.getElementById('zone-minigrid');
const triggerEl = document.getElementById('capture-trigger');
const galleryTriggerEl = document.getElementById('gallery-trigger');
const btnLabelEl = document.getElementById('capture-btn-label');
const inputEl = document.getElementById('mobile-camera-input');
const galleryInputEl = document.getElementById('mobile-gallery-input');
const previewWrap = document.getElementById('preview-wrap');
const previewImg = document.getElementById('preview-img');
const statusArea = document.getElementById('status-area');

let activeZoneId = null;
let lastPreviewUrl = null;
let pollTimer = null;
let uploadCount = 0;

window.addEventListener('pagehide', () => {
  if (pollTimer) clearInterval(pollTimer);
  if (lastPreviewUrl) {
    try { URL.revokeObjectURL(lastPreviewUrl); } catch {}
    lastPreviewUrl = null;
  }
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
  if (pollTimer) clearInterval(pollTimer);
  triggerEl.style.display = 'none';
  galleryTriggerEl.style.display = 'none';
  zoneBanner.classList.remove('active');
  subtitleEl.textContent = msg || 'This QR link has expired. Generate a new one on your computer.';
  setStatus('Session expired', 'error');
}

function colLetter(c) {
  return c >= 1 && c <= 26 ? String.fromCharCode(64 + c) : String(c);
}

function renderZoneBanner(session) {
  const zone = session?.active_zone || null;
  if (!zone) {
    activeZoneId = null;
    zoneBanner.classList.remove('active');
    if (session?.status === 'completed') {
      subtitleEl.textContent = 'Labels applied! You can close this tab.';
      setStatus('Completed on computer', 'success');
    } else if (uploadCount > 0 && session?.status === 'ready_for_review') {
      subtitleEl.textContent = 'Reviewing labels on your computer. Keep this open if a close-up is needed.';
    }
    return;
  }

  activeZoneId = zone.zone_id;
  zoneBanner.classList.add('active');
  zoneTitle.textContent = `Close-up requested: ${zone.label}`;
  zoneSub.textContent = `Frame compartments ${zone.label} (${zone.slot_count} ${zone.slot_count === 1 ? 'slot' : 'slots'}) up close and snap 1 photo.`;
  subtitleEl.textContent = `Get closer to ${zone.label} so the small label text is crisp.`;
  btnLabelEl.textContent = `Snap close-up of ${zone.label}`;

  const rows = Math.max(1, Math.min(26, Number(zone.grid_rows) || 1));
  const cols = Math.max(1, Math.min(26, Number(zone.grid_cols) || 1));
  const targets = new Set((zone.slot_coords || []).map((c) => String(c).toUpperCase()));
  zoneMinigrid.style.gridTemplateColumns = `repeat(${cols}, minmax(0, 1fr))`;
  zoneMinigrid.innerHTML = '';

  for (let r = 1; r <= rows; r++) {
    for (let c = 1; c <= cols; c++) {
      const cell = document.createElement('div');
      const coord = `${colLetter(c)}${r}`;
      const inBox = r >= zone.min_row && r <= zone.max_row && c >= zone.min_col && c <= zone.max_col;
      cell.className = 'zone-cell';
      if (targets.has(coord)) {
        cell.classList.add('is-target');
      } else if (inBox) {
        cell.classList.add('in-zone');
      }
      zoneMinigrid.appendChild(cell);
    }
  }
}

async function fetchStatus() {
  if (!token) return;
  try {
    const res = await fetch(`/api/v1/label-scan/sessions/${encodeURIComponent(token)}`);
    if (!res.ok) {
      if (res.status === 404 || res.status === 410) {
        showExpired();
      }
      return;
    }
    const data = await res.json();
    if (data.status === 'expired') {
      showExpired();
      return;
    }
    if (data.container_name) {
      titleEl.textContent = `Label Scan: ${data.container_name}`;
    }
    renderZoneBanner(data);
  } catch {
    // Transient Wi-Fi hiccup; keep polling
  }
}

async function handleFile(file) {
  if (!file || !token) return;
  triggerEl.classList.add('disabled');
  galleryTriggerEl.style.pointerEvents = 'none';
  btnLabelEl.textContent = 'Sending full-res photo...';
  setStatus('Uploading high-res image...', 'info');

  try {
    // Preserve up to 4096px so dense organizer stickers stay legible on the first pass.
    const prepared = await compressImageFile(file, 4096, 0.9);
    const fd = new FormData();
    fd.append('file', prepared);
    if (activeZoneId) {
      fd.append('zone_id', activeZoneId);
    }
    const res = await fetch(`/api/v1/label-scan/sessions/${encodeURIComponent(token)}/upload`, {
      method: 'POST',
      body: fd,
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      if (res.status === 410 || res.status === 404) {
        showExpired(body?.error?.message || body?.detail);
        return;
      }
      throw new Error(body?.error?.message || body?.detail || 'Upload failed.');
    }

    uploadCount += 1;
    if (lastPreviewUrl) {
      try { URL.revokeObjectURL(lastPreviewUrl); } catch {}
    }
    lastPreviewUrl = URL.createObjectURL(prepared);
    previewImg.src = lastPreviewUrl;
    previewWrap.style.display = 'block';

    triggerEl.classList.remove('disabled');
    galleryTriggerEl.style.pointerEvents = 'auto';
    btnLabelEl.textContent = activeZoneId ? 'Retake close-up photo' : 'Retake overview photo';
    setStatus('Photo sent to computer! Check your screen.', 'success');
    await fetchStatus();
  } catch (err) {
    triggerEl.classList.remove('disabled');
    galleryTriggerEl.style.pointerEvents = 'auto';
    btnLabelEl.textContent = 'Tap to open camera';
    setStatus(err.message || 'Upload failed. Try again.', 'error');
  }
}

inputEl.addEventListener('change', (e) => {
  const f = e.target?.files?.[0];
  inputEl.value = '';
  if (f) handleFile(f);
});

galleryInputEl.addEventListener('change', (e) => {
  const f = e.target?.files?.[0];
  galleryInputEl.value = '';
  if (f) handleFile(f);
});

if (!token) {
  showExpired('Missing scan token. Scan the QR code on your computer screen.');
} else {
  fetchStatus();
  pollTimer = setInterval(fetchStatus, 1500);
}
