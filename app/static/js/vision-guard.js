/**
 * Vision Provider Readiness Guard & Immediate No-Model Pop-Up
 * Single source of truth used by both Rapid Scan (modals.js) and Unit Setup Wizard.
 */
import { apiGet } from './api.js';
import { closeModal, escapeHtml, showToast } from './dom.js';

let activeCallbacks = {
  onSwitchProvider: null,
  onRetry: null,
  onManual: null,
};

export function closeNoModelModal() {
  closeModal('no-vision-model-modal', true);
}

function buildProviderExplanation(selectedProvider, meta) {
  const providers = meta?.providers || {};
  const localInfo = providers.local || {};
  const geminiInfo = providers.gemini || {};
  const openaiInfo = providers.openai || {};

  if (selectedProvider === 'gemini' && !geminiInfo.available) {
    return {
      headline: 'Google Gemini is not set up yet',
      detail: 'Add your Gemini API key in settings or switch to an available scanner below.',
    };
  }
  if (selectedProvider === 'openai' && !openaiInfo.available) {
    return {
      headline: 'OpenAI is not set up yet',
      detail: 'Add your OpenAI API key in settings or switch to an available scanner below.',
    };
  }
  return {
    headline: 'No AI vision model is running right now',
    detail: `Local AI (Ollama: ${localInfo.model || 'qwen2.5vl:7b'}) could not be reached, and no cloud scanner is set up. Start Ollama on your computer so photos can be scanned automatically.`,
  };
}

function renderNoModelModal(selectedProvider, meta) {
  const bodyEl = document.getElementById('no-vision-model-body');
  if (!bodyEl) return;

  const providers = meta?.providers || {};
  const explanation = buildProviderExplanation(selectedProvider, meta);
  const availableAlternatives = Object.entries(providers).filter(
    ([key, info]) => key !== selectedProvider && info && info.available
  );

  bodyEl.innerHTML = `
    <div class="nomodel-alert-box">
      <strong class="nomodel-headline">${escapeHtml(explanation.headline)}</strong>
      <p class="nomodel-detail">${escapeHtml(explanation.detail)}</p>
    </div>

    <div class="nomodel-providers-list">
      ${['local', 'gemini', 'openai'].map((key) => {
    const info = providers[key] || {};
    const isReady = Boolean(info.available);
    const label = info.label || key;
    const sub = key === 'local'
      ? (isReady ? `Running (${info.model || 'ready'})` : 'Not running')
      : (isReady ? `Ready (${info.model || 'connected'})` : 'Not set up');
    return `
          <div class="nomodel-provider-row ${isReady ? 'is-ready' : 'is-off'}">
            <div>
              <div class="nomodel-provider-name">${escapeHtml(label)}</div>
              <div class="nomodel-provider-sub">${escapeHtml(sub)}</div>
            </div>
            ${isReady ? `
              <button type="button" class="btn btn-primary btn-xs" data-nomodel-use="${escapeHtml(key)}">
                Use this
              </button>
            ` : `
              <span class="nomodel-status-pill">Unavailable</span>
            `}
          </div>
        `;
  }).join('')}
    </div>

    ${availableAlternatives.length === 0 ? `
      <p class="nomodel-tip">
        Start Ollama on your computer (<code>ollama serve</code>), then click <strong>Re-check &amp; Scan</strong> below.
      </p>
    ` : ''}
  `;

  bodyEl.querySelectorAll('[data-nomodel-use]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const nextProvider = btn.getAttribute('data-nomodel-use');
      closeNoModelModal();
      if (typeof activeCallbacks.onSwitchProvider === 'function') {
        await activeCallbacks.onSwitchProvider(nextProvider);
      }
      if (typeof activeCallbacks.onRetry === 'function') {
        await activeCallbacks.onRetry(nextProvider);
      }
    });
  });
}

export async function retryVisionModelCheck() {
  const retryBtn = document.getElementById('no-vision-model-retry-btn');
  if (retryBtn) {
    retryBtn.disabled = true;
    retryBtn.textContent = 'Checking…';
  }
  try {
    const meta = await apiGet('/api/v1/photos/vision-providers');
    const providers = meta?.providers || {};
    const readyEntry = Object.entries(providers).find(([, info]) => info && info.available);
    if (readyEntry) {
      const [readyKey] = readyEntry;
      closeNoModelModal();
      showToast(`Connected to ${providers[readyKey]?.label || readyKey}!`);
      if (typeof activeCallbacks.onSwitchProvider === 'function') {
        await activeCallbacks.onSwitchProvider(readyKey);
      }
      if (typeof activeCallbacks.onRetry === 'function') {
        await activeCallbacks.onRetry(readyKey);
      }
    } else {
      renderNoModelModal(meta?.default_provider || 'local', meta);
      showToast('Still no AI scanner available. Make sure Ollama is running.', 'error');
    }
  } catch {
    showToast('Could not check AI scanners.', 'error');
  } finally {
    if (retryBtn) {
      retryBtn.disabled = false;
      retryBtn.textContent = 'Re-check & Scan';
    }
  }
}

export function continueManualFromNoModel() {
  closeNoModelModal();
  if (typeof activeCallbacks.onManual === 'function') {
    activeCallbacks.onManual();
  }
}

/**
 * Checks whether the selected vision provider is currently available.
 * If not, immediately shows the #no-vision-model-modal popup and returns { ok: false }.
 */
export async function ensureVisionModelReady({
  provider = '',
  onSwitchProvider = null,
  onRetry = null,
  onManual = null,
} = {}) {
  let meta = null;
  try {
    meta = await apiGet('/api/v1/photos/vision-providers');
  } catch {
    meta = null;
  }

  const resolvedProvider = provider || meta?.default_provider || 'local';
  const providers = meta && typeof meta.providers === 'object' ? meta.providers : null;
  if (!providers || Object.keys(providers).length === 0) {
    return { ok: true, provider: resolvedProvider, meta };
  }
  const info = providers[resolvedProvider];
  if (info && info.available) {
    return { ok: true, provider: resolvedProvider, meta };
  }

  activeCallbacks = { onSwitchProvider, onRetry, onManual };
  renderNoModelModal(resolvedProvider, meta);
  document.getElementById('no-vision-model-modal')?.classList.add('open');
  return { ok: false, provider: resolvedProvider, meta };
}
