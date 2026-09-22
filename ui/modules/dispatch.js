// Delegated event dispatch: elements declare data-action / data-change /
// data-input (space-separated action names) and optional data-args='[...]'
// with $value / $el / $event tokens. Unknown action names throw (fail-fast).

import { getAction } from './actions.js';

const ACTIVATABLE_TAGS = new Set(['BUTTON', 'A', 'INPUT', 'SELECT', 'TEXTAREA']);

export function resolveActionArgs(el, event) {
  let args = [];
  if (el.dataset.args) {
    try {
      args = JSON.parse(el.dataset.args);
    } catch (err) {
      throw new Error(`Invalid data-args JSON on <${el.tagName.toLowerCase()}>: ${el.dataset.args}`);
    }
  }
  return args.map((a) => {
    if (a === '$value') return el.value;
    if (a === '$el') return el;
    if (a === '$event') return event;
    return a;
  });
}

export function runActionNames(el, names, event) {
  for (const name of names) {
    const fn = getAction(name);
    fn.apply(el, resolveActionArgs(el, event));
  }
}

function delegateEvent(event, attr) {
  const el = event.target.closest(`[${attr}]`);
  if (!el) return;
  const names = (el.getAttribute(attr) || '').trim().split(/\s+/).filter(Boolean);
  runActionNames(el, names, event);
}

function actionNamesFor(el) {
  return (el.getAttribute('data-action') || '').trim().split(/\s+/).filter(Boolean);
}

export function initDispatch() {
  // Capture the previous value so failed changes can roll back.
  document.addEventListener('focusin', (event) => {
    const el = event.target;
    if (el && el.matches && el.matches('[data-change]')) el.dataset.prev = el.value;
  });

  document.addEventListener('click', (event) => delegateEvent(event, 'data-action'));
  document.addEventListener('change', (event) => delegateEvent(event, 'data-change'));
  document.addEventListener('input', (event) => delegateEvent(event, 'data-input'));

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    const el = event.target.closest('[data-action]');
    if (!el) return;
    if (ACTIVATABLE_TAGS.has(el.tagName)) return;
    event.preventDefault();
    runActionNames(el, actionNamesFor(el), event);
  });
}
