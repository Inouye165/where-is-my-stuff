/**
 * WhereIsMyStuff — Modular ES Entrypoint
 * Zero-Scroll 100dvh Home ('Places') • 'What are you looking for?' Search • 'Needs a home' Spatial Flow
 */
import { apiGet, apiPost, onCrossTabSync } from './api.js';
import { scrollSlotCarousel, syncSlotCarousel } from './carousel.js';
import { initDelegatedEvents, registerActions } from './events.js';
import {
  cancelGridCellInlineEdit,
  handleCompartmentUploadAndScan,
  openOrCreateGridCompartment,
  saveGridCellRename,
  scanCompartmentPhotosWithAi,
  startGridCellQuickAdd,
  startGridCellRename,
  submitGridCellQuickAdd,
  triggerCompartmentUploadAndScan
} from './compartment-actions.js';
import { closeAllModals, closeLightbox, closeModal, closeOnScrim, openLightbox, showToast } from './dom.js';
import { closeScanAbortOnScrim, initUnsavedGuard, resolveScanAbortPrompt, resolveUnsavedPrompt } from './unsaved-guard.js';
import { closeUnitSetupWizard, launchMobileUnitSetup, openExistingUnitCaptureWizard, openUnitSetupWizard } from './unit-setup-wizard.js';
import { closeLabelScanModal, openLabelScanModal } from './label-scan.js';
import { openConvertItemToBoxModal, openMoveBoxModal, submitConvertItemToBox, submitMoveBox } from './box-actions.js';
import { closeNoModelModal, continueManualFromNoModel, retryVisionModelCheck } from './vision-guard.js';
import {
  chooseAddAction,
  handleItemFormSubmit,
  handleItemPhotoPreview,
  handlePrintQrBaseChange,
  handleQuickPhotoSelected,
  handleRapidPhotoSelected,
  handleStorageDelete,
  handleStorageFormSubmit,
  handleStorageParentChange,
  handleStoragePhotoChange,
  handleItemLocationChange,
  toggleItemLocationPicker,
  toggleStorageParentPicker,
  openAddItemModal,
  openAddSheet,
  openAddStorageModal,
  openBulkQrPrintModal,
  openEditStorageModal,
  openMoreModal,
  openPrintQrModal,
  openQuickPhotoModal,
  openRapidEntryModal,
  onVisionProviderChange,
  applyDetectedItemsToText,
  saveDetectedItemsDirect,
  setRapidSaveMode,
  toggleRapidLocationPicker,
  handleRapidLocationChange,
  openRapidManualMode,
  scanExistingLocationPhotos,
  toggleRapidAdvancedOptions,
  openRapidLlmMode,
  closeRapidLlmMode,
  refreshLlmPromptPreview,
  copyLlmInventoryPrompt,
  importLlmJsonToRapidList,
  handleRapidAddPartKeydown,
  handleRapidAddPartPaste,
  addPartToRapidList,
  toggleRapidDetectedItem,
  toggleAllRapidDetectedItems,
  clearRapidAiCrops,
  toggleRapidItemDetails,
  updateRapidDetectedItemField,
  removeRapidDetectedItem,
  toggleRapidItemAsBox,
  runRapidAiScan,
  startRapidPhoneCapture,
  resetPrintQrBaseUrl,
  selectRoomEmoji,
  setStorageTypeChoice,
  submitQuickPhoto,
  submitRapidEntry,
  toggleAutoDrawersUi,
  toggleUseGridUi,
  triggerBrowserPrint,
  triggerRapidPhotoInput,
  triggerRapidPhotoAndScan,
  updateDrawerBatchPreview,
  openGroupIntoBoxModal,
  startBoxPhoneScanFromModal,
  startBoxUploadScanFromModal,
  handleGroupBoxTargetChange,
  handleGroupBoxNameKeydown,
  toggleGroupBoxItem,
  toggleAllGroupBoxItems,
  submitGroupIntoBox
} from './modals.js';
import {
  addDrawerAtEmptyCell,
  applyGridPreset,
  autoNameAllDesignerSlots,
  cancelInlineGridDesigner,
  combineSelectedDesignerCells,
  designerClearSelection,
  fillAllEmptyDesignerCells,
  handleDesignerCellClick,
  handleDesignerDragEnd,
  handleDesignerDragLeave,
  handleDesignerDragOver,
  handleDesignerDragStart,
  handleDesignerDropOnEmpty,
  handleDesignerDropOnSlot,
  handleGridSlotDragEnd,
  handleGridSlotDragLeave,
  handleGridSlotDragOver,
  handleGridSlotDragStart,
  handleGridSlotDropOnEmpty,
  handleGridSlotDropOnSlot,
  openInlineGridDesigner,
  removeSelectedDesignerSlot,
  resizeSelectedDesignerSlot,
  redoGridDesigner,
  saveGridDesigner,
  setDesignerDefaultType,
  splitSelectedDesignerSlot,
  undoGridDesigner,
  toggleDesignerMultiSelect,
  toggleRowSectionBreak,
  updateGridDesignerDimensions,
  updateSelectedDesignerSlotField
} from './grid-designer.js';
import {
  bindDesktopPhotoIntercepts,
  chooseDesktopFileFallback,
  closePhonePhotoModal,
  closePhonePhotoOnScrim,
  finishLocationPhoneCapture,
  setPhoneCaptureSaveMode,
  refreshPhonePhotoQr
} from './phone-capture.js';
import {
  applyPhotoEditor,
  closePhotoEditorModal,
  closePhotoEditorOnScrim,
  flattenPhotoEditorQuad,
  nudgePhotoEditorFineRotation,
  resetPhotoEditorCrop,
  rotatePhotoEditor,
  setPhotoEditorFineRotation,
  setPhotoEditorMode,
  setPhotoEditorScale,
  startPhotoEditorCornerPick
} from './photo-editor.js';
import { compressImageFile } from './photo-compress.js';
import {
  getSavedLastRoute,
  handleGlobalBack,
  handleHashRouting,
  locateDrawerInUnit,
  navToDrawer,
  navToHome,
  navToItem,
  navToNeedsHome,
  navToRoom,
  navToStorageUnit,
  renderCurrentWorkspace,
  restoreLastScroll,
  saveLastScroll,
  switchAppTab
} from './router.js';
import { loadAllData, state } from './state.js';
import {
  cancelLimitedItemRename,
  clearMobileFind,
  deleteTouchDrawerPhoto,
  handleMobileFindInput,
  handleTouchPhotoPicked,
  isMobileOrTabletViewport,
  isPhoneViewport,
  navToTouchCapture,
  removePendingTouchPhoto,
  saveLimitedItemRename,
  saveTouchDrawerPhotos,
  selectTouchDrawer,
  selectTouchRoom,
  selectTouchUnit,
  startLimitedItemRename,
  stepTouchDrawer,
  toggleMobileTheme
} from './mobile-views.js';
import {
  adjustDrawerItemQty,
  adjustItemQty,
  applyGlobalViewOptions,
  applyUnitGridZoom,
  clearAllCatalogFilters,
  clearHomeSearch,
  clearUnitQuickSearch,
  closeUnitMoreMenu,
  deleteContainerFromMenu,
  deleteSpaceFromMenu,
  deleteDrawerPhoto,
  deleteItemAttachment,
  deleteItemFromView,
  editExistingPhoto,
  editItemFromView,
  handleDrawerDirectPhotoUpload,
  handleHomePhotoSearch,
  handleHomeSearchInput,
  handleItemAttachmentUpload,
  handleItemsSearchInput,
  handleSubFilterChange,
  handleUnitMoreKeydown,
  handleUnitQuickSearchInput,
  handleUnitQuickSearchKeydown,
  HOME_SEARCH_HINTS,
  openAddItemWithPrefill,
  openHomeCameraSnap,
  openMoveContainerModal,
  openOverallPhotoViewer,
  openUnitPhotoViewer,
  overallViewerAddPhoto,
  overallViewerCropPhoto,
  overallViewerRemovePhoto,
  overallViewerScanPhoto,
  overallViewerSetCover,
  cancelInlineItemRename,
  deleteDrawerFromMenu,
  dismissQuickAddFeedback,
  quickAddDrawerItems,
  resetGlobalViewOptions,
  saveInlineItemRename,
  setDrawerItemsViewMode,
  setGlobalViewMode,
  startInlineItemRename,
  stepGlobalZoom,
  toggleDrawerPhotosStrip,
  toggleGenericReviewMode,
  toggleGlobalViewMode,
  toggleQuickAddMoreMenu,
  quickMoveContainerToRoom,
  removeDrawerItem,
  saveDrawerLabel,
  saveItemRename,
  scanPhoneForLocation,
  selectCategory,
  selectUnitQuickSearchResult,
  setUnitGridZoom,
  setHomePage,
  stepOverallPhotoViewer,
  toggleOverallPhotoEditMode,
  skipNeedsHomeContainer,
  submitMoveContainer,
  toggleDrawerLabelEdit,
  toggleItemPartsEditor,
  toggleItemRenameEdit,
  toggleUnitMoreMenu,
  triggerDrawerDirectPhotoUpload,
  triggerItemAttachmentUpload,
  unitViewerCropPhoto,
  unitViewerRemovePhoto,
  unitViewerReplacePhoto,
  updateItemPartField,
  addItemPartToEditor,
  removeItemPartFromEditor,
  saveItemPartsList,
  expandSubBox,
  toggleExpandSubBox,
  toggleDrawerMoreMenu,
  toggleItemRowMenu,
  toggleBoxRowMenu,
  verifyItemFromView,
  findAndImportItemPhoto,
  openBoxCoverPhotoPicker,
  setLocationCoverPhoto,
  updateItemPdfDraftTitle,
  toggleItemPdfRemoveSource,
  toggleItemPdfPhotoSelection,
  selectAllItemPdfPhotos,
  setItemMainPhotoFromSaved,
  convertSelectedItemPhotosToPdf,
  toggleItemPhotosMoreMenu,
  toggleItemPdfSelectMode,
  openAttachmentViewer,
  toggleAttachmentViewerFullscreen,
  closeAttachmentViewerModal,
  overallViewerScanAsBox,
  toggleDrawerPhotoBoxMode,
  toggleDrawerPhotoBoxSelection,
  selectAllDrawerBoxPhotos,
  updateDrawerPhotoBoxDraft,
  scanSelectedPhotosAsBox,
  createEmptyBoxFromSelectedPhotos,
  detachLocationFromSpace
} from './views.js';

function resolveEffectiveTheme(pref) {
  if (pref === 'light' || pref === 'dark') return pref;
  if (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches) {
    return 'light';
  }
  return 'dark';
}

export function initTheme() {
  const effective = resolveEffectiveTheme(state.themePref);
  document.documentElement.setAttribute('data-theme', effective);

  const sysBtn = document.getElementById('theme-btn-system');
  const lightBtn = document.getElementById('theme-btn-light');
  const darkBtn = document.getElementById('theme-btn-dark');
  if (sysBtn) sysBtn.classList.toggle('active', state.themePref === 'system');
  if (lightBtn) lightBtn.classList.toggle('active', state.themePref === 'light');
  if (darkBtn) darkBtn.classList.toggle('active', state.themePref === 'dark');

  if (window.matchMedia && !state._mediaListenerBound) {
    state._mediaListenerBound = true;
    const mq = window.matchMedia('(prefers-color-scheme: light)');
    const onChange = () => {
      if (state.themePref === 'system') {
        document.documentElement.setAttribute('data-theme', resolveEffectiveTheme('system'));
      }
    };
    if (typeof mq.addEventListener === 'function') {
      mq.addEventListener('change', onChange);
    }
  }
}

export function setAppTheme(pref) {
  state.themePref = pref;
  try {
    localStorage.setItem('wims_theme_pref', pref);
  } catch { }
  initTheme();
}

function isInputFocused() {
  const el = document.activeElement;
  return Boolean(el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT'));
}

function initKeyboardAndRouter() {
  window.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      const lightbox = document.getElementById('lightbox-modal');
      if (lightbox && lightbox.classList.contains('open')) {
        closeLightbox();
        return;
      }
      const scanAbortModal = document.getElementById('scan-abort-confirm-modal');
      if (scanAbortModal && scanAbortModal.classList.contains('open')) {
        resolveScanAbortPrompt('continue');
        return;
      }
      const unsavedModal = document.getElementById('unsaved-confirm-modal');
      if (unsavedModal && unsavedModal.classList.contains('open')) {
        resolveUnsavedPrompt('cancel');
        return;
      }
      const editorModal = document.getElementById('photo-editor-modal');
      if (editorModal && editorModal.classList.contains('open')) {
        closePhotoEditorModal();
        return;
      }
      const phoneModal = document.getElementById('phone-photo-modal');
      if (phoneModal && phoneModal.classList.contains('open')) {
        closePhonePhotoModal();
        return;
      }
      const openScrims = Array.from(document.querySelectorAll('.modal-scrim.open'));
      if (openScrims.length > 0) {
        const topScrim = openScrims[openScrims.length - 1];
        if (topScrim && topScrim.id) {
          closeModal(topScrim.id);
        } else {
          closeAllModals();
        }
        return;
      }
      if (state.activeTab === 'places' && state.currentView === 'home' && state.homeSearchQuery) {
        clearHomeSearch();
        return;
      }
    }

    if ((e.ctrlKey || e.metaKey) && !isInputFocused() && state.currentView === 'unit') {
      const k = e.key.toLowerCase();
      if (k === 'z' && !e.shiftKey) {
        e.preventDefault();
        undoGridDesigner();
        return;
      }
      if (k === 'y' || (k === 'z' && e.shiftKey)) {
        e.preventDefault();
        redoGridDesigner();
        return;
      }
    }

    if ((e.key === '/' || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k')) && !isInputFocused()) {
      e.preventDefault();
      const unitQuickInput = document.getElementById('unit-quick-search-input');
      const homeInput = document.getElementById('home-search-input');
      const itemsInput = document.getElementById('items-search-input');
      if (unitQuickInput) {
        unitQuickInput.focus();
        unitQuickInput.select();
      } else if (homeInput) {
        homeInput.focus();
        homeInput.select();
      } else if (itemsInput) {
        itemsInput.focus();
        itemsInput.select();
      } else {
        switchAppTab('places');
        setTimeout(() => document.getElementById('home-search-input')?.focus(), 40);
      }
    }
  });

  let routeSyncTimer = null;
  const scheduleHashRouting = () => {
    if (routeSyncTimer !== null) return;
    routeSyncTimer = setTimeout(() => {
      routeSyncTimer = null;
      handleHashRouting();
    }, 0);
  };
  window.addEventListener('hashchange', scheduleHashRouting);
  window.addEventListener('popstate', scheduleHashRouting);

  let lastBucket = `${isMobileOrTabletViewport()}:${isPhoneViewport()}`;
  let resizeTimer = null;
  window.addEventListener('resize', () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(() => {
      const nextBucket = `${isMobileOrTabletViewport()}:${isPhoneViewport()}`;
      if (nextBucket !== lastBucket) {
        lastBucket = nextBucket;
        if (!isInputFocused()) {
          renderCurrentWorkspace();
        }
      } else if (state.globalView?.mode === 'fit-all' || state.globalView?.mode === 'fit-width' || (state.currentView === 'unit' && state.currentLocationId && state.unitGridZoom?.[state.currentLocationId] === 'fit')) {
        applyGlobalViewOptions();
      }
    }, 120);
  });
}

let deferredInstallPrompt = null;
let initialRouteRestored = false;

export function openMobileSearch() {
  switchAppTab('places');
  setTimeout(() => {
    const input = document.getElementById('mobile-find-input') || document.getElementById('home-search-input');
    if (input) {
      input.focus();
      input.select();
    }
  }, 50);
}

export async function triggerPwaInstall() {
  if (!deferredInstallPrompt) return;
  deferredInstallPrompt.prompt();
  try {
    await deferredInstallPrompt.userChoice;
  } catch { }
  deferredInstallPrompt = null;
  const btn = document.getElementById('more-install-app-btn');
  if (btn) btn.style.display = 'none';
}

function syncFullscreenButton() {
  const btn = document.getElementById('more-fullscreen-btn');
  if (!btn) return;
  btn.textContent = document.fullscreenElement ? 'Exit full screen' : 'Full screen';
}

export async function toggleFullScreenMode() {
  try {
    if (!document.fullscreenElement) {
      await document.documentElement.requestFullscreen();
    } else {
      await document.exitFullscreen();
    }
  } catch { }
  syncFullscreenButton();
}

export function openAppPopupWindow() {
  const w = Math.min(1280, Math.max(900, Math.round(window.screen.availWidth * 0.82)));
  const h = Math.min(860, Math.max(640, Math.round(window.screen.availHeight * 0.85)));
  const left = Math.round((window.screen.availWidth - w) / 2);
  const top = Math.round((window.screen.availHeight - h) / 2);
  const win = window.open(
    window.location.href,
    'WhereIsMyStuffApp',
    `popup=yes,width=${w},height=${h},left=${left},top=${top},resizable=yes`
  );
  if (win) {
    win.focus();
    closeModal('more-modal');
  }
}

function initPwaAndWindowModes() {
  if (typeof window === 'undefined') return;

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => { });
  }

  const isStandalone =
    (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
    window.navigator?.standalone === true;

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredInstallPrompt = e;
    const btn = document.getElementById('more-install-app-btn');
    if (btn && !isStandalone) btn.style.display = 'inline-flex';
  });

  window.addEventListener('appinstalled', () => {
    deferredInstallPrompt = null;
    const btn = document.getElementById('more-install-app-btn');
    if (btn) btn.style.display = 'none';
  });

  document.addEventListener('fullscreenchange', syncFullscreenButton);
  syncFullscreenButton();

  let scrollSaveTimer = null;
  document.addEventListener(
    'scroll',
    (e) => {
      const target = e.target;
      if (target && (target.id === 'unit-scroll-region' || target.id === 'app-workspace')) {
        clearTimeout(scrollSaveTimer);
        scrollSaveTimer = setTimeout(() => saveLastScroll(target.scrollTop), 150);
      }
    },
    true
  );
}

state.onWorkspaceRefresh = () => {
  if (!initialRouteRestored) {
    initialRouteRestored = true;
    const currentHash = window.location.hash;
    if (!currentHash || currentHash === '#' || currentHash === '#/') {
      const saved = getSavedLastRoute();
      if (saved && saved !== '#' && saved !== '#/') {
        history.replaceState(null, '', saved);
        handleHashRouting();
        restoreLastScroll();
        return;
      }
    }
  }

  if (window.location.hash && window.location.hash !== '#' && window.location.hash !== '#/') {
    handleHashRouting();
  } else {
    renderCurrentWorkspace();
  }
};

function startHintRotation() {
  if (typeof window === 'undefined') return;
  const prefersReduced = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  if (prefersReduced) return;

  setInterval(() => {
    if (state.activeTab !== 'places' || state.currentView !== 'home' || state.homeSearchQuery) return;
    state.homeSearchHintIndex = (state.homeSearchHintIndex + 1) % HOME_SEARCH_HINTS.length;
    const sub = document.getElementById('home-search-subtext');
    if (sub) sub.textContent = HOME_SEARCH_HINTS[state.homeSearchHintIndex];
  }, 4200);
}

function showPinUnlockModal() {
  if (typeof document === 'undefined' || document.getElementById('wims-pin-unlock-overlay')) {
    return;
  }
  const overlay = document.createElement('div');
  overlay.id = 'wims-pin-unlock-overlay';
  overlay.style.cssText = [
    'position:fixed', 'inset:0', 'z-index:9999', 'display:flex',
    'align-items:center', 'justify-content:center', 'padding:16px',
    'background:rgba(15,23,42,0.82)', 'backdrop-filter:blur(4px)'
  ].join(';');

  const card = document.createElement('form');
  card.style.cssText = [
    'width:100%', 'max-width:340px', 'padding:20px', 'border-radius:14px',
    'background:var(--surface-1, #1e293b)', 'color:var(--text-1, #f8fafc)',
    'border:1px solid rgba(148,163,184,0.25)', 'box-shadow:0 20px 40px rgba(0,0,0,0.45)'
  ].join(';');

  const title = document.createElement('h2');
  title.textContent = 'Enter Access PIN';
  title.style.cssText = 'margin:0 0 8px 0;font-size:1.1rem;font-weight:700;';

  const sub = document.createElement('p');
  sub.textContent = 'This WIMS inventory is PIN-protected on the network.';
  sub.style.cssText = 'margin:0 0 14px 0;font-size:0.875rem;opacity:0.8;';

  const input = document.createElement('input');
  input.type = 'password';
  input.autocomplete = 'current-password';
  input.placeholder = 'Access PIN';
  input.required = true;
  input.style.cssText = [
    'width:100%', 'box-sizing:border-box', 'padding:10px 12px', 'border-radius:8px',
    'border:1px solid rgba(148,163,184,0.35)', 'background:rgba(15,23,42,0.55)',
    'color:inherit', 'font-size:1rem', 'margin-bottom:10px'
  ].join(';');

  const errEl = document.createElement('div');
  errEl.style.cssText = 'min-height:18px;font-size:0.82rem;color:#f87171;margin-bottom:10px;';

  const submitBtn = document.createElement('button');
  submitBtn.type = 'submit';
  submitBtn.textContent = 'Unlock';
  submitBtn.style.cssText = [
    'width:100%', 'padding:10px 14px', 'border:0', 'border-radius:8px',
    'background:#0284c7', 'color:#fff', 'font-weight:600', 'cursor:pointer'
  ].join(';');

  card.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    errEl.textContent = '';
    submitBtn.disabled = true;
    try {
      const resp = await fetch('/api/v1/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin: input.value })
      });
      if (!resp.ok) {
        const body = await resp.json().catch(() => null);
        errEl.textContent = body?.error?.message || 'Invalid PIN.';
        submitBtn.disabled = false;
        input.focus();
        return;
      }
      overlay.remove();
      loadAllData();
    } catch {
      errEl.textContent = 'Could not reach server.';
      submitBtn.disabled = false;
    }
  });

  card.append(title, sub, input, errEl, submitBtn);
  overlay.appendChild(card);
  document.body.appendChild(overlay);
  input.focus();
}

async function refreshBackupStatusUi() {
  if (typeof document === 'undefined') return;
  const statusEl = document.getElementById('more-backup-status');
  const dlLink = document.getElementById('more-backup-download-link');
  if (!statusEl) return;

  try {
    const data = await apiGet('/api/v1/system/backups', 'Could not load backup status.');
    const autoText = data.auto_backup_enabled
      ? `Auto-backup: every ${data.interval_hours}h (keeps last ${data.retention_count})`
      : 'Auto-backup: off';
    if (data.latest_backup) {
      const mb = (data.latest_backup.size_bytes / (1024 * 1024)).toFixed(2);
      const when = new Date(data.latest_backup.created_at).toLocaleString();
      statusEl.textContent = `${autoText} • Last: ${when} (${mb} MB)`;
      if (dlLink) {
        dlLink.href = `/api/v1/system/backups/${encodeURIComponent(data.latest_backup.filename)}`;
        dlLink.textContent = `Download ${data.latest_backup.filename} →`;
        dlLink.style.display = 'inline-block';
      }
    } else {
      statusEl.textContent = `${autoText} • No backups yet`;
      if (dlLink) dlLink.style.display = 'none';
    }
  } catch {
    // Non-fatal if server is temporarily unreachable
  }
}

async function triggerBackupNow() {
  const btn = document.getElementById('more-backup-btn');
  const titleEl = document.getElementById('more-backup-btn-title');
  if (btn && btn.disabled) return;
  if (btn) btn.disabled = true;
  if (titleEl) titleEl.textContent = 'Backing up...';

  try {
    const res = await apiPost('/api/v1/system/backup?include_uploads=true', undefined, 'Backup failed.', {
      timeoutMs: 120000
    });
    const mb = (res.size_bytes / (1024 * 1024)).toFixed(2);
    showToast(`Backup saved: ${res.filename} (${mb} MB)`);
    await refreshBackupStatusUi();
  } catch (err) {
    showToast(err?.message || 'Backup failed.', 'error');
  } finally {
    if (btn) btn.disabled = false;
    if (titleEl) titleEl.textContent = 'Back Up Inventory Now';
  }
}

function bootstrapApp() {
  initTheme();
  registerActions({ triggerBackupNow });
  initDelegatedEvents();
  initKeyboardAndRouter();
  initPwaAndWindowModes();
  bindDesktopPhotoIntercepts();
  initUnsavedGuard();
  if (typeof window !== 'undefined') {
    window.addEventListener('wims:auth-required', showPinUnlockModal);
    window.addEventListener('wims:more-opened', () => {
      void refreshBackupStatusUi();
    });
  }
  onCrossTabSync(() => {
    loadAllData();
  });
  startHintRotation();
  loadAllData();
}

if (typeof document !== 'undefined') {
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrapApp);
  } else {
    bootstrapApp();
  }
}

// Explicit window bindings for inline DOM handlers in index.html
if (typeof window !== 'undefined') {
  window.syncThemeButtons = initTheme;
  Object.assign(window, {
    state: state,
    navToHome: navToHome,
    openAddSheet: openAddSheet,
    chooseAddAction: chooseAddAction,
    switchAppTab: switchAppTab,
    openMoreModal: openMoreModal,
    navToItem: navToItem,
    editItemFromView: editItemFromView,
    deleteItemFromView: deleteItemFromView,
    verifyItemFromView: verifyItemFromView,
    closeOnScrim: closeOnScrim,
    closeModal: closeModal,
    resolveUnsavedPrompt: resolveUnsavedPrompt,
    resolveScanAbortPrompt: resolveScanAbortPrompt,
    closeScanAbortOnScrim: closeScanAbortOnScrim,
    handleItemFormSubmit: handleItemFormSubmit,
    handleItemPhotoPreview: handleItemPhotoPreview,
    openAddItemModal: openAddItemModal,
    openAddItemWithPrefill: openAddItemWithPrefill,
    openAddStorageModal: openAddStorageModal,
    openUnitSetupWizard: openUnitSetupWizard,
    openExistingUnitCaptureWizard: openExistingUnitCaptureWizard,
    closeUnitSetupWizard: closeUnitSetupWizard,
    launchMobileUnitSetup: launchMobileUnitSetup,
    openLabelScanModal: openLabelScanModal,
    closeLabelScanModal: closeLabelScanModal,
    closeNoModelModal: closeNoModelModal,
    retryVisionModelCheck: retryVisionModelCheck,
    continueManualFromNoModel: continueManualFromNoModel,
    openEditStorageModal: openEditStorageModal,
    handleStorageFormSubmit: handleStorageFormSubmit,
    setStorageTypeChoice: setStorageTypeChoice,
    selectRoomEmoji: selectRoomEmoji,
    handleStorageParentChange: handleStorageParentChange,
    handleItemLocationChange: handleItemLocationChange,
    toggleItemLocationPicker: toggleItemLocationPicker,
    toggleStorageParentPicker: toggleStorageParentPicker,
    toggleAutoDrawersUi: toggleAutoDrawersUi,
    toggleUseGridUi: toggleUseGridUi,
    openInlineGridDesigner: openInlineGridDesigner,
    cancelInlineGridDesigner: cancelInlineGridDesigner,
    renderCurrentWorkspace: renderCurrentWorkspace,
    updateGridDesignerDimensions: updateGridDesignerDimensions,
    applyGridPreset: applyGridPreset,
    toggleDesignerMultiSelect: toggleDesignerMultiSelect,
    toggleRowSectionBreak: toggleRowSectionBreak,
    handleDesignerCellClick: handleDesignerCellClick,
    handleDesignerDragStart: handleDesignerDragStart,
    handleDesignerDragOver: handleDesignerDragOver,
    handleDesignerDragLeave: handleDesignerDragLeave,
    handleDesignerDragEnd: handleDesignerDragEnd,
    handleDesignerDropOnSlot: handleDesignerDropOnSlot,
    handleDesignerDropOnEmpty: handleDesignerDropOnEmpty,
    handleGridSlotDragStart: handleGridSlotDragStart,
    handleGridSlotDragOver: handleGridSlotDragOver,
    handleGridSlotDragLeave: handleGridSlotDragLeave,
    handleGridSlotDragEnd: handleGridSlotDragEnd,
    handleGridSlotDropOnSlot: handleGridSlotDropOnSlot,
    handleGridSlotDropOnEmpty: handleGridSlotDropOnEmpty,
    fillAllEmptyDesignerCells: fillAllEmptyDesignerCells,
    autoNameAllDesignerSlots: autoNameAllDesignerSlots,
    setDesignerDefaultType: setDesignerDefaultType,
    addDrawerAtEmptyCell: addDrawerAtEmptyCell,
    combineSelectedDesignerCells: combineSelectedDesignerCells,
    resizeSelectedDesignerSlot: resizeSelectedDesignerSlot,
    splitSelectedDesignerSlot: splitSelectedDesignerSlot,
    removeSelectedDesignerSlot: removeSelectedDesignerSlot,
    updateSelectedDesignerSlotField: updateSelectedDesignerSlotField,
    designerClearSelection: designerClearSelection,
    saveGridDesigner: saveGridDesigner,
    undoGridDesigner: undoGridDesigner,
    redoGridDesigner: redoGridDesigner,
    updateDrawerBatchPreview: updateDrawerBatchPreview,
    handleStoragePhotoChange: handleStoragePhotoChange,
    handleStorageDelete: handleStorageDelete,
    openRapidEntryModal: openRapidEntryModal,
    onVisionProviderChange: onVisionProviderChange,
    applyDetectedItemsToText: applyDetectedItemsToText,
    saveDetectedItemsDirect: saveDetectedItemsDirect,
    setRapidSaveMode: setRapidSaveMode,
    toggleRapidLocationPicker: toggleRapidLocationPicker,
    handleRapidLocationChange: handleRapidLocationChange,
    openRapidManualMode: openRapidManualMode,
    scanExistingLocationPhotos: scanExistingLocationPhotos,
    toggleRapidAdvancedOptions: toggleRapidAdvancedOptions,
    openRapidLlmMode: openRapidLlmMode,
    closeRapidLlmMode: closeRapidLlmMode,
    refreshLlmPromptPreview: refreshLlmPromptPreview,
    copyLlmInventoryPrompt: copyLlmInventoryPrompt,
    importLlmJsonToRapidList: importLlmJsonToRapidList,
    handleRapidAddPartKeydown: handleRapidAddPartKeydown,
    handleRapidAddPartPaste: handleRapidAddPartPaste,
    addPartToRapidList: addPartToRapidList,
    toggleRapidDetectedItem: toggleRapidDetectedItem,
    toggleAllRapidDetectedItems: toggleAllRapidDetectedItems,
    clearRapidAiCrops: clearRapidAiCrops,
    toggleRapidItemDetails: toggleRapidItemDetails,
    updateRapidDetectedItemField: updateRapidDetectedItemField,
    removeRapidDetectedItem: removeRapidDetectedItem,
    toggleRapidItemAsBox: toggleRapidItemAsBox,
    openConvertItemToBoxModal: openConvertItemToBoxModal,
    submitConvertItemToBox: submitConvertItemToBox,
    openMoveBoxModal: openMoveBoxModal,
    submitMoveBox: submitMoveBox,
    runRapidAiScan: runRapidAiScan,
    startRapidPhoneCapture: startRapidPhoneCapture,
    triggerRapidPhotoInput: triggerRapidPhotoInput,
    triggerRapidPhotoAndScan: triggerRapidPhotoAndScan,
    handleRapidPhotoSelected: handleRapidPhotoSelected,
    submitRapidEntry: submitRapidEntry,
    finishLocationPhoneCapture: finishLocationPhoneCapture,
    setPhoneCaptureSaveMode: setPhoneCaptureSaveMode,
    toggleDrawerLabelEdit: toggleDrawerLabelEdit,
    saveDrawerLabel: saveDrawerLabel,
    scanPhoneForLocation: scanPhoneForLocation,
    triggerDrawerDirectPhotoUpload: triggerDrawerDirectPhotoUpload,
    handleDrawerDirectPhotoUpload: handleDrawerDirectPhotoUpload,
    deleteDrawerPhoto: deleteDrawerPhoto,
    triggerItemAttachmentUpload: triggerItemAttachmentUpload,
    handleItemAttachmentUpload: handleItemAttachmentUpload,
    deleteItemAttachment: deleteItemAttachment,
    cancelInlineItemRename: cancelInlineItemRename,
    deleteDrawerFromMenu: deleteDrawerFromMenu,
    dismissQuickAddFeedback: dismissQuickAddFeedback,
    quickAddDrawerItems: quickAddDrawerItems,
    saveInlineItemRename: saveInlineItemRename,
    setDrawerItemsViewMode: setDrawerItemsViewMode,
    startInlineItemRename: startInlineItemRename,
    toggleDrawerPhotosStrip: toggleDrawerPhotosStrip,
    toggleGenericReviewMode: toggleGenericReviewMode,
    toggleQuickAddMoreMenu: toggleQuickAddMoreMenu,
    adjustDrawerItemQty: adjustDrawerItemQty,
    removeDrawerItem: removeDrawerItem,
    openQuickPhotoModal: openQuickPhotoModal,
    handleQuickPhotoSelected: handleQuickPhotoSelected,
    submitQuickPhoto: submitQuickPhoto,
    openPrintQrModal: openPrintQrModal,
    openBulkQrPrintModal: openBulkQrPrintModal,
    handlePrintQrBaseChange: handlePrintQrBaseChange,
    resetPrintQrBaseUrl: resetPrintQrBaseUrl,
    triggerBrowserPrint: triggerBrowserPrint,
    setAppTheme: setAppTheme,
    openLightbox: openLightbox,
    closeLightbox: closeLightbox,
    handleGlobalBack: handleGlobalBack,
    handleHomeSearchInput: handleHomeSearchInput,
    handleHomePhotoSearch: handleHomePhotoSearch,
    clearHomeSearch: clearHomeSearch,
    handleUnitQuickSearchInput: handleUnitQuickSearchInput,
    handleUnitQuickSearchKeydown: handleUnitQuickSearchKeydown,
    clearUnitQuickSearch: clearUnitQuickSearch,
    selectUnitQuickSearchResult: selectUnitQuickSearchResult,
    setUnitGridZoom: setUnitGridZoom,
    applyGlobalViewOptions: applyGlobalViewOptions,
    stepGlobalZoom: stepGlobalZoom,
    setGlobalViewMode: setGlobalViewMode,
    toggleGlobalViewMode: toggleGlobalViewMode,
    resetGlobalViewOptions: resetGlobalViewOptions,
    openHomeCameraSnap: openHomeCameraSnap,
    setHomePage: setHomePage,
    skipNeedsHomeContainer: skipNeedsHomeContainer,
    navToNeedsHome: navToNeedsHome,
    navToRoom: navToRoom,
    navToStorageUnit: navToStorageUnit,
    navToDrawer: navToDrawer,
    scrollSlotCarousel: scrollSlotCarousel,
    syncSlotCarousel: syncSlotCarousel,
    locateDrawerInUnit: locateDrawerInUnit,
    quickMoveContainerToRoom: quickMoveContainerToRoom,
    handleItemsSearchInput: handleItemsSearchInput,
    selectCategory: selectCategory,
    handleSubFilterChange: handleSubFilterChange,
    clearAllCatalogFilters: clearAllCatalogFilters,
    adjustItemQty: adjustItemQty,
    compressImageFile: compressImageFile,
    closePhonePhotoModal: closePhonePhotoModal,
    closePhonePhotoOnScrim: closePhonePhotoOnScrim,
    chooseDesktopFileFallback: chooseDesktopFileFallback,
    refreshPhonePhotoQr: refreshPhonePhotoQr,
    editExistingPhoto: editExistingPhoto,
    rotatePhotoEditor: rotatePhotoEditor,
    setPhotoEditorMode: setPhotoEditorMode,
    startPhotoEditorCornerPick: startPhotoEditorCornerPick,
    flattenPhotoEditorQuad: flattenPhotoEditorQuad,
    setPhotoEditorFineRotation: setPhotoEditorFineRotation,
    nudgePhotoEditorFineRotation: nudgePhotoEditorFineRotation,
    setPhotoEditorScale: setPhotoEditorScale,
    resetPhotoEditorCrop: resetPhotoEditorCrop,
    closePhotoEditorModal: closePhotoEditorModal,
    closePhotoEditorOnScrim: closePhotoEditorOnScrim,
    applyPhotoEditor: applyPhotoEditor,
    toggleUnitMoreMenu: toggleUnitMoreMenu,
    closeUnitMoreMenu: closeUnitMoreMenu,
    handleUnitMoreKeydown: handleUnitMoreKeydown,
    openUnitPhotoViewer: openUnitPhotoViewer,
    openOverallPhotoViewer: openOverallPhotoViewer,
    stepOverallPhotoViewer: stepOverallPhotoViewer,
    toggleOverallPhotoEditMode: toggleOverallPhotoEditMode,
    overallViewerAddPhoto: overallViewerAddPhoto,
    overallViewerCropPhoto: overallViewerCropPhoto,
    overallViewerSetCover: overallViewerSetCover,
    overallViewerRemovePhoto: overallViewerRemovePhoto,
    unitViewerReplacePhoto: unitViewerReplacePhoto,
    unitViewerCropPhoto: unitViewerCropPhoto,
    unitViewerRemovePhoto: unitViewerRemovePhoto,
    openMoveContainerModal: openMoveContainerModal,
    submitMoveContainer: submitMoveContainer,
    deleteContainerFromMenu: deleteContainerFromMenu,
    deleteSpaceFromMenu: deleteSpaceFromMenu,
    openMobileSearch: openMobileSearch,
    triggerPwaInstall: triggerPwaInstall,
    toggleFullScreenMode: toggleFullScreenMode,
    openAppPopupWindow: openAppPopupWindow,
    navToTouchCapture: navToTouchCapture,
    selectTouchRoom: selectTouchRoom,
    selectTouchUnit: selectTouchUnit,
    selectTouchDrawer: selectTouchDrawer,
    stepTouchDrawer: stepTouchDrawer,
    handleTouchPhotoPicked: handleTouchPhotoPicked,
    removePendingTouchPhoto: removePendingTouchPhoto,
    saveTouchDrawerPhotos: saveTouchDrawerPhotos,
    deleteTouchDrawerPhoto: deleteTouchDrawerPhoto,
    startLimitedItemRename: startLimitedItemRename,
    cancelLimitedItemRename: cancelLimitedItemRename,
    saveLimitedItemRename: saveLimitedItemRename,
    handleMobileFindInput: handleMobileFindInput,
    clearMobileFind: clearMobileFind,
    toggleMobileTheme: toggleMobileTheme,
    toggleItemRenameEdit: toggleItemRenameEdit,
    saveItemRename: saveItemRename,
    toggleItemPartsEditor: toggleItemPartsEditor,
    updateItemPartField: updateItemPartField,
    addItemPartToEditor: addItemPartToEditor,
    removeItemPartFromEditor: removeItemPartFromEditor,
    saveItemPartsList: saveItemPartsList,
    openGroupIntoBoxModal: openGroupIntoBoxModal,
    startBoxPhoneScanFromModal: startBoxPhoneScanFromModal,
    startBoxUploadScanFromModal: startBoxUploadScanFromModal,
    handleGroupBoxTargetChange: handleGroupBoxTargetChange,
    handleGroupBoxNameKeydown: handleGroupBoxNameKeydown,
    toggleGroupBoxItem: toggleGroupBoxItem,
    toggleAllGroupBoxItems: toggleAllGroupBoxItems,
    submitGroupIntoBox: submitGroupIntoBox,
    expandSubBox: expandSubBox,
    toggleExpandSubBox: toggleExpandSubBox,
    toggleDrawerMoreMenu: toggleDrawerMoreMenu,
    toggleItemRowMenu: toggleItemRowMenu,
    toggleBoxRowMenu: toggleBoxRowMenu,
    findAndImportItemPhoto: findAndImportItemPhoto,
    openBoxCoverPhotoPicker: openBoxCoverPhotoPicker,
    setLocationCoverPhoto: setLocationCoverPhoto,
    openOrCreateGridCompartment: openOrCreateGridCompartment,
    startGridCellRename: startGridCellRename,
    startGridCellQuickAdd: startGridCellQuickAdd,
    cancelGridCellInlineEdit: cancelGridCellInlineEdit,
    saveGridCellRename: saveGridCellRename,
    submitGridCellQuickAdd: submitGridCellQuickAdd,
    scanCompartmentPhotosWithAi: scanCompartmentPhotosWithAi,
    triggerCompartmentUploadAndScan: triggerCompartmentUploadAndScan,
    handleCompartmentUploadAndScan: handleCompartmentUploadAndScan,
    overallViewerScanPhoto: overallViewerScanPhoto,
    updateItemPdfDraftTitle: updateItemPdfDraftTitle,
    toggleItemPdfRemoveSource: toggleItemPdfRemoveSource,
    toggleItemPdfPhotoSelection: toggleItemPdfPhotoSelection,
    selectAllItemPdfPhotos: selectAllItemPdfPhotos,
    setItemMainPhotoFromSaved: setItemMainPhotoFromSaved,
    convertSelectedItemPhotosToPdf: convertSelectedItemPhotosToPdf,
    toggleItemPhotosMoreMenu: toggleItemPhotosMoreMenu,
    toggleItemPdfSelectMode: toggleItemPdfSelectMode,
    openAttachmentViewer: openAttachmentViewer,
    toggleAttachmentViewerFullscreen: toggleAttachmentViewerFullscreen,
    closeAttachmentViewerModal: closeAttachmentViewerModal,
    overallViewerScanAsBox: overallViewerScanAsBox,
    toggleDrawerPhotoBoxMode: toggleDrawerPhotoBoxMode,
    toggleDrawerPhotoBoxSelection: toggleDrawerPhotoBoxSelection,
    selectAllDrawerBoxPhotos: selectAllDrawerBoxPhotos,
    updateDrawerPhotoBoxDraft: updateDrawerPhotoBoxDraft,
    scanSelectedPhotosAsBox: scanSelectedPhotosAsBox,
    createEmptyBoxFromSelectedPhotos: createEmptyBoxFromSelectedPhotos,
    detachLocationFromSpace: detachLocationFromSpace
  });
}
