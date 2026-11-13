/**
 * Unsaved Changes Guard
 * Protects the Grid Designer and edit/create modals from accidental data loss
 * on navigation, modal close, or browser tab close/refresh.
 */

const TRACKED_MODAL_IDS = ['add-item-modal', 'add-storage-modal', 'drawer-rapid-entry-modal'];

let gridDirtyCheckFn = null;
let gridSaveFn = null;
let gridDiscardFn = null;

let activePromptResolve = null;

export function registerGridUnsavedHooks({ isDirty, save, discard }) {
  gridDirtyCheckFn = isDirty;
  gridSaveFn = save;
  gridDiscardFn = discard;
}

export function isGridDirty() {
  return typeof gridDirtyCheckFn === 'function' ? Boolean(gridDirtyCheckFn()) : false;
}

export function markModalClean(modalId) {
  const el = document.getElementById(modalId);
  if (el) el.dataset.dirty = 'false';
}

export function isModalDirty(modalId) {
  if (modalId === 'grid-designer-modal') {
    return isGridDirty();
  }
  const el = document.getElementById(modalId);
  return Boolean(el && el.classList.contains('open') && el.dataset.dirty === 'true');
}

export function hasAnyUnsavedChanges() {
  if (isGridDirty()) return true;
  return TRACKED_MODAL_IDS.some(id => isModalDirty(id));
}

export function resolveUnsavedPrompt(choice) {
  const modal = document.getElementById('unsaved-confirm-modal');
  if (modal) modal.classList.remove('open');
  if (activePromptResolve) {
    const fn = activePromptResolve;
    activePromptResolve = null;
    fn(choice);
  }
}

export function showUnsavedPrompt(message = 'You have unsaved changes. Would you like to save them before leaving?') {
  const modal = document.getElementById('unsaved-confirm-modal');
  const msgEl = document.getElementById('unsaved-confirm-msg');
  if (!modal) {
    return Promise.resolve(window.confirm(message) ? 'discard' : 'cancel');
  }
  if (msgEl) msgEl.textContent = message;
  modal.classList.add('open');

  return new Promise(resolve => {
    activePromptResolve = resolve;
  });
}

export async function guardModalClose(modalId, forceCloseFn) {
  if (!isModalDirty(modalId)) {
    forceCloseFn(modalId);
    return true;
  }

  const choice = await showUnsavedPrompt('You have unsaved changes. Save before closing?');
  if (choice === 'cancel') return false;

  if (choice === 'discard') {
    if (modalId === 'grid-designer-modal' && typeof gridDiscardFn === 'function') {
      gridDiscardFn();
    } else {
      markModalClean(modalId);
    }
    forceCloseFn(modalId);
    return true;
  }

  if (choice === 'save') {
    if (modalId === 'grid-designer-modal' && typeof gridSaveFn === 'function') {
      await gridSaveFn();
      return true;
    }
    const form = document.querySelector(`#${modalId} form`);
    if (form) {
      form.requestSubmit();
      return true;
    }
  }
  return false;
}

export async function guardNavigation(proceedFn) {
  if (!isGridDirty()) {
    proceedFn();
    return true;
  }

  const choice = await showUnsavedPrompt('You have unsaved grid layout changes. Save before leaving this page?');
  if (choice === 'cancel') return false;

  if (choice === 'discard') {
    if (typeof gridDiscardFn === 'function') gridDiscardFn();
    proceedFn();
    return true;
  }

  if (choice === 'save') {
    if (typeof gridSaveFn === 'function') {
      const ok = await gridSaveFn();
      if (ok !== false) {
        proceedFn();
        return true;
      }
    }
  }
  return false;
}

export function initUnsavedGuard() {
  if (typeof document === 'undefined') return;

  for (const id of TRACKED_MODAL_IDS) {
    const modal = document.getElementById(id);
    if (!modal) continue;
    modal.addEventListener('input', () => {
      if (modal.classList.contains('open')) modal.dataset.dirty = 'true';
    });
    modal.addEventListener('change', () => {
      if (modal.classList.contains('open')) modal.dataset.dirty = 'true';
    });
  }

  window.addEventListener('beforeunload', e => {
    if (hasAnyUnsavedChanges()) {
      e.preventDefault();
      e.returnValue = '';
    }
  });
}

