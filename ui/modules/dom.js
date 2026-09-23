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

// JSON-encode a value for a single-quoted data-args attribute.
export function jsonAttr(value) {
  return escapeHtml(JSON.stringify(value));
}
