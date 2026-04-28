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

console.info('Chicago Pipeline Review UI ready.');
