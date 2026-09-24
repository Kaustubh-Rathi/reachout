import { api, apiErrorText } from '../modules/api.js';
import { escapeHtml } from '../modules/dom.js';
import { showToast, appConfirm } from '../modules/toast.js';
import { state } from '../modules/store.js';
import { registerActions, getAction } from '../modules/actions.js';
import { renderCompanyCard } from '../components/company_card.js';
import { appendTimelineItem, buildTimelineEvents, isRecoveryAttempt } from '../components/timeline.js';

const HIERARCHY_PAGE_SIZE = 10;

let hierarchiesAbort = null;
let searchDebounce = null;

// 2. Fetch Companies
async function fetchCompanies() {
  try {
    const res = await api.getCompanies();
    if (res.ok) {
      state.companies = await res.json();
      const select = document.getElementById('company-filter');
      const selectedCompany = state.selectedCompany;
      select.innerHTML = '<option value="ALL">All Companies</option>';
      state.companies.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.id;
        opt.textContent = `${c.name} (${c.contact_count})`;
        select.appendChild(opt);
      });
      state.selectedCompany = state.companies.some(company => company.id === selectedCompany) ? selectedCompany : 'ALL';
      select.value = state.selectedCompany;
      getAction('clearLoadError')('companies');
      return true;
    }
    getAction('reportLoadError')('companies');
  } catch (err) {
    console.error('Error loading companies:', err);
    getAction('reportLoadError')('companies');
  }
  return false;
}

// 3. Fetch & Render Hierarchies (Company -> HR -> Endpoints -> History)
async function fetchHierarchies() {
  const summary = document.getElementById('hierarchy-summary-text');
  let request = null;
  try {
    const params = new URLSearchParams();
    if (state.searchQuery) params.set('search', state.searchQuery);
    if (state.selectedCompany !== 'ALL') params.set('company', state.selectedCompany);
    if (state.selectedCrmStatus !== 'ALL') params.set('crm_status', state.selectedCrmStatus);

    if (summary) summary.textContent = 'Refreshing company hierarchies…';
    if (hierarchiesAbort) hierarchiesAbort.abort();
    request = new AbortController();
    hierarchiesAbort = request;
    const res = await api.fetchHierarchy(params.toString(), { signal: request.signal });
    if (res.ok) {
      const hierarchies = await res.json();
      if (hierarchiesAbort !== request) return false;
      state.hierarchies = hierarchies;
      renderHierarchyView(state.hierarchies);
      getAction('clearLoadError')('hierarchies');
      return true;
    }
    const message = await apiErrorText(res);
    if (hierarchiesAbort !== request) return false;
    renderHierarchyLoadError(message);
    getAction('reportLoadError')('hierarchies');
    showToast('Failed to load hierarchies: ' + message, 'error');
  } catch (err) {
    if (err.name === 'AbortError' || hierarchiesAbort !== request) return false;
    renderHierarchyLoadError(err.message);
    console.error('Error loading hierarchies:', err);
    getAction('reportLoadError')('hierarchies');
    showToast('Failed to load hierarchies: ' + err.message, 'error');
  }
  return false;
}

function renderHierarchyLoadError(message) {
  const summary = document.getElementById('hierarchy-summary-text');
  const container = document.getElementById('hierarchy-cards-list');
  if (summary) summary.textContent = 'Company hierarchies unavailable';
  if (container) {
    container.innerHTML = `<div class="hierarchy-empty">Failed to load company hierarchies: ${escapeHtml(message)}</div>`;
  }
}

function matchesCoverage(contact, status) {
  const coverage = contact.coverage || {};
  if (status === 'WITH_SENDS') return (coverage.covered_endpoints || 0) > 0;
  if (status === 'FULLY_MESSAGED') return coverage.is_fully_covered === true;
  if (status === 'READY') return (coverage.ready_endpoints || 0) > 0;
  if (status === 'NEVER_ATTEMPTED') return (coverage.never_attempted_endpoints || 0) > 0;
  if (status === 'BLOCKED') return (coverage.blocked_endpoints || 0) > 0;
  if (status === 'HAS_FAILED') return (
    (coverage.retryable_failed_endpoints || 0) + (coverage.permanent_failed_endpoints || 0)
  ) > 0;
  return true;
}

function matchesPriority(contact, priority) {
  if (priority === 'RECOVERY') return (contact.history || []).some(isRecoveryAttempt);
  if (priority === 'FOLLOW_UP_DUE') return contact.follow_up_due === true;
  if (priority === 'INTERESTED') return contact.crm_outcome === 'INTERESTED';
  if (priority === 'NOT_INTERESTED') return contact.crm_outcome === 'NOT_INTERESTED';
  if (priority === 'RECENTLY_ACTIVE') return Boolean(
    contact.last_activity_at || contact.last_whatsapp_at || contact.last_email_at
  );
  return true;
}

function filterHierarchyForView(company) {
  const search = state.searchQuery.trim().toLowerCase();
  const companyMatches = !search || company.name.toLowerCase().includes(search) || (company.domain || '').toLowerCase().includes(search);
  const contacts = (company.contacts || []).filter(contact => {
    if (!companyMatches) {
      const searchable = [contact.name, contact.phone, contact.email, contact.designation]
        .some(value => String(value || '').toLowerCase().includes(search));
      if (!searchable) return false;
    }
    if (state.selectedCrmStatus !== 'ALL' && contact.crm_outcome !== state.selectedCrmStatus) return false;
    if (!matchesPriority(contact, state.selectedPriorityFilter)) return false;
    return matchesCoverage(contact, state.selectedCoverageStatus);
  });
  return contacts.length ? { ...company, contacts } : null;
}

function renderHierarchyView(hierarchies) {
  const container = document.getElementById('hierarchy-cards-list');
  container.innerHTML = '';
  const visible = (hierarchies || []).map(filterHierarchyForView).filter(Boolean);

  if (visible.length === 0) {
    const summaryTextEl = document.getElementById('hierarchy-summary-text');
    if (summaryTextEl) summaryTextEl.textContent = 'No matching companies';
    const emptyMsg = state.selectedPriorityFilter === 'RECOVERY'
      ? 'Recovery queue is clear — no contacts need operator review.'
      : 'No contacts match the selected filters.';
    container.innerHTML = `<div class="hierarchy-empty">${emptyMsg}</div>`;
    renderContactsPagination(0);
    return;
  }

  // Result count keeps the operator oriented; pagination bounds the scroll.
  const contactCount = visible.reduce((n, c) => n + (c.contacts ? c.contacts.length : 0), 0);
  const summaryTextEl = document.getElementById('hierarchy-summary-text');
  if (summaryTextEl) {
    summaryTextEl.textContent =
      `${visible.length} compan${visible.length === 1 ? 'y' : 'ies'} · ` +
      `${contactCount} contact${contactCount === 1 ? '' : 's'}`;
  }
  const totalPages = Math.max(1, Math.ceil(visible.length / HIERARCHY_PAGE_SIZE));
  if (state.contactsPage > totalPages) state.contactsPage = totalPages;
  const pageSlice = visible.slice(
    (state.contactsPage - 1) * HIERARCHY_PAGE_SIZE,
    state.contactsPage * HIERARCHY_PAGE_SIZE
  );

  pageSlice.forEach((comp) => container.appendChild(renderCompanyCard(comp)));

  renderContactsPagination(visible.length);
}

function renderContactsPagination(totalCount) {
  const host = document.getElementById('contacts-pagination');
  if (!host) return;
  const totalPages = Math.max(1, Math.ceil(totalCount / HIERARCHY_PAGE_SIZE));
  if (totalPages <= 1) {
    host.innerHTML = '';
    return;
  }
  host.innerHTML = `
    <button class="btn btn-outline btn-xs" data-action="changeContactsPage" data-args='[-1]' ${state.contactsPage <= 1 ? 'disabled' : ''} aria-label="Previous page">&lsaquo; Prev</button>
    <span class="page-indicator">Page ${state.contactsPage} of ${totalPages}</span>
    <button class="btn btn-outline btn-xs" data-action="changeContactsPage" data-args='[1]' ${state.contactsPage >= totalPages ? 'disabled' : ''} aria-label="Next page">Next &rsaquo;</button>
  `;
}

function changeContactsPage(delta) {
  state.contactsPage = Math.max(1, state.contactsPage + (Number(delta) || 0));
  renderHierarchyView(state.hierarchies);
}

function toggleCompany(companyId) {
  const list = document.getElementById('hrlist-' + companyId);
  const caret = document.getElementById('caret-' + companyId);
  if (!list) return;
  const isHidden = list.style.display === 'none';
  list.style.display = isHidden ? 'block' : 'none';
  if (caret) {
    caret.textContent = isHidden ? '▾' : '▸';
    const header = caret.closest('.company-card-header');
    if (header) header.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
  }
}

// 5. CRM Status Update Action
async function updateContactStatus(contactId, status, selectEl) {
  if (selectEl) selectEl.dataset.prev = selectEl.dataset.prev || selectEl.value;
  try {
    const res = await api.updateContactStatus(contactId, status);
    if (res.ok) {
      showToast(`Contact status updated to ${status}`, 'success');
      await fetchHierarchies();
      await getAction('fetchKpis')();
    } else {
      const err = await res.json();
      showToast(`Error: ${err.detail || 'Status update failed'}`, 'error');
      if (selectEl) selectEl.value = selectEl.dataset.prev;
    }
  } catch (err) {
    showToast('Error updating status: ' + err.message, 'error');
    if (selectEl) selectEl.value = selectEl.dataset.prev;
  }
}

async function archiveContact(contactId) {
  if (!(await appConfirm('Are you sure you want to archive this contact? This marks suppression so synchronizer will not recreate it.', { title: 'Archive contact', confirmLabel: 'Archive' }))) {
    return;
  }
  try {
    const res = await api.archiveContact(contactId);
    if (res.ok) {
      showToast('Contact archived and suppressed.', 'info');
      await fetchHierarchies();
      await getAction('fetchKpis')();
    } else {
      const err = await res.json().catch(() => ({}));
      showToast('Failed to archive contact: ' + (err.detail || res.statusText), 'error');
    }
  } catch (err) {
    showToast('Error archiving contact: ' + err.message, 'error');
  }
}

// Filter Helpers
function handleSearchChange() {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(() => {
    state.searchQuery = document.getElementById('search-input').value.trim();
    state.contactsPage = 1;
    fetchHierarchies();
  }, 200);
}

function applyFilters() {
  state.selectedCompany = document.getElementById('company-filter').value;
  state.selectedCrmStatus = document.getElementById('crm-status-filter').value;
  state.selectedCoverageStatus = document.getElementById('coverage-filter').value;
  state.contactsPage = 1;
  fetchHierarchies();
}

function setPriorityFilter(filterName) {
  state.selectedPriorityFilter = filterName;
  state.contactsPage = 1;
  document.querySelectorAll('.priority-pills .pill-btn').forEach(btn => {
    const isActive = btn.getAttribute('data-filter') === filterName;
    btn.classList.toggle('active', isActive);
    btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
  });
  fetchHierarchies();
}

// 8. Contact Activity Timeline / History Modal
async function openHistoryModal(contactId) {
  const modal = document.getElementById('history-modal');
  const timelineList = document.getElementById('history-timeline-list');
  const summaryBox = document.getElementById('history-contact-summary');

  modal.classList.add('open');
  summaryBox.innerHTML = '';
  timelineList.innerHTML = '<div style="color: var(--text-muted);">Loading activity history...</div>';

  try {
    const res = await api.getContact(contactId);
    if (!res.ok) {
      timelineList.innerHTML = `<div style="color: var(--accent-rose);">Failed to load history: ${escapeHtml(await apiErrorText(res))}</div>`;
      return false;
    }
    const detail = await res.json();
    summaryBox.innerHTML = `<strong>${escapeHtml(detail.name)}</strong> (${escapeHtml(detail.company_name)}) &bull; Phone(s): ${escapeHtml(((detail.phones && detail.phones.length ? detail.phones : (detail.phone ? [detail.phone] : [])).join(', ') || '-'))} &bull; Email(s): ${escapeHtml(((detail.emails && detail.emails.length ? detail.emails : (detail.email ? [detail.email] : [])).join(', ') || '-'))}`;

    timelineList.innerHTML = '';
    const events = buildTimelineEvents(detail);

    if (events.length === 0) {
      timelineList.innerHTML = '<div class="history-empty">No outreach attempts or CRM activities recorded yet.</div>';
      return true;
    }

    events.forEach((ev) => appendTimelineItem(timelineList, ev));
    return true;
  } catch (err) {
    timelineList.innerHTML = `<div style="color: var(--accent-rose);">Failed to load history: ${escapeHtml(err.message)}</div>`;
    return false;
  }
}

function closeHistoryModal() {
  document.getElementById('history-modal').classList.remove('open');
}

registerActions({ fetchCompanies, fetchHierarchies, changeContactsPage, toggleCompany, updateContactStatus, archiveContact, handleSearchChange, applyFilters, setPriorityFilter, openHistoryModal, closeHistoryModal });
