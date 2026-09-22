// Toast notifications and the promise-based confirm dialog.

import { escapeHtml } from './dom.js';

const TOAST_DURATION_MS = 4000;
const TOAST_ICONS = { success: '✓', error: '✕', info: 'ℹ️' };

export function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  const icon = TOAST_ICONS[type] || TOAST_ICONS.info;

  toast.innerHTML = `<span>${icon}</span><span>${escapeHtml(message)}</span>`;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transition = 'opacity 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }, TOAST_DURATION_MS);
}

let confirmResolver = null;

export function appConfirm(message, opts = {}) {
  document.getElementById('confirm-modal-message').textContent = message;
  document.getElementById('confirm-modal-title').textContent = opts.title || 'Confirm action';
  const okBtn = document.getElementById('confirm-modal-ok');
  okBtn.textContent = opts.confirmLabel || 'Confirm';
  okBtn.className = 'btn ' + (opts.danger === false ? 'btn-primary' : 'btn-rose');
  document.getElementById('confirm-modal').classList.add('open');
  return new Promise((resolve) => {
    confirmResolver = resolve;
  });
}

export function resolveConfirm(result) {
  document.getElementById('confirm-modal').classList.remove('open');
  if (confirmResolver) {
    const resolve = confirmResolver;
    confirmResolver = null;
    resolve(result);
  }
}
