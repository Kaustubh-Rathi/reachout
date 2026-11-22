import { api, apiErrorText } from '../modules/api.js';
import { escapeHtml, jsonAttr } from '../modules/dom.js';
import { showToast } from '../modules/toast.js';
import { state } from '../modules/store.js';
import { registerActions, getAction } from '../modules/actions.js';

// 10. Templates Management Drawer
async function fetchTemplates() {
  renderTemplatesLoading();
  try {
    const res = await api.listTemplates();
    if (res.ok) {
      state.templates = await res.json();
      getAction('clearLoadError')('templates');
      return true;
    }
    renderTemplatesLoadError(await apiErrorText(res));
    getAction('reportLoadError')('templates');
  } catch (err) {
    renderTemplatesLoadError(err.message);
    console.error('Error loading templates:', err);
    getAction('reportLoadError')('templates');
  }
  return false;
}

async function openTemplatesDrawer() {
  getAction('showPage')('templates');
  if (!state.templates || state.templates.length === 0) {
    const loaded = await fetchTemplates();
    if (!loaded) return false;
  }
  renderTemplatesList();
  return true;
}

function renderTemplatesLoading() {
  const container = document.getElementById('templates-list-container');
  if (container) container.innerHTML = '<div class="template-load-state">Loading templates…</div>';
}

function renderTemplatesLoadError(message) {
  const container = document.getElementById('templates-list-container');
  if (container) {
    container.innerHTML = `<div class="template-load-state template-load-error">Failed to load templates: ${escapeHtml(message)}</div>`;
  }
}

function renderTemplatesList() {
  const container = document.getElementById('templates-list-container');
  container.innerHTML = '';

  if (!state.templates || state.templates.length === 0) {
    container.innerHTML = `
      <div class="template-empty">
        <strong>No templates yet</strong>
        <div>Add a message template to start outreach.</div>
      </div>`;
    return;
  }

  state.templates.forEach(t => {
    const card = document.createElement('article');
    card.className = 'template-card';
    const channelClass = t.channel === 'WHATSAPP' ? 'template-channel-wa' : (t.channel === 'EMAIL' ? 'template-channel-em' : '');
    const channelLabel = t.channel === 'WHATSAPP' ? 'WhatsApp' : (t.channel === 'EMAIL' ? 'Email' : (t.channel || 'Unknown'));
    card.innerHTML = `
      <div class="template-card-header">
        <div class="template-card-title">
          <span class="template-id">${escapeHtml(t.id)}</span>
          <strong class="template-name">${escapeHtml(t.name)}</strong>
        </div>
        <div class="template-card-controls">
          <span class="template-channel ${channelClass}">${escapeHtml(channelLabel)}</span>
          <button class="btn btn-outline btn-xs" data-action="openTemplateFormById" data-args='[${jsonAttr(t.id)}]'>Edit</button>
        </div>
      </div>
      ${t.subject ? `<div class="template-subject">Subject: ${escapeHtml(t.subject)}</div>` : ''}
      <div class="template-body-preview">${escapeHtml(t.body || '')}</div>
      ${t.attachment_ref ? `<div class="template-attachment">Attachment: ${escapeHtml(t.attachment_ref)}</div>` : ''}
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
  document.getElementById('template-form-container').hidden = false;
}

function closeTemplateForm() {
  document.getElementById('template-form-container').hidden = true;
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

registerActions({ fetchTemplates, openTemplatesDrawer, openTemplateFormById, openTemplateForm, closeTemplateForm, saveTemplate });
