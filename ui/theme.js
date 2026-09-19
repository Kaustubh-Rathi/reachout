// Resolve the theme before first paint to avoid a flash of the wrong theme.
(function () {
  var MODE_KEY = 'reachout-theme-mode';
  var mode = 'system';
  try {
    mode = localStorage.getItem(MODE_KEY) || 'system';
  } catch (e) {
    mode = 'system';
  }
  if (mode !== 'light' && mode !== 'dark' && mode !== 'system') mode = 'system';
  var mql = window.matchMedia('(prefers-color-scheme: dark)');
  var resolved = mode === 'system' ? (mql.matches ? 'dark' : 'light') : mode;
  document.documentElement.setAttribute('data-theme-mode', mode);
  document.documentElement.setAttribute('data-theme', resolved);
})();
