// Server-injected limits (see <body data-*> in crm_dashboard.html).

export const APP_CONFIG = {
  defaultOutreachLimit: Number(document.body.dataset.defaultLimit) || 100,
  maxOutreachLimit: Number(document.body.dataset.maxLimit) || 1000,
};
