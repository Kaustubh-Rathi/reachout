// Thin fetch wrapper and error-message extraction for the dashboard API.

export async function apiFetch(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && typeof options.body === 'string' && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }
  return fetch(url, { ...options, headers });
}

// Extract a human-readable message from a failed API response.
export async function apiErrorText(res) {
  try {
    const body = await res.json();
    if (body && body.detail) {
      if (typeof body.detail === 'string') return body.detail;
      return body.detail.message || body.detail.reason || JSON.stringify(body.detail);
    }
  } catch (e) {
    /* non-JSON body */
  }
  return res.statusText || `HTTP ${res.status}`;
}
