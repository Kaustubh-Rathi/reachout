// Sender account row shared by WhatsApp and Email.

import { escapeHtml, jsonAttr } from '../modules/dom.js';
import { formatDate } from '../modules/format.js';

const SENDER_STATUS_BADGES = {
  ACTIVE: 'badge-sent',
  AUTHENTICATING: 'badge-partial',
  QR_REQUIRED: 'badge-partial',
  AUTH_REQUIRED: 'badge-uncovered',
  NOT_CONFIGURED: 'badge-uncovered',
  INACTIVE: 'badge-not-sent',
};

function senderBadgeClass(status) {
  return SENDER_STATUS_BADGES[status] || 'badge-failed';
}

function limitsLine(sender) {
  const daily = sender.daily_limit == null ? 'Unlimited' : String(sender.daily_limit);
  const hourly = sender.hourly_limit == null ? 'Unlimited' : String(sender.hourly_limit);
  return `<span class="sender-limits">Limit: ${escapeHtml(daily)}/day</span><span class="sender-limits">Rate: ${escapeHtml(hourly)}/hr</span>`;
}

/**
 * Build a sender account row. Identity values are deliberately never rendered:
 * the row shows the account's display name, health and limits only.
 * @param {object} sender Sender projection (id, display_name, status, …)
 * @param {{actionsHtml?: string, showLastChecked?: boolean}} [options]
 * @returns {HTMLElement}
 */
export function renderSenderCard(sender, options = {}) {
  const { actionsHtml = '', showLastChecked = false } = options;
  const card = document.createElement('div');
  card.className = 'sender-card';

  const errorHtml = sender.error_message
    ? `<div class="sender-error">${escapeHtml(sender.error_message)}</div>`
    : '';
  const lastCheckedHtml = showLastChecked && sender.last_checked
    ? `<span class="sender-meta">Last checked: ${escapeHtml(formatDate(sender.last_checked))}</span>`
    : '';
  const lastUsedHtml = sender.last_used_at
    ? `<span class="sender-meta">Last used: ${escapeHtml(formatDate(sender.last_used_at))}</span>`
    : '';

  card.innerHTML = `
    <div class="sender-card-main">
      <div class="sender-primary-line">
        <strong class="sender-name">${escapeHtml(sender.display_name || 'Sender')}</strong>
        <span class="sender-status ${senderBadgeClass(sender.status)}">${escapeHtml(sender.status || 'UNKNOWN')}</span>
      </div>
      <div class="sender-meta-line">
        ${limitsLine(sender)}
        ${lastCheckedHtml}
        ${lastUsedHtml}
      </div>
      ${errorHtml}
    </div>
    <div class="sender-actions">${actionsHtml}</div>
  `;
  return card;
}

/**
 * Action buttons for a sender, per channel and lifecycle state.
 * @param {object} sender
 * @param {'WHATSAPP'|'EMAIL'} channel
 * @returns {string}
 */
export function senderActionsHtml(sender, channel) {
  const args = `data-args='[${jsonAttr(sender.id)}]'`;
  const deactivate = `<button class="btn btn-amber btn-xs" data-action="deactivateSender" ${args} title="Deactivate and pause from rotation (history preserved)">Deactivate</button>`;
  const reactivate = `<button class="btn btn-outline btn-xs" data-action="reactivateSender" ${args} title="Reactivate sender into rotation">Reactivate</button>`;

  if (sender.status === 'INACTIVE') return reactivate;

  if (channel === 'WHATSAPP') {
    return `
      <button class="btn btn-emerald btn-xs" data-action="startWhatsAppAuth" ${args} title="Re-authenticate this WhatsApp sender (re-scan QR)">Re-authenticate</button>
      <button class="btn btn-outline btn-xs" data-action="checkWhatsAppAuthStatus" ${args} title="Check connection health">Check status</button>
      ${deactivate}
    `;
  }
  return `
    <button class="btn btn-primary btn-xs" data-action="openEmailConfigModal" ${args}>Reconfigure</button>
    <button class="btn btn-outline btn-xs" data-action="verifyEmailSender" ${args}>Test and verify</button>
    ${deactivate}
  `;
}
