/**
 * DOM Utilities, XSS-Safe Escaping, Modal Controls, Toast & Submit Locking
 */

import { guardModalClose, markModalClean } from './unsaved-guard.js';

export function escapeHtml(str) {
  if (str === null || str === undefined) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// Output is only safe inside a single-quoted JS string within an HTML attribute
// (e.g. onclick="fn('${escapeJsStr(x)}')"). The browser HTML-decodes the attribute
// before running it, so '&' must be entity-escaped or '&#39;' would break out.
export function escapeJsStr(str) {
  if (str === null || str === undefined || str === '') return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/"/g, '&quot;')
    .replace(/</g, '\\x3c')
    .replace(/>/g, '\\x3e')
    .replace(/\r/g, '\\r')
    .replace(/\n/g, '\\n');
}

export function sanitizePhotoUrl(url) {
  if (!url || typeof url !== 'string') return '';
  const trimmed = url.trim();
  if (!trimmed.startsWith('/uploads/')) return '';
  if (trimmed.includes('..') || trimmed.includes('\\') || trimmed.includes('\0')) return '';
  if (!/^\/uploads\/[a-zA-Z0-9._/\-]+$/.test(trimmed)) return '';
  return trimmed;
}

export function formatDate(isoStr) {
  if (!isoStr) return 'recently';
  try {
    const d = new Date(isoStr);
    if (Number.isNaN(d.getTime())) return String(isoStr);
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  } catch {
    return String(isoStr);
  }
}

export function pluralize(count, singular, plural = singular + 's') {
  return `${count} ${count === 1 ? singular : plural}`;
}

export function showToast(msg, type = 'success') {
  const shelf = document.getElementById('toast-shelf');
  if (!shelf) return;
  const toast = document.createElement('div');
  toast.className = `toast ${type === 'error' ? 'toast-error' : 'toast-success'}`;
  toast.textContent = msg;
  shelf.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 220);
  }, 3000);
}

export function openLightbox(src, caption = '') {
  const safeSrc = sanitizePhotoUrl(src);
  if (!safeSrc) return;
  const img = document.getElementById('lightbox-img');
  const cap = document.getElementById('lightbox-caption');
  const modal = document.getElementById('lightbox-modal');
  if (img) img.src = safeSrc;
  if (cap) cap.textContent = caption;
  if (modal) modal.classList.add('open');
}

export function closeLightbox() {
  document.getElementById('lightbox-modal')?.classList.remove('open');
}

const forceCloseHooks = new Map();

export function registerModalForceCloseHook(modalId, fn) {
  if (modalId && typeof fn === 'function') {
    forceCloseHooks.set(modalId, fn);
  }
}

export function forceCloseModal(id) {
  markModalClean(id);
  const el = document.getElementById(id);
  if (el) {
    if (el.dataset) el.dataset.scanning = 'false';
    el.classList.remove('open');
  }
  const hook = forceCloseHooks.get(id);
  if (hook) {
    try { hook(); } catch {}
  }
}

export function closeModal(id, force = false) {
  if (force) {
    forceCloseModal(id);
    return;
  }
  guardModalClose(id, forceCloseModal);
}

export function closeOnScrim(e, modalId) {
  if (e && e.target && e.target.id === modalId) {
    if (e.target.dataset?.scanning === 'true') {
      showToast('AI scan is still running — wait for it to finish or click Cancel.', 'info');
      return;
    }
    closeModal(modalId);
  }
}

export function closeAllModals() {
  for (const s of document.querySelectorAll('.modal-scrim.open')) {
    if (s.id) {
      forceCloseModal(s.id);
    } else {
      s.classList.remove('open');
    }
  }
  closeLightbox();
}

const activeLocks = new Set();

export async function withSubmitLock(btnId, busyText, actionFn) {
  if (activeLocks.has(btnId)) return;
  activeLocks.add(btnId);

  const btn = btnId ? document.getElementById(btnId) : null;
  const prevText = btn ? btn.textContent : '';
  if (btn) {
    btn.disabled = true;
    if (busyText) btn.textContent = busyText;
  }

  try {
    return await actionFn();
  } finally {
    if (btn) {
      btn.disabled = false;
      if (busyText) btn.textContent = prevText;
    }
    activeLocks.delete(btnId);
  }
}

