import { api, apiErrorText } from '../modules/api.js';
import { escapeHtml, jsonAttr } from '../modules/dom.js';
import { formatDate, maskEndpoint } from '../modules/format.js';
import { showToast, appConfirm } from '../modules/toast.js';
import { state } from '../modules/store.js';
import { registerActions, getAction } from '../modules/actions.js';

function openSendModal(contactId, channel = 'WHATSAPP', isResend = false, targetEndpoint = null) {
  let contact = null;
  let companyName = '';
  for (const h of state.hierarchies || []) {
    const found = (h.contacts || []).find(c => c.contact_id === contactId);
    if (found) {
      contact = found;
      companyName = h.name || '';
      break;
    }
  }
  if (!contact) return;

  document.getElementById('modal-contact-id').value = contactId;
  document.getElementById('modal-channel').value = channel;
  document.getElementById('modal-is-resend').value = isResend ? 'true' : 'false';

  const channelName = channel === 'WHATSAPP' ? 'WhatsApp' : (channel === 'EMAIL' ? 'Email' : 'Message');
  const title = isResend ? `Resend ${channelName} message` : `Send ${channelName} message`;
  document.getElementById('send-modal-title').innerText = title;

  const previewElem = document.getElementById('modal-recipient-preview');
  if (previewElem) {
    previewElem.textContent = companyName ? `${contact.name} at ${companyName}` : (contact.name || '');
  }

  const chip = document.getElementById('modal-channel-chip');
  if (chip) {
    chip.textContent = channelName;
    chip.className = `channel-chip ${channel === 'WHATSAPP' ? 'channel-chip-wa' : (channel === 'EMAIL' ? 'channel-chip-em' : 'channel-chip-other')}`;
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
      opt.textContent = `${channelName === 'Message' ? 'Message' : channelName} endpoint ${idx + 1}`;
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
      // Sender names and ids can identify real sending accounts, so expose neither.
      opt.textContent = `${channelName} sender ${matchingSenders.indexOf(s) + 1}`;
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
  if (subjGroup) subjGroup.hidden = channel !== 'EMAIL';
  if (channel === 'EMAIL') {
    document.getElementById('modal-subject-input').value = `Exploring opportunities at ${companyName}`;
  }

  // Default body
  const firstName = contact.first_name || (contact.name ? contact.name.split(' ')[0] : 'there');
  document.getElementById('modal-body-input').value = `Hi ${firstName},\n\nI hope you are well. Reaching out regarding software engineering roles at ${companyName}.\n\nBest regards,\nCandidate`;

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
    if (!(await appConfirm(`This ${channel === 'WHATSAPP' ? 'WhatsApp' : 'email'} endpoint was already contacted. Resend to it anyway?`, { title: 'Manual resend', confirmLabel: 'Resend', danger: false }))) {
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
      showToast(`${channel === 'WHATSAPP' ? 'WhatsApp' : 'Email'} message sent.`, 'success');
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

const FAILED_PAGE_SIZE = 50;
let failedOffset = 0;
let failedRequestSequence = 0;

function openFailedDrawer() {
  failedOffset = 0;
  renderFailedCampaignOptions();
  document.getElementById('failed-modal').classList.add('open');
  fetchFailedAttempts();
}

function closeFailedDrawer() {
  failedRequestSequence += 1;
  document.getElementById('failed-modal').classList.remove('open');
}

function renderFailedCampaignOptions() {
  const select = document.getElementById('failed-campaign-filter');
  const selected = select.value;
  select.innerHTML = '';
  const all = document.createElement('option');
  all.value = '';
  all.textContent = 'All campaigns';
  select.appendChild(all);
  state.campaigns.forEach(campaign => {
    const option = document.createElement('option');
    option.value = campaign.id;
    option.textContent = `${campaign.name} — ${campaign.status}`;
    select.appendChild(option);
  });
  select.value = state.campaigns.some(campaign => campaign.id === selected) ? selected : '';
}

function failedQuery() {
  const params = new URLSearchParams();
  const campaignId = document.getElementById('failed-campaign-filter').value;
  const channel = document.getElementById('failed-channel-filter').value;
  const failureCode = document.getElementById('failed-code-filter').value;
  const search = document.getElementById('failed-search-input').value.trim();
  if (campaignId) params.set('campaign_id', campaignId);
  if (channel) params.set('channel', channel);
  if (failureCode) params.set('failure_code', failureCode);
  if (search) params.set('search', search);
  params.set('offset', failedOffset);
  params.set('limit', FAILED_PAGE_SIZE);
  return params.toString();
}

function renderFailedCodeOptions(codes) {
  const select = document.getElementById('failed-code-filter');
  const selected = select.value;
  select.innerHTML = '';
  const all = document.createElement('option');
  all.value = '';
  all.textContent = 'All failure reasons';
  select.appendChild(all);
  (codes || []).forEach(code => {
    const option = document.createElement('option');
    option.value = code;
    option.textContent = code;
    select.appendChild(option);
  });
  const selectedStillValid = !selected || (codes || []).includes(selected);
  select.value = selectedStillValid ? selected : '';
  return Boolean(selected && !selectedStillValid);
}

async function fetchFailedAttempts(preserveOffset = false) {
  if (!preserveOffset) failedOffset = 0;
  const query = failedQuery();
  const request = ++failedRequestSequence;
  const container = document.getElementById('failed-list-container');
  const summary = document.getElementById('failed-list-summary');
  container.innerHTML = '<div class="history-empty">Loading failed dispatches...</div>';
  summary.innerText = 'Loading failed dispatches...';
  try {
    const res = await api.failedAttempts(query);
    if (!res.ok) {
      const message = await apiErrorText(res);
      if (request !== failedRequestSequence) return false;
      container.innerHTML = `<div class="failed-table-empty">${escapeHtml(message)}</div>`;
      summary.innerText = 'Failed dispatches could not be loaded';
      return false;
    }
    const result = await res.json();
    if (request !== failedRequestSequence) return false;
    if (renderFailedCodeOptions(result.failure_codes)) {
      await fetchFailedAttempts();
      return false;
    }
    renderFailedAttempts(result);
    return true;
  } catch (err) {
    if (request !== failedRequestSequence) return false;
    container.innerHTML = `<div class="failed-table-empty">${escapeHtml(err.message)}</div>`;
    summary.innerText = 'Failed dispatches could not be loaded';
    return false;
  }
}

/**
 * Campaigns are identified by an opaque id, so the table shows their names.
 * @param {string} campaignId
 * @returns {string} Human-readable campaign name or "Manual".
 */
function campaignLabel(campaignId) {
  if (!campaignId) return 'Manual';
  const match = (state.campaigns || []).find((c) => c.id === campaignId);
  return match && match.name ? match.name : 'Campaign';
}

function renderFailedAttempts(result) {
  const container = document.getElementById('failed-list-container');
  const summary = document.getElementById('failed-list-summary');
  const items = result.items || [];
  const start = result.total ? result.offset + 1 : 0;
  const end = result.offset + items.length;
  summary.innerText = `Showing ${start}-${end} of ${result.total} failed attempts • ${result.failed_destinations} distinct failed destinations`;
  document.getElementById('failed-page-prev').disabled = result.offset <= 0;
  document.getElementById('failed-page-next').disabled = !result.has_more;
  if (items.length === 0) {
    container.innerHTML = '<div class="failed-table-empty">No failed dispatches match these filters.</div>';
    return;
  }
  const rows = items.map(item => `
    <tr>
      <td>${item.completed_at ? formatDate(item.completed_at) : '--'}</td>
      <td><strong>${escapeHtml(item.contact_name || 'Unknown')}</strong><div class="failed-muted">${escapeHtml(item.company_name || '')}</div></td>
      <td class="failed-destination">${escapeHtml(maskEndpoint(item.destination) || 'Destination unavailable')}</td>
      <td>${escapeHtml(item.channel || '')}</td>
      <td>${escapeHtml(item.failure_code || '')}<div class="failed-muted">${escapeHtml(item.failure_class || '')}</div></td>
      <td class="failed-reason">${escapeHtml(item.failure_detail || '')}</td>
      <td>${escapeHtml(campaignLabel(item.campaign_id))}</td>
      <td><button class="btn btn-outline btn-xs" data-action="viewFailedContact" data-args='[${jsonAttr(item.contact_id)}]'>History</button></td>
    </tr>
  `).join('');
  container.innerHTML = `<table class="failure-table"><thead><tr><th>Completed</th><th>Contact</th><th>Destination</th><th>Channel</th><th>Reason</th><th>Detail</th><th>Campaign</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
}

function changeFailedPage(delta) {
  failedOffset = Math.max(0, failedOffset + Number(delta || 0));
  fetchFailedAttempts(true);
}

function resetFailedFilters() {
  document.getElementById('failed-campaign-filter').value = '';
  document.getElementById('failed-channel-filter').value = '';
  document.getElementById('failed-code-filter').value = '';
  document.getElementById('failed-search-input').value = '';
  failedOffset = 0;
  fetchFailedAttempts();
}

async function exportFailedCsv() {
  try {
    const params = new URLSearchParams(failedQuery());
    params.delete('offset');
    params.delete('limit');
    const res = await api.exportFailedAttempts(params.toString());
    if (!res.ok) throw new Error(await apiErrorText(res));
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'reachout_failed_attempts.csv';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  } catch (err) {
    showToast('Failed to export failed dispatches: ' + err.message, 'error');
  }
}

async function viewFailedContact(contactId) {
  closeFailedDrawer();
  await getAction('openHistoryModal')(contactId);
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
  container.innerHTML = '<div class="drawer-empty">Loading recovery queue\u2026</div>';
  try {
    const res = await api.recoveryQueue();
    if (!res.ok) {
      container.innerHTML = '<div class="drawer-empty">Error loading recovery queue: ' + escapeHtml(await apiErrorText(res)) + '</div>';
      return;
    }
    const items = await res.json();
    if (!items || items.length === 0) {
      container.innerHTML = '<div class="drawer-empty">Recovery queue is empty. Nothing needs operator review.</div>';
      return;
    }
    container.innerHTML = '<p class="drawer-count">' + items.length + ' attempt(s) need operator review.</p>' + items.map(function (it) {
      return '<div class="drawer-row">'
        + '<div class="drawer-row-title">' + escapeHtml(it.contact_name || 'Unknown contact') + ' <span class="drawer-row-company">@ ' + escapeHtml(it.company || '') + '</span></div>'
        + '<div class="drawer-row-meta"><span class="badge badge-recovery">' + escapeHtml(it.status || '') + '</span><span>' + escapeHtml(it.channel || '') + '</span>' + (it.prepared_at ? '<span>' + formatDate(it.prepared_at) + '</span>' : '') + '</div>'
        + (it.failure_detail ? '<div class="drawer-row-error">' + escapeHtml(it.failure_detail) + '</div>' : '')
        + '<div class="drawer-row-actions">'
        + '<button class="btn btn-emerald btn-xs" data-action="resolveRecoveryAttempt" data-args=\'[' + jsonAttr(it.id) + ',"mark_sent"]\'>Confirm sent</button>'
        + '<button class="btn btn-primary btn-xs" data-action="resolveRecoveryAttempt" data-args=\'[' + jsonAttr(it.id) + ',"retry"]\'>Allow fresh retry</button>'
        + '<button class="btn btn-outline btn-xs btn-danger-text" data-action="resolveRecoveryAttempt" data-args=\'[' + jsonAttr(it.id) + ',"cancel"]\'>Cancel</button>'
        + '</div></div>';
    }).join('');
  } catch (err) {
    container.innerHTML = '<div class="drawer-empty">Error loading recovery queue: ' + escapeHtml(err.message) + '</div>';
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
  container.innerHTML = '<div class="drawer-empty">Loading discrepancies\u2026</div>';
  try {
    const res = await api.getContactDiscrepancies();
    if (!res.ok) {
      container.innerHTML = '<div class="drawer-empty">Error loading discrepancies: ' + escapeHtml(await apiErrorText(res)) + '</div>';
      return false;
    }
    const data = await res.json();
    if (!data || !data.groups || data.groups.length === 0) {
      container.innerHTML = '<div class="drawer-empty">No data discrepancies found.</div>';
      return true;
    }
    let html = '<p class="drawer-count">Found ' + data.groups.length + ' discrepancy group(s).</p>';
    data.groups.forEach(function(g, i) {
      html += '<div class="drawer-row">';
      // The shared identifier itself is a phone/email, so only its kind and
      // the number of records involved are shown.
      html += '<div class="drawer-row-title">' + (i + 1) + '. ' + escapeHtml(g.kind) + ' shared by ' + (g.contacts ? g.contacts.length : 0) + ' contact(s)</div>';
      g.contacts.forEach(function(c) {
        html += '<div class="drawer-row-line"><strong>' + escapeHtml(c.name) + '</strong> <span class="drawer-row-company">@ ' + escapeHtml(c.company) + '</span>' + (c.last_whatsapp_at ? '<span class="drawer-row-when">last WhatsApp ' + escapeHtml(String(c.last_whatsapp_at).slice(0,10)) + '</span>' : '') + '</div>';
      });
      html += '</div>';
    });
    container.innerHTML = html;
    return true;
  } catch (err) {
    container.innerHTML = '<div class="drawer-empty">Error loading discrepancies: ' + escapeHtml(err.message) + '</div>';
    return false;
  }
}

registerActions({ openSendModal, handleTemplateSelectChange, closeSendModal, submitSendMessage, openFailedDrawer, closeFailedDrawer, fetchFailedAttempts, changeFailedPage, resetFailedFilters, exportFailedCsv, viewFailedContact, triggerSync, closeSyncModal, openRecoveryDrawer, closeRecoveryDrawer, fetchRecoveryQueue, resolveRecoveryAttempt, openDiscrepanciesDrawer, closeDiscrepanciesDrawer, fetchDiscrepancies });
