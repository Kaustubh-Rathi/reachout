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
