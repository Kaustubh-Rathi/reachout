// Theme management (system / light / dark) — persisted per browser.
//
// This module is the single owner of the theme mode key, resolution, and
// application. The pre-paint IIFE in ui/theme.js duplicates only the resolve
// step because it must run before first paint (ES modules are always deferred),
// so the theme flash cannot wait for this module.

const THEME_MODE_KEY = 'reachout-theme-mode';
const themeMedia = window.matchMedia('(prefers-color-scheme: dark)');

function readThemeMode() {
  try {
    const mode = localStorage.getItem(THEME_MODE_KEY) || 'system';
    return mode === 'light' || mode === 'dark' ? mode : 'system';
  } catch (e) {
    return 'system';
  }
}

function resolveTheme(mode) {
  if (mode === 'light' || mode === 'dark') return mode;
  return themeMedia.matches ? 'dark' : 'light';
}

function applyTheme(mode) {
  const resolved = resolveTheme(mode);
  document.documentElement.setAttribute('data-theme-mode', mode);
  document.documentElement.setAttribute('data-theme', resolved);
  document.querySelectorAll('.theme-menu [data-theme-mode]').forEach((item) => {
    item.setAttribute('aria-checked', item.getAttribute('data-theme-mode') === mode ? 'true' : 'false');
  });
  // The trigger shows the active mode's glyph, so the rail reads at a glance.
  // SVG elements do not reflect the `hidden` IDL property, so toggle the
  // attribute explicitly (the stylesheet keys off svg[hidden]).
  document.querySelectorAll('.theme-trigger [data-icon]').forEach((icon) => {
    if (icon.getAttribute('data-icon') === mode) icon.removeAttribute('hidden');
    else icon.setAttribute('hidden', '');
  });
}

export function setThemeMode(mode) {
  const safe = mode === 'light' || mode === 'dark' || mode === 'system' ? mode : 'system';
  try {
    localStorage.setItem(THEME_MODE_KEY, safe);
  } catch (e) {
    /* storage unavailable */
  }
  applyTheme(safe);
}

export function initTheme() {
  applyTheme(readThemeMode());
  themeMedia.addEventListener('change', () => {
    if (readThemeMode() === 'system') applyTheme('system');
  });
}
