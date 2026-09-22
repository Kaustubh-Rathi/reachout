// Modal accessibility: Escape-to-close, initial focus, focus trap, focus
// restore, and background scroll lock.

function openModals() {
  return Array.from(document.querySelectorAll('.modal-overlay.open'));
}

function modalFocusables(modal) {
  const selector =
    'a[href], button:not([disabled]), input:not([type="hidden"]):not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  return Array.from(modal.querySelectorAll(selector)).filter((el) => el.offsetParent !== null);
}

let currentTopModal = null;
let modalReturnFocus = null;

function syncModalState() {
  const modals = openModals();
  document.body.style.overflow = modals.length > 0 ? 'hidden' : '';
  const top = modals.length > 0 ? modals[modals.length - 1] : null;
  if (top === currentTopModal) return;
  if (top) {
    modalReturnFocus = document.activeElement;
    const focusables = modalFocusables(top);
    if (focusables.length > 0) focusables[0].focus();
    else {
      top.setAttribute('tabindex', '-1');
      top.focus();
    }
  } else if (modalReturnFocus && typeof modalReturnFocus.focus === 'function') {
    modalReturnFocus.focus();
  }
  currentTopModal = top;
}

export function initModalAccessibility() {
  // Global Escape-to-close: dismiss the topmost open dialog.
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape') return;
    const modals = openModals();
    if (modals.length === 0) return;
    modals[modals.length - 1].classList.remove('open');
  });

  new MutationObserver(syncModalState).observe(document.body, {
    subtree: true,
    attributes: true,
    attributeFilter: ['class'],
  });

  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Tab') return;
    const modals = openModals();
    if (modals.length === 0) return;
    const top = modals[modals.length - 1];
    const focusables = modalFocusables(top);
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
}
