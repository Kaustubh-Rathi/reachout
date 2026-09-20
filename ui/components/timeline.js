// Attempt timeline components.
//
// One home for the shared attempt-status logic and both presentations: the
// compact inline rows in the hierarchy cards and the full modal timeline —
// previously two renderers with duplicated status/icon/color mapping.

import { escapeHtml } from '../modules/dom.js';
import { formatDate } from '../modules/format.js';

export function isRecoveryAttempt(att) {
  return Boolean(att) && (att.status === 'RECOVERY_REQUIRED' || att.status === 'UNKNOWN');
}

const DOT_CLASSES = {
  SENT: 'dot-sent',
  INTERESTED: 'dot-interested',
  FOLLOW_UP: 'dot-followup',
  PENDING: 'dot-followup',
};

function dotClass(status) {
  return DOT_CLASSES[status] || 'dot-failed';
}

const BADGE_BY_STATUS = {
  SENT: 'badge-sent',
  INTERESTED: 'badge-sent',
};

function badgeClass(status) {
  return BADGE_BY_STATUS[status] || 'badge-failed';
}

/**
 * Compact attempt row used inside the hierarchy cards.
 *
 * @param {object} att Attempt projection
 * @returns {string} HTML
 */
export function renderCompactAttemptRow(att) {
  const ok = att.status === 'SENT';
  const inRecovery = isRecoveryAttempt(att);
  const icon = ok ? '✓' : (inRecovery ? '⏱' : '✕');
  const color = ok ? 'var(--accent-emerald)' : (inRecovery ? 'var(--accent-amber)' : 'var(--accent-rose)');
  const when = att.completed_at || att.prepared_at;
  return `
    <div class="attempt-row">
      <span class="attempt-icon" style="color: ${color}; font-weight: 700;">${icon}</span>
      <div class="attempt-row-body">
        <div><strong>${escapeHtml(att.channel)}</strong> ${escapeHtml(att.attempt_type || '')} &rarr; <span class="attempt-dest">${escapeHtml(att.destination || '')}</span></div>
        <div class="attempt-meta">${when ? formatDate(when) : '--'} &bull; ${escapeHtml(att.sender_account_id || 'auto')} &bull; ${escapeHtml(att.template_id || 'Direct')}</div>
        ${att.failure_detail ? `<div class="attempt-failure">${escapeHtml(att.failure_detail)}</div>` : ''}
      </div>
    </div>
  `;
}

/**
 * Build the sorted timeline event list (attempts + CRM milestones + reminders)
 * from a contact detail payload.
 *
 * @param {object} detail Contact detail (history, interested_at, reminders)
 * @returns {Array<object>}
 */
export function buildTimelineEvents(detail) {
  const events = [];

  (detail.history || []).forEach((h) => {
    // Titles/bodies are built as plain text; escaping happens once at render time.
    const destStr = h.destination ? ` → ${h.destination}` : '';
    const tplStr = h.template_id ? ` • Template: ${h.template_id}` : '';
    const refStr = h.provider_reference ? ` • Ref: ${h.provider_reference}` : '';
    const attStr = h.attachment ? ` • Attachment: ${h.attachment}` : '';
    const failStr = h.failure_detail ? `Failure: ${h.failure_detail} (${h.failure_code || ''})` : '';

    events.push({
      type: h.channel,
      title: `${h.channel} (${h.attempt_type || 'AUTOMATIC'})${destStr} • Sender: ${h.sender_account_id || 'AUTO'}${tplStr}${refStr}${attStr}`,
      status: h.status,
      body: (h.message_body || '') + (failStr ? `\n${failStr}` : ''),
      date: h.completed_at || h.prepared_at,
    });
  });

  if (detail.interested_at) {
    events.push({
      type: 'CRM',
      title: 'CRM - INTERESTED',
      status: 'INTERESTED',
      body: 'Contact marked as Interested.',
      date: detail.interested_at,
    });
  }

  (detail.reminders || []).forEach((r) => {
    events.push({
      type: 'REMINDER',
      title: `Follow-up Reminder (${r.status})`,
      status: r.status,
      body: r.reason,
      date: r.due_at,
    });
  });

  return events.sort((a, b) => new Date(b.date) - new Date(a.date));
}

/**
 * Render one timeline item element into a container.
 *
 * @param {HTMLElement} container
 * @param {object} ev Timeline event
 */
export function appendTimelineItem(container, ev) {
  const item = document.createElement('div');
  item.className = 'timeline-item';
  item.innerHTML = `
    <div class="timeline-dot ${dotClass(ev.status)}"></div>
    <div class="timeline-date">${formatDate(ev.date)}</div>
    <div class="timeline-title">${escapeHtml(ev.title)} <span class="badge ${badgeClass(ev.status)}">${escapeHtml(ev.status)}</span></div>
    <div class="timeline-body">${escapeHtml(ev.body || '')}</div>
  `;
  container.appendChild(item);
}
