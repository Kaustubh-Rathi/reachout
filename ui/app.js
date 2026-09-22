import { initEventStream, subscribeToEvents } from './modules/realtime.js';
import { initTheme, setThemeMode } from './modules/theme.js';
import { initModalAccessibility } from './modules/modal.js';
import { resolveConfirm, showToast } from './modules/toast.js';
import { getAction, registerActions } from './modules/actions.js';
import { initDispatch } from './modules/dispatch.js';

// Page modules register their own actions as an import side effect.
import './pages/overview.js';
import './pages/contacts.js';
import './pages/senders.js';
import './pages/templates.js';
import './pages/outreach.js';

// ------------------------------------------------------------------
// Header overflow ("More") menu
// ------------------------------------------------------------------
function toggleMoreMenu(event) {
  if (event) event.stopPropagation();
  const panel = document.getElementById('more-menu-panel');
  const btn = document.getElementById('more-menu-btn');
  if (!panel) return;
  const willOpen = !panel.classList.contains('open');
  panel.classList.toggle('open', willOpen);
  if (btn) btn.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
}

function closeMoreMenu() {
  const panel = document.getElementById('more-menu-panel');
  const btn = document.getElementById('more-menu-btn');
  if (panel) panel.classList.remove('open');
  if (btn) btn.setAttribute('aria-expanded', 'false');
}

document.addEventListener('click', (event) => {
  const panel = document.getElementById('more-menu-panel');
  if (panel && panel.classList.contains('open') && !event.target.closest('.menu-wrap')) {
    closeMoreMenu();
  }
});

document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeMoreMenu();
});

// ------------------------------------------------------------------
// Initial/background load failure reporting
// ------------------------------------------------------------------
const loadErrors = new Set();

function renderLoadErrors() {
  const banner = document.getElementById('load-error-banner');
  const areas = document.getElementById('load-error-areas');
  if (loadErrors.size === 0) {
    if (banner) banner.classList.remove('visible');
    return;
  }
  if (areas) areas.textContent = 'Unavailable: ' + Array.from(loadErrors).join(', ') + '.';
  if (banner) banner.classList.add('visible');
}

function reportLoadError(area) {
  loadErrors.add(area);
  renderLoadErrors();
}

function clearLoadError(area) {
  if (loadErrors.delete(area)) renderLoadErrors();
}

async function retryLoad() {
  loadErrors.clear();
  renderLoadErrors();
  await Promise.allSettled([
    getAction('fetchKpis')(),
    getAction('fetchCompanies')(),
    getAction('fetchHierarchies')(),
    getAction('fetchCampaigns')(),
    getAction('fetchSenders')(),
    getAction('fetchTemplates')(),
  ]);
}

// ------------------------------------------------------------------
// Live event handling
// ------------------------------------------------------------------
// Coalesce bursts of live events into a single refetch so a running
// campaign does not trigger a refetch storm.
const pendingRefresh = { kpis: false, hierarchies: false, campaigns: false, companies: false };
let liveRefreshTimer = null;

function scheduleLiveRefresh(flags) {
  Object.keys(flags).forEach((key) => {
    if (flags[key]) pendingRefresh[key] = true;
  });
  if (liveRefreshTimer) return;
  liveRefreshTimer = setTimeout(() => {
    liveRefreshTimer = null;
    const work = { ...pendingRefresh };
    pendingRefresh.kpis = false;
    pendingRefresh.hierarchies = false;
    pendingRefresh.campaigns = false;
    pendingRefresh.companies = false;
    const tasks = [];
    if (work.kpis) tasks.push(getAction('fetchKpis')());
    if (work.hierarchies) tasks.push(getAction('fetchHierarchies')());
    if (work.campaigns) tasks.push(getAction('fetchCampaigns')());
    if (work.companies) tasks.push(getAction('fetchCompanies')());
    Promise.allSettled(tasks);
  }, 400);
}

async function handleLiveEvent(ev) {
  if (!ev) return;
  // The WebSocket transport sets `type` to the dot-notation name
  // (e.g. "sender.status.changed") while SSE sets `event_type` to the
  // canonical snake_case name (e.g. "SENDER_STATUS_CHANGED"). Normalize both
  // to the same uppercase snake_case key before dispatching.
  const rawType = ev.event_type || ev.type || '';
  const type = String(rawType).toUpperCase().replace(/\./g, '_');
  const p = ev.payload || {};

  getAction('addActivityItem')(getAction('describeLiveActivity')(type, p), ev.occurred_at);

  if (type === 'SENDER_STATUS_CHANGED') {
    await getAction('fetchSenders')();
    if (p.sender_id && p.status) {
      showToast(`Sender ${p.sender_id} is now ${p.status}`, p.status === 'ACTIVE' ? 'success' : 'info');
    }
  } else if (type === 'SENDER_QR_RECEIVED') {
    await getAction('fetchSenders')();
    showToast(`QR Code received for ${p.sender_id}. Ready to scan!`, 'info');
  } else if (type === 'SENDER_AUTH_PROGRESS') {
    if (p.message) showToast(`[Auth] ${p.message}`, 'info');
  } else if (type === 'ATTEMPT_SENT') {
    const dest = p.destination || p.recipient || 'recipient';
    const ch = p.channel || 'Outreach';
    showToast(`Sent ${ch} to ${dest}`, 'success');
    scheduleLiveRefresh({ kpis: true, hierarchies: true, campaigns: true });
  } else if (type === 'ATTEMPT_FAILED') {
    showToast(`Message failed: ${p.failure_detail || p.failure_code || 'Error'}`, 'error');
    scheduleLiveRefresh({ kpis: true, hierarchies: true });
  } else if (type === 'ATTEMPT_RECOVERY_REQUIRED' || type === 'ATTEMPT_UNKNOWN') {
    showToast(`⚠️ Outreach recovery required for ${p.destination || 'contact'}`, 'error');
    scheduleLiveRefresh({ kpis: true, hierarchies: true });
  } else if (type.startsWith('CAMPAIGN_')) {
    scheduleLiveRefresh({ campaigns: true, kpis: true });
  } else if (type === 'CRM_STATUS_CHANGED' || type === 'INTERVIEW_STATUS_UPDATED' || type === 'CONTACT_ARCHIVED') {
    scheduleLiveRefresh({ kpis: true, hierarchies: true });
  } else if (type === 'SYNC_COMPLETED') {
    showToast('Source synchronization completed!', 'success');
    scheduleLiveRefresh({ kpis: true, companies: true, hierarchies: true });
  }
}

// ------------------------------------------------------------------
// Sidebar navigation: switch routed pages.
// ------------------------------------------------------------------
function setActiveNav(route) {
  document.querySelectorAll('.nav-item').forEach(function (el) {
    el.classList.toggle('active', el.dataset.nav === route);
  });
}

function showPage(route) {
  const page = route === 'contacts' || route === 'senders' || route === 'templates' ? route : 'overview';
  document.querySelectorAll('.page').forEach(function (el) {
    el.classList.toggle('active', el.id === 'page-' + page);
  });
  setActiveNav(page);
  return page;
}

async function navigate(route) {
  const page = showPage(route);
  if (page === 'senders') await getAction('openSendersDrawer')();
  if (page === 'templates') await getAction('openTemplatesDrawer')();
}

// ------------------------------------------------------------------
// Initial load + bootstrap
// ------------------------------------------------------------------
async function loadInitialData() {
  await Promise.all([
    getAction('fetchKpis')(),
    getAction('fetchCompanies')(),
    getAction('fetchSenders')(),
    getAction('fetchTemplates')(),
    getAction('fetchCampaigns')(),
  ]);
  await getAction('fetchHierarchies')();
  await getAction('fetchActivity')();
  // Signal for E2E tests that initial data has rendered.
  window.__dashboardReady = true;
}

registerActions({
  toggleMoreMenu,
  closeMoreMenu,
  renderLoadErrors,
  reportLoadError,
  clearLoadError,
  retryLoad,
  scheduleLiveRefresh,
  handleLiveEvent,
  setActiveNav,
  showPage,
  navigate,
  loadInitialData,
  setThemeMode,
  resolveConfirm,
});

initDispatch();
initModalAccessibility();

window.addEventListener('DOMContentLoaded', async () => {
  initTheme();
  await loadInitialData();
  subscribeToEvents(handleLiveEvent);
  initEventStream();
});
