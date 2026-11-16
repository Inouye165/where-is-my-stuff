/**
 * Resilient API Client with AbortController Timeouts & Structured Error Parsing
 */

const DEFAULT_TIMEOUT_MS = 15000;
const UPLOAD_TIMEOUT_MS = 45000;

const syncChannel = typeof BroadcastChannel !== 'undefined' ? new BroadcastChannel('wims_sync') : null;

export function notifyCrossTabSync() {
  const ts = `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  if (syncChannel) {
    try {
      syncChannel.postMessage({ type: 'refresh', ts });
      return;
    } catch {}
  }
  try {
    localStorage.setItem('wims_sync_ts', ts);
  } catch {}
}

export function onCrossTabSync(callback) {
  let debounceTimer = null;
  let lastSeenToken = null;

  const scheduleRefresh = (token) => {
    if (token && token === lastSeenToken) return;
    if (token) lastSeenToken = token;
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => {
      debounceTimer = null;
      callback();
    }, 50);
  };

  if (syncChannel) {
    syncChannel.addEventListener('message', e => {
      if (e && e.data && e.data.type === 'refresh') scheduleRefresh(e.data.ts);
    });
  } else if (typeof window !== 'undefined') {
    window.addEventListener('storage', e => {
      if (e && e.key === 'wims_sync_ts') scheduleRefresh(e.newValue);
    });
  }
}

function extractErrorMessage(payload, fallbackMessage) {
  if (!payload) return fallbackMessage;
  if (typeof payload.detail === 'string' && payload.detail.trim()) {
    return payload.detail;
  }
  if (payload.error && typeof payload.error.message === 'string' && payload.error.message.trim()) {
    return payload.error.message;
  }
  if (Array.isArray(payload.detail) && payload.detail.length > 0) {
    const first = payload.detail[0];
    if (first && first.msg) return first.msg;
  }
  return fallbackMessage;
}

export async function apiFetch(url, options = {}) {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, fallbackError = 'Request failed.', ...fetchOpts } = options;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, {
      ...fetchOpts,
      signal: controller.signal
    });

    if (!response.ok) {
      const errBody = await response.json().catch(() => null);
      const httpErr = new Error(extractErrorMessage(errBody, fallbackError));
      httpErr.status = response.status;
      throw httpErr;
    }

    const isMutation = fetchOpts.method && fetchOpts.method !== 'GET' && !String(url).includes('/search/');

    if (response.status === 204) {
      if (isMutation) notifyCrossTabSync();
      return null;
    }

    const data = await response.json();
    if (isMutation) {
      notifyCrossTabSync();
    }
    return data;
  } catch (err) {
    if (err && err.name === 'AbortError') {
      throw new Error('Request timed out. Check your Wi-Fi connection and try again.');
    }
    if (err instanceof TypeError) {
      const host = (typeof window !== 'undefined' && window.location && window.location.host)
        ? ` (${window.location.host})`
        : '';
      throw new Error(`Cannot reach the server${host}. Make sure the server is running and try again.`);
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export function apiGet(url, fallbackError = 'Could not load data.') {
  return apiFetch(url, { method: 'GET', fallbackError });
}

export function apiPost(url, payload = undefined, fallbackError = 'Could not save data.', options = {}) {
  const opts = { method: 'POST', fallbackError, ...options };
  if (payload !== undefined) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(payload);
  }
  return apiFetch(url, opts);
}

export function apiPut(url, payload, fallbackError = 'Could not update data.') {
  return apiFetch(url, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    fallbackError
  });
}

export function apiPatch(url, payload, fallbackError = 'Could not update data.') {
  return apiFetch(url, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
    fallbackError
  });
}

export function apiDelete(url, fallbackError = 'Could not delete record.') {
  return apiFetch(url, { method: 'DELETE', fallbackError });
}

export function apiUpload(url, formData, fallbackError = 'Photo upload failed.', options = {}) {
  return apiFetch(url, {
    method: 'POST',
    body: formData,
    timeoutMs: UPLOAD_TIMEOUT_MS,
    fallbackError,
    ...options
  });
}

