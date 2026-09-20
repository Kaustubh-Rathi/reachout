// Observable application store.
//
// A single, centralized store replaces scattered module-level state. Mutations
// through the proxy notify subscribers so views can react to state changes
// without imperative re-render wiring.

export function createStore(initialState = {}) {
  const subscribers = new Set();
  const state = new Proxy({ ...initialState }, {
    set(target, key, value) {
      const changed = target[key] !== value;
      target[key] = value;
      if (changed) notify();
      return true;
    },
    deleteProperty(target, key) {
      delete target[key];
      notify();
      return true;
    },
  });

  function notify() {
    subscribers.forEach((subscriber) => {
      try {
        subscriber(state);
      } catch (err) {
        console.error('Store subscriber error:', err);
      }
    });
  }

  function getState() {
    return state;
  }

  function subscribe(subscriber) {
    subscribers.add(subscriber);
    return () => subscribers.delete(subscriber);
  }

  return { state, getState, subscribe };
}

export const store = createStore({
  companies: [],
  hierarchies: [],
  senders: [],
  templates: [],
  activeCampaign: null,
  searchQuery: '',
  selectedCompany: 'ALL',
  selectedCrmStatus: 'ALL',
  selectedChannelStatus: 'ALL',
  selectedPriorityFilter: 'ALL',
  contactsPage: 1,
});
