import test from 'node:test';
import assert from 'node:assert/strict';

class FakeElement {
  constructor() {
    this.children = [];
    this.classList = {
      add() {},
      remove() {},
      toggle() {},
      contains() { return false; },
    };
    this.dataset = {};
    this.style = {};
    this.attributes = new Map();
    this.innerHTML = '';
    this.innerText = '';
    this.textContent = '';
  }

  appendChild(child) {
    this.children.push(child);
    return child;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.get(name) ?? null;
  }

  addEventListener() {}
  remove() {}
  closest() { return null; }
  matches() { return false; }
  querySelector() { return null; }
}

const elements = new Map();

globalThis.document = {
  body: new FakeElement(),
  addEventListener() {},
  createElement() { return new FakeElement(); },
  getElementById(id) {
    if (!elements.has(id)) elements.set(id, new FakeElement());
    return elements.get(id);
  },
  querySelector() { return null; },
  querySelectorAll() { return []; },
};

const location = { hash: '', protocol: 'http:', host: 'localhost' };
globalThis.location = location;
globalThis.window = {
  location,
  addEventListener() {},
  matchMedia() { return { matches: false, addEventListener() {} }; },
};

globalThis.MutationObserver = class {
  observe() {}
};

globalThis.localStorage = {
  getItem() { return null; },
  setItem() {},
};

await import('../app.js');
const { api } = await import('../modules/api.js');
const { getAction } = await import('../modules/actions.js');
const { state } = await import('../modules/store.js');
const { renderSenderCard } = await import('../components/sender_card.js');
const { renderCompanyCard } = await import('../components/company_card.js');

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function response(data, ok = true) {
  return {
    ok,
    status: ok ? 200 : 400,
    statusText: ok ? 'OK' : 'Bad Request',
    json: async () => data,
  };
}

test('stale sender and template loads cannot restore an older route', async () => {
  const originalListSenders = api.listSenders;
  const originalListTemplates = api.listTemplates;
  const senderResponse = deferred();
  const templateResponse = deferred();
  api.listSenders = () => senderResponse.promise;
  api.listTemplates = () => templateResponse.promise;
  state.senders = [];
  state.templates = [];

  try {
    window.location.hash = '#/overview';
    const senderNavigation = getAction('navigate')('senders');
    await getAction('navigate')('overview');
    senderResponse.resolve(response([]));
    await senderNavigation;
    assert.equal(window.location.hash, '#/overview');

    const templateNavigation = getAction('navigate')('templates');
    await getAction('navigate')('overview');
    templateResponse.resolve(response([]));
    await templateNavigation;
    assert.equal(window.location.hash, '#/overview');
  } finally {
    api.listSenders = originalListSenders;
    api.listTemplates = originalListTemplates;
  }
});

test('a delayed hierarchy body cannot replace a newer hierarchy result', async () => {
  const originalFetchHierarchy = api.fetchHierarchy;
  const firstBody = deferred();
  let calls = 0;
  api.fetchHierarchy = async () => {
    calls += 1;
    if (calls === 1) return response(firstBody.promise);
    return response([{ id: 'new', name: 'New', contacts: [] }]);
  };
  state.searchQuery = '';
  state.selectedCompany = 'ALL';
  state.selectedCrmStatus = 'ALL';
  state.selectedCoverageStatus = 'ALL';
  state.selectedPriorityFilter = 'ALL';

  try {
    const first = getAction('fetchHierarchies')();
    await Promise.resolve();
    await Promise.resolve();
    const second = getAction('fetchHierarchies')();
    await second;
    firstBody.resolve([{ id: 'old', name: 'Old', contacts: [] }]);
    await first;
    assert.equal(state.hierarchies[0].id, 'new');
  } finally {
    api.fetchHierarchy = originalFetchHierarchy;
  }
});

test('a delayed failed-attempt body cannot replace a newer result', async () => {
  const originalFailedAttempts = api.failedAttempts;
  const firstBody = deferred();
  let calls = 0;
  api.failedAttempts = async () => {
    calls += 1;
    if (calls === 1) return response(firstBody.promise);
    return response({
      items: [{ contact_name: 'New result' }],
      total: 1,
      offset: 0,
      failed_destinations: 1,
      failure_codes: [],
      has_more: false,
    });
  };
  ['failed-campaign-filter', 'failed-channel-filter', 'failed-code-filter', 'failed-search-input'].forEach((id) => {
    document.getElementById(id).value = '';
  });

  try {
    const first = getAction('fetchFailedAttempts')();
    await Promise.resolve();
    await Promise.resolve();
    const second = getAction('fetchFailedAttempts')();
    await second;
    firstBody.resolve({
      items: [{ contact_name: 'Old result' }],
      total: 1,
      offset: 0,
      failed_destinations: 1,
      failure_codes: [],
      has_more: false,
    });
    await first;
    assert.match(document.getElementById('failed-list-container').innerHTML, /New result/);
    assert.doesNotMatch(document.getElementById('failed-list-container').innerHTML, /Old result/);
  } finally {
    api.failedAttempts = originalFailedAttempts;
  }
});

test('endpoint channels and sender limits are escaped', () => {
  const payload = '<img src=x onerror=alert(1)>';
  const sender = renderSenderCard({
    id: 'sender-1',
    display_name: 'Sender',
    identity: 'sender@example.com',
    status: 'ACTIVE',
    channel: 'EMAIL',
    daily_limit: payload,
    hourly_limit: payload,
  });
  assert.doesNotMatch(sender.innerHTML, /<img/);
  assert.match(sender.innerHTML, /&lt;img/);

  const company = renderCompanyCard({
    id: 'company-1',
    name: 'Company',
    status: 'IN_PROGRESS',
    contacts: [{
      contact_id: 'contact-1',
      name: 'Contact',
      endpoints: [{
        label: 'Email',
        address: 'contact@example.com',
        channel: payload,
        status: 'FAILED',
        coverage_state: 'FAILED',
      }],
      history: [],
    }],
  });
  assert.doesNotMatch(company.innerHTML, /<img/);
  assert.match(company.innerHTML, /&lt;img/);
});

test('successful campaign start keeps the pause control without an error toast', async () => {
  const originalQuickStart = api.quickStartCampaign;
  const originalListCampaigns = api.listCampaigns;
  const campaign = {
    id: 'campaign-1',
    name: 'Run',
    channel: 'WHATSAPP',
    status: 'RUNNING',
    created_at: '2026-09-24T00:00:00Z',
    target_count: 25,
    target_progress_percent: 0,
    target_progress: { successful_sends: 0 },
    attempt_outcomes: {},
    global_endpoint_coverage: {},
  };
  api.quickStartCampaign = async () => ({
    ok: true,
    status: 200,
    json: async () => { throw new SyntaxError('empty body'); },
  });
  api.listCampaigns = async () => response([campaign]);
  state.campaigns = [];
  state.activeCampaign = null;
  state.selectedCampaignId = null;
  document.getElementById('campaign-limit-input').value = '25';
  document.getElementById('campaign-action-label').innerText = 'New Run';
  document.getElementById('campaign-action-btn').dataset.intent = 'start';
  document.getElementById('toast-container').children = [];
  const originalSetTimeout = globalThis.setTimeout;
  globalThis.setTimeout = () => 0;

  try {
    await getAction('onCampaignAction')();
    assert.equal(document.getElementById('campaign-action-btn').dataset.intent, 'pause');
    assert.match(document.getElementById('campaign-action-label').innerText, /Pause/);
    assert.doesNotMatch(document.getElementById('toast-container').children[0].innerHTML, /Failed to start/);
  } finally {
    globalThis.setTimeout = originalSetTimeout;
    api.quickStartCampaign = originalQuickStart;
    api.listCampaigns = originalListCampaigns;
  }
});
