/**
 * Desktop-to-Phone QR Camera Handoff
 */
import { apiGet, apiPost } from './api.js';
import { showToast } from './dom.js';
import { openPhotoEditorModal } from './photo-editor.js';

let pollTimer = null;
let activeToken = null;
let activeInputId = null;
let activeLabel = 'Take a photo';
let activeCallback = null;
let activeLocationId = null;
let activeLocationDoneCb = null;
let activeMultiPhotos = [];
let bypassInputId = null;
const registeredHandlers = new Map();

export function isDesktopBrowser() {
  if (typeof navigator === 'undefined') return true;
  if (navigator.userAgentData && typeof navigator.userAgentData.mobile === 'boolean') {
    return !navigator.userAgentData.mobile;
  }
  const ua = navigator.userAgent || '';
  const isMobileUa = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(ua);
  const isIpadOs = navigator.maxTouchPoints > 1 && /Macintosh/i.test(ua);
  return !isMobileUa && !isIpadOs;
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

export function closePhonePhotoModal() {
  stopPolling();
  activeToken = null;
  activeLocationId = null;
  activeLocationDoneCb = null;
  activeMultiPhotos = [];
  document.getElementById('phone-photo-modal')?.classList.remove('open');
}

export async function finishLocationPhoneCapture(shouldScan = false) {
  const cb = activeLocationDoneCb;
  const photos = [...activeMultiPhotos];
  const locId = activeLocationId;
  closePhonePhotoModal();
  if (typeof cb === 'function') {
    await cb({ locationId: locId, photos, shouldScan });
  }
}

export function closePhonePhotoOnScrim(e) {
  if (e && e.target && e.target.id === 'phone-photo-modal') {
    closePhonePhotoModal();
  }
}

export function chooseDesktopFileFallback() {
  const inputId = activeInputId;
  closePhonePhotoModal();
  if (!inputId) return;
  const inputEl = document.getElementById(inputId);
  if (!inputEl) return;
  bypassInputId = inputId;
  inputEl.click();
}

export async function refreshPhonePhotoQr() {
  if (activeLocationId) {
    await openLocationPhoneCaptureModal({
      locationId: activeLocationId,
      locationName: activeLabel,
      onDone: activeLocationDoneCb,
    });
    return;
  }
  if (!activeInputId) return;
  await openPhonePhotoModal({
    inputId: activeInputId,
    label: activeLabel,
    onPhotoReceived: activeCallback,
  });
}

function renderReceivedPhoneThumbs(photos) {
  const wrap = document.getElementById('phone-photo-received-wrap');
  const countEl = document.getElementById('phone-photo-received-count');
  const thumbsEl = document.getElementById('phone-photo-received-thumbs');
  if (!wrap || !countEl || !thumbsEl) return;

  if (!photos || photos.length === 0) {
    wrap.style.display = 'none';
    thumbsEl.innerHTML = '';
    return;
  }
  wrap.style.display = 'block';
  countEl.textContent = `${photos.length} ${photos.length === 1 ? 'photo' : 'photos'} added`;
  thumbsEl.innerHTML = '';
  for (const p of photos) {
    const src = p.thumbnail_path || p.original_path || '';
    if (!src.startsWith('/uploads/')) continue;
    const img = document.createElement('img');
    img.src = src;
    img.alt = 'Phone photo';
    thumbsEl.appendChild(img);
  }
}

export async function openLocationPhoneCaptureModal({ locationId, locationName = 'Drawer', onDone }) {
  stopPolling();
  activeInputId = null;
  activeLabel = locationName;
  activeCallback = null;
  activeLocationId = locationId;
  activeLocationDoneCb = onDone;
  activeMultiPhotos = [];

  const modal = document.getElementById('phone-photo-modal');
  const titleEl = document.getElementById('phone-photo-title');
  const qrImg = document.getElementById('phone-photo-qr-img');
  const statusEl = document.getElementById('phone-photo-status');
  const retryBtn = document.getElementById('phone-photo-retry-btn');
  const urlHintEl = document.getElementById('phone-photo-url-hint');
  const computerBtn = document.getElementById('phone-photo-computer-btn');
  const cancelBtn = document.getElementById('phone-photo-cancel-btn');
  const doneBtn = document.getElementById('phone-photo-done-btn');
  const scanBtn = document.getElementById('phone-photo-scan-btn');

  if (titleEl) titleEl.textContent = `Add photos to ${locationName}`;
  if (statusEl) {
    statusEl.textContent = 'Generating QR code...';
    statusEl.className = 'phone-qr-status';
  }
  if (retryBtn) retryBtn.style.display = 'none';
  if (computerBtn) computerBtn.style.display = 'none';
  if (cancelBtn) cancelBtn.style.display = 'inline-flex';
  if (doneBtn) doneBtn.style.display = 'none';
  if (scanBtn) scanBtn.style.display = 'none';
  renderReceivedPhoneThumbs([]);

  if (qrImg) {
    qrImg.style.display = 'none';
    qrImg.removeAttribute('src');
  }
  if (urlHintEl) urlHintEl.textContent = '';

  modal?.classList.add('open');

  try {
    const session = await apiPost(
      '/api/v1/photos/capture-sessions',
      { label: locationName, location_id: locationId },
      'Could not start phone camera session.'
    );
    activeToken = session.token;
    if (qrImg) {
      qrImg.style.display = 'block';
      qrImg.src = `${session.qr_url}?t=${Date.now()}`;
    }
    if (urlHintEl) {
      urlHintEl.textContent = session.capture_url;
    }
    if (statusEl) {
      statusEl.textContent = 'Scan with your phone to add 1 or more photos...';
    }

    const tokenForPoll = session.token;
    pollTimer = setInterval(async () => {
      if (activeToken !== tokenForPoll) {
        stopPolling();
        return;
      }
      try {
        const res = await apiGet(`/api/v1/photos/capture-sessions/${encodeURIComponent(tokenForPoll)}`);
        if (activeToken !== tokenForPoll) return;

        const list = Array.isArray(res.photos) && res.photos.length
          ? res.photos
          : (res.photo ? [res.photo] : []);
        if (list.length > 0) {
          activeMultiPhotos = list;
          renderReceivedPhoneThumbs(list);
          if (statusEl) {
            statusEl.textContent = `Receiving photos (${list.length} saved). Keep snapping or click Done.`;
          }
          if (cancelBtn) cancelBtn.style.display = 'none';
          if (doneBtn) doneBtn.style.display = 'inline-flex';
          if (scanBtn) scanBtn.style.display = 'inline-flex';
        } else if (res.status === 'expired') {
          stopPolling();
          if (statusEl) {
            statusEl.textContent = 'QR code expired. Generate a new one to continue.';
            statusEl.className = 'phone-qr-status expired';
          }
          if (retryBtn) retryBtn.style.display = 'inline-flex';
        }
      } catch {
        // Transient network hiccup; keep polling
      }
    }, 800);
  } catch (err) {
    if (statusEl) {
      statusEl.textContent = err.message || 'Could not start phone camera session.';
      statusEl.className = 'phone-qr-status expired';
    }
    if (retryBtn) retryBtn.style.display = 'inline-flex';
    showToast(err.message || 'Could not start phone camera session.', 'error');
  }
}

export async function openPhonePhotoModal({ inputId, label = 'Take a photo', onPhotoReceived }) {
  stopPolling();
  activeInputId = inputId;
  activeLabel = label;
  activeCallback = onPhotoReceived;
  activeLocationId = null;
  activeLocationDoneCb = null;
  activeMultiPhotos = [];

  const computerBtn = document.getElementById('phone-photo-computer-btn');
  const cancelBtn = document.getElementById('phone-photo-cancel-btn');
  const doneBtn = document.getElementById('phone-photo-done-btn');
  const scanBtn = document.getElementById('phone-photo-scan-btn');
  if (computerBtn) computerBtn.style.display = 'inline-flex';
  if (cancelBtn) cancelBtn.style.display = 'inline-flex';
  if (doneBtn) doneBtn.style.display = 'none';
  if (scanBtn) scanBtn.style.display = 'none';
  renderReceivedPhoneThumbs([]);

  const modal = document.getElementById('phone-photo-modal');
  const titleEl = document.getElementById('phone-photo-title');
  const qrImg = document.getElementById('phone-photo-qr-img');
  const statusEl = document.getElementById('phone-photo-status');
  const retryBtn = document.getElementById('phone-photo-retry-btn');
  const urlHintEl = document.getElementById('phone-photo-url-hint');

  if (titleEl) titleEl.textContent = label;
  if (statusEl) {
    statusEl.textContent = 'Generating QR code...';
    statusEl.className = 'phone-qr-status';
  }
  if (retryBtn) retryBtn.style.display = 'none';
  if (qrImg) {
    qrImg.style.display = 'none';
    qrImg.removeAttribute('src');
  }
  if (urlHintEl) urlHintEl.textContent = '';

  modal?.classList.add('open');

  try {
    const session = await apiPost('/api/v1/photos/capture-sessions', { label }, 'Could not start phone camera session.');
    activeToken = session.token;
    if (qrImg) {
      qrImg.style.display = 'block';
      qrImg.src = `${session.qr_url}?t=${Date.now()}`;
    }
    if (urlHintEl) {
      urlHintEl.textContent = session.capture_url;
    }
    if (statusEl) {
      statusEl.textContent = 'Scan with your phone camera — waiting for photo...';
    }

    const tokenForPoll = session.token;
    pollTimer = setInterval(async () => {
      if (activeToken !== tokenForPoll) {
        stopPolling();
        return;
      }
      try {
        const res = await apiGet(`/api/v1/photos/capture-sessions/${encodeURIComponent(tokenForPoll)}`);
        if (activeToken !== tokenForPoll) return;

        if (res.status === 'uploaded' && res.photo) {
          const cb = activeCallback;
          const labelForEditor = activeLabel;
          closePhonePhotoModal();
          openPhotoEditorModal({
            imageUrl: res.photo.original_path,
            sourcePhoto: res.photo,
            title: `Adjust ${labelForEditor}`,
            onApply: async (finalPhoto) => {
              if (typeof cb === 'function') {
                await cb(finalPhoto);
              }
            },
          });
        } else if (res.status === 'expired') {
          stopPolling();
          if (statusEl) {
            statusEl.textContent = 'QR code expired. Generate a new one to continue.';
            statusEl.className = 'phone-qr-status expired';
          }
          if (retryBtn) retryBtn.style.display = 'inline-flex';
        }
      } catch {
        // Transient network hiccup; keep polling while modal is open
      }
    }, 800);
  } catch (err) {
    if (statusEl) {
      statusEl.textContent = err.message || 'Could not start phone camera session.';
      statusEl.className = 'phone-qr-status expired';
    }
    if (retryBtn) retryBtn.style.display = 'inline-flex';
    showToast(err.message || 'Could not start phone camera session.', 'error');
  }
}

export function registerDesktopPhotoHandler(inputId, config) {
  registeredHandlers.set(inputId, config);
}

export function requestPhotoForInput(inputId) {
  const inputEl = document.getElementById(inputId);
  if (!inputEl) return;

  if (!isDesktopBrowser() || bypassInputId === inputId) {
    if (bypassInputId === inputId) bypassInputId = null;
    inputEl.click();
    return;
  }

  const cfg = registeredHandlers.get(inputId);
  if (!cfg) {
    inputEl.click();
    return;
  }

  const label = typeof cfg.getLabel === 'function' ? cfg.getLabel() : (cfg.label || 'Take a photo');
  openPhonePhotoModal({
    inputId,
    label,
    onPhotoReceived: cfg.onPhotoReceived,
  });
}

let interceptsBound = false;

export function bindDesktopPhotoIntercepts() {
  if (interceptsBound) return;
  interceptsBound = true;

  document.addEventListener('click', (e) => {
    if (!isDesktopBrowser()) return;

    const labelEl = e.target && typeof e.target.closest === 'function' ? e.target.closest('label[for]') : null;
    const directInput = e.target && e.target.tagName === 'INPUT' && e.target.type === 'file' ? e.target : null;
    const targetId = labelEl ? labelEl.getAttribute('for') : (directInput ? directInput.id : null);

    if (!targetId || !registeredHandlers.has(targetId)) return;

    if (bypassInputId === targetId) {
      bypassInputId = null;
      return;
    }

    e.preventDefault();
    e.stopPropagation();
    requestPhotoForInput(targetId);
  }, true);
}
