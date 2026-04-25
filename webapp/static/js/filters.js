// Filter panel: renders controls from /api/filters, collects state into
// window.FilterState, dispatches 'filterchange' when anything changes.

window.FilterState = {
  filters: {},   // { column: true | "value" | {min, max} }
  stage: null,
};

(async function initFilters() {
  const schema = await fetch('/api/filters').then(r => r.json());

  renderStagePills(schema.stage_pills);
  renderFilterPanel(schema.filter_groups);
  wireFilterToggle();

  window.dispatchEvent(new CustomEvent('filterchange'));
})();

function renderStagePills(cfg) {
  const container = document.getElementById('stage-pills');
  const pills = [{label: 'All', value: null}]
    .concat((cfg.values || []).map(v => ({label: capitalize(v), value: v})));

  pills.forEach((p, i) => {
    const el = document.createElement('div');
    el.className = 'filter-pill' + (i === 0 ? ' active' : '');
    el.textContent = p.label;
    el.onclick = () => {
      container.querySelectorAll('.filter-pill').forEach(e => e.classList.remove('active'));
      el.classList.add('active');
      window.FilterState.stage = p.value;
      window.dispatchEvent(new CustomEvent('filterchange'));
    };
    container.appendChild(el);
  });
}

function renderFilterPanel(groups) {
  const panel = document.getElementById('filter-panel');
  groups.forEach(g => {
    const groupEl = document.createElement('div');
    groupEl.className = 'filter-group';
    groupEl.innerHTML = `<div class="filter-group-title">${g.group}</div>`;
    g.filters.forEach(f => groupEl.appendChild(renderFilter(f)));
    panel.appendChild(groupEl);
  });
}

function renderFilter(f) {
  const ctrl = document.createElement('div');
  ctrl.className = 'filter-control';

  if (f.type === 'checkbox') {
    ctrl.innerHTML = `
      <input type="checkbox" class="filter-checkbox" data-col="${f.column}">
      <label>${f.label}</label>
    `;
    ctrl.querySelector('input').onchange = (e) => {
      if (e.target.checked) window.FilterState.filters[f.column] = true;
      else delete window.FilterState.filters[f.column];
      updateActiveCount();
      window.dispatchEvent(new CustomEvent('filterchange'));
    };
  } else if (f.type === 'range') {
    ctrl.style.flexDirection = 'column';
    ctrl.style.alignItems = 'flex-start';
    ctrl.style.gap = '4px';
    ctrl.innerHTML = `
      <label style="font-size:10px; color:#8b949e;">${f.label}</label>
      <div class="filter-range">
        <input type="number" class="filter-input" placeholder="Min" data-col="${f.column}" data-bound="min">
        <span style="color:#484f58;">—</span>
        <input type="number" class="filter-input" placeholder="Max" data-col="${f.column}" data-bound="max">
      </div>
    `;
    ctrl.querySelectorAll('input').forEach(i => {
      i.onchange = (e) => {
        const col = e.target.dataset.col;
        const bound = e.target.dataset.bound;
        const val = e.target.value === '' ? null : parseFloat(e.target.value);
        const cur = window.FilterState.filters[col] || {};
        if (val === null) delete cur[bound];
        else cur[bound] = val;
        if (Object.keys(cur).length === 0) delete window.FilterState.filters[col];
        else window.FilterState.filters[col] = cur;
        updateActiveCount();
        window.dispatchEvent(new CustomEvent('filterchange'));
      };
    });
  } else if (f.type === 'dropdown') {
    ctrl.style.flexDirection = 'column';
    ctrl.style.alignItems = 'flex-start';
    ctrl.style.gap = '4px';
    const opts = (f.options || []).map(o => `<option value="${o}">${o}</option>`).join('');
    ctrl.innerHTML = `
      <label style="font-size:10px; color:#8b949e;">${f.label}</label>
      <select class="filter-select" data-col="${f.column}">
        <option value="">Any</option>
        ${opts}
      </select>
    `;
    ctrl.querySelector('select').onchange = (e) => {
      const col = e.target.dataset.col;
      if (e.target.value === '') delete window.FilterState.filters[col];
      else window.FilterState.filters[col] = e.target.value;
      updateActiveCount();
      window.dispatchEvent(new CustomEvent('filterchange'));
    };
  } else if (f.type === 'text_search') {
    ctrl.style.flexDirection = 'column';
    ctrl.style.alignItems = 'flex-start';
    ctrl.style.gap = '4px';
    ctrl.innerHTML = `
      <label style="font-size:10px; color:#8b949e;">${f.label}</label>
      <input type="text" class="filter-input" style="width:100%;" placeholder="Search…" data-col="${f.column}">
    `;
    ctrl.querySelector('input').onchange = (e) => {
      const col = e.target.dataset.col;
      if (e.target.value === '') delete window.FilterState.filters[col];
      else window.FilterState.filters[col] = e.target.value;
      updateActiveCount();
      window.dispatchEvent(new CustomEvent('filterchange'));
    };
  }

  return ctrl;
}

function updateActiveCount() {
  const n = Object.keys(window.FilterState.filters).length;
  document.getElementById('active-filter-count').textContent = n;
}

function wireFilterToggle() {
  const btn = document.getElementById('filter-toggle');
  const panel = document.getElementById('filter-panel');
  btn.onclick = () => {
    panel.classList.toggle('open');
    btn.querySelector('.arrow').textContent =
      panel.classList.contains('open') ? '▾' : '▸';
  };
  document.getElementById('clear-filters').onclick = () => {
    window.FilterState.filters = {};
    // Reset all inputs
    panel.querySelectorAll('input[type="checkbox"]').forEach(i => i.checked = false);
    panel.querySelectorAll('input[type="number"], input[type="text"]').forEach(i => i.value = '');
    panel.querySelectorAll('select').forEach(s => s.value = '');
    updateActiveCount();
    window.dispatchEvent(new CustomEvent('filterchange'));
  };
}

function capitalize(s) { return s[0].toUpperCase() + s.slice(1); }

// Helper used by list.js and map.js to build query strings
window.filterStateToQuery = function() {
  const params = new URLSearchParams();
  for (const [col, val] of Object.entries(window.FilterState.filters)) {
    if (val === true) params.set(col, 'true');
    else if (typeof val === 'object') {
      if (val.min != null) params.set(`${col}.min`, val.min);
      if (val.max != null) params.set(`${col}.max`, val.max);
    } else {
      params.set(col, val);
    }
  }
  if (window.FilterState.stage) params.set('stage', window.FilterState.stage);
  return params.toString();
};
