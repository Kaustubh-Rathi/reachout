import { api } from '../modules/api.js';
import { escapeHtml } from '../modules/dom.js';
import { showToast } from '../modules/toast.js';
import { state } from '../modules/store.js';
import { registerActions, getAction } from '../modules/actions.js';
import { renderSenderCard, senderActionsHtml } from '../components/sender_card.js';

// Shared QR polling timer id for open/close/poll WhatsApp auth functions.
let waQrPollTimer = null;

// 9. Senders & Authentication Control Plane (Phase 8.2 & 8.3)
async function openSendersDrawer() {
  getAction('showPage')('senders');
  await fetchSenders();
}

async function fetchSenders() {
  try {
    const res = await api.listSenders();
    if (res.ok) {
      state.senders = await res.json();
      updateHeaderSendersIndicator();
      renderSendersModal();
      getAction('updateOnboarding')();
      getAction('clearLoadError')('senders');
    } else {
      getAction('reportLoadError')('senders');
    }
  } catch (err) {
    console.error('Error loading senders:', err);
    getAction('reportLoadError')('senders');
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

  const waSenders = state.senders.filter((s) => s.channel === 'WHATSAPP');
  const emSenders = state.senders.filter((s) => s.channel === 'EMAIL');

  const renderInto = (container, senders, channel, showLastChecked, emptyText) => {
    container.innerHTML = '';
    if (senders.length === 0) {
      container.innerHTML = `<div class="sender-empty">${emptyText}</div>`;
      return;
    }
    senders.forEach((s) => {
      container.appendChild(renderSenderCard(s, {
        actionsHtml: senderActionsHtml(s, channel),
        showLastChecked,
      }));
    });
  };

  renderInto(
    waContainer,
    waSenders,
    'WHATSAPP',
    true,
    'No WhatsApp sessions configured. Click "+ Add WhatsApp Session" to create one.'
  );
  renderInto(
    emContainer,
    emSenders,
    'EMAIL',
    false,
    'No Email senders configured. Click "+ Add Email Sender" to add one.'
  );
}

async function addWhatsAppSession() {
  try {
    showToast('Launching WhatsApp authentication...', 'info');
    const res = await api.addWhatsAppSession();
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
    res = await api.whatsappAuthStatus(senderId);
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
    const res = await api.deactivateSender(senderId);
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
    const res = await api.reactivateSender(senderId);
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
    const res = await api.whatsappAuthStart(senderId, {});
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
    const res = await api.whatsappAuthCheck(senderId);
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
    const res = await api.configureEmailSender({
      id: id,
      identity: addr,
      display_name: name,
      host: host,
      port: port,
      user: user,
      password: pwd,
      verify_now: true,
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
    const res = await api.verifyEmailSender(senderId);
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

registerActions({ openSendersDrawer, fetchSenders, updateHeaderSendersIndicator, renderSendersModal, addWhatsAppSession, openWhatsAppQrModal, closeWhatsAppQrModal, pollWhatsAppAuth, addEmailSession, deactivateSender, reactivateSender, startWhatsAppAuth, checkWhatsAppAuthStatus, openEmailConfigModal, closeEmailConfigModal, saveAndVerifyEmailConfig, verifyEmailSender, openReadinessModal, closeReadinessModal });
export { openSendersDrawer, fetchSenders, updateHeaderSendersIndicator, renderSendersModal, addWhatsAppSession, openWhatsAppQrModal, closeWhatsAppQrModal, pollWhatsAppAuth, addEmailSession, deactivateSender, reactivateSender, startWhatsAppAuth, checkWhatsAppAuthStatus, openEmailConfigModal, closeEmailConfigModal, saveAndVerifyEmailConfig, verifyEmailSender, openReadinessModal, closeReadinessModal };
