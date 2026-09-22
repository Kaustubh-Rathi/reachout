import { api, apiErrorText } from '../modules/api.js';
import { escapeHtml, jsonAttr } from '../modules/dom.js';
import { formatDate } from '../modules/format.js';
import { showToast, appConfirm } from '../modules/toast.js';
import { state } from '../modules/store.js';
import { registerActions, getAction } from '../modules/actions.js';

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
    const res = await api.previewMessage({
      contact_id: contactId,
      channel: channel,
      template_id: tplId,
      is_resend: isResend,
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
    const res = await api.sendMessage(channel, isResend, payload);
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(`${channel} message dispatched to ${destination || 'contact'}!`, 'success');
      closeSendModal();
      await getAction('fetchHierarchies')();
      await getAction('fetchKpis')();
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

async function triggerSync() {
  const syncBtn = document.getElementById('sync-btn');
  syncBtn.disabled = true;
  syncBtn.innerText = 'Syncing...';

  try {
    const res = await api.syncSource();
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
      await getAction('loadInitialData')();
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
    const res = await api.recoveryQueue();
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
    const res = await api.resolveRecovery(attemptId, { action: action });
    if (res.ok) {
      showToast('Recovery item ' + action.replace('_', ' ') + '.', 'success');
      await fetchRecoveryQueue();
      await getAction('fetchKpis')();
      await getAction('fetchHierarchies')();
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
    const res = await api.getContactDiscrepancies();
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

registerActions({ openSendModal, handleTemplateSelectChange, closeSendModal, submitSendMessage, triggerSync, closeSyncModal, openRecoveryDrawer, closeRecoveryDrawer, fetchRecoveryQueue, resolveRecoveryAttempt, openDiscrepanciesDrawer, closeDiscrepanciesDrawer, fetchDiscrepancies });
export { openSendModal, handleTemplateSelectChange, closeSendModal, submitSendMessage, triggerSync, closeSyncModal, openRecoveryDrawer, closeRecoveryDrawer, fetchRecoveryQueue, resolveRecoveryAttempt, openDiscrepanciesDrawer, closeDiscrepanciesDrawer, fetchDiscrepancies };
