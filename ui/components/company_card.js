// Company hierarchy card component.
//
// Renders the company card with its HR contacts, endpoints, and interaction
// history. Status/badge mapping is a data table, extended by adding entries
// rather than editing conditionals.

import { renderCompactAttemptRow, isRecoveryAttempt } from './timeline.js';
import { escapeHtml, jsonAttr } from '../modules/dom.js';
import { formatDate } from '../modules/format.js';

const COMPANY_STATUS_MAP = {
  NOT_CONTACTED: { cls: 'status-not-contacted', icon: '🔴', text: 'NOT CONTACTED' },
  IN_PROGRESS: { cls: 'status-in-progress', icon: '🟡', text: 'IN PROGRESS' },
  CONTACTED: { cls: 'status-contacted', icon: '🟢', text: 'CONTACTED' },
  CLOSED: { cls: 'status-closed', icon: '⚫', text: 'CLOSED' },
};

function companyStatusMeta(status) {
  return COMPANY_STATUS_MAP[status] || COMPANY_STATUS_MAP.NOT_CONTACTED;
}

const CRM_OPTIONS = [
  ['NOT_CONTACTED', 'Not Contacted'],
  ['PENDING_REPLY', 'Pending Reply'],
  ['CONTACTED', 'Contacted'],
  ['REPLIED', 'Replied'],
  ['FOLLOW_UP', 'Follow-Up Due'],
  ['INTERESTED', 'Interested ★'],
  ['NOT_INTERESTED', 'Not Interested'],
  ['INTERVIEW', 'Interview 📅'],
  ['OFFER', 'Offer 🏆'],
  ['REJECTED', 'Rejected ✕'],
  ['CLOSED', 'Closed ⚫'],
  ['DO_NOT_CONTACT', 'Do Not Contact ⛔'],
];

function hrStatusSelect(contactId, currentStatus) {
  const cur = currentStatus || 'NOT_CONTACTED';
  const options = CRM_OPTIONS.map(([value, label]) => {
    const selected = cur === value || (value === 'NOT_CONTACTED' && cur === 'NONE') ? 'selected' : '';
    return `<option value="${value}" ${selected}>${label}</option>`;
  }).join('');
  return `
    <select class="form-control select-compact" data-change="updateContactStatus" data-args='[${jsonAttr(contactId)},"$value","$el"]'>
      ${options}
    </select>
  `;
}

function endpointStatusBadge(endpoint, recoveryAtt) {
  const isSent = endpoint.status === 'SENT';
  if (isSent) return `<span class="endpoint-sent">✓ ${endpoint.channel} SENT</span>`;
  if (recoveryAtt) return `<span class="endpoint-recovery-badge">⏱ ${endpoint.channel} IN RECOVERY QUEUE</span>`;
  return '<span class="endpoint-uncontacted">○ Not contacted</span>';
}

function findRecoveryAttempt(hr, endpoint) {
  return (hr.history || []).find(
    (att) => isRecoveryAttempt(att) && att.channel === endpoint.channel && (att.destination || '') === (endpoint.address || '')
  );
}

function renderEndpointBox(hr, endpoint) {
  const isSent = endpoint.status === 'SENT';
  const recoveryAtt = findRecoveryAttempt(hr, endpoint);

  let metaDetails = '';
  if (isSent && endpoint.sender_account_id) {
    metaDetails = `<div class="endpoint-meta">${escapeHtml(endpoint.sender_account_id)} &bull; ${escapeHtml(endpoint.template_id || 'Direct')} &bull; ${formatDate(endpoint.sent_at)}</div>`;
  } else if (recoveryAtt && recoveryAtt.failure_detail) {
    metaDetails = `<div class="endpoint-meta endpoint-meta-warn">${escapeHtml(recoveryAtt.failure_detail)}</div>`;
  }

  const sendBtn = recoveryAtt
    ? '<button class="btn btn-amber btn-xs" data-action="openRecoveryDrawer">⏱ Review queue</button>'
    : (endpoint.channel === 'WHATSAPP'
      ? `<button class="btn btn-emerald btn-xs" data-action="openSendModal" data-args='[${jsonAttr(hr.contact_id)},"WHATSAPP",${isSent},${jsonAttr(endpoint.address)}]'>${isSent ? 'Resend WA' : 'Send WA'}</button>`
      : `<button class="btn btn-primary btn-xs" data-action="openSendModal" data-args='[${jsonAttr(hr.contact_id)},"EMAIL",${isSent},${jsonAttr(endpoint.address)}]'>${isSent ? 'Resend Email' : 'Send Email'}</button>`);

  return `
    <div class="endpoint-box ${isSent ? 'endpoint-covered' : ''}">
      <div class="endpoint-info">
        <span class="endpoint-label">${escapeHtml(endpoint.label)} &bull; ${endpointStatusBadge(endpoint, recoveryAtt)}</span>
        <span class="endpoint-addr">${escapeHtml(endpoint.address)}</span>
        ${metaDetails}
      </div>
      <div>${sendBtn}</div>
    </div>
  `;
}

function renderContactHistory(hr) {
  if (!(hr.history || []).length) {
    return '<div class="history-empty">No message history yet.</div>';
  }
  return (hr.history || []).map(renderCompactAttemptRow).join('');
}

function renderHrCard(hr, index) {
  const followUpBadge = hr.follow_up_due
    ? `<span class="badge badge-pending-reply badge-compact">⚠️ FOLLOW-UP DUE${hr.follow_up_due_at ? ` ${formatDate(hr.follow_up_due_at)}` : ''}</span>`
    : '';
  const endpointsHtml = (hr.endpoints || []).map((ep) => renderEndpointBox(hr, ep)).join('');

  return `
    <div class="hr-card" data-contact-id="${escapeHtml(hr.contact_id)}">
      <div class="hr-header">
        <div class="hr-title-wrap">
          <span class="hr-name-bold">HR ${index + 1}: ${escapeHtml(hr.name)}</span>
          ${hr.designation ? `<span class="hr-designation-text">&bull; ${escapeHtml(hr.designation)}</span>` : ''}
          ${followUpBadge}
        </div>
        <div class="hr-header-actions">
          <span>CRM Status:</span>
          ${hrStatusSelect(hr.contact_id, hr.crm_outcome)}
          <button class="btn btn-secondary btn-xs" data-action="openHistoryModal" data-args='[${jsonAttr(hr.contact_id)}]'>History</button>
          <button class="btn btn-outline-danger btn-xs" title="Archive / DNC" aria-label="Archive contact" data-action="archiveContact" data-args='[${jsonAttr(hr.contact_id)}]'>🗑</button>
        </div>
      </div>
      <div class="hr-contact-meta">
        <span>📞 ${escapeHtml(hr.phone || '-')}</span>
        <span>✉️ ${escapeHtml(hr.email || '-')}</span>
        ${hr.last_activity_at ? `<span>🕒 Last activity: ${formatDate(hr.last_activity_at)}</span>` : ''}
      </div>
      <div class="endpoints-container">
        ${endpointsHtml || '<div class="history-empty">No endpoints registered for this contact.</div>'}
      </div>
      <div class="hr-history-block">
        <div class="hr-history-title">Interaction History</div>
        ${renderContactHistory(hr)}
      </div>
    </div>
  `;
}

/**
 * Build the company hierarchy card element.
 *
 * @param {object} comp Company hierarchy projection
 * @returns {HTMLElement}
 */
export function renderCompanyCard(comp) {
  const card = document.createElement('div');
  card.className = 'company-card';
  card.setAttribute('data-company-id', comp.id);

  const meta = companyStatusMeta(comp.status);
  const hrsHtml = (comp.contacts || []).map((hr, index) => renderHrCard(hr, index)).join('');

  card.innerHTML = `
    <div class="company-card-header" role="button" tabindex="0" aria-expanded="false" aria-label="Expand or collapse HR contacts for ${escapeHtml(comp.name)}" data-action="toggleCompany" data-args='[${jsonAttr(comp.id)}]' title="Click to expand / collapse HR contacts">
      <div class="company-title-area">
        <span class="company-expand-caret" id="caret-${escapeHtml(comp.id)}">▸</span>
        <span class="company-name-lg">${escapeHtml(comp.name)}</span>
        <span class="company-status-badge ${meta.cls}">${meta.icon} ${meta.text}</span>
      </div>
      <div class="company-meta-pills">
        <span>HRs: <strong>${comp.total_contacts || 0}</strong></span>
        <span>Endpoints: <strong>${comp.covered_endpoints || 0} / ${comp.total_endpoints || 0} covered</strong></span>
      </div>
    </div>
    <div class="hr-contacts-list" id="hrlist-${escapeHtml(comp.id)}" style="display: none;">
      ${hrsHtml || '<div class="history-empty">No HR contacts registered.</div>'}
    </div>
  `;
  return card;
}
