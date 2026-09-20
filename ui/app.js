import { apiErrorText, apiFetch } from './modules/api.js';
import { escapeHtml, jsonAttr } from './modules/dom.js';
import { initEventStream, setWsBanner, subscribeToEvents } from './modules/realtime.js';
import { store } from './modules/store.js';

    // Server-injected limits (see <body data-*>).
    const APP_CONFIG = {
      defaultOutreachLimit: Number(document.body.dataset.defaultLimit) || 100,
      maxOutreachLimit: Number(document.body.dataset.maxLimit) || 1000,
    };

    // Centralized observable state (see modules/store.js).
    const state = store.state;

    const CONTACTS_PAGE_SIZE = 10;

    let hierarchiesAbort = null;

    // ------------------------------------------------------------------
    // Theme management (system / light / dark) — persisted per browser
    // ------------------------------------------------------------------
    const THEME_MODE_KEY = 'reachout-theme-mode';
    const themeMedia = window.matchMedia('(prefers-color-scheme: dark)');

    function resolveTheme(mode) {
      if (mode === 'light' || mode === 'dark') return mode;
      return themeMedia.matches ? 'dark' : 'light';
    }

    function applyTheme(mode) {
      const resolved = resolveTheme(mode);
      document.documentElement.setAttribute('data-theme-mode', mode);
      document.documentElement.setAttribute('data-theme', resolved);
      document.querySelectorAll('.theme-toggle button[data-theme-mode]').forEach((btn) => {
        btn.setAttribute('aria-pressed', btn.getAttribute('data-theme-mode') === mode ? 'true' : 'false');
      });
    }

    function setThemeMode(mode) {
      if (mode !== 'light' && mode !== 'dark' && mode !== 'system') mode = 'system';
      try { localStorage.setItem(THEME_MODE_KEY, mode); } catch (e) { /* storage unavailable */ }
      applyTheme(mode);
    }

    themeMedia.addEventListener('change', () => {
      let mode = 'system';
      try { mode = localStorage.getItem(THEME_MODE_KEY) || 'system'; } catch (e) { mode = 'system'; }
      if (mode === 'system') applyTheme('system');
    });

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

    // App Initialization
    window.addEventListener('DOMContentLoaded', async () => {
      let mode = 'system';
      try { mode = localStorage.getItem(THEME_MODE_KEY) || 'system'; } catch (e) { mode = 'system'; }
      applyTheme(mode);
      await loadInitialData();
      subscribeToEvents(handleLiveEvent);
      initEventStream();
    });

    async function loadInitialData() {
      await Promise.all([
        fetchKpis(),
        fetchCompanies(),
        fetchSenders(),
        fetchTemplates(),
        fetchCampaigns(),
      ]);
      await fetchHierarchies();
      await fetchActivity();
      // Signal for E2E tests that initial data has rendered.
      window.__dashboardReady = true;
    }

    // 1. Fetch & Render KPIs
    async function fetchKpis() {
      try {
        const res = await apiFetch('/api/crm/kpis');
        if (res.ok) {
          const data = await res.json();
          renderKpis(data);
          clearLoadError('KPIs');
        } else {
          reportLoadError('KPIs');
        }
      } catch (err) {
        console.error('Error loading KPIs:', err);
        reportLoadError('KPIs');
      }
    }

    function renderKpis(k) {
      document.getElementById('val-total').innerText = k.total_contacts || 0;
      document.getElementById('val-eligible').innerText = k.eligible || 0;
      document.getElementById('val-contacted').innerText = k.contacted || 0;
      document.getElementById('val-wa-sent').innerText = k.whatsapp_sent || 0;
      document.getElementById('val-email-sent').innerText = k.email_sent || 0;
      document.getElementById('val-interested').innerText = k.interested || 0;
      document.getElementById('val-not-interested').innerText = k.not_interested || 0;
      document.getElementById('val-interview').innerText = k.interview || 0;
      document.getElementById('val-followup').innerText = k.follow_up_due || 0;
      document.getElementById('val-failed').innerText = k.failed || 0;
      document.getElementById('val-recovery').innerText = k.recovery_required || 0;
    }

    // 2. Fetch Companies
    async function fetchCompanies() {
      try {
        const res = await apiFetch('/api/companies');
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
          clearLoadError('companies');
        } else {
          reportLoadError('companies');
        }
      } catch (err) {
        console.error('Error loading companies:', err);
        reportLoadError('companies');
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

    function isRecoveryAttempt(att) {
      return att && (att.status === 'RECOVERY_REQUIRED' || att.status === 'UNKNOWN');
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
        const res = await apiFetch(`/api/companies/hierarchy?${params.toString()}`, { signal: hierarchiesAbort.signal });
        if (res.ok) {
          state.hierarchies = await res.json();
          renderHierarchyView(state.hierarchies);
          clearLoadError('hierarchies');
        } else {
          reportLoadError('hierarchies');
          showToast('Failed to load hierarchies: ' + await apiErrorText(res), 'error');
        }
      } catch (err) {
        if (err.name === 'AbortError') return;
        console.error('Error loading hierarchies:', err);
        reportLoadError('hierarchies');
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
      if (state.selectedPriorityFilter === 'RECOVERY') {
        hierarchies = (hierarchies || []).filter(comp =>
          (comp.contacts || []).some(hr =>
            (hr.history || []).some(isRecoveryAttempt)
          )
        );
      }

      if (!hierarchies || hierarchies.length === 0) {
        const emptyMsg = state.selectedPriorityFilter === 'RECOVERY'
          ? `Recovery queue is clear &mdash; no contacts need operator review.`
          : 'No companies found matching the selected filter criteria.';
        container.innerHTML = `
          <div style="text-align: center; padding: 2.5rem; color: var(--text-secondary);">
            ${emptyMsg}
          </div>
        `;
        renderContactsPagination(0);
        return;
      }

      // Result count keeps the operator oriented; pagination bounds the scroll.
      const contactCount = hierarchies.reduce((n, c) => n + (c.contacts ? c.contacts.length : 0), 0);
      const summaryTextEl = document.getElementById('hierarchy-summary-text');
      if (summaryTextEl) {
        summaryTextEl.textContent =
          `${hierarchies.length} compan${hierarchies.length === 1 ? 'y' : 'ies'} · ` +
          `${contactCount} contact${contactCount === 1 ? '' : 's'}`;
      }
      const totalPages = Math.max(1, Math.ceil(hierarchies.length / CONTACTS_PAGE_SIZE));
      if (state.contactsPage > totalPages) state.contactsPage = totalPages;
      const pageSlice = hierarchies.slice(
        (state.contactsPage - 1) * CONTACTS_PAGE_SIZE,
        state.contactsPage * CONTACTS_PAGE_SIZE
      );

      pageSlice.forEach(comp => {
        const card = document.createElement('div');
        card.className = 'company-card';
        card.setAttribute('data-company-id', comp.id);

        let statusClass = 'status-not-contacted';
        let statusIcon = '🔴';
        let statusText = 'NOT CONTACTED';

        if (comp.status === 'IN_PROGRESS') {
          statusClass = 'status-in-progress';
          statusIcon = '🟡';
          statusText = 'IN PROGRESS';
        } else if (comp.status === 'CONTACTED') {
          statusClass = 'status-contacted';
          statusIcon = '🟢';
          statusText = 'CONTACTED';
        } else if (comp.status === 'CLOSED') {
          statusClass = 'status-closed';
          statusIcon = '⚫';
          statusText = 'CLOSED';
        }

        // Build HR Contacts HTML
        let hrsHtml = '';
        (comp.contacts || []).forEach((hr, hrIdx) => {
          const curStatus = hr.crm_outcome || 'NOT_CONTACTED';
          const statusSelect = `
            <select class="form-control" style="font-size: 0.75rem; padding: 0.2rem 0.5rem; background: var(--bg-surface); border: 1px solid var(--border-medium); border-radius: var(--radius-xs); color: var(--text-primary);" data-change="updateContactStatus" data-args='[${jsonAttr(hr.contact_id)},"$value","$el"]'>
              <option value="NOT_CONTACTED" ${curStatus === 'NOT_CONTACTED' || curStatus === 'NONE' ? 'selected' : ''}>Not Contacted</option>
              <option value="PENDING_REPLY" ${curStatus === 'PENDING_REPLY' ? 'selected' : ''}>Pending Reply</option>
              <option value="CONTACTED" ${curStatus === 'CONTACTED' ? 'selected' : ''}>Contacted</option>
              <option value="REPLIED" ${curStatus === 'REPLIED' ? 'selected' : ''}>Replied</option>
              <option value="FOLLOW_UP" ${curStatus === 'FOLLOW_UP' ? 'selected' : ''}>Follow-Up Due</option>
              <option value="INTERESTED" ${curStatus === 'INTERESTED' ? 'selected' : ''}>Interested ★</option>
              <option value="NOT_INTERESTED" ${curStatus === 'NOT_INTERESTED' ? 'selected' : ''}>Not Interested</option>
              <option value="INTERVIEW" ${curStatus === 'INTERVIEW' ? 'selected' : ''}>Interview 📅</option>
              <option value="OFFER" ${curStatus === 'OFFER' ? 'selected' : ''}>Offer 🏆</option>
              <option value="REJECTED" ${curStatus === 'REJECTED' ? 'selected' : ''}>Rejected ✕</option>
              <option value="CLOSED" ${curStatus === 'CLOSED' ? 'selected' : ''}>Closed ⚫</option>
              <option value="DO_NOT_CONTACT" ${curStatus === 'DO_NOT_CONTACT' ? 'selected' : ''}>Do Not Contact ⛔</option>
            </select>
          `;

          let endpointsHtml = '';
          (hr.endpoints || []).forEach(ep => {
            const isSent = ep.status === 'SENT';
            const recoveryAtt = (hr.history || []).find(att =>
              isRecoveryAttempt(att) && att.channel === ep.channel && (att.destination || '') === (ep.address || '')
            );
            const epClass = isSent ? 'endpoint-covered' : '';
            const statusBadge = isSent
              ? `<span style="color: var(--accent-emerald); font-weight: 600;">✓ ${ep.channel} SENT</span>`
              : (recoveryAtt
                ? `<span style="color: var(--accent-amber); font-weight: 600;">⏱ ${ep.channel} IN RECOVERY QUEUE</span>`
                : `<span style="color: var(--text-secondary);">○ Not contacted</span>`);

            let metaDetails = '';
            if (isSent && ep.sender_account_id) {
              metaDetails = `<div style="font-size: 0.675rem; color: var(--text-secondary);">${escapeHtml(ep.sender_account_id)} &bull; ${escapeHtml(ep.template_id || 'Direct')} &bull; ${formatDate(ep.sent_at)}</div>`;
            } else if (recoveryAtt && recoveryAtt.failure_detail) {
              metaDetails = `<div style="font-size: 0.675rem; color: var(--accent-amber);">${escapeHtml(recoveryAtt.failure_detail)}</div>`;
            }

            const sendBtn = recoveryAtt
              ? `<button class="btn btn-amber btn-xs" data-action="openRecoveryDrawer">⏱ Review queue</button>`
              : (ep.channel === 'WHATSAPP'
                ? `<button class="btn btn-emerald btn-xs" data-action="openSendModal" data-args='[${jsonAttr(hr.contact_id)},"WHATSAPP",${isSent},${jsonAttr(ep.address)}]'>${isSent ? 'Resend WA' : 'Send WA'}</button>`
                : `<button class="btn btn-primary btn-xs" data-action="openSendModal" data-args='[${jsonAttr(hr.contact_id)},"EMAIL",${isSent},${jsonAttr(ep.address)}]'>${isSent ? 'Resend Email' : 'Send Email'}</button>`);

            endpointsHtml += `
              <div class="endpoint-box ${epClass}">
                <div class="endpoint-info">
                  <span class="endpoint-label">${escapeHtml(ep.label)} &bull; ${statusBadge}</span>
                  <span class="endpoint-addr">${escapeHtml(ep.address)}</span>
                  ${metaDetails}
                </div>
                <div>${sendBtn}</div>
              </div>
            `;
          });

          // Contact history timeline (from backend attempts)
          const followUpBadge = hr.follow_up_due
            ? `<span class="badge badge-pending-reply" style="font-size: 0.68rem;">⚠️ FOLLOW-UP DUE${hr.follow_up_due_at ? ' ' + formatDate(hr.follow_up_due_at) : ''}</span>`
            : '';
          let historyHtml = '<div style="font-size: 0.75rem; color: var(--text-muted); padding: 0.25rem 0;">No message history yet.</div>';
          if ((hr.history || []).length > 0) {
            historyHtml = (hr.history || []).map(att => {
              const ok = att.status === 'SENT';
              const inRecovery = isRecoveryAttempt(att);
              const icon = ok ? '✓' : (inRecovery ? '⏱' : '✕');
              const color = ok ? 'var(--accent-emerald)' : (inRecovery ? 'var(--accent-amber)' : 'var(--accent-rose)');
              const when = att.completed_at || att.prepared_at;
              return `
                <div style="display: flex; gap: 0.5rem; align-items: flex-start; font-size: 0.72rem; padding: 0.2rem 0; border-bottom: 1px solid var(--border-subtle);">
                  <span style="color: ${color}; font-weight: 700;">${icon}</span>
                  <div style="flex: 1;">
                    <div><strong>${escapeHtml(att.channel)}</strong> ${escapeHtml(att.attempt_type || '')} &rarr; <span style="font-family: 'JetBrains Mono', monospace;">${escapeHtml(att.destination || '')}</span></div>
                    <div style="color: var(--text-muted);">${when ? formatDate(when) : '--'} &bull; ${escapeHtml(att.sender_account_id || 'auto')} &bull; ${escapeHtml(att.template_id || 'Direct')}</div>
                    ${att.failure_detail ? `<div style="color: var(--accent-rose);">${escapeHtml(att.failure_detail)}</div>` : ''}
                  </div>
                </div>
              `;
            }).join('');
          }

          const phoneDisplay = hr.phone || '-';
          const emailDisplay = hr.email || '-';

          hrsHtml += `
            <div class="hr-card" data-contact-id="${escapeHtml(hr.contact_id)}">
              <div class="hr-header">
                <div class="hr-title-wrap">
                  <span class="hr-name-bold">HR ${hrIdx + 1}: ${escapeHtml(hr.name)}</span>
                  ${hr.designation ? `<span class="hr-designation-text">&bull; ${escapeHtml(hr.designation)}</span>` : ''}
                  ${followUpBadge}
                </div>
                <div style="display: flex; align-items: center; gap: 0.5rem;">
                  <span>CRM Status:</span>
                  ${statusSelect}
                  <button class="btn btn-secondary btn-xs" data-action="openHistoryModal" data-args='[${jsonAttr(hr.contact_id)}]'>History</button>
                  <button class="btn btn-outline btn-xs" style="color: var(--accent-rose);" title="Archive / DNC" aria-label="Archive contact" data-action="archiveContact" data-args='[${jsonAttr(hr.contact_id)}]'>🗑</button>
                </div>
              </div>
              <div class="hr-contact-meta" style="font-size: 0.72rem; color: var(--text-secondary); padding: 0.35rem 0.9rem 0; display: flex; gap: 1rem; flex-wrap: wrap;">
                <span>📞 ${escapeHtml(phoneDisplay)}</span>
                <span>✉️ ${escapeHtml(emailDisplay)}</span>
                ${hr.last_activity_at ? `<span>🕒 Last activity: ${formatDate(hr.last_activity_at)}</span>` : ''}
              </div>
              <div class="endpoints-container">
                ${endpointsHtml || '<div style="color: var(--text-muted); font-size: 0.75rem;">No endpoints registered for this contact.</div>'}
              </div>
              <div class="hr-history-block" style="border-top: 1px dashed var(--border-subtle); padding: 0.5rem 0.9rem;">
                <div style="font-size: 0.68rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 0.25rem;">Interaction History</div>
                ${historyHtml}
              </div>
            </div>
          `;
        });

        card.innerHTML = `
          <div class="company-card-header" role="button" tabindex="0" aria-expanded="false" aria-label="Expand or collapse HR contacts for ${escapeHtml(comp.name)}" data-action="toggleCompany" data-args='[${jsonAttr(comp.id)}]' title="Click to expand / collapse HR contacts">
            <div class="company-title-area">
              <span class="company-expand-caret" id="caret-${escapeHtml(comp.id)}">▸</span>
              <span class="company-name-lg">${escapeHtml(comp.name)}</span>
              <span class="company-status-badge ${statusClass}">${statusIcon} ${statusText}</span>
            </div>
            <div class="company-meta-pills">
              <span>HRs: <strong>${comp.total_contacts || 0}</strong></span>
              <span>Endpoints: <strong>${comp.covered_endpoints || 0} / ${comp.total_endpoints || 0} covered</strong></span>
            </div>
          </div>
          <div class="hr-contacts-list" id="hrlist-${escapeHtml(comp.id)}" style="display: none;">
            ${hrsHtml || '<div style="color: var(--text-muted); font-size: 0.8rem; padding: 0.5rem;">No HR contacts registered.</div>'}
          </div>
        `;
        container.appendChild(card);
      });

      renderContactsPagination(hierarchies.length);
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
        const res = await apiFetch('/api/crm/status', {
          method: 'POST',
          body: JSON.stringify({ contact_id: contactId, status: status })
        });
        if (res.ok) {
          showToast(`Contact status updated to ${status}`, 'success');
          await fetchHierarchies();
          await fetchKpis();
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
        const res = await apiFetch(`/api/contacts/${contactId}`, { method: 'DELETE' });
        if (res.ok) {
          showToast('Contact archived and suppressed.', 'info');
          await fetchHierarchies();
          await fetchKpis();
        } else {
          const err = await res.json().catch(() => ({}));
          showToast('Failed to archive contact: ' + (err.detail || res.statusText), 'error');
        }
      } catch (err) {
        showToast('Error archiving contact: ' + err.message, 'error');
      }
    }

    // 6. Campaign Control Plane
    async function fetchCampaigns() {
      try {
        const res = await apiFetch('/api/campaigns');
        if (res.ok) {
          const list = await res.json();
          if (list && list.length > 0) {
            updateCampaignControls(list[0]);
          }
          clearLoadError('campaigns');
        } else {
          reportLoadError('campaigns');
        }
      } catch (err) {
        console.error('Error fetching campaigns:', err);
        reportLoadError('campaigns');
      }
    }

    function updateCampaignControls(camp) {
      state.activeCampaign = camp;
      const statusPill = document.getElementById('campaign-status-pill');
      const actionBtn = document.getElementById('campaign-action-btn');
      const actionLabel = document.getElementById('campaign-action-label');
      const stopBtn = document.getElementById('stop-campaign-btn');
      const limitWrap = document.getElementById('campaign-limit-wrap');
      const limitInput = document.getElementById('campaign-limit-input');

      const st = camp ? camp.status : 'IDLE';
      statusPill.innerText = st;
      statusPill.className = 'campaign-status-badge';

      const banner = document.getElementById('campaign-paused-banner');

      // Single context-aware primary control: Start -> Pause -> Resume.
      if (st === 'RUNNING') {
        statusPill.classList.add('status-running');
        actionBtn.className = 'btn btn-amber btn-sm';
        actionLabel.innerText = '⏸ Pause';
        actionBtn.dataset.action = 'pause';
        actionBtn.disabled = false;
        if (stopBtn) { stopBtn.hidden = false; stopBtn.disabled = false; }
        if (limitWrap) limitWrap.style.display = 'none';
        if (banner) banner.style.display = 'none';
      } else if (st === 'PAUSED') {
        statusPill.classList.add('status-paused');
        actionBtn.className = 'btn btn-primary btn-sm';
        actionLabel.innerText = '▶ Resume';
        actionBtn.dataset.action = 'resume';
        actionBtn.disabled = false;
        if (stopBtn) { stopBtn.hidden = false; stopBtn.disabled = false; }
        if (limitWrap) limitWrap.style.display = 'none';
        if (banner) banner.style.display = 'flex';
      } else {
        if (st === 'COMPLETED') statusPill.classList.add('status-completed');
        else if (st === 'FAILED') statusPill.classList.add('status-failed');
        actionBtn.className = 'btn btn-secondary btn-sm';
        actionLabel.innerText = '▶ New Run';
        actionBtn.dataset.action = 'start';
        actionBtn.disabled = false;
        if (stopBtn) { stopBtn.hidden = true; stopBtn.disabled = true; }
        if (limitWrap) limitWrap.style.display = 'flex';
        if (limitInput) limitInput.disabled = false;
        if (banner) banner.style.display = 'none';
      }

      if (camp) {
        document.getElementById('prog-pct').innerText = `${camp.progress_percent}%`;
        document.getElementById('prog-completed').innerText = camp.completed;
        document.getElementById('prog-pending').innerText = camp.pending;
        document.getElementById('prog-failed').innerText = camp.failed;
        document.getElementById('campaign-progress-fill').style.width = `${camp.progress_percent}%`;

        if (document.getElementById('prog-round')) document.getElementById('prog-round').innerText = `Round ${camp.current_round || 1}`;
        if (document.getElementById('prog-comp-cov')) document.getElementById('prog-comp-cov').innerText = camp.companies_covered || 0;
        if (document.getElementById('prog-comp-rem')) document.getElementById('prog-comp-rem').innerText = camp.companies_remaining || 0;
        if (document.getElementById('prog-curr-channel')) document.getElementById('prog-curr-channel').innerText = camp.current_dispatch_channel || camp.channel || 'WHATSAPP';
        if (document.getElementById('prog-curr-sender')) document.getElementById('prog-curr-sender').innerText = camp.current_sender || 'AUTO';
        if (document.getElementById('prog-curr-template')) document.getElementById('prog-curr-template').innerText = camp.template_used || 'AUTO';
      }
    }

    function onCampaignAction() {
      const btn = document.getElementById('campaign-action-btn');
      const action = btn ? btn.dataset.action : 'start';
      if (action === 'pause') return pauseCampaign();
      if (action === 'resume') return resumeCampaign();
      return startCampaign();
    }

    async function startCampaign() {
      const limitInput = document.getElementById('campaign-limit-input');
      const parsedLimit = limitInput ? parseInt(limitInput.value, 10) : NaN;
      // Clamp to the configured ceiling (the HTML max attribute is advisory only).
      let maxCount = Number.isFinite(parsedLimit) && parsedLimit > 0 ? parsedLimit : APP_CONFIG.defaultOutreachLimit;
      if (maxCount > APP_CONFIG.maxOutreachLimit) {
        maxCount = APP_CONFIG.maxOutreachLimit;
        if (limitInput) limitInput.value = maxCount;
        showToast(`Limit capped at the maximum of ${APP_CONFIG.maxOutreachLimit}.`, 'info');
      }
      const actionBtn = document.getElementById('campaign-action-btn');
      const originalHtml = actionBtn ? actionBtn.innerHTML : '';
      if (actionBtn) {
        actionBtn.disabled = true;
        actionBtn.innerHTML = '<span class="spinner"></span> Starting...';
      }

      try {
        const res = await apiFetch('/api/campaigns/quick-start', {
          method: 'POST',
          body: JSON.stringify({ channel: 'WHATSAPP', max_count: maxCount })
        });
        const data = await res.json();
        if (res.ok) {
          showToast(`Campaign started for up to ${maxCount} contacts!`, 'success');
          updateCampaignControls(data);
        } else {
          const detail = data.detail || {};
          const reason = detail.reason || 'OUTREACH_NOT_READY';
          const msg = detail.message || (typeof detail === 'string' ? detail : 'Prerequisites not met');
          openReadinessModal(reason, msg);
        }
      } catch (err) {
        showToast('Failed to start campaign: ' + err.message, 'error');
      } finally {
        if (actionBtn) {
          actionBtn.innerHTML = originalHtml;
          actionBtn.disabled = false;
        }
      }
    }

    async function pauseCampaign() {
      if (!state.activeCampaign) return;
      try {
        const res = await apiFetch(`/api/campaigns/${state.activeCampaign.id}/pause`, { method: 'POST' });
        if (res.ok) {
          const camp = await res.json();
          showToast('Campaign paused.', 'info');
          updateCampaignControls(camp);
        } else {
          showToast('Failed to pause campaign: ' + await apiErrorText(res), 'error');
        }
      } catch (err) {
        showToast('Error pausing campaign: ' + err.message, 'error');
      }
    }

    async function resumeCampaign() {
      if (!state.activeCampaign) return;
      try {
        const res = await apiFetch(`/api/campaigns/${state.activeCampaign.id}/resume`, { method: 'POST' });
        if (res.ok) {
          const camp = await res.json();
          showToast('Campaign resumed.', 'success');
          updateCampaignControls(camp);
        } else {
          showToast('Failed to resume campaign: ' + await apiErrorText(res), 'error');
        }
      } catch (err) {
        showToast('Error resuming campaign: ' + err.message, 'error');
      }
    }

    async function stopCampaign() {
      if (!state.activeCampaign) return;
      if (!(await appConfirm('Are you sure you want to permanently stop the campaign?', { title: 'Stop campaign', confirmLabel: 'Stop' }))) return;
      try {
        const res = await apiFetch(`/api/campaigns/${state.activeCampaign.id}/stop`, { method: 'POST' });
        if (res.ok) {
          const camp = await res.json();
          showToast('Campaign stopped.', 'info');
          updateCampaignControls(camp);
        } else {
          showToast('Failed to stop campaign: ' + await apiErrorText(res), 'error');
        }
      } catch (err) {
        showToast('Error stopping campaign: ' + err.message, 'error');
      }
    }

    // 7. Manual Send / Resend Modal Actions
    function openSendModal(contactId, channel = 'WHATSAPP', isResend = false, targetEndpoint = null) {
      let contact = null;
      for (const h of state.hierarchies || []) {
        const found = (h.contacts || []).find(c => c.contact_id === contactId);
        if (found) {
          contact = { ...found, company: h.name };
          break;
        }
      }
      if (!contact) return;

      document.getElementById('modal-contact-id').value = contactId;
      document.getElementById('modal-channel').value = channel;
      document.getElementById('modal-is-resend').value = isResend ? 'true' : 'false';

      const title = isResend ? `Resend ${channel} Message` : `Send ${channel} Message`;
      document.getElementById('send-modal-title').innerText = title;

      const previewElem = document.getElementById('modal-recipient-preview');
      if (previewElem) {
        previewElem.innerText = `${contact.name} (${contact.company || contact.company_id || ''})`;
      }

      // Populate Endpoints / Destination dropdown from the hierarchy's per-contact
      // endpoints array (falling back to the raw phone/email string). Preselect the
      // endpoint the operator actually clicked, so per-endpoint sends work.
      const destSelect = document.getElementById('modal-destination-select');
      destSelect.innerHTML = '';

      const channelEndpoints = (contact.endpoints || []).filter(ep => ep.channel === channel);
      const addresses = channelEndpoints.length > 0
        ? channelEndpoints.map(ep => ep.address)
        : String((channel === 'WHATSAPP' ? contact.phone : contact.email) || '')
            .split(',')
            .map(s => s.trim())
            .filter(Boolean);

      if (addresses.length === 0) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = channel === 'WHATSAPP' ? 'No phone number available' : 'No email address available';
        destSelect.appendChild(opt);
      } else {
        addresses.forEach((addr, idx) => {
          const opt = document.createElement('option');
          opt.value = addr;
          opt.textContent = `${channel === 'WHATSAPP' ? 'Phone' : 'Email'} ${idx + 1}: ${addr}`;
          destSelect.appendChild(opt);
        });
        if (targetEndpoint && addresses.includes(targetEndpoint)) {
          destSelect.value = targetEndpoint;
        }
      }

      // Populate Senders dropdown
      const senderSelect = document.getElementById('modal-sender-select');
      senderSelect.innerHTML = '';
      const matchingSenders = state.senders.filter(s => s.channel === channel);
      if (matchingSenders.length === 0) {
        const opt = document.createElement('option');
        opt.value = '';
        opt.textContent = 'Auto-Select Active Sender';
        senderSelect.appendChild(opt);
      } else {
        matchingSenders.forEach(s => {
          const opt = document.createElement('option');
          opt.value = s.id;
          opt.textContent = `${s.id} (${s.display_name}) - ${s.identity} [${s.status}]`;
          senderSelect.appendChild(opt);
        });
      }

      // Populate Templates
      const tplSelect = document.getElementById('modal-template-select');
      tplSelect.innerHTML = '<option value="">Custom Message (No Template)</option>';
      const matchingTpls = state.templates.filter(t => t.channel === channel);
      matchingTpls.forEach(t => {
        const opt = document.createElement('option');
        opt.value = t.id;
        opt.textContent = `${t.id} - ${t.name}`;
        tplSelect.appendChild(opt);
      });

      // Subject field visibility
      const subjGroup = document.getElementById('modal-subject-group');
      if (channel === 'EMAIL') {
        subjGroup.style.display = 'flex';
        document.getElementById('modal-subject-input').value = `Exploring opportunities at ${contact.company || contact.company_id || ''}`;
      } else {
        subjGroup.style.display = 'none';
      }

      // Default body
      const firstName = contact.first_name || (contact.name ? contact.name.split(' ')[0] : 'there');
      document.getElementById('modal-body-input').value = `Hi ${firstName},\n\nI hope you are well. Reaching out regarding software engineering roles at ${contact.company || contact.company_id || ''}.\n\nBest regards,\nCandidate`;

      // Reset attachment (prefilled when a template is selected).
      document.getElementById('modal-attachment-input').value = '';

      document.getElementById('send-modal').classList.add('open');
    }

    async function handleTemplateSelectChange() {
      const tplId = document.getElementById('modal-template-select').value;
      const contactId = document.getElementById('modal-contact-id').value;
      const channel = document.getElementById('modal-channel').value;
      const isResend = document.getElementById('modal-is-resend').value === 'true';
      if (!tplId || !contactId) return;

      // Rendering is owned by the backend so the preview matches the dispatched message.
      try {
        const res = await apiFetch('/api/outreach/preview', {
          method: 'POST',
          body: JSON.stringify({
            contact_id: contactId,
            channel: channel,
            template_id: tplId,
            is_resend: isResend,
          }),
        });
        if (!res.ok) {
          showToast('Failed to render template: ' + (await apiErrorText(res)), 'error');
          return;
        }
        const rendered = await res.json();
        document.getElementById('modal-body-input').value = rendered.body || '';
        if (rendered.subject) {
          document.getElementById('modal-subject-input').value = rendered.subject;
        }
        document.getElementById('modal-attachment-input').value = rendered.attachment_ref || '';
      } catch (err) {
        showToast('Failed to render template: ' + err.message, 'error');
      }
    }

    function closeSendModal() {
      document.getElementById('send-modal').classList.remove('open');
    }

    async function submitSendMessage() {
      const contactId = document.getElementById('modal-contact-id').value;
      const channel = document.getElementById('modal-channel').value;
      const isResend = document.getElementById('modal-is-resend').value === 'true';
      
      const destVal = document.getElementById('modal-destination-select').value;
      const destination = (destVal && destVal.trim() !== '') ? destVal.trim() : null;

      const senderVal = document.getElementById('modal-sender-select').value;
      const senderId = (senderVal && senderVal.trim() !== '') ? senderVal.trim() : null;

      const tplVal = document.getElementById('modal-template-select').value;
      const templateId = (tplVal && tplVal.trim() !== '') ? tplVal.trim() : null;

      const customBody = document.getElementById('modal-body-input').value || null;
      const subject = document.getElementById('modal-subject-input').value || null;
      const attachmentRef = document.getElementById('modal-attachment-input').value.trim() || null;

      if (isResend) {
        if (!(await appConfirm(`This endpoint (${destination || 'contact'}) was already contacted. Are you sure you want to proceed with a manual resend?`, { title: 'Manual resend', confirmLabel: 'Resend', danger: false }))) {
          return;
        }
      }

      let endpoint = '';
      if (channel === 'WHATSAPP') {
        endpoint = isResend ? '/api/outreach/resend-whatsapp' : '/api/outreach/send-whatsapp';
      } else {
        endpoint = isResend ? '/api/outreach/resend-email' : '/api/outreach/send-email';
      }

      const payload = {
        contact_id: contactId,
        destination: destination,
        sender_id: senderId,
        template_id: templateId,
        custom_body: customBody,
        subject: subject,
        attachment_ref: attachmentRef,
      };

      const submitBtn = document.getElementById('modal-submit-send-btn');
      const originalText = submitBtn.innerHTML;
      submitBtn.disabled = true;
      submitBtn.innerHTML = '<span class="spinner"></span> Dispatching...';

      try {
        const res = await apiFetch(endpoint, {
          method: 'POST',
          body: JSON.stringify(payload)
        });
        const data = await res.json();
        if (res.ok && data.success) {
          showToast(`${channel} message dispatched to ${destination || 'contact'}!`, 'success');
          closeSendModal();
          await fetchHierarchies();
          await fetchKpis();
        } else {
          showToast(`Send failed: ${data.failure_detail || data.detail || 'Provider error'}`, 'error');
        }
      } catch (err) {
        showToast('Error dispatching message: ' + err.message, 'error');
      } finally {
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalText;
      }
    }

    // 8. Contact Activity Timeline / History Modal
    async function openHistoryModal(contactId) {
      const modal = document.getElementById('history-modal');
      const timelineList = document.getElementById('history-timeline-list');
      const summaryBox = document.getElementById('history-contact-summary');
      
      modal.classList.add('open');
      timelineList.innerHTML = '<div style="color: var(--text-muted);">Loading activity history...</div>';

      try {
        const res = await apiFetch(`/api/contacts/${contactId}`);
        if (res.ok) {
          const detail = await res.json();
          summaryBox.innerHTML = `<strong>${escapeHtml(detail.name)}</strong> (${escapeHtml(detail.company_name)}) &bull; Phone(s): ${escapeHtml(((detail.phones && detail.phones.length ? detail.phones : (detail.phone ? [detail.phone] : [])).join(', ') || '-'))} &bull; Email(s): ${escapeHtml(((detail.emails && detail.emails.length ? detail.emails : (detail.email ? [detail.email] : [])).join(', ') || '-'))}`;

          timelineList.innerHTML = '';
          const history = detail.history || [];

          if (history.length === 0 && !detail.interested_at) {
            timelineList.innerHTML = '<div style="color: var(--text-muted); font-size: 0.8rem;">No outreach attempts or CRM activities recorded yet.</div>';
            return;
          }

          const events = [];

          history.forEach(h => {
            // Build titles/bodies as plain text; escaping happens once at render time
            // (previously HTML entities/divs were escaped again and shown literally).
            const destStr = h.destination ? ` → ${h.destination}` : '';
            const tplStr = h.template_id ? ` • Template: ${h.template_id}` : '';
            const refStr = h.provider_reference ? ` • Ref: ${h.provider_reference}` : '';
            const attStr = h.attachment ? ` • Attachment: ${h.attachment}` : '';
            const failStr = h.failure_detail ? `Failure: ${h.failure_detail} (${h.failure_code || ''})` : '';

            events.push({
              type: h.channel,
              title: `${h.channel} (${h.attempt_type || 'AUTOMATIC'})${destStr} • Sender: ${h.sender_account_id || 'AUTO'}${tplStr}${refStr}${attStr}`,
              status: h.status,
              body: (h.message_body || '') + (failStr ? '\n' + failStr : ''),
              date: h.completed_at || h.prepared_at,
              dotClass: h.status === 'SENT' ? 'dot-sent' : 'dot-failed'
            });
          });

          if (detail.interested_at) {
            events.push({
              type: 'CRM',
              title: 'CRM - INTERESTED',
              status: 'INTERESTED',
              body: 'Contact marked as Interested.',
              date: detail.interested_at,
              dotClass: 'dot-interested'
            });
          }

          if (detail.reminders) {
            detail.reminders.forEach(r => {
              events.push({
                type: 'REMINDER',
                title: `Follow-up Reminder (${r.status})`,
                status: r.status,
                body: r.reason,
                date: r.due_at,
                dotClass: 'dot-followup'
              });
            });
          }

          events.sort((a, b) => new Date(b.date) - new Date(a.date));

          events.forEach(ev => {
            const item = document.createElement('div');
            item.className = 'timeline-item';
            item.innerHTML = `
              <div class="timeline-dot ${ev.dotClass}"></div>
              <div class="timeline-date">${formatDate(ev.date)}</div>
              <div class="timeline-title">${escapeHtml(ev.title)} <span class="badge ${ev.status === 'SENT' ? 'badge-sent' : 'badge-failed'}">${escapeHtml(ev.status)}</span></div>
              <div class="timeline-body">${escapeHtml(ev.body || '')}</div>
            `;
            timelineList.appendChild(item);
          });
        }
      } catch (err) {
        timelineList.innerHTML = `<div style="color: var(--accent-rose);">Failed to load history: ${err.message}</div>`;
      }
    }

    function closeHistoryModal() {
      document.getElementById('history-modal').classList.remove('open');
    }

    // 9. Senders & Authentication Control Plane (Phase 8.2 & 8.3)
    async function openSendersDrawer() {
      showPage('senders');
      await fetchSenders();
    }

    async function fetchSenders() {
      try {
        const res = await apiFetch('/api/senders');
        if (res.ok) {
          state.senders = await res.json();
          updateHeaderSendersIndicator();
          renderSendersModal();
          updateOnboarding();
          clearLoadError('senders');
        } else {
          reportLoadError('senders');
        }
      } catch (err) {
        console.error('Error loading senders:', err);
        reportLoadError('senders');
      }
    }

    function updateHeaderSendersIndicator() {
      const waSenders = state.senders.filter(s => s.channel === 'WHATSAPP');
      const emSenders = state.senders.filter(s => s.channel === 'EMAIL');
      const activeWa = waSenders.filter(s => s.status === 'ACTIVE').length;
      const activeEm = emSenders.filter(s => s.status === 'ACTIVE').length;

      const waText = document.getElementById('hdr-wa-text');
      const waDot = document.getElementById('hdr-wa-indicator')?.querySelector('.live-dot');
      if (waText) waText.innerText = `WA: ${activeWa} Active`;
      if (waDot) waDot.style.background = activeWa > 0 ? 'var(--accent-emerald)' : 'var(--accent-rose)';

      const emText = document.getElementById('hdr-em-text');
      const emDot = document.getElementById('hdr-em-indicator')?.querySelector('.live-dot');
      if (emText) emText.innerText = `Email: ${activeEm} Active`;
      if (emDot) emDot.style.background = activeEm > 0 ? 'var(--accent-blue)' : 'var(--accent-rose)';

      const waBadge = document.getElementById('wa-active-badge');
      if (waBadge) waBadge.innerText = `${activeWa} of ${waSenders.length} Active`;

      const emBadge = document.getElementById('email-active-badge');
      if (emBadge) emBadge.innerText = `${activeEm} of ${emSenders.length} Active`;

      const readinessText = document.getElementById('overall-readiness-text');
      if (readinessText) {
        if (activeWa > 0 && activeEm > 0) {
          readinessText.innerText = 'All Channels Ready (WhatsApp + Email Active)';
          readinessText.style.color = 'var(--accent-emerald)';
        } else if (activeWa > 0) {
          readinessText.innerText = 'WhatsApp Channel Ready (Email inactive)';
          readinessText.style.color = 'var(--accent-emerald)';
        } else if (activeEm > 0) {
          readinessText.innerText = 'Email Channel Ready (WhatsApp unauthenticated)';
          readinessText.style.color = 'var(--accent-blue)';
        } else {
          readinessText.innerText = 'Not Ready (No active senders)';
          readinessText.style.color = 'var(--accent-rose)';
        }
      }

      // Overall readiness pill in the header, synced with the drawer verdict above.
      const hdrReadinessText = document.getElementById('hdr-readiness-text');
      const hdrReadinessDot = document.getElementById('hdr-readiness-dot');
      if (hdrReadinessText && hdrReadinessDot) {
        let label, color;
        if (activeWa > 0 && activeEm > 0) {
          label = 'All ready';
          color = 'var(--accent-emerald)';
        } else if (activeWa > 0 || activeEm > 0) {
          label = 'Partially ready';
          color = 'var(--accent-amber)';
        } else {
          label = 'Senders needed';
          color = 'var(--accent-rose)';
        }
        hdrReadinessText.innerText = label;
        hdrReadinessText.style.color = color;
        hdrReadinessDot.style.background = color;
      }
    }

    function renderSendersModal() {
      const waContainer = document.getElementById('wa-senders-list-container');
      const emContainer = document.getElementById('email-senders-list-container');
      if (!waContainer || !emContainer) return;

      const waSenders = state.senders.filter(s => s.channel === 'WHATSAPP');
      const emSenders = state.senders.filter(s => s.channel === 'EMAIL');

      // 1. Render WhatsApp Sessions
      waContainer.innerHTML = '';
      if (waSenders.length === 0) {
        waContainer.innerHTML = '<div style="color: var(--text-muted); font-size: 0.8rem;">No WhatsApp sessions configured. Click "+ Add WhatsApp Session" to create one.</div>';
      } else {
        waSenders.forEach(s => {
          const card = document.createElement('div');
          card.className = 'kpi-card';
          card.style.padding = '0.85rem';
          card.style.display = 'flex';
          card.style.flexDirection = 'column';
          card.style.gap = '0.5rem';

          let statusBadgeClass = 'badge-failed';
          if (s.status === 'ACTIVE') statusBadgeClass = 'badge-sent';
          else if (s.status === 'AUTHENTICATING') statusBadgeClass = 'badge-partial';
          else if (s.status === 'QR_REQUIRED') statusBadgeClass = 'badge-partial';
          else if (s.status === 'AUTH_REQUIRED' || s.status === 'NOT_CONFIGURED') statusBadgeClass = 'badge-uncovered';
          else if (s.status === 'INACTIVE') statusBadgeClass = 'badge-not-sent';

          const errorHtml = s.error_message ? `<div style="color: var(--accent-rose); font-size: 0.7rem;">⚠️ ${escapeHtml(s.error_message)}</div>` : '';
          const lastCheckedHtml = s.last_checked ? `<div style="font-size: 0.675rem; color: var(--text-muted);">Last verified: ${formatDate(s.last_checked)}</div>` : '';
          const lastUsedHtml = s.last_used_at ? `<div style="font-size: 0.675rem; color: var(--text-secondary);">Last used: ${formatDate(s.last_used_at)}</div>` : '';

          let actionButtons = '';
          if (s.status === 'INACTIVE') {
            actionButtons = `
              <button class="btn btn-outline btn-xs" data-action="reactivateSender" data-args='[${jsonAttr(s.id)}]' title="Reactivate sender into rotation">
                <span>⚡ Reactivate</span>
              </button>
            `;
          } else {
            actionButtons = `
              <button class="btn btn-emerald btn-xs" data-action="startWhatsAppAuth" data-args='[${jsonAttr(s.id)}]' title="Re-authenticate this WhatsApp sender (re-scan QR)">
                <span>📱 Re-Authenticate</span>
              </button>
              <button class="btn btn-outline btn-xs" data-action="checkWhatsAppAuthStatus" data-args='[${jsonAttr(s.id)}]' title="Check connection health">
                <span>🔍 Status</span>
              </button>
              <button class="btn btn-amber btn-xs" data-action="deactivateSender" data-args='[${jsonAttr(s.id)}]' title="Deactivate and pause from rotation (history preserved)">
                <span>⏸️ Deactivate</span>
              </button>
            `;
          }

          card.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: flex-start;">
              <div>
                <strong style="color: var(--text-primary);">${escapeHtml(s.display_name)}</strong>
                <div style="font-size: 0.725rem; font-family: monospace; color: var(--text-secondary);">${escapeHtml(s.id)}</div>
                <div style="font-size: 0.75rem; color: var(--accent-blue);">${escapeHtml(s.identity)}</div>
              </div>
              <span class="badge ${statusBadgeClass}">${escapeHtml(s.status)}</span>
            </div>
            <div style="font-size: 0.7rem; color: var(--text-muted);">
              Limit: ${s.daily_limit == null ? 'Unlimited' : s.daily_limit}/day &bull; Rate: ${s.hourly_limit == null ? 'Unlimited' : s.hourly_limit}/hr
            </div>
            ${errorHtml}
            ${lastCheckedHtml}
            ${lastUsedHtml}
            <div style="display: flex; gap: 0.35rem; margin-top: auto; padding-top: 0.35rem; border-top: 1px solid var(--border-subtle); flex-wrap: wrap;">
              ${actionButtons}
            </div>
          `;
          waContainer.appendChild(card);
        });
      }

      // 2. Render Email Sessions
      emContainer.innerHTML = '';
      if (emSenders.length === 0) {
        emContainer.innerHTML = '<div style="color: var(--text-muted); font-size: 0.8rem;">No Email senders configured. Click "+ Add Email Sender" to add one.</div>';
      } else {
        emSenders.forEach(s => {
          const card = document.createElement('div');
          card.className = 'kpi-card';
          card.style.padding = '0.85rem';
          card.style.display = 'flex';
          card.style.flexDirection = 'column';
          card.style.gap = '0.5rem';

          let statusBadgeClass = 'badge-failed';
          if (s.status === 'ACTIVE') statusBadgeClass = 'badge-sent';
          else if (s.status === 'INACTIVE') statusBadgeClass = 'badge-not-sent';
          else if (s.status === 'AUTH_REQUIRED') statusBadgeClass = 'badge-uncovered';

          const errorHtml = s.error_message ? `<div style="color: var(--accent-rose); font-size: 0.7rem;">⚠️ ${escapeHtml(s.error_message)}</div>` : '';
          const lastUsedHtml = s.last_used_at ? `<div style="font-size: 0.675rem; color: var(--text-secondary);">Last used: ${formatDate(s.last_used_at)}</div>` : '';

          let actionButtons = '';
          if (s.status === 'INACTIVE') {
            actionButtons = `
              <button class="btn btn-outline btn-xs" data-action="reactivateSender" data-args='[${jsonAttr(s.id)}]' title="Reactivate sender">
                <span>⚡ Reactivate</span>
              </button>
            `;
          } else {
            actionButtons = `
              <button class="btn btn-primary btn-xs" data-action="openEmailConfigModal" data-args='[${jsonAttr(s.id)}]'>
                <span>⚙️ Re-configure</span>
              </button>
              <button class="btn btn-outline btn-xs" data-action="verifyEmailSender" data-args='[${jsonAttr(s.id)}]'>
                <span>🔌 Test &amp; Verify</span>
              </button>
              <button class="btn btn-amber btn-xs" data-action="deactivateSender" data-args='[${jsonAttr(s.id)}]' title="Deactivate and pause from rotation (history preserved)">
                <span>⏸️ Deactivate</span>
              </button>
            `;
          }

          card.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: flex-start;">
              <div>
                <strong style="color: var(--text-primary);">${escapeHtml(s.display_name)}</strong>
                <div style="font-size: 0.725rem; font-family: monospace; color: var(--text-secondary);">${escapeHtml(s.id)}</div>
                <div style="font-size: 0.75rem; color: var(--accent-blue);">${escapeHtml(s.identity)}</div>
              </div>
              <span class="badge ${statusBadgeClass}">${escapeHtml(s.status)}</span>
            </div>
            ${errorHtml}
            ${lastUsedHtml}
            <div style="font-size: 0.7rem; color: var(--text-muted);">
              Limit: ${s.daily_limit == null ? 'Unlimited' : s.daily_limit}/day &bull; Rate: ${s.hourly_limit == null ? 'Unlimited' : s.hourly_limit}/hr
            </div>
            <div style="display: flex; gap: 0.35rem; margin-top: auto; padding-top: 0.35rem; border-top: 1px solid var(--border-subtle); flex-wrap: wrap;">
              ${actionButtons}
            </div>
          `;
          emContainer.appendChild(card);
        });
      }
    }


    async function addWhatsAppSession() {
      try {
        showToast('Launching WhatsApp authentication...', 'info');
        const res = await apiFetch('/api/senders/whatsapp/add', {
          method: 'POST',
          body: JSON.stringify({}),
        });
        if (res.ok) {
          const data = await res.json();
          const tempId = data.id;
          // Start QR auth immediately (launches the Camoufox browser + captures QR).
          await startWhatsAppAuth(tempId);
        } else {
          const err = await res.json();
          showToast(`Failed to start WhatsApp session: ${err.detail || 'Error'}`, 'error');
        }
      } catch (err) {
        showToast('Error starting WhatsApp session: ' + err.message, 'error');
      }
    }

    // ---- WhatsApp QR authentication modal ----
    // Used for both brand-new sessions (temp id) and re-authentication of an existing
    // sender (real wa_<phone> id). Polls auth/status until logged in, errored, or expired.
    async function openWhatsAppQrModal(senderId, existingName) {
      const modal = document.getElementById('wa-qr-modal');
      if (!modal) return;
      document.getElementById('wa-qr-title').textContent = 'Link WhatsApp Device';
      document.getElementById('wa-qr-subtitle').textContent = existingName
        ? `Re-authenticating ${existingName}`
        : 'Waiting for QR code...';
      document.getElementById('wa-qr-img').style.display = 'none';
      document.getElementById('wa-qr-status').innerHTML =
        '<span class="spinner"></span> Launching secure browser...';
      modal.classList.add('open');
      modal._waPollId = senderId;
      modal._waStopped = false;
      modal._waLastStatus = null;
      await pollWhatsAppAuth(senderId, existingName);
    }

    function closeWhatsAppQrModal() {
      const modal = document.getElementById('wa-qr-modal');
      if (!modal) return;
      modal._waStopped = true;
      modal.classList.remove('open');
    }

    async function pollWhatsAppAuth(senderId, existingName) {
      const modal = document.getElementById('wa-qr-modal');
      if (!modal || modal._waStopped || modal._waPollId !== senderId) return;

      let res;
      try {
        res = await apiFetch(`/api/senders/whatsapp/${senderId}/auth/status`);
      } catch (err) {
        // Network error: keep polling.
        setTimeout(() => pollWhatsAppAuth(senderId, existingName), 2000);
        return;
      }

      if (!res.ok) {
        // Strict 404 -> temp expired / cleaned up / unknown.
        if (res.status === 404) {
          document.getElementById('wa-qr-status').innerHTML =
            '<span style="color: var(--accent-rose);">Session expired. Please try again.</span>';
          modal._waStopped = true;
          setTimeout(() => closeWhatsAppQrModal(), 1500);
          showToast('Authentication session expired.', 'error');
        } else {
          try {
            const err = await res.json();
            document.getElementById('wa-qr-status').innerHTML =
              `<span style="color: var(--accent-rose);">${escapeHtml(err.detail || 'Unknown error')}</span>`;
          } catch (e) {
            document.getElementById('wa-qr-status').innerHTML = '<span style="color: var(--accent-rose);">Unknown error</span>';
          }
          modal._waStopped = true;
          setTimeout(() => closeWhatsAppQrModal(), 2000);
        }
        await fetchSenders();
        return;
      }

      const data = await res.json();
      modal._waLastStatus = data.status;
      const statusEl = document.getElementById('wa-qr-status');
      const imgEl = document.getElementById('wa-qr-img');

      if (data.status === 'ACTIVE') {
        imgEl.style.display = 'none';
        statusEl.innerHTML = '<span style="color: var(--accent-emerald); font-weight: 700;">✓ WhatsApp linked successfully!</span>';
        modal._waStopped = true;
        setTimeout(() => closeWhatsAppQrModal(), 1200);
        showToast('WhatsApp session authenticated and saved!', 'success');
        await fetchSenders();
        return;
      }

      if (data.status === 'QR_REQUIRED' && data.qr_code) {
        imgEl.src = data.qr_code;
        imgEl.style.display = 'block';
        statusEl.innerHTML = 'Scan with WhatsApp &gt; Linked Devices &gt; Link a Device';
      } else if (data.status === 'AUTHENTICATING') {
        imgEl.style.display = 'none';
        statusEl.innerHTML = '<span class="spinner"></span> Launching authentication context...';
      } else if (data.status === 'ERROR') {
        imgEl.style.display = 'none';
        statusEl.innerHTML = `<span style="color: var(--accent-rose);">${escapeHtml(data.error_message || 'Authentication failed')}</span>`;
        modal._waStopped = true;
        setTimeout(() => closeWhatsAppQrModal(), 2500);
        showToast(`Authentication failed: ${data.error_message || 'Error'}`, 'error');
        await fetchSenders();
        return;
      } else if (data.status === 'AUTH_REQUIRED') {
        imgEl.style.display = 'none';
        statusEl.innerHTML = '<span style="color: var(--accent-amber);">Waiting for QR code...</span>';
      }

      // Continue polling.
      setTimeout(() => pollWhatsAppAuth(senderId, existingName), 2000);
    }

    async function addEmailSession() {
      // Immediately open the SMTP configuration form. There is no intermediate dummy row.
      openEmailConfigModal('');
    }

    async function deactivateSender(senderId) {
      try {
        const res = await apiFetch(`/api/senders/${senderId}/deactivate`, { method: 'POST' });
        if (res.ok) {
          showToast(`Sender ${senderId} deactivated (paused from rotation).`, 'info');
          await fetchSenders();
        } else {
          const err = await res.json();
          showToast(`Failed: ${err.detail || 'Error'}`, 'error');
        }
      } catch (err) {
        showToast('Error deactivating sender: ' + err.message, 'error');
      }
    }

    async function reactivateSender(senderId) {
      try {
        const res = await apiFetch(`/api/senders/${senderId}/reactivate`, { method: 'POST' });
        if (res.ok) {
          showToast(`Sender ${senderId} reactivated.`, 'success');
          await fetchSenders();
        } else {
          const err = await res.json();
          showToast(`Failed: ${err.detail || 'Error'}`, 'error');
        }
      } catch (err) {
        showToast('Error reactivating sender: ' + err.message, 'error');
      }
    }

    async function startWhatsAppAuth(senderId) {
      try {
        showToast(`Starting authentication for ${senderId}...`, 'info');
        const res = await apiFetch(`/api/senders/whatsapp/${senderId}/auth/start`, {
          method: 'POST',
        });
        if (res.ok) {
          const existing = state.senders.find(s => s.id === senderId);
          openWhatsAppQrModal(senderId, existing ? existing.display_name : senderId);
        } else {
          const err = await res.json();
          showToast(`Auth error: ${err.detail || 'Failed'}`, 'error');
        }
      } catch (err) {
        showToast('Error starting authentication: ' + err.message, 'error');
      }
    }

    async function checkWhatsAppAuthStatus(senderId) {
      try {
        showToast(`Probing live session ${senderId}...`, 'info');
        const res = await apiFetch(`/api/senders/whatsapp/${senderId}/auth/check`, { method: 'POST' });
        if (res.ok) {
          const data = await res.json();
          if (data.status_changed) {
            showToast(`Session ${senderId}: ${data.probe} — status updated to ${data.status}`, 'info');
          } else {
            showToast(`Session ${senderId} status: ${data.status}`, data.status === 'ACTIVE' ? 'success' : 'info');
          }
          await fetchSenders();
        } else {
          const err = await res.json().catch(() => ({}));
          showToast(`Health check failed: ${err.detail || 'Error'}`, 'error');
        }
      } catch (err) {
        showToast('Error checking status: ' + err.message, 'error');
      }
    }

    // Email Configuration & Verification Modals
    function openEmailConfigModal(senderId = '') {
      const existing = senderId ? state.senders.find(s => s.id === senderId) : null;
      document.getElementById('email-config-title').textContent =
        existing ? `Configure ${existing.display_name}` : 'Add Email Sender';
      // For a brand-new session, leave the id blank so the backend auto-assigns EMAIL_SESSION_N.
      document.getElementById('cfg-email-id').value = existing ? existing.id : '';
      document.getElementById('cfg-email-name').value = existing ? existing.display_name : '';
      document.getElementById('cfg-email-addr').value = existing ? existing.identity : '';
      document.getElementById('cfg-email-host').value = '';
      document.getElementById('cfg-email-port').value = 587;
      document.getElementById('cfg-email-user').value = existing ? existing.identity : '';
      document.getElementById('cfg-email-pwd').value = '';
      document.getElementById('email-config-modal').classList.add('open');
    }

    function closeEmailConfigModal() {
      document.getElementById('email-config-modal').classList.remove('open');
    }

    async function saveAndVerifyEmailConfig() {
      const id = document.getElementById('cfg-email-id').value.trim();
      const name = document.getElementById('cfg-email-name').value.trim();
      const addr = document.getElementById('cfg-email-addr').value.trim();
      const host = document.getElementById('cfg-email-host').value.trim();
      const port = parseInt(document.getElementById('cfg-email-port').value, 10) || 587;
      const user = document.getElementById('cfg-email-user').value.trim() || addr;
      const pwd = document.getElementById('cfg-email-pwd').value;

      const saveBtn = document.getElementById('save-email-cfg-btn');
      const origText = saveBtn.innerHTML;
      saveBtn.disabled = true;
      saveBtn.innerHTML = '<span class="spinner"></span> Verifying...';

      try {
        const res = await apiFetch('/api/senders/email/configure', {
          method: 'POST',
          body: JSON.stringify({
            id: id,
            identity: addr,
            display_name: name,
            host: host,
            port: port,
            user: user,
            password: pwd,
            verify_now: true,
          })
        });
        const data = await res.json();
        if (res.ok) {
          showToast(`Email sender ${data.id} verified and marked ACTIVE!`, 'success');
          closeEmailConfigModal();
          await fetchSenders();
        } else {
          // Connection failed -> backend saved nothing.
          showToast(`Verification failed: ${data.detail || 'SMTP connection error'}`, 'error');
        }
      } catch (err) {
        showToast('Error saving email config: ' + err.message, 'error');
      } finally {
        saveBtn.disabled = false;
        saveBtn.innerHTML = origText;
      }
    }

    async function verifyEmailSender(senderId) {
      try {
        showToast(`Testing connection for ${senderId}...`, 'info');
        const res = await apiFetch(`/api/senders/email/${senderId}/verify`, { method: 'POST' });
        const data = await res.json();
        if (res.ok && data.verified) {
          showToast(`Email sender ${senderId} verified successfully!`, 'success');
        } else {
          showToast(`Verification failed: ${data.error_message || 'SMTP Connection Error'}`, 'error');
        }
        await fetchSenders();
      } catch (err) {
        showToast('Error verifying email: ' + err.message, 'error');
      }
    }

    // Outreach Readiness Modal
    function openReadinessModal(reason, detail) {
      const modal = document.getElementById('readiness-modal');
      document.getElementById('readiness-error-reason').innerText = reason || 'OUTREACH_NOT_READY';
      document.getElementById('readiness-error-detail').innerText = detail || 'Prerequisites for automated outreach are not satisfied.';
      modal.classList.add('open');
    }

    function closeReadinessModal() {
      document.getElementById('readiness-modal').classList.remove('open');
    }

    // 10. Templates Management Drawer
    async function fetchTemplates() {
      try {
        const res = await apiFetch('/api/templates');
        if (res.ok) {
          state.templates = await res.json();
          clearLoadError('templates');
        } else {
          reportLoadError('templates');
        }
      } catch (err) {
        console.error('Error loading templates:', err);
        reportLoadError('templates');
      }
    }

    async function openTemplatesDrawer() {
      showPage('templates');
      if (!state.templates || state.templates.length === 0) await fetchTemplates();
      renderTemplatesList();
    }

    function renderTemplatesList() {
      const container = document.getElementById('templates-list-container');
      container.innerHTML = '';

      if (!state.templates || state.templates.length === 0) {
        container.innerHTML = `
          <div style="text-align: center; padding: 2rem; color: var(--text-secondary);">
            <div style="font-size: 1.5rem; margin-bottom: 0.5rem;" aria-hidden="true">📝</div>
            <div style="font-weight: 600; color: var(--text-primary);">No templates yet</div>
            <div style="font-size: 0.8rem; margin-top: 0.25rem;">Add your first message template to start outreach.</div>
          </div>`;
        return;
      }

      state.templates.forEach(t => {
        const card = document.createElement('div');
        card.className = 'kpi-card';
        card.innerHTML = `
          <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem;">
            <strong>${escapeHtml(t.id)} - ${escapeHtml(t.name)}</strong>
            <div style="display: flex; align-items: center; gap: 0.5rem;">
              <span class="badge badge-sent">${escapeHtml(t.channel)}</span>
              <button class="btn btn-outline btn-xs" data-action="openTemplateFormById" data-args='[${jsonAttr(t.id)}]'>Edit</button>
            </div>
          </div>
          ${t.subject ? `<div style="font-size: 0.75rem; color: var(--accent-blue); font-weight: 600;">Subject: ${escapeHtml(t.subject)}</div>` : ''}
          <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.3rem;">Resume: ${t.attachment_ref ? escapeHtml(t.attachment_ref) : 'not set'}</div>
          <div style="font-size: 0.75rem; color: var(--text-secondary); background: var(--bg-surface-raised); padding: 0.45rem; border-radius: var(--radius-xs); white-space: pre-wrap; font-family: 'JetBrains Mono', monospace;">${escapeHtml(t.body)}</div>
        `;
        container.appendChild(card);
      });
    }

    // Template Add/Edit Inline Form (shared for create and edit)
    let editingTemplate = null;

    function renderTemplateForm(template) {
      editingTemplate = template || null;
      const isEdit = !!template;
      document.getElementById('template-form-title').innerText = isEdit ? 'Edit Template' : 'Add Template';
      document.getElementById('tpl-id').value = template ? (template.id || '') : '';
      document.getElementById('tpl-id').readOnly = isEdit;
      document.getElementById('tpl-name').value = template ? (template.name || '') : '';
      document.getElementById('tpl-channel').disabled = isEdit;
      document.getElementById('tpl-channel').value = (template && template.channel) ? template.channel : 'WHATSAPP';
      document.getElementById('tpl-subject').value = template ? (template.subject || '') : '';
      document.getElementById('tpl-body').value = template ? (template.body || '') : '';
      document.getElementById('tpl-attachment-ref').value = template ? (template.attachment_ref || '') : '';
    }

    function openTemplateFormById(templateId) {
      const t = state.templates.find(x => x.id === templateId);
      openTemplateForm(t || null);
    }

    function openTemplateForm(template) {
      renderTemplateForm(template);
      document.getElementById('template-form-container').style.display = 'flex';
    }

    function closeTemplateForm() {
      document.getElementById('template-form-container').style.display = 'none';
    }

    async function saveTemplate() {
      const id = document.getElementById('tpl-id').value.trim();
      const name = document.getElementById('tpl-name').value.trim();
      const channel = document.getElementById('tpl-channel').value;
      const subject = document.getElementById('tpl-subject').value.trim();
      const body = document.getElementById('tpl-body').value;
      const attachmentRef = document.getElementById('tpl-attachment-ref').value.trim();

      if (!id || !name || !body) {
        showToast('Template ID, Name and Body are required.', 'error');
        return;
      }

      const isEdit = !!editingTemplate;
      let payload;
      if (isEdit) {
        payload = {};
        if (name !== (editingTemplate.name || '')) payload.name = name;
        if (subject !== (editingTemplate.subject || '')) payload.subject = subject;
        if (body !== (editingTemplate.body || '')) payload.body = body;
        if (attachmentRef !== (editingTemplate.attachment_ref || '')) payload.attachment_ref = attachmentRef;
      } else {
        payload = { id: id, name: name, channel: channel, body: body, subject: subject, attachment_ref: attachmentRef };
      }

      try {
        showToast(isEdit ? `Saving template ${id}...` : 'Creating template...', 'info');
        const res = await apiFetch(isEdit ? `/api/templates/${encodeURIComponent(editingTemplate.id)}` : '/api/templates', {
          method: isEdit ? 'PUT' : 'POST',
          body: JSON.stringify(payload),
        });
        if (res.ok) {
          showToast(`Template ${id} saved.`, 'success');
          closeTemplateForm();
          await fetchTemplates();
          await openTemplatesDrawer();
        } else {
          const err = await res.json();
          showToast(`Failed to save template: ${err.detail || 'Error'}`, 'error');
        }
      } catch (err) {
        showToast('Error saving template: ' + err.message, 'error');
      }
    }

    // 11. Source Synchronization
    async function triggerSync() {
      const syncBtn = document.getElementById('sync-btn');
      syncBtn.disabled = true;
      syncBtn.innerText = 'Syncing...';

      try {
        const res = await apiFetch('/api/sync', { method: 'POST', body: '{}' });
        if (!res.ok) {
          showToast('Sync failed: ' + (await apiErrorText(res)), 'error');
          return;
        }
        const summary = await res.json();
          if (document.getElementById('sync-source-filename')) document.getElementById('sync-source-filename').innerText = summary.source_file || 'External Source';
          document.getElementById('sync-total').innerText = summary.total_read || 0;
          if (document.getElementById('sync-new-comp')) document.getElementById('sync-new-comp').innerText = summary.new_companies || 0;
          if (document.getElementById('sync-upd-comp')) document.getElementById('sync-upd-comp').innerText = summary.updated_companies || 0;
          document.getElementById('sync-new').innerText = summary.new_contacts || 0;
          document.getElementById('sync-updated').innerText = summary.updated_contacts || 0;
          document.getElementById('sync-unchanged').innerText = summary.unchanged_contacts || 0;
          if (document.getElementById('sync-new-phones')) document.getElementById('sync-new-phones').innerText = summary.new_phone_endpoints || 0;
          if (document.getElementById('sync-new-emails')) document.getElementById('sync-new-emails').innerText = summary.new_email_endpoints || 0;
          if (document.getElementById('sync-skipped')) document.getElementById('sync-skipped').innerText = summary.skipped_invalid || 0;

          const errBox = document.getElementById('sync-errors-box');
          const errList = document.getElementById('sync-errors-list');
          errList.innerHTML = '';
          if (summary.errors && summary.errors.length > 0) {
            errBox.style.display = 'block';
            summary.errors.forEach(e => {
              const li = document.createElement('li');
              li.textContent = e;
              errList.appendChild(li);
            });
          } else {
            errBox.style.display = 'none';
          }

          document.getElementById('sync-modal').classList.add('open');
          showToast('Source synchronization completed! History preserved.', 'success');
          await loadInitialData();
      } catch (err) {
        showToast('Sync error: ' + err.message, 'error');
      } finally {
        syncBtn.disabled = false;
        syncBtn.innerText = '🔄 Sync Contacts';
      }
    }

    function closeSyncModal() {
      document.getElementById('sync-modal').classList.remove('open');
    }

    // ---- Initial/background load failure reporting ----
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
        fetchKpis(),
        fetchCompanies(),
        fetchHierarchies(),
        fetchCampaigns(),
        fetchSenders(),
        fetchTemplates(),
      ]);
    }

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
        if (work.kpis) tasks.push(fetchKpis());
        if (work.hierarchies) tasks.push(fetchHierarchies());
        if (work.campaigns) tasks.push(fetchCampaigns());
        if (work.companies) tasks.push(fetchCompanies());
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

      addActivityItem(describeLiveActivity(type, p), ev.occurred_at);

      if (type === 'SENDER_STATUS_CHANGED') {
        await fetchSenders();
        if (p.sender_id && p.status) {
          showToast(`Sender ${p.sender_id} is now ${p.status}`, p.status === 'ACTIVE' ? 'success' : 'info');
        }
      } else if (type === 'SENDER_QR_RECEIVED') {
        await fetchSenders();
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

    // Filter Helpers
    let searchDebounce = null;
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

    // Utility Helpers
    function formatDate(dateStr) {
      if (!dateStr) return '';
      try {
        const d = new Date(dateStr);
        const day = d.getDate().toString().padStart(2, '0');
        const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        const month = months[d.getMonth()];
        const year = d.getFullYear();
        const hours = d.getHours().toString().padStart(2, '0');
        const minutes = d.getMinutes().toString().padStart(2, '0');
        return `${day} ${month} ${year} ${hours}:${minutes}`;
      } catch {
        return dateStr;
      }
    }


    function showToast(message, type = 'info') {
      const container = document.getElementById('toast-container');
      const toast = document.createElement('div');
      toast.className = `toast toast-${type}`;
      
      let icon = 'ℹ️';
      if (type === 'success') icon = '✓';
      if (type === 'error') icon = '✕';

      toast.innerHTML = `<span>${icon}</span><span>${escapeHtml(message)}</span>`;
      container.appendChild(toast);

      setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transition = 'opacity 0.3s ease';
        setTimeout(() => toast.remove(), 300);
      }, 4000);
    }

    // Promise-based styled confirmation, replacing blocking native confirm().
    let confirmResolver = null;

    function appConfirm(message, opts = {}) {
      document.getElementById('confirm-modal-message').textContent = message;
      document.getElementById('confirm-modal-title').textContent = opts.title || 'Confirm action';
      const okBtn = document.getElementById('confirm-modal-ok');
      okBtn.textContent = opts.confirmLabel || 'Confirm';
      okBtn.className = 'btn ' + (opts.danger === false ? 'btn-primary' : 'btn-rose');
      document.getElementById('confirm-modal').classList.add('open');
      return new Promise((resolve) => {
        confirmResolver = resolve;
      });
    }

    function resolveConfirm(result) {
      document.getElementById('confirm-modal').classList.remove('open');
      if (confirmResolver) {
        const resolve = confirmResolver;
        confirmResolver = null;
        resolve(result);
      }
    }
  
    // Global Escape-to-close: dismiss the topmost open dialog.
    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape') return;
      const openOverlays = document.querySelectorAll('.modal-overlay.open');
      if (openOverlays.length === 0) return;
      openOverlays[openOverlays.length - 1].classList.remove('open');
    });

    // ---- Modal focus management: initial focus, focus trap, restore, scroll lock ----
    let currentTopModal = null;
    let modalReturnFocus = null;

    function openModals() {
      return Array.from(document.querySelectorAll('.modal-overlay.open'));
    }

    function modalFocusables(modal) {
      const selector = 'a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
      return Array.from(modal.querySelectorAll(selector)).filter((el) => el.offsetParent !== null);
    }

    function syncModalState() {
      const modals = openModals();
      document.body.style.overflow = modals.length > 0 ? 'hidden' : '';
      const top = modals.length > 0 ? modals[modals.length - 1] : null;
      if (top === currentTopModal) return;
      if (top) {
        modalReturnFocus = document.activeElement;
        const focusables = modalFocusables(top);
        if (focusables.length > 0) focusables[0].focus();
        else {
          top.setAttribute('tabindex', '-1');
          top.focus();
        }
      } else if (modalReturnFocus && typeof modalReturnFocus.focus === 'function') {
        modalReturnFocus.focus();
      }
      currentTopModal = top;
    }

    new MutationObserver(syncModalState).observe(document.body, {
      subtree: true,
      attributes: true,
      attributeFilter: ['class'],
    });

    document.addEventListener('keydown', (event) => {
      if (event.key !== 'Tab') return;
      const modals = openModals();
      if (modals.length === 0) return;
      const top = modals[modals.length - 1];
      const focusables = modalFocusables(top);
      if (focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });

    function openRecoveryDrawer() {
      document.getElementById('recovery-modal').classList.add('open');
      fetchRecoveryQueue();
    }
    function closeRecoveryDrawer() {
      document.getElementById('recovery-modal').classList.remove('open');
    }
    async function fetchRecoveryQueue() {
      const container = document.getElementById('recovery-list-container');
      container.innerHTML = '<div style="color:var(--text-secondary);font-size:0.85rem;">Loading recovery queue...</div>';
      try {
        const res = await apiFetch('/api/outreach/recovery');
        if (!res.ok) {
          container.innerHTML = '<div style="color:var(--accent-rose);font-size:0.85rem;">Error loading recovery queue: ' + escapeHtml(await apiErrorText(res)) + '</div>';
          return;
        }
        const items = await res.json();
        if (!items || items.length === 0) {
          container.innerHTML = '<div style="color:var(--accent-emerald);font-size:0.85rem;">&#10003; Recovery queue is empty. Nothing needs operator review.</div>';
          return;
        }
        container.innerHTML = '<div style="font-size:0.8rem;color:var(--text-secondary);">' + items.length + ' attempt(s) need operator review.</div>' + items.map(function (it) {
          return '<div style="border:1px solid var(--border-medium);border-left:3px solid var(--accent-amber);border-radius:var(--radius-sm);padding:0.6rem 0.75rem;">'
            + '<div style="font-weight:600;font-size:0.82rem;">' + escapeHtml(it.contact_name || 'Unknown contact') + ' <span style="color:var(--text-secondary);font-weight:400;">@ ' + escapeHtml(it.company || '') + '</span></div>'
            + '<div style="font-size:0.75rem;color:var(--text-secondary);margin-top:0.15rem;"><strong>' + escapeHtml(it.channel || '') + '</strong> &rarr; <span style="font-family:monospace;">' + escapeHtml(it.destination || '') + '</span> &bull; <span class="badge badge-recovery">' + escapeHtml(it.status || '') + '</span>' + (it.prepared_at ? ' &bull; ' + formatDate(it.prepared_at) : '') + '</div>'
            + (it.failure_detail ? '<div style="font-size:0.75rem;color:var(--accent-rose);margin-top:0.15rem;">' + escapeHtml(it.failure_detail) + '</div>' : '')
            + '<div style="display:flex;gap:0.4rem;margin-top:0.5rem;flex-wrap:wrap;">'
            + '<button class="btn btn-emerald btn-xs" data-action="resolveRecoveryAttempt" data-args=\'[' + jsonAttr(it.id) + ',"mark_sent"]\'>Confirm sent</button>'
            + '<button class="btn btn-primary btn-xs" data-action="resolveRecoveryAttempt" data-args=\'[' + jsonAttr(it.id) + ',"retry"]\'>Retry</button>'
            + '<button class="btn btn-outline btn-xs" style="color:var(--accent-rose);" data-action="resolveRecoveryAttempt" data-args=\'[' + jsonAttr(it.id) + ',"cancel"]\'>Cancel</button>'
            + '</div></div>';
        }).join('');
      } catch (err) {
        container.innerHTML = '<div style="color:var(--accent-rose);font-size:0.85rem;">Error loading recovery queue: ' + escapeHtml(err.message) + '</div>';
      }
    }
    async function resolveRecoveryAttempt(attemptId, action) {
      try {
        const res = await apiFetch('/api/outreach/recovery/' + encodeURIComponent(attemptId) + '/resolve', {
          method: 'POST',
          body: JSON.stringify({ action: action })
        });
        if (res.ok) {
          showToast('Recovery item ' + action.replace('_', ' ') + '.', 'success');
          await fetchRecoveryQueue();
          await fetchKpis();
          await fetchHierarchies();
        } else {
          showToast('Resolve failed: ' + await apiErrorText(res), 'error');
        }
      } catch (err) {
        showToast('Error resolving recovery item: ' + err.message, 'error');
      }
    }

    function openDiscrepanciesDrawer() {
      document.getElementById('discrepancies-modal').classList.add('open');
      fetchDiscrepancies();
    }
    function closeDiscrepanciesDrawer() {
      document.getElementById('discrepancies-modal').classList.remove('open');
    }
    async function fetchDiscrepancies() {
      const container = document.getElementById('discrepancies-list-container');
      container.innerHTML = '<div style="color:var(--text-muted);font-size:0.85rem;">Loading discrepancies...</div>';
      try {
        const res = await apiFetch('/api/contacts/discrepancies');
        const data = await res.json();
        if (!data || !data.groups || data.groups.length === 0) {
          container.innerHTML = '<div style="color:var(--accent-emerald);font-size:0.85rem;">&#10003; No data discrepancies found.</div>';
          return;
        }
        let html = '<div style="font-size:0.85rem;color:var(--text-secondary);margin-bottom:0.5rem;">Found ' + data.groups.length + ' discrepancy group(s).</div>';
        data.groups.forEach(function(g, i) {
          html += '<div style="border:1px solid var(--border-medium);border-left:3px solid var(--accent-amber);border-radius:var(--radius-sm);padding:0.6rem 0.75rem;">';
          html += '<div style="font-weight:600;font-size:0.8rem;margin-bottom:0.35rem;">' + (i + 1) + '. ' + escapeHtml(g.kind) + ': <span style="color:var(--accent-amber);">' + escapeHtml(g.identifier) + '</span></div>';
          g.contacts.forEach(function(c) {
            html += '<div style="font-size:0.8rem;padding:0.15rem 0;">&#8226; <strong>' + escapeHtml(c.name) + '</strong> @ ' + escapeHtml(c.company) + ' <span style="color:var(--text-muted);">(' + escapeHtml(c.phone || c.email) + ')</span>' + (c.last_whatsapp_at ? ' &mdash; last WA ' + escapeHtml(String(c.last_whatsapp_at).slice(0,10)) : '') + '</div>';
          });
          html += '</div>';
        });
        container.innerHTML = html;
      } catch (err) {
        container.innerHTML = '<div style="color:var(--accent-rose);font-size:0.85rem;">Error loading discrepancies: ' + escapeHtml(err.message) + '</div>';
      }
    }

    // ------------------------------------------------------------------
    // Delegated event dispatch (replaces inline on* handlers).
    // Elements declare data-action / data-change / data-input (space-separated
    // action names) and optional data-args='[...]' with $value/$el/$event tokens.
    // ------------------------------------------------------------------
    function resolveActionArgs(el, event) {
      let args = [];
      if (el.dataset.args) {
        try {
          args = JSON.parse(el.dataset.args);
        } catch (err) {
          console.error('Invalid data-args on', el, err);
        }
      }
      return args.map(function (a) {
        if (a === '$value') return el.value;
        if (a === '$el') return el;
        if (a === '$event') return event;
        return a;
      });
    }

    // ------------------------------------------------------------------
    // Overview live activity feed
    // ------------------------------------------------------------------
    const ACTIVITY_MAX = 20;

    function describeLiveActivity(type, p) {
      const ch = p.channel || '';
      if (type === 'ATTEMPT_SENT') return `${ch || 'Message'} sent to ${p.destination || p.recipient || 'recipient'}`;
      if (type === 'ATTEMPT_FAILED') return `Send failed: ${p.failure_detail || p.failure_code || 'error'}`;
      if (type === 'ATTEMPT_RECOVERY_REQUIRED' || type === 'ATTEMPT_UNKNOWN') return `Recovery required for ${p.destination || 'contact'}`;
      if (type.startsWith('CAMPAIGN_')) return `Campaign ${type.replace('CAMPAIGN_', '').toLowerCase()}${p.campaign_id ? ' (' + p.campaign_id + ')' : ''}`;
      if (type === 'SENDER_STATUS_CHANGED') return `Sender ${p.sender_id || ''} is now ${p.status || ''}`;
      if (type === 'SENDER_QR_RECEIVED') return `QR code received for ${p.sender_id || ''}`;
      if (type === 'SYNC_COMPLETED') return `Sync completed: ${p.new_contacts || 0} new, ${p.updated_contacts || 0} updated contacts`;
      if (type === 'SYNC_STARTED') return `Sync started: ${p.source_file || ''}`;
      if (type === 'CRM_STATUS_CHANGED') return `Contact marked ${p.status || ''}`;
      return type;
    }

    function addActivityItem(text, occurredAt) {
      const list = document.getElementById('activity-list');
      if (!list) return;
      const empty = list.querySelector('.activity-empty');
      if (empty) empty.remove();
      const item = document.createElement('div');
      item.className = 'activity-item';
      const time = document.createElement('span');
      time.className = 'activity-time';
      time.textContent = occurredAt ? String(occurredAt).slice(11, 16) : '';
      const body = document.createElement('span');
      body.className = 'activity-text';
      body.textContent = text;
      item.appendChild(time);
      item.appendChild(body);
      list.prepend(item);
      while (list.children.length > ACTIVITY_MAX) list.removeChild(list.lastChild);
    }

    async function fetchActivity() {
      try {
        const res = await apiFetch('/api/events/history?limit=15');
        if (!res.ok) return;
        const events = await res.json();
        const list = document.getElementById('activity-list');
        if (list) list.innerHTML = '';
        (events || []).slice().reverse().forEach((ev) => addActivityItem(describeLiveActivity(ev.event_type, ev.payload || {}), ev.occurred_at));
      } catch (err) {
        console.error('Error loading activity:', err);
      }
    }

    // Sidebar navigation: switch routed pages.
    function setActiveNav(route) {
      document.querySelectorAll('.nav-item').forEach(function (el) {
        el.classList.toggle('active', el.dataset.nav === route);
      });
    }

    // Onboarding: visible until at least one sender is ready (persisted per browser).
    const ONBOARDING_KEY = 'reachout-onboarding-dismissed';

    function updateOnboarding() {
      const card = document.getElementById('onboarding-card');
      if (!card) return;
      let dismissed = false;
      try {
        dismissed = localStorage.getItem(ONBOARDING_KEY) === 'true';
      } catch (e) {
        dismissed = false;
      }
      const activeSenders = state.senders.filter((s) => s.status === 'ACTIVE').length;
      card.hidden = dismissed || activeSenders > 0;
    }

    function dismissOnboarding() {
      try {
        localStorage.setItem(ONBOARDING_KEY, 'true');
      } catch (e) {
        /* storage unavailable; the card just hides for this session */
      }
      const card = document.getElementById('onboarding-card');
      if (card) card.hidden = true;
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
      if (page === 'senders') await openSendersDrawer();
      if (page === 'templates') await openTemplatesDrawer();
    }

    // Registry of all delegated actions (module scope is not global).
    const ACTIONS = {
      navigate,
      dismissOnboarding,
      changeContactsPage,
      addEmailSession, addWhatsAppSession, applyFilters, archiveContact, checkWhatsAppAuthStatus,
      closeDiscrepanciesDrawer, closeEmailConfigModal, closeHistoryModal, closeMoreMenu, closeReadinessModal,
      closeRecoveryDrawer, closeSendModal, closeSyncModal, closeTemplateForm,
      closeWhatsAppQrModal, deactivateSender, fetchDiscrepancies, fetchRecoveryQueue,
      fetchSenders, handleSearchChange, handleTemplateSelectChange, onCampaignAction, openDiscrepanciesDrawer,
      openEmailConfigModal, openHistoryModal, openRecoveryDrawer, openSendModal, openSendersDrawer,
      openTemplateForm, openTemplateFormById, openTemplatesDrawer, reactivateSender, resolveConfirm,
      resolveRecoveryAttempt, retryLoad, saveAndVerifyEmailConfig, saveTemplate, setPriorityFilter,
      setThemeMode, startWhatsAppAuth, stopCampaign, submitSendMessage, toggleCompany, toggleMoreMenu,
      triggerSync, updateContactStatus, verifyEmailSender,
    };

    function runActionNames(el, names, event) {
      names.forEach(function (name) {
        const fn = ACTIONS[name];
        if (typeof fn !== 'function') {
          console.error('Unknown action:', name);
          return;
        }
        fn.apply(el, resolveActionArgs(el, event));
      });
    }

    function delegateEvent(event, attr) {
      const el = event.target.closest('[' + attr + ']');
      if (!el) return;
      const names = (el.getAttribute(attr) || '').trim().split(/\s+/).filter(Boolean);
      runActionNames(el, names, event);
    }

    document.addEventListener('focusin', function (event) {
      // Capture the previous value so failed changes can roll back.
      const el = event.target;
      if (el && el.matches && el.matches('[data-change]')) el.dataset.prev = el.value;
    });
    document.addEventListener('click', function (event) { delegateEvent(event, 'data-action'); });
    document.addEventListener('change', function (event) { delegateEvent(event, 'data-change'); });
    document.addEventListener('input', function (event) { delegateEvent(event, 'data-input'); });
    document.addEventListener('keydown', function (event) {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      const el = event.target.closest('[data-action]');
      if (!el) return;
      const tag = el.tagName;
      if (tag === 'BUTTON' || tag === 'A' || tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;
      event.preventDefault();
      const names = (el.getAttribute('data-action') || '').trim().split(/\s+/).filter(Boolean);
      runActionNames(el, names, event);
    });

    // Keyboard shortcuts (ignored while typing in a field).
    document.addEventListener('keydown', function (event) {
      if (event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
      const target = event.target;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.tagName === 'SELECT' || target.isContentEditable)) return;
      const shortcuts = {
        '/': function () {
          const search = document.getElementById('search-input');
          if (search) {
            search.focus();
            search.select();
          }
        },
        o: function () { navigate('overview'); },
        c: function () { navigate('contacts'); },
        s: function () { navigate('senders'); },
        t: function () { navigate('templates'); },
      };
      const handler = shortcuts[event.key];
      if (handler) {
        event.preventDefault();
        handler();
      }
    });
