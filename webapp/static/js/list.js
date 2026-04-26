// Ranked list: re-fetches on filterchange, renders rows, handles selection.

(function () {
  const LIST_PAGE_SIZE = 20;
  let currentOffset = 0;
  let currentTotal = 0;
  let reqId = 0;

  window.addEventListener('filterchange', () => {
    currentOffset = 0;
    loadList({replace: true});
  });

  document.getElementById('load-more').onclick = () => {
    currentOffset += LIST_PAGE_SIZE;
    loadList({replace: false});
  };

  async function loadList({replace}) {
    const myId = ++reqId;
    const qs = window.filterStateToQuery();
    const url = `/api/parcels?${qs}&limit=${LIST_PAGE_SIZE}&offset=${currentOffset}`;

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

    if (replace) list.innerHTML = '';

    if (replace && data.parcels.length === 0) {
      list.textContent = 'No parcels match these filters.';
      loadMore.style.display = 'none';
      document.getElementById('count-label').textContent =
        `0 of ${currentTotal.toLocaleString()}`;
      document.getElementById('top-bar-meta').textContent =
        `Score v N/A · ${currentTotal.toLocaleString()} parcels · Top ${LIST_PAGE_SIZE} shown`;
      return;
    }

    data.parcels.forEach(p => list.appendChild(renderParcelRow(p)));

    document.getElementById('count-label').textContent =
      `${Math.min(currentOffset + LIST_PAGE_SIZE, currentTotal)} of ${currentTotal.toLocaleString()}`;
    document.getElementById('top-bar-meta').textContent =
      `Score v N/A · ${currentTotal.toLocaleString()} parcels · Top ${LIST_PAGE_SIZE} shown`;
    loadMore.style.display =
      (currentOffset + LIST_PAGE_SIZE) < currentTotal ? '' : 'none';
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
    // Score tag — stub since score is NULL
    if (p.score != null) {
      const cls = p.score >= 80 ? 'score' : 'score-med';
      tags.push(`<span class="tag ${cls}">${Math.round(p.score)}</span>`);
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
    if (p.listing_status === 'listed') {
      tags.push('<span class="tag listed">Listed</span>');
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
})();
