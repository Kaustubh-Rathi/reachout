// Thin fetch wrapper, API route table, and typed client for the dashboard.
//
// Every endpoint path lives here (and only here); UI functions depend on the
// typed `api` client instead of inlining URL strings.

export async function apiFetch(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && typeof options.body === 'string' && !headers['Content-Type']) {
    headers['Content-Type'] = 'application/json';
  }
  return fetch(url, { ...options, headers });
}

// Extract a human-readable message from a failed API response.
export async function apiErrorText(res) {
  try {
    const body = await res.json();
    if (body && body.detail) {
      if (typeof body.detail === 'string') return body.detail;
      return body.detail.message || body.detail.reason || JSON.stringify(body.detail);
    }
  } catch (e) {
    /* non-JSON body */
  }
  return res.statusText || `HTTP ${res.status}`;
}

export const routes = {
  kpis: '/api/crm/kpis',
  companies: '/api/companies',
  hierarchy: (query) => `/api/companies/hierarchy?${query}`,
  crmStatus: '/api/crm/status',
  contact: (id) => `/api/contacts/${encodeURIComponent(id)}`,
  contactDiscrepancies: '/api/contacts/discrepancies',
  campaigns: '/api/campaigns',
  campaignQuickStart: '/api/campaigns/quick-start',
  campaignAction: (id, action) => `/api/campaigns/${encodeURIComponent(id)}/${action}`,
  outreachPreview: '/api/outreach/preview',
  outreachSend: (channel, isResend) =>
    `/api/outreach/${isResend ? 'resend-' : 'send-'}${channel === 'WHATSAPP' ? 'whatsapp' : 'email'}`,
  outreachRecovery: '/api/outreach/recovery',
  outreachRecoveryResolve: (id) => `/api/outreach/recovery/${encodeURIComponent(id)}/resolve`,
  senders: '/api/senders',
  whatsappAdd: '/api/senders/whatsapp/add',
  whatsappAuthStatus: (id) => `/api/senders/whatsapp/${encodeURIComponent(id)}/auth/status`,
  whatsappAuthStart: (id) => `/api/senders/whatsapp/${encodeURIComponent(id)}/auth/start`,
  whatsappAuthCheck: (id) => `/api/senders/whatsapp/${encodeURIComponent(id)}/auth/check`,
  senderDeactivate: (id) => `/api/senders/${encodeURIComponent(id)}/deactivate`,
  senderReactivate: (id) => `/api/senders/${encodeURIComponent(id)}/reactivate`,
  emailConfigure: '/api/senders/email/configure',
  emailVerify: (id) => `/api/senders/email/${encodeURIComponent(id)}/verify`,
  templates: '/api/templates',
  template: (id) => `/api/templates/${encodeURIComponent(id)}`,
  sync: '/api/sync',
  eventHistory: (limit) => `/api/events/history?limit=${limit}`,
};

export const api = {
  getKpis: () => apiFetch(routes.kpis),
  getCompanies: () => apiFetch(routes.companies),
  fetchHierarchy: (query, options = {}) => apiFetch(routes.hierarchy(query), options),
  updateContactStatus: (contactId, status) =>
    apiFetch(routes.crmStatus, { method: 'POST', body: JSON.stringify({ contact_id: contactId, status }) }),
  archiveContact: (contactId) => apiFetch(routes.contact(contactId), { method: 'DELETE' }),
  getContact: (contactId) => apiFetch(routes.contact(contactId)),
  getContactDiscrepancies: () => apiFetch(routes.contactDiscrepancies),
  listCampaigns: () => apiFetch(routes.campaigns),
  quickStartCampaign: (channel, maxCount) =>
    apiFetch(routes.campaignQuickStart, { method: 'POST', body: JSON.stringify({ channel, max_count: maxCount }) }),
  campaignAction: (campaignId, action) => apiFetch(routes.campaignAction(campaignId, action), { method: 'POST' }),
  previewMessage: (payload) => apiFetch(routes.outreachPreview, { method: 'POST', body: JSON.stringify(payload) }),
  sendMessage: (channel, isResend, payload) =>
    apiFetch(routes.outreachSend(channel, isResend), { method: 'POST', body: JSON.stringify(payload) }),
  recoveryQueue: () => apiFetch(routes.outreachRecovery),
  resolveRecovery: (attemptId, payload) =>
    apiFetch(routes.outreachRecoveryResolve(attemptId), { method: 'POST', body: JSON.stringify(payload) }),
  listSenders: () => apiFetch(routes.senders),
  addWhatsAppSession: () => apiFetch(routes.whatsappAdd, { method: 'POST', body: '{}' }),
  whatsappAuthStatus: (senderId) => apiFetch(routes.whatsappAuthStatus(senderId)),
  whatsappAuthStart: (senderId, payload) =>
    apiFetch(routes.whatsappAuthStart(senderId), { method: 'POST', body: JSON.stringify(payload) }),
  whatsappAuthCheck: (senderId) => apiFetch(routes.whatsappAuthCheck(senderId), { method: 'POST' }),
  deactivateSender: (senderId) => apiFetch(routes.senderDeactivate(senderId), { method: 'POST' }),
  reactivateSender: (senderId) => apiFetch(routes.senderReactivate(senderId), { method: 'POST' }),
  configureEmailSender: (payload) => apiFetch(routes.emailConfigure, { method: 'POST', body: JSON.stringify(payload) }),
  verifyEmailSender: (senderId) => apiFetch(routes.emailVerify(senderId), { method: 'POST' }),
  listTemplates: () => apiFetch(routes.templates),
  saveTemplate: (templateId, payload) =>
    apiFetch(templateId ? routes.template(templateId) : routes.templates, {
      method: templateId ? 'PUT' : 'POST',
      body: JSON.stringify(payload),
    }),
  syncSource: () => apiFetch(routes.sync, { method: 'POST', body: '{}' }),
  eventHistory: (limit) => apiFetch(routes.eventHistory(limit)),
};
