// Company hierarchy card component.
//
// Renders one flat company row plus, when expanded, its contacts with their
// endpoints and interaction history. Status/badge mapping is a data table,
// extended by adding entries rather than editing conditionals.

import { renderCompactAttemptRow, isRecoveryAttempt } from './timeline.js';
import { escapeHtml, jsonAttr } from '../modules/dom.js';
import { formatDate } from '../modules/format.js';

const COMPANY_STATUS_MAP = {
  NOT_CONTACTED: { cls: 'status-not-contacted', text: 'Not contacted' },
  IN_PROGRESS: { cls: 'status-in-progress', text: 'In progress' },
  CONTACTED: { cls: 'status-contacted', text: 'Contacted' },
  CLOSED: { cls: 'status-closed', text: 'Closed' },
};

function companyStatusMeta(status) {
  return COMPANY_STATUS_MAP[status] || COMPANY_STATUS_MAP.NOT_CONTACTED;
}

// Endpoint coverage state -> chip text. The colour class is the only styling
// hook; the label always says what the colour means.
const ENDPOINT_STATE_MAP = {
  SENT: { cls: 'ep-sent', text: 'Sent' },
  NEVER_ATTEMPTED: { cls: 'ep-idle', text: 'Never attempted' },
  RETRYABLE: { cls: 'ep-failed', text: 'Failed · retryable' },
  PERMANENT_FAILED: { cls: 'ep-failed', text: 'Permanent failure' },
  SUPPRESSED: { cls: 'ep-idle', text: 'Suppressed' },
  BLOCKED: { cls: 'ep-warn', text: 'Blocked · recovery' },
  READY: { cls: 'ep-idle', text: 'Ready' },
};

const CRM_OPTIONS = [
  ['NOT_CONTACTED', 'Not Contacted'],
  ['PENDING_REPLY', 'Pending Reply'],
  ['CONTACTED', 'Contacted'],
  ['REPLIED', 'Replied'],
  ['FOLLOW_UP', 'Follow-Up Due'],
  ['INTERESTED', 'Interested'],
  ['NOT_INTERESTED', 'Not Interested'],
  ['INTERVIEW', 'Interview'],
  ['OFFER', 'Offer'],
  ['REJECTED', 'Rejected'],
  ['CLOSED', 'Closed'],
  ['DO_NOT_CONTACT', 'Do Not Contact'],
];

function hrStatusSelect(contactId, currentStatus) {
  const cur = currentStatus || 'NOT_CONTACTED';
  const options = CRM_OPTIONS.map(([value, label]) => {
    const selected = cur === value || (value === 'NOT_CONTACTED' && cur === 'NONE') ? 'selected' : '';
    return `<option value="${value}" ${selected}>${label}</option>`;
  }).join('');
  return `
    <select class="form-control select-compact" aria-label="CRM outcome" data-change="updateContactStatus" data-args='[${jsonAttr(contactId)},"$value","$el"]'>
      ${options}
    </select>
  `;
}

function endpointStateChip(endpoint) {
  const state = endpoint.coverage_state || endpoint.status || 'NEVER_ATTEMPTED';
  const meta = ENDPOINT_STATE_MAP[state] || ENDPOINT_STATE_MAP.READY;
  return `<span class="endpoint-state ${meta.cls}">${meta.text}</span>`;
}

function channelChip(channel) {
  if (channel === 'WHATSAPP') return '<span class="channel-chip channel-chip-wa">WhatsApp</span>';
  if (channel === 'EMAIL') return '<span class="channel-chip channel-chip-em">Email</span>';
  // Unknown channel values are shown escaped rather than silently relabelled.
  return `<span class="channel-chip channel-chip-other">${escapeHtml(channel || 'Unknown')}</span>`;
}

function findEndpointAttempt(hr, endpoint) {
  if (endpoint.attempt_id) {
    const exact = (hr.history || []).find(attempt => attempt.id === endpoint.attempt_id);
    if (exact) return exact;
  }
  return (hr.history || []).find(attempt =>
    attempt.channel === endpoint.channel && (attempt.destination || '') === (endpoint.address || '')
  ) || null;
}

function renderEndpointRow(hr, endpoint) {
  const isSent = endpoint.coverage_state === 'SENT' || endpoint.status === 'SENT';
  const isBlocked = endpoint.coverage_state === 'BLOCKED' || isRecoveryAttempt(findEndpointAttempt(hr, endpoint));
  const isInFlight = ['PREPARED', 'QUEUED', 'SENDING'].includes(endpoint.status);
  const attempt = findEndpointAttempt(hr, endpoint);

  let metaDetails = '';
  if (isSent && endpoint.sender_account_id) {
    metaDetails = `<span class="endpoint-meta">${escapeHtml(endpoint.template_id || 'Direct')} · ${formatDate(endpoint.sent_at)}</span>`;
  } else if (endpoint.failure_detail || (attempt && attempt.failure_detail)) {
    const detail = endpoint.failure_detail || attempt.failure_detail;
    const code = endpoint.failure_code || attempt.failure_code;
    metaDetails = `<span class="endpoint-meta endpoint-meta-warn">${escapeHtml(detail)}${code ? ` · ${escapeHtml(code)}` : ''}</span>`;
  }

  let sendBtn;
  if (isBlocked) {
    sendBtn = '<button class="btn btn-amber btn-xs" data-action="openRecoveryDrawer">Review recovery</button>';
  } else if (isInFlight) {
    sendBtn = '<button class="btn btn-outline btn-xs" disabled>In flight</button>';
  } else if (endpoint.channel === 'WHATSAPP') {
    sendBtn = `<button class="btn btn-emerald btn-xs" data-action="openSendModal" data-args='[${jsonAttr(hr.contact_id)},"WHATSAPP",${isSent},${jsonAttr(endpoint.address)}]'>${isSent ? 'Resend WA' : 'Send WA'}</button>`;
  } else {
    sendBtn = `<button class="btn btn-primary btn-xs" data-action="openSendModal" data-args='[${jsonAttr(hr.contact_id)},"EMAIL",${isSent},${jsonAttr(endpoint.address)}]'>${isSent ? 'Resend Email' : 'Send Email'}</button>`;
  }

  return `
    <div class="endpoint-row ${isSent ? 'endpoint-row-sent' : ''}">
      ${channelChip(endpoint.channel)}
      <div class="endpoint-main">
        <span class="endpoint-addr">${endpoint.channel === 'WHATSAPP' ? 'WhatsApp endpoint' : endpoint.channel === 'EMAIL' ? 'Email endpoint' : 'Endpoint'}</span>
        <span class="endpoint-line">
          ${endpointStateChip(endpoint)}
          ${metaDetails}
        </span>
      </div>
      <div class="endpoint-action">${sendBtn}</div>
    </div>
  `;
}

function renderContactHistory(hr) {
  if (!(hr.history || []).length) {
    return '<div class="history-empty">No message history yet.</div>';
  }
  return (hr.history || []).map(renderCompactAttemptRow).join('');
}

function renderHrCard(hr) {
  const followUpBadge = hr.follow_up_due
    ? `<span class="badge badge-pending-reply badge-compact">Follow-up due${hr.follow_up_due_at ? ` · ${formatDate(hr.follow_up_due_at)}` : ''}</span>`
    : '';
  const endpointsHtml = (hr.endpoints || []).map((ep) => renderEndpointRow(hr, ep)).join('');
  const historyCount = (hr.history || []).length;

  return `
    <div class="hr-card" data-contact-id="${escapeHtml(hr.contact_id)}">
      <div class="hr-header">
        <div class="hr-identity">
          <span class="hr-name">${escapeHtml(hr.name)}</span>
          ${hr.designation ? `<span class="hr-designation">${escapeHtml(hr.designation)}</span>` : ''}
          ${followUpBadge}
        </div>
        <div class="hr-header-actions">
          ${hrStatusSelect(hr.contact_id, hr.crm_outcome)}
          <button class="btn btn-secondary btn-xs" data-action="openHistoryModal" data-args='[${jsonAttr(hr.contact_id)}]'>History</button>
          <button class="btn btn-outline btn-xs" title="Archive / do not contact" aria-label="Archive contact" data-action="archiveContact" data-args='[${jsonAttr(hr.contact_id)}]'>Archive</button>
        </div>
      </div>
      <div class="hr-contact-meta">
        ${hr.phone ? '<span>WhatsApp available</span>' : ''}
        ${hr.email ? '<span>Email available</span>' : ''}
        ${hr.last_activity_at ? `<span>Last activity ${formatDate(hr.last_activity_at)}</span>` : ''}
      </div>
      <div class="endpoint-list">
        ${endpointsHtml || '<div class="history-empty">No endpoints registered for this contact.</div>'}
      </div>
      <details class="hr-history-block">
        <summary>Interaction history${historyCount ? ` (${historyCount})` : ''}</summary>
        ${renderContactHistory(hr)}
      </details>
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
  const hrsHtml = (comp.contacts || []).map(renderHrCard).join('');
  const contactCount = (comp.contacts || []).length || comp.total_contacts || 0;

  card.innerHTML = `
    <div class="company-card-header" role="button" tabindex="0" aria-expanded="false" aria-controls="hrlist-${escapeHtml(comp.id)}" aria-label="Expand or collapse contacts for ${escapeHtml(comp.name)}" data-action="toggleCompany" data-args='[${jsonAttr(comp.id)}]' title="Click to expand / collapse contacts">
      <span class="company-expand-caret" id="caret-${escapeHtml(comp.id)}">▸</span>
      <span class="company-name-lg">${escapeHtml(comp.name)}</span>
      <span class="company-status-badge ${meta.cls}">${meta.text}</span>
      <span class="company-row-meta">${contactCount} contact${contactCount === 1 ? '' : 's'}</span>
      <span class="company-row-meta">${comp.covered_endpoints || 0} / ${comp.total_endpoints || 0} endpoints</span>
    </div>
    <div class="hr-contacts-list" id="hrlist-${escapeHtml(comp.id)}" style="display: none;">
      ${hrsHtml || '<div class="history-empty">No contacts registered.</div>'}
    </div>
  `;
  return card;
}
