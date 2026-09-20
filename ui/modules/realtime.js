// Real-time event streaming: WebSocket with automatic reconnect and SSE fallback.
//
// Owns the transport state machine, event normalization, and burst coalescing.
// Emits normalized events ({ type, payload, occurred_at }) to subscribers; it
// never calls fetch/render itself — subscribers own the reaction.

let activeWebSocket = null;
let activeEventSource = null;
let reconnectTimer = null;
let reconnectDelay = 3000;
const RECONNECT_MAX = 30000;
const SSE_EVENT_TYPES = [
  'CAMPAIGN_STARTED', 'CAMPAIGN_PAUSED', 'CAMPAIGN_COMPLETED', 'CAMPAIGN_STOPPED', 'CAMPAIGN_FAILED', 'CAMPAIGN_SENDER_UNAVAILABLE',
  'ATTEMPT_PREPARED', 'ATTEMPT_STARTED', 'ATTEMPT_SENT', 'ATTEMPT_FAILED', 'ATTEMPT_UNKNOWN', 'ATTEMPT_RECOVERY_REQUIRED',
  'CRM_STATUS_CHANGED', 'INTERVIEW_STATUS_UPDATED', 'CONTACT_ARCHIVED', 'FOLLOW_UP_DUE',
  'SYNC_STARTED', 'SYNC_COMPLETED', 'SYNC_FAILED',
  'SENDER_STATUS_CHANGED', 'SENDER_QR_RECEIVED', 'SENDER_AUTH_PROGRESS',
];

let subscriber = null;
const subscribers = new Set();

function subscribeToEvents(listener) {
  subscribers.add(listener);
  return () => subscribers.delete(listener);
}

function emit(event) {
  subscribers.forEach((listener) => {
    try {
      listener(event);
    } catch (err) {
      console.error('Live event listener error:', err);
    }
  });
}

function setWsBanner(visible) {
  const banner = document.getElementById('ws-banner');
  if (banner) banner.classList.toggle('visible', !!visible);
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  setWsBanner(true);
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    reconnectDelay = Math.min(reconnectDelay * 2, RECONNECT_MAX);
    initEventStream();
  }, reconnectDelay);
}

function closeTransports() {
  if (activeWebSocket) {
    try { activeWebSocket.onclose = null; activeWebSocket.close(); } catch (e) { /* already closed */ }
    activeWebSocket = null;
  }
  if (activeEventSource) {
    try { activeEventSource.close(); } catch (e) { /* already closed */ }
    activeEventSource = null;
  }
}

function handleRaw(data) {
  let parsed = null;
  try {
    parsed = JSON.parse(data);
  } catch (err) {
    console.error('Invalid live event payload:', err);
    return;
  }
  // The WebSocket transport sets `type` to the dot-notation name
  // (e.g. "sender.status.changed") while SSE sets `event_type` to the
  // canonical snake_case name. Normalize both to one uppercase snake_case key.
  const rawType = parsed.event_type || parsed.type || '';
  emit({
    type: String(rawType).toUpperCase().replace(/\./g, '_'),
    payload: parsed.payload || {},
    occurred_at: parsed.occurred_at,
  });
}

function initEventStream() {
  closeTransports();

  try {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(`${proto}//${window.location.host}/api/events/ws`);
    activeWebSocket = socket;

    socket.onopen = () => {
      reconnectDelay = 3000;
      setWsBanner(false);
    };
    socket.onmessage = (e) => handleRaw(e.data);
    // Only fall back while this socket is still the active transport.
    socket.onerror = () => {
      if (activeWebSocket === socket) fallbackToSSE();
    };
    socket.onclose = () => {
      if (activeWebSocket !== socket) return;
      activeWebSocket = null;
      scheduleReconnect();
    };
  } catch (e) {
    fallbackToSSE();
  }
}

function fallbackToSSE() {
  if (activeEventSource) return;
  // Stop the WebSocket so its close handler cannot schedule a competing reconnect.
  if (activeWebSocket) {
    try { activeWebSocket.onclose = null; activeWebSocket.onerror = null; activeWebSocket.close(); } catch (e) { /* already closed */ }
    activeWebSocket = null;
  }
  const source = new EventSource('/api/events/stream');
  activeEventSource = source;

  source.onopen = () => {
    reconnectDelay = 3000;
    setWsBanner(false);
  };
  source.onmessage = (e) => handleRaw(e.data);
  SSE_EVENT_TYPES.forEach((type) => {
    source.addEventListener(type, (e) => handleRaw(e.data));
  });
  source.onerror = () => {
    // Tear down SSE and retry the preferred WebSocket transport with backoff.
    if (activeEventSource) {
      try { activeEventSource.close(); } catch (e) { /* already closed */ }
      activeEventSource = null;
    }
    scheduleReconnect();
  };
}

export { initEventStream, subscribeToEvents, setWsBanner };
