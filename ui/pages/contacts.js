import { api, apiErrorText } from '../modules/api.js';
import { escapeHtml } from '../modules/dom.js';
import { showToast, appConfirm } from '../modules/toast.js';
import { state } from '../modules/store.js';
import { registerActions, getAction } from '../modules/actions.js';
import { renderCompanyCard } from '../components/company_card.js';
import { appendTimelineItem, buildTimelineEvents, isRecoveryAttempt } from '../components/timeline.js';

const CONTACTS_PAGE_SIZE = 10;

let hierarchiesAbort = null;
let searchDebounce = null;

// 2. Fetch Companies
async function fetchCompanies() {
  try {
    const res = await api.getCompanies();
    if (res.ok) {
      state.companies = await res.json();
      const select = document.getElementById('company-filter');
      select.innerHTML = '<option value="ALL">All Companies</option>';
      state.companies.forEach(c => {
        const opt = document.createElement('option');
        opt.value = c.id;
        opt.textContent = `${c.name} (${c.contact_count})`;
        select.appendChild(opt);
      });
      getAction('clearLoadError')('companies');
    } else {
      getAction('reportLoadError')('companies');
    }
  } catch (err) {
    console.error('Error loading companies:', err);
    getAction('reportLoadError')('companies');
  }
}

function renderHierarchySkeleton() {
  const container = document.getElementById('hierarchy-cards-list');
  container.innerHTML = [1, 2, 3].map(() => `
    <div class="skeleton-card" aria-hidden="true">
      <div class="skeleton-line" style="width: 38%;"></div>
      <div class="skeleton-line" style="width: 92%;"></div>
      <div class="skeleton-line" style="width: 71%;"></div>
    </div>
  `).join('');
}

// 3. Fetch & Render Hierarchies (Company -> HR -> Endpoints -> History)
async function fetchHierarchies() {
  const summary = document.getElementById('hierarchy-summary-text');
  const defaultSummary = 'Company-First progression &bull; Discrete Endpoint Tracking';
  try {
    const params = new URLSearchParams();
    if (state.searchQuery) params.set('search', state.searchQuery);
    if (state.selectedCompany !== 'ALL') params.set('company', state.selectedCompany);
    if (state.selectedCrmStatus !== 'ALL') params.set('crm_status', state.selectedCrmStatus);
    // RECOVERY is a client-side bucket (the server only knows INTERESTED /
    // NOT_INTERESTED / FOLLOW_UP_DUE / RECENTLY_ACTIVE / UNCONTACTED), so it
    // is never sent upstream; renderHierarchyView applies it locally.
    if (state.selectedPriorityFilter !== 'ALL' && state.selectedPriorityFilter !== 'RECOVERY') params.set('priority_filter', state.selectedPriorityFilter);
    if (state.selectedChannelStatus !== 'ALL') params.set('channel_status', state.selectedChannelStatus);

    if (summary) summary.innerHTML = 'Refreshing company hierarchies&hellip;';
    // Cancel any in-flight hierarchy request so a slow earlier response
    // cannot overwrite the results of a newer search/filter.
    if (hierarchiesAbort) hierarchiesAbort.abort();
    hierarchiesAbort = new AbortController();
    const res = await api.fetchHierarchy(params.toString(), { signal: hierarchiesAbort.signal });
    if (res.ok) {
      state.hierarchies = await res.json();
      renderHierarchyView(state.hierarchies);
      getAction('clearLoadError')('hierarchies');
    } else {
      getAction('reportLoadError')('hierarchies');
      showToast('Failed to load hierarchies: ' + await apiErrorText(res), 'error');
    }
  } catch (err) {
    if (err.name === 'AbortError') return;
    console.error('Error loading hierarchies:', err);
    getAction('reportLoadError')('hierarchies');
    showToast('Failed to load hierarchies: ' + err.message, 'error');
  } finally {
    if (summary) summary.innerHTML = defaultSummary;
  }
}

function renderHierarchyView(hierarchies) {
  const container = document.getElementById('hierarchy-cards-list');
  container.innerHTML = '';

  // Client-side RECOVERY bucket: keep companies with at least one contact
  // whose history holds a RECOVERY_REQUIRED / UNKNOWN attempt.
  let visible = hierarchies || [];
  if (state.selectedPriorityFilter === 'RECOVERY') {
    visible = visible.filter((comp) =>
      (comp.contacts || []).some((hr) => (hr.history || []).some(isRecoveryAttempt))
    );
  }

  if (visible.length === 0) {
    const emptyMsg = state.selectedPriorityFilter === 'RECOVERY'
      ? 'Recovery queue is clear &mdash; no contacts need operator review.'
      : 'No companies found matching the selected filter criteria.';
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
  const totalPages = Math.max(1, Math.ceil(visible.length / CONTACTS_PAGE_SIZE));
  if (state.contactsPage > totalPages) state.contactsPage = totalPages;
  const pageSlice = visible.slice(
    (state.contactsPage - 1) * CONTACTS_PAGE_SIZE,
    state.contactsPage * CONTACTS_PAGE_SIZE
  );

  pageSlice.forEach((comp) => container.appendChild(renderCompanyCard(comp)));

  renderContactsPagination(visible.length);
}

function renderContactsPagination(totalCount) {
  const host = document.getElementById('contacts-pagination');
  if (!host) return;
  const totalPages = Math.max(1, Math.ceil(totalCount / CONTACTS_PAGE_SIZE));
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
  state.selectedChannelStatus = document.getElementById('channel-filter').value;
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
  timelineList.innerHTML = '<div style="color: var(--text-muted);">Loading activity history...</div>';

  try {
    const res = await api.getContact(contactId);
    if (res.ok) {
      const detail = await res.json();
      summaryBox.innerHTML = `<strong>${escapeHtml(detail.name)}</strong> (${escapeHtml(detail.company_name)}) &bull; Phone(s): ${escapeHtml(((detail.phones && detail.phones.length ? detail.phones : (detail.phone ? [detail.phone] : [])).join(', ') || '-'))} &bull; Email(s): ${escapeHtml(((detail.emails && detail.emails.length ? detail.emails : (detail.email ? [detail.email] : [])).join(', ') || '-'))}`;

      timelineList.innerHTML = '';
      const events = buildTimelineEvents(detail);

      if (events.length === 0) {
        timelineList.innerHTML = '<div class="history-empty">No outreach attempts or CRM activities recorded yet.</div>';
        return;
      }

      events.forEach((ev) => appendTimelineItem(timelineList, ev));
    }
  } catch (err) {
    timelineList.innerHTML = `<div style="color: var(--accent-rose);">Failed to load history: ${err.message}</div>`;
  }
}

function closeHistoryModal() {
  document.getElementById('history-modal').classList.remove('open');
}

registerActions({ fetchCompanies, renderHierarchySkeleton, fetchHierarchies, renderHierarchyView, renderContactsPagination, changeContactsPage, toggleCompany, updateContactStatus, archiveContact, handleSearchChange, applyFilters, setPriorityFilter, openHistoryModal, closeHistoryModal });
export { fetchCompanies, renderHierarchySkeleton, fetchHierarchies, renderHierarchyView, renderContactsPagination, changeContactsPage, toggleCompany, updateContactStatus, archiveContact, handleSearchChange, applyFilters, setPriorityFilter, openHistoryModal, closeHistoryModal };
