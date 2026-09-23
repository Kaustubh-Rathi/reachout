import { api, apiErrorText } from '../modules/api.js';
import { formatTime } from '../modules/format.js';
import { showToast } from '../modules/toast.js';
import { state } from '../modules/store.js';
import { registerActions, getAction } from '../modules/actions.js';
import { APP_CONFIG } from '../modules/config.js';

const ACTIVITY_MAX = 20;
const ONBOARDING_KEY = 'reachout-onboarding-dismissed';

// 1. Fetch & Render KPIs
async function fetchKpis() {
  try {
    const res = await api.getKpis();
    if (res.ok) {
      const data = await res.json();
      renderKpis(data);
      getAction('clearLoadError')('KPIs');
    } else {
      getAction('reportLoadError')('KPIs');
    }
  } catch (err) {
    console.error('Error loading KPIs:', err);
    getAction('reportLoadError')('KPIs');
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

// 6. Campaign Control Plane
async function fetchCampaigns() {
  try {
    const res = await api.listCampaigns();
    if (res.ok) {
      const list = await res.json();
      if (list && list.length > 0) {
        updateCampaignControls(list[0]);
      }
      getAction('clearLoadError')('campaigns');
    } else {
      getAction('reportLoadError')('campaigns');
    }
  } catch (err) {
    console.error('Error fetching campaigns:', err);
    getAction('reportLoadError')('campaigns');
  }
}

function updateCampaignControls(camp) {
  state.activeCampaign = camp;
  const statusPill = document.getElementById('campaign-status-pill');
  const actionBtn = document.getElementById('campaign-action-btn');
  const actionLabel = document.getElementById('campaign-action-label');
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
    actionBtn.dataset.intent = 'pause';
    actionBtn.disabled = false;
    if (limitWrap) limitWrap.style.display = 'none';
    if (banner) banner.style.display = 'none';
  } else if (st === 'PAUSED') {
    statusPill.classList.add('status-paused');
    actionBtn.className = 'btn btn-primary btn-sm';
    actionLabel.innerText = '▶ Resume';
    actionBtn.dataset.intent = 'resume';
    actionBtn.disabled = false;
    if (limitWrap) limitWrap.style.display = 'none';
    if (banner) banner.style.display = 'flex';
  } else {
    if (st === 'COMPLETED') statusPill.classList.add('status-completed');
    else if (st === 'FAILED') statusPill.classList.add('status-failed');
    actionBtn.className = 'btn btn-secondary btn-sm';
    actionLabel.innerText = '▶ New Run';
    actionBtn.dataset.intent = 'start';
    actionBtn.disabled = false;
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
  const action = btn ? btn.dataset.intent : 'start';
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
    const res = await api.quickStartCampaign('WHATSAPP', maxCount);
    const data = await res.json();
    if (res.ok) {
      showToast(`Campaign started for up to ${maxCount} contacts!`, 'success');
      updateCampaignControls(data);
    } else {
      const detail = data.detail || {};
      const reason = detail.reason || 'OUTREACH_NOT_READY';
      const msg = detail.message || (typeof detail === 'string' ? detail : 'Prerequisites not met');
      getAction('openReadinessModal')(reason, msg);
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
    const res = await api.campaignAction(state.activeCampaign.id, 'pause');
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
    const res = await api.campaignAction(state.activeCampaign.id, 'resume');
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

// ------------------------------------------------------------------
// Overview live activity feed
// ------------------------------------------------------------------
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
  time.textContent = formatTime(occurredAt);
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
    const res = await api.eventHistory(15);
    if (!res.ok) return;
    const events = await res.json();
    const list = document.getElementById('activity-list');
    if (list) list.innerHTML = '';
    (events || []).slice().reverse().forEach((ev) => addActivityItem(describeLiveActivity(ev.event_type, ev.payload || {}), ev.occurred_at));
  } catch (err) {
    console.error('Error loading activity:', err);
  }
}

// Onboarding: visible until at least one sender is ready (persisted per browser).
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

registerActions({ fetchKpis, renderKpis, fetchCampaigns, updateCampaignControls, onCampaignAction, startCampaign, pauseCampaign, resumeCampaign, describeLiveActivity, addActivityItem, fetchActivity, updateOnboarding, dismissOnboarding });
export { fetchKpis, renderKpis, fetchCampaigns, updateCampaignControls, onCampaignAction, startCampaign, pauseCampaign, resumeCampaign, describeLiveActivity, addActivityItem, fetchActivity, updateOnboarding, dismissOnboarding };
