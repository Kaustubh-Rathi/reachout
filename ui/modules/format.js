// Display formatting helpers.

export function formatDate(dateStr) {
  if (!dateStr) return '';
  try {
    const d = new Date(dateStr);
    if (Number.isNaN(d.getTime())) return '';
    const day = d.getDate().toString().padStart(2, '0');
    const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
    const month = months[d.getMonth()];
    const year = d.getFullYear();
    const hours = d.getHours().toString().padStart(2, '0');
    const minutes = d.getMinutes().toString().padStart(2, '0');
    return `${day} ${month} ${year} ${hours}:${minutes}`;
  } catch (err) {
    return '';
  }
}

export function formatTime(dateStr) {
  if (!dateStr) return '';
  try {
    let s = String(dateStr).trim();
    if (!s) return '';
    if (/^\d{4}-\d{2}-\d{2}T/.test(s) && !/([Zz]|[+-]\d{2}:?\d{2}(:\d{2})?)$/.test(s)) {
      s += 'Z';
    }
    const d = new Date(s);
    if (Number.isNaN(d.getTime())) return '';
    const hours = d.getHours().toString().padStart(2, '0');
    const minutes = d.getMinutes().toString().padStart(2, '0');
    return `${hours}:${minutes}`;
  } catch (err) {
    return '';
  }
}

/**
 * Endpoint addresses are personal data, so lists show a masked form that still
 * distinguishes one endpoint from another. The full value never leaves the
 * form control that performs the send.
 * @param {string} value Phone number or email address.
 * @returns {string} Masked display value.
 */
export function maskEndpoint(value) {
  const text = String(value == null ? '' : value).trim();
  if (!text) return '';
  const at = text.lastIndexOf('@');
  if (at > 0) {
    const local = text.slice(0, at);
    const domain = text.slice(at + 1);
    const head = local.slice(0, 1);
    return `${head}${'\u2022'.repeat(Math.min(Math.max(local.length - 1, 3), 5))}@${domain}`;
  }
  const digits = text.replace(/\D/g, '');
  if (!digits) return '';
  if (digits.length <= 4) return digits;
  const prefix = text.startsWith('+') ? `+${digits.slice(0, 2)}` : digits.slice(0, 2);
  return `${prefix}\u2022\u2022\u2022\u2022${digits.slice(-3)}`;
}
