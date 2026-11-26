(function () {
  try {
    var pref = localStorage.getItem('wims_theme_pref') || 'dark';
    var resolved = pref === 'system'
      ? (window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark')
      : pref;
    document.documentElement.setAttribute('data-theme', resolved);
  } catch (e) {}
})();
