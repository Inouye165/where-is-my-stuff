/**
 * Accessible Dialog Manager & Keyboard Focus Trapping (WCAG 2.2 AA)
 */

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  'summary',
  '[tabindex]:not([tabindex="-1"])',
  '[role="button"]:not([aria-disabled="true"])'
].join(', ');

const openDialogStack = [];
const initializedDocs = new WeakSet();

function isElementVisible(el) {
  if (!el) return false;
  if (el.hidden) return false;
  if (typeof el.getAttribute === 'function' && el.getAttribute('aria-hidden') === 'true') return false;
  if (el.style && (el.style.display === 'none' || el.style.visibility === 'hidden')) return false;
  if (typeof el.closest === 'function' && el.closest('[hidden]')) return false;

  if (typeof window !== 'undefined' && typeof window.getComputedStyle === 'function') {
    try {
      const style = window.getComputedStyle(el);
      if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
    } catch {
      // Ignore mock elements that cannot be passed to getComputedStyle.
    }
  }
  return true;
}

export function getFocusableElements(container) {
  if (!container || typeof container.querySelectorAll !== 'function') return [];
  const candidates = Array.from(container.querySelectorAll(FOCUSABLE_SELECTOR));
  return candidates.filter((el) => {
    if (!el || el.disabled) return false;
    if (typeof el.getAttribute === 'function') {
      const tabIdx = el.getAttribute('tabindex');
      if (tabIdx === '-1') return false;
      if (el.getAttribute('aria-disabled') === 'true') return false;
    }
    return isElementVisible(el);
  });
}

function focusFallbackContainer(dialogEl) {
  const card = dialogEl.querySelector?.('.modal-card') || dialogEl;
  if (card && typeof card.focus === 'function') {
    if (typeof card.getAttribute === 'function' && !card.getAttribute('tabindex')) {
      card.setAttribute?.('tabindex', '-1');
    } else if (card.tabIndex === undefined) {
      card.tabIndex = -1;
    }
    card.focus();
  }
}

export function activateDialogFocus(dialogEl, doc = typeof document !== 'undefined' ? document : null) {
  if (!dialogEl || !doc) return;

  const existingIdx = openDialogStack.findIndex((entry) => entry.el === dialogEl);
  if (existingIdx === -1) {
    openDialogStack.push({ el: dialogEl, opener: doc.activeElement || null });
  } else {
    const [existing] = openDialogStack.splice(existingIdx, 1);
    openDialogStack.push(existing);
  }

  const active = doc.activeElement;
  if (active && typeof dialogEl.contains === 'function' && dialogEl.contains(active)) {
    return;
  }

  const autofocusTarget = dialogEl.querySelector?.('[autofocus]');
  if (autofocusTarget && !autofocusTarget.disabled && isElementVisible(autofocusTarget) && typeof autofocusTarget.focus === 'function') {
    autofocusTarget.focus();
    return;
  }

  const focusables = getFocusableElements(dialogEl);
  if (focusables.length > 0 && typeof focusables[0].focus === 'function') {
    focusables[0].focus();
    return;
  }

  focusFallbackContainer(dialogEl);
}

export function deactivateDialogFocus(dialogEl, doc = typeof document !== 'undefined' ? document : null) {
  if (!dialogEl) return;

  let removed = null;
  for (let i = openDialogStack.length - 1; i >= 0; i -= 1) {
    if (openDialogStack[i].el === dialogEl) {
      removed = openDialogStack.splice(i, 1)[0];
      break;
    }
  }

  if (openDialogStack.length > 0) {
    const topDialog = openDialogStack[openDialogStack.length - 1].el;
    const active = doc?.activeElement;
    if (!active || (typeof topDialog.contains === 'function' && !topDialog.contains(active))) {
      const focusables = getFocusableElements(topDialog);
      if (focusables.length > 0) {
        focusables[0].focus?.();
      } else {
        focusFallbackContainer(topDialog);
      }
    }
    return;
  }

  const opener = removed?.opener;
  if (opener && opener.isConnected !== false && typeof opener.focus === 'function') {
    opener.focus();
  }
}

function getTopActiveDialog(doc) {
  const pinOverlay = doc.getElementById?.('wims-pin-unlock-overlay');
  if (pinOverlay && isElementVisible(pinOverlay)) return pinOverlay;

  for (let i = openDialogStack.length - 1; i >= 0; i -= 1) {
    const candidate = openDialogStack[i].el;
    if (candidate && candidate.classList?.contains?.('open')) {
      return candidate;
    }
  }

  const openDialogs = doc.querySelectorAll?.('.modal-scrim.open, #lightbox-modal.open');
  if (openDialogs && openDialogs.length > 0) {
    return openDialogs[openDialogs.length - 1];
  }
  return null;
}

export function handleDialogFocusTrapKeydown(e, doc = typeof document !== 'undefined' ? document : null) {
  if (!e || e.key !== 'Tab' || !doc) return;

  const activeDialog = getTopActiveDialog(doc);
  if (!activeDialog) return;

  const focusables = getFocusableElements(activeDialog);
  if (focusables.length === 0) {
    e.preventDefault?.();
    focusFallbackContainer(activeDialog);
    return;
  }

  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  const active = doc.activeElement;
  const inside = focusables.includes(active);

  if (e.shiftKey) {
    if (!inside || active === first) {
      e.preventDefault?.();
      last.focus?.();
    }
    return;
  }

  if (!inside || active === last) {
    e.preventDefault?.();
    first.focus?.();
  }
}

function ensureDialogSemantics(doc) {
  const modals = Array.from(doc.querySelectorAll?.('.modal-scrim') || []);
  modals.forEach((modal, idx) => {
    if (typeof modal.setAttribute !== 'function') return;
    if (!modal.getAttribute?.('role')) {
      modal.setAttribute('role', 'dialog');
    }
    if (!modal.getAttribute?.('aria-modal')) {
      modal.setAttribute('aria-modal', 'true');
    }
    if (!modal.getAttribute?.('aria-labelledby')) {
      const titleEl = modal.querySelector?.('.modal-title');
      if (titleEl) {
        if (!titleEl.id) {
          titleEl.id = `${modal.id || `modal-${idx}`}-title`;
        }
        modal.setAttribute('aria-labelledby', titleEl.id);
      }
    }
  });

  const lightbox = doc.getElementById?.('lightbox-modal');
  if (lightbox && typeof lightbox.setAttribute === 'function') {
    if (!lightbox.getAttribute?.('role')) lightbox.setAttribute('role', 'dialog');
    if (!lightbox.getAttribute?.('aria-modal')) lightbox.setAttribute('aria-modal', 'true');
    if (!lightbox.getAttribute?.('aria-label')) lightbox.setAttribute('aria-label', 'Full size photo preview');
  }
}

export function initDialogA11y(doc = typeof document !== 'undefined' ? document : null) {
  if (!doc || typeof doc.addEventListener !== 'function') return;
  if (initializedDocs.has(doc)) return;
  initializedDocs.add(doc);

  ensureDialogSemantics(doc);

  doc.addEventListener('keydown', (e) => {
    handleDialogFocusTrapKeydown(e, doc);
  });

  if (typeof MutationObserver === 'function') {
    const dialogState = new WeakMap();
    const observed = Array.from(doc.querySelectorAll?.('.modal-scrim, #lightbox-modal') || []);
    const observer = new MutationObserver((mutations) => {
      for (const mutation of mutations) {
        if (mutation.type !== 'attributes' || mutation.attributeName !== 'class') continue;
        const target = mutation.target;
        const wasOpen = Boolean(dialogState.get(target));
        const isOpen = Boolean(target.classList?.contains?.('open'));
        if (wasOpen === isOpen) continue;
        dialogState.set(target, isOpen);
        if (isOpen) {
          activateDialogFocus(target, doc);
        } else {
          deactivateDialogFocus(target, doc);
        }
      }
    });

    for (const el of observed) {
      dialogState.set(el, Boolean(el.classList?.contains?.('open')));
      observer.observe(el, { attributes: true, attributeFilter: ['class'] });
    }
  }
}

export function _resetDialogStackForTests() {
  openDialogStack.length = 0;
}
