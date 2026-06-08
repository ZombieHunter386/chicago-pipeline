// Filter panel: renders controls from /api/filters, collects state into
// window.FilterState, dispatches 'filterchange' when anything changes.

window.FilterState = {
  filters: {},   // { column: true | "value" | {min, max} }
  stage: null,
  includeCondoUnits: false,
  topNOnly: false,
  // Map-layer toggles double as list filters. Categories present here are
  // included; categories absent are excluded. Initial state mirrors the
  // checkboxes in the layers panel (all on).
  visibleCategories: new Set(['top', 'consolidated', 'outreach', 'other']),
  // Active scoring profile — drives ?profile= on /api/parcels so the server
  // sorts by the profile's score_column. null = no profile param sent (legacy
  // default ordering).
  profile: null,
};

window.filtersReady = (async function initFilters() {
  try {
    const resp = await fetch('/api/filters');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const schema = await resp.json();

    renderStagePills(schema.stage_pills);
    renderFilterPanel(schema.filter_groups);
    wireFilterToggle();
    wireHeaderToggles();
    wireLayerToggles();

    window.dispatchEvent(new CustomEvent('filterchange'));
  } catch (err) {
    console.error('Failed to load /api/filters:', err);
    const panel = document.getElementById('filter-panel');
    if (panel) {
      panel.textContent = 'Filters unavailable — refresh to retry.';
    }
  }
})();

function renderStagePills(cfg) {
  const container = document.getElementById('stage-pills');
  container.innerHTML = '';
  const pills = [{label: 'All', value: null}]
    .concat((cfg.values || []).map(v => ({label: capitalize(v), value: v})));

  pills.forEach((p, i) => {
    const el = document.createElement('button');
    el.type = 'button';
    const isActive = i === 0;
    el.className = 'filter-pill stage-pill' + (isActive ? ' active' : '');
    el.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    el.textContent = p.label;
    el.onclick = () => {
      container.querySelectorAll('.filter-pill').forEach(e => {
        e.classList.remove('active');
        e.setAttribute('aria-pressed', 'false');
      });
      el.classList.add('active');
      el.setAttribute('aria-pressed', 'true');
      window.FilterState.stage = p.value;
      window.dispatchEvent(new CustomEvent('filterchange'));
    };
    container.appendChild(el);
  });
}

function renderFilterPanel(groups) {
  const panel = document.getElementById('filter-panel');
  panel.innerHTML = '';
  groups.forEach(g => {
    const groupEl = document.createElement('div');
    groupEl.className = 'filter-group';
    const title = document.createElement('div');
    title.className = 'filter-group-title';
    title.textContent = g.group;
    groupEl.appendChild(title);
    g.filters.forEach(f => groupEl.appendChild(renderFilter(f)));
    panel.appendChild(groupEl);
  });
}

function renderFilter(f) {
  const ctrl = document.createElement('div');
  ctrl.className = 'filter-control';
  const col = f.column;
  const colAttr = escapeHtml(col);
  const labelText = escapeHtml(f.label);

  if (f.type === 'checkbox') {
    ctrl.style.flexDirection = 'column';
    ctrl.style.alignItems = 'flex-start';
    ctrl.style.gap = '4px';
    const groupId = `filter-${colAttr}-tristate`;
    const groupLabel = `${labelText}: any, yes, or no`;
    ctrl.innerHTML = `
      <label id="${groupId}-label" style="font-size:11px; color:#c9d1d9;">${labelText}</label>
      <div role="radiogroup" aria-labelledby="${groupId}-label" class="tristate-group" data-col="${colAttr}">
        <button type="button" class="tristate-btn active" data-value="" aria-pressed="true">Any</button>
        <button type="button" class="tristate-btn" data-value="true" aria-pressed="false">Yes</button>
        <button type="button" class="tristate-btn" data-value="false" aria-pressed="false">No</button>
      </div>
    `;
    const buttons = ctrl.querySelectorAll('.tristate-btn');
    buttons.forEach(btn => {
      btn.onclick = () => {
        buttons.forEach(b => {
          b.classList.remove('active');
          b.setAttribute('aria-pressed', 'false');
        });
        btn.classList.add('active');
        btn.setAttribute('aria-pressed', 'true');
        const v = btn.dataset.value;
        if (v === 'true') window.FilterState.filters[col] = true;
        else if (v === 'false') window.FilterState.filters[col] = false;
        else delete window.FilterState.filters[col];
        updateActiveCount();
        window.dispatchEvent(new CustomEvent('filterchange'));
      };
    });
  } else if (f.type === 'range') {
    ctrl.style.flexDirection = 'column';
    ctrl.style.alignItems = 'flex-start';
    ctrl.style.gap = '4px';
    const minId = `filter-${colAttr}-min`;
    const maxId = `filter-${colAttr}-max`;
    const groupId = `filter-${colAttr}-label`;
    ctrl.innerHTML = `
      <label id="${groupId}" style="font-size:10px; color:#8b949e;">${labelText}</label>
      <div class="filter-range">
        <label for="${minId}" class="visually-hidden" style="position:absolute;left:-9999px;">${labelText} min</label>
        <input type="number" id="${minId}" class="filter-input" placeholder="Min" data-col="${colAttr}" data-bound="min">
        <span style="color:#484f58;">—</span>
        <label for="${maxId}" class="visually-hidden" style="position:absolute;left:-9999px;">${labelText} max</label>
        <input type="number" id="${maxId}" class="filter-input" placeholder="Max" data-col="${colAttr}" data-bound="max">
      </div>
    `;
    ctrl.querySelectorAll('input').forEach(i => {
      i.onchange = (e) => {
        const c = e.target.dataset.col;
        const bound = e.target.dataset.bound;
        const val = e.target.value === '' ? null : parseFloat(e.target.value);
        const cur = window.FilterState.filters[c] || {};
        if (val === null) delete cur[bound];
        else cur[bound] = val;
        if (Object.keys(cur).length === 0) delete window.FilterState.filters[c];
        else window.FilterState.filters[c] = cur;
        updateActiveCount();
        window.dispatchEvent(new CustomEvent('filterchange'));
      };
    });
  } else if (f.type === 'dropdown') {
    ctrl.style.flexDirection = 'column';
    ctrl.style.alignItems = 'flex-start';
    ctrl.style.gap = '4px';
    const ddId = `filter-${colAttr}-dropdown`;

    const label = document.createElement('label');
    label.setAttribute('for', ddId);
    label.style.fontSize = '10px';
    label.style.color = '#8b949e';
    label.textContent = f.label;
    ctrl.appendChild(label);

    const select = document.createElement('select');
    select.id = ddId;
    select.className = 'filter-select';
    select.dataset.col = col;

    const anyOpt = document.createElement('option');
    anyOpt.value = '';
    anyOpt.textContent = 'Any';
    select.appendChild(anyOpt);

    (f.options || []).forEach(o => {
      const opt = document.createElement('option');
      opt.value = o;
      opt.textContent = o;
      select.appendChild(opt);
    });

    select.onchange = (e) => {
      const c = e.target.dataset.col;
      if (e.target.value === '') delete window.FilterState.filters[c];
      else window.FilterState.filters[c] = e.target.value;
      updateActiveCount();
      window.dispatchEvent(new CustomEvent('filterchange'));
    };

    ctrl.appendChild(select);
  } else if (f.type === 'multiselect') {
    ctrl.style.flexDirection = 'column';
    ctrl.style.alignItems = 'flex-start';
    ctrl.style.gap = '4px';
    const label = document.createElement('label');
    label.style.fontSize = '10px';
    label.style.color = '#8b949e';
    label.textContent = f.label;
    ctrl.appendChild(label);

    const box = document.createElement('div');
    box.className = 'filter-multiselect';
    box.dataset.col = col;
    box.style.maxHeight = '160px';
    box.style.overflowY = 'auto';
    box.style.border = '1px solid #30363d';
    box.style.borderRadius = '4px';
    box.style.padding = '4px 6px';
    box.style.width = '100%';
    box.style.boxSizing = 'border-box';

    (f.options || []).forEach(o => {
      const row = document.createElement('label');
      row.style.display = 'flex';
      row.style.alignItems = 'center';
      row.style.gap = '6px';
      row.style.fontSize = '11px';
      row.style.color = '#c9d1d9';
      row.style.padding = '2px 0';
      row.style.cursor = 'pointer';
      const cb = document.createElement('input');
      cb.type = 'checkbox';
      cb.value = o;
      cb.dataset.col = col;
      cb.addEventListener('change', () => {
        const checked = [...box.querySelectorAll('input:checked')].map(i => i.value);
        if (checked.length === 0) delete window.FilterState.filters[col];
        else window.FilterState.filters[col] = checked;
        updateActiveCount();
        window.dispatchEvent(new CustomEvent('filterchange'));
      });
      row.appendChild(cb);
      const span = document.createElement('span');
      span.textContent = o;
      row.appendChild(span);
      box.appendChild(row);
    });
    ctrl.appendChild(box);
  } else if (f.type === 'text_search') {
    ctrl.style.flexDirection = 'column';
    ctrl.style.alignItems = 'flex-start';
    ctrl.style.gap = '4px';
    const txtId = `filter-${colAttr}-text`;
    ctrl.innerHTML = `
      <label for="${txtId}" style="font-size:10px; color:#8b949e;">${labelText}</label>
      <input type="text" id="${txtId}" class="filter-input" style="width:100%;" placeholder="Search…" data-col="${colAttr}">
    `;
    ctrl.querySelector('input').onchange = (e) => {
      const c = e.target.dataset.col;
      if (e.target.value === '') delete window.FilterState.filters[c];
      else window.FilterState.filters[c] = e.target.value;
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
  btn.setAttribute('aria-expanded', 'false');
  btn.onclick = () => {
    panel.classList.toggle('open');
    const isOpen = panel.classList.contains('open');
    const arrow = btn.querySelector('.arrow');
    if (arrow) arrow.textContent = isOpen ? '▾' : '▸';
    btn.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
  };
  document.getElementById('clear-filters').onclick = () => {
    window.FilterState.filters = {};
    panel.querySelectorAll('input[type="checkbox"]').forEach(i => i.checked = false);
    panel.querySelectorAll('.filter-multiselect input').forEach(i => i.checked = false);
    panel.querySelectorAll('input[type="number"], input[type="text"]').forEach(i => i.value = '');
    panel.querySelectorAll('select').forEach(s => s.value = '');
    panel.querySelectorAll('.tristate-group').forEach(g => {
      g.querySelectorAll('.tristate-btn').forEach(b => {
        const isAny = b.dataset.value === '';
        b.classList.toggle('active', isAny);
        b.setAttribute('aria-pressed', isAny ? 'true' : 'false');
      });
    });
    updateActiveCount();
    window.dispatchEvent(new CustomEvent('filterchange'));
  };
}

// Helper used by list.js and map.js to build query strings
window.filterStateToQuery = function() {
  const params = new URLSearchParams();
  for (const [col, val] of Object.entries(window.FilterState.filters)) {
    if (val === true) params.set(col, 'true');
    else if (val === false) params.set(col, 'false');
    else if (Array.isArray(val)) {
      val.forEach(v => params.append(col, v));
    } else if (typeof val === 'object' && val !== null) {
      // {min, max} range — keep the legacy dot-notation so existing URL links
      // aren't broken. All other complex operators (between, not_null,
      // prefix_in, in) are JSON-encoded as a single ?col=<json> param.
      const keys = Object.keys(val);
      if (keys.length > 0 && keys.every(k => k === 'min' || k === 'max')) {
        if (val.min != null) params.set(`${col}.min`, val.min);
        if (val.max != null) params.set(`${col}.max`, val.max);
      } else {
        params.set(col, JSON.stringify(val));
      }
    } else {
      params.set(col, val);
    }
  }
  if (window.FilterState.stage) params.set('stage', window.FilterState.stage);
  if (window.FilterState.includeCondoUnits) {
    params.set('include_condo_units', 'true');
  }
  if (window.FilterState.topNOnly) {
    params.set('top_n_only', 'true');
  }
  if (window.FilterState.profile) {
    params.set('profile', window.FilterState.profile);
  }
  // Pass active categories so the server filters list/map identically.
  // Skip when all 4 are visible (no-op filter).
  const allCats = ['top', 'consolidated', 'outreach', 'other'];
  const visible = allCats.filter(c => window.FilterState.visibleCategories.has(c));
  if (visible.length > 0 && visible.length < allCats.length) {
    params.set('categories', visible.join(','));
  }
  return params.toString();
};

function wireHeaderToggles() {
  const condoCb = document.getElementById('include-condo-units');
  if (condoCb) {
    condoCb.addEventListener('change', () => {
      window.FilterState.includeCondoUnits = condoCb.checked;
      window.dispatchEvent(new CustomEvent('filterchange'));
    });
  }
  const topNCb = document.getElementById('top-n-only');
  if (topNCb) {
    topNCb.addEventListener('change', () => {
      window.FilterState.topNOnly = topNCb.checked;
      window.dispatchEvent(new CustomEvent('filterchange'));
    });
  }
}

function wireLayerToggles() {
  // Map's layer panel checkboxes also act as list filters. When unchecked,
  // the matching parcel-category disappears from BOTH the map and the list.
  // Without this wiring the map's setStyle({opacity:0}) was decoupled from
  // the left list — user reported this as a bug.
  const layerToCategory = {
    top: 'top',
    consolidated: 'consolidated',
    outreach: 'outreach',
    other: 'other',
  };
  document.querySelectorAll('.layer-toggle input[data-layer]').forEach(cb => {
    const layer = cb.dataset.layer;
    const cat = layerToCategory[layer];
    if (!cat) return; // 'group' and 'parcel_outlines' are visual-only
    cb.addEventListener('change', () => {
      if (cb.checked) window.FilterState.visibleCategories.add(cat);
      else window.FilterState.visibleCategories.delete(cat);
      window.dispatchEvent(new CustomEvent('filterchange'));
    });
  });
}
