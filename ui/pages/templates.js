import { api } from '../modules/api.js';
import { escapeHtml, jsonAttr } from '../modules/dom.js';
import { showToast } from '../modules/toast.js';
import { state } from '../modules/store.js';
import { registerActions, getAction } from '../modules/actions.js';

// 10. Templates Management Drawer
async function fetchTemplates() {
  try {
    const res = await api.listTemplates();
    if (res.ok) {
      state.templates = await res.json();
      getAction('clearLoadError')('templates');
    } else {
      getAction('reportLoadError')('templates');
    }
  } catch (err) {
    console.error('Error loading templates:', err);
    getAction('reportLoadError')('templates');
  }
}

async function openTemplatesDrawer() {
  getAction('showPage')('templates');
  if (!state.templates || state.templates.length === 0) await fetchTemplates();
  renderTemplatesList();
}

function renderTemplatesList() {
  const container = document.getElementById('templates-list-container');
  container.innerHTML = '';

  if (!state.templates || state.templates.length === 0) {
    container.innerHTML = `
      <div style="text-align: center; padding: 2rem; color: var(--text-secondary);">
        <div style="font-size: 1.5rem; margin-bottom: 0.5rem;" aria-hidden="true">📝</div>
        <div style="font-weight: 600; color: var(--text-primary);">No templates yet</div>
        <div style="font-size: 0.8rem; margin-top: 0.25rem;">Add your first message template to start outreach.</div>
      </div>`;
    return;
  }

  state.templates.forEach(t => {
    const card = document.createElement('div');
    card.className = 'kpi-card';
    card.innerHTML = `
      <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.35rem;">
        <strong>${escapeHtml(t.id)} - ${escapeHtml(t.name)}</strong>
        <div style="display: flex; align-items: center; gap: 0.5rem;">
          <span class="badge badge-sent">${escapeHtml(t.channel)}</span>
          <button class="btn btn-outline btn-xs" data-action="openTemplateFormById" data-args='[${jsonAttr(t.id)}]'>Edit</button>
        </div>
      </div>
      ${t.subject ? `<div style="font-size: 0.75rem; color: var(--accent-blue); font-weight: 600;">Subject: ${escapeHtml(t.subject)}</div>` : ''}
      <div style="font-size: 0.75rem; color: var(--text-secondary); margin-top: 0.3rem;">Resume: ${t.attachment_ref ? escapeHtml(t.attachment_ref) : 'not set'}</div>
      <div style="font-size: 0.75rem; color: var(--text-secondary); background: var(--bg-surface-raised); padding: 0.45rem; border-radius: var(--radius-xs); white-space: pre-wrap; font-family: 'JetBrains Mono', monospace;">${escapeHtml(t.body)}</div>
    `;
    container.appendChild(card);
  });
}

// Template Add/Edit Inline Form (shared for create and edit)
let editingTemplate = null;

function renderTemplateForm(template) {
  editingTemplate = template || null;
  const isEdit = !!template;
  document.getElementById('template-form-title').innerText = isEdit ? 'Edit Template' : 'Add Template';
  document.getElementById('tpl-id').value = template ? (template.id || '') : '';
  document.getElementById('tpl-id').readOnly = isEdit;
  document.getElementById('tpl-name').value = template ? (template.name || '') : '';
  document.getElementById('tpl-channel').disabled = isEdit;
  document.getElementById('tpl-channel').value = (template && template.channel) ? template.channel : 'WHATSAPP';
  document.getElementById('tpl-subject').value = template ? (template.subject || '') : '';
  document.getElementById('tpl-body').value = template ? (template.body || '') : '';
  document.getElementById('tpl-attachment-ref').value = template ? (template.attachment_ref || '') : '';
}

function openTemplateFormById(templateId) {
  const t = state.templates.find(x => x.id === templateId);
  openTemplateForm(t || null);
}

function openTemplateForm(template) {
  renderTemplateForm(template);
  document.getElementById('template-form-container').style.display = 'flex';
}

function closeTemplateForm() {
  document.getElementById('template-form-container').style.display = 'none';
}

async function saveTemplate() {
  const id = document.getElementById('tpl-id').value.trim();
  const name = document.getElementById('tpl-name').value.trim();
  const channel = document.getElementById('tpl-channel').value;
  const subject = document.getElementById('tpl-subject').value.trim();
  const body = document.getElementById('tpl-body').value;
  const attachmentRef = document.getElementById('tpl-attachment-ref').value.trim();

  if (!id || !name || !body) {
    showToast('Template ID, Name and Body are required.', 'error');
    return;
  }

  const isEdit = !!editingTemplate;
  let payload;
  if (isEdit) {
    payload = {};
    if (name !== (editingTemplate.name || '')) payload.name = name;
    if (subject !== (editingTemplate.subject || '')) payload.subject = subject;
    if (body !== (editingTemplate.body || '')) payload.body = body;
    if (attachmentRef !== (editingTemplate.attachment_ref || '')) payload.attachment_ref = attachmentRef;
  } else {
    payload = { id: id, name: name, channel: channel, body: body, subject: subject, attachment_ref: attachmentRef };
  }

  try {
    showToast(isEdit ? `Saving template ${id}...` : 'Creating template...', 'info');
    const res = await api.saveTemplate(isEdit ? editingTemplate.id : null, payload);
    if (res.ok) {
      showToast(`Template ${id} saved.`, 'success');
      closeTemplateForm();
      await fetchTemplates();
      await openTemplatesDrawer();
    } else {
      const err = await res.json();
      showToast(`Failed to save template: ${err.detail || 'Error'}`, 'error');
    }
  } catch (err) {
    showToast('Error saving template: ' + err.message, 'error');
  }
}

registerActions({ fetchTemplates, openTemplatesDrawer, renderTemplatesList, renderTemplateForm, openTemplateFormById, openTemplateForm, closeTemplateForm, saveTemplate });
export { fetchTemplates, openTemplatesDrawer, renderTemplatesList, renderTemplateForm, openTemplateFormById, openTemplateForm, closeTemplateForm, saveTemplate };
