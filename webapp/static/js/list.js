// Ranked list: re-fetches on filterchange, renders rows, handles selection.

(function () {
  const LIST_PAGE_SIZE = 20;
  // Cap groups returned per filterchange — without this 1k+ same-owner
  // clusters all land in the merge and the list overflows the page.
  // 100 is plenty: groups sort by score desc so the 100 visible are the
  // top-100 candidates. Enough for "Load more" to cycle through.
  const GROUPS_FETCH_LIMIT = 100;
  let currentOffset = 0;
  let currentTotal = 0;
  // Cache the merged + sorted entity queue between renders so 'Load more'
  // can paginate across both groups and parcels without duplicating items.
  let mergedQueue = [];
  let renderedFromQueue = 0;
  let reqId = 0;
  let sortBy = '';
  let sortDir = 'desc';

  window.addEventListener('filterchange', () => {
    currentOffset = 0;
    loadList({replace: true});
  });

  document.getElementById('load-more').onclick = () => {
    // If the merged queue still has unrendered entries (groups + already-
    // fetched parcels), render the next page from it without re-fetching.
    // Only hit /api/parcels when the local queue is exhausted.
    const moreInQueue = renderedFromQueue < mergedQueue.length;
    if (moreInQueue) {
      const list = document.getElementById('parcel-list');
      const renderEnd = Math.min(renderedFromQueue + LIST_PAGE_SIZE, mergedQueue.length);
      for (let i = renderedFromQueue; i < renderEnd; i++) {
        const item = mergedQueue[i];
        if (item.kind === 'group') {
          list.appendChild(renderGroupRow(item.payload));
        } else {
          list.appendChild(renderParcelRow(item.payload));
        }
      }
      renderedFromQueue = renderEnd;
      // Update the count label and Load-more visibility without a re-fetch.
      document.getElementById('count-label').textContent =
        `${renderedFromQueue.toLocaleString()} of ${currentTotal.toLocaleString()}`;
      const stillMoreQueue = renderedFromQueue < mergedQueue.length;
      const moreInApi = (currentOffset + LIST_PAGE_SIZE) < currentTotal;
      document.getElementById('load-more').style.display =
        (stillMoreQueue || moreInApi) ? '' : 'none';
      return;
    }
    currentOffset += LIST_PAGE_SIZE;
    loadList({replace: false});
  };

  const sortByEl = document.getElementById('sort-by');
  const sortDirEl = document.getElementById('sort-dir');
  if (sortByEl && sortDirEl) {
    sortByEl.addEventListener('change', () => {
      sortBy = sortByEl.value;
      currentOffset = 0;
      loadList({replace: true});
      window.dispatchEvent(new CustomEvent('sortchange', {detail: {sort: sortBy, dir: sortDir}}));
    });
    sortDirEl.addEventListener('click', () => {
      sortDir = sortDir === 'desc' ? 'asc' : 'desc';
      sortDirEl.textContent = sortDir === 'desc' ? '↓' : '↑';
      if (sortBy) {
        currentOffset = 0;
        loadList({replace: true});
        window.dispatchEvent(new CustomEvent('sortchange', {detail: {sort: sortBy, dir: sortDir}}));
      }
    });
  }

  // Map a sort column name to the equivalent field on a consolidation-group
  // payload. Groups don't carry per-parcel fields like year_built, so unmapped
  // fields return undefined → group sinks to the bottom of the sort.
  const GROUP_FIELD_MAP = {
    score: 'score',
    lot_size_sf: 'combined_lot_size_sf',
    building_sf: 'combined_building_sf',
    assessed_total: 'sum_assessed_total',
    estimated_annual_tax: 'sum_estimated_annual_tax',
    hold_duration_years: 'longest_hold_years',
    year_built: 'oldest_year_built',
  };

  function valueFor(item, field) {
    if (!field) return item.payload?.score;
    if (item.kind === 'group') {
      const mapped = GROUP_FIELD_MAP[field];
      return mapped ? item.payload[mapped] : undefined;
    }
    return item.payload[field];
  }

  function sortMergedQueue(arr) {
    const field = sortBy || 'score';
    const dir = (sortBy ? sortDir : 'desc') === 'asc' ? 1 : -1;
    arr.sort((a, b) => {
      const av = valueFor(a, field);
      const bv = valueFor(b, field);
      const aNull = av == null, bNull = bv == null;
      if (aNull && !bNull) return 1;
      if (!aNull && bNull) return -1;
      if (aNull && bNull) return 0;
      if (typeof av === 'string' || typeof bv === 'string') {
        return String(av).localeCompare(String(bv)) * dir;
      }
      return (av - bv) * dir;
    });
  }

  async function loadList({replace}) {
    const myId = ++reqId;
    const qs = window.filterStateToQuery();
    const sortQs = sortBy ? `&sort=${encodeURIComponent(sortBy)}&dir=${sortDir}` : '';
    const url = `/api/parcels?${qs}&limit=${LIST_PAGE_SIZE}&offset=${currentOffset}${sortQs}`;

    const list = document.getElementById('parcel-list');
    const loadMore = document.getElementById('load-more');

    let data;
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      data = await r.json();
    } catch (err) {
      if (myId !== reqId) return; // stale
      currentTotal = 0;
      list.textContent = "Couldn't load parcels — refresh to retry.";
      loadMore.style.display = 'none';
      return;
    }

    if (myId !== reqId) return; // stale

    currentTotal = data.total;

    if (replace) {
      list.innerHTML = '';
      mergedQueue = [];
      renderedFromQueue = 0;

      // Fetch up to GROUPS_FETCH_LIMIT consolidation groups using the same
      // filter query so groups whose members don't match the filter drop
      // out alongside parcels. Groups paginate with parcels via mergedQueue.
      let groups = [];
      try {
        const rg = await fetch(`/api/consolidation-groups?${qs}&limit=${GROUPS_FETCH_LIMIT}`);
        if (rg.ok) {
          const gd = await rg.json();
          if (myId !== reqId) return; // stale
          groups = gd.groups || [];
        }
      } catch (_) { /* non-fatal — parcels still render below */ }

      mergedQueue = [
        ...groups.map(g => ({ kind: 'group', payload: g })),
        ...data.parcels.map(p => ({ kind: 'parcel', payload: p })),
      ];
      sortMergedQueue(mergedQueue);
    } else {
      data.parcels.forEach(p => {
        mergedQueue.push({ kind: 'parcel', payload: p });
      });
      const head = mergedQueue.slice(0, renderedFromQueue);
      const tail = mergedQueue.slice(renderedFromQueue);
      sortMergedQueue(tail);
      mergedQueue = head.concat(tail);
    }

    if (replace && mergedQueue.length === 0) {
      document.getElementById('count-label').textContent =
        `0 of ${currentTotal.toLocaleString()}`;
      document.getElementById('top-bar-meta').textContent =
        `${currentTotal.toLocaleString()} parcels`;
      list.textContent = 'No parcels match these filters.';
      loadMore.style.display = 'none';
      return;
    }

    // Render the next LIST_PAGE_SIZE entries from the merged queue.
    const renderEnd = Math.min(renderedFromQueue + LIST_PAGE_SIZE, mergedQueue.length);
    for (let i = renderedFromQueue; i < renderEnd; i++) {
      const item = mergedQueue[i];
      if (item.kind === 'group') {
        list.appendChild(renderGroupRow(item.payload));
      } else {
        list.appendChild(renderParcelRow(item.payload));
      }
    }
    renderedFromQueue = renderEnd;

    // Count label shows rendered-so-far / total parcel count.
    document.getElementById('count-label').textContent =
      `${renderedFromQueue.toLocaleString()} of ${currentTotal.toLocaleString()}`;
    const condoNote = window.FilterState && window.FilterState.includeCondoUnits
      ? '(incl. condo units)'
      : '(excl. individual condo units)';
    const topNote = window.FilterState && window.FilterState.topNOnly
      ? '· Top 20 Scores only'
      : '';
    document.getElementById('top-bar-meta').textContent =
      `${currentTotal.toLocaleString()} parcels ${condoNote} ${topNote}`.trim();
    // Show 'Load more' when either the merged queue still has unrendered
    // entries OR the parcels API has more pages to fetch.
    const moreInQueue = renderedFromQueue < mergedQueue.length;
    const moreInApi = (currentOffset + LIST_PAGE_SIZE) < currentTotal;
    loadMore.style.display = (moreInQueue || moreInApi) ? '' : 'none';
  }

  function renderParcelRow(p) {
    const el = document.createElement('div');
    el.className = 'parcel-item';
    el.dataset.pin = p.pin;
    el.tabIndex = 0;
    el.setAttribute('role', 'button');

    const details = [
      p.lot_size_sf ? `${Math.round(p.lot_size_sf).toLocaleString()} SF lot` : null,
      p.zone_class,
      p.year_built ? `Built ${p.year_built}` : null,
      p.hold_duration_years ? `Held ${Math.round(p.hold_duration_years)}yr` : null,
    ].filter(Boolean).join(' · ') || '—';

    const tags = [];
    if (p.score != null) {
      const cls = p.score >= 80 ? 'score' : 'score-med';
      // Score is out of 100 (significant weights sum to 1.0 per pipeline.score).
      // 2 decimals so the user can distinguish near-ties.
      tags.push(`<span class="tag ${cls}" title="Score out of 100">${p.score.toFixed(2)}</span>`);
    }
    if (p.is_absentee) tags.push('<span class="tag absentee">Absentee</span>');
    if (p.is_llc) tags.push('<span class="tag llc">LLC</span>');
    if (p.tax_delinquent) tags.push('<span class="tag delinquent">Tax delinquent</span>');
    if (p.far_gap && p.far_gap >= 1.5) {
      tags.push(`<span class="tag underbuilt">FAR gap ${p.far_gap.toFixed(1)}x</span>`);
    }
    if (p.consolidation_group_id != null) {
      tags.push('<span class="tag llc">Consolidated</span>');
    }
    if (p.is_condo_building) {
      const u = p.condo_unit_count || 0;
      const miss = p.condo_units_missing_sf_count || 0;
      const sfNote = miss > 0 ? ` · SF incomplete (${miss}/${u})` : '';
      tags.push(`<span class="tag stage">Condo · ${u} unit${u === 1 ? '' : 's'}${sfNote}</span>`);
    }
    if (p.stage && p.stage !== 'scored') {
      tags.push(`<span class="tag stage">${escapeHtml(capitalize(p.stage))}</span>`);
    }

    el.innerHTML = `
      <div class="address">${escapeHtml(p.address || p.pin)}</div>
      <div class="details">${escapeHtml(details)}</div>
      <div class="tags">${tags.join('')}</div>
    `;

    const select = () => {
      document.querySelectorAll('.parcel-item.selected').forEach(e => e.classList.remove('selected'));
      el.classList.add('selected');
      window.dispatchEvent(new CustomEvent('parcelselect', {detail: {pin: p.pin}}));
    };

    el.onclick = select;
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        if (e.key === ' ') e.preventDefault();
        select();
      }
    });

    return el;
  }

  function renderGroupRow(g) {
    const el = document.createElement('div');
    el.className = 'parcel-item parcel-item-group';
    el.dataset.groupId = g.group_id;
    el.tabIndex = 0;
    el.setAttribute('role', 'button');

    const details = [
      `${g.parcel_count} parcels`,
      g.combined_lot_size_sf
        ? `${Math.round(g.combined_lot_size_sf).toLocaleString()} SF combined lot`
        : null,
      g.combined_building_sf
        ? `${Math.round(g.combined_building_sf).toLocaleString()} SF combined bldg`
        : null,
    ].filter(Boolean).join(' · ');

    const title = g.owner_name || `Group ${g.group_id}`;
    const tags = [];
    if (g.score != null) {
      const cls = g.score >= 80 ? 'score' : 'score-med';
      tags.push(`<span class="tag ${cls}" title="Score out of 100">${g.score.toFixed(2)}</span>`);
    }
    tags.push(`<span class="tag llc">Consolidation Group</span>`);
    if (g.sum_estimated_annual_tax) {
      const t = Math.round(g.sum_estimated_annual_tax).toLocaleString();
      tags.push(`<span class="tag stage">~$${t}/yr taxes</span>`);
    }

    el.innerHTML = `
      <div class="address">${escapeHtml(title)}</div>
      <div class="details">${escapeHtml(details)}</div>
      <div class="tags">${tags.join('')}</div>
    `;

    const select = () => {
      document.querySelectorAll('.parcel-item.selected').forEach(e => e.classList.remove('selected'));
      el.classList.add('selected');
      window.dispatchEvent(new CustomEvent('parcelselect', {detail: {groupId: g.group_id}}));
    };

    el.onclick = select;
    el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        if (e.key === ' ') e.preventDefault();
        select();
      }
    });

    return el;
  }
})();

// Bulk-trace top 20: kicks off /api/enrichment/bulk for the first 20 visible
// parcels (groups skipped — they don't carry a single PIN), then polls the job
// status every 2s and updates an inline progress bar. On completion the page
// reloads so the newly-fetched contacts populate the detail panels.
(function () {
  const btn = document.getElementById('bulk-trace-btn');
  if (!btn) return;
  btn.addEventListener('click', async () => {
    const visibleRows = document.querySelectorAll('#parcel-list [data-pin]');
    const pins = Array.from(visibleRows).slice(0, 20).map(r => r.dataset.pin);
    if (pins.length === 0) {
      alert('No parcels in current view'); return;
    }
    const estCost = (pins.length * 0.10).toFixed(2);
    if (!confirm(`Trace top ${pins.length} parcels (est. $${estCost})? Parcels with existing contacts are skipped.`)) return;

    const r = await fetch('/api/enrichment/bulk', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({pins}),
    });
    if (!r.ok) { alert('Bulk trace failed to start'); return; }
    const {job_id} = await r.json();
    pollProgress(job_id, pins.length);
  });

  async function pollProgress(jobId, total) {
    const prog = document.getElementById('bulk-trace-progress');
    const done = document.getElementById('bulk-trace-done');
    const totalEl = document.getElementById('bulk-trace-total');
    const fill = document.getElementById('bulk-trace-fill');
    prog.hidden = false;
    totalEl.textContent = total;
    while (true) {
      await new Promise(r => setTimeout(r, 2000));
      const r = await fetch(`/api/enrichment/job/${jobId}`);
      const data = await r.json();
      const doneCount = (data.pins || []).filter(p =>
        p.status === 'done' || p.status === 'skipped' || p.status === 'error').length;
      done.textContent = doneCount;
      fill.style.width = `${(doneCount / total * 100).toFixed(1)}%`;
      if (data.status === 'complete') {
        setTimeout(() => { prog.hidden = true; window.location.reload(); }, 1500);
        break;
      }
      if (data.status === 'paused') {
        alert(`Bulk trace paused: ${data.paused_reason}`);
        break;
      }
      if (data.status === 'failed') {
        alert('Bulk trace failed');
        break;
      }
    }
  }
})();
