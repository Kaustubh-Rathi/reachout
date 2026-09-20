// Sender account card component.
//
// One renderer for both WhatsApp and Email senders — the two cards were
// previously near-duplicate template blocks differing only in the badge map
// and the action buttons.

import { escapeHtml, jsonAttr } from '../modules/dom.js';
import { formatDate } from '../modules/format.js';

// Shared status → badge class map (superset covering both channels).
export const SENDER_STATUS_BADGES = {
  ACTIVE: 'badge-sent',
  AUTHENTICATING: 'badge-partial',
  QR_REQUIRED: 'badge-partial',
  AUTH_REQUIRED: 'badge-uncovered',
  NOT_CONFIGURED: 'badge-uncovered',
  INACTIVE: 'badge-not-sent',
};

export function senderBadgeClass(status) {
  return SENDER_STATUS_BADGES[status] || 'badge-failed';
}

function limitsLine(sender) {
  const daily = sender.daily_limit == null ? 'Unlimited' : sender.daily_limit;
  const hourly = sender.hourly_limit == null ? 'Unlimited' : sender.hourly_limit;
  return `<div class="sender-limits">Limit: ${daily}/day &bull; Rate: ${hourly}/hr</div>`;
}

/**
 * Build a sender account card element.
 *
 * @param {object} sender Sender projection (id, display_name, identity, status, …)
 * @param {{actionsHtml?: string, showLastChecked?: boolean}} [options]
 * @returns {HTMLElement}
 */
export function renderSenderCard(sender, options = {}) {
  const { actionsHtml = '', showLastChecked = false } = options;
  const card = document.createElement('div');
  card.className = 'kpi-card sender-card';

  const errorHtml = sender.error_message
    ? `<div class="sender-error">⚠️ ${escapeHtml(sender.error_message)}</div>`
    : '';
  const lastCheckedHtml = showLastChecked && sender.last_checked
    ? `<div class="sender-meta">Last verified: ${formatDate(sender.last_checked)}</div>`
    : '';
  const lastUsedHtml = sender.last_used_at
    ? `<div class="sender-meta">Last used: ${formatDate(sender.last_used_at)}</div>`
    : '';

  card.innerHTML = `
    <div class="sender-card-header">
      <div>
        <strong class="sender-name">${escapeHtml(sender.display_name)}</strong>
        <div class="sender-id">${escapeHtml(sender.id)}</div>
        <div class="sender-identity">${escapeHtml(sender.identity)}</div>
      </div>
      <span class="badge ${senderBadgeClass(sender.status)}">${escapeHtml(sender.status)}</span>
    </div>
    ${limitsLine(sender)}
    ${errorHtml}
    ${lastCheckedHtml}
    ${lastUsedHtml}
    <div class="sender-actions">${actionsHtml}</div>
  `;
  return card;
}

/**
 * Action buttons for a sender, per channel and lifecycle state.
 *
 * @param {object} sender
 * @param {'WHATSAPP'|'EMAIL'} channel
 * @returns {string}
 */
export function senderActionsHtml(sender, channel) {
  const args = `data-args='[${jsonAttr(sender.id)}]'`;
  const deactivate = `<button class="btn btn-amber btn-xs" data-action="deactivateSender" ${args} title="Deactivate and pause from rotation (history preserved)">
      <span>⏸️ Deactivate</span>
    </button>`;
  const reactivate = `<button class="btn btn-outline btn-xs" data-action="reactivateSender" ${args} title="Reactivate sender into rotation">
      <span>⚡ Reactivate</span>
    </button>`;

  if (sender.status === 'INACTIVE') return reactivate;

  if (channel === 'WHATSAPP') {
    return `
      <button class="btn btn-emerald btn-xs" data-action="startWhatsAppAuth" ${args} title="Re-authenticate this WhatsApp sender (re-scan QR)">
        <span>📱 Re-Authenticate</span>
      </button>
      <button class="btn btn-outline btn-xs" data-action="checkWhatsAppAuthStatus" ${args} title="Check connection health">
        <span>🔍 Status</span>
      </button>
      ${deactivate}
    `;
  }
  return `
    <button class="btn btn-primary btn-xs" data-action="openEmailConfigModal" ${args}>
      <span>⚙️ Re-configure</span>
    </button>
    <button class="btn btn-outline btn-xs" data-action="verifyEmailSender" ${args}>
      <span>🔌 Test & Verify</span>
    </button>
    ${deactivate}
  `;
}
