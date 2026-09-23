// Central action registry shared by every page module and the dispatcher.
//
// Page modules call `registerActions({...})` at import time. The delegated
// dispatcher resolves action names through `getAction`, which throws when an
// element references an action nobody registered — fail-fast instead of a
// silent no-op.

const registry = new Map();

export function registerActions(actions) {
  for (const [name, fn] of Object.entries(actions)) {
    if (typeof fn !== 'function') {
      throw new Error(`Action "${name}" is not a function`);
    }
    if (registry.has(name)) {
      throw new Error(`Duplicate action registration: ${name}`);
    }
    registry.set(name, fn);
  }
}

export function getAction(name) {
  const fn = registry.get(name);
  if (!fn) {
    throw new Error(`Unknown action: ${name}`);
  }
  return fn;
}
