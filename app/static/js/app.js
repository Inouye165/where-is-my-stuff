/**
 * WhereIsMyStuff — Modular ES Entrypoint
 * Zero-Scroll 100dvh Home ('Places') • Native Rail & Thumb Bar • 'Needs a home' Spatial Flow
 */
import { onCrossTabSync } from './api.js';
import { closeAllModals, closeLightbox, closeModal, closeOnScrim, openLightbox } from './dom.js';
import { initUnsavedGuard, resolveUnsavedPrompt } from './unsaved-guard.js';
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
  openAddItemModal,
  openAddSheet,
  openAddStorageModal,
  openBulkQrPrintModal,
  openEditStorageModal,
  openMoreModal,
  openPrintQrModal,
  openQuickPhotoModal,
  openRapidEntryModal,
  resetPrintQrBaseUrl,
  selectRoomEmoji,
  setStorageTypeChoice,
  submitQuickPhoto,
  submitRapidEntry,
  toggleAutoDrawersUi,
  toggleUseGridUi,
  triggerBrowserPrint,
  triggerRapidPhotoInput,
  updateDrawerBatchPreview
} from './modals.js';
import {
  addDrawerAtEmptyCell,
  applyGridPreset,
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
  openGridDesignerModal,
  openInlineGridDesigner,
  removeSelectedDesignerSlot,
  resizeSelectedDesignerSlot,
  saveGridDesigner,
  splitSelectedDesignerSlot,
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
  refreshPhonePhotoQr
} from './phone-capture.js';
import {
  applyPhotoEditor,
  closePhotoEditorModal,
  closePhotoEditorOnScrim,
  resetPhotoEditorCrop,
  rotatePhotoEditor
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
import { loadAllData, state, UNASSIGNED_ROOM_LABEL } from './state.js';
import {
  adjustItemQty,
  clearAllCatalogFilters,
  clearHomeSearch,
  closeUnitMoreMenu,
  deleteContainerFromMenu,
  deleteItemFromDrawer,
  deleteItemFromView,
  dismissStarterRoom,
  editExistingPhoto,
  editItemFromDrawer,
  editItemFromView,
  focusDrawerFromViewer,
  handleHomePhotoSearch,
  handleHomeSearchInput,
  handleItemsSearchInput,
  handleSubFilterChange,
  handleUnitMoreKeydown,
  HOME_SEARCH_HINTS,
  openAddItemWithPrefill,
  openHomeCameraSnap,
  openItemDrawer,
  openMoveContainerModal,
  openStarterRoom,
  openUnitPhotoViewer,
  quickMoveContainerToRoom,
  selectCategory,
  setHomePage,
  skipNeedsHomeContainer,
  submitMoveContainer,
  toggleUnitMoreMenu,
  unitViewerCropPhoto,
  unitViewerRemovePhoto,
  unitViewerReplacePhoto,
  verifyItemFromDrawer,
  verifyItemFromView,
  closeSlideDrawer
} from './views.js';

// Keep explicit label & placeholder exports for Home Places view
export const HOME_UNASSIGNED_TILE_TITLE = UNASSIGNED_ROOM_LABEL || 'Needs a home';
export const HOME_SEARCH_PROMPT = 'What are you looking for?';

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
  } catch {}
  initTheme();
}

function isInputFocused() {
  const el = document.activeElement;
  return Boolean(el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.tagName === 'SELECT'));
}

function initKeyboardAndRouter() {
  window.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
      const openScrims = document.querySelectorAll('.modal-scrim.open, .lightbox-overlay.open');
      if (openScrims.length > 0) {
        closeAllModals();
        return;
      }
      if (state.activeDrawerItem) {
        closeSlideDrawer();
        return;
      }
      if (state.activeTab === 'places' && state.currentView === 'home' && state.homeSearchQuery) {
        clearHomeSearch();
        return;
      }
    }

    if ((e.key === '/' || ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k')) && !isInputFocused()) {
      e.preventDefault();
      const homeInput = document.getElementById('home-search-input');
      const itemsInput = document.getElementById('items-search-input');
      if (homeInput) {
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

  window.addEventListener('hashchange', handleHashRouting);
  window.addEventListener('popstate', handleHashRouting);
}

let deferredInstallPrompt = null;
let initialRouteRestored = false;

export function openMobileSearch() {
  switchAppTab('places');
  const searchBtn = document.getElementById('mobile-nav-search');
  document.querySelectorAll('.mobile-nav-btn').forEach(b => b.classList.remove('active'));
  if (searchBtn) searchBtn.classList.add('active');
  setTimeout(() => {
    const input = document.getElementById('home-search-input');
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
  } catch {}
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
  } catch {}
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
    navigator.serviceWorker.register('/sw.js').catch(() => {});
  }

  const isStandalone =
    (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) ||
    window.navigator.standalone === true;

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

function bootstrapApp() {
  initTheme();
  initKeyboardAndRouter();
  initPwaAndWindowModes();
  bindDesktopPhotoIntercepts();
  initUnsavedGuard();
  onCrossTabSync(() => {
    loadAllData(false);
  });
  startHintRotation();
  loadAllData(true);
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
    openSettingsModal: openMoreModal,
    closeSlideDrawer: closeSlideDrawer,
    openItemDrawer: openItemDrawer,
    editItemFromDrawer: editItemFromDrawer,
    deleteItemFromDrawer: deleteItemFromDrawer,
    verifyItemFromDrawer: verifyItemFromDrawer,
    navToItem: navToItem,
    editItemFromView: editItemFromView,
    deleteItemFromView: deleteItemFromView,
    verifyItemFromView: verifyItemFromView,
    closeOnScrim: closeOnScrim,
    closeModal: closeModal,
    resolveUnsavedPrompt: resolveUnsavedPrompt,
    handleItemFormSubmit: handleItemFormSubmit,
    handleItemPhotoPreview: handleItemPhotoPreview,
    openAddItemModal: openAddItemModal,
    openAddItemWithPrefill: openAddItemWithPrefill,
    openAddStorageModal: openAddStorageModal,
    openEditStorageModal: openEditStorageModal,
    handleStorageFormSubmit: handleStorageFormSubmit,
    setStorageTypeChoice: setStorageTypeChoice,
    selectRoomEmoji: selectRoomEmoji,
    handleStorageParentChange: handleStorageParentChange,
    toggleAutoDrawersUi: toggleAutoDrawersUi,
    toggleUseGridUi: toggleUseGridUi,
    openGridDesignerModal: openGridDesignerModal,
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
    fillAllEmptyDesignerCells: fillAllEmptyDesignerCells,
    addDrawerAtEmptyCell: addDrawerAtEmptyCell,
    combineSelectedDesignerCells: combineSelectedDesignerCells,
    resizeSelectedDesignerSlot: resizeSelectedDesignerSlot,
    splitSelectedDesignerSlot: splitSelectedDesignerSlot,
    removeSelectedDesignerSlot: removeSelectedDesignerSlot,
    updateSelectedDesignerSlotField: updateSelectedDesignerSlotField,
    designerClearSelection: designerClearSelection,
    saveGridDesigner: saveGridDesigner,
    updateDrawerBatchPreview: updateDrawerBatchPreview,
    handleStoragePhotoChange: handleStoragePhotoChange,
    handleStorageDelete: handleStorageDelete,
    openRapidEntryModal: openRapidEntryModal,
    triggerRapidPhotoInput: triggerRapidPhotoInput,
    handleRapidPhotoSelected: handleRapidPhotoSelected,
    submitRapidEntry: submitRapidEntry,
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
    openHomeCameraSnap: openHomeCameraSnap,
    setHomePage: setHomePage,
    skipNeedsHomeContainer: skipNeedsHomeContainer,
    openStarterRoom: openStarterRoom,
    dismissStarterRoom: dismissStarterRoom,
    navToNeedsHome: navToNeedsHome,
    navToRoom: navToRoom,
    navToStorageUnit: navToStorageUnit,
    navToDrawer: navToDrawer,
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
    resetPhotoEditorCrop: resetPhotoEditorCrop,
    closePhotoEditorModal: closePhotoEditorModal,
    closePhotoEditorOnScrim: closePhotoEditorOnScrim,
    applyPhotoEditor: applyPhotoEditor,
    toggleUnitMoreMenu: toggleUnitMoreMenu,
    closeUnitMoreMenu: closeUnitMoreMenu,
    handleUnitMoreKeydown: handleUnitMoreKeydown,
    openUnitPhotoViewer: openUnitPhotoViewer,
    unitViewerReplacePhoto: unitViewerReplacePhoto,
    unitViewerCropPhoto: unitViewerCropPhoto,
    unitViewerRemovePhoto: unitViewerRemovePhoto,
    focusDrawerFromViewer: focusDrawerFromViewer,
    openMoveContainerModal: openMoveContainerModal,
    submitMoveContainer: submitMoveContainer,
    deleteContainerFromMenu: deleteContainerFromMenu,
    openMobileSearch: openMobileSearch,
    triggerPwaInstall: triggerPwaInstall,
    toggleFullScreenMode: toggleFullScreenMode,
    openAppPopupWindow: openAppPopupWindow
  });
}
