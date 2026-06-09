// App entry. filters.js fires the initial 'filterchange' event once the
// filter schema has loaded; list.js and map.js listen and populate
// themselves. This file exists as the documented entry point and for
// any cross-panel wiring that doesn't belong to a specific module.

// Wire the always-visible address search bar at the top of the left panel
// to the same FilterState the filter panel uses, so they stay in sync.
(function wireAddressSearch() {
  const input = document.getElementById('address-search');
  if (!input) return;
  // Debounce so we don't fire a request per keystroke.
  let t = null;
  input.addEventListener('input', (e) => {
    clearTimeout(t);
    t = setTimeout(() => {
      const v = e.target.value.trim();
      if (v) {
        window.FilterState.filters.address = v;
      } else {
        delete window.FilterState.filters.address;
      }
      window.dispatchEvent(new CustomEvent('filterchange'));
    }, 250);
  });
})();

// Profile dropdown — fetches /api/profile-defaults, populates the top-bar
// <select>, persists selection to localStorage, and merges recommended
// filters into FilterState non-destructively (user-set wins) on change.
(function initProfileSelector() {
  const sel = document.getElementById('profile-selector');
  if (!sel) return;

  const PROFILE_LABELS = {
    value_add: 'Value-add multifamily',
    adu: 'ADU candidates',
    redev: 'Redevelopment',
  };

  let registry = {};

  // Merge a profile's recommended_filters into FilterState.filters without
  // overwriting keys the user has already set. Scalar and complex object
  // values (between, not_null, prefix_in, in, min, max) are all supported
  // now that _build_where handles them on the backend.
  //
  // {between: [a, b]} gets normalised to {min: a, max: b} so the range
  // filter control can populate its min/max inputs natively.
  function mergeRecommendedFilters(recommended) {
    for (const [col, val] of Object.entries(recommended || {})) {
      if (col in window.FilterState.filters) continue; // user-set wins
      if (val === 1) window.FilterState.filters[col] = true;
      else if (val === 0) window.FilterState.filters[col] = false;
      else if (val && typeof val === 'object' && Array.isArray(val.between)) {
        window.FilterState.filters[col] = {min: val.between[0], max: val.between[1]};
      } else {
        window.FilterState.filters[col] = val;
      }
    }
  }

  // Auto-expand the filter panel so the user can see the just-applied
  // profile filters without having to click the toggle. Idempotent.
  function expandFilterPanel() {
    const panel = document.getElementById('filter-panel');
    const btn = document.getElementById('filter-toggle');
    if (!panel || !btn || panel.classList.contains('open')) return;
    panel.classList.add('open');
    const arrow = btn.querySelector('.arrow');
    if (arrow) arrow.textContent = '▾';
    btn.setAttribute('aria-expanded', 'true');
  }

  async function init() {
    try {
      const resp = await fetch('/api/profile-defaults');
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      registry = await resp.json();
    } catch (e) {
      console.warn('profile-defaults fetch failed:', e);
      return;
    }

    // Add a leading "— choose —" option so the user can see there's a choice.
    const defaultOpt = document.createElement('option');
    defaultOpt.value = '';
    defaultOpt.textContent = '— Profile —';
    sel.appendChild(defaultOpt);

    Object.keys(registry).forEach(name => {
      const opt = document.createElement('option');
      opt.value = name;
      opt.textContent = PROFILE_LABELS[name] || name;
      sel.appendChild(opt);
    });

    // Restore last selection from localStorage; fall back to value_add.
    const saved = localStorage.getItem('selectedProfile') || 'value_add';
    if (registry[saved]) {
      sel.value = saved;
      window.FilterState.profile = saved;
      // Merge recommended filters for the restored profile before the initial
      // filterchange fires (filters.js fires it at the end of initFilters).
      // We wait for filtersReady so FilterState.filters is fully initialised.
      if (window.filtersReady && typeof window.filtersReady.then === 'function') {
        window.filtersReady.then(() => {
          mergeRecommendedFilters(registry[saved] && registry[saved].recommended_filters);
          // Refresh the visible filter controls so the auto-merged values appear.
          if (typeof window.refreshFilterPanel === 'function') {
            window.refreshFilterPanel();
          }
        });
      }
    }

    sel.addEventListener('change', () => {
      const name = sel.value;
      if (name) {
        localStorage.setItem('selectedProfile', name);
        window.FilterState.profile = name;
        mergeRecommendedFilters(registry[name] && registry[name].recommended_filters);
        // Refresh the visible filter controls + auto-expand the panel so the
        // user sees the applied filters immediately.
        if (typeof window.refreshFilterPanel === 'function') {
          window.refreshFilterPanel();
        }
        expandFilterPanel();
      } else {
        window.FilterState.profile = null;
      }
      window.dispatchEvent(new CustomEvent('filterchange'));
    });
  }

  init();
})();

console.info('Chicago Pipeline Review UI ready.');
