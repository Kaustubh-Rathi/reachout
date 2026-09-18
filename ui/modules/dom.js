// DOM escaping and attribute helpers shared across the dashboard.

export function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// Escape a value interpolated inside a single-quoted JS string that itself
// lives in a double-quoted HTML attribute.
export function jsAttr(str) {
  return escapeHtml(String(str == null ? '' : str).replace(/\\/g, '\\\\').replace(/'/g, "\\'"));
}

// JSON-encode a value for a single-quoted data-args attribute.
export function jsonAttr(value) {
  return escapeHtml(JSON.stringify(value));
}
