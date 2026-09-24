import { api, apiErrorText } from '../modules/api.js';
import { formatDate, formatTime } from '../modules/format.js';
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
      return true;
    }
    getAction('reportLoadError')('KPIs');
  } catch (err) {
    console.error('Error loading KPIs:', err);
    getAction('reportLoadError')('KPIs');
  }
  return false;
}

function renderKpis(k) {
  const setText = (id, value) => {
    const element = document.getElementById(id);
    if (element) element.innerText = value;
  };
  setText('overview-summary-text', `${k.total_contacts || 0} contacts · ${k.total_endpoints || 0} phone and email endpoints`);
  setText('val-total', k.total_contacts || 0);
  setText('val-fully-messaged', k.fully_messaged_contacts || 0);
  setText('val-dispatch-complete', k.dispatch_complete_contacts || 0);
  setText('val-with-sends', k.contacts_with_any_send || 0);
  setText('val-ready-contacts', k.ready_contacts || 0);
  setText('val-ready-endpoints', k.ready_endpoints || 0);
  setText('val-total-endpoints', k.total_endpoints || 0);
  setText('val-covered-endpoints', `${k.covered_endpoints || 0} / ${k.total_endpoints || 0}`);
  setText('val-covered-endpoints-detail', k.covered_endpoints || 0);
  setText('val-coverage-percent', k.endpoint_coverage_percent || 0);
  setText('val-ready-endpoints-detail', k.ready_endpoints || 0);
  setText('val-blocked-endpoints', k.blocked_endpoints || 0);
  setText('val-blocked-contacts', k.blocked_contacts || 0);
  setText('val-never-attempted', k.never_attempted_endpoints || 0);
  setText('val-permanent-failed', k.permanent_failed_endpoints || 0);
  setText('val-retryable-failed', k.retryable_failed_endpoints || 0);
  setText('val-wa-sent', k.whatsapp_sent_endpoints || 0);
  setText('val-email-sent', k.email_sent_endpoints || 0);
  setText('val-failed', k.failed_attempts || 0);
  setText('val-failed-destinations', k.failed_destinations || 0);
  setText('val-interested', k.interested || 0);
  setText('val-not-interested', k.not_interested || 0);
  setText('val-interview', k.interview || 0);
  setText('val-followup', k.follow_up_due || 0);
  setText('val-recovery', k.unresolved_attempts || 0);
}

// 6. Campaign Control Plane
async function fetchCampaigns() {
  try {
    const res = await api.listCampaigns();
    if (res.ok) {
      const list = await res.json();
      state.campaigns = list || [];
      const selectedStillExists = state.campaigns.some(campaign => campaign.id === state.selectedCampaignId);
      if (!selectedStillExists) state.selectedCampaignId = state.campaigns[0]?.id || null;
      renderCampaignOptions();
      updateCampaignControls(state.campaigns.find(campaign => campaign.id === state.selectedCampaignId) || null);
      getAction('clearLoadError')('campaigns');
      return true;
    }
    getAction('reportLoadError')('campaigns');
  } catch (err) {
    console.error('Error fetching campaigns:', err?.name || 'Error', err?.message || String(err), err?.stack || '');
    getAction('reportLoadError')('campaigns');
  }
  return false;
}

function renderCampaignOptions() {
  const selector = document.getElementById('campaign-selector');
  if (!selector) return;
  selector.innerHTML = '';
  if (state.campaigns.length === 0) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = 'No campaigns';
    selector.appendChild(option);
    return;
  }
  state.campaigns.forEach(campaign => {
    const option = document.createElement('option');
    option.value = campaign.id;
    option.textContent = `${campaign.name} — ${campaign.status} — ${formatDate(campaign.created_at)}`;
    option.selected = campaign.id === state.selectedCampaignId;
    selector.appendChild(option);
  });
}

function selectCampaign() {
  const selector = document.getElementById('campaign-selector');
  state.selectedCampaignId = selector?.value || null;
  updateCampaignControls(state.campaigns.find(campaign => campaign.id === state.selectedCampaignId) || null);
}

function upsertCampaign(campaign) {
  if (!campaign) return;
  state.campaigns = [
    campaign,
    ...state.campaigns.filter(item => item.id !== campaign.id),
  ].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
  state.selectedCampaignId = campaign.id;
  renderCampaignOptions();
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
    const targetCount = camp.target_count;
    const targetPercent = Number.isFinite(camp.target_progress_percent) ? camp.target_progress_percent : null;
    const sentAttempts = camp.attempt_outcomes?.sent || 0;
    const targetSuccesses = camp.target_progress?.successful_sends || 0;
    const coverage = camp.global_endpoint_coverage || {};
    const coveragePercent = Number(coverage.coverage_percent || 0);
    const setText = (id, value) => {
      const element = document.getElementById(id);
      if (element) element.innerText = value;
    };

    document.getElementById('target-progress-label').innerText = targetCount == null
      ? 'Target not recorded'
      : `${targetSuccesses} / ${targetCount} successful sends`;
    const targetProgressBar = document.getElementById('target-progress-bar');
    targetProgressBar.setAttribute('aria-valuenow', targetPercent == null ? '0' : targetPercent);
    targetProgressBar.setAttribute(
      'aria-valuetext',
      targetCount == null ? 'Target not recorded' : `${targetSuccesses} of ${targetCount} successful sends`,
    );
    document.getElementById('target-progress-fill').style.width = `${targetPercent == null ? 0 : Math.max(0, Math.min(100, targetPercent))}%`;
    document.getElementById('target-progress-detail').innerText = targetCount == null
      ? 'This campaign predates target persistence; attempt outcomes remain available below.'
      : `${targetPercent}% of the configured successful-send target`;

    setText('coverage-progress-label', `${coverage.covered_endpoints || 0} / ${coverage.total_endpoints || 0}`);
    const coverageProgressBar = document.getElementById('coverage-progress-bar');
    coverageProgressBar.setAttribute('aria-valuenow', coveragePercent);
    coverageProgressBar.setAttribute(
      'aria-valuetext',
      `${coverage.covered_endpoints || 0} of ${coverage.total_endpoints || 0} endpoints covered`,
    );
    document.getElementById('coverage-progress-fill').style.width = `${Math.max(0, Math.min(100, coveragePercent))}%`;
    setText(
      'coverage-progress-detail',
      `${coveragePercent}% covered • ${coverage.ready_endpoints || 0} ready • ${coverage.blocked_endpoints || 0} blocked • ${coverage.never_attempted_endpoints || 0} never attempted`
    );

    setText('attempt-total', camp.attempt_outcomes?.total_attempts || 0);
    setText('attempt-sent', sentAttempts);
    setText('attempt-failed', camp.attempt_outcomes?.failed || 0);
    setText('attempt-unresolved', camp.attempt_outcomes?.unresolved || 0);
    setText('attempt-pending', camp.attempt_outcomes?.pending || 0);
    setText('attempt-success-rate', `${camp.attempt_outcomes?.sent_percent || 0}%`);

    setText('prog-round', `Round ${camp.current_round || 1}`);
    setText('prog-curr-channel', camp.current_dispatch_channel || camp.channel || 'WHATSAPP');
    setText('prog-curr-sender', camp.current_sender || 'AUTO');
    setText('prog-curr-template', camp.template_used || 'AUTO');
  } else {
    ['target-progress-label', 'coverage-progress-label', 'target-progress-detail', 'coverage-progress-detail'].forEach(id => {
      const element = document.getElementById(id);
      if (element) element.innerText = id.includes('label') ? 'No campaign data' : 'Start or select a campaign to view progress';
    });
    document.getElementById('target-progress-bar').setAttribute('aria-valuenow', '0');
    document.getElementById('coverage-progress-bar').setAttribute('aria-valuenow', '0');
    document.getElementById('target-progress-fill').style.width = '0%';
    document.getElementById('coverage-progress-fill').style.width = '0%';
    ['attempt-total', 'attempt-sent', 'attempt-failed', 'attempt-unresolved', 'attempt-pending', 'attempt-success-rate'].forEach(id => {
      const element = document.getElementById(id);
      if (element) element.innerText = id === 'attempt-success-rate' ? '0%' : 0;
    });
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
  const actionLabel = document.getElementById('campaign-action-label');
  if (actionBtn) actionBtn.disabled = true;
  if (actionLabel) actionLabel.innerHTML = '<span class="spinner"></span> Starting...';
  let accepted = false;

  try {
    const res = await api.quickStartCampaign('WHATSAPP', maxCount);
    if (res.ok) {
      accepted = true;
      showToast(`Campaign started with a target of ${maxCount} successful sends.`, 'success');
      let data = null;
      try {
        data = await res.json();
      } catch (err) {
        if (err.name !== 'SyntaxError') console.error('Error reading campaign start response:', err);
      }
      if (data) {
        upsertCampaign(data);
        updateCampaignControls(data);
      } else {
        await getAction('fetchCampaigns')();
      }
    } else {
      const data = await res.json().catch(() => ({}));
      const detail = data.detail || {};
      const reason = detail.reason || 'OUTREACH_NOT_READY';
      const msg = detail.message || (typeof detail === 'string' ? detail : 'Prerequisites not met');
      getAction('openReadinessModal')(reason, msg);
    }
  } catch (err) {
    if (!accepted) showToast('Failed to start campaign: ' + err.message, 'error');
  } finally {
    if (!accepted) updateCampaignControls(state.activeCampaign);
  }
}

async function pauseCampaign() {
  if (!state.activeCampaign) return;
  try {
    const res = await api.campaignAction(state.activeCampaign.id, 'pause');
    if (res.ok) {
      const camp = await res.json();
      showToast('Campaign paused.', 'info');
      upsertCampaign(camp);
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
      upsertCampaign(camp);
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
  if (type === 'ATTEMPT_FAILED') {
    const destination = p.destination || p.recipient || 'unknown destination';
    const reason = p.failure_detail || p.failure_code || 'Unknown error';
    return `${ch || 'Message'} send failed for ${destination}: ${reason}`;
  }
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
    if (!res.ok) return false;
    const events = await res.json();
    const list = document.getElementById('activity-list');
    if (list) list.innerHTML = '';
    (events || []).slice().reverse().forEach((ev) => addActivityItem(describeLiveActivity(ev.event_type, ev.payload || {}), ev.occurred_at));
    return true;
  } catch (err) {
    console.error('Error loading activity:', err);
    return false;
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

registerActions({ fetchKpis, fetchCampaigns, selectCampaign, onCampaignAction, describeLiveActivity, addActivityItem, fetchActivity, updateOnboarding, dismissOnboarding });
