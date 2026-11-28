(function () {
  try {
    var pref = localStorage.getItem('wims_theme_pref') || 'dark';
    var resolved = pref === 'system'
      ? (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')
      : pref;
    document.documentElement.setAttribute('data-theme', resolved);
    var themeMeta = document.querySelector('meta[name="theme-color"]');
    if (themeMeta) {
      themeMeta.setAttribute('content', resolved === 'light' ? '#f8fafc' : '#0b1120');
    }
    var isStandalone = Boolean(
      (window.matchMedia && (
        window.matchMedia('(display-mode: standalone)').matches ||
        window.matchMedia('(display-mode: fullscreen)').matches
      )) || window.navigator.standalone === true
    );
    if (isStandalone) {
      document.documentElement.classList.add('is-standalone');
    }
  } catch (e) {}
})();
