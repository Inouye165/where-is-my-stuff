/**
 * Centralized Delegated Event Dispatcher
 * Supports data-action, data-change-action, data-input-action, data-submit-action, data-keydown-action
 */

const actionRegistry = new Map();

export function registerActions(actions) {
  if (!actions || typeof actions !== 'object') return;
  for (const [name, fn] of Object.entries(actions)) {
    if (typeof fn === 'function') {
      actionRegistry.set(name, fn);
    }
  }
}

export function unregisterAction(name) {
  actionRegistry.delete(name);
}

export function getRegisteredAction(name) {
  return actionRegistry.get(name);
}

function parseArgs(rawArgs) {
  if (rawArgs === undefined || rawArgs === null || rawArgs === '') {
    return null;
  }
  try {
    return JSON.parse(rawArgs);
  } catch {
    return rawArgs;
  }
}

async function dispatchElementAction(el, attrName, event) {
  const actionName = el.dataset?.[attrName];
  if (!actionName) return;
  const fn = actionRegistry.get(actionName);
  if (typeof fn !== 'function') return;

  if (el.dataset.preventDefault !== undefined || attrName === 'submitAction') {
    event.preventDefault();
  }
  if (el.dataset.stopPropagation !== undefined) {
    event.stopPropagation();
  }

  const parsed = parseArgs(el.dataset.args);
  if (parsed === null) {
    return fn(event, el);
  }
  if (Array.isArray(parsed)) {
    return fn(...parsed, event, el);
  }
  return fn(parsed, event, el);
}

export function initDelegatedEvents(root = typeof document !== 'undefined' ? document : null) {
  if (!root || typeof root.addEventListener !== 'function') return;

  root.addEventListener('click', (event) => {
    const target = event.target?.closest?.('[data-action]');
    if (target) {
      void dispatchElementAction(target, 'action', event);
    }
  });

  root.addEventListener('change', (event) => {
    const target = event.target?.closest?.('[data-change-action]');
    if (target) {
      void dispatchElementAction(target, 'changeAction', event);
    }
  });

  root.addEventListener('input', (event) => {
    const target = event.target?.closest?.('[data-input-action]');
    if (target) {
      void dispatchElementAction(target, 'inputAction', event);
    }
  });

  root.addEventListener('submit', (event) => {
    const target = event.target?.closest?.('[data-submit-action]');
    if (target) {
      void dispatchElementAction(target, 'submitAction', event);
    }
  });

  root.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      const btnRole = event.target?.closest?.('[role="button"][tabindex="0"]');
      if (btnRole && event.target === btnRole) {
        const tag = (btnRole.tagName || '').toUpperCase();
        if (!['BUTTON', 'A', 'INPUT', 'TEXTAREA', 'SELECT'].includes(tag)) {
          event.preventDefault();
          btnRole.click();
          return;
        }
      }
    }
    const target = event.target?.closest?.('[data-keydown-action]');
    if (target) {
      void dispatchElementAction(target, 'keydownAction', event);
    }
  });
}
